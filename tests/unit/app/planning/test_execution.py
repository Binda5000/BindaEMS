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
) -> dict[str, Any]:
    """Health des core, in der der Plan der Absicht ``plan_id`` heißt."""
    held = (
        None
        if plan_id is None
        else {"id": plan_id, "grid_charge_allowed": grid_charge, "valid_until": "…", "slots": []}
    )
    return {
        "mode": mode,
        "intent": {"version": 3, "control": control, "plan": held},
        "actuators": [
            {"id": "wallbox.evcs", "effective": "live", "locked": []},
            {"id": "victron", "effective": effective, "locked": list(locked)},
        ],
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
    assert core_has_plan(health(), PLAN_ID, False)
    assert not core_has_plan(health(), PLAN_ID, True)
    assert not core_has_plan(health(), PLAN_ID + 1, False)
    assert not core_has_plan(health(plan_id=None), PLAN_ID, False)
    assert not core_has_plan(None, PLAN_ID, False)
    assert not core_has_plan({"intent": None}, PLAN_ID, False)


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
