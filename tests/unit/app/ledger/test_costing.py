from datetime import UTC, datetime, timedelta

import pytest
from tests.app_helpers import ingest, price_row

from bindaems.app.ledger.costing import LedgerCosting, SlotCost, cost_basis
from bindaems.app.ledger.service import LedgerSlot
from bindaems.shared.timeutil import SLOT

S = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)


def slot(index: int, flows: dict[str, float], price: float | None = None) -> LedgerSlot:
    return LedgerSlot(
        slot_start=S + index * SLOT,
        covered_s=900.0,
        flows_wh=flows,
        counters={},
        import_wh=sum(wh for name, wh in flows.items() if name.startswith("grid>")),
        export_wh=0.0,
        import_price_ct=price,
        origin="stream",
    )


def basis(slots: list[LedgerSlot], *, wear: float = 2.0, usable: float = 10.0) -> list[SlotCost]:
    return cost_basis(slots, (0.0, None, 0.0), wear_ct=wear, usable_kwh=usable)


def test_pv_charge_is_free() -> None:
    costs = basis([slot(0, {"pv>battery": 2000.0}), slot(1, {"battery>house": 1000.0})])
    assert costs[0].avg_ct == 0.0 and costs[0].stock_kwh == 2.0
    assert costs[1].discharge_ct == 2.0 and costs[1].stock_kwh == 1.0
    assert costs[1].slot_start == S + SLOT


def test_grid_and_pv_charge_mix_by_energy() -> None:
    costs = basis([slot(0, {"pv>battery": 1000.0, "grid>battery": 1000.0}, price=30.0)])
    assert costs[0].avg_ct == pytest.approx(15.0) and costs[0].stock_kwh == 2.0


def test_charge_mixes_with_existing_stock() -> None:
    costs = basis([slot(0, {"grid>battery": 1000.0}, 30.0), slot(1, {"pv>battery": 2000.0})])
    assert costs[1].avg_ct == pytest.approx(10.0) and costs[1].stock_kwh == 3.0


def test_discharge_uses_average_at_slot_start() -> None:
    costs = basis(
        [
            slot(0, {"grid>battery": 2000.0}, 20.0),
            slot(1, {"battery>house": 1000.0, "pv>battery": 3000.0}),
        ]
    )
    assert costs[1].discharge_ct == pytest.approx(22.0)  # 20 ct Mittelwert davor + 2 ct Verschleiß
    assert costs[1].stock_kwh == 4.0
    assert costs[1].avg_ct == pytest.approx(20.0 * 1.0 / 4.0)  # 1 kWh Rest zu 20 ct, 3 kWh PV


def test_empty_battery_discharge_costs_wear_only() -> None:
    costs = basis([slot(0, {"battery>house": 500.0})], wear=3.0)
    assert costs[0].discharge_ct == 3.0 and costs[0].stock_kwh == 0.0 and costs[0].avg_ct is None


def test_emptied_battery_loses_its_average() -> None:
    costs = basis([slot(0, {"grid>battery": 1000.0}, 30.0), slot(1, {"battery>house": 2000.0})])
    assert costs[1].stock_kwh == 0.0 and costs[1].avg_ct is None
    assert costs[1].discharge_ct == pytest.approx(32.0)


def test_discharge_to_wallbox_counts_too() -> None:
    costs = basis([slot(0, {"pv>battery": 2000.0}), slot(1, {"battery>wb:evcs": 1500.0})])
    assert costs[1].stock_kwh == 0.5


def test_stock_is_capped_at_usable_capacity() -> None:
    costs = basis(
        [slot(0, {"grid>battery": 4000.0}, 10.0), slot(1, {"pv>battery": 8000.0})], usable=10.0
    )
    assert costs[1].stock_kwh == 10.0
    assert costs[1].avg_ct == pytest.approx(10.0 * 4.0 / 12.0)  # gewichtet vor dem Kappen


def test_grid_charge_without_price_makes_the_stock_unknown() -> None:
    # A1: solange Energie aus einer Netzladung ohne Bezugspreis mindestens 1 % des Inhalts ist, ist
    # der Einstandspreis unbekannt, auch nach weiterer PV- oder bepreister Netzladung
    costs = basis(
        [
            slot(0, {"grid>battery": 1000.0}, 20.0),
            slot(1, {"grid>battery": 3000.0}, None),
            slot(2, {"battery>house": 1000.0}),
            slot(3, {"pv>battery": 1000.0, "grid>battery": 500.0}, 30.0),
            slot(4, {"battery>house": 1000.0}),
        ]
    )
    assert costs[1].stock_kwh == 4.0 and costs[1].unpriced_kwh == 3.0
    assert costs[1].avg_ct == pytest.approx(20.0)  # nur die bepreiste kWh
    assert costs[2].discharge_ct is None and costs[2].stock_kwh == 3.0
    assert costs[2].unpriced_kwh == pytest.approx(2.25)  # anteilig mit entladen
    assert costs[3].stock_kwh == 4.5 and costs[3].unpriced_kwh == pytest.approx(2.25)
    assert costs[3].avg_ct == pytest.approx((0.75 * 20.0 + 0.5 * 30.0) / 2.25)
    assert costs[4].discharge_ct is None


def test_grid_charge_without_price_into_an_empty_battery() -> None:
    costs = basis([slot(0, {"grid>battery": 1000.0}, None), slot(1, {"battery>house": 500.0})])
    assert costs[0].avg_ct is None and costs[0].stock_kwh == 1.0
    assert costs[0].discharge_ct == 2.0  # zu Beginn des Slots war der Akku leer
    assert costs[1].discharge_ct is None and costs[1].stock_kwh == 0.5


def test_unknown_stock_is_known_again_once_emptied() -> None:
    costs = basis(
        [
            slot(0, {"grid>battery": 1000.0}, None),
            slot(1, {"battery>house": 1000.0}),
            slot(2, {"battery>house": 500.0}),
            slot(3, {"pv>battery": 1000.0}),
            slot(4, {"battery>house": 500.0}),
        ]
    )
    assert costs[1].discharge_ct is None and costs[1].stock_kwh == 0.0
    assert costs[1].avg_ct is None
    assert costs[2].discharge_ct == 2.0  # leer und bekannt: nur Verschleiß
    assert costs[3].avg_ct == 0.0
    assert costs[4].discharge_ct == 2.0


DAY_SLOTS = 96


def daily_cycle(days: int, extra: dict[int, LedgerSlot]) -> list[LedgerSlot]:
    """Je Tag 8 kWh PV in den Akku (Slot 0) und 7,2 kWh heraus (Slot 40); ``extra`` je Index."""
    slots: dict[int, LedgerSlot] = {}
    for day in range(days):
        slots[day * DAY_SLOTS] = slot(day * DAY_SLOTS, {"pv>battery": 8000.0})
        slots[day * DAY_SLOTS + 40] = slot(day * DAY_SLOTS + 40, {"battery>house": 7200.0})
    slots.update(extra)
    return [slots[index] for index in sorted(slots)]


def discharges(costs: list[SlotCost], day: int) -> list[float | None]:
    """``discharge_ct`` der Entladung am Tag ``day`` (Slot 40) und aller späteren."""
    return [c.discharge_ct for c in costs if (c.slot_start - S) // SLOT % DAY_SLOTS == 40][day:]


def test_a_few_wh_without_price_do_not_make_the_stock_unknown() -> None:
    # N2 (Fall aus dem Review): 10-kWh-Akku, 8 kWh rein und 7,2 kWh raus je Tag; der Bestand der
    # Buchhaltung fällt nie unter 2,8 kWh. 10 Wh Netzladung ohne Preis an Tag 5 sind 0,4 % davon.
    night = 5 * DAY_SLOTS + 60
    costs = basis(daily_cycle(30, {night: slot(night, {"grid>battery": 10.0}, None)}))
    assert min(c.stock_kwh for c in costs[10:]) == pytest.approx(2.8)
    assert all(ct == pytest.approx(2.0) for ct in discharges(costs, 6))  # nur PV: 0 ct + Verschleiß


def test_unpriced_share_is_known_again_after_a_few_days() -> None:
    # 1 kWh ohne Preis bei 2,8 kWh Bestand: 26 %; jede PV-Ladung verdünnt den Anteil
    night = 5 * DAY_SLOTS + 60
    costs = basis(daily_cycle(30, {night: slot(night, {"grid>battery": 1000.0}, None)}))
    assert costs[-1].unpriced_kwh < 1e-6
    by_day = discharges(costs, 0)
    assert by_day[6] is None  # 8,5 % ohne Preis
    assert by_day[7] is None  # 2,2 %
    assert all(ct == pytest.approx(2.0) for ct in by_day[8:])  # 0,6 %: wieder bekannt


def test_large_unpriced_share_stays_unknown() -> None:
    slots = [slot(0, {"grid>battery": 6000.0}, None), slot(1, {"pv>battery": 1000.0})]
    for day in range(1, 11):
        slots.append(slot(day * DAY_SLOTS, {"pv>battery": 200.0}))
        slots.append(slot(day * DAY_SLOTS + 40, {"battery>house": 500.0}))
    costs = basis(slots)
    assert all(c.discharge_ct is None for c in costs[2:])
    assert costs[-1].unpriced_kwh / costs[-1].stock_kwh > 0.5


def test_unpriced_share_of_one_percent_is_unknown() -> None:
    [at_limit] = cost_basis(
        [slot(0, {"battery>house": 1000.0})], (10.0, 10.0, 0.1), wear_ct=2.0, usable_kwh=10.0
    )
    [below] = cost_basis(
        [slot(0, {"battery>house": 1000.0})], (10.0, 10.0, 0.0999), wear_ct=2.0, usable_kwh=10.0
    )
    assert at_limit.discharge_ct is None
    assert below.discharge_ct == pytest.approx(12.0)  # Durchschnitt der bepreisten Energie


def test_discharge_and_cap_take_the_unpriced_share_proportionally() -> None:
    [discharged] = cost_basis(
        [slot(0, {"battery>house": 5000.0})], (10.0, 20.0, 2.0), wear_ct=2.0, usable_kwh=10.0
    )
    assert discharged.stock_kwh == 5.0 and discharged.unpriced_kwh == pytest.approx(1.0)
    assert discharged.avg_ct == pytest.approx(20.0)
    [capped] = cost_basis(
        [slot(0, {"pv>battery": 4000.0})], (8.0, 20.0, 4.0), wear_ct=2.0, usable_kwh=10.0
    )
    assert capped.stock_kwh == 10.0 and capped.unpriced_kwh == pytest.approx(4.0 * 10.0 / 12.0)
    assert capped.avg_ct == pytest.approx(10.0)  # 4 kWh zu 20 ct, 4 kWh PV zu 0 ct


def test_unknown_start_state_is_taken_over() -> None:
    [cost] = cost_basis(
        [slot(0, {"battery>house": 1000.0})], (5.0, None, 5.0), wear_ct=2.0, usable_kwh=10.0
    )
    assert cost.discharge_ct is None and cost.stock_kwh == 4.0 and cost.avg_ct is None
    assert cost.unpriced_kwh == pytest.approx(4.0)


def test_start_state_is_taken_over() -> None:
    [cost] = cost_basis(
        [slot(0, {"battery>house": 1000.0})], (5.0, 12.0, 0.0), wear_ct=2.0, usable_kwh=10.0
    )
    assert cost.discharge_ct == 14.0 and cost.stock_kwh == 4.0 and cost.avg_ct == 12.0


def test_slot_without_battery_flows_carries_state() -> None:
    costs = basis([slot(0, {"pv>battery": 1000.0}), slot(1, {"pv>house": 500.0})])
    assert costs[1].stock_kwh == 1.0 and costs[1].avg_ct == 0.0


# --- LedgerCosting ---------------------------------------------------------------------------


def build(engine, ledger, settings_service) -> LedgerCosting:
    """Wie runtime.py: der Dienst hört auf die Abrechnung."""
    costing = LedgerCosting(engine, ledger, settings_service, usable_kwh=10.0)
    ledger.on_slots_changed(costing.mark_dirty)
    return costing


@pytest.fixture
def costing(engine, ledger, settings_service) -> LedgerCosting:
    return build(engine, ledger, settings_service)


def test_costs_computes_on_first_read(costing, ledger) -> None:
    ingest(ledger, S, {"pv>battery": 2000.0})
    ingest(ledger, S + SLOT, {"battery>house": 1000.0})
    costs = costing.costs(S, S + 2 * SLOT)
    assert set(costs) == {S, S + SLOT}
    assert costs[S + SLOT].discharge_ct == 2.0 and costs[S + SLOT].stock_kwh == 1.0


def test_catch_up_writes_nothing_when_clean(costing, ledger) -> None:
    ingest(ledger, S, {"pv>battery": 2000.0})
    assert costing.catch_up() == 1
    assert costing.catch_up() == 0


def test_catch_up_recomputes_from_the_earliest_dirty_slot(costing, ledger) -> None:
    for index in range(4):
        ingest(ledger, S + index * SLOT, {"pv>battery": 1000.0})
    assert costing.catch_up() == 4
    ingest(ledger, S + 5 * SLOT, {"pv>battery": 1000.0})  # neuer Slot am Ende
    assert costing.catch_up() == 1
    ingest(ledger, S + 4 * SLOT, {"grid>battery": 1000.0})  # Nachtrag mitten drin
    assert costing.catch_up() == 2  # ab Slot 4: 4 und 5
    costs = costing.costs(S, S + 6 * SLOT)
    assert costs[S + 5 * SLOT].stock_kwh == 6.0
    assert S + 4 * SLOT in costs


def test_replaced_slot_marks_dirty(costing, ledger) -> None:
    ingest(ledger, S, {"pv>battery": 2000.0}, covered_s=300.0)
    ingest(ledger, S + SLOT, {"battery>house": 1000.0})
    assert costing.costs(S, S + 2 * SLOT)[S + SLOT].stock_kwh == 1.0
    ingest(ledger, S, {"pv>battery": 4000.0}, covered_s=900.0)  # größere Abdeckung ersetzt
    assert costing.costs(S, S + 2 * SLOT)[S + SLOT].stock_kwh == 3.0


def test_rejected_ingest_does_not_mark_dirty(costing, ledger) -> None:
    ingest(ledger, S, {"pv>battery": 2000.0})
    costing.catch_up()
    assert not ingest(ledger, S, {"pv>battery": 9000.0})
    assert costing.catch_up() == 0


def test_settings_change_recomputes_all(costing, ledger, settings_service) -> None:
    ingest(ledger, S, {"pv>battery": 2000.0})
    ingest(ledger, S + SLOT, {"battery>house": 500.0})
    ingest(ledger, S + 2 * SLOT, {"battery>house": 500.0})
    assert costing.costs(S, S + 3 * SLOT)[S + 2 * SLOT].discharge_ct == 2.0
    current = settings_service.current()
    planning = current.settings.planning.model_copy(update={"wear_ct": 5.0})
    settings_service.update(
        current.settings.model_copy(update={"planning": planning}),
        base_version=current.version,
        actor="t",
        source="ui",
    )
    costs = costing.costs(S, S + 3 * SLOT)
    assert [costs[S + i * SLOT].discharge_ct for i in range(3)] == [5.0, 5.0, 5.0]


def test_other_settings_change_does_not_recompute(costing, ledger, settings_service) -> None:
    ingest(ledger, S, {"pv>battery": 2000.0})
    costing.catch_up()
    current = settings_service.current()
    planning = current.settings.planning.model_copy(update={"margin_ct": 7.0})
    settings_service.update(
        current.settings.model_copy(update={"planning": planning}),
        base_version=current.version,
        actor="t",
        source="ui",
    )
    assert costing.catch_up() == 0


def test_reprice_recomputes_from_the_first_changed_slot(
    costing, ledger, store, clock, settings_service
) -> None:
    ingest(ledger, S, {"grid>battery": 1000.0})  # ohne Preis
    ingest(ledger, S + SLOT, {"pv>battery": 1000.0})
    assert costing.costs(S, S + 2 * SLOT)[S].avg_ct is None
    store.save([price_row(S, 10.0, "primary")])
    assert ledger.fill_missing_prices() == 1
    costs = costing.costs(S, S + 2 * SLOT)
    assert costs[S].avg_ct == pytest.approx(13.44)
    assert costs[S + SLOT].avg_ct == pytest.approx(13.44 / 2)


def test_discharge_is_unknown_until_the_price_is_filled_in(costing, ledger, store) -> None:
    ingest(ledger, S, {"grid>battery": 1000.0})  # ohne Preis
    ingest(ledger, S + SLOT, {"battery>wb:evcs": 500.0})
    assert costing.costs(S, S + 2 * SLOT)[S + SLOT].discharge_ct is None
    store.save([price_row(S, 10.0, "primary")])
    assert ledger.fill_missing_prices() == 1
    assert costing.costs(S, S + 2 * SLOT)[S + SLOT].discharge_ct == pytest.approx(13.44 + 2.0)


def test_fresh_database_computes_from_the_first_slot(engine, ledger, settings_service) -> None:
    ingest(ledger, S, {"pv>battery": 2000.0})
    ingest(ledger, S + SLOT, {"battery>house": 1000.0})
    restarted = build(engine, ledger, settings_service)
    assert restarted.catch_up() == 2


def test_restart_continues_after_the_last_cost_row(engine, ledger, settings_service) -> None:
    first = build(engine, ledger, settings_service)
    ingest(ledger, S, {"pv>battery": 2000.0})
    first.catch_up()
    ingest(ledger, S + SLOT, {"battery>house": 1000.0})  # während die Nachführung nicht lief
    restarted = build(engine, ledger, settings_service)
    assert restarted.catch_up() == 1
    assert restarted.costs(S, S + 2 * SLOT)[S + SLOT].stock_kwh == 1.0


def set_wear(settings_service, wear_ct: float) -> None:
    current = settings_service.current()
    planning = current.settings.planning.model_copy(update={"wear_ct": wear_ct})
    settings_service.update(
        current.settings.model_copy(update={"planning": planning}),
        base_version=current.version,
        actor="t",
        source="ui",
    )


def test_restart_recomputes_from_a_slot_changed_in_the_middle(
    engine, ledger, settings_service, clock
) -> None:
    # B2: die Markierung lag nur im Speicher; ein Neustart vor der Nachführung ließ sie verfallen
    first = build(engine, ledger, settings_service)
    ingest(ledger, S, {"pv>battery": 1000.0}, covered_s=300.0)
    for index in range(1, 4):
        ingest(ledger, S + index * SLOT, {"battery>house": 200.0})
    first.catch_up()
    clock.advance(timedelta(seconds=1))
    ingest(ledger, S, {"pv>battery": 3000.0}, covered_s=900.0)  # ersetzt, dann Absturz
    restarted = build(engine, ledger, settings_service)
    assert restarted.catch_up() == 4
    assert restarted.costs(S, S + 4 * SLOT)[S + 3 * SLOT].stock_kwh == pytest.approx(2.4)


def test_restart_recomputes_after_a_price_was_filled_in(
    engine, ledger, store, settings_service, clock
) -> None:
    first = build(engine, ledger, settings_service)
    ingest(ledger, S, {"pv>battery": 1000.0})
    ingest(ledger, S + SLOT, {"grid>battery": 1000.0})  # ohne Preis
    ingest(ledger, S + 2 * SLOT, {"battery>house": 500.0})
    first.catch_up()
    clock.advance(timedelta(seconds=1))
    store.save([price_row(S + SLOT, 10.0, "primary")])
    assert ledger.fill_missing_prices() == 1  # dann Neustart vor der Nachführung
    restarted = build(engine, ledger, settings_service)
    assert restarted.catch_up() == 2  # ab dem neu bewerteten Slot
    assert restarted.costs(S, S + 3 * SLOT)[S + 2 * SLOT].discharge_ct == pytest.approx(
        13.44 / 2 + 2.0
    )


def test_restart_recomputes_after_the_wear_changed(engine, ledger, settings_service) -> None:
    first = build(engine, ledger, settings_service)
    ingest(ledger, S, {"pv>battery": 2000.0})
    ingest(ledger, S + SLOT, {"battery>house": 500.0})
    first.catch_up()
    set_wear(settings_service, 5.0)  # dann Neustart vor der Nachführung
    restarted = build(engine, ledger, settings_service)
    assert restarted.catch_up() == 2
    assert restarted.costs(S, S + 2 * SLOT)[S + SLOT].discharge_ct == 5.0


def test_restart_recomputes_after_the_usable_capacity_changed(
    engine, ledger, settings_service
) -> None:
    first = build(engine, ledger, settings_service)
    ingest(ledger, S, {"pv>battery": 8000.0})
    ingest(ledger, S + SLOT, {"pv>battery": 4000.0})
    first.catch_up()
    smaller = LedgerCosting(engine, ledger, settings_service, usable_kwh=6.0)
    assert smaller.catch_up() == 2
    assert smaller.costs(S, S + 2 * SLOT)[S + SLOT].stock_kwh == 6.0


def test_restart_recomputes_rows_lost_in_an_interrupted_catch_up(
    engine, ledger, settings_service
) -> None:
    from sqlalchemy import delete

    from bindaems.app.db.schema import ledger_cost

    first = build(engine, ledger, settings_service)
    for index in range(4):
        ingest(ledger, S + index * SLOT, {"pv>battery": 1000.0})
    first.catch_up()
    with engine.begin() as conn:  # wie ein Abbruch zwischen zwei Blöcken
        conn.execute(delete(ledger_cost).where(ledger_cost.c.slot_start == S + SLOT))
    restarted = build(engine, ledger, settings_service)
    assert restarted.catch_up() == 3
    assert restarted.costs(S, S + 4 * SLOT)[S + 3 * SLOT].stock_kwh == 4.0


def test_restart_without_changes_recomputes_nothing(engine, ledger, settings_service) -> None:
    first = build(engine, ledger, settings_service)
    ingest(ledger, S, {"pv>battery": 1000.0})
    ingest(ledger, S + SLOT, {"battery>house": 500.0})
    first.catch_up()
    assert build(engine, ledger, settings_service).catch_up() == 0


def test_empty_ledger_has_nothing_to_do(costing) -> None:
    assert costing.catch_up() == 0
    assert costing.costs(S, S + SLOT) == {}


def test_long_range_is_computed_in_blocks(costing, ledger) -> None:
    for index in range(3):
        ingest(ledger, S + timedelta(days=7 * index), {"pv>battery": 1000.0})
    assert costing.catch_up() == 3
    costs = costing.costs(S, S + timedelta(days=15))
    assert [c.stock_kwh for c in costs.values()] == [1.0, 2.0, 3.0]


def test_listener_failure_does_not_break_ingest(ledger) -> None:
    def boom(_start: datetime) -> None:
        raise RuntimeError("kaputt")

    ledger.on_slots_changed(boom)
    assert ingest(ledger, S, {"pv>battery": 1000.0})


def test_failed_catch_up_stays_dirty(costing, ledger, monkeypatch) -> None:
    ingest(ledger, S, {"pv>battery": 1000.0})
    monkeypatch.setattr(ledger, "slots", lambda *_: (_ for _ in ()).throw(OSError("weg")))
    with pytest.raises(OSError):
        costing.catch_up()
    monkeypatch.undo()
    assert costing.catch_up() == 1
