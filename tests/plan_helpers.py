"""Helfer für Tests rund um den Akkuplan (Plan 3b); spätere Tasks erweitern sie."""

from __future__ import annotations

from datetime import datetime

from bindaems.shared.plan import PLAN_VALID, BatteryIntent, Plan, PlanSlot

DAY = "2026-10-16"


def at(text: str) -> datetime:
    """Zeitpunkt aus ISO-Text mit Zeitzone."""
    return datetime.fromisoformat(text)


def sample_slot(start: str, *, soc_pct: float = 50.0, price_ct: float = 20.0) -> PlanSlot:
    """Slot ab ``start`` (``HH:MM`` als UTC am 16. 10. 2026): Eigenverbrauch, sonst Nullen."""
    return PlanSlot(
        start=at(f"{DAY}T{start}:00+00:00"),
        intent=BatteryIntent(),
        soc_start_pct=soc_pct,
        soc_end_pct=soc_pct,
        pv_w=0.0,
        load_w=0.0,
        import_w=0.0,
        export_w=0.0,
        charge_pv_w=0.0,
        charge_grid_w=0.0,
        discharge_w=0.0,
        import_ct=price_ct,
        feed_in_ct=None,
        estimated=False,
        cost_ct=0.0,
        budget_kw=None,
        reasons=[],
    )


def sample_plan(created: str, slots: list[str]) -> Plan:
    """Plan des Optimierers, erzeugt zu ``created`` (ISO mit Zeitzone), mit Slots ``HH:MM``."""
    created_at = at(created)
    return Plan(
        created_at=created_at,
        trigger="start",
        source="optimizer",
        solver_status="optimal",
        fallback_reason=None,
        valid_until=created_at + PLAN_VALID,
        settings_version=1,
        grid_charge_allowed=False,
        reserve_pct=10.0,
        soc_max_pct=100.0,
        month_peak_kw=None,
        power_price=False,
        expected_cost_ct=0.0,
        baseline_cost_ct=0.0,
        issues=[],
        slots=[sample_slot(start) for start in slots],
    )
