"""Vorheizen und Vorkühlen im Plan (Plan 5c, Task 7, Präzisierungen 7 und 11).

Nach dem ersten Planungsschritt (Optimierer oder Ersatzplan) baut ``apply_thermal`` aus den
fertigen Slots die Eingabe der Verschiebung (``thermal_shift``), rechnet sie und schreibt das
Ergebnis in ``PlanSlot.thermal_w``, ``thermal_grid_w``, ``thermal_steps`` und ``Plan.thermal``.
``import_w``, ``export_w`` und ``cost_ct`` der Slots bleiben die des ersten Schritts; die
Ersparnis steht nur in ``Plan.thermal.saving_ct``.

Je Slot:

- ``base_w = base_power_w(Modell, Sollwert des Profils, Außentemperatur der Prognose, PV des
  Slots) · Laufanteil``; Profil-Schlüssel ``(Gruppe, lokale Viertelstunde)`` wie
  ``thermal_model.mode_profile``. Fehlt der Laufanteil, ist er 0; fehlen Sollwert oder
  Außentemperatur, sind ``base_w`` und Laufanteil 0 (der Slot wird nicht verschoben, nie ein
  Ersatzwert).
- Einspeisung, Bezug, Entladung des Akkus, Preise und Budget (``budget_kw · 1000``, ohne Budget
  ``None``) aus dem Slot des Plans.

Abweichung jetzt ``start_k`` aus Health: Ist minus Sollwert ohne Versatz, beim Kühlen mit
umgekehrtem Vorzeichen (vorgekühlt ist positiv); ohne beide Werte 0. Komfortband und Marge aus
``learning.thermal`` je Betriebsart, der Restwert des Akkus aus den Eingangsdaten.

Grund (``reason``): Betriebsart aus Health nicht ``heizen`` oder ``kuehlen`` → ``mode``; kein
taugliches Modell der Betriebsart → ``no_model``; sonst das Ergebnis der Verschiebung (``ok``,
``not_worth``, ``solver``). Das Band ist je Richtung der kleinere Wert aus Einstellung und
``max_offset_k`` der Klimaanlage (``band``). Plant sie einen Versatz außerhalb (``check_thermal``),
gilt sie nicht (``solver``). An den core geht sie nur mit Schalter an und ``ok``
(``active``). Mit Schalter aus bleibt ``ok`` als Vorschau, jeder andere Grund wird ``off``. Die
Slots tragen die Verschiebung, wann immer der Grund ``ok`` ist, auch in der Vorschau; die
Grundcodes ``KLIMA_MEHR``/``KLIMA_WENIGER`` nur, wenn sie an den core geht.

Das Modul ist rein bis auf das Protokoll eines Befunds; die Verschiebung rechnet der Solver
(blockiert: im Thread aufrufen).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol, cast, get_args

import structlog

from bindaems.app.learning.thermal_model import (
    Mode,
    ModeModel,
    ModeProfile,
    base_power_w,
    profile_key,
)
from bindaems.app.planning.explain import thermal_reason, with_thermal
from bindaems.app.planning.plausibility import check_thermal
from bindaems.app.planning.thermal_shift import (
    STEP_K,
    ShiftInput,
    ShiftResult,
    ShiftSlot,
    shift,
)
from bindaems.shared.plan import Plan, PlanSlot, PlanThermal, ThermalMode, ThermalReason
from bindaems.shared.settings import ThermalPlanSettings
from bindaems.shared.timeutil import ensure_utc

log = structlog.get_logger(__name__)

_MODES: frozenset[str] = frozenset(get_args(ThermalMode))

ShiftFn = Callable[[ShiftInput], ShiftResult]


class ThermalModels(Protocol):
    """Quelle der Wärmemodelle (``ThermalModelService``)."""

    def current(self, mode: Mode) -> tuple[ModeModel, ModeProfile, datetime] | None: ...


def health_mode(entry: Mapping[str, Any] | None) -> ThermalMode:
    """Betriebsart aus dem Eintrag der Klimaanlage in Health; ohne Eintrag oder mit einem
    unbekannten Wert ``unbekannt``."""
    mode = entry.get("mode") if entry is not None else None
    return cast(ThermalMode, mode) if isinstance(mode, str) and mode in _MODES else "unbekannt"


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def start_k(mode: Mode, entry: Mapping[str, Any] | None) -> float:
    """Abweichung jetzt in K für die Verschiebung: ``actual_c − base_setpoint_c`` beim Heizen,
    umgekehrt beim Kühlen; ohne beide Werte 0."""
    actual = _finite(entry.get("actual_c")) if entry is not None else None
    base = _finite(entry.get("base_setpoint_c")) if entry is not None else None
    if actual is None or base is None:
        return 0.0
    gap = actual - base
    return gap if mode == "heizen" else -gap


def usable(model: ModeModel) -> bool:
    """Taugt das Modell für die Verschiebung? ``usable`` und Speicher, Zeitkonstante und
    Höchstleistung endlich und positiv."""
    values = (model.c_kwh_per_k, model.tau_h, model.max_w)
    return model.usable and all(v is not None and math.isfinite(v) and v > 0 for v in values)


def shift_slots(
    plan: Plan,
    model: ModeModel,
    profile: ModeProfile,
    temperatures: Mapping[datetime, float],
) -> list[ShiftSlot]:
    """Die Slots der Verschiebung aus den Slots des Plans (siehe Moduldokumentation)."""
    result: list[ShiftSlot] = []
    for slot in plan.slots:
        key = profile_key(slot.start)
        setpoint = profile.setpoint_c.get(key)
        outdoor = temperatures.get(ensure_utc(slot.start))
        on_share = profile.on_share.get(key, 0.0)
        if setpoint is None or outdoor is None:
            base_w, on_share = 0.0, 0.0
        else:
            base_w = base_power_w(model, setpoint, outdoor, slot.pv_w) * on_share
        result.append(
            ShiftSlot(
                start=slot.start,
                base_w=base_w,
                on_share=on_share,
                export_w=slot.export_w,
                import_w=slot.import_w,
                discharge_w=slot.discharge_w,
                import_ct=slot.import_ct,
                feed_in_ct=slot.feed_in_ct,
                budget_w=slot.budget_kw * 1000 if slot.budget_kw is not None else None,
            )
        )
    return result


def band(mode: Mode, settings: ThermalPlanSettings, *, max_offset_k: float) -> tuple[float, float]:
    """Komfortband ``(up_k, down_k)`` der Betriebsart: je Richtung der kleinere Wert aus der
    Einstellung und ``max_offset_k`` der Klimaanlage, auf ein Vielfaches von 0,5 K abgerundet."""
    if mode == "heizen":
        up_k, down_k = settings.heat_up_k, settings.heat_down_k
    else:
        up_k, down_k = settings.cool_up_k, settings.cool_down_k

    def capped(value: float) -> float:
        return math.floor(min(value, max_offset_k) / STEP_K + 1e-9) * STEP_K

    return capped(up_k), capped(down_k)


def _shifted_slots(plan: Plan, mode: Mode, result: ShiftResult, *, explain: bool) -> list[PlanSlot]:
    by_start = {item.start: item for item in result.slots}
    slots: list[PlanSlot] = []
    for slot in plan.slots:
        item = by_start.get(slot.start)
        if item is None:
            slots.append(slot)
            continue
        update: dict[str, Any] = {
            "thermal_w": item.thermal_w,
            "thermal_grid_w": max(0.0, item.grid_w),
            "thermal_steps": item.steps,
        }
        if explain:
            update["reasons"] = with_thermal(slot.reasons, thermal_reason(mode, item.steps))
        slots.append(slot.model_copy(update=update))
    return slots


def apply_thermal(
    plan: Plan,
    *,
    device: str,
    max_offset_k: float,
    entry: Mapping[str, Any] | None,
    models: ThermalModels,
    temperatures: Mapping[datetime, float],
    settings: ThermalPlanSettings,
    end_value_ct: float,
    shift_fn: ShiftFn = shift,
) -> Plan:
    """``plan`` mit der Verschiebung der Klimaanlage ``device`` (siehe Moduldokumentation).

    ``max_offset_k`` ist die harte Grenze des Versatzes der Klimaanlage (``CoeOffsetConfig``),
    ``entry`` ihr Eintrag in Health (``None`` ohne Health), ``temperatures`` die Außentemperatur
    je Slotbeginn (UTC) aus der Prognose."""
    mode = health_mode(entry)
    current: Mode | None = mode if mode == "heizen" or mode == "kuehlen" else None
    computed_at: datetime | None = None
    result: ShiftResult | None = None
    reason: ThermalReason
    saving_ct = 0.0
    if current is None:
        reason = "mode"
    else:
        state = models.current(current)
        if state is not None:
            computed_at = state[2]
        if state is None or not usable(state[0]):
            reason = "no_model"
        else:
            model, profile, _ = state
            up_k, down_k = band(current, settings, max_offset_k=max_offset_k)
            result = shift_fn(
                ShiftInput(
                    mode=current,
                    c_kwh_per_k=float(model.c_kwh_per_k or 0.0),
                    tau_h=float(model.tau_h or 0.0),
                    max_w=float(model.max_w or 0.0),
                    up_k=up_k,
                    down_k=down_k,
                    margin_ct=settings.margin_ct,
                    end_value_ct=end_value_ct,
                    start_k=start_k(current, entry),
                    slots=shift_slots(plan, model, profile, temperatures),
                )
            )
            reason, saving_ct = result.reason, max(0.0, result.saving_ct)
            if reason == "ok":
                shifted = _shifted_slots(plan, current, result, explain=False)
                findings = check_thermal(shifted, up_k=up_k, down_k=down_k)
                if findings:
                    log.warning(
                        "Verschiebung der Klimaanlage verworfen, weil sie unplausibel ist",
                        finding=findings[0],
                    )
                    reason, saving_ct = "solver", 0.0
    active = settings.enabled and reason == "ok"
    shown: ThermalReason = reason if settings.enabled or reason == "ok" else "off"
    slots = plan.slots
    if reason == "ok" and result is not None and current is not None:
        slots = _shifted_slots(plan, current, result, explain=active)
    thermal = PlanThermal(
        device=device,
        mode=mode,
        active=active,
        saving_ct=saving_ct,
        model_computed_at=computed_at,
        reason=shown,
    )
    return plan.model_copy(update={"thermal": thermal, "slots": slots})


def failed(plan: Plan, *, device: str, entry: Mapping[str, Any] | None, enabled: bool) -> Plan:
    """``plan`` ohne Verschiebung nach einem unerwarteten Fehler: Grund ``solver`` (mit Schalter
    aus ``off``), die Slots unverändert."""
    thermal = PlanThermal(
        device=device,
        mode=health_mode(entry),
        active=False,
        saving_ct=0.0,
        model_computed_at=None,
        reason="solver" if enabled else "off",
    )
    return plan.model_copy(update={"thermal": thermal})
