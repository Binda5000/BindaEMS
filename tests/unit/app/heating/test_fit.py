from datetime import date, timedelta

import pytest

from bindaems.app.heating.fit import HeatDay, HeatFit, day_heat, fit_demand, season_of
from bindaems.shared.settings import HeatingSettings

S = HeatingSettings(burner_kw=27, oil_l_per_h=2.9, oil_price_eur_l=1.1, ac_cop=3.0)

_counter = 0


def day(
    *,
    hdd: float | None = 5.0,
    burner_h: float | None = 1.0,
    ac: float | None = 0.0,
    known_h: float = 24.0,
    day_h: float = 24.0,
) -> HeatDay:
    global _counter
    _counter += 1
    return HeatDay(
        day=date(2026, 1, 1) + timedelta(days=_counter),
        burner_h=burner_h,
        known_h=known_h,
        day_h=day_h,
        burner_end=False,
        hdd_kd=hdd,
        outdoor_mean_c=10.0,
        ac_heat_el_kwh=ac,
        note=None,
    )


def good_days(n: int) -> list[HeatDay]:
    return [day(hdd=2.0 + i % 7, burner_h=(3 + i % 7) / 3) for i in range(n)]


def test_day_heat_from_burner_hours() -> None:
    heat = day_heat(day(burner_h=4.0, ac=2.0), S, ac_power_known=True)
    assert heat.oil_heat_kwh == pytest.approx(108.0)
    assert heat.oil_l == pytest.approx(11.6)
    assert heat.oil_eur == pytest.approx(12.76)
    assert heat.ac_heat_kwh == pytest.approx(6.0)
    assert heat.demand_kwh == pytest.approx(114.0)


def test_day_heat_without_nominal_power_is_unknown() -> None:
    heat = day_heat(day(burner_h=4.0), HeatingSettings(), ac_power_known=True)
    assert heat.oil_heat_kwh is None and heat.oil_l is None and heat.demand_kwh is None


def test_without_ac_power_demand_is_oil_only() -> None:
    assert day_heat(day(burner_h=1.0, ac=None), S, ac_power_known=False).demand_kwh == 27.0


def test_fit_recovers_base_and_slope() -> None:
    days = [day(hdd=h, burner_h=(10 + 4.5 * h) / 27, ac=0.0) for h in range(2, 18)]
    fit = fit_demand(days, S, ac_power_known=True)
    assert isinstance(fit, HeatFit)
    assert fit.base_kwh_day == pytest.approx(10.0)
    assert fit.per_kd == pytest.approx(4.5)
    assert fit.r2 == pytest.approx(1.0) and fit.n == 16 and not fit.through_origin


def test_fit_skips_incomplete_warm_and_unknown_days() -> None:
    days = [*good_days(14), day(hdd=0.5), day(hdd=None), day(known_h=20.0, day_h=24.0)]
    fit = fit_demand(days, S, ac_power_known=True)
    assert isinstance(fit, HeatFit) and fit.n == 14


def test_fit_needs_14_days() -> None:
    assert fit_demand(good_days(13), S, ac_power_known=True) == "Noch 13 von 14 Tagen."


def test_fit_negative_base_goes_through_origin() -> None:
    days = [day(hdd=h, burner_h=max(0.0, 5 * h - 8) / 27) for h in range(2, 18)]
    fit = fit_demand(days, S, ac_power_known=True)
    assert isinstance(fit, HeatFit)
    assert fit.through_origin and fit.base_kwh_day == 0.0


def test_fit_without_nominal_power() -> None:
    assert (
        fit_demand(good_days(20), HeatingSettings(), ac_power_known=True) == "Nennleistung fehlt."
    )


def test_fit_without_spread_in_cold() -> None:
    assert (
        fit_demand([day(hdd=8.0)] * 20, S, ac_power_known=True)
        == "Die Tage waren alle gleich kalt."
    )


def test_season_bounds() -> None:
    assert season_of(date(2026, 11, 9)) == (date(2026, 7, 1), date(2027, 6, 30), "2026/27")
    assert season_of(date(2027, 6, 30))[2] == "2026/27"
    assert season_of(date(2027, 7, 1))[2] == "2027/28"
