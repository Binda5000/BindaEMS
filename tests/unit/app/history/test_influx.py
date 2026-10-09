import base64

import pytest

from bindaems.app.history.influx import InfluxQueryError, Series, quote_ident, quote_str

URL = "http://influx.lan:8086/query"


async def test_query_parses_series_and_sends_auth(respx_mock, reader) -> None:
    route = respx_mock.get(URL).respond(
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
    assert request.url.params["q"] == "SELECT 1"
    expected = "Basic " + base64.b64encode(b"bindaems:geheim").decode()
    assert request.headers["authorization"] == expected


async def test_error_in_result_raises(respx_mock, reader) -> None:
    respx_mock.get(URL).respond(json={"results": [{"error": "database not found"}]})
    with pytest.raises(InfluxQueryError, match="database not found"):
        await reader.query("SELECT 1")


async def test_http_error_raises(respx_mock, reader) -> None:
    respx_mock.get(URL).respond(401)
    with pytest.raises(InfluxQueryError):
        await reader.query("SELECT 1")


def test_quoting_escapes_quotes_and_backslashes() -> None:
    assert quote_str("it's\\") == "'it\\'s\\\\'"
    assert quote_ident('a"b') == '"a\\"b"'
