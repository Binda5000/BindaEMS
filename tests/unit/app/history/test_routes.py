import httpx
import pytest
from tests.app_helpers import login_as

from bindaems.app.history.routes import history_router
from bindaems.app.history.series import build_catalog

URL = "http://influx.lan:8086/query"
HOUR = {"from": "2026-10-09T00:00:00Z", "to": "2026-10-09T01:00:00Z"}


def test_history_returns_scaled_points(client, auth, respx_mock) -> None:
    login_as(client, auth, "viewer")
    route = respx_mock.get(URL).respond(
        json={
            "results": [
                {
                    "series": [
                        {
                            "name": "forecast",
                            "columns": ["time", "v"],
                            "values": [[1791504000000, 1.5], [1791504060000, None]],
                        }
                    ]
                }
            ]
        }
    )
    body = client.get("/api/history", params={"series": "forecast.pv", **HOUR}).json()
    assert (body["rp"], body["step_s"]) == ("long", 60)
    assert body["series"]["forecast.pv"] == {
        "label": "PV-Prognose",
        "unit": "W",
        "points": [[1791504000000, 1500.0]],
    }
    assert '"long"."forecast"' in route.calls.last.request.url.params["q"]


@pytest.mark.parametrize(
    "params",
    [
        {"series": "gibts", **HOUR},
        {"series": "grid", "from": "2026-10-09T01:00:00Z", "to": "2026-10-09T00:00:00Z"},
        {"series": "grid", "from": "2025-01-01T00:00:00Z", "to": "2026-10-09T00:00:00Z"},
        {"series": "grid", "from": "2026-10-09T00:00:00", "to": "2026-10-09T01:00:00"},
    ],
)
def test_bad_requests_are_422(client, auth, params) -> None:
    login_as(client, auth, "viewer")
    assert client.get("/api/history", params=params).status_code == 422


def test_influx_down_is_502(client, auth, respx_mock) -> None:
    login_as(client, auth, "viewer")
    respx_mock.get(URL).mock(side_effect=httpx.ConnectError("weg"))
    response = client.get("/api/history", params={"series": "grid", **HOUR})
    assert response.status_code == 502 and response.json() == {
        "detail": "InfluxDB nicht erreichbar"
    }


def test_catalog_endpoint(client, auth) -> None:
    login_as(client, auth, "viewer")
    catalog = client.get("/api/history/catalog").json()
    assert catalog[0] == {"id": "grid", "label": "Netz", "unit": "W"}


def test_own_labels_replace_catalog_labels(make_client, reader, cfg, clock, guard, auth) -> None:
    client = make_client(
        history_router(reader, build_catalog(cfg), clock, guard, lambda: {"wallbox.evcs": "Garage"})
    )
    login_as(client, auth, "viewer")
    labels = {item["id"]: item["label"] for item in client.get("/api/history/catalog").json()}
    assert (labels["wallbox.evcs"], labels["wallbox.twc"]) == ("Garage", "Wallbox twc")
