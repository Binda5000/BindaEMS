import base64

import pytest
from tests.app_helpers import influx_q

from bindaems.app.history.influx import InfluxQueryError, Series, quote_ident, quote_str

URL = "http://influx.lan:8086/query"


async def test_query_parses_series_and_sends_auth(respx_mock, reader) -> None:
    route = respx_mock.post(URL).respond(
        json={
            "results": [
                {
                    "statement_id": 0,
                    "series": [
                        {
                            "name": "power",
                            "tags": {"id": "grid"},
                            "columns": ["time", "v"],
                            "values": [[1791504000000, 512.5]],
                        }
                    ],
                }
            ]
        }
    )
    assert await reader.query("SELECT 1", database="bindaems") == [
        Series("power", {"id": "grid"}, ["time", "v"], [[1791504000000, 512.5]])
    ]
    request = route.calls.last.request
    assert (request.url.params["db"], request.url.params["epoch"]) == ("bindaems", "ms")
    assert "q" not in request.url.params and influx_q(request) == "SELECT 1"  # Formular-Body
    expected = "Basic " + base64.b64encode(b"bindaems:geheim").decode()
    assert request.headers["authorization"] == expected


async def test_long_query_goes_in_the_body_not_the_url(respx_mock, reader) -> None:
    # N1: 36 Mitternächte × Signale ergeben über 20 KB; ein Proxy davor nimmt solche URLs nicht an
    route = respx_mock.post(URL).respond(json={"results": []})
    long = ";".join(['SELECT last("kwh") FROM "raw"."energy"'] * 500)
    assert await reader.query(long) == []
    request = route.calls.last.request
    assert len(str(request.url)) < 200 and influx_q(request) == long
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"


async def test_error_in_result_raises(respx_mock, reader) -> None:
    respx_mock.post(URL).respond(json={"results": [{"error": "database not found"}]})
    with pytest.raises(InfluxQueryError, match="database not found"):
        await reader.query("SELECT 1")


async def test_http_error_raises(respx_mock, reader) -> None:
    respx_mock.post(URL).respond(401)
    with pytest.raises(InfluxQueryError):
        await reader.query("SELECT 1")


def test_quoting_escapes_quotes_and_backslashes() -> None:
    assert quote_str("it's\\") == "'it\\'s\\\\'"
    assert quote_ident('a"b') == '"a\\"b"'


@pytest.mark.parametrize(
    "q",
    [
        'DROP MEASUREMENT "power"',
        'DELETE FROM "power" WHERE time < now() - 1d',
        'SELECT "v" INTO "copy" FROM "power"',
        'select * into "bindaems"."raw"."x" from "power"',
        'SELECT 1; DROP MEASUREMENT "power"',
        'SHOW MEASUREMENTS;\nDROP SERIES FROM "power"',
        'SELECT * FROM "m" WHERE "id" = \'a\'; DELETE FROM "m"',
        'SELECT 1 -- \'; DROP MEASUREMENT "m"',
        'SELECT 1 /* \'; */ DROP MEASUREMENT "m"',
        "",
        " ; ",
        'SELECT * FROM "m" WHERE "id" = \'offen',
    ],
)
async def test_only_reading_statements_are_sent(respx_mock, reader, q: str) -> None:
    # N10: per POST lehnt InfluxDB 1.x schreibende Anweisungen nicht ab, der Reader tut es
    route = respx_mock.post(URL).respond(json={"results": []})
    with pytest.raises(ValueError, match="InfluxQL"):
        await reader.query(q)
    assert not route.called


@pytest.mark.parametrize(
    "q",
    [
        "  select 1",
        'SHOW SERIES FROM "W","kW"',
        'SELECT last("kwh") FROM "raw"."energy";SELECT first("kwh") FROM "raw"."energy";',
        "SELECT * FROM \"m\" WHERE \"id\" = 'a;DROP MEASUREMENT \\'m\\''",
        'SELECT "into" FROM "m" WHERE "x;y" = \'z\'',
        'SELECT "v" FROM /^[a-z_]+\\.[a-z0-9_\']+$/ WHERE "id" =~ /;/',
        'SELECT "v" / 1000 FROM "m"',
    ],
)
async def test_reading_statements_pass(respx_mock, reader, q: str) -> None:
    route = respx_mock.post(URL).respond(json={"results": []})
    assert await reader.query(q) == []
    assert influx_q(route.calls.last.request) == q
