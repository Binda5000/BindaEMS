"""Tests der geplanten Autos als Eingangsdaten des Planers (Plan 4b, Task 9, Präzisierung 10)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from tests.plan_helpers import (
    PLANNED_DEPARTURE,
    at,
    planned_car,
    planned_health,
    scenario,
    vehicle_health,
)

from bindaems.app.planning.inputs import SlotInput
from bindaems.app.planning.vehicles import grid_allowed, horizon_need, planned_vehicles
from bindaems.shared.config import Config
from bindaems.shared.control import ChargeMode, Intent, VehicleIntent

START = "2026-10-16T18:00:00+00:00"  # 20:00 Ortszeit
NOW = at("2026-10-16T18:05:00+00:00")
DEPARTURE = PLANNED_DEPARTURE  # 07:00 Ortszeit
HOUR = timedelta(hours=1)


def _slots(count: int = 48, **kwargs: Any) -> list[SlotInput]:
    """``count`` Slots ab ``START`` (Standard 12 h) ohne PV, Last 500 W."""
    return scenario(
        prices=[20.0] * count,
        pv=[0.0] * count,
        load=[500.0] * count,
        start=START,
        **kwargs,
    ).slots


def _intent(version: int = 3) -> Intent:
    planned = VehicleIntent(mode=ChargeMode.PLANNED)
    return Intent(version=version, vehicles={"tesla": planned})


def test_only_planned_plugged_cars_with_trusted_health(cfg: Config) -> None:
    slots = _slots()
    health = planned_health(
        vehicle_health("tesla"), vehicle_health("egolf", mode="pv", planned=None)
    )
    cars = planned_vehicles(cfg, health, _intent(), now=NOW, slots=slots)
    assert [car.key for car in cars] == ["tesla"]
    tesla = cars[0]
    assert (tesla.name, tesla.wallbox, tesla.departure_at) == (
        "Tesla Model 3",
        "twc",
        at(DEPARTURE),
    )
    assert (tesla.soc_pct, tesla.soc_assumed, tesla.target_soc_pct, tesla.limit_pct) == (
        40.0,
        False,
        80.0,
        None,
    )
    assert (tesla.efficiency, tesla.usable_kwh) == (0.9, 75.0)
    assert (tesla.min_w, tesla.max_w) == (5 * 230 * 3, 16 * 230 * 3)
    assert tesla.need_kwh == pytest.approx(40 / 100 * 75 / 0.9)
    # Abfahrt im Horizont, keine PV: ganzer Bedarf im Horizont und alles aus dem Netz erlaubt
    assert tesla.horizon_need_kwh == tesla.need_kwh == tesla.grid_allowed_kwh

    # Health aus einem Zyklus mit älterer Absicht: kein Auto
    assert planned_vehicles(cfg, health, _intent(version=4), now=NOW, slots=slots) == []
    # ohne bekannte Absicht kein Auto
    assert planned_vehicles(cfg, health, None, now=NOW, slots=slots) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"plugged": False},
        {"plugged": None},
        {"wallbox": None},
        {"wallbox": "unbekannt"},
        {"mode": "boost"},
        {"planned": None},
        {"departure_at": "2026-10-16T18:10:00+00:00"},  # noch im aktuellen Slot
        {"departure_at": "kein Datum"},
    ],
)
def test_cars_without_a_usable_plan_entry_are_left_out(
    cfg: Config, changes: dict[str, Any]
) -> None:
    health = planned_health(vehicle_health("tesla", **changes))
    assert planned_vehicles(cfg, health, _intent(), now=NOW, slots=_slots()) == []


def test_departure_at_the_end_of_the_current_slot_is_included(cfg: Config) -> None:
    health = planned_health(vehicle_health("tesla", departure_at="2026-10-16T18:15:00+00:00"))
    assert len(planned_vehicles(cfg, health, _intent(), now=NOW, slots=_slots())) == 1


def test_health_without_vehicles_or_without_health_has_no_cars(cfg: Config) -> None:
    assert planned_vehicles(cfg, None, _intent(), now=NOW, slots=_slots()) == []
    assert planned_vehicles(cfg, {"intent_version": 3}, _intent(), now=NOW, slots=_slots()) == []


def test_assumed_soc_counts_from_zero_and_the_limit_is_kept(cfg: Config) -> None:
    entry = vehicle_health(
        "egolf", soc_pct=0.0, soc_source="assumed", charge_limit_pct=70.0, target=70.0
    )
    [egolf] = planned_vehicles(cfg, planned_health(entry), _intent(), now=NOW, slots=_slots())
    assert (egolf.soc_pct, egolf.soc_assumed, egolf.limit_pct) == (0.0, True, 70.0)
    assert egolf.target_soc_pct == 70.0  # wirksames Ziel aus Health
    assert egolf.need_kwh == pytest.approx(70 / 100 * 38.6 / 0.9)
    # e-Golf an der EVCS: zwei Phasen, Mindeststrom der EVCS und des Autos 6 A
    assert (egolf.min_w, egolf.max_w) == (6 * 230 * 2, 16 * 230 * 2)


def test_missing_soc_is_assumed(cfg: Config) -> None:
    entry = vehicle_health("tesla", soc_pct=None, soc_source=None)
    [tesla] = planned_vehicles(cfg, planned_health(entry), _intent(), now=NOW, slots=_slots())
    assert (tesla.soc_pct, tesla.soc_assumed) == (0.0, True)


def test_cars_follow_the_priority_of_the_intent(cfg: Config) -> None:
    health = planned_health(vehicle_health("tesla"), vehicle_health("egolf"))
    intent = _intent().model_copy(update={"priority": ["egolf", "tesla"]})
    cars = planned_vehicles(cfg, health, intent, now=NOW, slots=_slots())
    assert [car.key for car in cars] == ["egolf", "tesla"]
    # ohne vollständige Priorität gilt die Reihenfolge der Konfiguration
    intent = _intent().model_copy(update={"priority": ["egolf"]})
    cars = planned_vehicles(cfg, health, intent, now=NOW, slots=_slots())
    assert [car.key for car in cars] == ["tesla", "egolf"]


def test_r2_allowance_uses_p25_surplus_in_priority_order() -> None:
    # P25 3500 W, Last 500 W: 3 kW Überschuss je Slot, 0,75 kWh; acht Slots bis zur Abfahrt 6 kWh.
    # P50 liegt höher und zählt nicht.
    slots = _slots(8, pv_p25=[3500.0] * 8)
    slots = [slot.model_copy(update={"pv_w": 6000.0}) for slot in slots]
    departure = at(START) + timedelta(hours=2)
    tesla = planned_car(key="tesla", horizon_need_kwh=10.0, departure_at=departure)
    egolf = planned_car(key="egolf", horizon_need_kwh=4.0, departure_at=departure)
    allowed = grid_allowed([tesla, egolf], slots, ["tesla", "egolf"])
    assert allowed == pytest.approx({"tesla": 4.0, "egolf": 4.0})
    allowed = grid_allowed([tesla, egolf], slots, ["egolf", "tesla"])
    assert allowed == pytest.approx({"tesla": 8.0, "egolf": 0.0})


def test_r2_counts_only_slots_before_the_departure() -> None:
    slots = _slots(8, pv_p25=[3500.0] * 8)
    # Abfahrt nach vier Slots: 3 kWh Überschuss davor
    car = planned_car(horizon_need_kwh=10.0, departure_at=at(START) + timedelta(hours=1))
    assert grid_allowed([car], slots, ["tesla"]) == pytest.approx({"tesla": 7.0})


def test_r2_without_p25_uses_p50_and_never_goes_negative() -> None:
    slots = _slots(8)
    slots = [slot.model_copy(update={"pv_w": 2500.0}) for slot in slots]  # 0,5 kWh je Slot
    car = planned_car(horizon_need_kwh=3.0, departure_at=at(START) + timedelta(hours=3))
    assert grid_allowed([car], slots, ["tesla"]) == pytest.approx({"tesla": 0.0})


def test_r2_leaves_the_early_surplus_for_a_car_that_leaves_earlier() -> None:
    # Überschuss nur in den ersten vier Slots (je 0,75 kWh), danach keiner. Das erste Auto fährt
    # spät und nimmt 1,5 kWh, von hinten; das zweite fährt nach vier Slots und bekommt den Rest.
    slots = _slots(8, pv_p25=[3500.0] * 4 + [0.0] * 4)
    first = planned_car(key="tesla", horizon_need_kwh=1.5, departure_at=at(START) + 2 * HOUR)
    second = planned_car(key="egolf", horizon_need_kwh=2.0, departure_at=at(START) + HOUR)
    allowed = grid_allowed([first, second], slots, ["tesla", "egolf"])
    assert allowed == pytest.approx({"tesla": 0.0, "egolf": 0.5})


def test_departure_beyond_horizon_reduces_the_need() -> None:
    horizon_end = at("2026-10-17T12:00:00+00:00")
    departure = horizon_end + 2 * HOUR
    # Wallbox-Seite (Ruling R10): need enthält η schon, die Zeit danach zählt ohne η
    assert horizon_need(30.0, 11040.0, departure, horizon_end) == pytest.approx(30 - 11.04 * 2)
    assert horizon_need(30.0, 11040.0, departure, horizon_end) == pytest.approx(7.92)
    # Abfahrt im Horizont: der ganze Bedarf; weit dahinter: nichts
    assert horizon_need(30.0, 11040.0, horizon_end, horizon_end) == 30.0
    assert horizon_need(30.0, 11040.0, horizon_end - HOUR, horizon_end) == 30.0
    assert horizon_need(30.0, 11040.0, horizon_end + 10 * HOUR, horizon_end) == 0.0


def test_planned_vehicles_carry_horizon_need_and_allowance(cfg: Config) -> None:
    # 16 Slots ab 18:00 UTC, Abfahrt 2 h nach dem Horizontende, keine PV
    slots = _slots(16)
    horizon_end = slots[-1].start + timedelta(minutes=15)
    departure = (horizon_end + 2 * HOUR).isoformat()
    health = planned_health(vehicle_health("tesla", soc_pct=40.0, departure_at=departure))
    [tesla] = planned_vehicles(cfg, health, _intent(), now=NOW, slots=slots)
    expected = horizon_need(tesla.need_kwh, tesla.max_w, tesla.departure_at, horizon_end)
    assert tesla.horizon_need_kwh == pytest.approx(expected) and expected < tesla.need_kwh
    assert tesla.grid_allowed_kwh == pytest.approx(expected)


def test_infeasible_target_stays_in_the_inputs_with_its_full_need(cfg: Config) -> None:
    # Review Focus 5: zu spät angesteckt, 40 kWh in zwei Slots gehen nicht; das Auto bleibt mit
    # dem ganzen Bedarf drin, der Optimierer weist die Fehlmenge aus
    departure = (at(START) + timedelta(minutes=30)).isoformat()
    health = planned_health(
        vehicle_health("tesla", soc_pct=30.0, target=80.0, departure_at=departure)
    )
    [tesla] = planned_vehicles(cfg, health, _intent(), now=NOW, slots=_slots())
    assert tesla.need_kwh == pytest.approx(50 / 100 * 75 / 0.9)
    assert tesla.horizon_need_kwh == tesla.need_kwh


def test_departure_passed_since_the_last_health_uses_the_next_one(cfg: Config) -> None:
    # Health stammt aus einem Zyklus vor der Abfahrt um 07:00 Ortszeit; jetzt ist 07:00:30
    now = at("2026-10-17T05:00:30+00:00")
    slots = scenario(
        prices=[20.0] * 8, pv=[0.0] * 8, load=[500.0] * 8, start="2026-10-17T05:00:00+00:00"
    ).slots
    intent = Intent(
        version=3, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PLANNED, departure="07:00")}
    )
    health = planned_health(vehicle_health("tesla", departure_at="2026-10-17T05:00:00+00:00"))
    [tesla] = planned_vehicles(cfg, health, intent, now=now, slots=slots)
    assert tesla.departure_at == datetime.fromisoformat("2026-10-18T05:00:00+00:00")
