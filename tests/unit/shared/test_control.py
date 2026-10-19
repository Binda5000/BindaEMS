from dataclasses import fields
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from tests.plan_helpers import sample_plan

from bindaems.core.safety.types import de
from bindaems.shared.control import (
    PERSISTENT_KINDS,
    THERMAL_PREFIX,
    VICTRON,
    ChargeMode,
    Command,
    CommandKind,
    GuestIntent,
    Intent,
    SupportIntent,
    SurplusOrder,
    SurplusPriority,
    VehicleIntent,
    next_departure,
    thermal_actuator,
    vehicle_actuator,
    wallbox_actuator,
)
from bindaems.shared.plan import intent_plan
from bindaems.shared.timeutil import LOCAL_TZ


def test_intent_defaults_and_validation() -> None:
    assert Intent(version=1) == Intent(version=1, control=False, actuators={})
    for bad in (
        {"version": 0},
        {"version": 1, "actuators": {"victron": "an"}},
        {"version": 1, "x": 1},
    ):
        with pytest.raises(ValidationError):
            Intent.model_validate(bad)


def test_actuator_ids() -> None:
    assert (VICTRON, wallbox_actuator("evcs"), vehicle_actuator("tesla")) == (
        "victron",
        "wallbox.evcs",
        "vehicle.tesla",
    )


def test_de_formats_german() -> None:
    assert (de(10.7), de(-300.0), de(2070.0), de(22770.0)) == ("10,7", "−300", "2070", "22770")


# Absicht für die Laderegelung (Plan 2c-1)


def test_intent_defaults_for_charging() -> None:
    intent = Intent(version=1)
    assert (intent.vehicles, intent.priority) == ({}, None)
    assert intent.surplus_priority == SurplusPriority(
        kind=SurplusOrder.BATTERY_UNTIL_SOC, soc_pct=30.0
    )
    assert VehicleIntent() == VehicleIntent(mode=ChargeMode.PV, boost_until=None, wallbox=None)


def test_charge_modes_and_surplus_orders_keep_their_api_names() -> None:
    assert [mode.value for mode in ChargeMode] == ["pv", "min_pv", "now", "off", "planned"]
    assert [order.value for order in SurplusOrder] == [
        "cars_first",
        "battery_first",
        "battery_until_soc",
    ]


def test_boost_until_needs_a_time_zone() -> None:
    with pytest.raises(ValidationError):
        VehicleIntent(boost_until=datetime(2026, 10, 12, 18, 0))
    aware = VehicleIntent(boost_until=datetime(2026, 10, 12, 18, 0, tzinfo=UTC))
    assert aware.boost_until == datetime(2026, 10, 12, 18, 0, tzinfo=UTC)


def test_charging_intent_from_json() -> None:
    intent = Intent.model_validate(
        {
            "version": 2,
            "vehicles": {
                "tesla": {
                    "mode": "min_pv",
                    "boost_until": "2026-10-12T18:00:00+02:00",
                    "wallbox": "twc",
                },
                "egolf": {"mode": "off"},
            },
            "priority": ["egolf", "tesla"],
            "surplus_priority": {"kind": "battery_first"},
        }
    )
    tesla = intent.vehicles["tesla"]
    assert (tesla.mode, tesla.wallbox) == (ChargeMode.MIN_PV, "twc")
    assert tesla.boost_until == datetime(2026, 10, 12, 16, 0, tzinfo=UTC)
    assert intent.vehicles["egolf"] == VehicleIntent(mode=ChargeMode.OFF)
    assert intent.priority == ["egolf", "tesla"]
    assert intent.surplus_priority == SurplusPriority(kind=SurplusOrder.BATTERY_FIRST, soc_pct=30.0)


@pytest.mark.parametrize(
    "bad",
    [
        {"vehicles": {"tesla": {"mode": "turbo"}}},
        {"vehicles": {"tesla": {"boost": True}}},
        {"surplus_priority": {"kind": "nie"}},
        {"surplus_priority": {"soc_pct": -1}},
        {"surplus_priority": {"soc_pct": 100.1}},
        {"surplus_priority": {"stufe": 1}},
        {"priority": "tesla"},
    ],
    ids=[
        "modus",
        "fremdfeld-fahrzeug",
        "vorrang",
        "soc-negativ",
        "soc-ueber-100",
        "stufe",
        "liste",
    ],
)
def test_invalid_charging_fields_are_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Intent.model_validate({"version": 1} | bad)


def test_command_wake_defaults_to_false() -> None:
    start = Command("vehicle.tesla", CommandKind.CHARGING, True)
    assert start.wake is False
    assert Command("vehicle.tesla", CommandKind.CHARGING, True, wake=True).wake is True


def test_command_refresh_is_the_last_field_and_defaults_to_false() -> None:
    # ``wake`` behält seinen Platz davor; neue Felder kommen nur hinten dazu.
    assert [field.name for field in fields(Command)][-2:] == ["wake", "refresh"]
    plain = Command("victron", CommandKind.GRID_SETPOINT, 2000.0)
    assert plain.refresh is False
    assert Command("victron", CommandKind.GRID_SETPOINT, 2000.0, refresh=True).refresh is True


# Modus des Gastes in der Absicht (Plan 2c-2a, Design 3.1)


def test_guest_charges_in_pv_by_default() -> None:
    assert Intent(version=1).guest == GuestIntent(mode=ChargeMode.PV)
    assert (
        Intent.model_validate({"version": 2, "guest": {"mode": "now"}}).guest.mode is ChargeMode.NOW
    )


def test_support_is_the_last_field_of_the_intent() -> None:
    assert list(Intent.model_fields)[-2:] == ["plan", "support"]


def test_guest_shows_its_mode_as_in_the_api() -> None:
    assert Intent(version=1).model_dump(mode="json")["guest"] == {"mode": "pv"}
    for mode in ChargeMode:
        intent = Intent.model_validate({"version": 1, "guest": {"mode": mode.value}})
        assert intent.model_dump(mode="json")["guest"] == {"mode": mode.value}


@pytest.mark.parametrize(
    "bad",
    [{"mode": "turbo"}, {"boost_until": "2026-10-12T18:00:00+02:00"}, {"wallbox": "evcs"}],
    ids=["modus", "boost", "wallbox"],
)
def test_guest_has_only_a_mode(bad: dict[str, object]) -> None:
    # kein Boost und keine Übersteuerung der Wallbox für einen Gast
    with pytest.raises(ValidationError):
        Intent.model_validate({"version": 1, "guest": bad})


def test_intent_defaults_have_no_plan_and_allow_support() -> None:
    intent = Intent(version=1)
    assert intent.plan is None
    assert intent.support == SupportIntent(allowed=True, soc_pct=None)
    dumped = intent.model_dump(mode="json")
    assert dumped["plan"] is None
    assert dumped["support"] == {"allowed": True, "soc_pct": None}


def test_stored_intent_without_plan_and_support_loads_with_defaults() -> None:
    intent = Intent.model_validate({"version": 3, "control": True})
    assert (intent.plan, intent.support) == (None, SupportIntent())


def test_intent_round_trips_with_a_plan() -> None:
    plan = sample_plan("2026-10-16T10:01:00+00:00", ["10:00", "10:15"]).model_copy(update={"id": 4})
    intent = Intent(
        version=2,
        plan=intent_plan(plan, grid_charge_allowed=True),
        support=SupportIntent(allowed=False, soc_pct=80.0),
    )
    assert Intent.model_validate(intent.model_dump(mode="json")) == intent
    assert Intent.model_validate_json(intent.model_dump_json()) == intent


@pytest.mark.parametrize("soc", [-1, 100.5, 101])
def test_support_threshold_out_of_range_is_invalid(soc: float) -> None:
    with pytest.raises(ValidationError):
        SupportIntent(soc_pct=soc)
    assert SupportIntent(soc_pct=0).soc_pct == 0
    assert SupportIntent(soc_pct=100).soc_pct == 100


def test_support_has_only_allowed_and_threshold() -> None:
    with pytest.raises(ValidationError):
        Intent.model_validate({"version": 1, "support": {"allowed": True, "x": 1}})


def vienna(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """Zeitpunkt in Wiener Zeit (UTC-bewusst, wie ``next_departure`` ihn liefert)."""
    return datetime(year, month, day, hour, minute, tzinfo=LOCAL_TZ).astimezone(UTC)


def test_vehicle_intent_defaults_and_old_files_load() -> None:
    v = VehicleIntent.model_validate({"mode": "pv"})
    assert (v.target_soc_pct, v.departure) == (80, "07:00")
    assert VehicleIntent(mode=ChargeMode.PLANNED).mode == "planned"
    assert ChargeMode.PLANNED == "planned"


@pytest.mark.parametrize("bad", [19, 101, 80.5, "x"])
def test_target_soc_must_be_a_whole_number_from_20_to_100(bad: object) -> None:
    with pytest.raises(ValidationError):
        VehicleIntent.model_validate({"target_soc_pct": bad})


@pytest.mark.parametrize("target", [20, 100])
def test_target_soc_bounds_are_valid(target: int) -> None:
    assert VehicleIntent(target_soc_pct=target).target_soc_pct == target


@pytest.mark.parametrize("bad", ["7:00", "24:00", "07:60", "", "07:00:00", "0700"])
def test_departure_must_be_hh_mm(bad: str) -> None:
    with pytest.raises(ValidationError):
        VehicleIntent(departure=bad)


@pytest.mark.parametrize("good", ["00:00", "07:00", "23:59"])
def test_departure_accepts_valid_times(good: str) -> None:
    assert VehicleIntent(departure=good).departure == good


def test_next_departure_is_today_before_the_time() -> None:
    now = vienna(2026, 10, 18, 6, 59)
    assert next_departure("07:00", now) == vienna(2026, 10, 18, 7, 0)


def test_next_departure_rolls_over_after_passing() -> None:
    now = vienna(2026, 10, 18, 7, 0)
    assert next_departure("07:00", now) == vienna(2026, 10, 19, 7, 0)
    assert next_departure("07:00", vienna(2026, 10, 18, 7, 1)) == vienna(2026, 10, 19, 7, 0)
    assert next_departure("00:00", vienna(2026, 10, 18, 23, 59)) == vienna(2026, 10, 19, 0, 0)


def test_next_departure_uses_the_local_date_of_now() -> None:
    # 23:30 UTC ist in Wien (Sommerzeit) schon der nächste Tag, 01:30
    now = datetime(2026, 7, 1, 23, 30, tzinfo=UTC)
    assert next_departure("07:00", now) == vienna(2026, 7, 2, 7, 0)


def test_next_departure_returns_utc() -> None:
    result = next_departure("07:00", vienna(2026, 10, 18, 6, 0))
    assert result.utcoffset() == timedelta(0)
    assert result.tzinfo is UTC


def test_next_departure_on_dst_days() -> None:
    # Sommerzeit: 02:30 gibt es am 28. 3. 2027 nicht, es gilt die erste gültige Zeit danach
    assert next_departure("02:30", vienna(2027, 3, 28, 1, 0)) == vienna(2027, 3, 28, 3, 0)
    # Winterzeit: 02:30 gibt es zweimal, es gilt das erste Vorkommen (Sommerzeit, +02:00)
    first = next_departure("02:30", vienna(2026, 10, 25, 1, 0))
    assert first == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    assert first.astimezone(LOCAL_TZ).fold == 0
    assert first.astimezone(LOCAL_TZ).utcoffset() == timedelta(hours=2)


def test_next_departure_after_the_first_winter_time_occurrence_is_the_next_day() -> None:
    now = datetime(2026, 10, 25, 0, 45, tzinfo=UTC)  # 02:45 Sommerzeit, nach dem ersten 02:30
    assert next_departure("02:30", now) == vienna(2026, 10, 26, 2, 30)


def test_thermal_actuator() -> None:
    assert thermal_actuator("klima") == "thermal.klima"
    assert thermal_actuator("klima").startswith(THERMAL_PREFIX)


def test_setpoint_offset_is_not_persistent() -> None:
    assert CommandKind.SETPOINT_OFFSET == "setpoint_offset"
    assert CommandKind.SETPOINT_OFFSET not in PERSISTENT_KINDS
