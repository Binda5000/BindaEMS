import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from tests.app_helpers import T_APP

from bindaems.app.forecast.openmeteo import (
    OPEN_METEO_URL,
    ForecastParseError,
    fetch_plane,
    parse_plane,
)
from bindaems.shared.config import PvPlane
from bindaems.shared.timeutil import ManualClock

FIXTURE = Path("tests/fixtures/openmeteo_sued_2026-10-09.json")


def test_value_at_t_belongs_to_preceding_slot() -> None:
    slots = {s.slot_start: s for s in parse_plane(FIXTURE.read_text())}
    assert len(slots) == 384
    morning = slots[datetime(2026, 10, 9, 5, 15, tzinfo=UTC)]  # Zeitstempel 05:30 UTC
    assert (morning.gti_w_m2, morning.temp_c) == (15.5, 12.2)
    assert slots[datetime(2026, 10, 9, 5, 0, tzinfo=UTC)].gti_w_m2 == 0.0
    assert min(slots) == datetime(2026, 10, 7, 23, 45, tzinfo=UTC)


async def test_request_parameters(respx_mock, cfg) -> None:
    route = respx_mock.get(OPEN_METEO_URL).respond(200, text="{}")
    plane = PvPlane(name="Ost", kwp=5, tilt_deg=20, azimuth_deg=-90)
    async with httpx.AsyncClient() as http:
        raw = await fetch_plane(http, ManualClock(T_APP), cfg.site.location, plane)
    assert raw.source == "open_meteo"
    params = route.calls.last.request.url.params
    assert (params["latitude"], params["longitude"]) == ("48.2", "16.37")
    assert params["minutely_15"] == "global_tilted_irradiance,temperature_2m"
    assert (params["tilt"], params["azimuth"], params["forecast_days"], params["past_days"]) == (
        "20.0",
        "-90.0",
        "3",
        "1",
    )
    assert (params["timezone"], params["timeformat"]) == ("GMT", "unixtime")


def test_error_response_raises() -> None:
    with pytest.raises(ForecastParseError, match="Invalid value"):
        parse_plane('{"error": true, "reason": "Invalid value"}')


def _body(**changes) -> str:
    data = json.loads(FIXTURE.read_text())
    for key, value in changes.items():
        section, field = key.split("__")
        data[section][field] = value
    return json.dumps(data)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"minutely_15_units__global_tilted_irradiance": "kW/m²"}, "W/m²"),
        ({"minutely_15__temperature_2m": [1.0]}, "ungleich lang"),
    ],
)
def test_bad_shapes_raise(changes, message) -> None:
    with pytest.raises(ForecastParseError, match=message):
        parse_plane(_body(**changes))


def test_null_values_are_dropped() -> None:
    data = json.loads(FIXTURE.read_text())
    data["minutely_15"]["global_tilted_irradiance"][0] = None
    data["minutely_15"]["temperature_2m"][1] = None
    assert len(parse_plane(json.dumps(data))) == 382
