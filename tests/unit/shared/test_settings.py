from datetime import time

import pytest
from pydantic import ValidationError

from bindaems.shared.settings import (
    FeedInSettings,
    PriceComponent,
    RuntimeSettings,
    TariffSettings,
    TimeWindow,
    settings_warnings,
)


def test_defaults_follow_spec() -> None:
    s = RuntimeSettings()
    assert (s.tariff.vat_pct, s.tariff.fixed_price_gross_ct) == (20.0, 30.0)
    assert [c.id for c in s.tariff.components] == [
        "spot",
        "supplier_markup",
        "grid_usage",
        "grid_loss",
        "electricity_tax",
        "renewables_levy",
    ]
    assert s.tariff.components[0].source == "spot"
    assert s.tariff.components[1].value_ct == 1.2 and s.tariff.components[1].vat
    snap = s.tariff.components[2].windows[0]
    assert (snap.months, snap.start, snap.end, snap.factor) == (
        [4, 5, 6, 7, 8, 9],
        time(10),
        time(16),
        0.8,
    )
    assert snap.weekdays == [0, 1, 2, 3, 4, 5, 6]
    assert (s.prices.vat_mode, s.prices.vat_fallback, s.prices.reference_source) == (
        "auto",
        "gross",
        "energy_charts",
    )
    model = s.pv_model
    assert (model.performance_ratio, model.temp_coeff_pct_per_k, model.noct_c) == (
        0.85,
        -0.35,
        45.0,
    )


def test_exactly_one_spot_component_required() -> None:
    with pytest.raises(ValidationError, match="Börsenpreis"):
        TariffSettings(components=[PriceComponent(id="a", name="A", value_ct=1.0)])


def test_window_needs_exactly_one_of_factor_and_value() -> None:
    with pytest.raises(ValidationError):
        TimeWindow(factor=0.8, value_ct=1.0)
    with pytest.raises(ValidationError):
        TimeWindow()


@pytest.mark.parametrize(("start", "end"), [(time(10, 10), time(16)), (time(16), time(10))])
def test_window_times_on_quarter_hours_and_ordered(start: time, end: time) -> None:
    with pytest.raises(ValidationError):
        TimeWindow(start=start, end=end, factor=1.0)
    assert TimeWindow(start=time(22), end=time(0), factor=1.0).end == time(0)


@pytest.mark.parametrize("monthly", [{"2026-13": 7.0}, {"2026-09": -1.0}])
def test_feed_in_months_and_values_validated(monthly: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        FeedInSettings(monthly_ct=monthly)


def test_warnings_list_missing_values() -> None:
    assert settings_warnings(RuntimeSettings()) == [
        "Tarifbestandteil „Netznutzungsentgelt Arbeitspreis (Netzebene 7)“ hat noch keinen Wert.",
        "Tarifbestandteil „Netzverlustentgelt“ hat noch keinen Wert.",
        "Tarifbestandteil „Elektrizitätsabgabe“ hat noch keinen Wert.",
        "Tarifbestandteil „Erneuerbaren-Förderbeitrag“ hat noch keinen Wert.",
        "Noch kein OeMAG-Monatswert eingetragen.",
    ]
