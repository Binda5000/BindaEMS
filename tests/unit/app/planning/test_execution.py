"""Tests des Stands der Ausführung (Plan 3c, Task 11, Präzisierung 12)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from tests.plan_helpers import sample_plan

from bindaems.app.planning.execution import (
    EXECUTION_TEXTS,
    core_has_plan,
    execution_state,
    execution_text,
)
from bindaems.shared.plan import Plan

PLAN_ID = 7
CREATED_AT = "2026-10-16T10:01:00+00:00"


def stored_plan() -> Plan:
    plan = sample_plan("2026-10-16T10:01:00+00:00", ["10:00", "10:15"])
    return plan.model_copy(update={"id": PLAN_ID})


def health(
    *,
    plan_id: int | None = PLAN_ID,
    grid_charge: bool = False,
    control: bool = True,
    mode: str = "CONTROL",
    locked: tuple[str, ...] = (),
    effective: str = "live",
    plan_state: str = "valid",
    created_at: str | None = CREATED_AT,
) -> dict[str, Any]:
    """Health des core, in der der Plan der Absicht ``plan_id`` heißt; ``plan_state`` ist sein
    Stand beim core. ``created_at`` ``None`` lässt das Feld weg (ein älterer core)."""
    held: dict[str, Any] | None = None
    if plan_id is not None:
        held = {"id": plan_id, "grid_charge_allowed": grid_charge, "valid_until": "…", "slots": []}
        if created_at is not None:
            held["created_at"] = created_at
    return {
        "mode": mode,
        "intent": {"version": 3, "control": control, "plan": held},
        "actuators": [
            {"id": "wallbox.evcs", "effective": "live", "locked": []},
            {"id": "victron", "effective": effective, "locked": list(locked)},
        ],
        "plan": {"id": plan_id, "state": plan_state if plan_id is not None else "none"},
    }


# Jeder Fall ändert genau eine Stelle des Standes ``live``.
CASES: dict[str, Callable[[], dict[str, Any]]] = {
    "no plan": lambda: {"plan": None},
    "expired": lambda: {"valid": False},
    "disconnected": lambda: {"connected": False},
    "no health": lambda: {"health": None},
    "other id": lambda: {"health": health(plan_id=PLAN_ID + 1)},
    "no plan in core": lambda: {"health": health(plan_id=None)},
    "grid charge differs": lambda: {"health": health(grid_charge=True)},
    "control off": lambda: {"health": health(control=False)},
    "observe": lambda: {"health": health(mode="OBSERVE")},
    "safe": lambda: {"health": health(mode="SAFE")},
    "expired in core": lambda: {"health": health(plan_state="expired")},
    "core plan state unknown": lambda: {"health": {**health(), "plan": {}}},
    "victron locked": lambda: {"health": health(locked=("foreign",), effective="dry_run")},
    "victron dry": lambda: {"health": health(effective="dry_run")},
    "victron live": lambda: {},
    "degraded live": lambda: {"health": health(mode="DEGRADED")},
}


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("no plan", "none"),
        ("expired", "none"),
        ("disconnected", "unreachable"),
        ("no health", "pending"),
        ("other id", "pending"),
        ("no plan in core", "pending"),
        ("grid charge differs", "pending"),
        ("control off", "off"),
        ("observe", "idle"),
        ("safe", "idle"),
        ("expired in core", "pending"),
        ("core plan state unknown", "pending"),
        ("victron locked", "locked"),
        ("victron dry", "dry_run"),
        ("victron live", "live"),
        ("degraded live", "live"),
    ],
)
def test_execution_state(case: str, expected: str) -> None:
    arguments: dict[str, Any] = {
        "plan": stored_plan(),
        "valid": True,
        "connected": True,
        "health": health(),
        "grid_charge_allowed": False,
        **CASES[case](),
    }
    assert execution_state(**arguments) == expected


def test_execution_state_first_matching_wins() -> None:
    # Getrennt schlägt jeden Stand der Health; ohne Plan schlägt „none“ die Trennung.
    assert (
        execution_state(
            stored_plan(),
            valid=True,
            connected=False,
            health=health(control=False),
            grid_charge_allowed=False,
        )
        == "unreachable"
    )
    assert (
        execution_state(None, valid=False, connected=False, health=None, grid_charge_allowed=False)
        == "none"
    )
    # Regelbetrieb aus schlägt den Betriebszustand und die Sperre.
    assert (
        execution_state(
            stored_plan(),
            valid=True,
            connected=True,
            health=health(control=False, mode="SAFE", locked=("x",)),
            grid_charge_allowed=False,
        )
        == "off"
    )


@pytest.mark.parametrize(
    ("held", "expected"),
    [
        pytest.param(health(plan_id=None, control=False), "off", id="aus ohne Plan"),
        pytest.param(health(plan_id=PLAN_ID + 1, control=False), "off", id="aus, anderer Plan"),
        pytest.param(health(plan_id=None, mode="OBSERVE"), "idle", id="observe ohne Plan"),
        pytest.param(health(grid_charge=True, mode="SAFE"), "idle", id="safe, anderer Schalter"),
        pytest.param({"mode": "CONTROL", "intent": None}, "off", id="core ohne Absicht"),
    ],
)
def test_off_and_idle_come_before_pending(held: dict[str, Any], expected: str) -> None:
    # Gesamtreview I1: Der Sender sendet nur, wenn der core mit der Absicht regelt; ein core mit
    # Regelbetrieb aus sagt das, statt „noch nicht beim core“
    state = execution_state(
        stored_plan(), valid=True, connected=True, health=held, grid_charge_allowed=False
    )
    assert state == expected


def test_execution_state_compares_the_grid_charge_switch_of_the_app() -> None:
    held = health(grid_charge=True)
    arguments = {"valid": True, "connected": True, "health": held}
    assert execution_state(stored_plan(), grid_charge_allowed=True, **arguments) == "live"
    assert execution_state(stored_plan(), grid_charge_allowed=False, **arguments) == "pending"


def test_execution_state_without_victron_is_not_live() -> None:
    held = health()
    held["actuators"] = []
    state = execution_state(
        stored_plan(), valid=True, connected=True, health=held, grid_charge_allowed=False
    )
    assert state == "dry_run"


def test_core_has_plan_is_what_the_sender_compares() -> None:
    plan = stored_plan()
    assert core_has_plan(health(), plan, grid_charge_allowed=False)
    assert not core_has_plan(health(), plan, grid_charge_allowed=True)
    assert not core_has_plan(health(plan_id=PLAN_ID + 1), plan, grid_charge_allowed=False)
    assert not core_has_plan(health(plan_id=None), plan, grid_charge_allowed=False)
    assert not core_has_plan(None, plan, grid_charge_allowed=False)
    assert not core_has_plan({"intent": None}, plan, grid_charge_allowed=False)


def test_same_id_other_created_at_is_not_the_plan() -> None:
    # Nach einer Wiederherstellung der Datenbank kann eine id wiederkehren
    other = health(created_at="2026-10-16T09:01:00+00:00")
    assert not core_has_plan(other, stored_plan(), grid_charge_allowed=False)


@pytest.mark.parametrize("written", ["2026-10-16T10:01:00Z", "2026-10-16T12:01:00+02:00"])
def test_created_at_is_compared_as_a_moment(written: str) -> None:
    held = health(created_at=written)
    assert core_has_plan(held, stored_plan(), grid_charge_allowed=False)


def test_health_without_created_at_compares_id_and_grid_charge_only() -> None:
    # Ein älterer core meldet kein created_at; während der Umstellung darf der Plan nicht endlos
    # neu hinausgehen
    older = health(created_at=None)
    assert core_has_plan(older, stored_plan(), grid_charge_allowed=False)
    assert not core_has_plan(older, stored_plan(), grid_charge_allowed=True)


@pytest.mark.parametrize("written", ["gestern", 5, "2026-10-16T10:01:00"])
def test_unreadable_created_at_is_not_the_plan(written: Any) -> None:
    assert not core_has_plan(health(created_at=written), stored_plan(), grid_charge_allowed=False)


def test_execution_texts() -> None:
    held = health()
    assert execution_text("unreachable", None) == (
        "Der core ist nicht erreichbar; er führt den Plan nicht aus."
    )
    assert execution_text("pending", held) == "Der Plan ist noch nicht beim core."
    assert execution_text("off", held) == "Regelbetrieb ist aus: Der core führt den Plan nicht aus."
    assert execution_text("idle", {"mode": "SAFE"}) == "Der core regelt gerade nicht (SAFE)."
    assert execution_text("locked", held) == (
        "Victron ist gesperrt: Der core führt den Plan nicht aus."
    )
    assert execution_text("dry_run", held) == (
        "Dry-Run: Der core rechnet den Plan mit und sendet nichts an Victron."
    )
    assert execution_text("live", held) == "Der core führt den Plan aus."
    assert execution_text("none", None) is None


def test_execution_text_idle_without_a_known_mode() -> None:
    assert execution_text("idle", None) == "Der core regelt gerade nicht (unbekannt)."
    assert execution_text("idle", {"mode": None}) == "Der core regelt gerade nicht (unbekannt)."


def test_every_state_has_a_text_except_none() -> None:
    assert set(EXECUTION_TEXTS) == {
        "unreachable",
        "pending",
        "off",
        "idle",
        "locked",
        "dry_run",
        "live",
    }
