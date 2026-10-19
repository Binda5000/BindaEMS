"""Optimierer für Akku und geplante Autos (Plan 3b, Präzisierungen 5 und 6; Plan 4b,
Präzisierung 11): ein MILP mit HiGHS über PuLP.

Minimiert über den Horizont Bezug minus Einspeisung, Verschleiß beim Entladen, die Marge beim
Netzladen, den Leistungspreis je Monat und die Abweichung vom vorigen Plan in den ersten Slots,
abzüglich des Restwerts der Energie im Akku am Horizontende. Regel R1 ist hart: Netzladen nur,
wenn ab diesem Slot laut Prognose nichts mehr eingespeist wird.

Geplante Autos (``PlanInputs.vehicles``) laden je Slot vor ihrer Abfahrt ``e = pv_ev + gr_ev``,
0 oder zwischen ``min_w`` und ``max_w``. PV deckt zuerst das Haus, dann die Autos, dann den Akku:
Solange ein Auto aus dem Netz lädt, geht keine PV in den Akku oder ins Netz. Der Akku gibt nichts
ins Auto. Die Energien der Autos stehen auf der Wallbox-Seite (Ruling R10): Der Bedarf
``Σ e · Δ + Fehlmenge ≥ horizon_need_kwh`` rechnet ohne Wirkungsgrad, denn der steckt schon in
``need_kwh``; Regel R2 vergleicht die Netzenergie ins Auto ``Σ gr_ev · Δ`` mit
``grid_allowed_kwh``. Die Fehlmenge kostet ``SHORTFALL_CT``, die Überschreitung von R2 ``R2_CT``
je kWh: Ziel vor Regel R vor Kosten. PV ins Auto kostet nichts, Netz ins Auto den Bezugspreis.
Ohne Autos ist das Modell das des Akkus allein.

Leistungen in W, Energien in kWh, Preise in ct/kWh. Reine Rechnung im selben Prozess; den
Kindprozess und das harte Zeitlimit stellt der Planungsdienst.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import highspy
import pulp

from bindaems.app.planning.battery_model import SLOT_H, SimSlot, params_of, sim_slots
from bindaems.app.planning.inputs import INERTIA_SLOTS, PlanInputs
from bindaems.app.planning.vehicles import PlanVehicleInput, _slots_before
from bindaems.shared.plan import SolverStatus
from bindaems.shared.timeutil import LOCAL_TZ, ensure_utc

TIME_LIMIT_S: Final = 20.0
MIP_GAP: Final = 0.005
SHORTFALL_CT: Final = 2000.0  # Strafe je kWh, die einem Auto bis zur Abfahrt fehlt
R2_CT: Final = 200.0  # Strafe je kWh Netzenergie ins Auto über Regel R2

_KWH: Final = SLOT_H / 1000  # W über einen Slot → kWh


@dataclass(frozen=True)
class SlotFlows:
    """Flüsse eines Slots in W und SOC am Slotende in kWh.

    ``pv_h``/``pv_b``/``pv_x``: PV ins Haus, in den Akku, ins Netz; ``gr_h``/``gr_b``: Netz ins
    Haus, in den Akku; ``bt_h``: Akku ins Haus. ``ev``: ``(pv_ev, gr_ev)`` je geplantem Auto, PV
    und Netz ins Auto; es fehlen Autos, die in dem Slot nicht laden können (ab der Abfahrt, ohne
    Bedarf).
    """

    pv_h: float
    pv_b: float
    pv_x: float
    gr_h: float
    gr_b: float
    bt_h: float
    soc_end_kwh: float
    ev: Mapping[str, tuple[float, float]] = field(default_factory=dict)


@dataclass(frozen=True)
class OptimizerResult:
    """Ergebnis eines Laufs; ``flows`` ist leer, wenn es keine Lösung gibt.

    ``peaks_kw`` ist die Spitze des Optimierers je Monat mit Leistungspreis (``JJJJ-MM``).
    Je geplantem Auto: ``shortfall_kwh`` die Fehlmenge gegen ``horizon_need_kwh``,
    ``r2_excess_kwh`` die Netzenergie über ``grid_allowed_kwh`` (Regel R2), beide in kWh auf der
    Wallbox-Seite; leer ohne Lösung.
    """

    status: SolverStatus
    flows: tuple[SlotFlows, ...]
    peaks_kw: Mapping[str, float]
    objective_ct: float | None
    solve_ms: int
    message: str | None = None
    shortfall_kwh: Mapping[str, float] = field(default_factory=dict)
    r2_excess_kwh: Mapping[str, float] = field(default_factory=dict)


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


@dataclass(frozen=True, eq=False)
class _Car:
    """Variablen des Autos ``inputs.vehicles[index]`` in den Slots ``0 … len(pv) − 1`` vor
    seiner Abfahrt."""

    index: int
    car: PlanVehicleInput
    pv: list[pulp.LpVariable]
    grid: list[pulp.LpVariable]
    short: pulp.LpVariable
    rho: pulp.LpVariable

    def power(self, t: int) -> Any:
        """Ladeleistung ``e`` im Slot ``t`` in W."""
        return self.pv[t] + self.grid[t]


def _add_car(prob: pulp.LpProblem, inputs: PlanInputs, index: int) -> _Car:
    """Variablen und Bedingungen des Autos ``inputs.vehicles[index]``; die Kosten stehen in
    ``_solve``.

    Variablen gibt es in den Slots vor dem ersten Slot ab der Abfahrt (``T_c``), ohne Bedarf in
    keinem. Je Slot ``min_w · u ≤ e ≤ max_w · u`` mit ``u`` binär. Bedarf auf der Wallbox-Seite
    (Ruling R10): ``Σ e · Δ + short ≥ horizon_need_kwh``, ohne Wirkungsgrad. Mehr als
    ``need_kwh`` lädt es nicht, denn am Ziel stoppt der core; liegt der Bedarf unter einem Slot
    mit ``min_w``, darf es diesen einen Slot laden. Regel R2: ``Σ gr_ev · Δ ≤ grid_allowed_kwh +
    rho``.
    """
    car = inputs.vehicles[index]
    count = _slots_before(car.departure_at, inputs.slots) if car.need_kwh > 0 else 0
    pv = [
        prob.add_variable(f"pv_ev{index}_{t}", lowBound=0, upBound=car.max_w) for t in range(count)
    ]
    grid = [
        prob.add_variable(f"gr_ev{index}_{t}", lowBound=0, upBound=car.max_w) for t in range(count)
    ]
    short = prob.add_variable(f"fehl{index}", lowBound=0)
    rho = prob.add_variable(f"rho{index}", lowBound=0)
    entry = _Car(index=index, car=car, pv=pv, grid=grid, short=short, rho=rho)
    for t in range(count):
        on = prob.add_variable(f"u{index}_{t}", cat=pulp.LpBinary)
        prob += entry.power(t) <= car.max_w * on, f"auto{index}_max_{t}"
        prob += entry.power(t) >= car.min_w * on, f"auto{index}_min_{t}"
    energy = pulp.lpSum(_KWH * entry.power(t) for t in range(count))
    prob += energy + short >= car.horizon_need_kwh, f"auto{index}_bedarf"
    if count:
        prob += energy <= max(car.need_kwh, _KWH * car.min_w), f"auto{index}_ziel"
        prob += (
            pulp.lpSum(_KWH * g for g in grid) <= car.grid_allowed_kwh + rho,
            f"auto{index}_r2",
        )
    return entry


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

    # PV deckt zuerst das Haus: sonst ginge PV in den Akku und das Haus bezöge aus dem Netz,
    # verdecktes Netzladen an Schalter, ``GC_t``, Marge und R1 vorbei.
    pv_h = [
        prob.add_variable(f"pv_h_{t}", lowBound=cover, upBound=cover)
        for t, cover in enumerate(min(s.pv_w, s.load_w) for s in inputs.slots)
    ]
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
    cars = [_add_car(prob, inputs, index) for index in range(len(inputs.vehicles))]
    # Autos, die im Slot laden können; PV und Netz ins Auto je Slot
    active = [[c for c in cars if t < len(c.pv)] for t in range(count)]
    car_pv = [pulp.lpSum(c.pv[t] for c in active[t]) for t in range(count)]
    car_grid = [pulp.lpSum(c.grid[t] for c in active[t]) for t in range(count)]
    imports = [gr_h[t] + gr_b[t] + car_grid[t] for t in range(count)]

    terms: list[Any] = []
    for t, (slot, sim) in enumerate(zip(inputs.slots, slots, strict=True)):
        charge = pv_b[t] + gr_b[t]
        prob += pv_h[t] + car_pv[t] + pv_b[t] + pv_x[t] == slot.pv_w, f"pv_{t}"
        prob += pv_h[t] + gr_h[t] + bt_h[t] == slot.load_w, f"haus_{t}"
        prob += charge <= sim.max_charge_w * y[t], f"laden_{t}"
        prob += bt_h[t] <= sim.max_discharge_w * (1 - y[t]), f"entladen_{t}"
        if t in z:
            prob += gr_b[t] <= caps[t] * z[t], f"netzladen_{t}"
        prob += imports[t] <= battery.grid_max_import_w, f"bezug_{t}"
        if active[t] and slot.pv_w > slot.load_w:
            # PV erst in die Autos: Lädt eines aus dem Netz, geht keine PV in Akku oder Netz
            # (sonst Netz über das Auto in den Akku, an R1 und Schalter vorbei)
            w = prob.add_variable(f"w_{t}", cat=pulp.LpBinary)
            prob += car_grid[t] <= sum(c.car.max_w for c in active[t]) * w, f"auto_netz_{t}"
            prob += pv_b[t] + pv_x[t] <= slot.pv_w * (1 - w), f"auto_pv_{t}"
        before = soc0 if t == 0 else soc[t - 1]
        prob += (
            soc[t] == before + _KWH * (params.eta_ch * charge - bt_h[t] / params.eta_dis),
            f"soc_{t}",
        )
        feed_in_ct = slot.feed_in_ct or 0.0
        terms.append(
            _KWH
            * (
                slot.import_ct * imports[t]
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
            prob += imports[t] / 1000 <= peak0 + d1 + d2, f"spitze_{t}"

    # Planträgheit: Abweichung der Netto-Akkuleistung vom vorigen Plan in den ersten Slots.
    for t, slot in enumerate(inputs.slots[:INERTIA_SLOTS]):
        if slot.prev_battery_w is None:
            continue
        dev = var("dev", t)
        net = pv_b[t] + gr_b[t] - bt_h[t]
        prob += dev >= net - slot.prev_battery_w, f"traeg_auf_{t}"
        prob += dev >= slot.prev_battery_w - net, f"traeg_ab_{t}"
        terms.append(_KWH * planning.inertia_ct * dev)
    # ... und der Ladeleistung jedes Autos (ein Auto, das der vorige Plan nicht nennt: 0 W)
    for t, slot in enumerate(inputs.slots[:INERTIA_SLOTS]):
        if slot.prev_ev_w is None:
            continue
        for c in active[t]:
            dev = var(f"dev_ev{c.index}", t)
            previous = slot.prev_ev_w.get(c.car.key, 0.0)
            prob += dev >= c.power(t) - previous, f"traeg_auto{c.index}_auf_{t}"
            prob += dev >= previous - c.power(t), f"traeg_auto{c.index}_ab_{t}"
            terms.append(_KWH * planning.inertia_ct * dev)

    for c in cars:
        terms.append(SHORTFALL_CT * c.short + R2_CT * c.rho)
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
            ev={c.car.key: (_value(c.pv[t]), _value(c.grid[t])) for c in active[t]},
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
        shortfall_kwh={c.car.key: _value(c.short) for c in cars},
        r2_excess_kwh={c.car.key: _value(c.rho) for c in cars},
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
