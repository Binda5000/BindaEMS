"""Tests des Ersatzplans (Präzisierung 10): Eigenverbrauch, Halten vor teuren Slots, Netzladen
nur mit Schalter, Spread-Regel und Regel R1."""

from __future__ import annotations

from tests.plan_helpers import scenario, small_battery

from bindaems.app.planning.fallback import expensive_today, fallback_intents
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind

EVENING = [12.0] * 8 + [40.0] * 4  # 02:00–04:00 billig, 04:00–05:00 teuer (lokal)


def test_default_is_eigenverbrauch() -> None:
    inputs = scenario(prices=[20.0] * 8, pv=[0.0] * 8, load=[500.0] * 8, soc_pct=80.0)
    assert {i.kind for i in fallback_intents(inputs)} == {BatteryIntentKind.EIGENVERBRAUCH}


def test_holds_cheapest_discharging_slots_when_soc_short_for_expensive_evening() -> None:
    # Der Abend braucht 2 kWh am Hausanschluss, also 2,13 kWh aus dem Akku (Wirkungsgrad). Mit
    # 58 % hat der Akku 3,8 kWh über der Reserve; die acht billigen Slots zu 1 kW zehren je
    # 0,27 kWh. Ein gehaltener Slot reicht nicht (1,93 kWh bleiben), zwei reichen (2,20 kWh).
    prices = [14.0, 12.0, 13.0, 12.5, 15.0, 15.0, 15.0, 15.0] + [40.0] * 4
    inputs = scenario(
        prices=prices,
        pv=[0.0] * 12,
        load=[1000.0] * 8 + [2000.0] * 4,
        battery=small_battery(),
        soc_pct=58.0,
    )
    intents = fallback_intents(inputs)
    held = [i for i, x in enumerate(intents) if x.kind == BatteryIntentKind.HALTEN]
    assert held == [1, 3]
    assert all(x.kind != BatteryIntentKind.HALTEN for x in intents[8:])


def test_holds_every_cheap_slot_when_the_battery_cannot_cover_the_evening_anyway() -> None:
    prices = [14.0, 12.0, 13.0, 12.5, 15.0, 15.0, 15.0, 15.0] + [40.0] * 4
    inputs = scenario(
        prices=prices, pv=[0.0] * 12, load=[2000.0] * 12, battery=small_battery(), soc_pct=40.0
    )
    intents = fallback_intents(inputs)
    held = [i for i, x in enumerate(intents) if x.kind == BatteryIntentKind.HALTEN]
    assert held == list(range(8))


def test_no_hold_when_soc_suffices() -> None:
    inputs = scenario(prices=EVENING, pv=[0.0] * 12, load=[1000.0] * 12, soc_pct=90.0)
    assert all(i.kind == BatteryIntentKind.EIGENVERBRAUCH for i in fallback_intents(inputs))


def test_grid_charge_needs_switch_spread_and_r1() -> None:
    base = dict(
        prices=[8.0] * 8 + [40.0] * 4,
        load=[2000.0] * 12,
        battery=small_battery(),
        soc_pct=20.0,
    )
    on = fallback_intents(scenario(pv=[0.0] * 12, grid_charge=True, **base))
    off = fallback_intents(scenario(pv=[0.0] * 12, grid_charge=False, **base))
    assert on[0] == BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=5000)
    assert all(i.kind != BatteryIntentKind.NETZLADEN for i in off)


def test_only_today_counts() -> None:
    inputs = scenario(
        prices=[20.0] * 88 + [60.0] * 8,
        pv=[0.0] * 96,
        load=[1500.0] * 96,
        soc_pct=30.0,
        start="2026-10-16T20:00:00+00:00",  # 22:00 lokal: teuer erst morgen
    )
    assert all(i.kind == BatteryIntentKind.EIGENVERBRAUCH for i in fallback_intents(inputs))


def test_expensive_today_is_the_top_fifth_of_today_and_not_a_flat_day() -> None:
    inputs = scenario(prices=EVENING, pv=[0.0] * 12, load=[1000.0] * 12)
    assert expensive_today(inputs) == [8, 9, 10, 11]
    flat = scenario(prices=[20.0] * 12, pv=[0.0] * 12, load=[1000.0] * 12)
    assert expensive_today(flat) == []


def test_grid_charge_needs_a_spread_over_wear_and_margin() -> None:
    # 40 · 0,88 − 35 − 2 = −1,8 ct: das Netzladen rechnet sich nicht.
    inputs = scenario(
        prices=[35.0] * 8 + [40.0] * 4,
        pv=[0.0] * 12,
        load=[2000.0] * 12,
        battery=small_battery(),
        soc_pct=20.0,
        grid_charge=True,
    )
    assert all(i.kind != BatteryIntentKind.NETZLADEN for i in fallback_intents(inputs))


def test_grid_charge_is_blocked_by_feed_in_after_it() -> None:
    base = dict(
        prices=[8.0] * 8 + [40.0] * 4 + [8.0] * 4,
        load=[2000.0] * 16,
        battery=small_battery(),
        soc_pct=20.0,
        grid_charge=True,
    )
    free = fallback_intents(scenario(pv=[0.0] * 16, **base))
    assert any(i.kind == BatteryIntentKind.NETZLADEN for i in free)
    # 8 kW PV nach dem Abend: 6 kW Überschuss bei 5 kW Ladeleistung speisen 1 kW ein (Regel R1).
    blocked = fallback_intents(scenario(pv=[0.0] * 12 + [8000.0] * 4, **base))
    assert all(i.kind != BatteryIntentKind.NETZLADEN for i in blocked)


def test_no_grid_charge_without_headroom_below_the_setpoint_limit() -> None:
    # Bei 7,95 kW Last bleiben bis zum höchsten Sollwert von 8 kW nur 50 W: kein NETZLADEN.
    inputs = scenario(
        prices=[8.0] * 8 + [40.0] * 4,
        pv=[0.0] * 12,
        load=[7950.0] * 12,
        battery=small_battery(),
        soc_pct=20.0,
        grid_charge=True,
    )
    assert all(i.kind != BatteryIntentKind.NETZLADEN for i in fallback_intents(inputs))
