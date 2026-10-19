"""Aktor der Klimaanlage: Sollwertversatz über den CoE-Adapter (Plan 6a, Entscheidungen 3 und 9).

Gesendet wird über die Funktion ``set_offset`` (im core ``CoeAdapter.set_offset``): Der Adapter
hält den Wert und sendet ihn sofort und danach zyklisch, aber nur mit Freigabe der Laufzeit
(``permit``), sonst 0. Der Aktor wartet nie.

Zurückgelesen wird das Echo der UVR (``thermal.<name>.offset_active_k``); bestätigt ist ein Wert,
wenn das Echo ihn auf 0,05 K zeigt (``matches``). Ohne brauchbares Echo ist der Aktor nicht
steuerbar (Ruling R27): Kein Versatz ließe sich bestätigen. Zeigt das Echo im Live-Betrieb länger
als ``ECHO_MISMATCH_S`` etwas anderes als den Wert, den der Adapter sendet
(``thermal.<name>.offset_sent_k``), meldet der Aktor ``thermal.echo_mismatch.<name>``. Nach dem
ersten Senden gilt das auch im Dry-Run, etwa nach einer Übergabe (gewollt, Ruling R10). Ein
fehlendes Echo bei verbundener Quelle meldet er immer, auch ohne Senden.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Final

from bindaems.core.control.actuator import Capabilities, Confirmation, matches
from bindaems.shared.config import CoeOffsetConfig
from bindaems.shared.control import CommandKind, CommandValue, thermal_actuator
from bindaems.shared.domain import Alarm, Severity, Snapshot
from bindaems.shared.numbers import finite_number

REASON_NO_VALUES: Final = "Keine Werte der Klimaanlage."
REASON_NOT_ACCEPTED: Final = "CoE-Verbindung: Versatz nicht angenommen."
REASON_NO_ECHO: Final = "Kein Echo des Versatzes von der UVR."
# So lange darf das Echo im Live-Betrieb vom gesendeten Wert abweichen, bevor der Alarm kommt
ECHO_MISMATCH_S: Final = 120.0


class ThermalActuator:
    """Eine Klimaanlage an der UVR als Aktor mit der einen Art ``setpoint_offset`` in K.

    ``name`` ist der Schlüssel des Geräts in ``thermal.devices``. ``set_offset`` übergibt dem
    Adapter den Versatz und sagt, ob er angenommen wurde (ohne Sitzung oder außerhalb der Grenze
    nicht); ``connected`` sagt, ob die Quelle Werte liefert. Die Grenzen und die Schrittregel
    prüft die Safety-Schicht, nicht der Aktor.
    """

    confirm_timeout_s = 60.0  # das C.M.I. sendet bei Änderung

    def __init__(
        self,
        name: str,
        cfg: CoeOffsetConfig,
        set_offset: Callable[[float], bool],
        connected: Callable[[], bool],
    ) -> None:
        self.id = thermal_actuator(name)
        self._name = name
        self._label = cfg.label
        self._prefix = f"thermal.{name}."
        self._set_offset = set_offset
        self._connected = connected
        self._sent: float | None = None  # der zuletzt angenommene Wert; ``None``: nie live
        self._expected: float | None = None  # was das Echo zeigen soll, zur letzten Beobachtung
        self._echo: float | None = None
        self._mismatch_since: datetime | None = None

    def capabilities(self, snap: Snapshot, now: datetime) -> Capabilities:
        """Steuerbar, solange die Quelle Werte liefert und das Echo brauchbar ist (Ruling R27).

        Ohne Echo ließe sich kein Versatz bestätigen: Jeder Schreibvorgang liefe in die Frist, und
        die Regelung schriebe danach neu. Nicht steuerbar geht die Strategie auf 0, die Laufzeit
        gibt nicht frei, und der Adapter sendet 0; den Befund meldet ``alarms``.

        Nebenbei beobachtet der Aktor hier das Echo für ``alarms``: Die Laufzeit fragt die
        Fähigkeiten in jedem Zyklus, mit dem Snapshot und der Zeit der Safety-Schicht.
        """
        connected = self._connected()
        self._observe(snap, now, connected=connected)
        if not connected:
            return Capabilities(False, (REASON_NO_VALUES,))
        if self._echo_of(snap) is None:
            return Capabilities(False, (REASON_NO_ECHO,))
        return Capabilities(True)

    def present(self, snap: Snapshot) -> dict[CommandKind, CommandValue]:
        """Der Versatz, wie das Echo der UVR ihn zeigt; ohne brauchbares Echo ist er unbekannt."""
        echo = self._echo_of(snap)
        return {} if echo is None else {CommandKind.SETPOINT_OFFSET: echo}

    def send(self, kind: CommandKind, value: CommandValue) -> str | None:
        """Übergibt den Versatz dem Adapter, der ihn sofort sendet.

        ``None``, wenn er angenommen ist; sonst der Grund, warum nicht gesendet wurde. Eine andere
        Art oder einen Wert, der keine endliche Zahl ist, sendet der Aktor nie.
        """
        if kind != CommandKind.SETPOINT_OFFSET:
            return f"Die Klimaanlage kennt die Befehlsart „{kind}“ nicht."
        offset = finite_number(value)
        if offset is None:
            return f"Ungültiger Wert für „{kind}“."
        if not self._set_offset(offset):
            return REASON_NOT_ACCEPTED
        self._sent = offset
        return None

    def confirm(
        self, kind: CommandKind, value: CommandValue, snap: Snapshot
    ) -> tuple[Confirmation, str]:
        """Bestätigt, sobald das Echo ``value`` zeigt, sonst offen; die Frist hat der Ausführer."""
        present = self.present(snap)
        if kind in present and matches(kind, value, present[kind]):
            return Confirmation.CONFIRMED, ""
        return Confirmation.PENDING, ""

    def alarms(self, now: datetime) -> list[Alarm]:
        """``thermal.echo_mismatch.<name>`` (Warnung), wenn das Echo zu lange abweicht."""
        since, expected = self._mismatch_since, self._expected
        if since is None or expected is None or (now - since).total_seconds() < ECHO_MISMATCH_S:
            return []
        echo = "kein Echo" if self._echo is None else f"Echo {_kelvin(self._echo)}"
        message = (
            f"{self._label}: Das Echo der UVR zeigt seit mehr als {ECHO_MISMATCH_S:.0f} s nicht "
            f"den gesendeten Versatz (gesendet {_kelvin(expected, sign=True)}, {echo})."
        )
        start = since + timedelta(seconds=ECHO_MISMATCH_S)
        return [Alarm(f"thermal.echo_mismatch.{self._name}", Severity.WARNING, message, start)]

    def _echo_of(self, snap: Snapshot) -> float | None:
        return finite_number(snap.ok(self._prefix + "offset_active_k"))

    def _observe(self, snap: Snapshot, now: datetime, *, connected: bool) -> None:
        """Merkt, seit wann das Echo nicht zeigt, was der Adapter sendet.

        Beurteilt wird nur mit Werten der Quelle. Ein fehlendes Echo weicht immer ab (Ruling R27):
        Ohne Echo ist der Aktor nicht steuerbar und sendet nie, der Befund käme sonst nie. Ein
        Echo mit Wert wird ab dem ersten angenommenen ``send`` dieses Laufs (Live-Betrieb)
        beurteilt, danach auch im Dry-Run, etwa nach einer Übergabe: Ein Echo, das dann nicht 0
        zeigt, ist weiter ein Befund (gewollt, Ruling R10). Erwartet wird ``offset_sent_k``, der
        Wert, den der Adapter wirklich sendet (ohne Freigabe 0); ohne ihn der zuletzt angenommene
        Wert, ohne beides 0.
        """
        if not connected:
            self._mismatch_since = None
            return
        self._echo = self._echo_of(snap)
        if self._sent is None and self._echo is not None:
            self._mismatch_since = None
            return
        sent = finite_number(snap.ok(self._prefix + "offset_sent_k"))
        if sent is not None:
            self._expected = sent
        else:
            self._expected = 0.0 if self._sent is None else self._sent
        if self._echo is not None and matches(
            CommandKind.SETPOINT_OFFSET, self._expected, self._echo
        ):
            self._mismatch_since = None
        elif self._mismatch_since is None:
            self._mismatch_since = now


def _kelvin(value: float, *, sign: bool = False) -> str:
    """Ein Versatz zum Lesen: eine Nachkommastelle mit Komma, auf Wunsch mit Vorzeichen."""
    value += 0.0  # aus -0.0 wird 0.0
    text = f"{value:+.1f}" if sign else f"{value:.1f}"
    return f"{text.replace('.', ',')} K"
