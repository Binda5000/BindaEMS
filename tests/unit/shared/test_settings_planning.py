"""Einstellungen „Planung“ (Plan 3b, Präzisierung 3)."""

import pytest
from pydantic import ValidationError

from bindaems.shared.settings import PlanningSettings, RuntimeSettings


def test_planning_defaults() -> None:
    p = PlanningSettings()
    assert (
        p.grid_charge_allowed,
        p.margin_ct,
        p.roundtrip_pct,
        p.wear_ct,
        p.end_value_factor,
        p.inertia_ct,
    ) == (False, 2.0, 88.0, 2.0, 0.9, 0.5)


def test_stored_settings_without_planning_load_defaults() -> None:
    assert RuntimeSettings.model_validate({}).planning == PlanningSettings()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("roundtrip_pct", 49.0),
        ("roundtrip_pct", 101.0),
        ("end_value_factor", 1.1),
        ("end_value_factor", -0.1),
        ("margin_ct", -0.1),
        ("wear_ct", -0.1),
        ("inertia_ct", -0.1),
        ("margin_ct", float("nan")),
    ],
)
def test_planning_rejects_out_of_range(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        PlanningSettings.model_validate({field: value})
