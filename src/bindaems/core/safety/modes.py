"""Wirksame Betriebsart je Aktor (Spec 8.7, Regel 8): Dry-Run oder live.

Die Bedienung fordert eine Betriebsart an (``Intent``). Live wird sie nur, wenn die Konfiguration
den Aktor dafür freigibt (``live_allowed``) und, bei Victron und EVCS, der Watchdog am Cerbo scharf
ist. Sonst läuft der Aktor im Dry-Run, und die Antwort nennt den Grund.

Die Schicht ist rein: Zustand und Zeit kommen als Argumente.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from bindaems.core.safety.limits import ActuatorSpec, HardLimits
from bindaems.shared.control import ActuatorMode, Intent

REASON_NOT_ALLOWED: Final = "In config.yaml nicht für den Live-Betrieb freigegeben (live_allowed)."
REASON_WATCHDOG_MODE: Final = "Watchdog am Cerbo ist nicht scharf."


@dataclass(frozen=True)
class ModeInfo:
    """Angeforderte und wirksame Betriebsart eines Aktors.

    ``reasons`` ist leer, wenn die wirksame der angeforderten Betriebsart entspricht. Sonst nennt
    es, warum ein angeforderter Live-Betrieb nicht gilt: den ersten zutreffenden Grund.
    """

    requested: ActuatorMode
    effective: ActuatorMode
    reasons: tuple[str, ...]


def effective_modes(
    intent: Intent | None, limits: HardLimits, watchdog_armed: bool
) -> dict[str, ModeInfo]:
    """Angeforderte und wirksame Betriebsart je Aktor, in der Reihenfolge von ``limits.actuators``.

    Angefordert ist die Betriebsart aus der Absicht, ohne Absicht oder ohne Eintrag für den Aktor
    Dry-Run. Wirksam ist ``LIVE`` nur bei Anforderung ``LIVE``, mit ``live_allowed`` und, wenn der
    Aktor den Watchdog braucht, mit scharfem Watchdog. Sonst gilt ``DRY_RUN``, bei angefordertem
    Live-Betrieb mit dem ersten zutreffenden Grund (``REASON_NOT_ALLOWED`` vor
    ``REASON_WATCHDOG_MODE``).
    """
    modes: dict[str, ModeInfo] = {}
    for actuator, spec in limits.actuators.items():
        requested = _requested(intent, actuator)
        if requested != ActuatorMode.LIVE:
            modes[actuator] = ModeInfo(requested, ActuatorMode.DRY_RUN, ())
            continue
        denial = _live_denial(spec, watchdog_armed)
        if denial is None:
            modes[actuator] = ModeInfo(requested, ActuatorMode.LIVE, ())
        else:
            modes[actuator] = ModeInfo(requested, ActuatorMode.DRY_RUN, (denial,))
    return modes


def watchdog_required(intent: Intent | None, limits: HardLimits) -> bool:
    """Braucht der Betrieb den Watchdog?

    Ja, wenn ein Aktor, der ihn braucht (Victron, EVCS), freigegeben (``live_allowed``) und live
    angefordert ist. Ob der Watchdog scharf ist, spielt dabei keine Rolle.
    """
    return any(
        spec.needs_watchdog
        and spec.live_allowed
        and _requested(intent, actuator) == ActuatorMode.LIVE
        for actuator, spec in limits.actuators.items()
    )


def _requested(intent: Intent | None, actuator: str) -> ActuatorMode:
    """Die angeforderte Betriebsart; ohne Absicht oder ohne Eintrag für den Aktor ``DRY_RUN``."""
    if intent is None:
        return ActuatorMode.DRY_RUN
    return intent.actuators.get(actuator, ActuatorMode.DRY_RUN)


def _live_denial(spec: ActuatorSpec, watchdog_armed: bool) -> str | None:
    """Der erste Grund, aus dem der Aktor nicht live gehen darf; ``None``, wenn er darf."""
    if not spec.live_allowed:
        return REASON_NOT_ALLOWED
    if spec.needs_watchdog and not watchdog_armed:
        return REASON_WATCHDOG_MODE
    return None
