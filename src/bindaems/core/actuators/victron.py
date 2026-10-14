"""Victron-Aktor: Netz-Sollwert-Override, Entladegrenze und Min-SOC, geschrieben per MQTT am Cerbo.

Die Schreibwege stehen in Plan 2b, Präzisierung 1:

- ``hub4/0/Overrides/Setpoint``: Netz-Sollwert als flüchtiger Override in W; ``None`` hebt ihn auf
- ``settings/0/Settings/CGwacs/MaxDischargePower``: Entladegrenze in W (−1 = unbegrenzt), persistent
- ``settings/0/Settings/CGwacs/BatteryLife/MinimumSocLimit``: Min-SOC in %, persistent

Geschrieben wird über die Funktion ``send`` (im core ``VictronMqttAdapter.write``), die den Auftrag
nur einreiht; der Aktor wartet nie. Modbus TCP zum Cerbo (Register 2716/2717) bleibt ungenutzt.

Zurückgelesen wird, was der Cerbo meldet: ``ess.override.setpoint``, ``ess.max_discharge_w`` und
``ess.min_soc_pct``. Entladegrenze und Min-SOC zählen nur mit Qualität ``OK`` und als endliche
Zahl; ohne brauchbaren Wert ist die Art unbekannt. Beim Netz-Sollwert ist „kein Override aktiv“
(``None``) etwas anderes als „unbekannt“ (die Art fehlt): Ein ``null`` des Cerbo (der D-Bus-Wert
ist ungültig) erreicht den Zustandsspeicher als ungültiger Wert und heißt „kein Override“, solange
die Verbindung zum Cerbo steht. Das ist vorläufig: Dass ``null`` den Override aufhebt und der Cerbo
danach „kein Override“ so meldet, bestätigt erst das Prüfprotokoll Teil 2, Punkt 17.2-1 (Plan 2b,
Präzisierung 1).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Final

from bindaems.core.actuators import REASON_NOT_ACCEPTED, REASON_NOT_CONNECTED
from bindaems.core.adapters.victron_mqtt import (
    MAX_DISCHARGE_PATH,
    MIN_SOC_PATH,
    SETPOINT_OVERRIDE_PATH,
)
from bindaems.core.control.actuator import Capabilities, Confirmation, matches
from bindaems.shared.control import VICTRON, CommandKind, CommandValue
from bindaems.shared.domain import Alarm, Quality, Snapshot
from bindaems.shared.numbers import finite_number

# So lange hält der Cerbo einen Override ohne Schreiben; danach fällt er zurück (Prüfprotokoll
# Teil 2). Die Regelung frischt ihn davor auf (``control.override_refresh_s``, höchstens 280 s).
OVERRIDE_TIMEOUT_S: Final = 300.0

# Schreibpfade je Art
_PATHS: Final = {
    CommandKind.GRID_SETPOINT: SETPOINT_OVERRIDE_PATH,
    CommandKind.MAX_DISCHARGE: MAX_DISCHARGE_PATH,
    CommandKind.MIN_SOC: MIN_SOC_PATH,
}

# Signale zum Rücklesen: der Override für sich, die beiden Einstellungen als Zahl
_OVERRIDE_SIGNAL: Final = "ess.override.setpoint"
_SETTING_SIGNALS: Final = {
    CommandKind.MAX_DISCHARGE: "ess.max_discharge_w",
    CommandKind.MIN_SOC: "ess.min_soc_pct",
}


class VictronActuator:
    """Die Victron-Anlage als Aktor (Plan 2b, Präzisierung 1 und 12).

    ``send`` reiht einen Schreibauftrag (Pfad, Wert) ein und sagt, ob er angenommen wurde;
    ``connected`` sagt, ob die Verbindung zum Cerbo steht. Sie entscheidet über die Fähigkeiten und
    darüber, ob ein ungültiger Override-Wert „kein Override“ heißt. Die Grenzen für die Werte prüft
    die Safety-Schicht, nicht der Aktor.
    """

    id = VICTRON
    confirm_timeout_s = 10.0

    def __init__(
        self,
        send: Callable[[str, float | int | None], bool],
        connected: Callable[[], bool],
    ) -> None:
        self._write = send
        self._connected = connected

    def capabilities(self, snap: Snapshot, now: datetime) -> Capabilities:
        """Steuerbar, solange die Verbindung zum Cerbo steht; sie wird bei jeder Frage geprüft."""
        if self._connected():
            return Capabilities(True)
        return Capabilities(False, (REASON_NOT_CONNECTED,))

    def present(self, snap: Snapshot) -> dict[CommandKind, CommandValue]:
        """Override, Entladegrenze und Min-SOC, wie der Cerbo sie meldet; was fehlt, ist unbekannt.

        Entladegrenze und Min-SOC zählen als endliche Zahl mit Qualität ``OK``. Der Netz-Sollwert:

        - Eine endliche Zahl mit Qualität ``OK`` ist der Override.
        - Der Wert ``None`` mit Qualität ``OK`` oder ``INVALID`` heißt „kein Override aktiv“
          (``None``), solange der Cerbo verbunden ist. Ein ``null`` des Cerbo (der D-Bus-Wert ist
          ungültig) erreicht den Zustandsspeicher als ``INVALID``. Vorläufig, bis das
          Prüfprotokoll Teil 2, Punkt 17.2-1, das Verhalten des Cerbo bestätigt.
        - Alles andere lässt die Art fehlen: ein fehlendes oder veraltetes Signal, NaN, ±∞, Text,
          ein Wahrheitswert und ``None`` ohne Verbindung zum Cerbo.
        """
        present: dict[CommandKind, CommandValue] = {}
        # Nicht ``snap.ok``: Das gibt für ein fehlendes Signal und für „OK mit Wert None“ dasselbe.
        override = snap.get(_OVERRIDE_SIGNAL)
        if override is not None:
            if override.value is None:
                if override.quality in (Quality.OK, Quality.INVALID) and self._connected():
                    present[CommandKind.GRID_SETPOINT] = None  # kein Override aktiv
            elif override.quality is Quality.OK:
                setpoint = finite_number(override.value)
                if setpoint is not None:
                    present[CommandKind.GRID_SETPOINT] = setpoint
        for kind, signal in _SETTING_SIGNALS.items():
            number = finite_number(snap.ok(signal))
            if number is not None:
                present[kind] = number
        return present

    def send(self, kind: CommandKind, value: CommandValue) -> str | None:
        """Reiht den Auftrag beim Cerbo ein: der Wert geht als Gleitkommazahl hinaus.

        ``None``, wenn der Auftrag angenommen ist; sonst der Grund, warum nicht gesendet wurde.
        Eine andere Art oder einen Wert, der keine endliche Zahl ist, schreibt der Aktor nie;
        ``None`` ist nur beim Netz-Sollwert ein Wert und hebt den Override auf.
        """
        path = _PATHS.get(kind)
        if path is None:
            return f"Victron kennt die Befehlsart „{kind}“ nicht."
        payload: float | None
        if kind == CommandKind.GRID_SETPOINT and value is None:
            payload = None
        else:
            payload = finite_number(value)
            if payload is None:
                return f"Ungültiger Wert für „{kind}“."
        return None if self._write(path, payload) else REASON_NOT_ACCEPTED

    def confirm(
        self, kind: CommandKind, value: CommandValue, snap: Snapshot
    ) -> tuple[Confirmation, str]:
        """Bestätigt, sobald das Rücklesen ``value`` zeigt, sonst offen.

        Scheitern kann das Rücklesen hier nicht; die Frist überwacht der Ausführer.
        """
        present = self.present(snap)
        if kind in present and matches(kind, value, present[kind]):
            return Confirmation.CONFIRMED, ""
        return Confirmation.PENDING, ""

    def alarms(self, now: datetime) -> list[Alarm]:
        """Victron hat keine eigenen Alarme."""
        return []
