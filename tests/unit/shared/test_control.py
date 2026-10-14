from dataclasses import fields
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from bindaems.core.safety.types import de
from bindaems.shared.control import (
    VICTRON,
    ChargeMode,
    Command,
    CommandKind,
    GuestIntent,
    Intent,
    SurplusOrder,
    SurplusPriority,
    VehicleIntent,
    vehicle_actuator,
    wallbox_actuator,
)


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
    assert [mode.value for mode in ChargeMode] == ["pv", "min_pv", "now", "off"]
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


def test_command_wake_is_the_last_field_and_defaults_to_false() -> None:
    assert fields(Command)[-1].name == "wake"
    start = Command("vehicle.tesla", CommandKind.CHARGING, True)
    assert start.wake is False
    assert Command("vehicle.tesla", CommandKind.CHARGING, True, wake=True).wake is True


# Modus des Gastes in der Absicht (Plan 2c-2a, Design 3.1)


def test_guest_charges_in_pv_by_default() -> None:
    assert Intent(version=1).guest == GuestIntent(mode=ChargeMode.PV)
    assert (
        Intent.model_validate({"version": 2, "guest": {"mode": "now"}}).guest.mode is ChargeMode.NOW
    )


def test_guest_is_the_last_field_of_the_intent() -> None:
    assert list(Intent.model_fields)[-1] == "guest"


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
