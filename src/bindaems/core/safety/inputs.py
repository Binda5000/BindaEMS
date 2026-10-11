"""Regel 7 der Safety-Schicht: Eingangsdaten auf Frische, Plausibilität und SOC-Sprünge prüfen.

``InputCheck`` entscheidet je Zyklus, ob die Messwerte brauchbar sind (Spec 8.3). Ein Problem ist:

1. Ein Sicherheitssignal (``SAFETY_SIGNALS``) fehlt oder hat nicht die Qualität ``OK``.
2. Die Plausibilitätsprüfung aus Phase 1 meldet einen Alarm (ID ``plaus.…``).
3. Der Akku-SOC ist gesprungen: Weicht er um mehr als 5 Prozentpunkte von einem Wert ab, den die
   Prüfung höchstens 60 s zuvor zuletzt gesehen hat, gilt er 60 s lang als unplausibel.

Die Sprungerkennung gilt nur für den SOC. Netzleistungen springen legitim, etwa beim Start einer
Wallbox; für sie bleiben die Wertebereiche und die Energiebilanz der Plausibilitätsprüfung.

Die Schicht ist rein: Die Zeit kommt als Argument. Abstand und Haltezeit zählen nach dem ``now``
der Auswertungen, nicht nach ``Reading.ts``: Bei Victron-MQTT mit ``suppress-republish`` bleibt
der Zeitstempel beim letzten Wertwechsel stehen, obwohl der Wert seither in jedem Zyklus gültig war.
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

# Sprung des SOC: Weicht ein gültiger Wert um mehr als SOC_JUMP_PP Prozentpunkte von einem Wert ab,
# den die Prüfung höchstens SOC_JUMP_WINDOW_S zuvor zuletzt gesehen hat, gilt der SOC danach
# SOC_JUMP_HOLD_S lang als unplausibel.
SOC_JUMP_PP: Final = 5.0
SOC_JUMP_WINDOW_S: Final = 60.0
SOC_JUMP_HOLD_S: Final = 60.0

_PLAUSIBILITY_PREFIX: Final = "plaus."  # Alarm-IDs der Plausibilitätsprüfung aus Phase 1


class _Soc(NamedTuple):
    """Ein gültiger SOC-Wert in % und das ``now`` der Auswertung, die ihn zuletzt gesehen hat."""

    pct: float
    seen_at: datetime


class _Jump(NamedTuple):
    """Ein erkannter Sprung von ``before`` auf ``after`` (in %), gehalten bis ``until``."""

    before: float
    after: float
    until: datetime


class InputCheck:
    """Regel 7: Sind die Eingangsdaten dieses Zyklus brauchbar?

    Der Zustand ist der letzte gültige SOC-Wert mit dem Zeitpunkt, zu dem eine Auswertung ihn
    zuletzt gesehen hat, und der letzte erkannte Sprung. Wertet ein weiterer Zyklus denselben Wert
    noch einmal aus, ist das kein Sprung und verlängert die Haltezeit nicht.
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

        Ein Sprung ist ein gültiger SOC-Wert ``b`` (Qualität ``OK``, endliche Zahl), der um mehr
        als ``SOC_JUMP_PP`` vom letzten gültigen Wert ``a`` abweicht, wenn eine frühere Auswertung
        ``a`` zuletzt höchstens ``SOC_JUMP_WINDOW_S`` vor diesem ``now`` gesehen hat. Jede
        Auswertung mit gültigem SOC merkt sich den Wert und ihr ``now`` als „zuletzt gesehen“, auch
        bei unverändertem Wert; ``Reading.ts`` zählt nicht. Ein Sprung setzt die Haltezeit auf
        ``now + SOC_JUMP_HOLD_S``; sie ist halboffen, bei ``now`` gleich ihrem Ende ist sie
        vorbei. Ein weiterer Sprung ersetzt Haltezeit und Werte.

        Fehlt der SOC, hat er nicht die Qualität ``OK`` oder ist er keine endliche Zahl, bleibt der
        Speicher unverändert: Nach einer Lücke von mehr als ``SOC_JUMP_WINDOW_S`` ist ein
        geänderter Wert kein Sprung. Läuft die Uhr zurück, gilt der Abstand als klein und die
        Haltezeit dauert entsprechend länger: lieber zu streng.
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
        """Prüft einen gültigen SOC auf einen Sprung und merkt ihn als zuletzt gesehen."""
        reading = snap.get(_SOC)
        if reading is None or reading.quality is not Quality.OK:
            return
        pct = finite_number(reading.value)
        if pct is None:
            return
        last = self._last_soc
        if last is not None and _is_jump(last, pct, now):
            self._jump = _Jump(last.pct, pct, now + timedelta(seconds=SOC_JUMP_HOLD_S))
        self._last_soc = _Soc(pct, now)


def _is_ok(snap: Snapshot, signal: str) -> bool:
    """Das Signal ist vorhanden und hat die Qualität ``OK``."""
    reading = snap.get(signal)
    return reading is not None and reading.quality is Quality.OK


def _is_jump(last: _Soc, pct: float, now: datetime) -> bool:
    """``pct`` weicht von ``last`` um mehr als ``SOC_JUMP_PP`` ab, und ``last`` ist nicht zu alt.

    Nicht zu alt heißt: ``now − last.seen_at`` ist höchstens ``SOC_JUMP_WINDOW_S``. Ein negativer
    Abstand (die Uhr lief zurück) zählt als klein.
    """
    return (
        abs(pct - last.pct) > SOC_JUMP_PP
        and (now - last.seen_at).total_seconds() <= SOC_JUMP_WINDOW_S
    )
