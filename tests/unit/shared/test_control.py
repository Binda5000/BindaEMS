import pytest
from pydantic import ValidationError

from bindaems.core.safety.types import de
from bindaems.shared.control import VICTRON, Intent, vehicle_actuator, wallbox_actuator


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
