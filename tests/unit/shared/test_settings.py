from datetime import time

import pytest
from pydantic import ValidationError

from bindaems.shared.settings import (
    EnergyComponent,
    FeedInSettings,
    LearningSettings,
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
        "power_price",
        "grid_base_fee",
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
        TariffSettings(components=[EnergyComponent(id="a", name="A", value_ct=1.0)])


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
        "Tarifbestandteil „Leistungspreis (ElWG)“ hat noch keinen Wert.",
        "Tarifbestandteil „Grundgebühr Netz“ hat noch keinen Wert.",
        "Noch kein OeMAG-Monatswert eingetragen.",
    ]


def test_learning_defaults() -> None:
    assert RuntimeSettings().learning == LearningSettings(load_model_enabled=True, ha_sensors=[])


def test_at_most_eight_sensors() -> None:
    names = [f"sensor.s{i}" for i in range(9)]
    assert len(LearningSettings(ha_sensors=names[:8]).ha_sensors) == 8
    with pytest.raises(ValidationError):
        LearningSettings(ha_sensors=names)


def test_sensors_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="Sensoren dürfen nicht doppelt vorkommen"):
        LearningSettings(ha_sensors=["sensor.a", "sensor.a"])


@pytest.mark.parametrize("entity_id", ["Sensor.a", "sensor", "sensor.a b", "sensor.a.b", ""])
def test_sensor_names_must_be_entity_ids(entity_id: str) -> None:
    with pytest.raises(ValidationError):
        LearningSettings(ha_sensors=[entity_id])


def test_old_settings_without_learning_load() -> None:
    old = RuntimeSettings().model_dump(mode="json", exclude={"learning"})
    assert "learning" not in old
    assert RuntimeSettings.model_validate(old).learning == LearningSettings()
