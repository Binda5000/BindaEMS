"""Arten der Tarifbestandteile (Spec 9.1): Energie, Leistungspreis, Gebühr, Einspeisung."""

from datetime import date

import pytest
from pydantic import ValidationError

from bindaems.shared.settings import (
    EnergyComponent,
    FeedInComponent,
    MonthlyFeeComponent,
    PowerComponent,
    RuntimeSettings,
    TariffSettings,
    settings_warnings,
)

SPOT = {"id": "spot", "name": "Börsenpreis", "source": "spot"}


def test_stored_settings_without_kind_load_as_energy() -> None:
    data = {
        "tariff": {
            "components": [
                {"id": "spot", "name": "Börsenpreis", "source": "spot"},
                {"id": "grid_usage", "name": "Netz", "value_ct": 8.11},
            ]
        }
    }
    tariff = RuntimeSettings.model_validate(data).tariff
    assert [type(c) for c in tariff.components] == [EnergyComponent, EnergyComponent]
    assert tariff.components[1].value_ct == 8.11


def test_default_tariff_has_power_price_and_base_fee() -> None:
    tariff = TariffSettings()
    power = next(c for c in tariff.components if c.id == "power_price")
    assert isinstance(power, PowerComponent)
    assert (power.tier_kw, power.minimum_kw, power.valid_from) == (10.0, 2.0, date(2027, 1, 1))
    assert power.value_eur is None
    assert any(isinstance(c, MonthlyFeeComponent) for c in tariff.components)


def test_kind_is_always_dumped_and_round_trips() -> None:
    settings = RuntimeSettings()
    dumped = settings.model_dump(mode="json")
    kinds = [c["kind"] for c in dumped["tariff"]["components"]]
    assert kinds[-2:] == ["power_per_kw_year", "fixed_per_month"]
    assert RuntimeSettings.model_validate(dumped) == settings


def test_overlapping_power_prices_are_rejected() -> None:
    a = {
        "id": "p1",
        "name": "A",
        "kind": "power_per_kw_year",
        "value_eur": 19,
        "valid_from": "2027-01-01",
        "valid_until": "2027-12-31",
    }
    b = {**a, "id": "p2", "valid_from": "2027-06-01", "valid_until": None}
    with pytest.raises(
        ValidationError, match="Leistungspreise dürfen sich zeitlich nicht überschneiden"
    ):
        TariffSettings.model_validate({"components": [SPOT, a, b]})


def test_adjacent_power_prices_are_allowed() -> None:
    a = {"id": "p1", "name": "A", "kind": "power_per_kw_year", "valid_until": "2027-12-31"}
    b = {"id": "p2", "name": "B", "kind": "power_per_kw_year", "valid_from": "2028-01-01"}
    TariffSettings.model_validate({"components": [SPOT, a, b]})


def test_windows_only_for_energy_and_feed_in() -> None:
    fee = {
        "id": "f",
        "name": "Gebühr",
        "kind": "fixed_per_month",
        "windows": [{"start": "10:00", "end": "16:00", "factor": 0.8}],
    }
    with pytest.raises(ValidationError):  # extra="forbid": MonthlyFeeComponent hat kein windows
        TariffSettings.model_validate({"components": [SPOT, fee]})


def test_feed_in_value_may_be_negative() -> None:
    item = {"id": "fee", "name": "Einspeiseentgelt", "kind": "feed_in_per_kwh", "value_ct": -0.5}
    tariff = TariffSettings.model_validate({"components": [SPOT, item]})
    assert isinstance(tariff.components[1], FeedInComponent)
    assert tariff.components[1].value_ct == -0.5 and tariff.components[1].vat is False


def test_unknown_kind_is_rejected() -> None:
    item = {"id": "x", "name": "X", "kind": "unbekannt"}
    with pytest.raises(ValidationError):
        TariffSettings.model_validate({"components": [SPOT, item]})


def test_power_validity_order_is_checked() -> None:
    item = {
        "id": "p",
        "name": "P",
        "kind": "power_per_kw_year",
        "valid_from": "2028-01-01",
        "valid_until": "2027-01-01",
    }
    with pytest.raises(ValidationError, match="valid_from liegt nach valid_until"):
        TariffSettings.model_validate({"components": [SPOT, item]})


def test_spot_count_only_counts_energy_components() -> None:
    fee = {"id": "f", "name": "Gebühr", "kind": "fixed_per_month"}
    with pytest.raises(ValidationError, match="Börsenpreis"):
        TariffSettings.model_validate({"components": [fee]})


def test_missing_power_price_warns() -> None:
    settings = RuntimeSettings(
        tariff=TariffSettings(components=[EnergyComponent(id="spot", name="B", source="spot")])
    )
    assert "Kein Leistungspreis eingetragen; nach ElWG gilt er ab 1. 1. 2027." in (
        settings_warnings(settings)
    )


def test_power_price_without_value_warns() -> None:
    warnings = settings_warnings(RuntimeSettings())
    assert any(
        w.startswith("6 Tarifbestandteile haben noch keinen Wert: ")
        and "„Leistungspreis (ElWG)“" in w
        and w.endswith("„Grundgebühr Netz“.")
        for w in warnings
    )
    assert not any(w.startswith("Tarifbestandteil „") for w in warnings)


def test_feed_in_without_value_warns_and_spot_does_not() -> None:
    item = {"id": "fee", "name": "Einspeiseentgelt", "kind": "feed_in_per_kwh"}
    settings = RuntimeSettings(tariff=TariffSettings.model_validate({"components": [SPOT, item]}))
    warnings = settings_warnings(settings)
    assert "Tarifbestandteil „Einspeiseentgelt“ hat noch keinen Wert." in warnings
    assert not any("Börsenpreis" in w for w in warnings)
