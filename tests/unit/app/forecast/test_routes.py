from tests.app_helpers import login_as


def test_pv_route_has_quantiles(client, auth, filled_forecast):
    login_as(client, auth, "viewer")
    slots = client.get("/api/forecast/pv").json()["slots"]
    assert slots and set(slots[0]) == {"start", "p50_w", "p25_w", "p75_w"}
    assert all(s["p25_w"] <= s["p50_w"] <= s["p75_w"] for s in slots)
    assert any(s["p25_w"] < s["p50_w"] < s["p75_w"] for s in slots)
