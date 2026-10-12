"""Wirksame Betriebsart je Aktor (Spec 8.7, Regel 8): Dry-Run oder live.

Die Bedienung fordert eine Betriebsart an (``Intent``). Live wird sie nur, wenn die Konfiguration
den Aktor dafür freigibt (``live_allowed``), die Absicht den Regelbetrieb verlangt und, bei Victron
und EVCS, der Watchdog am Cerbo scharf ist. Sonst läuft der Aktor im Dry-Run, und die Antwort nennt
den Grund.

Live gewünscht ist ein Aktor nur im Regelbetrieb (Plan 2b, Präzisierung 2). Hält ein Aktor noch
Werte des EMS und ist nicht mehr live gewünscht, wird er übergeben (Präzisierung 3): Er bekommt
seine Sicherwerte und ist dafür wirksam live, sofern er darf, auch ohne Regelbetrieb. Welche
Aktoren übergeben werden, kommt als ``handing_back`` von außen.

Die Schicht ist rein: Zustand und Zeit kommen als Argumente.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Final

from bindaems.core.safety.limits import ActuatorSpec, HardLimits
from bindaems.shared.control import ActuatorMode, Intent

REASON_NOT_ALLOWED: Final = "In config.yaml nicht für den Live-Betrieb freigegeben (live_allowed)."
REASON_CONTROL_OFF_MODE: Final = "Regelbetrieb ist aus."
REASON_WATCHDOG_MODE: Final = "Watchdog am Cerbo ist nicht scharf."
REASON_HANDBACK_MODE: Final = "Übergabe: Sicherwerte werden gesetzt."


@dataclass(frozen=True)
class ModeInfo:
    """Angeforderte und wirksame Betriebsart eines Aktors.

    ``reasons`` nennt, warum die wirksame Betriebsart nicht einfach die angeforderte ist:

    - ein angeforderter Live-Betrieb, der nicht gilt: der erste zutreffende Grund
    - eine Übergabe: ``REASON_HANDBACK_MODE``, solange der Aktor dafür live ist, sonst der Grund,
      aus dem er es nicht ist

    Sonst ist ``reasons`` leer.
    """

    requested: ActuatorMode
    effective: ActuatorMode
    reasons: tuple[str, ...]


def effective_modes(
    intent: Intent | None,
    limits: HardLimits,
    watchdog_armed: bool,
    *,
    handing_back: AbstractSet[str] = frozenset(),
) -> dict[str, ModeInfo]:
    """Angeforderte und wirksame Betriebsart je Aktor, in der Reihenfolge von ``limits.actuators``.

    Angefordert ist die Betriebsart aus der Absicht, ohne Absicht oder ohne Eintrag für den Aktor
    Dry-Run. Wirksam ist ``LIVE`` nur bei Anforderung ``LIVE``, mit ``live_allowed``, im
    Regelbetrieb und, wenn der Aktor den Watchdog braucht, mit scharfem Watchdog. Sonst gilt
    ``DRY_RUN``, bei angefordertem Live-Betrieb mit dem ersten zutreffenden Grund
    (``REASON_NOT_ALLOWED``, dann ``REASON_CONTROL_OFF_MODE``, dann ``REASON_WATCHDOG_MODE``).

    Ein Aktor in ``handing_back`` wird übergeben. Er behält die angeforderte Betriebsart und ist
    wirksam ``LIVE`` mit ``REASON_HANDBACK_MODE``, wenn Freigabe und Watchdog nichts einwenden,
    auch ohne Regelbetrieb; sonst ``DRY_RUN`` mit dem Einwand. Was nicht im Katalog steht, zählt
    nicht.
    """
    control = intent is not None and intent.control
    modes: dict[str, ModeInfo] = {}
    for actuator, spec in limits.actuators.items():
        requested = _requested(intent, actuator)
        if actuator in handing_back:
            modes[actuator] = _handback_mode(requested, spec, watchdog_armed)
            continue
        if requested != ActuatorMode.LIVE:
            modes[actuator] = ModeInfo(requested, ActuatorMode.DRY_RUN, ())
            continue
        denial = _live_denial(spec, watchdog_armed, control=control)
        if denial is None:
            modes[actuator] = ModeInfo(requested, ActuatorMode.LIVE, ())
        else:
            modes[actuator] = ModeInfo(requested, ActuatorMode.DRY_RUN, (denial,))
    return modes


def live_wanted(intent: Intent | None, limits: HardLimits) -> frozenset[str]:
    """Die Aktoren, die live gewünscht sind: Regelbetrieb an, live angefordert, ``live_allowed``.

    Ob der Watchdog scharf ist, spielt keine Rolle; das entscheidet ``effective_modes``.
    """
    if intent is None or not intent.control:
        return frozenset()
    return initially_owned(intent, limits)


def initially_owned(intent: Intent | None, limits: HardLimits) -> frozenset[str]:
    """Die Aktoren, die beim Start als owned gelten (Plan 2b, Präzisierung 3).

    Das sind alle, die die gespeicherte Absicht live anfordert und ``config.yaml`` freigibt, ohne
    Rücksicht auf den Regelbetrieb: Ihr Gerät kann noch Werte vom letzten Lauf halten.
    """
    return frozenset(
        actuator
        for actuator, spec in limits.actuators.items()
        if spec.live_allowed and _requested(intent, actuator) == ActuatorMode.LIVE
    )


def watchdog_required(
    intent: Intent | None, limits: HardLimits, *, handing_back: AbstractSet[str] = frozenset()
) -> bool:
    """Braucht der Betrieb den Watchdog? (Plan 2b, Präzisierung 2)

    Ja, wenn ein Aktor, der ihn braucht (Victron, EVCS), freigegeben ist (``live_allowed``) und
    live gewünscht ist (``live_wanted``, also nur im Regelbetrieb) oder gerade übergeben wird
    (``handing_back``). Ob der Watchdog scharf ist, spielt dabei keine Rolle.
    """
    wanted = live_wanted(intent, limits)
    return any(
        spec.needs_watchdog
        and spec.live_allowed
        and (actuator in wanted or actuator in handing_back)
        for actuator, spec in limits.actuators.items()
    )


def _requested(intent: Intent | None, actuator: str) -> ActuatorMode:
    """Die angeforderte Betriebsart; ohne Absicht oder ohne Eintrag für den Aktor ``DRY_RUN``."""
    if intent is None:
        return ActuatorMode.DRY_RUN
    return intent.actuators.get(actuator, ActuatorMode.DRY_RUN)


def _handback_mode(requested: ActuatorMode, spec: ActuatorSpec, watchdog_armed: bool) -> ModeInfo:
    """Betriebsart eines Aktors, der übergeben wird: live für seine Sicherwerte, wenn er darf."""
    denial = _live_denial(spec, watchdog_armed)
    if denial is None:
        return ModeInfo(requested, ActuatorMode.LIVE, (REASON_HANDBACK_MODE,))
    return ModeInfo(requested, ActuatorMode.DRY_RUN, (denial,))


def _live_denial(spec: ActuatorSpec, watchdog_armed: bool, *, control: bool = True) -> str | None:
    """Der erste Grund, aus dem der Aktor nicht live gehen darf; ``None``, wenn er darf.

    ``control`` sagt, ob die Absicht den Regelbetrieb verlangt; die Übergabe fragt nicht danach.
    """
    if not spec.live_allowed:
        return REASON_NOT_ALLOWED
    if not control:
        return REASON_CONTROL_OFF_MODE
    if spec.needs_watchdog and not watchdog_armed:
        return REASON_WATCHDOG_MODE
    return None
