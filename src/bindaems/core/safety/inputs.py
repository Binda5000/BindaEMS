"""Regel 7 der Safety-Schicht: Eingangsdaten auf Frische, Plausibilität und SOC-Sprünge prüfen.

``InputCheck`` entscheidet je Zyklus, ob die Messwerte brauchbar sind (Spec 8.3). Ein Problem ist:

1. Ein Sicherheitssignal (``SAFETY_SIGNALS``) fehlt oder hat nicht die Qualität ``OK``.
2. Die Plausibilitätsprüfung aus Phase 1 meldet einen Alarm (ID ``plaus.…``).
3. Der Akku-SOC ist gesprungen: Ändert er sich zwischen zwei Messungen, die höchstens 60 s
   auseinanderliegen, um mehr als 5 Prozentpunkte, gilt er 60 s lang als unplausibel.

Die Sprungerkennung gilt nur für den SOC. Netzleistungen springen legitim, etwa beim Start einer
Wallbox; für sie bleiben die Wertebereiche und die Energiebilanz der Plausibilitätsprüfung.

Die Schicht ist rein: Die Zeit kommt als Argument. Der Abstand zweier Messungen zählt nach den
Zeitstempeln der Messwerte (``Reading.ts``), die Haltezeit nach ``now``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final, NamedTuple

from bindaems.core.safety.types import InputStatus, de, finite_number
from bindaems.shared.domain import Alarm, Quality, Snapshot

_SOC: Final = "battery.soc_pct"

SAFETY_SIGNALS: Final = (
    "grid.l1.power_w",
    "grid.l2.power_w",
    "grid.l3.power_w",
    "grid.l1.voltage_v",
    "grid.l2.voltage_v",
    "grid.l3.voltage_v",
    _SOC,
    "battery.power_w",
)
SOC_JUMP_PP: Final = 5.0  # mehr Prozentpunkte Änderung sind ein Sprung, ...
SOC_JUMP_WINDOW_S: Final = 60.0  # ... wenn die Messungen höchstens so viele Sekunden trennen
SOC_JUMP_HOLD_S: Final = 60.0  # so lange gilt der SOC nach einem Sprung als unplausibel

_PLAUSIBILITY_PREFIX: Final = "plaus."  # Alarm-IDs der Plausibilitätsprüfung aus Phase 1


class _Soc(NamedTuple):
    """Eine gültige SOC-Messung: der Wert in % und der Zeitstempel des Messwerts."""

    pct: float
    ts: datetime


class _Jump(NamedTuple):
    """Ein erkannter Sprung von ``before`` auf ``after`` (in %), gehalten bis ``until``."""

    before: float
    after: float
    until: datetime


class InputCheck:
    """Regel 7: Sind die Eingangsdaten dieses Zyklus brauchbar?

    Der Zustand ist die letzte gültige SOC-Messung und der letzte erkannte Sprung. Dieselbe
    Messung zählt nur einmal: Wertet ein weiterer Zyklus denselben Messwert (gleicher
    ``Reading.ts``) noch einmal aus, ändert das weder die letzte Messung noch die Haltezeit.
    """

    def __init__(self) -> None:
        self._last_soc: _Soc | None = None
        self._jump: _Jump | None = None

    def evaluate(self, snap: Snapshot, plausibility: Sequence[Alarm], now: datetime) -> InputStatus:
        """Die Probleme der Eingangsdaten zum Zeitpunkt ``now``; ``ok`` heißt: keine.

        Die Probleme kommen in dieser Reihenfolge:

        1. ``Messwert fehlt oder ist veraltet: …``: in einer Meldung alle ``SAFETY_SIGNALS``
           ohne Qualität ``OK``, in der Reihenfolge der Liste.
        2. Die Meldung jedes Alarms aus ``plausibility``, dessen ID mit ``plaus.`` beginnt, in
           der übergebenen Reihenfolge. Andere Alarme zählen nicht.
        3. ``Sprung im Akku-SOC: a % → b %.``, solange die Haltezeit läuft.

        Ein Sprung ist eine neue gültige SOC-Messung (Qualität ``OK``, endliche Zahl, anderer
        ``Reading.ts`` als die letzte), die um mehr als ``SOC_JUMP_PP`` von der letzten abweicht
        und höchstens ``SOC_JUMP_WINDOW_S`` von ihr entfernt liegt. Er setzt die Haltezeit auf
        ``now + SOC_JUMP_HOLD_S``; sie ist halboffen, bei ``now`` gleich ihrem Ende ist sie
        vorbei. Läuft die Uhr zurück, dauert sie entsprechend länger: lieber zu streng. Ein
        weiterer Sprung ersetzt Haltezeit und Werte. Danach gilt die neue Messung als letzte,
        auch wenn sie kein Sprung war. Fehlt der SOC oder ist er ungültig, bleibt die letzte
        gültige Messung unverändert.
        """
        self._track_soc(snap, now)
        problems: list[str] = []
        missing = [name for name in SAFETY_SIGNALS if not _is_ok(snap, name)]
        if missing:
            problems.append(f"Messwert fehlt oder ist veraltet: {', '.join(missing)}.")
        problems.extend(a.message for a in plausibility if a.id.startswith(_PLAUSIBILITY_PREFIX))
        jump = self._jump
        if jump is not None and now < jump.until:
            problems.append(f"Sprung im Akku-SOC: {de(jump.before)} % → {de(jump.after)} %.")
        return InputStatus(not problems, tuple(problems))

    def _track_soc(self, snap: Snapshot, now: datetime) -> None:
        """Wertet eine neue gültige SOC-Messung aus und merkt sie sich als letzte."""
        reading = snap.get(_SOC)
        if reading is None or reading.quality is not Quality.OK:
            return
        pct = finite_number(reading.value)
        if pct is None:
            return
        measured = _Soc(pct, reading.ts)
        last = self._last_soc
        if last is not None:
            if measured.ts == last.ts:
                return  # dieselbe Messung wie im Zyklus davor
            if _is_jump(last, measured):
                until = now + timedelta(seconds=SOC_JUMP_HOLD_S)
                self._jump = _Jump(last.pct, measured.pct, until)
        self._last_soc = measured


def _is_ok(snap: Snapshot, signal: str) -> bool:
    """Das Signal ist vorhanden und hat die Qualität ``OK``."""
    reading = snap.get(signal)
    return reading is not None and reading.quality is Quality.OK


def _is_jump(before: _Soc, after: _Soc) -> bool:
    """Mehr als ``SOC_JUMP_PP`` Unterschied bei höchstens ``SOC_JUMP_WINDOW_S`` Abstand."""
    return (
        abs(after.pct - before.pct) > SOC_JUMP_PP
        and abs((after.ts - before.ts).total_seconds()) <= SOC_JUMP_WINDOW_S
    )
