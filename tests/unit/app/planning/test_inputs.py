"""Tests der Eingangsdaten: Horizont, Restwert und Leistungspreis je Monat (Plan 3b, Task 3)."""

from __future__ import annotations

import math

import pytest
from tests.plan_helpers import (
    NOW,
    assemble,
    at,
    estimated,
    flat,
    known,
    previous_plan,
    price,
    state,
    with_power_price,
)

from bindaems.app.planning.battery_model import params_of, sim_slots
from bindaems.app.planning.inputs import (
    END_WINDOW,
    MAX_SLOTS,
    PlanningUnavailable,
    end_value_ct,
    horizon_prices,
    peak_months,
)
from bindaems.shared.settings import PlanningSettings, TariffSettings
from bindaems.shared.timeutil import SLOT


def test_horizon_runs_to_last_known_price_max_36h():
    prices = known(NOW, slots=200)
    horizon, after = horizon_prices(prices, NOW)
    assert len(horizon) == MAX_SLOTS == 144 and horizon[0].start == at("2026-10-16T10:00:00+00:00")
    assert after[0].start == horizon[-1].start + SLOT
    assert after[-1].start < horizon[-1].start + SLOT + END_WINDOW


def test_horizon_ends_at_last_known_price():
    horizon, after = horizon_prices(known(NOW, slots=40), NOW)
    assert len(horizon) == 40 and after == []


def test_short_prices_extend_to_4h_with_estimates():
    prices = known(NOW, slots=8) + estimated(NOW + 8 * SLOT, slots=40)
    horizon, _ = horizon_prices(prices, NOW)
    assert len(horizon) == 16 and [p.origin for p in horizon[8:]] == ["estimated"] * 8


def test_estimates_do_not_extend_a_long_horizon():
    prices = known(NOW, slots=20) + estimated(NOW + 20 * SLOT, slots=40)
    horizon, after = horizon_prices(prices, NOW)
    assert len(horizon) == 20 and after[0].origin == "estimated"


def test_estimates_stop_at_a_gap():
    prices = known(NOW, slots=8) + estimated(NOW + 10 * SLOT, slots=40)
    horizon, _ = horizon_prices(prices, NOW)
    assert len(horizon) == 8


def test_prices_before_the_current_slot_are_ignored():
    horizon, _ = horizon_prices(known(NOW - 4 * SLOT, slots=30), NOW)
    assert horizon[0].start == at("2026-10-16T10:00:00+00:00") and len(horizon) == 26


def test_no_price_for_current_slot_is_unavailable():
    with pytest.raises(PlanningUnavailable, match="kein Preis für den aktuellen Slot"):
        assemble(prices=known(NOW + SLOT, slots=20))


def test_unavailable_carries_reason_without_prefix():
    with pytest.raises(PlanningUnavailable) as caught:
        assemble(battery=None)
    assert caught.value.reason == "kein SOC vom core" and str(caught.value) == "kein SOC vom core"


def test_horizon_stops_at_missing_forecast():
    inputs = assemble(prices=known(NOW, slots=40), pv=flat(NOW, 10, 0.0))
    assert len(inputs.slots) == 10


def test_horizon_stops_at_missing_load_forecast():
    inputs = assemble(prices=known(NOW, slots=40), load=flat(NOW, 7, 500.0))
    assert len(inputs.slots) == 7


def test_too_short_horizon_is_unavailable():
    with pytest.raises(PlanningUnavailable, match="zu wenig Daten für einen Plan"):
        assemble(prices=known(NOW, slots=40), load=flat(NOW, 3, 500.0))


def test_four_slots_are_enough():
    assert len(assemble(load=flat(NOW, 4, 500.0)).slots) == 4


@pytest.mark.parametrize(
    "field,reason",
    [
        ("battery", "kein SOC vom core"),
        ("pv", "keine PV-Prognose"),
        ("load", "keine Lastprognose"),
    ],
)
def test_missing_inputs_are_unavailable(field, reason):
    with pytest.raises(PlanningUnavailable, match=reason):
        assemble(**{field: None})


def test_check_order_is_battery_price_pv_load():
    with pytest.raises(PlanningUnavailable, match="kein SOC"):
        assemble(battery=None, prices=[], pv=None, load=None)
    with pytest.raises(PlanningUnavailable, match="kein Preis"):
        assemble(prices=[], pv=None, load=None)
    with pytest.raises(PlanningUnavailable, match="keine PV-Prognose"):
        assemble(pv=None, load=None)


def test_horizon_over_dst_day():
    now = at("2026-10-24T22:00:00+00:00")  # 25. 10. 00:00 lokal
    inputs = assemble(now=now, prices=known(now, slots=100))
    assert len(inputs.slots) == 100 and inputs.slots[-1].start == at("2026-10-25T22:45:00+00:00")


def test_slots_carry_forecasts_and_prices():
    inputs = assemble(
        prices=known(NOW, slots=20, ct=12.5, feed_in=4.0) + estimated(NOW + 20 * SLOT, slots=3),
        pv=flat(NOW, 200, 700.0),
        load=flat(NOW, 200, 300.0),
    )
    first = inputs.slots[0]
    assert (first.pv_w, first.load_w, first.import_ct, first.feed_in_ct) == (
        700.0,
        300.0,
        12.5,
        4.0,
    )
    assert first.origin == "primary" and inputs.created_at == NOW


def test_end_value_from_median_after_horizon():
    after = [price(ct) for ct in (10.0, 20.0, 30.0)]
    assert end_value_ct(after, PlanningSettings()) == pytest.approx(
        (math.sqrt(0.88) * 20 - 2) * 0.9
    )
    assert end_value_ct([], PlanningSettings()) == 0.0
    assert end_value_ct([price(1.0)], PlanningSettings()) == 0.0


def test_end_value_uses_prices_after_the_actual_horizon():
    prices = known(NOW, slots=20, ct=10.0) + known(NOW + 20 * SLOT, slots=96, ct=40.0)
    inputs = assemble(prices=prices, pv=flat(NOW, 12, 0.0))  # Prognose endet nach 12 Slots
    # nach Slot 12 liegen 8 Preise zu 10 ct und 88 zu 40 ct im 24-h-Fenster: Median 40 ct
    assert inputs.end_value_ct == pytest.approx((math.sqrt(0.88) * 40 - 2) * 0.9)


def test_peak_months_current_and_next():
    tariff = with_power_price(value_eur=30.0, above_eur=60.0, valid_from="2027-01-01")
    starts = [
        at("2027-01-31T22:30:00+00:00"),
        at("2027-01-31T22:45:00+00:00"),
        at("2027-01-31T23:00:00+00:00"),
    ]
    months = peak_months(starts, tariff, month_peak_kw=3.4)
    assert [(m.month, m.peak0_kw) for m in months] == [("2027-01", 3.4), ("2027-02", 2.0)]
    assert months[0].below_ct_per_kw == pytest.approx(300.0)
    assert months[0].above_ct_per_kw == pytest.approx(600.0)
    assert months[0].tier_kw == 10.0


def test_peak_zero_is_at_least_the_minimum():
    tariff = with_power_price(value_eur=30.0, valid_from="2027-01-01", minimum_kw=2.0)
    [month] = peak_months([at("2027-01-10T10:00:00+00:00")], tariff, month_peak_kw=0.5)
    assert month.peak0_kw == 2.0
    [month] = peak_months([at("2027-01-10T10:00:00+00:00")], tariff, month_peak_kw=None)
    assert month.peak0_kw == 2.0 and month.above_ct_per_kw == month.below_ct_per_kw


def test_peak_months_skip_months_without_power_price():
    tariff = with_power_price(value_eur=30.0, valid_from="2027-02-01")
    starts = [at("2027-01-31T22:30:00+00:00"), at("2027-01-31T23:00:00+00:00")]
    assert [m.month for m in peak_months(starts, tariff, 3.0)] == ["2027-02"]
    assert peak_months([], tariff, 3.0) == []


def test_missing_power_rate_has_no_peak_months():
    assert peak_months([at("2027-03-01T10:00:00+00:00")], TariffSettings(), 5.0) == []


def test_inputs_carry_peak_months():
    tariff = with_power_price(value_eur=30.0, valid_from="2026-01-01")
    inputs = assemble(tariff=tariff, month_peak_kw=4.0)
    assert [(m.month, m.peak0_kw) for m in inputs.peak_months] == [("2026-10", 4.0)]
    assert inputs.month_peak_kw == 4.0


def test_inputs_carry_the_support_threshold():
    assert assemble().support_soc_pct == 70.0
    assert assemble(support_soc_pct=85.0).support_soc_pct == 85.0


def test_missing_feed_in_counts_as_zero():
    inputs = assemble(prices=known(NOW, slots=20, feed_in=None))
    assert sim_slots(inputs)[0].feed_in_ct is None  # das Akkumodell rechnet mit 0 ct


def test_bms_limits_apply_to_first_hour():
    inputs = assemble(battery=state(limit_now_charge_w=3000.0))
    assert [s.max_charge_w for s in sim_slots(inputs)[3:5]] == [3000.0, 10000.0]


def test_bms_limits_never_raise_the_configured_limits():
    inputs = assemble(battery=state(limit_now_charge_w=50000.0, limit_now_discharge_w=2000.0))
    slots = sim_slots(inputs)
    assert slots[0].max_charge_w == 10000.0
    assert [s.max_discharge_w for s in slots[:5]] == [2000.0] * 4 + [12000.0]


def test_sim_slots_follow_inputs():
    inputs = assemble(pv=flat(NOW, 200, 700.0), load=flat(NOW, 200, 300.0))
    first = sim_slots(inputs)[0]
    assert (first.start, first.pv_w, first.load_w, first.import_ct) == (
        inputs.slots[0].start,
        700.0,
        300.0,
        20.0,
    )


def test_params_of_uses_reserve_and_cap():
    params = params_of(assemble(battery=state(soc_pct=50.0)))
    assert (params.capacity_kwh, params.lo_kwh, params.hi_kwh) == (30.0, 6.0, 30.0)
    assert params.eta_ch == params.eta_dis == pytest.approx(math.sqrt(0.88))


def test_params_of_widens_limits_around_soc_outside_them():
    below = params_of(assemble(battery=state(soc_pct=10.0)))
    assert below.lo_kwh == pytest.approx(3.0)
    above = params_of(assemble(battery=state(soc_pct=100.0, soc_max_pct=90.0)))
    assert above.hi_kwh == pytest.approx(30.0)


def test_previous_plan_battery_power_for_first_four_slots():
    inputs = assemble(previous=previous_plan(battery_w=-1500.0, slots=8))
    assert [s.prev_battery_w for s in inputs.slots[:5]] == [-1500.0] * 4 + [None]


def test_previous_plan_charging_is_positive_and_gaps_are_none():
    inputs = assemble(previous=previous_plan(battery_w=800.0, slots=2))
    assert [s.prev_battery_w for s in inputs.slots[:4]] == [800.0, 800.0, None, None]
    assert all(s.prev_battery_w is None for s in assemble().slots)
