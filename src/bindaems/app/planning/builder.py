"""Plan aus Absichten (Plan 3b, Task 7).

Rechnet die Absichten mit dem Akkumodell nach und füllt daraus Slots, Budget (Präzisierungen 10
und 11), Kosten, Vergleichskosten „Victron allein“ und die Gründe der Erklärung. Reine Rechnung.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from math import ceil

from bindaems.app.planning.battery_model import SimStep, params_of, sim_slots, simulate
from bindaems.app.planning.explain import reasons_for
from bindaems.app.planning.inputs import PlanInputs
from bindaems.shared.plan import (
    PLAN_VALID,
    BatteryIntent,
    Plan,
    PlanSlot,
    PlanSource,
    SolverStatus,
)
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, ensure_utc

_TENTH_EPS = 1e-9  # Gleitkommafehler vor dem Aufrunden auf 0,1 kW
_SOC_EPS = 1e-9  # Gleitkommafehler vor dem Aufrunden auf ganze Prozent und beim Bedarf null


def _resimulate(inputs: PlanInputs, intents: Sequence[BatteryIntent]) -> list[SimStep]:
    params = params_of(inputs)
    soc0_kwh = inputs.battery.soc_pct / 100 * params.capacity_kwh
    return simulate(soc0_kwh, sim_slots(inputs), intents, params)


def baseline_cost_ct(inputs: PlanInputs) -> float:
    """Energiekosten in ct, wenn der Akku in allen Slots ``EIGENVERBRAUCH`` fährt („Victron
    allein“), ab demselben SOC."""
    steps = _resimulate(inputs, [BatteryIntent() for _ in inputs.slots])
    return sum(item.cost_ct for item in steps)


def _month_of(inputs: PlanInputs, index: int) -> str:
    return ensure_utc(inputs.slots[index].start).astimezone(LOCAL_TZ).strftime("%Y-%m")


def _budgets(
    inputs: PlanInputs,
    steps: Sequence[SimStep],
    *,
    source: PlanSource,
    optimizer_peaks_kw: Mapping[str, float] | None,
) -> list[float | None]:
    """Budget je Slot (akzeptierte Monatsspitze in kW) für Monate mit Leistungspreis.

    Ersatzplan (Präzisierung 10): ``max(bisherige Monatsspitze, minimum_kw)``, das ist
    ``peak0_kw``. Optimierer (Präzisierung 11): ``ceil(max(Spitze des Optimierers, höchster
    Bezug der Nachrechnung im Monat) · 10) / 10``; fehlt die Spitze des Optimierers für einen
    Monat, gilt ``peak0_kw``.
    """
    by_month = {entry.month: entry for entry in inputs.peak_months}
    budget_of: dict[str, float] = {}
    for month, entry in by_month.items():
        if source == "fallback":
            budget_of[month] = entry.peak0_kw
            continue
        peak_kw = (optimizer_peaks_kw or {}).get(month, entry.peak0_kw)
        import_kw = max(
            (
                item.import_w / 1000
                for index, item in enumerate(steps)
                if _month_of(inputs, index) == month
            ),
            default=0.0,
        )
        budget_of[month] = ceil(max(peak_kw, import_kw) * 10 - _TENTH_EPS) / 10
    return [budget_of.get(_month_of(inputs, index)) for index in range(len(inputs.slots))]


def support_thresholds(
    slots: Sequence[PlanSlot], reserve_pct: float
) -> list[tuple[float, datetime]]:
    """Plan-Schwelle der Akku-Unterstützung je Slot und der Zeitpunkt, bis zu dem der Plan den Akku
    braucht (Präzisierung 6).

    Mit der Entladung ``d_t`` eines Slots in Prozentpunkten (``soc_start_pct - soc_end_pct``) gilt
    rückwärts ``R_N = 0`` und ``R_t = max(0, d_t + R_{t+1})``; die Schwelle ist
    ``min(100, ceil(reserve_pct + R_t))``. „Bis“ ist der Beginn des ersten späteren Slots mit
    ``R = 0``, sonst das Horizontende.
    """
    count = len(slots)
    if count == 0:
        return []
    starts = [slot.start for slot in slots] + [slots[-1].start + SLOT]
    need = [0.0] * (count + 1)
    until = [starts[count]] * (count + 1)
    for index in range(count - 1, -1, -1):
        drop = slots[index].soc_start_pct - slots[index].soc_end_pct
        need[index] = max(0.0, drop + need[index + 1])
        until[index] = starts[index + 1] if need[index + 1] <= _SOC_EPS else until[index + 1]
    return [
        (min(100.0, float(ceil(reserve_pct + need[index] - _SOC_EPS))), until[index])
        for index in range(count)
    ]


def build_plan(
    inputs: PlanInputs,
    intents: Sequence[BatteryIntent],
    *,
    source: PlanSource,
    solver_status: SolverStatus | None,
    fallback_reason: str | None = None,
    issues: Sequence[str] = (),
    optimizer_peaks_kw: Mapping[str, float] | None = None,
) -> Plan:
    """Plan aus den Absichten: Nachrechnung, Budget, Gründe, Kosten und Vergleichskosten."""
    if len(intents) != len(inputs.slots):
        raise ValueError("Absichten und Slots müssen gleich viele sein")
    params = params_of(inputs)
    steps = _resimulate(inputs, intents)
    budgets = _budgets(inputs, steps, source=source, optimizer_peaks_kw=optimizer_peaks_kw)

    drafts: list[PlanSlot] = []
    for slot, intent, item, budget in zip(inputs.slots, intents, steps, budgets, strict=True):
        drafts.append(
            PlanSlot(
                start=slot.start,
                intent=intent,
                soc_start_pct=item.soc_start_kwh / params.capacity_kwh * 100,
                soc_end_pct=item.soc_end_kwh / params.capacity_kwh * 100,
                pv_w=slot.pv_w,
                load_w=slot.load_w,
                import_w=item.import_w,
                export_w=item.export_w,
                charge_pv_w=item.charge_pv_w,
                charge_grid_w=item.charge_grid_w,
                discharge_w=item.discharge_w,
                import_ct=slot.import_ct,
                feed_in_ct=slot.feed_in_ct,
                estimated=slot.origin == "estimated",
                cost_ct=item.cost_ct,
                budget_kw=budget,
                reasons=[],
            )
        )
    thresholds = support_thresholds(drafts, inputs.battery.reserve_pct)
    reasons = reasons_for(
        inputs,
        intents,
        steps,
        budgets,
        fallback=source == "fallback",
        thresholds=thresholds,
    )
    slots = [
        draft.model_copy(update={"support_soc_pct": threshold, "reasons": slot_reasons})
        for draft, (threshold, _), slot_reasons in zip(drafts, thresholds, reasons, strict=True)
    ]
    return Plan(
        created_at=inputs.created_at,
        trigger=inputs.trigger,
        source=source,
        solver_status=solver_status,
        fallback_reason=fallback_reason,
        valid_until=inputs.created_at + PLAN_VALID,
        settings_version=inputs.settings_version,
        grid_charge_allowed=inputs.planning.grid_charge_allowed,
        reserve_pct=inputs.battery.reserve_pct,
        soc_max_pct=inputs.battery.soc_max_pct,
        month_peak_kw=inputs.month_peak_kw,
        power_price=bool(inputs.peak_months),
        expected_cost_ct=sum(item.cost_ct for item in steps),
        baseline_cost_ct=baseline_cost_ct(inputs),
        issues=list(issues),
        slots=slots,
    )
