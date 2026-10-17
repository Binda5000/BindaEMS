"""Tests der Planspeicherung und Aufbewahrung (Plan 3b, Task 8)."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine
from tests.plan_helpers import at, sample_inputs, sample_plan

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
