"""Gültiger Slot des Plans und Health „plan“ (Plan 3c, Präzisierung 5)."""

from datetime import datetime, timedelta

from tests.helpers import T0

from bindaems.core.control.plan_exec import ActiveSlot, active_slot, plan_health
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind, IntentPlan, IntentPlanSlot

HALTEN = BatteryIntentKind.HALTEN
CHARGE = BatteryIntentKind.NETZLADEN


def at(hhmm: str) -> datetime:
    """Der Zeitpunkt ``hhmm`` (UTC) am Tag von ``T0``."""
    hour, minute = hhmm.split(":")
    return T0.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)


PLAN_1001 = IntentPlan(
    id=1001,
    created_at=at("10:01"),
    valid_until=at("10:46"),
    grid_charge_allowed=True,
    slots=[
        IntentPlanSlot(start=at("10:00"), intent=BatteryIntent()),
        IntentPlanSlot(
            start=at("10:15"),
            intent=BatteryIntent(kind=HALTEN),
            budget_kw=7.5,
            import_ct=21.5,
            support_soc_pct=35.0,
        ),
        IntentPlanSlot(start=at("10:30"), intent=BatteryIntent(kind=CHARGE, grid_charge_w=2000.0)),
        IntentPlanSlot(start=at("10:45"), intent=BatteryIntent()),
    ],
)


def test_active_slot_inside_validity() -> None:
    slot = active_slot(PLAN_1001, at("10:20"))
    assert slot == ActiveSlot(
        plan_id=1001,
        start=at("10:15"),
        intent=BatteryIntent(kind=HALTEN),
        budget_kw=7.5,
        import_ct=21.5,
        support_soc_pct=35.0,
    )


def test_active_slot_follows_the_quarter_hours() -> None:
    assert active_slot(PLAN_1001, at("10:01")).start == at("10:00")  # type: ignore[union-attr]
    assert active_slot(PLAN_1001, at("10:29")).start == at("10:15")  # type: ignore[union-attr]
    slot = active_slot(PLAN_1001, at("10:30"))
    assert slot is not None and slot.intent.grid_charge_w == 2000.0
    assert slot.budget_kw is None and slot.import_ct is None and slot.support_soc_pct is None


def test_no_slot_outside_validity() -> None:
    assert active_slot(PLAN_1001, at("10:46")) is None  # Review Focus 3: valid_until ist offen
    assert active_slot(PLAN_1001, at("09:59")) is None
    assert active_slot(PLAN_1001, at("10:00")) is None  # vor created_at (Uhrabweichung)
    assert active_slot(None, at("10:20")) is None


def test_last_instant_before_valid_until_is_still_valid() -> None:
    last = at("10:46") - timedelta(microseconds=1)
    slot = active_slot(PLAN_1001, last)
    assert slot is not None and slot.start == at("10:45")


def test_a_plan_without_the_slot_has_no_active_slot() -> None:
    gap = PLAN_1001.model_copy(update={"slots": PLAN_1001.slots[:1]})
    assert active_slot(gap, at("10:20")) is None


def test_plan_health_states() -> None:
    assert plan_health(None, None, at("10:20"))["state"] == "none"
    valid = plan_health(PLAN_1001, active_slot(PLAN_1001, at("10:20")), at("10:20"))
    assert valid["state"] == "valid"
    assert plan_health(PLAN_1001, None, at("10:50"))["state"] == "expired"
    # Uhrabweichung: Der Plan liegt in der Zukunft der Wanduhr, wirkt nicht und heißt auch „expired“
    assert plan_health(PLAN_1001, None, at("09:59"))["state"] == "expired"


def test_plan_health_of_a_valid_plan() -> None:
    health = plan_health(PLAN_1001, active_slot(PLAN_1001, at("10:20")), at("10:20"))
    assert health == {
        "id": 1001,
        "state": "valid",
        "valid_until": "2026-10-08T10:46:00+00:00",
        "slot": "2026-10-08T10:15:00+00:00",
        "intent": "HALTEN",
        "grid_charge_w": 0.0,
        "budget_kw": 7.5,
        "support_soc_pct": 35.0,
        "price_ct": 21.5,
    }


def test_plan_health_without_a_slot_has_nulls() -> None:
    nothing = dict.fromkeys(
        ("slot", "intent", "grid_charge_w", "budget_kw", "support_soc_pct", "price_ct")
    )
    assert plan_health(None, None, at("10:20")) == {
        "id": None,
        "state": "none",
        "valid_until": None,
        **nothing,
    }
    assert plan_health(PLAN_1001, None, at("10:50")) == {
        "id": 1001,
        "state": "expired",
        "valid_until": "2026-10-08T10:46:00+00:00",
        **nothing,
    }


def test_plan_health_slot_without_numbers_has_nulls() -> None:
    health = plan_health(PLAN_1001, active_slot(PLAN_1001, at("10:05")), at("10:05"))
    assert (health["intent"], health["budget_kw"], health["price_ct"]) == (
        "EIGENVERBRAUCH",
        None,
        None,
    )


def test_plan_health_of_a_valid_plan_without_a_slot() -> None:
    # Ruht die Regelung, bindet nichts; der Stand folgt dann der Gültigkeit allein.
    health = plan_health(PLAN_1001, None, at("10:20"))
    assert (health["state"], health["valid_until"]) == ("valid", "2026-10-08T10:46:00+00:00")
    assert (health["slot"], health["intent"], health["price_ct"]) == (None, None, None)


def test_active_slot_carries_the_power_of_each_vehicle() -> None:  # Plan 4b, Präzisierung 7
    slots = [slot.model_copy(update={"vehicles": {"tesla": 6900}}) for slot in PLAN_1001.slots]
    plan = PLAN_1001.model_copy(update={"slots": slots})
    found = active_slot(plan, at("10:20"))
    assert found is not None and found.vehicles == {"tesla": 6900}
    # Ein Slot ohne Autos nennt keins; ohne Angabe ist die Liste leer.
    assert active_slot(PLAN_1001, at("10:20")).vehicles == {}  # type: ignore[union-attr]
    assert ActiveSlot(1, at("10:00"), BatteryIntent(), None, None, None).vehicles == {}
