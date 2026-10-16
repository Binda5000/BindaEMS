from datetime import date, datetime, time

import pytest

from bindaems.app.tariff.calc import (
    billed_kw,
    component_net_ct,
    feed_in_price,
    import_price,
    monthly_fees_eur,
    power_cost_eur,
    power_rate,
)
from bindaems.shared.settings import EnergyComponent, FeedInSettings, TariffSettings, TimeWindow

SPOT = {"id": "spot", "name": "Börsenpreis", "kind": "energy_per_kwh", "source": "spot"}
VALUES = {"grid_usage": 8.0, "grid_loss": 0.6, "electricity_tax": 1.5, "renewables_levy": 0.5}


def tariff() -> TariffSettings:
    """Standardbestandteile mit Werten: grid_usage 8.0, grid_loss 0.6, electricity_tax 1.5,
    renewables_levy 0.5."""
    default = TariffSettings()
    components = [
        component.model_copy(update={"value_ct": VALUES[component.id]})
        if component.id in VALUES
        else component
        for component in default.components
    ]
    return default.model_copy(update={"components": components})


def tariff_with_extra(component: EnergyComponent) -> TariffSettings:
    """tariff() plus ein zusätzlicher Bestandteil."""
    base = tariff()
    return base.model_copy(update={"components": [*base.components, component]})


def at(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def test_import_price_sums_components_with_vat() -> None:
    price = import_price(at("2026-10-09T08:00:00+00:00"), 10.0, tariff())
    assert price.net_ct == pytest.approx(21.8) and price.gross_ct == pytest.approx(26.16)
    assert price.parts_net["grid_usage"] == 8.0 and price.missing == ()
    assert price.parts_net["spot"] == 10.0


def test_snap_factor_in_summer_window() -> None:  # 10:00 MESZ
    price = import_price(at("2026-07-15T08:00:00+00:00"), 10.0, tariff())
    assert price.net_ct == pytest.approx(20.2)


def test_snap_window_end_is_exclusive_in_local_time() -> None:  # 16:00 MESZ
    price = import_price(at("2026-07-15T14:00:00+00:00"), 10.0, tariff())
    assert price.net_ct == pytest.approx(21.8)
    # 15:45 MESZ liegt noch im Fenster
    price = import_price(at("2026-07-15T13:45:00+00:00"), 10.0, tariff())
    assert price.net_ct == pytest.approx(20.2)


def test_snap_starts_on_first_of_april() -> None:
    price = import_price(at("2026-04-01T08:00:00+00:00"), 10.0, tariff())
    assert price.net_ct == pytest.approx(20.2)


def test_snap_not_in_october() -> None:
    price = import_price(at("2026-10-01T08:00:00+00:00"), 10.0, tariff())
    assert price.net_ct == pytest.approx(21.8)


def test_validity_dates_use_local_date() -> None:
    t = tariff_with_extra(
        EnergyComponent(id="alt", name="Alt", value_ct=1.0, valid_until=date(2026, 12, 31))
    )
    # 23:45 Ortszeit am 31. 12.
    assert import_price(at("2026-12-31T22:45:00+00:00"), 10.0, t).parts_net.get("alt") == 1.0
    # 00:00 Ortszeit am 1. 1.
    assert "alt" not in import_price(at("2026-12-31T23:00:00+00:00"), 10.0, t).parts_net


def test_component_without_vat() -> None:
    t = tariff_with_extra(EnergyComponent(id="frei", name="Frei", value_ct=1.0, vat=False))
    price = import_price(at("2026-10-09T08:00:00+00:00"), 10.0, t)
    assert price.gross_ct == pytest.approx(27.16)


def test_missing_values_are_reported() -> None:
    price = import_price(at("2026-10-09T08:00:00+00:00"), 10.0, TariffSettings())
    assert price.missing == ("grid_usage", "grid_loss", "electricity_tax", "renewables_levy")
    assert price.net_ct == pytest.approx(11.2)


def test_negative_spot_passes_through() -> None:
    price = import_price(at("2026-10-09T08:00:00+00:00"), -5.0, tariff())
    assert price.net_ct == pytest.approx(6.8)


def test_window_with_absolute_value() -> None:
    window = TimeWindow(start=time(0), end=time(6), value_ct=4.0)
    nacht = EnergyComponent(id="nacht", name="Nacht", value_ct=2.0, windows=[window])
    t = tariff_with_extra(nacht)
    # 04:00 Ortszeit
    assert import_price(at("2026-10-09T02:00:00+00:00"), 10.0, t).parts_net["nacht"] == 4.0
    # 10:00 Ortszeit
    assert import_price(at("2026-10-09T08:00:00+00:00"), 10.0, t).parts_net["nacht"] == 2.0
    # ein Fenster mit value_ct deckt einen fehlenden Grundwert
    empty = EnergyComponent(id="leer", name="Leer", windows=[window])
    assert component_net_ct(empty, at("2026-10-09T02:00:00+00:00"), 10.0) == 4.0
    missing = import_price(at("2026-10-09T02:00:00+00:00"), 10.0, tariff_with_extra(empty))
    assert missing.missing == ()


def test_feed_in_uses_latest_month_not_after_slot() -> None:
    feed_in = FeedInSettings(monthly_ct={"2026-08": 6.1, "2026-09": 7.3})
    assert feed_in_price(at("2026-10-09T08:00:00+00:00"), feed_in, tariff()) == 7.3
    # 23:45 Ortszeit am 31. 8.
    assert feed_in_price(at("2026-08-31T21:45:00+00:00"), feed_in, tariff()) == 6.1
    assert feed_in_price(at("2026-07-10T08:00:00+00:00"), feed_in, tariff()) is None


def test_feed_in_month_boundary_in_local_time() -> None:
    feed_in = FeedInSettings(monthly_ct={"2026-08": 6.1, "2026-09": 7.3})
    # 00:00 Ortszeit am 1. 9.
    assert feed_in_price(at("2026-08-31T22:00:00+00:00"), feed_in, tariff()) == 7.3


RATE = TariffSettings.model_validate(
    {
        "components": [
            SPOT,
            {
                "id": "power_price",
                "name": "Leistungspreis",
                "kind": "power_per_kw_year",
                "value_eur": 19.0,
                "value_above_eur": 38.0,
                "valid_from": "2027-01-01",
            },
        ]
    }
)


def test_power_rate_before_validity_is_none() -> None:
    assert power_rate(date(2026, 12, 31), RATE) is None


def test_power_cost_below_tier() -> None:
    rate = power_rate(date(2027, 1, 15), RATE)
    assert rate is not None
    assert power_cost_eur(7.5, rate) == pytest.approx(14.25)  # 7,5 · 19 / 12 · 1,2


def test_power_cost_above_tier() -> None:
    rate = power_rate(date(2027, 1, 15), RATE)
    assert rate is not None
    assert power_cost_eur(12.0, rate) == pytest.approx(26.6)  # (10 · 19 + 2 · 38) / 12 · 1,2


def test_power_cost_at_tier_boundary() -> None:
    rate = power_rate(date(2027, 1, 15), RATE)
    assert rate is not None
    assert power_cost_eur(10.0, rate) == pytest.approx(19.0)


def test_power_cost_uses_minimum_without_peak() -> None:
    rate = power_rate(date(2027, 1, 15), RATE)
    assert rate is not None
    assert billed_kw(None, rate) == 2.0 and billed_kw(1.2, rate) == 2.0
    assert power_cost_eur(None, rate) == pytest.approx(3.8)


def test_power_rate_without_value_is_missing_and_free() -> None:
    rate = power_rate(date(2027, 3, 1), TariffSettings())  # Standard: Leistungspreis ohne Satz
    assert rate is not None and rate.missing and power_cost_eur(9.0, rate) == 0.0


def test_above_rate_defaults_to_below_rate() -> None:
    tariff = TariffSettings.model_validate(
        {
            "components": [
                SPOT,
                {"id": "p", "name": "P", "kind": "power_per_kw_year", "value_eur": 24.0},
            ]
        }
    )
    rate = power_rate(date(2027, 1, 1), tariff)
    assert rate is not None and rate.above_eur_kw_year == 24.0


def test_net_power_rate_without_vat() -> None:
    tariff = TariffSettings.model_validate(
        {
            "components": [
                SPOT,
                {
                    "id": "p",
                    "name": "P",
                    "kind": "power_per_kw_year",
                    "value_eur": 12.0,
                    "vat": False,
                },
            ]
        }
    )
    rate = power_rate(date(2027, 1, 1), tariff)
    assert rate is not None and rate.vat_factor == 1.0
    assert power_cost_eur(6.0, rate) == pytest.approx(6.0)


def test_monthly_fees_sum_valid_components_gross() -> None:
    tariff = TariffSettings.model_validate(
        {
            "components": [
                SPOT,
                {"id": "a", "name": "A", "kind": "fixed_per_month", "value_eur": 3.5},
                {
                    "id": "b",
                    "name": "B",
                    "kind": "fixed_per_month",
                    "value_eur": 2.0,
                    "vat": False,
                    "valid_until": "2026-09-30",
                },
            ]
        }
    )
    assert monthly_fees_eur(date(2026, 10, 1), tariff) == pytest.approx(4.2)
    assert monthly_fees_eur(date(2026, 9, 1), tariff) == pytest.approx(6.2)


def test_feed_in_adds_feed_in_components() -> None:
    tariff = TariffSettings.model_validate(
        {
            "components": [
                SPOT,
                {"id": "fee", "name": "Entgelt", "kind": "feed_in_per_kwh", "value_ct": -0.5},
            ]
        }
    )
    feed_in = FeedInSettings(monthly_ct={"2026-10": 7.3})
    assert feed_in_price(at("2026-10-09T08:00:00+00:00"), feed_in, tariff) == pytest.approx(6.8)
    assert feed_in_price(at("2026-09-09T08:00:00+00:00"), feed_in, tariff) is None


def test_feed_in_component_validity_vat_and_windows() -> None:
    tariff = TariffSettings.model_validate(
        {
            "components": [
                SPOT,
                {
                    "id": "bonus",
                    "name": "Bonus",
                    "kind": "feed_in_per_kwh",
                    "value_ct": 1.0,
                    "vat": True,
                    "valid_from": "2026-10-10",
                    "windows": [{"months": [10], "start": "10:00", "end": "16:00", "factor": 0.5}],
                },
            ]
        }
    )
    feed_in = FeedInSettings(monthly_ct={"2026-10": 7.3})
    # vor Gültigkeit: nur OeMAG
    assert feed_in_price(at("2026-10-09T08:00:00+00:00"), feed_in, tariff) == pytest.approx(7.3)
    # gültig, außerhalb des Fensters: 1,0 ct netto · 1,2
    assert feed_in_price(at("2026-10-10T04:00:00+00:00"), feed_in, tariff) == pytest.approx(8.5)
    # im Fenster (12:00 Ortszeit): 1,0 · 0,5 · 1,2
    assert feed_in_price(at("2026-10-10T10:00:00+00:00"), feed_in, tariff) == pytest.approx(7.9)


def test_import_price_ignores_other_kinds() -> None:
    tariff = TariffSettings()  # mit Leistungspreis und Grundgebühr
    price = import_price(at("2026-10-09T08:00:00+00:00"), 10.0, tariff)
    energy_ids = {c.id for c in tariff.components if c.kind == "energy_per_kwh"}
    assert set(price.parts_net) <= energy_ids
