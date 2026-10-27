from datetime import time

import pytest
from pydantic import ValidationError

from bindaems.shared.settings import (
    DrivingSettings,
    EnergyComponent,
    FeedInSettings,
    LearningSettings,
    RuntimeSettings,
    TariffSettings,
    ThermalPlanSettings,
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


def test_driving_defaults() -> None:
    d = RuntimeSettings().learning.driving
    assert (d.return_reserve_pct, d.target_min_pct, d.target_max_pct) == (20, 50, 90)
    assert d.automation == {} and d.presence_entities == {}


def test_target_min_above_max_is_rejected() -> None:
    with pytest.raises(ValidationError, match=r"Ziel-SOC mindestens liegt über höchstens\."):
        DrivingSettings(target_min_pct=80, target_max_pct=60)
    assert DrivingSettings(target_min_pct=70, target_max_pct=70).target_min_pct == 70


def test_reserve_above_50_is_rejected() -> None:
    assert DrivingSettings(return_reserve_pct=50).return_reserve_pct == 50
    with pytest.raises(ValidationError):
        DrivingSettings(return_reserve_pct=51)
    with pytest.raises(ValidationError):
        DrivingSettings(return_reserve_pct=-1)


@pytest.mark.parametrize("field", ["target_min_pct", "target_max_pct"])
@pytest.mark.parametrize("value", [19, 101])
def test_targets_stay_between_20_and_100(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        DrivingSettings(**{field: value})  # type: ignore[arg-type]


def test_driving_per_vehicle_entries() -> None:
    d = DrivingSettings(
        automation={"tesla": True}, presence_entities={"tesla": "device_tracker.auto"}
    )
    assert d.automation == {"tesla": True}
    assert d.presence_entities == {"tesla": "device_tracker.auto"}
    with pytest.raises(ValidationError):
        DrivingSettings(presence_entities={"tesla": "Device Tracker"})
    with pytest.raises(ValidationError):
        DrivingSettings(automation={"": True})


def test_old_learning_settings_without_driving_load() -> None:
    old = RuntimeSettings().model_dump(mode="json")
    del old["learning"]["driving"]
    assert RuntimeSettings.model_validate(old).learning.driving == DrivingSettings()


def test_driving_settings_round_trip() -> None:
    settings = RuntimeSettings(
        learning=LearningSettings(driving=DrivingSettings(automation={"tesla": True}))
    )
    assert RuntimeSettings.model_validate(settings.model_dump(mode="json")) == settings


def test_thermal_plan_defaults() -> None:
    t = RuntimeSettings().learning.thermal
    assert (t.enabled, t.heat_up_k, t.heat_down_k, t.cool_up_k, t.cool_down_k, t.margin_ct) == (
        False,
        1.5,
        1.0,
        1.5,
        1.0,
        2.0,
    )


@pytest.mark.parametrize("field", ["heat_up_k", "heat_down_k", "cool_up_k", "cool_down_k"])
@pytest.mark.parametrize("value", [0.3, 1.25, 0.75])
def test_band_not_multiple_of_half_is_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError, match=r"Das Komfortband geht in Schritten von 0,5 K\."):
        ThermalPlanSettings(**{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "limit"),
    [("heat_up_k", 3.0), ("cool_up_k", 3.0), ("heat_down_k", 2.0), ("cool_down_k", 2.0)],
)
def test_band_above_limits_is_rejected(field: str, limit: float) -> None:
    assert getattr(ThermalPlanSettings(**{field: limit}), field) == limit  # type: ignore[arg-type]
    assert getattr(ThermalPlanSettings(**{field: 0.0}), field) == 0.0  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ThermalPlanSettings(**{field: limit + 0.5})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ThermalPlanSettings(**{field: -0.5})  # type: ignore[arg-type]


def test_thermal_margin_stays_between_0_and_20() -> None:
    assert ThermalPlanSettings(margin_ct=0).margin_ct == 0
    assert ThermalPlanSettings(margin_ct=20).margin_ct == 20
    with pytest.raises(ValidationError):
        ThermalPlanSettings(margin_ct=20.5)
    with pytest.raises(ValidationError):
        ThermalPlanSettings(margin_ct=-1)
    with pytest.raises(ValidationError):
        ThermalPlanSettings(margin_ct=float("nan"))


def test_old_learning_settings_without_thermal_load() -> None:
    old = RuntimeSettings().model_dump(mode="json")
    del old["learning"]["thermal"]
    assert RuntimeSettings.model_validate(old).learning.thermal == ThermalPlanSettings()


def test_thermal_settings_round_trip() -> None:
    settings = RuntimeSettings(
        learning=LearningSettings(thermal=ThermalPlanSettings(enabled=True, heat_up_k=2.5))
    )
    assert RuntimeSettings.model_validate(settings.model_dump(mode="json")) == settings
