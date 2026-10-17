"""Mehrkosten beim Boost (Plan 3c, Task 16, Präzisierung 14). Die Sätze sind erfundene Beispiele."""

from datetime import date

import pytest
from tests.plan_helpers import with_power_price

from bindaems.app.control.boost_cost import boost_cost
from bindaems.shared.settings import TariffSettings

DAY = date(2027, 1, 15)


def test_no_power_rate_means_no_cost(cfg) -> None:
    # Der Standardtarif hat den Leistungspreis erst ab 2027 und ohne Satz
    assert boost_cost(cfg, TariffSettings(), 5.0, date(2026, 10, 9)) == {
        "applies": False,
        "month_peak_kw": 5.0,
        "vehicles": [],
    }


def test_a_component_without_a_value_means_no_cost(cfg) -> None:
    result = boost_cost(cfg, TariffSettings(), 5.0, DAY)
    assert result["applies"] is False and result["vehicles"] == []


def test_a_rate_outside_its_validity_means_no_cost(cfg) -> None:
    tariff = with_power_price(value_eur=48.0, valid_from="2027-02-01")
    assert boost_cost(cfg, tariff, 5.0, DAY)["applies"] is False


def test_cost_is_the_rate_difference(cfg) -> None:
    # erfundener Satz 48 €/kW/Jahr = 4,00 €/kW/Monat netto:
    # Tesla 16 A · 3 Phasen · 230 V = 11,04 kW; brutto mit 20 % MwSt.
    tariff = with_power_price(value_eur=48.0, tier_kw=100.0)
    result = boost_cost(cfg, tariff, 5.0, DAY)
    assert result["applies"] is True and result["month_peak_kw"] == 5.0
    tesla = result["vehicles"][0]
    assert tesla == {"id": "tesla", "extra_kw": 11.04, "cost_eur": pytest.approx(52.99)}


def test_the_vehicles_are_those_of_the_limits_with_their_own_power(cfg) -> None:
    tariff = with_power_price(value_eur=48.0, tier_kw=100.0)
    vehicles = boost_cost(cfg, tariff, 5.0, DAY)["vehicles"]
    assert [item["id"] for item in vehicles] == list(cfg.vehicles)
    egolf = next(item for item in vehicles if item["id"] == "egolf")
    assert egolf["extra_kw"] == pytest.approx(16 * 2 * 230 / 1000)  # zwei Phasen


def test_the_upper_tier_counts_above_the_threshold(cfg) -> None:
    # 8 kW Spitze, Stufe bei 10 kW: 2 kW mehr zum unteren, 9,04 kW zum oberen Satz
    tariff = with_power_price(value_eur=24.0, above_eur=60.0, tier_kw=10.0)
    tesla = boost_cost(cfg, tariff, 8.0, DAY)["vehicles"][0]
    expected = (24.0 * 2.0 + 60.0 * 9.04) / 12 * 1.2
    assert tesla["cost_eur"] == pytest.approx(expected, abs=0.005)


def test_minimum_kw_counts_as_peak(cfg) -> None:
    # Spitze 1 kW, Mindestleistung 6 kW: gerechnet wird ab 6 kW, nicht ab 1 kW
    tariff = with_power_price(value_eur=48.0, tier_kw=100.0, minimum_kw=6.0)
    tesla = boost_cost(cfg, tariff, 1.0, DAY)["vehicles"][0]
    assert tesla["cost_eur"] == pytest.approx(52.99)


def test_without_a_peak_the_minimum_counts(cfg) -> None:
    tariff = with_power_price(value_eur=48.0, tier_kw=100.0, minimum_kw=2.0)
    result = boost_cost(cfg, tariff, None, DAY)
    assert result["month_peak_kw"] is None
    assert result["vehicles"][0]["cost_eur"] == pytest.approx(52.99)


def test_a_peak_above_the_boost_still_adds_the_full_power(cfg) -> None:
    # Spitze bereits 20 kW: der Boost kostet trotzdem Spitze + Ladeleistung, nicht nur die Differenz
    tariff = with_power_price(value_eur=48.0, tier_kw=100.0)
    tesla = boost_cost(cfg, tariff, 20.0, DAY)["vehicles"][0]
    assert tesla["cost_eur"] == pytest.approx(52.99)
