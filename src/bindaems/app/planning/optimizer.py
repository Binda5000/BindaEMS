"""Optimierer für den Akku (Plan 3b, Präzisierungen 5 und 6): ein MILP mit HiGHS über PuLP.

Minimiert über den Horizont Bezug minus Einspeisung, Verschleiß beim Entladen, die Marge beim
Netzladen, den Leistungspreis je Monat und die Abweichung vom vorigen Plan in den ersten Slots,
abzüglich des Restwerts der Energie im Akku am Horizontende. Regel R1 ist hart: Netzladen nur,
wenn ab diesem Slot laut Prognose nichts mehr eingespeist wird.

Leistungen in W, Energien in kWh, Preise in ct/kWh. Reine Rechnung im selben Prozess; den
Kindprozess und das harte Zeitlimit stellt der Planungsdienst.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import highspy
import pulp

from bindaems.app.planning.battery_model import SLOT_H, SimSlot, params_of, sim_slots
from bindaems.app.planning.inputs import INERTIA_SLOTS, PlanInputs
from bindaems.shared.plan import SolverStatus
from bindaems.shared.timeutil import LOCAL_TZ, ensure_utc

TIME_LIMIT_S: Final = 20.0
MIP_GAP: Final = 0.005

_KWH: Final = SLOT_H / 1000  # W über einen Slot → kWh


@dataclass(frozen=True)
class SlotFlows:
    """Flüsse eines Slots in W und SOC am Slotende in kWh.

    ``pv_h``/``pv_b``/``pv_x``: PV ins Haus, in den Akku, ins Netz; ``gr_h``/``gr_b``: Netz ins
    Haus, in den Akku; ``bt_h``: Akku ins Haus.
    """

    pv_h: float
    pv_b: float
    pv_x: float
    gr_h: float
    gr_b: float
    bt_h: float
    soc_end_kwh: float


@dataclass(frozen=True)
class OptimizerResult:
    """Ergebnis eines Laufs; ``flows`` ist leer, wenn es keine Lösung gibt.

    ``peaks_kw`` ist die Spitze des Optimierers je Monat mit Leistungspreis (``JJJJ-MM``).
    """

    status: SolverStatus
    flows: tuple[SlotFlows, ...]
    peaks_kw: Mapping[str, float]
    objective_ct: float | None
    solve_ms: int
    message: str | None = None


def grid_charge_cap_w(inputs: PlanInputs, index: int) -> float:
    """Höchstes Netzladen ``GC_t`` im Slot ``index``; 0 bei ausgeschaltetem Schalter.

    Der Sollwert bei ``NETZLADEN(P)`` ist Hauslast + P, daher begrenzt neben der Ladeleistung
    auch der höchste Sollwert abzüglich der Last (Präzisierung 5).
    """
    return _grid_charge_cap_w(inputs, sim_slots(inputs)[index])


def _grid_charge_cap_w(inputs: PlanInputs, slot: SimSlot) -> float:
    if not inputs.planning.grid_charge_allowed:
        return 0.0
    setpoint_w = inputs.battery.max_grid_charge_setpoint_w
    return min(slot.max_charge_w, max(0.0, setpoint_w - slot.load_w))


def _month(inputs: PlanInputs, index: int) -> str:
    return ensure_utc(inputs.slots[index].start).astimezone(LOCAL_TZ).strftime("%Y-%m")


def _value(var: pulp.LpVariable) -> float:
    value = var.varValue
    return max(0.0, float(value)) if value is not None else 0.0


def solve(
    inputs: PlanInputs, *, time_limit_s: float = TIME_LIMIT_S, gap: float = MIP_GAP
) -> OptimizerResult:
    """Löst das Modell; jede Ausnahme wird zum Status ``error`` mit ihrem Text."""
    started = time.perf_counter()
    try:
        return _solve(inputs, time_limit_s, gap, started)
    except Exception as exc:  # Solver und Modellaufbau dürfen den Dienst nie anhalten
        return OptimizerResult(
            status="error",
            flows=(),
            peaks_kw={},
            objective_ct=None,
            solve_ms=_elapsed_ms(started),
            message=str(exc),
        )


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _solve(inputs: PlanInputs, time_limit_s: float, gap: float, started: float) -> OptimizerResult:
    params = params_of(inputs)
    slots = sim_slots(inputs)
    planning = inputs.planning
    battery = inputs.battery
    switch = planning.grid_charge_allowed
    count = len(inputs.slots)
    soc0 = battery.soc_pct / 100 * params.capacity_kwh

    prob = pulp.LpProblem("akku", pulp.LpMinimize)

    def var(name: str, t: int, upper: float | None = None) -> pulp.LpVariable:
        return prob.add_variable(f"{name}_{t}", lowBound=0, upBound=upper)

    pv_h = [var("pv_h", t) for t in range(count)]
    pv_b = [var("pv_b", t) for t in range(count)]
    pv_x = [var("pv_x", t) for t in range(count)]
    gr_h = [var("gr_h", t) for t in range(count)]
    caps = [_grid_charge_cap_w(inputs, sim) for sim in slots]
    gr_b = [var("gr_b", t, caps[t]) for t in range(count)]
    bt_h = [var("bt_h", t) for t in range(count)]
    y = [prob.add_variable(f"y_{t}", cat=pulp.LpBinary) for t in range(count)]
    z: dict[int, pulp.LpVariable] = {
        t: prob.add_variable(f"z_{t}", cat=pulp.LpBinary)
        for t in range(count)
        if switch and caps[t] > 0
    }
    soc = [
        prob.add_variable(f"s_{t + 1}", lowBound=params.lo_kwh, upBound=params.hi_kwh)
        for t in range(count)
    ]

    terms: list[Any] = []
    for t, (slot, sim) in enumerate(zip(inputs.slots, slots, strict=True)):
        charge = pv_b[t] + gr_b[t]
        prob += pv_h[t] + pv_b[t] + pv_x[t] == slot.pv_w, f"pv_{t}"
        prob += pv_h[t] + gr_h[t] + bt_h[t] == slot.load_w, f"haus_{t}"
        prob += charge <= sim.max_charge_w * y[t], f"laden_{t}"
        prob += bt_h[t] <= sim.max_discharge_w * (1 - y[t]), f"entladen_{t}"
        if t in z:
            prob += gr_b[t] <= caps[t] * z[t], f"netzladen_{t}"
        prob += gr_h[t] + gr_b[t] <= battery.grid_max_import_w, f"bezug_{t}"
        before = soc0 if t == 0 else soc[t - 1]
        prob += (
            soc[t] == before + _KWH * (params.eta_ch * charge - bt_h[t] / params.eta_dis),
            f"soc_{t}",
        )
        feed_in_ct = slot.feed_in_ct or 0.0
        terms.append(
            _KWH
            * (
                slot.import_ct * (gr_h[t] + gr_b[t])
                - feed_in_ct * pv_x[t]
                + planning.wear_ct * bt_h[t]
                + planning.margin_ct * gr_b[t]
            )
        )

    # Regel R1: Netzladen nur, wenn ab diesem Slot nichts mehr eingespeist wird.
    for t, z_t in z.items():
        pv_energy = sum(_KWH * s.pv_w for s in inputs.slots[t:])
        if pv_energy > 0:
            prob += (
                pulp.lpSum(_KWH * pv_x[tau] for tau in range(t, count)) <= pv_energy * (1 - z_t),
                f"r1_{t}",
            )

    # Leistungspreis: Spitze je Monat über der verrechneten Spitze vor dem Horizont.
    peaks: dict[str, tuple[float, pulp.LpVariable, pulp.LpVariable]] = {}
    for k, peak in enumerate(inputs.peak_months):
        d1 = prob.add_variable(
            f"d1_{k}", lowBound=0, upBound=max(0.0, peak.tier_kw - peak.peak0_kw)
        )
        d2 = prob.add_variable(f"d2_{k}", lowBound=0)
        peaks[peak.month] = (peak.peak0_kw, d1, d2)
        terms.append(peak.below_ct_per_kw * d1 + peak.above_ct_per_kw * d2)
    for t in range(count):
        entry = peaks.get(_month(inputs, t))
        if entry is not None:
            peak0, d1, d2 = entry
            prob += (gr_h[t] + gr_b[t]) / 1000 <= peak0 + d1 + d2, f"spitze_{t}"

    # Planträgheit: Abweichung der Netto-Akkuleistung vom vorigen Plan in den ersten Slots.
    for t, slot in enumerate(inputs.slots[:INERTIA_SLOTS]):
        if slot.prev_battery_w is None:
            continue
        dev = var("dev", t)
        net = pv_b[t] + gr_b[t] - bt_h[t]
        prob += dev >= net - slot.prev_battery_w, f"traeg_auf_{t}"
        prob += dev >= slot.prev_battery_w - net, f"traeg_ab_{t}"
        terms.append(_KWH * planning.inertia_ct * dev)

    terms.append(-inputs.end_value_ct * soc[-1])
    prob += pulp.lpSum(terms)

    prob.solve(pulp.HiGHS(msg=False, timeLimit=time_limit_s, gapRel=gap))
    status = _status(prob)
    if prob.sol_status not in (pulp.LpSolutionOptimal, pulp.LpSolutionIntegerFeasible):
        return OptimizerResult(
            status=status,
            flows=(),
            peaks_kw={},
            objective_ct=None,
            solve_ms=_elapsed_ms(started),
            message=pulp.LpStatus[prob.status] if status == "error" else None,
        )
    flows = tuple(
        SlotFlows(
            pv_h=_value(pv_h[t]),
            pv_b=_value(pv_b[t]),
            pv_x=_value(pv_x[t]),
            gr_h=_value(gr_h[t]),
            gr_b=_value(gr_b[t]),
            bt_h=_value(bt_h[t]),
            soc_end_kwh=float(soc[t].varValue),
        )
        for t in range(count)
    )
    return OptimizerResult(
        status=status,
        flows=flows,
        peaks_kw={
            month: peak0 + _value(d1) + _value(d2) for month, (peak0, d1, d2) in peaks.items()
        },
        objective_ct=float(pulp.value(prob.objective)),
        solve_ms=_elapsed_ms(started),
    )


def _status(prob: pulp.LpProblem) -> SolverStatus:
    """Status des Laufs: ``sol_status`` 1 optimal, 2 Zeitlimit mit Lösung; ein Zeitlimit ohne
    Lösung meldet PuLP als „Not Solved“, HiGHS selbst als ``kTimeLimit``."""
    if prob.sol_status == pulp.LpSolutionOptimal:
        return "optimal"
    if prob.sol_status == pulp.LpSolutionIntegerFeasible:
        return "time_limit"
    if prob.status == pulp.LpStatusInfeasible:
        return "infeasible"
    model = getattr(prob, "solverModel", None)
    if model is not None and model.getModelStatus() == highspy.HighsModelStatus.kTimeLimit:
        return "time_limit"
    return "error"
