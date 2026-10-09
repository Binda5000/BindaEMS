import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from tests.app_helpers import T_APP

from bindaems.app.fetch import FetchError
from bindaems.app.prices.sources import (
    AwattarSource,
    EnergyChartsSource,
    PriceInterval,
    PriceParseError,
    SmartEnergySource,
    make_reference,
)
from bindaems.shared.timeutil import LOCAL_TZ

FIX = Path("tests/fixtures")
T0 = 1791496800  # 2026-10-08T22:00:00Z


def test_smartenergy_recorded_response(source) -> None:
    intervals = source.parse((FIX / "smartenergy_2026-10-09.json").read_text())
    assert len(intervals) == 96
    first, last = intervals[0], intervals[-1]
    assert first == PriceInterval(
        datetime(2026, 10, 8, 22, tzinfo=UTC), datetime(2026, 10, 8, 22, 15, tzinfo=UTC), 17.824
    )
    assert (last.start, last.ct_kwh) == (datetime(2026, 10, 9, 21, 45, tzinfo=UTC), 25.405)


def test_smartenergy_interval_60_gives_hour_intervals(source) -> None:
    body = json.dumps(
        {
            "tariff": "EPEXSPOTAT",
            "unit": "ct/kWh",
            "interval": 60,
            "data": [{"date": "2026-10-09T00:00:00+02:00", "value": 9.5}],
        }
    )
    [interval] = source.parse(body)
    assert interval.end - interval.start == timedelta(hours=1)
    assert interval.start == datetime(2026, 10, 8, 22, tzinfo=UTC)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"unit": "EUR/MWh", "interval": 15, "data": []}, "unbekannte Einheit"),
        (
            {
                "unit": "ct/kWh",
                "interval": 15,
                "data": [{"date": "2026-10-09T00:00:00", "value": 1}],
            },
            "ohne Zeitzone",
        ),
        ({"unit": "ct/kWh", "interval": 0, "data": []}, "Intervall"),
        (
            {
                "unit": "ct/kWh",
                "interval": 15,
                "data": [{"date": "2026-10-09T00:00:00+02:00", "value": True}],
            },
            "Preis",
        ),
        ([], "kein JSON-Objekt"),
    ],
)
def test_smartenergy_rejects_bad_shapes(source, body, message) -> None:
    with pytest.raises(PriceParseError, match=f"^smartENERGY: .*{message}"):
        source.parse(json.dumps(body))


def test_smartenergy_rejects_non_json(source) -> None:
    with pytest.raises(PriceParseError, match=r"^smartENERGY: "):
        source.parse("<html>Wartung</html>")


async def test_smartenergy_fetch_sends_user_agent(respx_mock, source) -> None:
    route = respx_mock.get(SmartEnergySource.URL).respond(200, text="{}")
    raw = await source.fetch()
    assert raw.ok and raw.source == "smartenergy" and raw.fetched_at == T_APP
    assert route.calls.last.request.headers["user-agent"] == "BindaEMS/0.1.0"


def test_energy_charts_recorded_response(energy_charts) -> None:
    intervals = energy_charts.parse((FIX / "energycharts_at_2026-10-09.json").read_text())
    assert len(intervals) == 96 and intervals[0].ct_kwh == pytest.approx(15.45)
    assert intervals[0].start == datetime(2026, 10, 8, 22, tzinfo=UTC)
    assert intervals[-1].end == datetime(2026, 10, 9, 22, tzinfo=UTC)


def test_energy_charts_skips_null_prices_and_ends_at_next_stamp(energy_charts) -> None:
    body = json.dumps(
        {
            "unix_seconds": [T0, T0 + 900, T0 + 1800],
            "price": [100.0, None, 120.0],
            "unit": "EUR / MWh",
        }
    )
    first, last = energy_charts.parse(body)
    assert (first.end - first.start, first.ct_kwh) == (timedelta(minutes=15), 10.0)
    assert last.end == datetime(2026, 10, 8, 22, 45, tzinfo=UTC)
    single = json.dumps({"unix_seconds": [T0], "price": [100.0], "unit": "EUR / MWh"})
    [only] = energy_charts.parse(single)
    assert only.end - only.start == timedelta(minutes=15)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"unix_seconds": [T0], "price": [1.0], "unit": "EUR / kWh"}, "unbekannte Einheit"),
        ({"unix_seconds": [T0, T0 + 900], "price": [1.0], "unit": "EUR / MWh"}, "ungleich lang"),
        ({"unix_seconds": [T0 + 900, T0], "price": [1.0, 2.0], "unit": "EUR / MWh"}, "aufsteigend"),
    ],
)
def test_energy_charts_rejects_bad_shapes(energy_charts, body, message) -> None:
    with pytest.raises(PriceParseError, match=f"^Energy-Charts: .*{message}"):
        energy_charts.parse(json.dumps(body))


async def test_energy_charts_request_parameters(respx_mock, energy_charts) -> None:
    route = respx_mock.get(EnergyChartsSource.URL).respond(200, text="{}")
    start = datetime(2026, 10, 9, tzinfo=LOCAL_TZ)
    await energy_charts.fetch(start, start + timedelta(days=2))
    params = route.calls.last.request.url.params
    assert (params["bzn"], params["start"], params["end"]) == (
        "AT",
        "2026-10-09T00:00:00+02:00",
        "2026-10-11T00:00:00+02:00",
    )


def test_awattar_recorded_response(awattar) -> None:
    intervals = awattar.parse((FIX / "awattar_2026-10-09.json").read_text())
    assert len(intervals) == 24 and intervals[0].ct_kwh == pytest.approx(14.853)
    assert intervals[0].end - intervals[0].start == timedelta(hours=1)


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            {"start_timestamp": 0, "end_timestamp": 3600000, "marketprice": 1.0, "unit": "ct/kWh"},
            "unbekannte Einheit",
        ),
        (
            {"start_timestamp": 3600000, "end_timestamp": 0, "marketprice": 1.0, "unit": "Eur/MWh"},
            "Ende",
        ),
    ],
)
def test_awattar_rejects_bad_shapes(awattar, entry, message) -> None:
    with pytest.raises(PriceParseError, match=f"^aWATTar: .*{message}"):
        awattar.parse(json.dumps({"object": "list", "data": [entry]}))


async def test_awattar_request_parameters(respx_mock, awattar) -> None:
    route = respx_mock.get(AwattarSource.URL).respond(200, text="{}")
    start = datetime(2026, 10, 9, tzinfo=LOCAL_TZ)
    await awattar.fetch(start, start + timedelta(days=1))
    params = route.calls.last.request.url.params
    assert (params["start"], params["end"]) == (str(T0 * 1000), str((T0 + 86400) * 1000))


def test_make_reference_picks_source(clock) -> None:
    http = httpx.AsyncClient()
    assert make_reference("energy_charts", http, clock).name == "energy_charts"
    assert make_reference("awattar", http, clock).name == "awattar"


async def test_network_error_raises_fetch_error(respx_mock, source) -> None:
    respx_mock.get(SmartEnergySource.URL).mock(side_effect=httpx.ConnectError("weg"))
    with pytest.raises(FetchError, match="smartenergy: ConnectError"):
        await source.fetch()


async def test_http_error_is_returned_for_archiving(respx_mock, source) -> None:
    respx_mock.get(SmartEnergySource.URL).respond(503, text="Wartung")
    raw = await source.fetch()
    assert (raw.ok, raw.status, raw.body) == (False, 503, "Wartung")


@pytest.mark.parametrize("stamp", [1e20, "1791496800", None])
def test_out_of_range_or_bad_stamps_are_parse_errors(energy_charts, awattar, stamp) -> None:
    with pytest.raises(PriceParseError, match=r"^Energy-Charts: ungültiger Zeitstempel"):
        energy_charts.parse(
            json.dumps({"unix_seconds": [stamp], "price": [1.0], "unit": "EUR / MWh"})
        )
    entry = {"start_timestamp": stamp, "end_timestamp": 0, "marketprice": 1.0, "unit": "Eur/MWh"}
    with pytest.raises(PriceParseError, match=r"^aWATTar: ungültiger Zeitstempel"):
        awattar.parse(json.dumps({"data": [entry]}))
