import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs
from tests.app_helpers import T_APP

from bindaems.app.storm.geosphere import (
    SOURCE,
    URL,
    WarningsUnavailable,
    fetch_warnings,
    parse_warnings,
)
from bindaems.shared.config import LatLon
from bindaems.shared.timeutil import ManualClock

FIXTURES = Path("tests/fixtures/geosphere")


def _body(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _entry(**props: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "warnid": 1,
        "chgid": 1,
        "warntypid": 5,
        "warnstufeid": 2,
        "begin": "01.01.1999 00:00",
        "end": "01.01.1999 01:00",
        "text": "Test",
        "rawinfo": {"wtype": 5, "wlevel": 2, "start": "1679896800", "end": "1679900400"},
    }
    base.update(props)
    return {"type": "Warning", "properties": base}


def _feature(*entries: dict[str, Any]) -> str:
    return json.dumps({"type": "Feature", "properties": {"warnings": list(entries)}})


def test_parses_two_warnings_from_doc_example() -> None:
    first, second = parse_warnings(_body("warnings_two.json"))
    assert (first.warn_id, first.version) == (4149, 6)
    assert (second.warn_id, second.version) == (4150, 2)
    for warning in (first, second):
        assert (warning.kind, warning.level) == ("sturm", 1)
    assert first.text == "Gelbe Windwarnung von Mo, 27.03.2023 08:00 bis Mo, 27.03.2023 18:00"
    assert second.text == "Gelbe Windwarnung von Di, 28.03.2023 08:00 bis Di, 28.03.2023 18:00"


def test_times_come_from_rawinfo_epoch() -> None:
    first, second = parse_warnings(_body("warnings_two.json"))
    assert first.begin == datetime(2023, 3, 27, 6, 0, tzinfo=UTC)
    assert first.end == datetime(2023, 3, 27, 16, 0, tzinfo=UTC)
    assert second.begin == datetime(2023, 3, 28, 6, 0, tzinfo=UTC)
    (local,) = parse_warnings(_feature(_entry()))  # Text-„begin“ 1999 wird ignoriert
    assert local.begin == datetime(2023, 3, 27, 6, 0, tzinfo=UTC)
    assert local.end == datetime(2023, 3, 27, 7, 0, tzinfo=UTC)


def test_skips_warning_without_valid_rawinfo() -> None:
    body = _feature(
        _entry(warnid=1, rawinfo=None),
        _entry(warnid=2, rawinfo={"start": "1679900400", "end": "1679896800"}),
        _entry(warnid=3, warntypid=9),
        _entry(warnid=4),
    )
    with capture_logs() as logs:
        warnings = parse_warnings(body)
    assert [w.warn_id for w in warnings] == [4]
    skipped = [entry for entry in logs if entry.get("code") == "storm_warning_skipped"]
    assert [entry["warn_id"] for entry in skipped] == [1, 2, 3]


def test_empty_warnings() -> None:
    assert parse_warnings(_body("warnings_none.json")) == []


@pytest.mark.parametrize(
    "body",
    [
        _body("error_municipal.json"),
        "not json",
        '{"type": "Feature", "properties": {}}',
        '{"type": "error", "msg": "boom"}',
        "[]",
    ],
)
def test_error_body_raises(body: str) -> None:
    with pytest.raises(WarningsUnavailable):
        parse_warnings(body)


async def test_fetch_sends_coords_and_lang() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params.get("lat") == "0.0":
            return httpx.Response(503, text="down")
        return httpx.Response(200, text=_body("warnings_two.json"))

    clock = ManualClock(T_APP)
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        warnings = await fetch_warnings(http, clock, LatLon(lat=48.2, lon=16.5))
        assert len(warnings) == 2
        request = seen[0]
        assert str(request.url).startswith(URL)
        assert dict(request.url.params) == {"lon": "16.5", "lat": "48.2", "lang": "de"}
        assert request.headers["User-Agent"].startswith("BindaEMS/")
        with pytest.raises(WarningsUnavailable, match="503"):
            await fetch_warnings(http, clock, LatLon(lat=0, lon=16.5))
    assert SOURCE == "geosphere_warn"


async def test_fetch_network_error_raises() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("kaputt")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        with pytest.raises(WarningsUnavailable):
            await fetch_warnings(http, ManualClock(T_APP), LatLon(lat=48.2, lon=16.5))
