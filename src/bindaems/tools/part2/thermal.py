"""Teil Klimaanlage des Prüfprotokolls Teil 2 (Plan 6a, Entscheidung 14): der Sollwertversatz.

Das Werkzeug nutzt denselben CoE-Adapter und denselben Aktor wie der core (``CoeAdapter``,
``ThermalActuator``) und bindet dafür denselben UDP-Port; der core bleibt darum gestoppt. Die
Sitzung (``ThermalSession``) hat nur diesen Adapter, sie spricht weder mit dem Cerbo noch mit
einer Wallbox. Gesendet wird nur, wenn das Werkzeug ausdrücklich gestartet und bestätigt ist.

Die Schritte:

1. **Werte:** Alle Werte der Empfangstabelle kommen an, binnen ``stale_after_s`` des Geräts (so
   oft muss das C.M.I. senden). Sonst endet der Teil ohne zu senden.
2. **Versatz +0,5 K:** senden, das Echo der UVR zeigt ihn binnen ``echo_s`` (60 s); dann die
   Rückfrage „Zeigt die Visu der UVR „EMS“ +0,5 K?“ (mit ``--yes`` übersprungen: niemand sieht
   auf die Visu).
3. **Versatz 0:** senden, das Echo zeigt 0.
4. **Timeout der UVR** (Rückfrage, mit ``--yes`` übersprungen): +0,5 K senden, bis das Echo es
   zeigt, dann nichts mehr senden (der Port des Werkzeugs schweigt, ``ThermalSession.mute``);
   das Echo fällt binnen ``timeout_s`` (5 min 30 s) von selbst auf 0.

Gesendet wird über den Aktor wie im core, mit Freigabe des Adapters (``permit_for``) nur für die
Dauer des Wartens auf das Echo. Am Ende, nach einem Fehler und nach Strg+C sendet die Sitzung 0
(``ThermalSession.restore``), und beim Stoppen sendet der Adapter selbst noch einmal 0.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from bindaems.core.actuators.thermal import ThermalActuator
from bindaems.core.adapters.coe import CoeAdapter, DatagramPort, PortFactory, coe_source, udp_port
from bindaems.core.control.actuator import Confirmation
from bindaems.core.control.thermal import thermal_mode
from bindaems.core.safety.ranges import check_range
from bindaems.core.state.store import StateStore
from bindaems.shared.config import CoeOffsetConfig, Config
from bindaems.shared.control import Command, CommandKind
from bindaems.shared.domain import Snapshot
from bindaems.shared.numbers import de, finite_number
from bindaems.shared.timeutil import SystemClock
from bindaems.tools.part2.session import (
    RESTORE_S,
    SKIPPED_UNATTENDED,
    Part2Session,
    seconds,
    show_finding,
)
from bindaems.tools.verify_checks import Finding, Status

ITEM: Final = "17.2-9"  # der Punkt der Klimaanlage im Prüfprotokoll Teil 2
KIND: Final = CommandKind.SETPOINT_OFFSET
TEST_OFFSET_K: Final = 0.5  # eine Stufe, wie die Strategie sie geht
# so lange über das Warten auf das Echo hinaus gilt die Freigabe beim Senden eines Versatzes
PERMIT_MARGIN_S: Final = 10.0
SKIPPED: Final = "übersprungen"
NOT_FOUND: Final = "Keine Klimaanlage „{}“ in thermal.devices."

QUESTION_VISU: Final = "Zeigt die Visu der UVR „EMS“ +0,5 K?"
QUESTION_TIMEOUT: Final = (
    "Timeout der UVR prüfen? Das Werkzeug sendet +0,5 K und danach bis zu 5 min 30 s nichts "
    "mehr; die UVR muss den Versatz von selbst auf 0 setzen."
)

# Bezeichnung und Einheit je Wert der Empfangstabelle, in ihrer Reihenfolge
_LABELS: Final = {
    "actual_c": ("Ist", "°C"),
    "setpoint_c": ("Soll", "°C"),
    "offset_active_k": ("Echo", "K"),
    "power_kw": ("Leistung", "W"),
    "cmd_on": ("Anforderung", ""),
    "cooling": ("Kühlen", ""),
    "venting": ("Lüften", ""),
    "heating": ("Heizen", ""),
    "running": ("Läuft", ""),
}


@dataclass(frozen=True)
class ThermalTimings:
    """Fristen der Schritte in Sekunden.

    - ``values_s``: so lange wartet Schritt 1 auf alle Werte; ``None``: ``stale_after_s`` des
      Geräts
    - ``echo_s``: so lange wartet ein Schritt auf das Echo eines gesendeten Versatzes
    - ``timeout_s``: so lange wartet Schritt 4 ohne Paket darauf, dass das Echo auf 0 fällt
    """

    values_s: float | None = None
    echo_s: float = 60.0
    timeout_s: float = 330.0


DEFAULT_TIMINGS: Final = ThermalTimings()  # die Fristen an der Anlage


def kelvin(value: float | None) -> str:
    """Ein Versatz zum Lesen: „+0,5 K“, „0 K“, „−1 K“; ohne Wert „kein Echo“."""
    if value is None:
        return "kein Echo"
    value = round(value, 1) + 0.0  # aus -0.0 wird 0.0
    return f"{'+' if value > 0 else ''}{de(value)} K"


class _Mute:
    """Schaltet den Port des Werkzeugs stumm (Schritt 4): Gesendetes geht dann nicht hinaus.

    ``open`` ist die ``PortFactory`` des Adapters; jeder Port, den sie öffnet, folgt ``muted``.
    """

    def __init__(self, port_factory: PortFactory) -> None:
        self.muted = False
        self._port_factory = port_factory

    async def open(
        self, on_datagram: Callable[[bytes, tuple[str, int]], None], port: int
    ) -> DatagramPort:
        return _MutablePort(await self._port_factory(on_datagram, port), self)


class _MutablePort:
    def __init__(self, inner: DatagramPort, mute: _Mute) -> None:
        self._inner = inner
        self._mute = mute

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        if not self._mute.muted:
            self._inner.sendto(data, addr)

    def close(self) -> None:
        self._inner.close()


class ThermalSession(Part2Session):
    """Die Sitzung des Teils Klimaanlage: nur der CoE-Adapter und der Aktor ``thermal.<name>``.

    ``restore`` stellt nicht einen vorgefundenen Wert wieder her, sondern sendet 0 und wartet auf
    das Echo 0: Der sichere Wert der Klimaanlage ist 0 (Plan 6a, Entscheidung 8), und am Ende soll
    kein Versatz bleiben, auch keiner, den ein früherer Lauf des core hinterließ.
    """

    def __init__(
        self,
        cfg: Config,
        name: str,
        *,
        store: StateStore,
        adapter: CoeAdapter,
        actuator: ThermalActuator,
        mute: _Mute,
        ask: Callable[[str], bool],
        unattended: bool = False,
        poll_s: float = 0.5,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(
            cfg,
            store=store,
            adapters=[adapter],
            actuators={actuator.id: actuator},
            ask=ask,
            monotonic=monotonic,
            poll_s=poll_s,
            unattended=unattended,
        )
        self.name = name
        self.adapter = adapter
        self.actuator = actuator
        self.mute = mute

    def echo(self, snap: Snapshot | None = None) -> float | None:
        """Das Echo der UVR (``present``); ``None`` ohne brauchbares Echo."""
        present = self.actuator.present(self.snapshot() if snap is None else snap)
        return finite_number(present.get(KIND))

    async def send_and_confirm(self, value: float, timeout_s: float) -> float | None:
        """Sendet den Versatz über den Aktor und wartet, bis das Echo ihn zeigt.

        Vorher prüfen ihn die Regeln 1–3 der Safety-Schicht; würden sie ihn klemmen oder ablehnen,
        ist das ein Fehler des Werkzeugs (``ValueError``). Ein Versatz außer 0 bekommt die
        Freigabe des Adapters für ``timeout_s`` und ``PERMIT_MARGIN_S``; 0 nimmt sie zurück, der
        Adapter hält und sendet dann 0. Ergebnis sind die Sekunden bis zum passenden Echo, sonst
        ``None``; dann nennt ``failure`` den Grund.
        """
        step = check_range(Command(self.actuator.id, KIND, value), self.limits, None)
        if step.reject or step.findings:
            problems = " ".join(finding.text for finding in step.findings) or "abgelehnt"
            raise ValueError(f"Versatz {kelvin(value)} nicht zulässig: {problems}")
        self.failure = None
        self.sent = False
        self.adapter.permit_for(timeout_s + PERMIT_MARGIN_S if value != 0 else 0.0)
        refusal = self.actuator.send(KIND, value)
        if refusal is not None:
            self.failure = f"nicht gesendet: {refusal}"
            return None
        self.sent = True

        def shown(snap: Snapshot) -> bool:
            state, _ = self.actuator.confirm(KIND, value, snap)
            return state == Confirmation.CONFIRMED

        waited = await self.wait_for(shown, timeout_s)
        if waited is None:
            self.failure = f"Echo nach {de(timeout_s)} s {kelvin(self.echo())}"
        return waited

    async def restore(self, timeout_s: float = RESTORE_S) -> list[Finding]:
        """Sendet 0 (der Port spricht wieder) und wartet bis ``timeout_s`` auf das Echo 0.

        Ohne Werte der Klimaanlage geht die 0 trotzdem hinaus; auf ein Echo wartet die Sitzung dann
        nicht. Der Befund erscheint auch auf der Konsole.
        """
        title = "Am Ende: Versatz 0"
        self.mute.muted = False
        # die Quelle, nicht die Fähigkeiten: Ohne Echo ist der Aktor nicht steuerbar (Ruling R27),
        # die Werte kommen aber, und das fehlende Echo ist der Befund
        connected = self._store.connected(coe_source(self.name)) is True
        try:
            waited = await self.send_and_confirm(0.0, timeout_s if connected else 0.0)
        except Exception as exc:  # ein Befund statt eines Abbruchs
            finding = Finding(ITEM, title, "fail", f"{type(exc).__name__}: {exc}")
        else:
            finding = self._end_finding(title, waited, connected)
        show_finding(finding)
        return [finding]

    def _end_finding(self, title: str, waited: float | None, connected: bool) -> Finding:
        if not self.sent:
            return Finding(ITEM, title, "fail", self.failure or "nicht gesendet")
        if waited is not None:
            return Finding(ITEM, title, "ok", f"Echo 0 K nach {seconds(waited)} s")
        if not connected:
            return Finding(ITEM, title, "info", "0 gesendet; ohne Werte der Klimaanlage kein Echo")
        return Finding(ITEM, title, "fail", f"0 gesendet, aber {self.failure}")


def build_thermal_session(
    cfg: Config,
    name: str,
    *,
    ask: Callable[[str], bool],
    unattended: bool = False,
    port_factory: PortFactory = udp_port,
    poll_s: float = 0.5,
) -> ThermalSession:
    """Die Sitzung mit dem echten CoE-Adapter und dem Aktor wie im core (``CoreRuntime``).

    Der Aktor ist steuerbar, solange die Quelle ``thermal.<name>`` Werte liefert und das Echo
    kommt. ``port_factory`` öffnet den Port (an der Anlage der echte UDP-Port). ``ValueError``,
    wenn ``name`` kein Gerät der Art ``coe_offset`` ist.
    """
    device = cfg.thermal.devices.get(name)
    if not isinstance(device, CoeOffsetConfig):
        raise ValueError(NOT_FOUND.format(name))
    store = StateStore(SystemClock())
    mute = _Mute(port_factory)
    adapter = CoeAdapter(name, device, store, mute.open)

    def connected() -> bool:
        return store.connected(coe_source(name)) is True

    actuator = ThermalActuator(name, device, adapter.set_offset, connected)
    return ThermalSession(
        cfg,
        name,
        store=store,
        adapter=adapter,
        actuator=actuator,
        mute=mute,
        ask=ask,
        unattended=unattended,
        poll_s=poll_s,
    )


async def run_thermal(
    session: ThermalSession, cfg: Config, name: str, *, timings: ThermalTimings = DEFAULT_TIMINGS
) -> list[Finding]:
    """Führt den Teil Klimaanlage aus; die Befunde in der Reihenfolge des Berichts.

    Jeder Befund steht auch sofort in ``session.findings``. Die 0 am Ende sendet danach
    ``session.restore``. ``name`` muss ein Gerät der Art ``coe_offset`` sein, sonst ``ValueError``.
    """
    device = cfg.thermal.devices.get(name)
    if not isinstance(device, CoeOffsetConfig):
        raise ValueError(NOT_FOUND.format(name))
    first = len(session.findings)
    await _ThermalSteps(session, name, device, timings).run()
    return session.findings[first:]


class _ThermalSteps:
    """Die vier Schritte eines Laufs."""

    def __init__(
        self, session: ThermalSession, name: str, device: CoeOffsetConfig, timings: ThermalTimings
    ) -> None:
        self._session = session
        self._name = name
        self._device = device
        self._t = timings
        self._prefix = f"thermal.{name}."
        self._signals = {
            key: self._prefix + ("power_w" if key == "power_kw" else key)
            for key in device.receive.channels()
        }

    async def run(self) -> None:
        if not await self._values():
            return
        await self._offset()
        await self._zero()
        await self._timeout()

    async def _values(self) -> bool:
        """Alle Werte der Empfangstabelle sind da, sonst FEHLER und Ende ohne Senden."""
        title = "Werte der Empfangstabelle"
        wait_s = self._t.values_s if self._t.values_s is not None else self._device.stale_after_s
        waited = await self._session.wait_for(lambda snap: not self._missing(snap), wait_s)
        snap = self._session.snapshot()
        if waited is None:
            missing = ", ".join(_LABELS.get(key, (key, ""))[0] for key in self._missing(snap))
            detail = f"nach {de(wait_s)} s fehlen: {missing}"
            stats = self._session.adapter.stats()
            if stats["unit_mismatch"]:
                detail += f"; Einheit oder Art passt nicht ({stats['unit_mismatch']}-mal)"
            if stats["foreign_packets"]:
                foreign = stats["foreign_packets"]
                detail += f"; Pakete ohne Wert der Tabelle oder von fremder Adresse: {foreign}"
            self._record(title, "fail", detail)
            return False
        shown = ", ".join(self._shown(key, snap) for key in self._signals)
        mode = thermal_mode(snap, self._name)
        count = len(self._signals)
        detail = f"alle {count} Werte nach {seconds(waited)} s: {shown}; Betriebsart {mode}"
        self._record(title, "ok", detail)
        return True

    def _missing(self, snap: Snapshot) -> list[str]:
        return [key for key, signal in self._signals.items() if snap.ok(signal) is None]

    def _shown(self, key: str, snap: Snapshot) -> str:
        label, unit = _LABELS.get(key, (key, ""))
        value = snap.ok(self._signals[key])
        if isinstance(value, bool):
            return f"{label} {'ja' if value else 'nein'}"
        number = finite_number(value)
        text = repr(value) if number is None else de(round(number, 1))
        return f"{label} {text} {unit}".rstrip()

    async def _offset(self) -> None:
        """+0,5 K senden, das Echo zeigt es; dann die Rückfrage zur Visu der UVR."""
        title = f"Versatz {kelvin(TEST_OFFSET_K)}"
        waited = await self._session.send_and_confirm(TEST_OFFSET_K, self._t.echo_s)
        if waited is None:
            self._record(title, "fail", self._session.failure or "kein Echo")
            return
        self._record(title, "ok", f"Echo {kelvin(TEST_OFFSET_K)} nach {seconds(waited)} s")
        visu = "Visu der UVR"
        if self._session.unattended:  # niemand sieht auf die Visu
            self._record(visu, "info", SKIPPED_UNATTENDED)
        elif self._session.ask(QUESTION_VISU):
            self._record(visu, "ok", f"zeigt „EMS“ {kelvin(TEST_OFFSET_K)}")
        else:
            self._record(visu, "fail", f"zeigt „EMS“ nicht {kelvin(TEST_OFFSET_K)}")

    async def _zero(self) -> None:
        """0 senden, das Echo zeigt 0."""
        title = "Versatz 0"
        waited = await self._session.send_and_confirm(0.0, self._t.echo_s)
        if waited is None:
            self._record(title, "fail", self._session.failure or "kein Echo")
            return
        self._record(title, "ok", f"Echo 0 K nach {seconds(waited)} s")

    async def _timeout(self) -> None:
        """+0,5 K senden, dann schweigen: Die UVR setzt den Versatz von selbst auf 0."""
        title = "Timeout der UVR"
        if self._session.unattended:  # der Schritt dauert Minuten und braucht den Betreiber
            self._record(title, "info", SKIPPED_UNATTENDED)
            return
        if not self._session.ask(QUESTION_TIMEOUT):
            self._record(title, "info", SKIPPED)
            return
        waited = await self._session.send_and_confirm(TEST_OFFSET_K, self._t.echo_s)
        if waited is None:
            failure = self._session.failure or "kein Echo"
            self._record(title, "fail", f"{kelvin(TEST_OFFSET_K)}: {failure}")
            return
        self._session.mute.muted = True  # ab jetzt geht kein Paket mehr hinaus
        try:
            fell = await self._session.wait_for(self._echo_zero, self._t.timeout_s)
        finally:
            self._session.mute.muted = False
            self._session.adapter.permit_for(0.0)  # hält und sendet ab jetzt wieder 0
        if fell is None:
            echo = kelvin(self._session.echo())
            self._record(title, "fail", f"nach {de(self._t.timeout_s)} s ohne Paket {echo}")
            return
        self._record(title, "ok", f"Echo 0 K nach {seconds(fell)} s ohne Paket")

    def _echo_zero(self, snap: Snapshot) -> bool:
        echo = self._session.echo(snap)
        return echo is not None and abs(echo) < 0.05

    def _record(self, title: str, status: Status, detail: str) -> None:
        self._session.record(Finding(ITEM, title, status, detail))
