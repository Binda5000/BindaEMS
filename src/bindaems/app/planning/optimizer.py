"""Optimierer für Akku und geplante Autos (Plan 3b, Präzisierungen 5 und 6; Plan 4b,
Präzisierung 11): ein MILP mit HiGHS über PuLP.

Minimiert über den Horizont Bezug minus Einspeisung, Verschleiß beim Entladen, die Marge beim
Netzladen, den Leistungspreis je Monat und die Abweichung vom vorigen Plan in den ersten Slots,
abzüglich des Restwerts der Energie im Akku am Horizontende. Regel R1 ist hart: Nach Netzladen
wird nichts eingespeist, solange Netzenergie im Akku sein kann, also bis der Akku wieder leer ist
(SOC am Slotende ≤ Reserve + ``R1_EMPTY_TOL_KWH``, auch wenn der SOC zu Beginn darunter liegt) oder
der Horizont endet. Das gilt auch für Netzenergie, die schon zu Beginn im Akku sein kann
(``PlanInputs.grid_in_battery_at_start``, Fix-Welle F2): Sie sperrt Akku ins Auto hart, die
Einspeisung kostet bis zum leeren Akku ``CARRIED_EXPORT_CT`` je kWh, denn vermeiden lässt sie sich
nicht immer, wenn das Netzladen schon geschehen ist.

Geplante Autos (``PlanInputs.vehicles``) laden je Slot vor ihrer Abfahrt ``e = pv_ev + gr_ev +
bt_ev``, 0 oder zwischen ``min_w`` und ``max_w``. PV deckt zuerst das Haus, dann die Autos, dann
den Akku: Solange ein Auto aus dem Netz oder dem Akku lädt, geht keine PV in den Akku oder ins
Netz. Die Energien der Autos stehen auf der Wallbox-Seite (Ruling R10): Der Bedarf
``Σ e · Δ + Fehlmenge ≥ horizon_need_kwh`` rechnet ohne Wirkungsgrad, denn der steckt schon in
``need_kwh``; Regel R2 vergleicht die Netzenergie ins Auto ``Σ gr_ev · Δ`` mit
``grid_allowed_kwh``. Die Fehlmenge kostet ``SHORTFALL_CT``, die Überschreitung von R2 ``R2_CT``
je kWh: Ziel vor Regel R vor Kosten. PV ins Auto kostet nichts, Netz ins Auto den Bezugspreis.
Ohne Autos ist das Modell das des Akkus allein.

Akku ins Auto (Plan Akku ins Auto, Entscheidungen 2 und 3): Nur mit dem Schalter
``planning.car_battery_allowed`` gibt es ``bt_ev`` je Auto und Slot, ``0 ≤ bt_ev ≤ max_w``; sonst
ist das Modell das von vorher. Haus und Autos teilen sich die Entladegrenze und den SOC, je kWh
kostet ``bt_ev`` Verschleiß plus Marge (die Verluste stecken im SOC). Solange Netzenergie im Akku
sein kann (``c_t`` aus Regel R1, auch Netzenergie von vor dem Horizont), gibt der Akku keinem Auto
etwas, sonst ginge Netzstrom über den Akku an Regel R2 vorbei ins Auto. Regel R2 zählt ``bt_ev``
nicht.

Ein erwartetes Auto (Plan 5b, Präzisierung 10) steht erst ab seiner Ankunft im Modell: Seine
Variablen gibt es nur in den Slots ``[arrival_at, departure_at)`` (``charge_window``); Bedarf,
Ziel, Fehlmenge und Regel R2 rechnen sonst wie bei einem angesteckten Auto.

Netzgrenze je Phase (Plan Netzgrenze je Phase, Entscheidungen 4 und 5): Mit
``BatteryState.grid_phase_import_w`` gilt zusätzlich zur Summengrenze je Slot ``t`` und Phase ``p``
``(gr_h + gr_b + gr_r) / 3 + Σ_{c aktiv, p ∈ Phasen_c} gr_ev_c / n_c ≤ R_t`` (``phase_room_w``).
Haus und Netzladen sind symmetrisch, ein Auto lädt auf jeder seiner Phasen gleich viel
(``phase_shares``); PV und Akku ins Auto zählen nicht. Ohne Grenze gibt es keine solche Bedingung.

Nachladen auf die Reserve (Plan Netzgrenze je Phase, Entscheidung 9): Liegt der SOC zu Beginn
unter der Reserve, gibt es ``gr_r`` (Netz → Akku, auch bei ausgeschaltetem Schalter) mit
``0 ≤ gr_r ≤ recharge_cap_w``. Es zählt wie ``gr_b`` zur Ladung, zum Bezug, zur Spitze und zur
Phasengrenze und kostet den Bezugspreis ohne Marge, setzt aber kein ``z_t`` (Regel R1). Höchstens
die Lücke bis zur Reserve wird nachgeladen (``Σ η_ch · gr_r · Δ ≤ Reserve − SOC₀``), und was
nachgeladen ist, verlässt den Akku nicht (``s_t ≥ SOC₀ + Σ_{τ≤t} η_ch · gr_r_τ · Δ``), sonst wäre
es Netzladen am Schalter vorbei. Ziel ist ``s_T + short_r ≥ Reserve`` am Horizontende,
``short_r`` kostet ``SHORTFALL_CT`` je kWh; Nachgeladenes trägt keinen Restwert
(``+ Restwert · Σ η_ch · gr_r · Δ``), sonst lohnte ``gr_r`` auch über der Reserve oder wenn die
PV sie füllt. PV in den Akku bleibt vorrangig, weil sie nichts kostet. Mit dem Schalter lädt ein
Slot zusammen höchstens ``GC_t`` (``gr_b + gr_r ≤ GC_t``).

Unwetter-Reserve (Plan Unwetter-Reserve, Entscheidungen 5 und 6): Hat ein Slot ``t`` eine
Untergrenze ``F_t`` (``SlotInput.storm_floor_pct``, in kWh umgerechnet), gilt am Slotende
``s_t + short_s_t ≥ F_t`` mit ``short_s_t ≥ 0``; je kWh und Slot kostet ``short_s``
``STORM_SHORTFALL_CT``. Die Untergrenze ist weich, damit das Modell lösbar bleibt, wenn das Ziel
nicht mehr zu erreichen ist (Warnung schon im Gange, Akku leer); hart bleibt die Reserve
(``lo``). Liegt der SOC zu Beginn unter der höchsten Untergrenze ``F_max``, gibt es bis zum letzten
Slot mit Untergrenze ``gr_s`` (Netz → Akku für die Unwetter-Reserve, auch bei ausgeschaltetem
Schalter) mit ``0 ≤ gr_s ≤ storm_charge_caps_w``. Es teilt sich die Obergrenze des Nachladens
(``gr_r + gr_s ≤ recharge_cap_w``-Formel), zählt wie ``gr_r`` zur Ladung, zum Bezug, zur Spitze,
zur Phasengrenze und zur Planträgheit, kostet den Bezugspreis ohne Marge, setzt kein ``z_t``
(Regel R1) und trägt keinen Restwert. Höchstens ``Σ η_ch · gr_s · Δ ≤ F_max − max(SOC₀,
Reserve)`` wird aufgeladen. Was aufgeladen ist, bleibt bis zum letzten Slot mit Untergrenze im
Akku (``s_t ≥ min(SOC₀, Reserve) + Nachgeladenes + Aufgeladenes``, eine Bedingung zusammen mit
der des Nachladens), sonst wäre es Netzladen am Schalter vorbei; danach darf das Haus es
verbrauchen. PV bleibt vorrangig, weil sie nichts kostet. Gibt es ``gr_s``, gibt der Akku keinem
Auto etwas (kein ``bt_ev``): Die Plausibilität zählt das Aufladen als Netzenergie im Akku, und
Regel R2 bleibt so unberührt. Mit dem Schalter lädt ein Slot zusammen
höchstens ``GC_t`` (``gr_b + gr_r + gr_s ≤ GC_t``). Ohne Untergrenze gibt es weder ``gr_s`` noch
eine dieser Bedingungen.

Leistungen in W, Energien in kWh, Preise in ct/kWh. Reine Rechnung im selben Prozess; den
Kindprozess und das harte Zeitlimit stellt der Planungsdienst.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import highspy
import pulp

from bindaems.app.planning.battery_model import (
    SLOT_H,
    BatteryParams,
    SimSlot,
    params_of,
    reserve_kwh,
    sim_slots,
)
from bindaems.app.planning.grid_phases import PHASES, phase_room_w, phase_shares
from bindaems.app.planning.inputs import INERTIA_SLOTS, PlanInputs
from bindaems.app.planning.vehicles import PlanVehicleInput, cars_charging, charge_window
from bindaems.shared.plan import RESERVE_RECHARGE_MAX_W, SolverStatus
from bindaems.shared.timeutil import LOCAL_TZ, ensure_utc

TIME_LIMIT_S: Final = 20.0
MIP_GAP: Final = 0.005
SHORTFALL_CT: Final = 2000.0  # Strafe je kWh, die einem Auto bis zur Abfahrt fehlt
R2_CT: Final = 200.0  # Strafe je kWh Netzenergie ins Auto über Regel R2
R1_EMPTY_TOL_KWH: Final = 0.05  # Akku gilt als leer bei SOC ≤ untere Grenze + Toleranz (Regel R1)
# Strafe je kWh Einspeisung, solange Netzenergie von vor dem Horizont im Akku sein kann (Regel R1)
CARRIED_EXPORT_CT: Final = 200.0
# Strafe je kWh und Slot unter der Untergrenze der Unwetter-Reserve (Entscheidung 5): 1 kWh über
# 10 Slots kostet so viel wie 1 kWh Fehlmenge eines Autos
STORM_SHORTFALL_CT: Final = 200.0

_KWH: Final = SLOT_H / 1000  # W über einen Slot → kWh


@dataclass(frozen=True)
class SlotFlows:
    """Flüsse eines Slots in W und SOC am Slotende in kWh.

    ``pv_h``/``pv_b``/``pv_x``: PV ins Haus, in den Akku, ins Netz; ``gr_h``/``gr_b``: Netz ins
    Haus, in den Akku; ``bt_h``: Akku ins Haus. ``gr_r``: Netz in den Akku beim Nachladen auf die
    Reserve (Entscheidung 9), 0 ohne. ``gr_s``: Netz in den Akku für die Unwetter-Reserve (Plan
    Unwetter-Reserve, Entscheidung 6), 0 ohne. ``ev``: ``(pv_ev, gr_ev)`` je geplantem Auto, PV
    und Netz ins Auto; es fehlen Autos, die in dem Slot nicht laden können (ab der Abfahrt, ohne
    Bedarf, ein erwartetes Auto vor seiner Ankunft). ``ev_battery``: ``bt_ev`` je Auto, Akku ins
    Auto, nur mit dem Schalter „Akku ins Auto, wenn übrig“ und nur für Autos, die im Slot laden
    können.
    """

    pv_h: float
    pv_b: float
    pv_x: float
    gr_h: float
    gr_b: float
    bt_h: float
    soc_end_kwh: float
    ev: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    ev_battery: Mapping[str, float] = field(default_factory=dict)
    gr_r: float = 0.0
    gr_s: float = 0.0


@dataclass(frozen=True)
class OptimizerResult:
    """Ergebnis eines Laufs; ``flows`` ist leer, wenn es keine Lösung gibt.

    ``peaks_kw`` ist die Spitze des Optimierers je Monat mit Leistungspreis oder gedachtem Satz
    (``JJJJ-MM``, ``PeakMonth.virtual``). Mit
    erwarteten Autos (Plan 5b, Ruling F-1) ist sie die Spitze ohne deren Netzleistung:
    ``max(peak0, höchster Bezug im Monat ohne erwartete Autos)``. Sie geht ins Budget des Wächters
    im core, und ein Auto, das nur die Prognose kennt, darf es nicht anheben.
    Je geplantem Auto: ``shortfall_kwh`` die Fehlmenge gegen ``horizon_need_kwh``,
    ``r2_excess_kwh`` die Netzenergie über ``grid_allowed_kwh`` (Regel R2), beide in kWh auf der
    Wallbox-Seite; leer ohne Lösung. ``storm_shortfall_kwh`` ist die Summe der Fehlmengen unter
    der Untergrenze der Unwetter-Reserve über die Slots (kWh · Slot), 0 ohne.
    """

    status: SolverStatus
    flows: tuple[SlotFlows, ...]
    peaks_kw: Mapping[str, float]
    objective_ct: float | None
    solve_ms: int
    message: str | None = None
    shortfall_kwh: Mapping[str, float] = field(default_factory=dict)
    r2_excess_kwh: Mapping[str, float] = field(default_factory=dict)
    storm_shortfall_kwh: float = 0.0


def ev_power_w(result: OptimizerResult) -> list[dict[str, float]]:
    """Ladeleistung ``e = pv_ev + gr_ev + bt_ev`` je Auto und Slot in W; ein Auto unter
    ``EV_ON_W`` (Rundung des Solvers) fehlt im Slot."""
    return [
        cars_charging(
            {
                key: pv + grid + flows.ev_battery.get(key, 0.0)
                for key, (pv, grid) in flows.ev.items()
            }
        )
        for flows in result.flows
    ]


def ev_battery_power_w(result: OptimizerResult) -> list[dict[str, float]]:
    """Akkuanteil ``bt_ev`` je Auto und Slot in W; ein Auto unter ``EV_ON_W`` fehlt im Slot."""
    return [cars_charging(dict(flows.ev_battery)) for flows in result.flows]


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


def recharge_cap_w(inputs: PlanInputs, index: int) -> float:
    """Höchstes Nachladen auf die Reserve im Slot ``index`` (Entscheidung 9): ``min(
    RESERVE_RECHARGE_MAX_W, Ladeleistung, höchster Sollwert − Last)``, unabhängig vom Schalter; 0,
    wenn der SOC zu Beginn nicht unter der Reserve liegt. Für alle Slots ``recharge_caps_w``."""
    if not _below_reserve(inputs):
        return 0.0
    return _recharge_cap_w(inputs, sim_slots(inputs)[index])


def recharge_caps_w(inputs: PlanInputs) -> list[float]:
    """``recharge_cap_w`` aller Slots in einem Zug; ohne SOC unter der Reserve lauter 0."""
    if not _below_reserve(inputs):
        return [0.0] * len(inputs.slots)
    return [_recharge_cap_w(inputs, slot) for slot in sim_slots(inputs)]


def _below_reserve(inputs: PlanInputs) -> bool:
    return inputs.battery.soc_pct < inputs.battery.reserve_pct


def _recharge_cap_w(inputs: PlanInputs, slot: SimSlot) -> float:
    setpoint_w = inputs.battery.max_grid_charge_setpoint_w
    return min(RESERVE_RECHARGE_MAX_W, slot.max_charge_w, max(0.0, setpoint_w - slot.load_w))


def storm_charge_caps_w(inputs: PlanInputs) -> list[float]:
    """Höchstes Aufladen für die Unwetter-Reserve je Slot (Entscheidung 6): die Formel von
    ``recharge_cap_w`` in den Slots bis zum letzten mit Untergrenze, wenn der SOC zu Beginn unter
    der höchsten Untergrenze liegt; sonst 0. Nachladen und Aufladen teilen sich diese Grenze."""
    return _storm_caps_w(inputs, sim_slots(inputs))


def _storm_caps_w(inputs: PlanInputs, slots: Sequence[SimSlot]) -> list[float]:
    floors = [t for t, s in enumerate(inputs.slots) if s.storm_floor_pct is not None]
    top_pct = max((inputs.slots[t].storm_floor_pct or 0.0 for t in floors), default=0.0)
    if not floors or inputs.battery.soc_pct >= top_pct:
        return [0.0] * len(inputs.slots)
    last = floors[-1]
    return [_recharge_cap_w(inputs, slot) if t <= last else 0.0 for t, slot in enumerate(slots)]


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
    """Variablen des Autos ``inputs.vehicles[index]`` je Slot, in dem es laden kann (Schlüssel
    von ``pv`` und ``grid``: ab der Ankunft, vor der Abfahrt). ``battery`` (Akku ins Auto) hat
    dieselben Schlüssel, mit ausgeschaltetem Schalter keine."""

    index: int
    car: PlanVehicleInput
    pv: dict[int, pulp.LpVariable]
    grid: dict[int, pulp.LpVariable]
    battery: dict[int, pulp.LpVariable]
    short: pulp.LpVariable
    rho: pulp.LpVariable

    def power(self, t: int) -> Any:
        """Ladeleistung ``e`` im Slot ``t`` in W."""
        if t in self.battery:
            return self.pv[t] + self.grid[t] + self.battery[t]
        return self.pv[t] + self.grid[t]


def _add_car(
    prob: pulp.LpProblem, inputs: PlanInputs, index: int, *, battery_allowed: bool
) -> _Car:
    """Variablen und Bedingungen des Autos ``inputs.vehicles[index]``; die Kosten stehen in
    ``_solve``.

    Variablen gibt es in den Slots vor dem ersten Slot ab der Abfahrt (``T_c``), bei einem
    erwarteten Auto erst ab dem ersten Slot ab der Ankunft (``charge_window``), ohne Bedarf in
    keinem. Je Slot ``min_w · u ≤ e ≤ max_w · u`` mit ``u`` binär. Bedarf auf der Wallbox-Seite
    (Ruling R10): ``Σ e · Δ + short ≥ horizon_need_kwh``, ohne Wirkungsgrad. Mehr als
    ``need_kwh`` lädt es nicht, denn am Ziel stoppt der core; liegt der Bedarf unter einem Slot
    mit ``min_w``, darf es diesen einen Slot laden. Regel R2: ``Σ gr_ev · Δ ≤ grid_allowed_kwh +
    rho``; ``bt_ev`` (nur mit ``planning.car_battery_allowed``) zählt zum Bedarf, nicht zu R2.
    """
    car = inputs.vehicles[index]
    window = charge_window(car, inputs.slots) if car.need_kwh > 0 else range(0)
    pv = {t: prob.add_variable(f"pv_ev{index}_{t}", lowBound=0, upBound=car.max_w) for t in window}
    grid = {
        t: prob.add_variable(f"gr_ev{index}_{t}", lowBound=0, upBound=car.max_w) for t in window
    }
    battery = (
        {t: prob.add_variable(f"bt_ev{index}_{t}", lowBound=0, upBound=car.max_w) for t in window}
        if battery_allowed
        else {}
    )
    short = prob.add_variable(f"fehl{index}", lowBound=0)
    rho = prob.add_variable(f"rho{index}", lowBound=0)
    entry = _Car(index=index, car=car, pv=pv, grid=grid, battery=battery, short=short, rho=rho)
    for t in window:
        on = prob.add_variable(f"u{index}_{t}", cat=pulp.LpBinary)
        prob += entry.power(t) <= car.max_w * on, f"auto{index}_max_{t}"
        prob += entry.power(t) >= car.min_w * on, f"auto{index}_min_{t}"
    energy = pulp.lpSum(_KWH * entry.power(t) for t in window)
    prob += energy + short >= car.horizon_need_kwh, f"auto{index}_bedarf"
    if window:
        prob += energy <= max(car.need_kwh, _KWH * car.min_w), f"auto{index}_ziel"
        prob += (
            pulp.lpSum(_KWH * g for g in grid.values()) <= car.grid_allowed_kwh + rho,
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
    # Nachladen auf die Reserve (Entscheidung 9): nur unter der Reserve, auch ohne Schalter
    reserve = reserve_kwh(inputs)
    below_reserve = _below_reserve(inputs)
    recharge_caps = [_recharge_cap_w(inputs, sim) if below_reserve else 0.0 for sim in slots]
    gr_r = {t: var("gr_r", t, cap) for t, cap in enumerate(recharge_caps) if cap > 0}
    # Aufladen für die Unwetter-Reserve (Entscheidung 6): bis zum Ende des letzten Fensters
    gr_s = {t: var("gr_s", t, cap) for t, cap in enumerate(_storm_caps_w(inputs, slots)) if cap > 0}
    # Netz in den Akku je Slot: Netzladen, Nachladen und Aufladen
    to_battery: list[Any] = []
    for t in range(count):
        into = gr_b[t] + gr_r[t] if t in gr_r else gr_b[t]
        to_battery.append(into + gr_s[t] if t in gr_s else into)
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
    # Mit Aufladen für die Unwetter-Reserve gibt der Akku keinem Auto etwas (Ruling R7)
    car_battery_allowed = planning.car_battery_allowed and not gr_s
    cars = [
        _add_car(prob, inputs, index, battery_allowed=car_battery_allowed)
        for index in range(len(inputs.vehicles))
    ]
    # Autos, die im Slot laden können; PV und Netz ins Auto je Slot
    active = [[c for c in cars if t in c.pv] for t in range(count)]
    car_pv = [pulp.lpSum(c.pv[t] for c in active[t]) for t in range(count)]
    car_grid = [pulp.lpSum(c.grid[t] for c in active[t]) for t in range(count)]
    # Akku ins Auto je Slot (nur mit dem Schalter; sonst leer)
    car_battery = [
        pulp.lpSum(c.battery[t] for c in active[t] if t in c.battery) for t in range(count)
    ]
    car_battery_max = [sum(c.car.max_w for c in active[t] if t in c.battery) for t in range(count)]
    imports = [gr_h[t] + to_battery[t] + car_grid[t] for t in range(count)]
    # Netzgrenze je Phase (Entscheidung 4): Anteil jedes Autos je Phase; ohne Grenze keine
    phase_limit_w = battery.grid_phase_import_w
    shares = {c.index: phase_shares(c.car.grid_phases) for c in cars}

    terms: list[Any] = []
    for t, (slot, sim) in enumerate(zip(inputs.slots, slots, strict=True)):
        charge = pv_b[t] + to_battery[t]
        prob += pv_h[t] + car_pv[t] + pv_b[t] + pv_x[t] == slot.pv_w, f"pv_{t}"
        prob += pv_h[t] + gr_h[t] + bt_h[t] == slot.load_w, f"haus_{t}"
        prob += charge <= sim.max_charge_w * y[t], f"laden_{t}"
        discharge = bt_h[t] + car_battery[t] if car_battery_max[t] else bt_h[t]
        prob += discharge <= sim.max_discharge_w * (1 - y[t]), f"entladen_{t}"
        if t in z:
            prob += gr_b[t] <= caps[t] * z[t], f"netzladen_{t}"
        if (t in gr_r or t in gr_s) and switch:
            prob += to_battery[t] <= caps[t], f"netzladen_summe_{t}"
        if t in gr_r and t in gr_s:
            # Nachladen und Aufladen teilen sich die Grenze des Nachladens
            prob += gr_r[t] + gr_s[t] <= _recharge_cap_w(inputs, sim), f"unwetter_nachladen_{t}"
        prob += imports[t] <= battery.grid_max_import_w, f"bezug_{t}"
        if phase_limit_w is not None:
            room_w = phase_room_w(phase_limit_w, slot.load_w, slot.pv_w)
            for phase in PHASES:
                cars_w = pulp.lpSum(
                    shares[c.index][phase] * c.grid[t]
                    for c in active[t]
                    if phase in shares[c.index]
                )
                prob += (gr_h[t] + to_battery[t]) / 3 + cars_w <= room_w, f"phase_{t}_{phase}"
        if active[t] and slot.pv_w > slot.load_w:
            # PV erst in die Autos: Lädt eines aus dem Netz, geht keine PV in Akku oder Netz
            # (sonst Netz über das Auto in den Akku, an R1 und Schalter vorbei); ebenso, wenn es
            # aus dem Akku lädt (sonst entlüde der Akku über das Auto ins Netz)
            w = prob.add_variable(f"w_{t}", cat=pulp.LpBinary)
            from_outside = car_grid[t] + car_battery[t] if car_battery_max[t] else car_grid[t]
            prob += from_outside <= sum(c.car.max_w for c in active[t]) * w, f"auto_netz_{t}"
            prob += pv_b[t] + pv_x[t] <= slot.pv_w * (1 - w), f"auto_pv_{t}"
        before = soc0 if t == 0 else soc[t - 1]
        prob += (
            soc[t] == before + _KWH * (params.eta_ch * charge - discharge / params.eta_dis),
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
        if car_battery_max[t]:
            terms.append(_KWH * (planning.wear_ct + planning.margin_ct) * car_battery[t])

    # Regel R1: Keine Einspeisung, solange Netzenergie im Akku sein kann. ``e_t`` = 1 heißt: Der
    # Akku ist am Ende von Slot t leer (SOC ≤ Reserve + Toleranz, auch wenn SOC₀ darunter liegt:
    # Energie unter der Reserve verlässt den Akku nie, Entscheidung 9).
    # Die Binärvariable des letzten Slots bleibt, um die Lösezeit nicht zu berühren. ``c_t`` ≥ 0
    # hält fest, dass nach Netzladen noch Netzenergie im Akku ist: Es springt bei Netzladen auf 1
    # und fällt erst nach einem leeren Akku wieder. ``c_t`` ist nur nach unten beschränkt, der
    # Löser drückt es so weit wie möglich, und es bleibt stetig.
    #
    # Netzenergie von vor dem Horizont (``grid_in_battery_at_start``, Fix-Welle F2) hält ``k_t``
    # fest, ebenso gebaut, mit ``k_0`` = 1. Für Akku ins Auto ist es hart wie ``c_t``. Für die
    # Einspeisung kostet es ``CARRIED_EXPORT_CT`` je kWh (``x_t``) statt sie zu verbieten: Das
    # Netzladen ist schon geschehen, und wird der Akku vor der PV nicht leer, ließe sich die
    # Einspeisung nicht mehr vermeiden; ein hartes Verbot machte das Modell unlösbar.
    carried = inputs.grid_in_battery_at_start
    if z or carried:
        first = 0 if carried else min(z)
        span = params.hi_kwh - params.lo_kwh
        empty = {t: prob.add_variable(f"e_{t}", cat=pulp.LpBinary) for t in range(first, count)}
        for t in range(first, count):
            prob += (
                soc[t] <= reserve + R1_EMPTY_TOL_KWH + span * (1 - empty[t]),
                f"leer_{t}",
            )
    if z:
        start = min(z)
        in_battery = {
            t: prob.add_variable(f"c_{t}", lowBound=0, upBound=1) for t in range(start, count)
        }
        for t in range(start, count):
            if t in z:
                prob += in_battery[t] >= z[t], f"netz_im_akku_z_{t}"
            if t > start:
                prob += in_battery[t] >= in_battery[t - 1] - empty[t - 1], f"netz_im_akku_{t}"
            if inputs.slots[t].pv_w > 0:
                prob += pv_x[t] <= inputs.slots[t].pv_w * (1 - in_battery[t]), f"r1_{t}"
            # Entscheidung 2: Kein Akku ins Auto, solange Netzenergie im Akku sein kann
            if car_battery_max[t]:
                prob += (
                    car_battery[t] <= car_battery_max[t] * (1 - in_battery[t]),
                    f"r1_auto_{t}",
                )
    if carried:
        before = {
            t: prob.add_variable(f"k_{t}", lowBound=1 if t == 0 else 0, upBound=1)
            for t in range(count)
        }
        for t in range(count):
            if t > 0:
                prob += before[t] >= before[t - 1] - empty[t - 1], f"netz_von_vorher_{t}"
            pv_w = inputs.slots[t].pv_w
            if pv_w > 0:
                over = var("x_vorher", t)
                prob += pv_x[t] <= pv_w * (1 - before[t]) + over, f"r1_vorher_{t}"
                terms.append(_KWH * CARRIED_EXPORT_CT * over)
            if car_battery_max[t]:
                prob += (
                    car_battery[t] <= car_battery_max[t] * (1 - before[t]),
                    f"r1_auto_vorher_{t}",
                )

    # Leistungspreis: Spitze je Monat über der verrechneten Spitze vor dem Horizont; ein gedachter
    # Monat ohne Leistungspreis (Entscheidung 8) rechnet gleich, mit ``tier_kw`` 0 nur über ``d2``.
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
        net = pv_b[t] + to_battery[t] - bt_h[t] - car_battery[t]
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

    # Slots, in denen Aufgeladenes im Akku bleibt: vom ersten ``gr_s`` bis zum letzten Slot mit
    # Untergrenze (Ruling R9); dort rechnet ``_add_storm`` das Nachgeladene mit
    floor_slots = [t for t, s in enumerate(inputs.slots) if s.storm_floor_pct is not None]
    kept = range(min(gr_s), floor_slots[-1] + 1) if gr_s else range(0)
    recharged: dict[int, Any] = {}
    if below_reserve:
        recharged = _add_recharge(
            prob, terms, soc, gr_r, soc0, reserve, params.eta_ch, inputs.end_value_ct, kept
        )
    storm_short = _add_storm(prob, terms, inputs, soc, gr_s, soc0, reserve, params, recharged)

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
            ev_battery={c.car.key: _value(c.battery[t]) for c in active[t] if t in c.battery},
            gr_r=_value(gr_r[t]) if t in gr_r else 0.0,
            gr_s=_value(gr_s[t]) if t in gr_s else 0.0,
        )
        for t in range(count)
    )
    expected = {car.key for car in inputs.vehicles if car.expected}
    if expected:
        peaks_kw = _known_peaks_kw(inputs, {m: p[0] for m, p in peaks.items()}, flows, expected)
    else:
        peaks_kw = {
            month: peak0 + _value(d1) + _value(d2) for month, (peak0, d1, d2) in peaks.items()
        }
    return OptimizerResult(
        status=status,
        flows=flows,
        peaks_kw=peaks_kw,
        objective_ct=float(pulp.value(prob.objective)),
        solve_ms=_elapsed_ms(started),
        shortfall_kwh={c.car.key: _value(c.short) for c in cars},
        r2_excess_kwh={c.car.key: _value(c.rho) for c in cars},
        storm_shortfall_kwh=sum(_value(short) for short in storm_short),
    )


def _add_recharge(
    prob: pulp.LpProblem,
    terms: list[Any],
    soc: list[pulp.LpVariable],
    gr_r: Mapping[int, pulp.LpVariable],
    soc0: float,
    reserve: float,
    eta_ch: float,
    end_value_ct: float,
    kept: range = range(0),
) -> dict[int, Any]:
    """Bedingungen und Ziel des Nachladens auf die Reserve (Entscheidung 9), nur bei ``SOC₀`` unter
    der Reserve: Höchstens die Lücke bis zur Reserve wird nachgeladen (``nachladen_menge``), das
    Nachgeladene bleibt im Akku (``nachgeladen_t`` über die Summe ``r_t`` bis Slot t), und am
    Horizontende steht der Akku an der Reserve oder es fehlt ``short_r`` (``SHORTFALL_CT`` je kWh).
    Gibt die Summe ``r_t`` je Slot zurück. In den Slots ``kept`` (Aufladen für die Unwetter-Reserve)
    fehlt ``nachgeladen_t``: Dort steht die gemeinsame Bedingung von ``_add_storm``.

    Nachgeladenes trägt keinen Restwert: ``+ end_value_ct · Σ η_ch · gr_r · Δ`` hebt den Anteil an
    ``− end_value_ct · s_T`` auf. Sonst lohnte ``gr_r``, sobald ``import_ct < η_ch · Restwert``, und
    wäre Netzladen am Schalter vorbei, auch wenn die PV die Reserve füllt.
    """
    totals: dict[int, Any] = {}
    if gr_r:
        recharged = pulp.lpSum(eta_ch * _KWH * g for g in gr_r.values())
        prob += recharged <= reserve - soc0, "nachladen_menge"
        terms.append(end_value_ct * recharged)
        totals = _running(prob, "r", gr_r, eta_ch, len(soc))
        for t, total in totals.items():
            if t not in kept:
                prob += soc[t] >= soc0 + total, f"nachgeladen_{t}"
    short = prob.add_variable("fehl_reserve", lowBound=0)
    prob += soc[-1] + short >= reserve, "reserve_ende"
    terms.append(SHORTFALL_CT * short)
    return totals


def _running(
    prob: pulp.LpProblem,
    name: str,
    flows: Mapping[int, pulp.LpVariable],
    eta_ch: float,
    stop: int,
) -> dict[int, Any]:
    """Geladene Energie ``Σ_{τ≤t} η_ch · flows_τ · Δ`` in kWh je Slot ``t`` vom ersten Slot mit
    ``flows`` bis vor ``stop``; je Slot mit Fluss eine Variable ``{name}_t`` mit Bedingung
    ``{name}_summe_t``, sonst gilt die Summe des Slots davor."""
    totals: dict[int, Any] = {}
    total: Any = 0
    for t in range(min(flows), stop):
        if t in flows:
            running = prob.add_variable(f"{name}_{t}", lowBound=0)
            prob += running == total + eta_ch * _KWH * flows[t], f"{name}_summe_{t}"
            total = running
        totals[t] = total
    return totals


def _add_storm(
    prob: pulp.LpProblem,
    terms: list[Any],
    inputs: PlanInputs,
    soc: list[pulp.LpVariable],
    gr_s: Mapping[int, pulp.LpVariable],
    soc0: float,
    reserve: float,
    params: BatteryParams,
    recharged: Mapping[int, Any],
) -> list[pulp.LpVariable]:
    """Untergrenze und Aufladen der Unwetter-Reserve (Entscheidungen 5 und 6); gibt die
    Fehlmengen ``short_s`` je Slot mit Untergrenze zurück, ohne Untergrenze keine.

    Je Slot ``t`` mit Untergrenze ``s_t + short_s_t ≥ F_t`` (``unwetter_t``), ``short_s`` kostet
    ``STORM_SHORTFALL_CT`` je kWh. Aufgeladen wird höchstens bis zur höchsten Untergrenze
    (``unwetter_menge``: ``Σ η_ch · gr_s · Δ ≤ F_max − max(SOC₀, Reserve)``). Wie beim Nachladen
    trägt Aufgeladenes keinen Restwert (``+ end_value_ct · Σ η_ch · gr_s · Δ``), sonst lohnte
    ``gr_s`` über die Untergrenzen hinaus.

    Aufgeladenes bleibt bis zum letzten Slot mit Untergrenze im Akku (Ruling R9, wie
    ``nachgeladen_t``): ``s_t ≥ min(SOC₀, Reserve) + r_t + Σ_{τ≤t} η_ch · gr_s_τ · Δ``
    (``unwetter_gehalten_t``) vom ersten Slot mit ``gr_s`` an, mit ``r_t`` dem Nachgeladenen bis
    Slot t (``recharged``, ohne Nachladen 0). Sonst ginge es vor dem Fenster schon wieder ins Haus,
    Netzladen am Schalter vorbei, auch wenn die PV die Untergrenze füllt. Danach darf das Haus es
    verbrauchen (Entscheidung 6).
    """
    floors = {
        t: slot.storm_floor_pct / 100 * params.capacity_kwh
        for t, slot in enumerate(inputs.slots)
        if slot.storm_floor_pct is not None
    }
    if gr_s:
        charged = pulp.lpSum(params.eta_ch * _KWH * g for g in gr_s.values())
        prob += charged <= max(floors.values()) - max(soc0, reserve), "unwetter_menge"
        terms.append(inputs.end_value_ct * charged)
        # Basis ``min(SOC₀, Reserve)``, nicht ``SOC₀`` (Ruling I1): Der Akku darf vor dem Fenster
        # Energie über der Reserve ans Haus abgeben, auch wenn er mit SOC₀ < F startet; nur das
        # Aufgeladene und das Nachgeladene bleiben. Netzladen ist so höchstens bis ``F_max`` und
        # mit 3 kW möglich, nie mehr (``unwetter_menge``). Eine Basis SOC₀ hielte auch die
        # Eigenenergie fest, die nur die Untergrenze im Fenster schützen muss.
        base = min(soc0, reserve)
        stored = _running(prob, "unwetter_s", gr_s, params.eta_ch, max(floors) + 1)
        for t, total in stored.items():
            prob += (
                soc[t] >= base + recharged.get(t, 0) + total,
                f"unwetter_gehalten_{t}",
            )
    shorts: list[pulp.LpVariable] = []
    for t, floor_kwh in floors.items():
        short = prob.add_variable(f"fehl_unwetter_{t}", lowBound=0)
        prob += soc[t] + short >= floor_kwh, f"unwetter_{t}"
        terms.append(STORM_SHORTFALL_CT * short)
        shorts.append(short)
    return shorts


def _known_peaks_kw(
    inputs: PlanInputs,
    peak0_kw: Mapping[str, float],
    flows: tuple[SlotFlows, ...],
    expected: set[str],
) -> dict[str, float]:
    """Spitze je Monat ohne die Netzleistung der erwarteten Autos (Ruling F-1): das Größere aus
    ``peak0`` und dem höchsten Bezug ``gr_h + gr_b + gr_r + gr_s + Netz in angesteckte Autos``
    im Monat."""
    result = dict(peak0_kw)
    for t, item in enumerate(flows):
        month = _month(inputs, t)
        if month not in result:
            continue
        cars_w = sum(grid for key, (_, grid) in item.ev.items() if key not in expected)
        grid_w = item.gr_h + item.gr_b + item.gr_r + item.gr_s + cars_w
        result[month] = max(result[month], grid_w / 1000)
    return result


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
