"""Tesla in der Laderegelung: Strom, Weckstart und „Tesla an der EVCS“ (Design 6.5, 7, 8).

Die Bausteine, die ``ChargeController`` für Fahrzeuge mit Tessie braucht (Präzisierungen 5 und 12):

- ``TeslaPolicy``: je Fahrzeug das gleitende Mittel des Wunsches, das vorsichtige Minimum des
  freien Stroms und das Weckkontingent
- ``EvcsOverrides``: die Fahrzeuge mit Tessie, die laut Übersteuerung an einer EVCS hängen
- die Gründe ``wake`` und ``assignment_unclear``

Die Schicht ist rein: Die Zeit kommt als Argument, auf der Uhr der Safety-Schicht; nur der
Kalendertag des Weckkontingents gilt nach der Wanduhr.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Final

from bindaems.core.control.assignment import AssignmentKind, VehicleState
from bindaems.core.control.phase_guard import SlidingMin
from bindaems.core.control.tesla_policy import MovingAverage, WakeQuota
from bindaems.shared.config import Config, ControlConfig, EvcsConfig, WakeConfig
from bindaems.shared.control import vehicle_actuator

WAKE: Final = "wake"  # ein Start mit Weckerlaubnis
ASSIGNMENT_UNCLEAR: Final = "assignment_unclear"  # die Zuordnung ist unklar (Präzisierung 5)


class TeslaPolicy:
    """Strom und Wecken eines Fahrzeugs mit Tessie am Wall Connector (Präzisierung 12).

    - Der Strom ist ``floor(min(Mittel des Wunsches über tesla_average_s, niedrigster freier Wert
      über tesla_caution_s))``; die Grenzen seines Ladepunkts setzt die Regelung danach.
    - Weckstarts begrenzt ``WakeQuota(wake.interval_s, wake.per_day)``.

    Beide Fenster beginnen mit ``restart`` neu: mit jedem neuen Ansteckvorgang und nach einer Pause.
    Das Kontingent bleibt, solange der core läuft.
    """

    def __init__(self, control: ControlConfig, wake: WakeConfig) -> None:
        self._average_s = control.tesla_average_s
        self._caution_s = control.tesla_caution_s
        self._quota = WakeQuota(wake.interval_s, wake.per_day)
        self._average = MovingAverage(self._average_s)
        self._caution = SlidingMin(self._caution_s)

    def restart(self) -> None:
        """Beide Fenster beginnen neu; ``SlidingMin`` kennt kein Leeren, es gibt ein neues."""
        self._average = MovingAverage(self._average_s)
        self._caution = SlidingMin(self._caution_s)

    def limits(self, now: datetime, wanted: int | None, free: float | None) -> tuple[int, int]:
        """Nimmt Wunsch und freien Strom dieses Zyklus auf; zurück kommen Wunsch und Grenze in A.

        ``wanted`` ist der Wunsch eines Autos, das laden kann; ``None`` heißt, es kann nicht laden
        (abgesteckt, „geladen“ oder Aus) und wünscht nichts: Das Mittel bekommt dann keinen Wert.
        ``free`` ist der freie Strom des Wächters; ein unbekannter zählt im Fenster als 0.

        Der Wunsch ist das abgerundete Mittel, ohne einen Wert im Fenster 0. Die Grenze ist der
        abgerundete niedrigste freie Wert im Fenster, diesen eingeschlossen. Der Strom ist dann
        ``min(Wunsch, Grenze)``, denn ``floor(min(a, b)) = min(floor(a), floor(b))``.
        """
        if wanted is not None:
            self._average.add(now, float(wanted))
        value = 0.0 if free is None else free
        lowest = self._caution.value(now)  # vor dem neuen Wert; ``None`` bei leerem Fenster
        self._caution.add(now, value)
        average = self._average.value(now)
        wish = 0 if average is None else math.floor(average)
        return wish, math.floor(value if lowest is None else min(lowest, value))

    def may_wake(self, now: datetime, wall_now: datetime) -> bool:
        """Ob ein Weckstart jetzt ins Kontingent passt (Abstand und Zahl je Kalendertag)."""
        return self._quota.allowed(now, wall_now)

    def note_wake(self, now: datetime, wall_now: datetime) -> None:
        """Zählt einen Weckstart, der ein Urteil mit Wert bekam, live wie im Dry-Run."""
        self._quota.note(now, wall_now)

    def wakes_today(self, wall_now: datetime) -> int:
        """Die Weckstarts am Kalendertag (Wiener Zeit) von ``wall_now``."""
        return self._quota.today(wall_now)


class EvcsOverrides:
    """„Tesla an der EVCS“: Fahrzeuge mit Tessie, laut Übersteuerung an einer EVCS (Design 8).

    Geregelt wird dann die EVCS mit den Werten des Fahrzeugs; das Fahrzeug selbst bekommt einmal je
    Ansteckvorgang seinen Höchststrom. Dafür merkt sich die Klasse, an welcher EVCS jedes im letzten
    Zyklus hing.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._seen: dict[str, str] = {}  # Fahrzeug → EVCS im letzten Zyklus

    def update(self, vehicles: Mapping[str, VehicleState]) -> tuple[set[str], set[str]]:
        """Die Fahrzeuge an einer EVCS und die Aktoren derer, die neu an ihre EVCS kamen.

        Neu ist ein Fahrzeug an einer EVCS, an der es im letzten Zyklus nicht hing. Das ist auch
        jeder neue Ansteckvorgang: Eine Übersteuerung gilt nur für einen Vorgang (Präzisierung 3),
        und zwischen zwei Vorgängen liegt ein Zyklus, in dem das Fahrzeug nicht an der EVCS hängt.
        """
        found: dict[str, str] = {}
        for key, state in vehicles.items():
            wallbox = state.wallbox
            if (
                wallbox is not None
                and state.kind is AssignmentKind.OVERRIDE
                and isinstance(self._cfg.wallboxes[wallbox], EvcsConfig)
                and self._cfg.vehicles[key].tessie is not None
            ):
                found[key] = wallbox
        arrived = {vehicle_actuator(k) for k, w in found.items() if self._seen.get(k) != w}
        self._seen = found
        return set(found), arrived
