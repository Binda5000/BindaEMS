from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from bindaems.app.charging.estimate import (
    ESTIMATED_MODES,
    REACH_EPS_KWH,
    ChargeEstimate,
    estimate_charge,
    pv_for_car,
)
from bindaems.shared.control import ChargeMode
from bindaems.shared.plan import BatteryIntent, PlanSlot
from bindaems.shared.timeutil import SLOT

T0 = datetime(2026, 11, 4, 22, 0, tzinfo=UTC)
USABLE = 75.0
EFF = 0.9
MIN_W = 4140.0
MAX_W = 11040.0


def slot(
    start: datetime,
    *,
    import_ct: float = 20.0,
    export_w: float = 0.0,
    import_w: float = 0.0,
    estimated: bool = False,
    ev_w: dict[str, float] | None = None,
    ev_battery_w: dict[str, float] | None = None,
) -> PlanSlot:
    return PlanSlot(
        start=start,
        intent=BatteryIntent(),
        soc_start_pct=50.0,
        soc_end_pct=50.0,
        pv_w=0.0,
        load_w=0.0,
        import_w=import_w,
        export_w=export_w,
        charge_pv_w=0.0,
        charge_grid_w=0.0,
        discharge_w=0.0,
        import_ct=import_ct,
        feed_in_ct=None,
        estimated=estimated,
        cost_ct=0.0,
        budget_kw=None,
        ev_w=ev_w or {},
        ev_battery_w=ev_battery_w or {},
        reasons=[],
    )


def slots_from(start: datetime, **kw: float) -> list[PlanSlot]:
    return [slot(start + i * SLOT, **kw) for i in range(8)]


def run(
    slots: list[PlanSlot],
    *,
    now: datetime = T0,
    soc: float = 20.0,
    target: float = 80.0,
    departure: datetime | None = None,
    now_w: float = MAX_W,
    power_cost_eur: float | None = None,
) -> ChargeEstimate:
    return estimate_charge(
        slots,
        key="tesla",
        now=now,
        soc_pct=soc,
        target_soc_pct=target,
        departure_at=departure or slots[-1].start + SLOT,
        usable_kwh=USABLE,
        efficiency=EFF,
        min_w=MIN_W,
        max_w=MAX_W,
        now_w=now_w,
        power_cost_eur=power_cost_eur,
    )


def soc_for_kwh(kwh: float, target: float = 80.0) -> float:
    """Ladestand, bei dem genau ``kwh`` an der Wallbox fehlen."""
    return target - kwh * EFF / USABLE * 100.0


def four_slots() -> list[PlanSlot]:
    return [slot(T0 + i * SLOT, import_ct=ct) for i, ct in enumerate((30.0, 10.0, 20.0, 40.0))]


def test_modes_listed() -> None:
    assert ESTIMATED_MODES == (
        ChargeMode.PLANNED,
        ChargeMode.NOW,
        ChargeMode.PV,
        ChargeMode.MIN_PV,
    )
    assert REACH_EPS_KWH == 0.1


def test_pv_only_cost_is_zero() -> None:
    est = run(slots_from(T0, export_w=5000.0))
    assert est.modes[ChargeMode.PV].cost_eur == 0.0
    assert est.modes[ChargeMode.PV].energy_kwh == 10.0
    assert est.need_kwh > 10.0


def test_pv_below_min_power_charges_nothing() -> None:
    est = run(slots_from(T0, export_w=3000.0))
    assert est.modes[ChargeMode.PV].energy_kwh == 0.0
    assert est.modes[ChargeMode.PV].finish_at is None
    assert est.modes[ChargeMode.MIN_PV].cost_eur > 0


def test_planned_picks_cheapest_slots() -> None:
    est = run(four_slots(), soc=soc_for_kwh(2 * 11.04 * 0.25))
    planned = est.modes[ChargeMode.PLANNED]
    assert planned.cost_eur == round(11.04 * 0.25 * (0.10 + 0.20), 2)
    assert planned.finish_at == T0 + 3 * SLOT
    assert planned.reachable


def test_now_charges_from_now() -> None:
    est = run(four_slots(), soc=soc_for_kwh(2 * 11.04 * 0.25))
    now = est.modes[ChargeMode.NOW]
    assert now.cost_eur == round(11.04 * 0.25 * (0.30 + 0.10), 2)
    assert now.finish_at == T0 + 2 * SLOT


@settings(max_examples=200, deadline=None)
@given(
    prices=st.lists(st.floats(0, 60), min_size=1, max_size=96),
    surplus=st.lists(st.floats(0, 12000), min_size=96, max_size=96),
    soc=st.floats(0, 100),
)
def test_planned_never_costs_more_than_now(
    prices: list[float], surplus: list[float], soc: float
) -> None:
    slots = [slot(T0 + i * SLOT, import_ct=ct, export_w=surplus[i]) for i, ct in enumerate(prices)]
    est = run(slots, soc=soc, target=100.0)
    now, planned = est.modes[ChargeMode.NOW], est.modes[ChargeMode.PLANNED]
    if now.reachable:
        assert planned.cost_eur <= now.cost_eur + 0.01
        assert planned.reachable
    for mode in ESTIMATED_MODES:
        m = est.modes[mode]
        assert m.energy_kwh <= est.need_kwh + 1e-6 + 0.05
        assert m.soc_pct <= est.target_soc_pct


def test_now_includes_power_cost() -> None:
    est = run(four_slots(), power_cost_eur=1.5)
    assert est.modes[ChargeMode.NOW].power_cost_eur == 1.5
    for mode in (ChargeMode.PLANNED, ChargeMode.PV, ChargeMode.MIN_PV):
        assert est.modes[mode].power_cost_eur is None


def test_departure_within_current_slot() -> None:
    now = T0 + timedelta(minutes=7)
    dep = T0 + timedelta(minutes=15)
    est = run(slots_from(T0), now=now, departure=dep)
    assert est.modes[ChargeMode.NOW].energy_kwh == round(11.04 * 8 / 60, 1)
    assert est.until == dep


def test_departure_not_after_now_raises() -> None:
    with pytest.raises(ValueError):
        run(slots_from(T0), now=T0, departure=T0 - SLOT)
    with pytest.raises(ValueError):
        run(slots_from(T0), now=T0, departure=T0)


def test_window_ends_at_plan_end() -> None:
    start = datetime(2026, 11, 4, 20, 0, tzinfo=UTC)
    slots = [slot(start + i * SLOT) for i in range(12)]  # bis 23:00
    est = run(slots, now=start, soc=5.0, departure=datetime(2026, 11, 5, 7, 0, tzinfo=UTC))
    assert est.until == datetime(2026, 11, 4, 23, 0, tzinfo=UTC)
    assert est.modes[ChargeMode.NOW].reachable is False


def test_own_planned_charging_counts_as_surplus() -> None:
    s = slot(T0, ev_w={"tesla": 5000.0})
    assert pv_for_car(s, "tesla") == 5000.0
    s = slot(T0, ev_w={"tesla": 5000.0}, import_w=2000.0)
    assert pv_for_car(s, "tesla") == 3000.0
    s = slot(T0, ev_w={"tesla": 5000.0}, ev_battery_w={"tesla": 1000.0})
    assert pv_for_car(s, "tesla") == 4000.0
    assert pv_for_car(slot(T0, export_w=700.0), "tesla") == 700.0


def test_already_at_target() -> None:
    est = run(four_slots(), soc=80.0, target=80.0)
    assert est.need_kwh == 0
    for mode in ESTIMATED_MODES:
        m = est.modes[mode]
        assert m.energy_kwh == 0
        assert m.reachable
        assert m.finish_at is None


def test_price_estimated_flag() -> None:
    est = run([slot(T0, estimated=True), slot(T0 + SLOT)])
    assert est.price_estimated is True
    pv_slots = [slot(T0 + i * SLOT, export_w=12000.0, estimated=True) for i in range(4)]
    est = run(pv_slots, soc=soc_for_kwh(1.0))
    assert est.modes[ChargeMode.PV].cost_eur == 0.0
    assert est.price_estimated is False


def test_dst_day() -> None:
    start = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)  # 25.10. 00:00 Ortszeit
    slots = [slot(start + i * SLOT) for i in range(100)]
    est = run(slots, now=start, soc=0.0, target=100.0, departure=start + 100 * SLOT)
    assert est.modes[ChargeMode.NOW].energy_kwh == round(min(est.need_kwh, 11.04 * 25), 1)
    assert est.until == start + 100 * SLOT
