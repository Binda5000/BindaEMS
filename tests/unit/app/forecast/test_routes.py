import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from tests.app_helpers import ingest, login_as
from tests.unit.app.forecast.conftest import NOW, snowy

from bindaems.app.forecast.archive import RecordValue
from bindaems.app.forecast.service import OVERRIDE_NOT_SAVED
from bindaems.shared.timeutil import LOCAL_TZ


def test_pv_route_has_quantiles(client, auth, filled_forecast):
    login_as(client, auth, "viewer")
    slots = client.get("/api/forecast/pv").json()["slots"]
    assert slots and set(slots[0]) == {"start", "p50_w", "p25_w", "p75_w", "uncut_p50_w"}
    assert all(s["p25_w"] <= s["p50_w"] <= s["p75_w"] for s in slots)
    assert any(s["p25_w"] < s["p50_w"] < s["p75_w"] for s in slots)


def test_load_route_shape_without_forecast(client, auth) -> None:
    login_as(client, auth, "viewer")
    body = client.get("/api/forecast/load").json()
    assert set(body) == {"issued_at", "slots", "days", "method", "basis", "status"}
    assert body["method"] == "profile"
    assert body["issued_at"] is None and body["slots"] == []
    assert body["basis"] == {"ledger": 0, "ha": 0}
    assert [d["kwh"] for d in body["days"]] == [None, None, None]
    assert body["status"]["message"] == "Noch keine Daten für die Lastprognose"


def test_load_route_with_forecast(client, auth, load_service, ledger, clock) -> None:
    clock.advance(NOW - clock.now())  # die Route zählt die Tage von der Uhr der app
    for day in ("2026-10-13", "2026-10-14", "2026-10-15"):
        ingest(ledger, f"{day}T08:00:00+00:00", {"grid>house": 125})  # 500 W
    asyncio.run(load_service.refresh())
    login_as(client, auth, "viewer")
    body = client.get("/api/forecast/load").json()
    assert body["issued_at"] == "2026-10-16T06:00:00+00:00"
    assert set(body["slots"][0]) == {"start", "p50_w"} and body["slots"][0]["p50_w"] == 500
    assert body["method"] == "profile"
    assert body["basis"] == {"ledger": 3, "ha": 0}
    assert [d["date"] for d in body["days"]] == ["2026-10-16", "2026-10-17", "2026-10-18"]
    assert [d["kwh"] for d in body["days"]] == pytest.approx([8.0, 12.0, 12.0])  # ab 08:00 Uhr
    assert body["status"]["ok"] is True


def test_load_route_needs_login(client) -> None:
    assert client.get("/api/forecast/load").status_code == 401


def test_quality_route_validates_days(client, auth) -> None:
    login_as(client, auth, "viewer")
    assert client.get("/api/forecast/quality?days=0").status_code == 422
    assert client.get("/api/forecast/quality?days=61").status_code == 422
    assert client.get("/api/forecast/quality?days=60").status_code == 200
    assert client.get("/api/forecast/quality?days=1").status_code == 200


def test_quality_route_without_records(client, auth) -> None:
    login_as(client, auth, "viewer")
    assert client.get("/api/forecast/quality").json() == {"days": 14, "rows": []}


def test_quality_route_counts_back_from_today(
    client, auth, clock, archive, ledger, quality
) -> None:
    clock.advance(NOW - clock.now())  # heute ist der 16.10.2026 (Ortszeit)
    slot = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)  # 13 Tage zuvor: noch im Fenster
    archive.record("load", slot - timedelta(hours=3), {slot: RecordValue(1000, 1000)})
    ingest(ledger, slot, {"grid>house": 200})  # 800 W
    old = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)  # 14 Tage zuvor: außerhalb
    archive.record("load", old - timedelta(hours=3), {old: RecordValue(1000, 1000)})
    ingest(ledger, old, {"grid>house": 200})
    quality.update(date(2026, 10, 3))
    quality.update(date(2026, 10, 2))
    login_as(client, auth, "viewer")
    body = client.get("/api/forecast/quality?days=14").json()
    assert body == {
        "days": 14,
        "rows": [
            {
                "kind": "load",
                "horizon": "h0_6",
                "n": 1,
                "mae_w": 200.0,
                "bias_w": 200.0,
                "rel_mae_pct": 25.0,
                "improvement_pct": None,
            }
        ],
    }


def test_quality_route_needs_login(client) -> None:
    assert client.get("/api/forecast/quality").status_code == 401


def test_load_forecast_names_method(client, auth, load_service, ledger, clock, monkeypatch) -> None:
    clock.advance(NOW - clock.now())
    ingest(ledger, "2026-10-15T08:00:00+00:00", {"grid>house": 125})
    asyncio.run(load_service.refresh())
    login_as(client, auth, "viewer")
    assert client.get("/api/forecast/load").json()["method"] == "profile"
    latest = load_service.latest()
    assert latest is not None
    monkeypatch.setattr(load_service, "latest", lambda: replace(latest, method="model"))
    assert client.get("/api/forecast/load").json()["method"] == "model"


def _rate(archive, ledger, quality, slot: datetime, values: dict[str, float]) -> None:
    """Archiviert je Art eine Prognose für den Slot (Vorlauf 3 h), das Ist sind 800 W."""
    for kind, power_w in values.items():
        archive.record(kind, slot - timedelta(hours=3), {slot: RecordValue(power_w, power_w)})
    ingest(ledger, slot, {"grid>house": 200})  # 800 W
    quality.update(slot.astimezone(LOCAL_TZ).date())


def test_quality_has_improvement_for_model_rows(client, auth, clock, archive, ledger, quality):
    clock.advance(NOW - clock.now())
    slot = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
    _rate(archive, ledger, quality, slot, {"load": 1000, "load_profile": 1000, "load_model": 900})
    login_as(client, auth, "viewer")
    rows = client.get("/api/forecast/quality?days=14").json()["rows"]
    assert [(r["kind"], r["mae_w"], r["improvement_pct"]) for r in rows] == [
        ("load", 200.0, None),
        ("load_profile", 200.0, None),
        ("load_model", 100.0, 50.0),
    ]


def test_improvement_is_null_without_profile_rows_of_the_same_horizon(
    client, auth, clock, archive, ledger, quality
):
    clock.advance(NOW - clock.now())
    slot = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
    _rate(archive, ledger, quality, slot, {"load_model": 900})
    near = slot + timedelta(hours=1)  # Profil nur in der Klasse 6–24 h
    archive.record("load_profile", near - timedelta(hours=15), {near: RecordValue(1000, 1000)})
    ingest(ledger, near, {"grid>house": 200})
    quality.update(slot.astimezone(LOCAL_TZ).date())
    login_as(client, auth, "viewer")
    rows = client.get("/api/forecast/quality").json()["rows"]
    assert {(r["kind"], r["horizon"]): r["improvement_pct"] for r in rows} == {
        ("load_profile", "h6_24"): None,
        ("load_model", "h0_6"): None,
    }


def test_improvement_is_null_when_the_profile_is_exact(
    client, auth, clock, archive, ledger, quality
):
    clock.advance(NOW - clock.now())
    slot = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
    _rate(archive, ledger, quality, slot, {"load_profile": 800, "load_model": 900})
    login_as(client, auth, "viewer")
    rows = client.get("/api/forecast/quality").json()["rows"]
    assert [(r["kind"], r["improvement_pct"]) for r in rows] == [
        ("load_profile", None),
        ("load_model", None),
    ]


def test_quality_daily_sorted_by_date_and_kind(client, auth, clock, archive, ledger, quality):
    clock.advance(NOW - clock.now())
    late = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
    early = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)
    _rate(archive, ledger, quality, late, {"load_model": 900, "load": 1000, "pv": 1000})
    _rate(archive, ledger, quality, early, {"load_profile": 1100, "load_model": 700})
    # zweiter Slot im Nahbereich und ein Slot in der Klasse 6–24 h am selben Tag: die Klassen haben
    # ungleich viele Slots (2 gegen 1), das Tagesmittel zählt nach Slots (400 / 3) und ist nicht
    # das Mittel der Klassenmittel (150)
    next_slot = late + timedelta(minutes=15)
    archive.record("load_model", next_slot - timedelta(hours=3), {next_slot: RecordValue(900, 900)})
    ingest(ledger, next_slot, {"grid>house": 200})
    archive.record("load_model", late - timedelta(hours=15), {late: RecordValue(1000, 1000)})
    quality.update(late.astimezone(LOCAL_TZ).date())
    login_as(client, auth, "viewer")
    body = client.get("/api/forecast/quality/daily?days=7").json()
    assert body["days"] == 7
    assert [(r["date"], r["kind"], r["n"], r["mae_w"]) for r in body["rows"]] == [
        ("2026-10-14", "load_profile", 1, 300.0),
        ("2026-10-14", "load_model", 1, 100.0),
        ("2026-10-15", "pv", 1, 1000.0),
        ("2026-10-15", "load", 1, 200.0),
        ("2026-10-15", "load_model", 3, 400.0 / 3),
    ]
    assert set(body["rows"][0]) == {"date", "kind", "n", "mae_w"}


def test_quality_daily_defaults_to_28_days_and_counts_back_from_today(
    client, auth, clock, archive, ledger, quality
):
    clock.advance(NOW - clock.now())  # heute ist der 16.10.2026
    inside = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)  # 27 Tage zuvor
    outside = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)  # 28 Tage zuvor
    _rate(archive, ledger, quality, inside, {"load": 1000})
    _rate(archive, ledger, quality, outside, {"load": 1000})
    login_as(client, auth, "viewer")
    body = client.get("/api/forecast/quality/daily").json()
    assert body["days"] == 28 and [r["date"] for r in body["rows"]] == ["2026-09-19"]


def test_quality_daily_without_records(client, auth) -> None:
    login_as(client, auth, "viewer")
    assert client.get("/api/forecast/quality/daily").json() == {"days": 28, "rows": []}


def test_quality_daily_rejects_days_out_of_range(client, auth) -> None:
    login_as(client, auth, "viewer")
    assert client.get("/api/forecast/quality/daily?days=0").status_code == 422
    assert client.get("/api/forecast/quality/daily?days=61").status_code == 422
    assert client.get("/api/forecast/quality/daily?days=60").status_code == 200
    assert client.get("/api/forecast/quality/daily?days=1").status_code == 200


def test_quality_daily_needs_login(client) -> None:
    assert client.get("/api/forecast/quality/daily").status_code == 401


SNOW_AT = datetime(2026, 10, 9, 0, 0, tzinfo=UTC)  # heute 02:00 Ortszeit (die Uhr: 10:00)


@pytest.fixture
def snowy_forecast(forecast_service, respx_mock):
    """Prognose nach 2 cm Schneefall in der Nacht: vermutet bedeckt, gekürzt auf 10 %."""
    respx_mock.routes["open_meteo"].respond(200, text=snowy({SNOW_AT: 2.0}))
    asyncio.run(forecast_service.refresh())
    return forecast_service


def test_pv_route_shows_the_snow_cut(client, auth, snowy_forecast) -> None:
    login_as(client, auth, "viewer")
    body = client.get("/api/forecast/pv").json()
    assert body["snow"] == {
        "state": "vermutet",
        "factor_pct": 10,
        "since": "2026-10-09T00:00:00+00:00",
        "override": None,
    }
    cut = [s for s in body["slots"] if s["uncut_p50_w"] > s["p50_w"]]
    assert cut and all(s["p50_w"] == pytest.approx(0.1 * s["uncut_p50_w"]) for s in cut)


def test_pv_route_without_forecast_has_no_snow(client, auth) -> None:
    login_as(client, auth, "viewer")
    body = client.get("/api/forecast/pv").json()
    assert body["snow"] is None and body["slots"] == []


def test_pv_route_without_cut_is_free(client, auth, filled_forecast) -> None:
    login_as(client, auth, "viewer")
    body = client.get("/api/forecast/pv").json()
    assert body["snow"] == {"state": "frei", "factor_pct": 100, "since": None, "override": None}
    assert all(s["uncut_p50_w"] == s["p50_w"] for s in body["slots"])


def test_snow_buttons_need_operator_and_csrf(client, auth, snowy_forecast) -> None:
    csrf = login_as(client, auth, "viewer")
    assert (
        client.post("/api/forecast/pv/snow", json={"mode": "free"}, headers=csrf).status_code == 403
    )
    login_as(client, auth, "operator")
    assert client.post("/api/forecast/pv/snow", json={"mode": "free"}).status_code == 403  # CSRF
    assert snowy_forecast.latest().snow.state == "vermutet"


def test_snow_button_free_and_back_to_auto(client, auth, snowy_forecast, audit) -> None:
    csrf = login_as(client, auth, "operator")
    response = client.post("/api/forecast/pv/snow", json={"mode": "free"}, headers=csrf)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "state": "frei",
        "factor_pct": 100,
        "since": None,
        "override": "free",
    }
    assert client.get("/api/forecast/pv").json()["snow"]["override"] == "free"
    entry = audit.recent(1)[0]
    assert (entry.actor, entry.source, entry.action, entry.details) == (
        "operator-test",
        "ui",
        "forecast.snow_override",
        {"mode": "free"},
    )
    back = client.post("/api/forecast/pv/snow", json={"mode": None}, headers=csrf)
    assert back.status_code == 200
    assert (back.json()["state"], back.json()["override"]) == ("vermutet", None)


def test_snow_button_covered(client, auth, filled_forecast) -> None:
    csrf = login_as(client, auth, "operator")
    body = client.post("/api/forecast/pv/snow", json={"mode": "covered"}, headers=csrf).json()
    assert (body["state"], body["factor_pct"], body["override"]) == ("vermutet", 10, "covered")


@pytest.mark.parametrize("body", [{"mode": "x"}, {}, {"mode": "free", "extra": 1}, {"mode": 1}])
def test_snow_button_rejects_other_bodies(client, auth, snowy_forecast, body) -> None:
    csrf = login_as(client, auth, "operator")
    assert client.post("/api/forecast/pv/snow", json=body, headers=csrf).status_code == 422


def test_snow_button_without_forecast_is_a_conflict(client, auth) -> None:
    csrf = login_as(client, auth, "operator")
    response = client.post("/api/forecast/pv/snow", json={"mode": "free"}, headers=csrf)
    assert response.status_code == 409
    assert response.json()["detail"] == "Noch keine PV-Prognose"


def test_snow_button_with_unwritable_file_names_the_error(
    client, auth, snowy_forecast, tmp_path, audit
) -> None:
    snowy_forecast._override_path = tmp_path / "fehlt" / "pv_snow_override.json"  # kein Verzeichnis
    csrf = login_as(client, auth, "operator")
    response = client.post("/api/forecast/pv/snow", json={"mode": "free"}, headers=csrf)
    assert response.status_code == 503
    assert response.json()["detail"] == OVERRIDE_NOT_SAVED
    snow = client.get("/api/forecast/pv").json()["snow"]
    assert (snow["state"], snow["override"]) == ("vermutet", None)  # nichts gesetzt
    assert all(e.action != "forecast.snow_override" for e in audit.recent())
