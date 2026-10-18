from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import event
from tests.app_helpers import (
    SLOT_FLOWS_MSG,
    ingest,
    price_row,
    set_feed_in,
    set_grid_usage,
)

from bindaems.app.ledger.service import MonthPeak, parse_slot_flows
from bindaems.shared.domain import SlotFlows
from bindaems.shared.settings import FeedInComponent, TimeWindow
from bindaems.shared.timeutil import SLOT

S = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
MSG = SLOT_FLOWS_MSG


@pytest.mark.parametrize(
    "change",
    [
        {"slot_start": "2026-10-09T08:07:00+00:00"},
        {"slot_start": "2026-10-09T08:00:00"},
        {"covered_s": 901.0},
        {"flows_wh": {"pv>house": -1.0}},
    ],
)
def test_parse_slot_flows_validates_message(change) -> None:
    with pytest.raises(ValueError):
        parse_slot_flows(MSG | change)


def test_parse_slot_flows_reads_counters() -> None:
    flows = parse_slot_flows(MSG | {"counters": {"x.energy_kwh": [None, 3.0]}})
    assert flows.slot_start == S and flows.counters == {"x.energy_kwh": (None, 3.0)}


def test_ingest_stores_slot_with_import_price_and_mirrors_to_influx(
    ledger, prices_with_10ct, sink
) -> None:
    assert ledger.ingest(parse_slot_flows(MSG), "stream")
    [slot] = ledger.slots(S, S + SLOT)
    assert (slot.import_wh, slot.export_wh, slot.import_price_ct) == (
        400.0,
        300.0,
        pytest.approx(13.44),
    )
    assert slot.origin == "stream" and slot.counters["grid.energy_import_kwh"] == (100.0, 100.4)
    grid_house = next(p for p in sink.points if p.tags.get("flow") == "grid>house")
    assert grid_house.rp == "long" and grid_house.fields["kwh"] == 0.15
    assert grid_house.fields["eur"] == pytest.approx(0.15 * 13.44 / 100)
    pv_house = next(p for p in sink.points if p.tags.get("flow") == "pv>house")
    assert "eur" not in pv_house.fields


def test_export_points_carry_negative_revenue(ledger, feed_in_7ct, sink) -> None:
    ledger.ingest(parse_slot_flows(MSG), "stream")
    pv_grid = next(p for p in sink.points if p.tags.get("flow") == "pv>grid")
    assert pv_grid.fields["eur"] == pytest.approx(-0.3 * 7.0 / 100)


def test_ingest_keeps_record_with_larger_coverage(ledger) -> None:
    assert ledger.ingest(parse_slot_flows(MSG | {"covered_s": 600.0}), "stream")
    assert not ledger.ingest(parse_slot_flows(MSG | {"covered_s": 500.0}), "influx")
    assert ledger.ingest(parse_slot_flows(MSG), "influx")
    assert ledger.slots(S, S + SLOT)[0].covered_s == 900.0
    assert ledger.slots(S, S + SLOT)[0].origin == "influx"


def test_replacing_a_slot_zeroes_the_flows_it_no_longer_has(
    ledger, prices_with_10ct, feed_in_7ct, sink
) -> None:
    estimate = {"pv>battery": 500.0, "grid>house": 200.0, "pv>grid": 100.0, "pv>house": 100.0}
    assert ledger.ingest(SlotFlows(S, 0.0, estimate, {}), "estimate")
    sink.points.clear()
    assert ledger.ingest(SlotFlows(S, 900.0, {"pv>house": 300.0}, {}), "influx")
    points = {p.tags["flow"]: p for p in sink.points}
    assert {flow: p.fields for flow, p in points.items()} == {
        "pv>house": {"kwh": 0.3},
        "pv>battery": {"kwh": 0.0},  # die Schätzung hatte den Fluss, der echte Slot nicht
        "grid>house": {"kwh": 0.0, "eur": 0.0},
        "pv>grid": {"kwh": 0.0, "eur": 0.0},
    }
    assert all(p.ts == S and p.rp == "long" for p in points.values())
    sink.points.clear()
    assert not ledger.ingest(SlotFlows(S, 600.0, {"pv>battery": 1.0}, {}), "stream")
    assert sink.points == []  # nicht ersetzt: nichts zu schreiben


async def test_backfill_fills_slots_missed_while_app_was_down(ledger, respx_mock, reader) -> None:
    ledger.ingest(parse_slot_flows(MSG), "stream")
    route = respx_mock.get("http://influx.lan:8086/query").respond(
        json={
            "results": [
                {
                    "series": [
                        {
                            "name": "flows",
                            "tags": {"flow": "_coverage"},
                            "columns": ["time", "wh", "covered_s"],
                            "values": [[1791532800000, None, 900.0], [1791533700000, None, 880.0]],
                        },
                        {
                            "name": "flows",
                            "tags": {"flow": "grid>house"},
                            "columns": ["time", "wh", "covered_s"],
                            "values": [[1791532800000, 150.0, None], [1791533700000, 210.0, None]],
                        },
                    ]
                }
            ]
        }
    )
    assert await ledger.backfill(reader, S, S + 2 * SLOT) == 1
    assert ledger.slots(S + SLOT, S + 2 * SLOT)[0].flows_wh == {"grid>house": 210.0}
    assert route.calls.last.request.url.params["q"] == (
        'SELECT "wh", "covered_s" FROM "raw"."flows" WHERE time >= 1791532800000ms '
        'AND time < 1791534600000ms GROUP BY "flow"'
    )


async def test_backfill_skips_slots_without_coverage(ledger, respx_mock, reader) -> None:
    respx_mock.get("http://influx.lan:8086/query").respond(
        json={
            "results": [
                {
                    "series": [
                        {
                            "name": "flows",
                            "tags": {"flow": "grid>house"},
                            "columns": ["time", "wh", "covered_s"],
                            "values": [[1791532800000, 150.0, None]],
                        }
                    ]
                }
            ]
        }
    )
    assert await ledger.backfill(reader, S, S + SLOT) == 0


def test_day_summary_energy_balance_and_kpis(ledger, clock, prices_with_10ct, feed_in_7ct) -> None:
    ledger.ingest(parse_slot_flows(MSG), "stream")
    clock.advance(SLOT)  # 10:15 Ortszeit, die Viertelstunde der Nachricht ist vorbei
    [day] = ledger.days(date(2026, 10, 9), date(2026, 10, 9))
    assert (day.pv_kwh, day.import_kwh, day.export_kwh) == (1.0, 0.4, 0.3)
    assert (day.house_kwh, dict(day.wallbox_kwh)) == (0.6, {"evcs": 0.2, "twc": 0.25})
    assert (day.battery_charge_kwh, day.battery_discharge_kwh, day.consumption_kwh) == (
        0.1,
        0.05,
        1.05,
    )
    assert day.counter_kwh["grid.energy_import_kwh"] == pytest.approx(0.4)
    # 41 Viertelstunden sind vorbei, der Tag ist nicht fertig
    assert (day.slots, day.expected_slots, day.complete) == (1, 41, False)
    assert day.coverage == pytest.approx(1 / 41)
    assert day.cost_eur == pytest.approx(0.4 * 0.1344)
    assert day.revenue_eur == pytest.approx(0.3 * 0.07)
    assert day.autarky == pytest.approx(1 - 0.4 / 1.05) and day.self_consumption == pytest.approx(
        0.7
    )


def test_savings_follow_spec_formulas(ledger, prices_with_10ct, feed_in_7ct) -> None:
    ledger.ingest(parse_slot_flows(MSG), "stream")
    [day] = ledger.days(date(2026, 10, 9), date(2026, 10, 9))
    net_cost = 0.4 * 0.1344 - 0.3 * 0.07
    assert day.net_cost_eur == pytest.approx(net_cost)
    assert day.savings_no_plant_eur == pytest.approx(1.05 * 0.30 - net_cost)
    assert day.savings_same_import_eur == pytest.approx(0.4 * 0.30 - 0.3 * 0.07 - net_cost)


def test_day_summary_on_dst_day_expects_100_slots(ledger, clock) -> None:
    clock.advance(timedelta(days=17))  # 26.10.
    [day] = ledger.days(date(2026, 10, 25), date(2026, 10, 25))
    assert (day.expected_slots, day.slots, day.coverage, day.complete) == (100, 0, 0.0, True)
    assert day.autarky is None and day.self_consumption is None


def test_cost_unknown_when_import_slot_has_no_price(ledger) -> None:
    ledger.ingest(parse_slot_flows(MSG), "stream")  # keine Preise gespeichert
    assert ledger.days(date(2026, 10, 9), date(2026, 10, 9))[0].cost_eur is None


def test_revenue_follows_feed_in_setting_at_read_time(
    ledger, prices_with_10ct, settings_service
) -> None:
    ledger.ingest(parse_slot_flows(MSG), "stream")
    assert ledger.days(date(2026, 10, 9), date(2026, 10, 9))[0].revenue_eur is None
    set_feed_in(settings_service, {"2026-10": 8.0})
    assert ledger.days(date(2026, 10, 9), date(2026, 10, 9))[0].revenue_eur == pytest.approx(
        0.3 * 0.08
    )


def _set_windowed_feed_in(settings_service, month: str, start: time, end: time) -> None:
    """OeMAG 7,0 ct und ein Einspeisebestandteil, der im Fenster 5,0 ct statt 0 gilt."""
    set_feed_in(settings_service, {month: 7.0})
    settings = settings_service.current().settings
    bonus = FeedInComponent(
        id="bonus",
        name="Bonus",
        value_ct=0.0,
        windows=[TimeWindow(start=start, end=end, value_ct=5.0)],
    )
    tariff = settings.tariff.model_copy(update={"components": [*settings.tariff.components, bonus]})
    settings_service.update(
        settings.model_copy(update={"tariff": tariff}),
        base_version=settings_service.current().version,
        actor="test",
        source="ui",
    )


@pytest.mark.parametrize(
    ("day", "month", "utc_offset_h"),
    [
        (date(2026, 10, 9), "2026-10", 2),
        (date(2026, 3, 29), "2026-03", 2),  # Sommerzeit beginnt um 02:00 Ortszeit
        (date(2026, 10, 25), "2026-10", 1),  # Winterzeit beginnt um 03:00 Ortszeit
    ],
)
def test_revenue_applies_feed_in_window_per_slot(
    ledger, settings_service, day, month, utc_offset_h
) -> None:
    _set_windowed_feed_in(settings_service, month, time(10), time(16))
    export = {"pv>grid": 1000.0}
    midnight = datetime.combine(day, time(0), tzinfo=UTC) - timedelta(hours=utc_offset_h)
    ingest(ledger, midnight + timedelta(hours=12), export)  # 12:00 Ortszeit, im Fenster
    [inside] = ledger.days(day, day)
    assert inside.revenue_eur == pytest.approx(0.12)
    ingest(ledger, midnight + timedelta(hours=8), export)  # 08:00 Ortszeit, außerhalb
    [both] = ledger.days(day, day)
    assert both.revenue_eur == pytest.approx(0.12 + 0.07)
    assert both.net_cost_eur is None or both.net_cost_eur == pytest.approx(-both.revenue_eur)


def test_revenue_applies_window_in_both_hours_of_repeated_hour(ledger, settings_service) -> None:
    _set_windowed_feed_in(settings_service, "2026-10", time(2), time(3))
    export = {"pv>grid": 1000.0}
    ingest(ledger, "2026-10-25T00:30:00+00:00", export)  # 02:30 CEST
    ingest(ledger, "2026-10-25T01:30:00+00:00", export)  # 02:30 CET
    ingest(ledger, "2026-10-25T02:30:00+00:00", export)  # 03:30 CET, außerhalb
    [day] = ledger.days(date(2026, 10, 25), date(2026, 10, 25))
    assert day.revenue_eur == pytest.approx(0.12 + 0.12 + 0.07)


def test_revenue_unknown_when_any_export_slot_has_no_price(ledger, settings_service) -> None:
    set_feed_in(settings_service, {"2026-10": 7.0})
    ingest(ledger, "2026-10-09T08:00:00+00:00", {"pv>grid": 1000.0})
    assert ledger.days(date(2026, 10, 9), date(2026, 10, 9))[0].revenue_eur == pytest.approx(0.07)
    ingest(ledger, "2026-09-30T08:00:00+00:00", {"pv>grid": 1000.0})  # vor dem ersten OeMAG-Wert
    assert ledger.days(date(2026, 9, 30), date(2026, 9, 30))[0].revenue_eur is None


def test_fill_missing_prices_and_reprice(ledger, store, settings_service, audit, sink) -> None:
    ledger.ingest(parse_slot_flows(MSG), "stream")
    store.save([price_row(S, 10.0, "primary")])
    sink.points.clear()
    assert ledger.fill_missing_prices() == 1
    assert ledger.slots(S, S + SLOT)[0].import_price_ct == pytest.approx(13.44)
    assert any("eur" in p.fields for p in sink.points)  # Kosten nachgetragen
    assert ledger.fill_missing_prices() == 0
    set_grid_usage(settings_service, 8.0)
    assert ledger.reprice(date(2026, 10, 9), date(2026, 10, 9), actor="chris", source="ui") == 1
    assert ledger.slots(S, S + SLOT)[0].import_price_ct == pytest.approx(23.04)
    entry = audit.recent()[0]
    assert entry.action == "ledger.reprice"
    assert entry.details == {"from": "2026-10-09", "to": "2026-10-09", "slots": 1}


def test_reprice_zeroes_the_euros_of_a_slot_that_lost_its_price(
    ledger, prices_with_10ct, feed_in_7ct, settings_service, sink, monkeypatch
) -> None:
    # B7a: ohne Preis bliebe in Influx der alte Euro-Wert stehen; wie bei weggefallenen Flüssen 0
    ledger.ingest(parse_slot_flows(MSG), "stream")
    sink.points.clear()
    monkeypatch.setattr(ledger._prices, "slots", lambda start, end: [])  # Preis weg
    set_feed_in(settings_service, {})  # Einspeisevergütung weg
    assert ledger.reprice(date(2026, 10, 9), date(2026, 10, 9), actor="chris", source="ui") == 1
    assert ledger.slots(S, S + SLOT)[0].import_price_ct is None
    points = {p.tags["flow"]: p.fields for p in sink.points}
    assert points["grid>house"] == {"kwh": 0.15, "eur": 0.0}
    assert points["grid>wb:twc"] == {"kwh": 0.25, "eur": 0.0}
    assert points["pv>grid"] == {"kwh": 0.3, "eur": 0.0}
    assert points["pv>house"] == {"kwh": 0.4}  # ohne Kosten und Erlös auch ohne eur


def test_first_ingest_without_price_writes_no_euros(ledger, sink) -> None:
    ledger.ingest(parse_slot_flows(MSG), "stream")
    assert all("eur" not in p.fields for p in sink.points)


async def test_stream_handler_ignores_invalid_messages(ledger) -> None:
    await ledger.handle_stream_message({"slot_start": "kaputt"})
    assert ledger.slots(S, S + SLOT) == []
    await ledger.handle_stream_message(MSG)
    assert len(ledger.slots(S, S + SLOT)) == 1


def test_component_status_follows_latest_slot(ledger, clock) -> None:
    assert not ledger.component_status().ok
    ledger.ingest(parse_slot_flows(MSG), "stream")
    clock.advance(timedelta(minutes=40))  # Slot 10:00–10:15 Ortszeit, jetzt 10:40
    assert ledger.component_status().message == "letzte Viertelstunde 10:00"
    clock.advance(timedelta(minutes=10))
    status = ledger.component_status()
    assert (status.ok, status.message) == (False, "keine Abrechnungsdaten seit 10:15")


def test_parse_slot_flows_tolerates_rounding_above_a_slot() -> None:
    # der core summiert Kommazahlen; 900,0018 s ist Rundung, keine ungültige Viertelstunde
    assert parse_slot_flows(MSG | {"covered_s": 900.0018}).covered_s == 900.0
    assert parse_slot_flows(MSG | {"covered_s": 899.9983}).covered_s == 899.9983


def test_coverage_of_today_counts_only_past_slots(ledger, clock) -> None:
    ledger.ingest(parse_slot_flows(MSG | {"slot_start": "2026-10-09T07:45:00+00:00"}), "stream")
    clock.advance(timedelta(minutes=14))  # 10:14: die Viertelstunde 10:00–10:15 läuft noch
    today, tomorrow = ledger.days(date(2026, 10, 9), date(2026, 10, 10))
    assert (today.slots, today.expected_slots) == (1, 40)
    clock.advance(timedelta(minutes=1))  # 10:15
    [today] = ledger.days(date(2026, 10, 9), date(2026, 10, 9))
    assert today.expected_slots == 41
    # ein Tag in der Zukunft hat noch keine Viertelstunde
    assert (tomorrow.expected_slots, tomorrow.coverage, tomorrow.complete) == (0, 0.0, False)


def test_estimated_slots_are_counted_and_do_not_add_coverage(ledger, clock) -> None:
    ledger.ingest(parse_slot_flows(MSG), "stream")  # 900 s
    estimate = SlotFlows(S - SLOT, 0.0, {"pv>house": 50.0}, {})
    assert ledger.ingest(estimate, "estimate")
    clock.advance(SLOT)  # 10:15 Ortszeit
    [day] = ledger.days(date(2026, 10, 9), date(2026, 10, 9))
    assert (day.slots, day.estimated_slots) == (2, 1)
    assert day.coverage == pytest.approx(900 / (day.expected_slots * 900))
    assert day.pv_kwh == pytest.approx(1.05)  # geschätzte Flüsse zählen in die Summen


def test_month_peak_is_highest_quarter_of_local_month(ledger) -> None:
    ingest(
        ledger, "2026-09-30T21:45:00+00:00", {"grid>house": 3000}
    )  # 30. 9. 23:45 lokal: September
    ingest(ledger, "2026-10-01T06:00:00+00:00", {"grid>house": 1500})
    ingest(ledger, "2026-10-05T17:15:00+00:00", {"grid>house": 1200, "grid>wb:evcs": 800})
    peak = ledger.month_peak(date(2026, 10, 9))
    assert peak == MonthPeak(8.0, datetime(2026, 10, 5, 17, 15, tzinfo=UTC))


def test_month_peak_without_slots_is_none(ledger) -> None:
    assert ledger.month_peak(date(2026, 10, 9)) is None


def test_month_peak_tie_takes_earlier_slot(ledger) -> None:
    ingest(ledger, "2026-10-07T10:00:00+00:00", {"grid>house": 1000})
    ingest(ledger, "2026-10-03T10:00:00+00:00", {"grid>house": 1000})
    peak = ledger.month_peak(date(2026, 10, 20))
    assert peak == MonthPeak(4.0, datetime(2026, 10, 3, 10, 0, tzinfo=UTC))


def test_month_peak_month_edges_follow_local_time_in_winter(ledger) -> None:
    ingest(ledger, "2026-11-30T22:45:00+00:00", {"grid>house": 900})  # 30. 11. 23:45 lokal
    ingest(ledger, "2026-11-30T23:00:00+00:00", {"grid>house": 5000})  # 1. 12. 00:00 lokal
    assert ledger.month_peak(date(2026, 11, 15)) == MonthPeak(
        3.6, datetime(2026, 11, 30, 22, 45, tzinfo=UTC)
    )
    assert ledger.month_peak(date(2026, 12, 1)) == MonthPeak(
        20.0, datetime(2026, 11, 30, 23, 0, tzinfo=UTC)
    )


def test_month_peaks_per_month(ledger) -> None:
    ingest(ledger, "2026-09-30T21:45:00+00:00", {"grid>house": 3000})  # 30. 9. 23:45 lokal
    ingest(ledger, "2026-10-01T06:00:00+00:00", {"grid>house": 1500})
    ingest(ledger, "2026-10-05T17:15:00+00:00", {"grid>house": 1200, "grid>wb:evcs": 800})
    ingest(ledger, "2026-10-07T17:15:00+00:00", {"grid>house": 2000})  # Gleichstand: früher gewinnt
    ingest(ledger, "2026-10-31T23:00:00+00:00", {"grid>house": 100})  # 1. 11. 00:00 lokal
    ingest(ledger, "2026-12-20T10:00:00+00:00", {"grid>house": 9000})  # außerhalb
    peaks = ledger.month_peaks(date(2026, 9, 15), date(2026, 11, 2))
    assert peaks == [
        MonthPeak(12.0, datetime(2026, 9, 30, 21, 45, tzinfo=UTC)),
        MonthPeak(8.0, datetime(2026, 10, 5, 17, 15, tzinfo=UTC)),
        MonthPeak(0.4, datetime(2026, 10, 31, 23, 0, tzinfo=UTC)),
    ]


def test_month_peaks_skip_months_without_slots(ledger) -> None:
    ingest(ledger, "2026-03-10T10:00:00+00:00", {"grid>house": 500})
    ingest(ledger, "2026-05-10T10:00:00+00:00", {"grid>house": 750})
    peaks = ledger.month_peaks(date(2026, 1, 1), date(2026, 12, 31))
    assert [peak.slot_start.month for peak in peaks] == [3, 5]
    assert ledger.month_peaks(date(2026, 8, 1), date(2026, 8, 31)) == []
    assert ledger.month_peaks(date(2026, 10, 2), date(2026, 10, 1)) == []


def test_month_peaks_agree_with_month_peak_and_use_one_query(ledger, engine) -> None:
    for month in range(1, 13):
        ingest(ledger, f"2026-{month:02d}-15T10:00:00+00:00", {"grid>house": 100 * month})
        ingest(ledger, f"2026-{month:02d}-16T10:00:00+00:00", {"grid>house": 50 * month})
    statements: list[str] = []
    event.listen(engine, "before_cursor_execute", lambda *args: statements.append(args[2]))
    peaks = ledger.month_peaks(date(2026, 1, 1), date(2026, 12, 31))
    assert len(statements) == 1
    assert peaks == [ledger.month_peak(date(2026, month, 1)) for month in range(1, 13)]
