"""Tesla in der Laderegelung: Strom, Weckstart und „Tesla an der EVCS“ (Design 6.5, 7, 8).

Die Bausteine, die ``ChargeController`` für Fahrzeuge mit Tessie braucht (Präzisierungen 5 und 12):

- ``TeslaPolicy``: je Fahrzeug das gleitende Mittel des Wunsches, das vorsichtige Minimum des
  freien Stroms und das Weckkontingent
- ``EvcsOverrides``: die Fahrzeuge mit Tessie, die laut Übersteuerung oder Erkennung an einer EVCS
  hängen
- ``maximum_command`` und ``wake_command``: der Höchststrom „Tesla an der EVCS“ und der Start mit
  Weckerlaubnis, jeweils als Befehl
- ``tessie_current``: der Strom, den Tessie meldet, auf den Phasen der Wallbox
- die Gründe ``wake``, ``assignment_unclear`` und ``assignment_detected``

Tesla, Zuordnung und Wiederaufnahme (Design 7 bis 9; ``ChargeController`` verbindet die Bausteine):

- Ein Fahrzeug mit Tessie am Wall Connector bekommt ``floor(min(Mittel des Wunsches, niedrigster
  freier Wert))`` über ``control.tesla_average_s`` bzw. ``control.tesla_caution_s``, in seinem
  Strombereich (Präzisierung 12). Der Wunsch zählt nur, solange das Auto laden kann. Beide
  Fenster beginnen mit jedem neuen Ansteckvorgang und nach ``idle`` neu; wird der Ladepunkt nur
  wieder geregelt, behalten sie ihre Werte.
- Will der Modus starten und ist der Aktor nicht steuerbar, aber weckbar, kommt ein Start mit
  Weckerlaubnis (``wake``) ohne Strom: bei bekanntem Ansteckzustand, ohne Überlast und im
  Weckkontingent. Das Kontingent zählt jeden Weckstart mit einem Urteil mit Wert.
- „Tesla an der EVCS“: Hängt ein Fahrzeug mit Tessie laut Übersteuerung oder Erkennung an einer
  EVCS (Plan 2c-2b, Präzisierung 7), bekommt es mit jeder Ankunft dort einmal seinen Höchststrom
  mit dem Modus als Grund, sobald es steuerbar ist und die EVCS geregelt wird; geregelt wird dann
  die EVCS. Wechselt an derselben EVCS nur die Art (erkannt ↔ Übersteuerung), ist das keine
  Ankunft. Erledigt ist das wie jeder Befehl (Präzisierung 10); nach ``idle`` geht es noch einmal
  hinaus.
- Ab einer neuen Erkennung tragen die Befehle eines Autos zuletzt ``assignment_detected``, bis zum
  ersten Zyklus mit Befehlen für das Auto, den Höchststrom eingeschlossen; ist es vorher nicht mehr
  erkannt, entfällt der Grund (Plan 2c-2b, Präzisierung 8).
- Solange die Zuordnung unklar ist (Präzisierung 5), gibt es kein „Laden an/aus“ nach dem Modus;
  der Strom sinkt nur, wenn der Wächter weniger als den Ist-Strom erlaubt, nie unter den
  Mindeststrom. Die Gründe sind ``(Modus, "assignment_unclear", Ursache)``. Die Überlast setzt
  weiter den Mindeststrom und stoppt ab ``EVCS_STOP``. Fahrzeuge mit Tessie pausieren
  (Präzisierung 6).
- Nach ``idle`` trägt die erste Startfolge (Strom und ``charging=True``) je Fahrzeug zusätzlich
  ``resume_after_outage``, bis die Safety-Schicht ihren Start mit Wert oder ``UNCHANGED``
  beurteilt. Das gilt erst, wenn seit dem Start des core schon geregelt wurde.

Die Schicht ist rein: Die Zeit kommt als Argument, auf der Uhr der Safety-Schicht; nur der
Kalendertag des Weckkontingents gilt nach der Wanduhr.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Final

from bindaems.core.control.assignment import AssignmentKind, VehicleState
from bindaems.core.control.charge_inputs import ChargeInputs, VehicleObs
from bindaems.core.control.charge_modes import MODE_CODES, Effective
from bindaems.core.control.last_values import Key, LastValues
from bindaems.core.control.phase_guard import SlidingMin
from bindaems.core.control.tesla_policy import MovingAverage, WakeQuota
from bindaems.shared.config import Config, ControlConfig, EvcsConfig, Phase, PhaseMap, WakeConfig
from bindaems.shared.control import Command, CommandKind, vehicle_actuator

WAKE: Final = "wake"  # ein Start mit Weckerlaubnis
ASSIGNMENT_UNCLEAR: Final = "assignment_unclear"  # die Zuordnung ist unklar (Präzisierung 5)
# der Befehl folgt aus einer neuen Erkennung (Plan 2c-2b, Präzisierung 8)
ASSIGNMENT_DETECTED: Final = "assignment_detected"

# So steht ein Fahrzeug „an der EVCS“ (Plan 2c-2b, Präzisierung 7)
_AT_THE_EVCS: Final = frozenset({AssignmentKind.OVERRIDE, AssignmentKind.DETECTED})


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
    """„Tesla an der EVCS“: Fahrzeuge mit Tessie, laut Übersteuerung oder Erkennung an einer EVCS
    (Design 8; Plan 2c-2b, Präzisierung 7).

    Geregelt wird dann die EVCS mit den Werten des Fahrzeugs; das Fahrzeug selbst bekommt mit
    jeder Ankunft an der EVCS einmal seinen Höchststrom. Dafür merkt sich die Klasse, an welcher
    EVCS jedes im letzten Zyklus hing, gleich mit welcher Art.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._seen: dict[str, str] = {}  # Fahrzeug → EVCS im letzten Zyklus

    def update(self, vehicles: Mapping[str, VehicleState]) -> tuple[set[str], set[str]]:
        """Die Fahrzeuge an einer EVCS und die Aktoren derer, die neu an ihre EVCS kamen.

        An einer EVCS hängt ein Fahrzeug mit Tessie, das dort übersteuert oder erkannt ist;
        Schlüssel, die keine Fahrzeuge der Konfiguration sind (Gäste), übergeht die Klasse. Neu ist
        ein Fahrzeug an einer EVCS, an der es im letzten Zyklus nicht hing; wechselt dort nur die
        Art, ist es nicht neu. Neu ist es auch mit jedem neuen Ansteckvorgang: Übersteuerung und
        Erkennung gelten nur für einen Vorgang (Präzisierung 3; Plan 2c-2b, Präzisierung 4), und
        zwischen zwei Vorgängen liegt ein Zyklus, in dem das Fahrzeug nicht an der EVCS hängt.
        """
        found: dict[str, str] = {}
        for key, state in vehicles.items():
            vehicle = self._cfg.vehicles.get(key)  # ``None`` für einen Gast
            wallbox = state.wallbox
            if (
                vehicle is not None
                and vehicle.tessie is not None
                and wallbox is not None
                and state.kind in _AT_THE_EVCS
                and isinstance(self._cfg.wallboxes[wallbox], EvcsConfig)
            ):
                found[key] = wallbox
        arrived = {vehicle_actuator(k) for k, w in found.items() if self._seen.get(k) != w}
        self._seen = found
        return set(found), arrived


def maximum_command(
    cfg: Config, last: LastValues, key: str, mode: Effective, inputs: ChargeInputs
) -> list[Command]:
    """„Tesla an der EVCS“: der Höchststrom an das Fahrzeug selbst, sobald es steuerbar ist.

    Er geht hinaus, solange er sich gegenüber dem letzten eigenen Wert ändert;
    ``ChargeController.step`` vergisst den mit jeder Ankunft an der EVCS (``EvcsOverrides``). Grund
    ist der Modus; nach einer neuen Erkennung hängt die Regelung ``assignment_detected`` an.
    """
    actuator = vehicle_actuator(key)
    value = float(cfg.vehicles[key].max_a)
    steerable = inputs.controllable.get(actuator) is True
    if not steerable or (actuator, CommandKind.CURRENT) in inputs.blocked:
        return []
    if not last.changed((actuator, CommandKind.CURRENT), value):
        return []
    return [last.command(actuator, CommandKind.CURRENT, value, (MODE_CODES[mode],))]


def wake_command(
    last: LastValues,
    policy: TeslaPolicy,
    now: datetime,
    wall_now: datetime,
    actuator: str,
    reasons: tuple[str, ...],
    blocked: frozenset[Key],
) -> list[Command]:
    """Der Start mit Weckerlaubnis (Design 7.1): ``charging=True`` mit ``wake``, ohne Strom.

    Er kommt, wenn ``charging=True`` sich ändert, nicht blockiert ist und das Weckkontingent ihn
    erlaubt. Die Gründe ``reasons`` baut die Regelung: die eines Starts, danach ``wake``.
    """
    on = (actuator, CommandKind.CHARGING)
    if on in blocked or not last.changed(on, True):
        return []
    if not policy.may_wake(now, wall_now):
        return []
    return [last.command(actuator, CommandKind.CHARGING, True, reasons, wake=True)]


def tessie_current(tessie: VehicleObs, phase_map: PhaseMap) -> dict[Phase, float] | None:
    """Der Strom aus Tessie auf den ersten ``phases`` Phasen der ``phase_map``; sonst ``None``."""
    if not tessie.reachable or tessie.actual_current_a is None or tessie.phases is None:
        return None
    return {phase: tessie.actual_current_a for phase in phase_map[: tessie.phases]}
