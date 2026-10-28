"""Eigenschaften des Optimierers (Präzisierungen 5 und 6; Plan 4b, Präzisierung 11) über zufällige
kurze Horizonte mit bis zu zwei geplanten Autos.

Jede Eingabe ist lösbar. Für jedes Ergebnis: SOC innerhalb der Grenzen, Einspeisung nie über der
PV, das Haus bezieht aus Netz und Akku nie mehr als den Fehlbetrag (PV deckt es zuerst), nie Laden
und Entladen im selben Slot, kein Netzladen bei ausgeschaltetem Schalter und Regel R1: Nach einem
Slot mit Netzladen wird bis zum Leerstand des Akkus (oder zum Horizontende) nichts eingespeist.

Für die Autos: kein Laden ab der Abfahrt, Ladeleistung 0 oder zwischen ``min_w`` und ``max_w``,
die PV-Bilanz und der Netzanschluss gelten mit den Autos, die Netzenergie ins Auto bleibt unter
``grid_allowed_kwh`` plus der gemeldeten Überschreitung (Regel R2), Ladung und Fehlmenge decken den
Bedarf im Horizont, und solange ein Auto aus dem Netz lädt, geht keine PV in Akku oder Netz.

Akku ins Auto (Plan Akku ins Auto): Haus und Autos zusammen entladen nie über der Entladegrenze,
nie zugleich mit Laden, nichts bei ausgeschaltetem Schalter, nichts, solange nach Netzladen
Netzenergie im Akku sein kann (Regel R1 bis zum Leerstand), und nichts, solange PV in Akku oder Netz
geht.
"""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
from tests.plan_helpers import planned_car, scenario

from bindaems.app.planning.battery_model import SLOT_H, params_of, sim_slots
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.optimizer import R1_EMPTY_TOL_KWH, solve
from bindaems.app.planning.vehicles import (
    PlanVehicleInput,
    grid_allowed,
    horizon_need,
    need_kwh,
)
from bindaems.shared.timeutil import SLOT

_KWH = SLOT_H / 1000
_EMPTY_EPS_KWH = 1e-6  # Rundung des Lösers an der Leer-Grenze von Regel R1

# Zwei erfundene Autos: Schlüssel, Name, Wallbox, Kapazität, kleinste und volle Ladeleistung
_CARS = (
    ("tesla", "Tesla Model 3", "twc", 75.0, 5 * 230.0 * 3, 16 * 230.0 * 3),
    ("egolf", "e-Golf", "evcs", 32.0, 6 * 230.0 * 2, 16 * 230.0 * 2),
)


@st.composite
def horizons(draw: st.DrawFn) -> dict[str, object]:
    n = draw(st.integers(min_value=4, max_value=12))
    series = st.lists
    pv = draw(series(st.floats(0.0, 9000.0), min_size=n, max_size=n))
    p25_share = draw(st.floats(0.0, 1.0))
    cars = [
        {
            "departs_after": draw(st.integers(min_value=1, max_value=n + 8)),
            "soc_pct": draw(st.floats(0.0, 100.0)),
            "target_soc_pct": draw(st.floats(20.0, 100.0)),
            "efficiency": draw(st.floats(0.5, 1.0)),
        }
        for _ in range(draw(st.integers(min_value=0, max_value=2)))
    ]
    return {
        "prices": draw(series(st.floats(-5.0, 40.0), min_size=n, max_size=n)),
        "pv": pv,
        "pv_p25": [p25_share * w for w in pv],
        "load": draw(series(st.floats(100.0, 3000.0), min_size=n, max_size=n)),
        "soc_pct": draw(st.floats(10.0, 100.0)),
        "grid_charge": draw(st.booleans()),
        "car_battery": draw(st.booleans()),
        "cars": cars,
    }


def _with_cars(inputs: PlanInputs, specs: list[dict[str, Any]]) -> PlanInputs:
    """Die Autos zu den Slots von ``inputs``: Bedarf, Bedarf im Horizont und Regel R2 wie
    ``planned_vehicles``."""
    first = inputs.slots[0].start
    horizon_end = inputs.slots[-1].start + SLOT
    cars: list[PlanVehicleInput] = []
    for spec, (key, name, wallbox, usable, min_w, max_w) in zip(specs, _CARS, strict=False):
        departure_at = first + spec["departs_after"] * SLOT
        need = need_kwh(spec["soc_pct"], spec["target_soc_pct"], usable, spec["efficiency"])
        cars.append(
            planned_car(
                key=key,
                name=name,
                wallbox=wallbox,
                soc_pct=spec["soc_pct"],
                target_soc_pct=spec["target_soc_pct"],
                departure_at=departure_at,
                efficiency=spec["efficiency"],
                usable_kwh=usable,
                min_w=min_w,
                max_w=max_w,
                need_kwh=need,
                horizon_need_kwh=horizon_need(need, max_w, departure_at, horizon_end),
            )
        )
    allowed = grid_allowed(cars, inputs.slots, [car.key for car in cars])
    vehicles = [car.model_copy(update={"grid_allowed_kwh": allowed[car.key]}) for car in cars]
    return inputs.model_copy(update={"vehicles": vehicles})


@settings(max_examples=30, deadline=None)
@given(horizons())
def test_optimizer_respects_limits_and_rule_r1(kwargs: dict[str, Any]) -> None:
    specs = kwargs.pop("cars")
    inputs = _with_cars(scenario(**kwargs), specs)
    result = solve(inputs)
    assert result.status in ("optimal", "time_limit") and result.flows
    params = params_of(inputs)
    sims = sim_slots(inputs)
    for index, (slot, flow) in enumerate(zip(inputs.slots, result.flows, strict=True)):
        bt_ev = sum(flow.ev_battery.values())
        assert params.lo_kwh - 0.01 <= flow.soc_end_kwh <= params.hi_kwh + 0.01
        assert flow.pv_x <= slot.pv_w + 1
        assert flow.gr_h + flow.bt_h <= max(0.0, slot.load_w - slot.pv_w) + 1
        assert flow.bt_h + bt_ev <= sims[index].max_discharge_w + 1
        assert min(flow.pv_b + flow.gr_b, flow.bt_h + bt_ev) <= 1
        if not inputs.planning.grid_charge_allowed:
            assert flow.gr_b == 0
        if not inputs.planning.car_battery_allowed:
            assert bt_ev == 0
        if flow.gr_b > 1:
            end = next(
                (
                    j
                    for j in range(index, len(result.flows))
                    if result.flows[j].soc_end_kwh
                    <= params.lo_kwh + R1_EMPTY_TOL_KWH + _EMPTY_EPS_KWH
                ),
                len(result.flows) - 1,
            )
            later = result.flows[index : end + 1]
            assert sum(f.pv_x for f in later) * SLOT_H / 1000 <= 0.001
            assert all(sum(f.ev_battery.values()) <= 1 for f in later)
        pv_ev = sum(pv for pv, _ in flow.ev.values())
        gr_ev = sum(grid for _, grid in flow.ev.values())
        assert flow.pv_h + pv_ev + flow.pv_b + flow.pv_x <= slot.pv_w + 1
        assert flow.gr_h + flow.gr_b + gr_ev <= inputs.battery.grid_max_import_w + 1
        if gr_ev > 1 or bt_ev > 1:
            assert flow.pv_b + flow.pv_x <= 1
    for car in inputs.vehicles:
        charged = [
            sum(flow.ev.get(car.key, (0.0, 0.0))) + flow.ev_battery.get(car.key, 0.0)
            for flow in result.flows
        ]
        for slot, power_w in zip(inputs.slots, charged, strict=True):
            if slot.start >= car.departure_at:
                assert power_w == 0
            assert power_w <= 1 or car.min_w - 1 <= power_w <= car.max_w + 1
        grid_kwh = sum(flow.ev.get(car.key, (0.0, 0.0))[1] for flow in result.flows) * _KWH
        assert grid_kwh <= car.grid_allowed_kwh + result.r2_excess_kwh[car.key] + 1e-3
        shortfall = result.shortfall_kwh[car.key]
        assert sum(charged) * _KWH + shortfall >= car.horizon_need_kwh - 1e-3
