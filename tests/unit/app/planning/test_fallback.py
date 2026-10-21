"""Tests des Ersatzplans (Präzisierung 10): Eigenverbrauch, Halten vor teuren Slots, Netzladen
nur mit Schalter, Spread-Regel und Regel R1."""

from __future__ import annotations

from datetime import datetime

import pytest
from tests.plan_helpers import (
    SCENARIO_START,
    at,
    planned_car,
    resimulate,
    scenario,
    small_battery,
    state,
)

from bindaems.app.planning.battery_model import SLOT_H, eta, params_of
from bindaems.app.planning.fallback import expensive_today, fallback_intents, fallback_vehicles
from bindaems.app.planning.plausibility import R1_TOL_KWH, export_while_charged_kwh
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind
from bindaems.shared.timeutil import SLOT

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
    # 0,88 · (40 − 2) − 35 = −1,56 ct < 2 ct Marge: das Netzladen rechnet sich nicht.
    inputs = scenario(
        prices=[35.0] * 8 + [40.0] * 4,
        pv=[0.0] * 12,
        load=[2000.0] * 12,
        battery=small_battery(),
        soc_pct=20.0,
        grid_charge=True,
    )
    assert all(i.kind != BatteryIntentKind.NETZLADEN for i in fallback_intents(inputs))


_R1_BASE = dict(
    prices=[8.0] * 8 + [40.0] * 4 + [8.0] * 4,
    load=[2000.0] * 16,
    battery=small_battery(),
    soc_pct=20.0,
    grid_charge=True,
)


def test_fallback_charges_what_the_evening_uses_before_pv() -> None:
    # PV ab Slot 12 (8 kW, 6 kW Überschuss bei 5 kW Ladeleistung): Die Netzladung der billigen
    # Slots ist am Abend (Slots 8–11) verbraucht, der Akku ist leer, bevor die PV einspeist.
    # Regel R1 gilt nur bis der Akku leer ist, also darf der Ersatzplan laden.
    inputs = scenario(pv=[0.0] * 12 + [8000.0] * 4, **_R1_BASE)
    intents = fallback_intents(inputs)
    charging = [i for i, x in enumerate(intents) if x.kind == BatteryIntentKind.NETZLADEN]
    assert charging
    steps = resimulate(inputs, intents)
    lo_kwh = params_of(inputs).lo_kwh
    assert all(export_while_charged_kwh(steps, i, lo_kwh) <= R1_TOL_KWH for i in charging)


def test_grid_charge_is_blocked_when_it_lasts_into_pv() -> None:
    # Das Haus braucht in den billigen Slots 0–7 nichts (Last 0), so bleibt jede Netzladung bis
    # zum teuren Block (Slots 8–11) im Akku. PV 8 kW ab Slot 9 speist dort 1 kW ein (6 kW
    # Überschuss bei 5 kW Ladeleistung). Die kleinste Ladung des Ersatzplans ist ein Slot mit voller
    # Leistung: 5 kW · 0,25 h · η_ch 0,938 = 1,17 kWh über der Reserve. Slot 8 (Last 2 kW)
    # entlädt 2 kW · 0,25 h / 0,938 = 0,53 kWh; bei Einspeisebeginn in Slot 9 bleiben 0,64 kWh,
    # mehr als die 0,1 kWh Toleranz des leeren Akkus. Jedes Netzladen wäre eine Einspeisung bei
    # geladenem Akku (Regel R1) und fällt weg.
    inputs = scenario(
        prices=[8.0] * 8 + [40.0] * 4 + [8.0] * 4,
        pv=[0.0] * 9 + [8000.0] * 7,
        load=[0.0] * 8 + [2000.0] * 8,
        battery=small_battery(),
        soc_pct=20.0,
        grid_charge=True,
    )
    params = params_of(inputs)
    smallest = [BatteryIntent()] * 7 + [
        BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=5000)
    ]
    smallest += [BatteryIntent()] * 8
    steps = resimulate(inputs, smallest)
    assert steps[8].soc_end_kwh - params.lo_kwh == pytest.approx(0.64, abs=0.01)
    assert export_while_charged_kwh(steps, 7, params.lo_kwh) > R1_TOL_KWH  # sonst hielte R1
    intents = fallback_intents(inputs)
    assert all(x.kind != BatteryIntentKind.NETZLADEN for x in intents)


def test_earlier_grid_charge_may_not_push_an_accepted_one_into_pv() -> None:
    # Slot 2 (8 ct) lädt zuerst für den teuren Slot 3; der leert den Akku bis 0,05 kWh über der
    # Reserve, bevor die PV in Slot 4 einspeist. Für den teuren Slot 5 käme danach Slot 0 dazu:
    # Slot 1 leert ihn bis 0,08 kWh über der Reserve, für sein eigenes Fenster ist der Akku also
    # leer. Diese 0,08 kWh trägt der Akku aber bis Slot 3 mit, der dann bei 0,13 kWh endet: Die
    # Ladung aus Slot 2 wäre bei der Einspeisung noch im Akku. Slot 0 und 1 laden darum nicht.
    probe = scenario(prices=[1.0] * 2, pv=[0.0] * 2, load=[0.0] * 2, battery=small_battery())
    efficiency = eta(probe.planning.roundtrip_pct)
    charge_kwh = 5000 * SLOT_H / 1000 * efficiency
    inputs = scenario(
        prices=[10.0, 10.0, 8.0, 40.0, 12.0, 40.0],
        pv=[0.0] * 4 + [9000.0, 0.0],
        load=[
            0.0,
            (charge_kwh - 0.08) * efficiency / SLOT_H * 1000,
            0.0,
            (charge_kwh - 0.05) * efficiency / SLOT_H * 1000,
            500.0,
            5000.0,
        ],
        battery=small_battery(),
        soc_pct=20.0,
        grid_charge=True,
    )
    intents = fallback_intents(inputs)
    charging = [i for i, x in enumerate(intents) if x.kind == BatteryIntentKind.NETZLADEN]
    assert charging == [2]
    steps = resimulate(inputs, intents)
    lo_kwh = params_of(inputs).lo_kwh
    assert export_while_charged_kwh(steps, 2, lo_kwh) <= R1_TOL_KWH


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


def test_grid_charge_spread_charges_wear_on_the_delivered_energy() -> None:
    # Gleiche Spread-Regel wie die Plausibilität: 0,88 · (40 − 2) − 31,3 = 2,14 ≥ 2 ct Marge
    # (mit w statt η_rt · w wären es 1,90 ct, und es gäbe kein Netzladen).
    inputs = scenario(
        prices=[31.3] * 8 + [40.0] * 4,
        pv=[0.0] * 12,
        load=[2000.0] * 12,
        battery=small_battery(),
        soc_pct=20.0,
        grid_charge=True,
    )
    assert fallback_intents(inputs)[0] == BatteryIntent(
        kind=BatteryIntentKind.NETZLADEN, grid_charge_w=5000
    )


# Geplante Autos (Plan 4b, Entscheidung 8)


def _slot(n: int) -> datetime:
    return at(SCENARIO_START) + n * SLOT


def test_fallback_charges_pv_first_then_cheapest_slots() -> None:
    # 5,5 kW Überschuss um 03:00 und 03:15 decken 2,75 kWh; der Rest (2,76 kWh) kommt aus dem
    # billigsten Slot davor (02:15, beim Gleichstand der frühere) mit voller Leistung. Nach der
    # Abfahrt (04:00) lädt es nicht.
    car = planned_car(departure_at=_slot(8), need_kwh=5.51)
    inputs = scenario(
        prices=[30.0, 10.0, 20.0, 10.0, 30.0, 30.0, 30.0, 30.0, 5.0, 5.0],
        pv=[0.0] * 4 + [6000.0] * 2 + [0.0] * 4,
        load=[500.0] * 10,
        vehicles=[car],
    )
    assert fallback_vehicles(inputs) == {
        "tesla": pytest.approx([0.0, 11040.0, 0.0, 0.0, 5500.0, 5500.0, 0.0, 0.0, 0.0, 0.0])
    }


def test_fallback_grid_only_covers_what_is_left_and_respects_min_power() -> None:
    # 1 kWh Rest: ein Slot mit 4000 W statt voller Leistung; Überschuss unter der
    # Mindestleistung (3450 W) zählt nicht
    car = planned_car(departure_at=_slot(4), need_kwh=1.0)
    inputs = scenario(
        prices=[20.0, 20.0, 10.0, 20.0],
        pv=[0.0, 2000.0, 0.0, 0.0],
        load=[500.0] * 4,
        vehicles=[car],
    )
    assert fallback_vehicles(inputs) == {"tesla": [0.0, 0.0, 4000.0, 0.0]}
    small = car.model_copy(update={"need_kwh": 0.1, "horizon_need_kwh": 0.1})
    inputs = inputs.model_copy(update={"vehicles": [small]})
    assert fallback_vehicles(inputs) == {"tesla": [0.0, 0.0, 3450.0, 0.0]}


def test_fallback_pv_beyond_the_horizon_need_and_grid_only_for_it() -> None:
    # Abfahrt nach dem Horizont: PV lädt bis zum ganzen Bedarf, das Netz nur, was im Horizont
    # nötig ist
    car = planned_car(departure_at=_slot(40), need_kwh=20.0, horizon_need_kwh=2.0)
    inputs = scenario(
        prices=[20.0, 10.0, 20.0, 20.0],
        pv=[0.0, 0.0, 6000.0, 6000.0],
        load=[500.0] * 4,
        vehicles=[car],
    )
    assert fallback_vehicles(inputs) == {"tesla": [0.0, 0.0, 5500.0, 5500.0]}
    more = car.model_copy(update={"horizon_need_kwh": 3.75})
    inputs = inputs.model_copy(update={"vehicles": [more]})
    assert fallback_vehicles(inputs) == {"tesla": pytest.approx([0.0, 4000.0, 5500.0, 5500.0])}


def test_fallback_shares_pv_and_grid_limit_between_cars_in_priority_order() -> None:
    golf = planned_car(
        key="egolf", name="e-Golf", wallbox="evcs", departure_at=_slot(2), need_kwh=0.9
    )
    tesla = planned_car(departure_at=_slot(2), need_kwh=2.76)
    inputs = scenario(
        prices=[10.0, 20.0],
        pv=[4100.0, 0.0],
        load=[500.0] * 2,
        vehicles=[golf, tesla],
        battery=state(grid_max_import_w=8000.0),
    )
    # Der e-Golf nimmt die PV (3600 W); der Tesla lädt im billigen Slot, aber nur bis zur
    # Grenze des Netzes (8000 W + 3600 W Überschuss − 3600 W e-Golf), den Rest danach
    assert fallback_vehicles(inputs) == {
        "egolf": pytest.approx([3600.0, 0.0]),
        "tesla": pytest.approx([8000.0, 3450.0]),
    }


def test_fallback_battery_ignores_the_car_import() -> None:
    # Das Auto bezieht im teuren Slot; das ist kein knapper Akku
    inputs = scenario(prices=EVENING, pv=[0.0] * 12, load=[1000.0] * 12, soc_pct=90.0)
    ev_w: list[dict[str, float]] = [{}] * 8 + [{"tesla": 11040.0}] * 4
    assert all(i.kind == BatteryIntentKind.EIGENVERBRAUCH for i in fallback_intents(inputs, ev_w))


def test_fallback_grid_charge_leaves_room_for_the_cars_below_the_import_limit() -> None:
    # 9 kW Netzbezug höchstens: 2 kW Haus und 4 kW Auto lassen 3 kW fürs Netzladen (sonst 5 kW)
    inputs = scenario(
        prices=[8.0] * 8 + [40.0] * 4,
        pv=[0.0] * 12,
        load=[2000.0] * 12,
        battery=small_battery(grid_max_import_w=9000.0),
        soc_pct=20.0,
        grid_charge=True,
    )
    ev_w: list[dict[str, float]] = [{"tesla": 4000.0}] * 8 + [{}] * 4
    assert fallback_intents(inputs, ev_w)[0] == BatteryIntent(
        kind=BatteryIntentKind.NETZLADEN, grid_charge_w=3000
    )
    assert fallback_intents(inputs)[0] == BatteryIntent(
        kind=BatteryIntentKind.NETZLADEN, grid_charge_w=5000
    )


def test_fallback_vehicles_skip_expected_cars() -> None:
    # Ruling T10-1: Ein erwartetes Auto lädt im Ersatzplan nie, und es nimmt dem angesteckten
    # weder PV noch Platz unter dem Netzbezug
    expected = planned_car(departure_at=_slot(4), need_kwh=5.0, expected=True, arrival_at=_slot(1))
    golf = planned_car(
        key="egolf", name="e-Golf", wallbox="evcs", departure_at=_slot(4), need_kwh=2.75
    )
    inputs = scenario(
        prices=[20.0] * 4, pv=[6000.0] * 4, load=[500.0] * 4, vehicles=[expected, golf]
    )
    powers = fallback_vehicles(inputs)
    assert "tesla" not in powers
    assert powers["egolf"] == pytest.approx([5500.0, 5500.0, 0.0, 0.0])
