"""Tests der Planspeicherung und Aufbewahrung (Plan 3b, Task 8)."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, insert, select, text
from structlog.testing import capture_logs
from tests.plan_helpers import at, sample_inputs, sample_plan

from bindaems.app.db.schema import plan_inputs_table, plan_table
from bindaems.app.planning.store import PlanStore
from bindaems.shared.plan import Plan


@pytest.fixture
def store(engine: Engine) -> PlanStore:
    return PlanStore(engine)


def test_sample_inputs_are_not_trivial() -> None:
    inputs = sample_inputs()
    assert any(slot.origin == "estimated" for slot in inputs.slots)
    assert any(slot.feed_in_ct is None for slot in inputs.slots)
    assert inputs.peak_months and inputs.month_peak_kw is not None
    assert any(slot.prev_battery_w is not None for slot in inputs.slots)
    assert any(slot.prev_ev_w is not None for slot in inputs.slots)
    assert all(slot.pv_p25_w is not None for slot in inputs.slots) and inputs.vehicles
    assert inputs.created_at.utcoffset() is not None


def test_save_and_latest_roundtrip(store: PlanStore) -> None:
    first = store.save(
        sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00"]), sample_inputs()
    )
    second = store.save(
        sample_plan(created="2026-10-16T10:16:00+00:00", slots=["10:15"]), sample_inputs()
    )
    assert first.id is not None and second.id == first.id + 1 and store.latest() == second
    assert store.inputs_of(first.id) == sample_inputs()


def test_latest_without_plan_is_none(store: PlanStore) -> None:
    assert store.latest() is None


def test_inputs_of_unknown_plan_is_none(store: PlanStore) -> None:
    assert store.inputs_of(99) is None


def test_cleanup_trims_after_7_days_and_deletes_after_30(store: PlanStore) -> None:
    old = store.save(
        sample_plan(created="2026-09-10T10:01:00+00:00", slots=["10:00"]), sample_inputs()
    )
    # gültig bis 10:46: der Slot 10:45 läuft noch, der Slot 11:00 liegt danach
    week = store.save(
        sample_plan(
            created="2026-10-08T10:01:00+00:00",
            slots=["10:00", "10:15", "10:30", "10:45", "11:00"],
        ),
        sample_inputs(),
    )
    assert old.id is not None and week.id is not None
    assert week.valid_until == at("2026-10-08T10:46:00+00:00")
    assert store.cleanup(at("2026-10-16T10:00:00+00:00")) == (1, 1)
    trimmed = store.latest()
    assert trimmed is not None
    assert trimmed.id == week.id and trimmed.trimmed
    assert [s.start.strftime("%H:%M") for s in trimmed.slots] == [
        "10:00",
        "10:15",
        "10:30",
        "10:45",
    ]
    assert store.inputs_of(week.id) is None and store.inputs_of(old.id) is None
    assert store.cleanup(at("2026-10-16T10:00:00+00:00")) == (0, 0)


def _insert_broken(engine: Engine, created: str, data: object) -> int:
    """Eine Plan-Zeile, deren ``data`` kein gültiger ``Plan`` ist (etwa nach einer Änderung des
    Formats); liefert die ``id``."""
    with engine.begin() as conn:
        result = conn.execute(
            insert(plan_table).values(
                created_at=at(created),
                trigger="start",
                source="optimizer",
                solver_status="optimal",
                fallback_reason=None,
                valid_until=at(created),
                settings_version=1,
                expected_cost_ct=0.0,
                trimmed=False,
                data=data,
            )
        )
        key = result.inserted_primary_key
        assert key is not None
        conn.execute(insert(plan_inputs_table).values(plan_id=key[0], data={}))
        return int(key[0])


@pytest.mark.parametrize("data", [{"bogus": 1}, ["kein", "Plan"], None])
def test_cleanup_skips_an_unreadable_row(store: PlanStore, engine: Engine, data: object) -> None:
    # Eine Zeile, die sich nicht lesen lässt (10 Tage alt, würde gekürzt), und ein Plan nach
    # 30 Tagen: Das Aufräumen löscht den alten Plan trotzdem und lässt die Zeile stehen.
    old = store.save(
        sample_plan(created="2026-09-10T10:01:00+00:00", slots=["10:00"]), sample_inputs()
    )
    broken_id = _insert_broken(engine, "2026-10-06T10:01:00+00:00", data)
    assert old.id is not None
    with capture_logs() as logs:
        assert store.cleanup(at("2026-10-16T10:00:00+00:00")) == (1, 0)
    unreadable = [entry for entry in logs if entry.get("code") == "plan_row_unreadable"]
    assert len(unreadable) == 1 and unreadable[0]["plan_id"] == broken_id
    assert unreadable[0]["log_level"] == "warning"
    with engine.connect() as conn:
        assert list(conn.execute(select(plan_table.c.id)).scalars()) == [broken_id]
        assert conn.execute(text("select count(*) from plan_inputs")).scalar_one() == 1


def test_latest_skips_an_unreadable_row(store: PlanStore, engine: Engine) -> None:
    good = store.save(
        sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00"]), sample_inputs()
    )
    _insert_broken(engine, "2026-10-16T10:16:00+00:00", {"bogus": 1})  # neuer als der gute
    with capture_logs() as logs:
        assert store.latest() == good
    assert [entry["code"] for entry in logs] == ["plan_row_unreadable"]


def test_plans_between_skips_an_unreadable_row(store: PlanStore, engine: Engine) -> None:
    good = store.save(
        sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00"]), sample_inputs()
    )
    _insert_broken(engine, "2026-10-16T10:16:00+00:00", {"bogus": 1})
    found = store.plans_between(at("2026-10-16T10:00:00+00:00"), at("2026-10-16T11:00:00+00:00"))
    assert found == [good]


def test_cleanup_keeps_recent_plans_untouched(store: PlanStore) -> None:
    recent = store.save(
        sample_plan(created="2026-10-15T10:01:00+00:00", slots=["10:00", "10:15"]), sample_inputs()
    )
    assert recent.id is not None
    assert store.cleanup(at("2026-10-16T10:00:00+00:00")) == (0, 0)
    assert store.latest() == recent
    assert store.inputs_of(recent.id) == sample_inputs()


def test_plans_between_reads_the_window(store: PlanStore) -> None:
    def save(created: str) -> Plan:
        return store.save(sample_plan(created=created, slots=["10:00"]), sample_inputs())

    before = save("2026-10-16T09:14:00+00:00")  # gültig bis 09:59: vor dem Fenster
    inside = [save("2026-10-16T09:16:00+00:00"), save("2026-10-16T10:01:00+00:00")]
    save("2026-10-16T11:00:00+00:00")  # nicht vor dem Ende des Fensters
    found = store.plans_between(at("2026-10-16T10:00:00+00:00"), at("2026-10-16T11:00:00+00:00"))
    assert found == inside and before not in found


def test_plans_between_orders_by_creation_and_reads_trimmed_plans(store: PlanStore) -> None:
    late = store.save(
        sample_plan(created="2026-10-16T10:16:00+00:00", slots=["10:15"]), sample_inputs()
    )
    early = store.save(
        sample_plan(created="2026-10-08T10:01:00+00:00", slots=["10:00", "11:00"]), sample_inputs()
    )
    store.cleanup(at("2026-10-16T10:30:00+00:00"))
    found = store.plans_between(at("2026-10-08T10:00:00+00:00"), at("2026-10-17T00:00:00+00:00"))
    assert [plan.id for plan in found] == [early.id, late.id]
    assert found[0].trimmed and [s.start.strftime("%H:%M") for s in found[0].slots] == ["10:00"]
