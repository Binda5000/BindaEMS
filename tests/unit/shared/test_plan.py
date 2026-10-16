"""Plan-Typen (Plan 3b, Präzisierung 2): Absicht, Slot, Plan."""

import pytest
from pydantic import ValidationError
from tests.plan_helpers import at, sample_plan

from bindaems.shared.plan import BatteryIntent, BatteryIntentKind, Plan


def test_netzladen_needs_power_and_others_none() -> None:
    assert BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2300).grid_charge_w == 2300
    with pytest.raises(ValidationError):
        BatteryIntent(kind=BatteryIntentKind.NETZLADEN)
    with pytest.raises(ValidationError):
        BatteryIntent(kind=BatteryIntentKind.HALTEN, grid_charge_w=100)


def test_plan_slot_lookup_and_validity() -> None:
    plan = sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00", "10:15", "10:30"])
    assert plan.valid_until == at("2026-10-16T10:46:00+00:00")
    slot = plan.slot_at(at("2026-10-16T10:20:00+00:00"))
    assert slot is not None
    assert slot.start == at("2026-10-16T10:15:00+00:00")
    assert plan.slot_at(at("2026-10-16T10:45:00+00:00")) is None
    assert plan.horizon_end == at("2026-10-16T10:45:00+00:00")
    assert plan.valid_at(at("2026-10-16T10:45:59+00:00"))
    assert not plan.valid_at(plan.valid_until)
    assert not plan.valid_at(at("2026-10-16T10:00:59+00:00"))


def test_plan_roundtrips_as_json() -> None:
    plan = sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00"])
    assert Plan.model_validate_json(plan.model_dump_json()) == plan


def test_plan_needs_at_least_one_slot() -> None:
    plan = sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00"])
    with pytest.raises(ValidationError):
        Plan.model_validate({**plan.model_dump(), "slots": []})
