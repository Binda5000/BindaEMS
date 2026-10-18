"""Plan-Typen (Plan 3b, Präzisierung 2): Absicht, Slot, Plan."""

import pytest
from pydantic import ValidationError
from tests.plan_helpers import at, sample_plan

from bindaems.shared.plan import (
    MAX_INTENT_PLAN_SLOTS,
    BatteryIntent,
    BatteryIntentKind,
    IntentPlanSlot,
    Plan,
    PlanVehicle,
    Reason,
    intent_plan,
)

EIGENVERBRAUCH = BatteryIntentKind.EIGENVERBRAUCH
HALTEN = BatteryIntentKind.HALTEN
NETZLADEN = BatteryIntentKind.NETZLADEN
HOLD = {"intent": BatteryIntent(kind=HALTEN)}
NETZLADEN_3000 = {"intent": BatteryIntent(kind=NETZLADEN, grid_charge_w=3000.0)}
DAY = "2026-10-16T"


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


def _stored(created: str, slots: list[str], **kwargs: object) -> Plan:
    """Gespeicherter Plan (mit ``id``) für die Übergabe an den core."""
    return sample_plan(at(created).isoformat(), slots, **kwargs).model_copy(update={"id": 7})  # type: ignore[arg-type]


def test_intent_plan_keeps_the_binding_slots_only() -> None:
    plan = _stored(DAY + "10:01:00+00:00", ["10:00", "10:15", "10:30", "10:45", "11:00"])
    sent = intent_plan(plan, grid_charge_allowed=True)
    assert [s.start for s in sent.slots] == [
        at(DAY + "10:00:00+00:00"),
        at(DAY + "10:15:00+00:00"),
        at(DAY + "10:30:00+00:00"),
        at(DAY + "10:45:00+00:00"),
    ]
    assert (sent.id, sent.valid_until) == (7, at(DAY + "10:46:00+00:00"))
    assert sent.created_at == plan.created_at
    assert sent.grid_charge_allowed is True


def test_intent_plan_has_at_most_eight_slots() -> None:
    assert MAX_INTENT_PLAN_SLOTS == 8
    times = [f"{10 + i // 4:02d}:{i % 4 * 15:02d}" for i in range(12)]
    plan = _stored(DAY + "10:01:00+00:00", times).model_copy(
        update={"valid_until": at(DAY + "13:00:00+00:00")}
    )
    assert len(intent_plan(plan, grid_charge_allowed=True).slots) == MAX_INTENT_PLAN_SLOTS


def test_intent_plan_turns_grid_charge_into_self_consumption_when_switched_off() -> None:
    plan = _stored(DAY + "10:01:00+00:00", ["10:00", "10:15"], intents=[NETZLADEN_3000, HOLD])
    sent = intent_plan(plan, grid_charge_allowed=False)
    assert [s.intent.kind for s in sent.slots] == [EIGENVERBRAUCH, HALTEN]
    assert [s.intent.grid_charge_w for s in sent.slots] == [0.0, 0.0]
    assert sent.grid_charge_allowed is False
    kept = intent_plan(plan, grid_charge_allowed=True)
    assert [s.intent for s in kept.slots] == [NETZLADEN_3000["intent"], HOLD["intent"]]


def test_intent_plan_needs_a_stored_plan() -> None:
    with pytest.raises(ValueError):
        intent_plan(sample_plan(DAY + "10:01:00+00:00", ["10:00"]), grid_charge_allowed=True)


def test_intent_plan_carries_budget_price_and_threshold() -> None:
    change = {"budget_kw": 4.5, "import_ct": 31.5, "support_soc_pct": 62.0}
    plan = _stored(DAY + "10:01:00+00:00", ["10:00", "10:15"], intents=[change, {}])
    first, second = intent_plan(plan, grid_charge_allowed=True).slots
    assert (first.budget_kw, first.import_ct, first.support_soc_pct) == (4.5, 31.5, 62.0)
    assert (second.budget_kw, second.import_ct, second.support_soc_pct) == (None, 20.0, None)


def test_intent_plan_slot_defaults_and_ranges() -> None:
    slot = IntentPlanSlot(start=at(DAY + "10:00:00+00:00"), intent=BatteryIntent())
    assert (slot.budget_kw, slot.import_ct, slot.support_soc_pct) == (None, None, None)
    for bad in ({"budget_kw": -1.0}, {"support_soc_pct": 101.0}, {"support_soc_pct": -1.0}):
        with pytest.raises(ValidationError):
            IntentPlanSlot(start=at(DAY + "10:00:00+00:00"), intent=BatteryIntent(), **bad)


def test_intent_plan_validity_and_slot_lookup() -> None:
    sent = intent_plan(
        _stored(DAY + "10:01:00+00:00", ["10:00", "10:15", "10:30"]), grid_charge_allowed=True
    )
    slot = sent.slot_at(at(DAY + "10:20:00+00:00"))
    assert slot is not None
    assert slot.start == at(DAY + "10:15:00+00:00")
    assert sent.slot_at(at(DAY + "10:45:00+00:00")) is None
    assert sent.valid_at(at(DAY + "10:45:59+00:00"))
    assert not sent.valid_at(sent.valid_until)
    assert not sent.valid_at(at(DAY + "10:00:59+00:00"))


def test_stored_plan_without_threshold_loads() -> None:
    raw = sample_plan(DAY + "10:01:00+00:00", ["10:00"]).model_dump(mode="json")
    del raw["slots"][0]["support_soc_pct"]
    assert Plan.model_validate(raw).slots[0].support_soc_pct is None


def test_plan_slot_threshold_roundtrips() -> None:
    plan = sample_plan(DAY + "10:01:00+00:00", ["10:00"], intents=[{"support_soc_pct": 55.0}])
    assert Plan.model_validate_json(plan.model_dump_json()).slots[0].support_soc_pct == 55.0


def test_intent_plan_carries_vehicle_power() -> None:
    plan = _stored(
        DAY + "10:01:00+00:00",
        ["10:00", "10:15"],
        intents=[{"ev_w": {"tesla": 7012.6, "egolf": 0.4}}, {}],
    )
    first, second = intent_plan(plan, grid_charge_allowed=False).slots
    assert first.vehicles == {"tesla": 7013, "egolf": 0}
    assert second.vehicles == {}


def test_intent_plan_slot_vehicles_must_not_be_negative() -> None:
    with pytest.raises(ValidationError):
        IntentPlanSlot(
            start=at(DAY + "10:00:00+00:00"), intent=BatteryIntent(), vehicles={"tesla": -1}
        )


def test_old_plan_json_loads_without_vehicles() -> None:
    raw = sample_plan(DAY + "10:01:00+00:00", ["10:00"]).model_dump(mode="json")
    del raw["vehicles"]
    del raw["slots"][0]["ev_w"]
    plan = Plan.model_validate(raw)
    assert plan.vehicles == []
    assert plan.slots[0].ev_w == {}


def test_old_intent_plan_json_loads_without_vehicles() -> None:
    sent = intent_plan(_stored(DAY + "10:01:00+00:00", ["10:00"]), grid_charge_allowed=True)
    raw = sent.model_dump(mode="json")
    del raw["slots"][0]["vehicles"]
    assert type(sent).model_validate(raw).slots[0].vehicles == {}


def _vehicle(**changes: object) -> PlanVehicle:
    fields: dict[str, object] = {
        "key": "tesla",
        "name": "Tesla Model 3",
        "wallbox": "evcs",
        "target_soc_pct": 80,
        "departure_at": at(DAY + "05:00:00+00:00"),
        "soc_start_pct": 40.0,
        "soc_assumed": False,
        "limit_pct": None,
        "need_kwh": 25.0,
        "planned_kwh": 25.0,
        "shortfall_kwh": 0.0,
        "expected_soc_pct": 80.0,
        "finish_at": at(DAY + "03:30:00+00:00"),
        "reachable": True,
    }
    return PlanVehicle(**(fields | changes))  # type: ignore[arg-type]


def test_plan_vehicle_roundtrips_in_a_plan() -> None:
    plan = sample_plan(DAY + "10:01:00+00:00", ["10:00"], intents=[{"ev_w": {"tesla": 7000.0}}])
    plan = plan.model_copy(
        update={"vehicles": [_vehicle(), _vehicle(key="egolf", finish_at=None, limit_pct=70.0)]}
    )
    again = Plan.model_validate_json(plan.model_dump_json())
    assert again == plan
    assert again.vehicles[1].finish_at is None
    assert again.vehicles[1].limit_pct == 70.0
    assert again.slots[0].ev_w == {"tesla": 7000.0}


def test_plan_vehicle_rejects_unknown_fields_and_naive_times() -> None:
    with pytest.raises(ValidationError):
        _vehicle(extra=1)
    with pytest.raises(ValidationError):
        _vehicle(departure_at=at("2026-10-16T05:00:00"))


def test_new_reason_codes_and_trigger_are_accepted() -> None:
    for code in ("ZIEL_SOC_ABFAHRT", "AUTO_WARTET_AUF_PV", "ZIEL_SOC_UNERREICHBAR"):
        assert Reason(code=code, params={}).code == code  # type: ignore[arg-type]
    plan = sample_plan(DAY + "10:01:00+00:00", ["10:00"]).model_copy(update={"trigger": "vehicles"})
    assert Plan.model_validate_json(plan.model_dump_json()).trigger == "vehicles"
    with pytest.raises(ValidationError):
        Reason(code="GIBT_ES_NICHT", params={})  # type: ignore[arg-type]
