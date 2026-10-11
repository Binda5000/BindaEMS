"""Betriebszustände des core (Spec 7.2): START, OBSERVE, CONTROL, DEGRADED und SAFE.

Der Zustandsautomat entscheidet in jedem Zyklus aus den Eingaben (``OperatingInputs``), in welchem
Zustand der core steht und warum. Von OBSERVE oder SAFE nach CONTROL geht es nur durch das Gate:
Es ist offen, wenn keine ``safe_reasons`` vorliegen, die kritischen Messwerte seit 30 s frisch
sind, die 3 Phasen bestätigt sind und der Watchdog scharf ist, sofern der Betrieb ihn braucht.

Die Schicht ist rein: Die Zeit kommt als Argument, sie loggt und sendet nichts. Wechselt der
Zustand, liefert ``step`` eine ``Transition``; Stream-Meldung und Log-Zeile sind Sache der Laufzeit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from bindaems.core.safety.modes import REASON_WATCHDOG_MODE
from bindaems.shared.control import OperatingState

REASON_CONTROL_OFF: Final = "Regelbetrieb ist aus."
REASON_NOT_FRESH: Final = "Kritische Messwerte noch nicht 30 s aktuell."
REASON_PHASES: Final = "3 Phasen mit je einem Gerät nicht bestätigt."
REASON_WATCHDOG: Final = REASON_WATCHDOG_MODE  # gleicher Text, eine Quelle

_RUNNING: Final = frozenset({OperatingState.CONTROL, OperatingState.DEGRADED})

_Outcome = tuple[OperatingState, tuple[str, ...]]


@dataclass(frozen=True)
class OperatingInputs:
    """Was die Laufzeit dem Zustandsautomaten in jedem Zyklus meldet.

    - ``control_requested``: Die gültige Absicht verlangt den Regelbetrieb.
    - ``data_fresh``: Alle kritischen Messwerte sind seit mindestens 30 s frisch.
    - ``phases_ok``: Die 3 Phasen mit je einem Gerät sind bestätigt.
    - ``watchdog_required``: Der Betrieb braucht den Watchdog am Cerbo.
    - ``watchdog_armed``: Der Watchdog ist scharf.
    - ``safe_reasons``: Gründe für den sicheren Zustand, etwa veraltete Cerbo-Daten.
    - ``degraded_reasons``: Gründe für den eingeschränkten Betrieb, etwa gesperrte Teilsysteme.
    """

    control_requested: bool
    data_fresh: bool
    phases_ok: bool
    watchdog_required: bool
    watchdog_armed: bool
    safe_reasons: tuple[str, ...] = ()
    degraded_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Transition:
    """Ein Zustandswechsel zum Zeitpunkt ``ts``; ``reasons`` sind die Gründe des neuen Zustands."""

    previous: OperatingState
    state: OperatingState
    reasons: tuple[str, ...]
    ts: datetime


class OperatingStateMachine:
    """Hält den Betriebszustand; die Laufzeit ruft ``step`` einmal je Zyklus auf.

    Der Automat beginnt in START, und der erste Schritt führt immer nach OBSERVE. Die Regeln
    gelten der Reihe nach, die erste zutreffende entscheidet. Die Gate-Gründe sind die
    ``safe_reasons``, dann ``REASON_NOT_FRESH`` (Messwerte nicht frisch), ``REASON_PHASES``
    (Phasen nicht bestätigt) und ``REASON_WATCHDOG`` (Watchdog benötigt und nicht scharf); ohne
    einen von ihnen ist das Gate offen.

    START, OBSERVE und SAFE:

    1. Regelbetrieb aus: OBSERVE mit ``REASON_CONTROL_OFF``.
    2. Gate zu: bleibt in SAFE, sonst OBSERVE; die Gründe sind die Gate-Gründe.
    3. Aus START: OBSERVE ohne Gründe, denn der erste Schritt schaltet nie weiter.
    4. Gate offen: DEGRADED mit den ``degraded_reasons``, wenn es welche gibt, sonst CONTROL.

    CONTROL und DEGRADED:

    1. Regelbetrieb aus: OBSERVE mit ``REASON_CONTROL_OFF``.
    2. ``safe_reasons`` oder Phasen nicht bestätigt: SAFE mit den ``safe_reasons`` und, falls die
       Phasen nicht stimmen, ``REASON_PHASES``.
    3. Sonst DEGRADED mit den ``degraded_reasons`` und, falls der Watchdog benötigt wird und nicht
       scharf ist, ``REASON_WATCHDOG``; gibt es keinen Grund, CONTROL.

    ``data_fresh`` wirkt nur am Gate. Veralten die Cerbo-Daten im laufenden Betrieb, melden die
    Eingaben das als ``safe_reasons``.
    """

    def __init__(self) -> None:
        self._state = OperatingState.START
        self._reasons: tuple[str, ...] = ()
        self._since: datetime | None = None

    @property
    def state(self) -> OperatingState:
        """Der Betriebszustand; vor dem ersten Schritt START."""
        return self._state

    @property
    def reasons(self) -> tuple[str, ...]:
        """Die Gründe des Zustands aus dem letzten Schritt; vor dem ersten Schritt leer."""
        return self._reasons

    @property
    def since(self) -> datetime | None:
        """Der Zeitpunkt des letzten Zustandswechsels; vor dem ersten Schritt ``None``."""
        return self._since

    def step(self, now: datetime, inputs: OperatingInputs) -> Transition | None:
        """Wertet einen Zyklus aus und aktualisiert ``reasons`` in jedem Fall.

        Nur bei einem Zustandswechsel liefert es eine ``Transition`` mit den neuen Gründen und
        setzt ``since = now``; bleibt der Zustand, ist das Ergebnis ``None``.
        """
        if self._state in _RUNNING:
            state, reasons = _decide_running(inputs)
        else:
            state, reasons = _decide_gated(self._state, inputs)
        self._reasons = reasons
        if state == self._state:
            return None
        transition = Transition(self._state, state, reasons, now)
        self._state = state
        self._since = now
        return transition


def _decide_gated(state: OperatingState, inputs: OperatingInputs) -> _Outcome:
    """START, OBSERVE und SAFE: Regelbetrieb und Gate entscheiden, ob es weitergeht."""
    if not inputs.control_requested:
        return OperatingState.OBSERVE, (REASON_CONTROL_OFF,)
    gate = _gate_reasons(inputs)
    if gate:
        held = OperatingState.SAFE if state == OperatingState.SAFE else OperatingState.OBSERVE
        return held, gate
    if state == OperatingState.START:
        return OperatingState.OBSERVE, ()
    return _running_state(tuple(inputs.degraded_reasons))


def _decide_running(inputs: OperatingInputs) -> _Outcome:
    """CONTROL und DEGRADED: Regelbetrieb, Gründe für SAFE, sonst die für DEGRADED."""
    if not inputs.control_requested:
        return OperatingState.OBSERVE, (REASON_CONTROL_OFF,)
    safe = list(inputs.safe_reasons)
    if not inputs.phases_ok:
        safe.append(REASON_PHASES)
    if safe:
        return OperatingState.SAFE, tuple(safe)
    degraded = list(inputs.degraded_reasons)
    if _watchdog_missing(inputs):
        degraded.append(REASON_WATCHDOG)
    return _running_state(tuple(degraded))


def _running_state(degraded_reasons: tuple[str, ...]) -> _Outcome:
    """DEGRADED, wenn es Gründe gibt, sonst CONTROL; die Gründe bleiben."""
    state = OperatingState.DEGRADED if degraded_reasons else OperatingState.CONTROL
    return state, degraded_reasons


def _gate_reasons(inputs: OperatingInputs) -> tuple[str, ...]:
    """Die Gründe, aus denen das Gate zu ist, in fester Reihenfolge; leer, wenn es offen ist."""
    reasons = list(inputs.safe_reasons)
    if not inputs.data_fresh:
        reasons.append(REASON_NOT_FRESH)
    if not inputs.phases_ok:
        reasons.append(REASON_PHASES)
    if _watchdog_missing(inputs):
        reasons.append(REASON_WATCHDOG)
    return tuple(reasons)


def _watchdog_missing(inputs: OperatingInputs) -> bool:
    """Der Betrieb braucht den Watchdog, und er ist nicht scharf."""
    return inputs.watchdog_required and not inputs.watchdog_armed
