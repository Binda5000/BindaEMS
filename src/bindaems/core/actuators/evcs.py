"""EVCS-Aktor: schreibt über den GX-Dienst ``evcharger`` des Cerbo, liest aus dem Modbus-Abbild.

Den Schreibweg hat der Betreiber so entschieden (Plan 2b, Präzisierung 1): der Sollstrom in ganzen
Ampere auf ``evcharger/<Instanz>/SetCurrent``, Laden an/aus (1/0) auf
``evcharger/<Instanz>/StartStop``. Geschrieben wird über die Funktion ``send`` (im core
``VictronMqttAdapter.write``), die den Auftrag nur einreiht; der Aktor wartet nie. Den Modus und den
Maximalstrom der EVCS schreibt das EMS nie.

Zurückgelesen wird, was die EVCS selbst meldet (``wallbox.<name>.set_current_a`` und
``start_stop`` aus dem Modbus-Abbild), ersatzweise, was der Cerbo über sie weiß
(``gx_set_current_a`` und ``gx_start_stop``). Ein Wert zählt nur mit Qualität ``OK`` und als
endliche Zahl; der Modbus-Adapter speichert ein Abbild ohne bekannte Produkt-ID als ungültig.
Ohne brauchbaren Wert ist die Art unbekannt.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Final

from bindaems.core.actuators import REASON_NOT_ACCEPTED, REASON_NOT_CONNECTED
from bindaems.core.adapters.victron_mqtt import evcharger_path
from bindaems.core.checks.values import evcs_mode
from bindaems.core.control.actuator import Capabilities, Confirmation, matches
from bindaems.shared.config import EvcsConfig
from bindaems.shared.control import CommandKind, CommandValue, wallbox_actuator
from bindaems.shared.domain import Alarm, Snapshot
from bindaems.shared.numbers import finite_number

# Gründe, warum die EVCS nicht steuerbar ist (Plan „Texte“); die Gründe zum Cerbo (nicht verbunden,
# Auftrag nicht angenommen) teilt sie mit Victron und stehen in ``core.actuators``
REASON_NO_INSTANCE: Final = "Keine evcharger-Instanz in victron.instances.evcharger."
REASON_NOT_MANUAL: Final = "EVCS nicht im manuellen Modus."

# Signale zum Rücklesen, in der Reihenfolge der Quellen: erst das Modbus-Abbild, dann der Cerbo
_CURRENT_SIGNALS: Final = ("set_current_a", "gx_set_current_a")
_CHARGING_SIGNALS: Final = ("start_stop", "gx_start_stop")


class EvcsActuator:
    """Eine Victron EVCS NS als Aktor (Plan 2b, Präzisierung 1 und 12).

    ``key`` ist der Schlüssel der Wallbox in ``wallboxes``, ``instance`` die Instanz des GX-Dienstes
    ``evcharger`` aus ``victron.instances.evcharger`` oder ``None``, wenn die Konfiguration keine
    kennt; ohne sie ist die EVCS nicht steuerbar. ``send`` reiht einen Schreibauftrag (Pfad, Wert)
    ein und sagt, ob er angenommen wurde; ``connected`` sagt, ob die Verbindung zum Cerbo steht.
    ``cfg`` ist die Konfiguration der Wallbox; der Aktor liest sie nicht, denn die Grenzen für den
    Strom prüft die Safety-Schicht (``HardLimits.from_config``) aus denselben Werten.
    """

    confirm_timeout_s = 10.0

    def __init__(
        self,
        key: str,
        cfg: EvcsConfig,
        instance: int | None,
        send: Callable[[str, float | int | None], bool],
        connected: Callable[[], bool],
    ) -> None:
        self.id = wallbox_actuator(key)
        self._key = key
        self._instance = instance
        self._write = send
        self._connected = connected

    def capabilities(self, snap: Snapshot, now: datetime) -> Capabilities:
        """Steuerbar mit Instanz, verbundenem Cerbo und EVCS im manuellen Modus.

        Die Gründe stehen in dieser Reihenfolge. Ein unbekannter Modus zählt als „nicht manuell“.
        """
        reasons: list[str] = []
        if self._instance is None:
            reasons.append(REASON_NO_INSTANCE)
        if not self._connected():
            reasons.append(REASON_NOT_CONNECTED)
        if evcs_mode(snap, self._key) != 0:  # 0 = manuell; ohne Wert (None) ebenfalls ein Grund
            reasons.append(REASON_NOT_MANUAL)
        return Capabilities(not reasons, tuple(reasons))

    def present(self, snap: Snapshot) -> dict[CommandKind, CommandValue]:
        """Sollstrom in A und Laden an/aus, wie das Gerät sie meldet; was fehlt, ist unbekannt.

        Jede Art kommt aus der ersten Quelle mit brauchbarem Wert. Laden ist nur 1 (``True``) oder
        0 (``False``); jeder andere Wert fällt zur nächsten Quelle und sonst heraus. Beim Strom ist
        das der Sollstrom, nicht der Messstrom und nicht der Maximalstrom.
        """
        prefix = f"wallbox.{self._key}."
        present: dict[CommandKind, CommandValue] = {}
        for name in _CURRENT_SIGNALS:
            current = finite_number(snap.ok(prefix + name))
            if current is not None:
                present[CommandKind.CURRENT] = current
                break
        for name in _CHARGING_SIGNALS:
            flag = finite_number(snap.ok(prefix + name))
            if flag in (0.0, 1.0):
                present[CommandKind.CHARGING] = flag == 1.0
                break
        return present

    def send(self, kind: CommandKind, value: CommandValue) -> str | None:
        """Reiht den Auftrag beim GX-Dienst ein: den Strom in ganzen Ampere, Laden als 1 oder 0.

        ``None``, wenn der Auftrag angenommen ist; sonst der Grund, warum nicht gesendet wurde.
        Eine andere Art oder einen Strom, der keine endliche Zahl ist, schreibt der Aktor nie.
        """
        if self._instance is None:  # ohne Instanz gibt es keinen Pfad, und der Adapter würfe
            return REASON_NO_INSTANCE
        if kind == CommandKind.CURRENT:
            amps = finite_number(value)
            if amps is None:
                return f"Ungültiger Wert für „{kind}“."
            path, payload = evcharger_path(self._instance, "SetCurrent"), round(amps)
        elif kind == CommandKind.CHARGING:
            path, payload = evcharger_path(self._instance, "StartStop"), 1 if value else 0
        else:
            return f"Die EVCS kennt die Befehlsart „{kind}“ nicht."
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
        """Die EVCS hat keine eigenen Alarme."""
        return []
