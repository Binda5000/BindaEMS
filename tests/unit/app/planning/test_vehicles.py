"""Tests der geplanten Autos als Eingangsdaten des Planers (Plan 4b, Task 9, Präzisierung 10)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from tests.plan_helpers import (
    PLANNED_DEPARTURE,
    at,
    planned_car,
    planned_health,
    scenario,
    vehicle_health,
)

from bindaems.app.learning.driving_profile import VehicleProfile, WeekdayProfile
from bindaems.app.planning.inputs import SlotInput
from bindaems.app.planning.vehicles import (
    EV_ON_W,
    cars_charging,
    expected_vehicles,
    grid_allowed,
    horizon_need,
    latest_start,
    planned_vehicles,
)
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


def test_r2_counts_only_what_the_car_can_take_and_leaves_the_rest() -> None:
    # Ruling R11: 9 kW Überschuss je Slot (2,25 kWh) über 2 h. Der e-Golf nimmt höchstens
    # 7,36 kW (1,84 kWh je Slot, 14,72 kWh); der Rest (0,41 kWh je Slot, 3,28 kWh) geht an den
    # Tesla.
    slots = _slots(8, pv_p25=[9500.0] * 8)
    departure = at(START) + 2 * HOUR
    egolf = planned_car(
        key="egolf", max_w=7360.0, min_w=2760.0, horizon_need_kwh=20.0, departure_at=departure
    )
    tesla = planned_car(key="tesla", horizon_need_kwh=10.0, departure_at=departure)
    allowed = grid_allowed([egolf, tesla], slots, ["egolf", "tesla"])
    assert allowed == pytest.approx({"egolf": 20.0 - 14.72, "tesla": 10.0 - 3.28})


def test_r2_counts_surplus_below_the_minimum_power() -> None:
    # 1 kW Überschuss liegt unter min_w (3,45 kW) und zählt trotzdem: 0,25 kWh je Slot
    slots = _slots(8, pv_p25=[1500.0] * 8)
    car = planned_car(horizon_need_kwh=5.0, departure_at=at(START) + 2 * HOUR)
    assert grid_allowed([car], slots, ["tesla"]) == pytest.approx({"tesla": 3.0})


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
    # nach dem Horizont zählt die Leistung wie bei ``latest_start``: durch 1,1 geteilt, 15 min
    # Puffer vor der Abfahrt: 11,04 · (2 − 0,25) / 1,1 kWh
    assert horizon_need(30.0, 11040.0, departure, horizon_end) == pytest.approx(
        30 - 11.04 * (2 - 0.25) / 1.1
    )
    # Abfahrt im Horizont: der ganze Bedarf; weit dahinter: nichts
    assert horizon_need(30.0, 11040.0, horizon_end, horizon_end) == 30.0
    assert horizon_need(30.0, 11040.0, horizon_end - HOUR, horizon_end) == 30.0
    assert horizon_need(30.0, 11040.0, horizon_end + 10 * HOUR, horizon_end) == 0.0


def test_horizon_need_keeps_the_latest_start_margin() -> None:
    horizon_end = at("2026-10-17T12:00:00+00:00")
    departure = horizon_end + timedelta(minutes=30)
    # Abfahrt 30 min hinter dem Horizont, Bedarf 10 kWh, 11 kW: Wie bei ``latest_start`` gilt
    # die Ladedauer mal 1,1 und 15 min Puffer vor der Abfahrt. Nach dem Horizont bleiben
    # (0,5 h − 0,25 h) / 1,1 · 11 kW = 2,5 kWh (nicht 5,5 kWh wie ohne Zuschlag und Puffer).
    assert horizon_need(10.0, 11000.0, departure, horizon_end) == pytest.approx(10 - 2.5)
    # dieselbe Rechnung rückwärts: Mit dem Rest nach dem Horizont startet ``latest_start`` genau
    # am Horizontende
    after_kwh = 10.0 - horizon_need(10.0, 11000.0, departure, horizon_end)
    start = latest_start(departure, after_kwh, 11000.0)
    assert start is not None and abs(start - horizon_end) < timedelta(seconds=1)
    # Abfahrt weniger als der Puffer hinter dem Horizont: nach dem Horizont geht nichts mehr
    assert horizon_need(10.0, 11000.0, horizon_end + timedelta(minutes=10), horizon_end) == 10.0


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


def test_planner_departure_uses_date(cfg: Config) -> None:
    # Health-Abfahrt ist vorbei (Freitag 07:00); die Absicht nennt den Montag 23.11.
    now = at("2026-11-20T06:00:30+00:00")
    slots = scenario(
        prices=[20.0] * 8, pv=[0.0] * 8, load=[500.0] * 8, start="2026-11-20T06:00:00+00:00"
    ).slots
    dated = VehicleIntent(
        mode=ChargeMode.PLANNED, departure="07:00", departure_on=date(2026, 11, 23)
    )
    health = planned_health(vehicle_health("tesla", departure_at="2026-11-20T06:00:00+00:00"))
    [tesla] = planned_vehicles(
        cfg, health, Intent(version=3, vehicles={"tesla": dated}), now=now, slots=slots
    )
    assert tesla.departure_at == datetime.fromisoformat("2026-11-23T06:00:00+00:00")
    # Ohne Tag: nächster Morgen
    daily = dated.model_copy(update={"departure_on": None})
    [tesla] = planned_vehicles(
        cfg, health, Intent(version=3, vehicles={"tesla": daily}), now=now, slots=slots
    )
    assert tesla.departure_at == datetime.fromisoformat("2026-11-21T06:00:00+00:00")


# --- erwartete Autos (Plan 5b, Task 9, Präzisierung 10) ---------------------------------------

ARRIVAL = at("2026-10-16T18:45:00+00:00")  # 20:45 Ortszeit, Freitag: Viertelstunde 83


def _weekday(day: int, soc: float | None) -> WeekdayProfile:
    return WeekdayProfile(
        weekday=day,
        basis="weekday",
        days=8,
        trips=8,
        trip_share=1.0,
        departure_min=None,
        departure_p50_min=None,
        departure_p80_min=None,
        departure_confidence=None,
        arrival_p50_min=None,
        arrival_soc_p50=soc,
        consumption_p50_pp=None,
        consumption_p80_pp=None,
        target_soc_pct=None,
        target_clamped=False,
        target_confidence=None,
    )


def _profile(
    presence: list[float] | None = None,
    socs: dict[int, float | None] | None = None,
    saturday: list[float] | None = None,
) -> VehicleProfile:
    """Anwesenheit 0,3 bis 20:30 Ortszeit und 0,8 ab 20:45 an jedem Tag (oder ``presence``, 96
    Werte für jeden Tag, am Samstag ``saturday``); Ladestand bei Ankunft am Freitag 35 %, am
    Samstag 22 %."""
    day = presence or [0.3] * 83 + [0.8] * 13
    values: dict[int, float | None] = {day_: 50.0 for day_ in range(7)} | {4: 35.0, 5: 22.0}
    values.update(socs or {})
    return VehicleProfile(
        key="tesla",
        source="tessie",
        data_from=at("2026-08-01T00:00:00+00:00"),
        data_to=at("2026-10-15T00:00:00+00:00"),
        days=60,
        weekdays=[_weekday(index, values[index]) for index in range(7)],
        presence=[list(saturday if index == 5 and saturday else day) for index in range(7)],
    )


def _away(**changes: Any) -> dict[str, Any]:
    return vehicle_health("tesla", **{"plugged": False, **changes})


def _expected(
    cfg: Config,
    health_entry: dict[str, Any] | None = None,
    *,
    profile: VehicleProfile | None = None,
    intent: Intent | None = None,
    slots: list[SlotInput] | None = None,
    now: datetime = NOW,
    **kwargs: Any,
) -> list[Any]:
    return expected_vehicles(
        cfg,
        intent or _intent(),
        planned_health(health_entry or _away()),
        {"tesla": profile or _profile()},
        slots or _slots(),
        now,
        **kwargs,
    )


def test_unplugged_planned_car_is_expected_from_arrival(cfg: Config) -> None:
    [tesla] = _expected(cfg)
    assert (tesla.expected, tesla.arrival_at) == (True, ARRIVAL)
    assert (tesla.key, tesla.name, tesla.wallbox) == ("tesla", "Tesla Model 3", "twc")
    # Ladestand der Ankunft am Freitag, Ziel und Abfahrt aus der Absicht (80 %, 07:00 Ortszeit)
    assert (tesla.soc_pct, tesla.soc_assumed, tesla.target_soc_pct) == (35.0, False, 80.0)
    assert (tesla.limit_pct, tesla.departure_at) == (None, at(DEPARTURE))
    assert (tesla.efficiency, tesla.usable_kwh) == (0.9, 75.0)
    assert (tesla.min_w, tesla.max_w) == (5 * 230 * 3, 16 * 230 * 3)
    assert tesla.need_kwh == pytest.approx(45 / 100 * 75 / 0.9)
    assert tesla.horizon_need_kwh == tesla.need_kwh == tesla.grid_allowed_kwh


def test_plugged_cars_have_no_expected_flag(cfg: Config) -> None:
    [tesla] = planned_vehicles(
        cfg, planned_health(vehicle_health("tesla")), _intent(), now=NOW, slots=_slots()
    )
    assert (tesla.expected, tesla.arrival_at) == (False, None)


def test_plugged_car_is_not_expected(cfg: Config) -> None:
    assert _expected(cfg, vehicle_health("tesla", plugged=True)) == []


def test_car_already_among_the_plugged_inputs_is_not_expected(cfg: Config) -> None:
    assert _expected(cfg, plugged=[planned_car(key="tesla")]) == []


def test_unknown_plug_state_is_not_expected(cfg: Config) -> None:
    assert _expected(cfg, _away(plugged=None)) == []
    entry = _away()
    del entry["plugged"]
    assert _expected(cfg, entry) == []
    assert expected_vehicles(cfg, _intent(), None, {"tesla": _profile()}, _slots(), NOW) == []


def test_car_in_pv_mode_is_not_expected(cfg: Config) -> None:
    pv_intent = Intent(version=3, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PV)})
    assert _expected(cfg, intent=pv_intent) == []
    assert _expected(cfg, intent=Intent(version=3)) == []


def test_car_without_profile_is_not_expected(cfg: Config) -> None:
    assert expected_vehicles(cfg, _intent(), planned_health(_away()), {}, _slots(), NOW) == []


def test_no_arrival_soc_means_not_expected(cfg: Config) -> None:
    assert _expected(cfg, profile=_profile(socs={4: None})) == []


def test_no_default_wallbox_means_not_expected(cfg: Config) -> None:
    wallboxes = {key: box for key, box in cfg.wallboxes.items() if key != "twc"}
    assert _expected(cfg.model_copy(update={"wallboxes": wallboxes})) == []


def test_arrival_after_departure_means_not_expected(cfg: Config) -> None:
    # Abfahrt 20:30 Ortszeit, die Prognose steigt erst um 20:45 über 0,5
    early = _intent().model_copy(
        update={"vehicles": {"tesla": VehicleIntent(mode=ChargeMode.PLANNED, departure="20:30")}}
    )
    assert _expected(cfg, intent=early) == []
    # Abfahrt genau zur Viertelstunde der Ankunft: der Slot beginnt nicht vor der Abfahrt
    exact = early.model_copy(
        update={"vehicles": {"tesla": VehicleIntent(mode=ChargeMode.PLANNED, departure="20:45")}}
    )
    assert _expected(cfg, intent=exact) == []
    # eine Viertelstunde später reicht
    later = early.model_copy(
        update={"vehicles": {"tesla": VehicleIntent(mode=ChargeMode.PLANNED, departure="21:00")}}
    )
    [tesla] = _expected(cfg, intent=later)
    assert tesla.arrival_at == ARRIVAL and tesla.departure_at == at("2026-10-16T19:00:00+00:00")


def test_departure_must_be_after_the_current_slot(cfg: Config) -> None:
    # Jetzt 20:05, aktueller Slot bis 20:15: Abfahrt 20:15 liegt nicht danach
    today = _intent().model_copy(
        update={"vehicles": {"tesla": VehicleIntent(mode=ChargeMode.PLANNED, departure="20:15")}}
    )
    assert _expected(cfg, profile=_profile([0.9] * 96), intent=today) == []


def test_presence_below_half_never_expects_the_car(cfg: Config) -> None:
    assert _expected(cfg, profile=_profile([0.49] * 96)) == []
    # genau 0,5 genügt
    [tesla] = _expected(cfg, profile=_profile([0.5] * 96))
    assert tesla.arrival_at == at(START) + timedelta(minutes=15)


def test_the_current_slot_does_not_count_for_the_arrival(cfg: Config) -> None:
    # Hoch nur im aktuellen Slot (Viertelstunde 80) und nach der Abfahrt
    presence = [0.0] * 80 + [0.9] + [0.0] * 15
    assert _expected(cfg, profile=_profile(presence)) == []


def test_arrival_is_the_first_slot_over_half(cfg: Config) -> None:
    # 20:15 niedrig, 20:30 hoch, 20:45 wieder niedrig
    presence = [0.0] * 82 + [0.5] + [0.1] * 13
    [tesla] = _expected(cfg, profile=_profile(presence))
    assert tesla.arrival_at == at("2026-10-16T18:30:00+00:00")


def test_arrival_soc_follows_the_weekday_of_the_arrival(cfg: Config) -> None:
    # Ankunft nach Mitternacht: Samstag (22 %); Abfahrt Sonntag 07:00
    start = "2026-10-16T21:00:00+00:00"  # 23:00 Ortszeit, Freitag
    slots = scenario(prices=[20.0] * 48, pv=[0.0] * 48, load=[500.0] * 48, start=start).slots
    friday = [0.0] * 96
    saturday = [0.0] * 4 + [0.9] * 92  # ab 01:00 Ortszeit hoch
    intent = Intent(
        version=3,
        vehicles={"tesla": VehicleIntent(mode=ChargeMode.PLANNED, departure="07:00")},
    )
    [tesla] = _expected(
        cfg,
        profile=_profile(friday, saturday=saturday),
        intent=intent,
        slots=slots,
        now=at("2026-10-16T21:05:00+00:00"),
    )
    assert tesla.arrival_at == at("2026-10-16T23:00:00+00:00")  # Samstag 01:00 Ortszeit
    assert tesla.soc_pct == 22.0


def test_holiday_counts_as_weekday_six(cfg: Config) -> None:
    # Montag, 26. 10. 2026 ist Nationalfeiertag: Wochentag 6 (Ladestand 55 %) statt Montag (20 %)
    start = "2026-10-26T06:00:00+00:00"
    slots = scenario(prices=[20.0] * 48, pv=[0.0] * 48, load=[500.0] * 48, start=start).slots
    intent = Intent(
        version=3,
        vehicles={"tesla": VehicleIntent(mode=ChargeMode.PLANNED, departure="20:00")},
    )
    [tesla] = _expected(
        cfg,
        profile=_profile([0.9] * 96, socs={0: 20.0, 6: 55.0}),
        intent=intent,
        slots=slots,
        now=at("2026-10-26T06:05:00+00:00"),
    )
    assert tesla.soc_pct == 55.0


def test_expected_cars_follow_the_priority_and_skip_the_rest(cfg: Config) -> None:
    health = planned_health(_away(), vehicle_health("egolf", plugged=False))
    intent = Intent(
        version=3,
        priority=["egolf", "tesla"],
        vehicles={
            "tesla": VehicleIntent(mode=ChargeMode.PLANNED),
            "egolf": VehicleIntent(mode=ChargeMode.PLANNED),
        },
    )
    profiles = {"tesla": _profile(), "egolf": _profile()}
    cars = expected_vehicles(cfg, intent, health, profiles, _slots(), NOW)
    assert [car.key for car in cars] == ["egolf", "tesla"]
    # der e-Golf hat kein Profil
    cars = expected_vehicles(cfg, intent, health, {"tesla": _profile()}, _slots(), NOW)
    assert [car.key for car in cars] == ["tesla"]


def test_expected_car_departure_uses_the_date_of_the_intent(cfg: Config) -> None:
    # Ruling T9: Die Absicht nennt Montag 07:00 (Freitag jetzt); ohne Tag wäre es Samstag 07:00
    dated = VehicleIntent(
        mode=ChargeMode.PLANNED, departure="07:00", departure_on=date(2026, 10, 19)
    )
    [tesla] = _expected(cfg, intent=Intent(version=3, vehicles={"tesla": dated}))
    assert tesla.departure_at == at("2026-10-19T05:00:00+00:00")
    daily = dated.model_copy(update={"departure_on": None})
    [tesla] = _expected(cfg, intent=Intent(version=3, vehicles={"tesla": daily}))
    assert tesla.departure_at == at(DEPARTURE)


def _wallbox(key: str, occupant: str | None, assignment: str | None) -> dict[str, Any]:
    return {"id": key, "occupant": occupant, "name": None, "assignment": assignment}


def test_expected_car_needs_a_free_default_wallbox(cfg: Config) -> None:
    # Ruling F-4: Steht am Wall Connector (default_wallbox des Tesla) laut Health schon ein
    # anderes Auto oder ein Gast, kommt der Tesla nicht als erwartetes Auto in den Plan
    profiles = {"tesla": _profile()}

    def expected(health: dict[str, Any]) -> list[str]:
        cars = expected_vehicles(cfg, _intent(), health, profiles, _slots(), NOW)
        return [car.key for car in cars]

    # der e-Golf steckt am Wall Connector
    golf_there = vehicle_health("egolf", wallbox="twc")
    assert expected(planned_health(_away(), golf_there)) == []
    # ein Gast steht dort
    guest = planned_health(_away()) | {"wallboxes": [_wallbox("twc", "guest", "default")]}
    assert expected(guest) == []
    # unklare Zuordnung: dort steckt jemand, aber nicht der Tesla
    unclear = planned_health(_away()) | {"wallboxes": [_wallbox("twc", None, "unclear")]}
    assert expected(unclear) == []
    # frei: der e-Golf an der EVCS, der Wall Connector ohne Vorgang
    free = planned_health(_away(), vehicle_health("egolf")) | {
        "wallboxes": [_wallbox("twc", None, None), _wallbox("evcs", "egolf", "detected")]
    }
    assert expected(free) == ["tesla"]
    # ein abgestecktes Auto belegt nichts, auch wenn Health seine letzte Wallbox nennt
    golf_away = vehicle_health("egolf", wallbox="twc", plugged=False)
    assert expected(planned_health(_away(), golf_away)) == ["tesla"]


def test_expected_and_arrival_come_together() -> None:
    # Ruling T10: Ankunft nur bei einem erwarteten Auto und ein erwartetes Auto nur mit Ankunft;
    # so meinen charge_window, R2 und die Plausibilität (arrival_at) dasselbe wie expected
    with pytest.raises(ValidationError, match="Ankunft"):
        planned_car(arrival_at=at(START))
    with pytest.raises(ValidationError, match="Ankunft"):
        planned_car(expected=True)
    car = planned_car(expected=True, arrival_at=at(START))
    assert (car.expected, car.arrival_at) == (True, at(START))


def test_grid_allowed_counts_pv_from_arrival(cfg: Config) -> None:
    # 3 kW Überschuss (P25 3500 W, Last 500 W) nur vor der Ankunft (die ersten drei Slots)
    # zählt nicht: die Netzenergie bleibt der ganze Bedarf
    before = _slots(48, pv_p25=[3500.0] * 3 + [0.0] * 45)
    [tesla] = _expected(cfg, slots=before)
    assert tesla.grid_allowed_kwh == pytest.approx(tesla.horizon_need_kwh)
    # ab der Ankunft zählt er: acht Slots · 0,75 kWh
    after = _slots(48, pv_p25=[0.0] * 3 + [3500.0] * 8 + [0.0] * 37)
    [tesla] = _expected(cfg, slots=after)
    assert tesla.grid_allowed_kwh == pytest.approx(tesla.horizon_need_kwh - 6.0)


def test_grid_allowed_leaves_the_plugged_cars_their_surplus(cfg: Config) -> None:
    slots = _slots(48, pv_p25=[0.0] * 3 + [3500.0] * 8 + [0.0] * 37)
    plugged = planned_car(
        key="egolf", wallbox="evcs", horizon_need_kwh=4.5, departure_at=at(DEPARTURE)
    )
    [alone] = _expected(cfg, slots=slots)
    [shared] = _expected(cfg, slots=slots, plugged=[plugged])
    # der e-Golf nimmt 4,5 kWh der 6 kWh Überschuss vorweg
    assert alone.grid_allowed_kwh == pytest.approx(alone.horizon_need_kwh - 6.0)
    assert shared.grid_allowed_kwh == pytest.approx(shared.horizon_need_kwh - 1.5)


def test_plugged_allowance_is_unchanged_by_arrival_support() -> None:
    slots = _slots(8, pv_p25=[3500.0] * 8)
    car = planned_car(horizon_need_kwh=10.0, departure_at=at(START) + 2 * HOUR)
    assert grid_allowed([car], slots, ["tesla"]) == pytest.approx({"tesla": 4.0})
    late = car.model_copy(update={"expected": True, "arrival_at": at(START) + HOUR})
    assert grid_allowed([late], slots, ["tesla"]) == pytest.approx({"tesla": 7.0})


def test_cars_charging_drops_cars_below_the_on_threshold() -> None:
    powers = {"a": EV_ON_W, "b": EV_ON_W - 0.01, "c": 0.0, "d": 4140.0}
    assert cars_charging(powers) == {"a": EV_ON_W, "d": 4140.0}
