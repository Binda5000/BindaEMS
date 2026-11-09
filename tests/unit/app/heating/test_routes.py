"""Tests von ``GET /api/heating/demand``."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import insert
from tests.app_helpers import login_as

from bindaems.app.db.schema import heating_day
from bindaems.app.heating.days import HeatingDays
from bindaems.app.heating.routes import NO_BURNER, heating_router
from bindaems.app.learning.weather import WeatherArchive
from bindaems.shared.timeutil import ManualClock

FIRST = date(2026, 12, 1)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(datetime(2026, 12, 20, 12, 0, tzinfo=UTC))


@pytest.fixture
def days(engine, clock) -> HeatingDays:
    return HeatingDays(
        engine,
        None,
        WeatherArchive(engine),
        "klima",
        clock,
        ac_power_known=True,  # type: ignore[arg-type]
    )


def _settings(settings_service, burner_kw: float | None = 27.0) -> None:
    current = settings_service.current()
    heating = current.settings.heating.model_copy(update={"burner_kw": burner_kw})
    settings_service.update(
        current.settings.model_copy(update={"heating": heating}),
        base_version=current.version,
        actor="test",
        source="ui",
    )


def _store(engine, count: int) -> None:
    with engine.begin() as conn:
        for n in range(count):
            hdd = 5.0 + n
            conn.execute(
                insert(heating_day).values(
                    day=(FIRST + timedelta(days=n)).isoformat(),
                    burner_h=hdd / 2,
                    known_h=24.0,
                    day_h=24.0,
                    burner_end=False,
                    hdd_kd=hdd,
                    outdoor_mean_c=15.0 - hdd,
                    ac_heat_el_kwh=1.0,
                )
            )


@pytest.fixture
def client(make_client, days, settings_service, clock, guard, auth):
    _settings(settings_service)
    client = make_client(heating_router(days, settings_service, clock, guard, ac_power_known=True))
    login_as(client, auth, "viewer")
    return client


def test_demand_404_without_burner(make_client, settings_service, clock, guard, auth) -> None:
    client = make_client(heating_router(None, settings_service, clock, guard, ac_power_known=False))
    login_as(client, auth, "viewer")
    response = client.get("/api/heating/demand")
    assert response.status_code == 404 and response.json()["detail"] == NO_BURNER


def test_demand_returns_season_points_and_fit(client, engine) -> None:
    _store(engine, 16)
    body = client.get("/api/heating/demand?tag=2026-12-15").json()
    assert body["season"] == "2026/27"
    assert (body["first"], body["last"]) == ("2026-07-01", "2027-06-30")
    assert body["fit"]["n"] == 16 and body["reason"] is None
    assert len(body["points"]) == 16
    point = body["points"][0]
    assert point["day"] == "2026-12-01" and point["complete"] and point["used"]
    assert point["oil_heat_kwh"] == pytest.approx(2.5 * 27.0)
    assert point["ac_heat_kwh"] == pytest.approx(3.0)
    assert body["ac_by_month"] == {"2026-12": pytest.approx(16.0)}
    assert body["heating_limit_c"] == 15.0 and body["ac_cop"] == 3.0
    assert body["totals"]["ac_heat_el_kwh"] == pytest.approx(16.0)
    assert set(body["fit"]) == {"per_kd", "base_kwh_day", "r2", "n", "through_origin"}


def test_demand_reports_that_the_ac_power_is_known(client) -> None:
    assert client.get("/api/heating/demand?tag=2026-12-15").json()["ac_power_known"] is True


def test_demand_reports_an_unknown_ac_power(
    make_client, days, settings_service, clock, guard, auth
) -> None:
    client = make_client(heating_router(days, settings_service, clock, guard, ac_power_known=False))
    login_as(client, auth, "viewer")
    assert client.get("/api/heating/demand?tag=2026-12-15").json()["ac_power_known"] is False


def test_demand_without_enough_days_has_reason(client, engine) -> None:
    _store(engine, 5)
    body = client.get("/api/heating/demand?tag=2026-12-15").json()
    assert body["fit"] is None and body["reason"] == "Noch 5 von 14 Tagen."


def test_demand_defaults_to_the_current_season(client) -> None:
    assert client.get("/api/heating/demand").json()["season"] == "2026/27"


def test_demand_requires_login(make_client, days, settings_service, clock, guard) -> None:
    client = make_client(heating_router(days, settings_service, clock, guard, ac_power_known=True))
    assert client.get("/api/heating/demand").status_code == 401
