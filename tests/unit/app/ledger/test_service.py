from datetime import UTC, date, datetime, timedelta

import pytest
from tests.app_helpers import SLOT_FLOWS_MSG, price_row, set_feed_in, set_grid_usage

from bindaems.app.ledger.service import parse_slot_flows
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


def test_day_summary_energy_balance_and_kpis(ledger, prices_with_10ct, feed_in_7ct) -> None:
    ledger.ingest(parse_slot_flows(MSG), "stream")
    [day] = ledger.days(date(2026, 10, 9), date(2026, 10, 9))
    assert (day.pv_kwh, day.import_kwh, day.export_kwh) == (1.0, 0.4, 0.3)
    assert (day.house_kwh, dict(day.wallbox_kwh)) == (0.6, {"evcs": 0.2, "twc": 0.25})
    assert (day.battery_charge_kwh, day.battery_discharge_kwh, day.consumption_kwh) == (
        0.1,
        0.05,
        1.05,
    )
    assert day.counter_kwh["grid.energy_import_kwh"] == pytest.approx(0.4)
    assert (day.slots, day.expected_slots) == (1, 96) and day.coverage == pytest.approx(1 / 96)
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


def test_day_summary_on_dst_day_expects_100_slots(ledger) -> None:
    [day] = ledger.days(date(2026, 10, 25), date(2026, 10, 25))
    assert (day.expected_slots, day.slots, day.coverage) == (100, 0, 0.0)
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
