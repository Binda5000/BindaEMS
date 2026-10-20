from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, insert

from bindaems.app.db.schema import charging_session
from bindaems.app.history.influx import Series, check_reading
from bindaems.app.learning.driving_data import (
    DrivingDataError,
    PresencePoint,
    load_history,
)
from bindaems.shared.config import Config
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.settings import DrivingSettings

END = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
START = END - timedelta(days=56)
T1 = START + timedelta(days=1, hours=7)
T2 = T1 + timedelta(hours=9)
HA_TAGS = {"domain": "device_tracker", "entity_id": "tesla"}


def _ha(*rows: tuple[datetime, float | None, str | None], name: str = "state") -> Series:
    return Series(
        name,
        HA_TAGS,
        ["time", "value", "state"],
        [[epoch_ms(ts), value, state] for ts, value, state in rows],
    )


def _status(field: str, *rows: tuple[datetime, object]) -> Series:
    return Series("status", {}, ["time", field], [[epoch_ms(ts), value] for ts, value in rows])


def _soc(*rows: tuple[datetime, float]) -> Series:
    return Series("soc", {}, ["time", "last"], [[epoch_ms(ts), pct] for ts, pct in rows])


async def _load(
    fake_reader,
    engine: Engine,
    cfg: Config,
    key: str = "tesla",
    settings: DrivingSettings | None = None,
):
    return await load_history(
        fake_reader, engine, cfg, settings or DrivingSettings(), key, start=START, end=END
    )


async def test_source_order_prefers_configured_entity(fake_reader, engine, cfg) -> None:
    vehicle = cfg.vehicles["egolf"]  # hat home_entity; dazu ein Tessie-Abschnitt
    assert vehicle.home_entity is not None
    cfg = cfg.model_copy(
        update={
            "vehicles": {
                **cfg.vehicles,
                "egolf": vehicle.model_copy(update={"tessie": cfg.vehicles["tesla"].tessie}),
            }
        }
    )
    settings = DrivingSettings(presence_entities={"egolf": "device_tracker.egolf_zone"})
    history = await _load(fake_reader, engine, cfg, "egolf", settings)
    assert history.source == "ha"
    assert all(db == "homeassistant" for _, db in fake_reader.queries[:2])
    assert "'egolf_zone'" in fake_reader.queries[0][0]

    fake_reader.queries.clear()
    history = await _load(fake_reader, engine, cfg, "egolf")  # ohne Eintrag: home_entity
    assert history.source == "home_entity"
    assert "'egolf'" in fake_reader.queries[0][0]

    plain = cfg.model_copy(
        update={
            "vehicles": {
                **cfg.vehicles,
                "egolf": vehicle.model_copy(update={"home_entity": None, "tessie": None}),
            }
        }
    )
    assert (await _load(fake_reader, engine, plain, "egolf")).source == "sessions"


async def test_home_entity_from_ha_database(fake_reader, engine, cfg) -> None:
    fake_reader.answer("LIMIT 1", [_ha((START - timedelta(days=3), 1.0, "home"))])
    fake_reader.answer(
        "FROM /.*/",
        [_ha((T1, 0.0, None), (T2, 1.0, None), name="state")],
    )
    history = await _load(fake_reader, engine, cfg, "egolf")
    assert history.source == "home_entity"
    assert history.seed is True
    assert history.presence == [PresencePoint(T1, False), PresencePoint(T2, True)]
    window = fake_reader.queries[0][0]
    assert (
        'SELECT "value", "state" FROM /.*/ WHERE "domain" = \'device_tracker\' '
        "AND \"entity_id\" = 'egolf' AND "
    ) in window
    assert f"time >= {epoch_ms(START)}ms AND time <= {epoch_ms(END)}ms" in window


async def test_zone_state_counts_as_away(fake_reader, engine, cfg) -> None:
    fake_reader.answer("FROM /.*/", [_ha((T1, None, "Arbeit"), (T2, None, "home"))])
    settings = DrivingSettings(presence_entities={"tesla": "device_tracker.tesla"})
    history = await _load(fake_reader, engine, cfg, settings=settings)
    assert history.source == "ha"
    assert history.presence == [PresencePoint(T1, False), PresencePoint(T2, True)]


async def test_ha_points_without_any_state_are_ignored(fake_reader, engine, cfg) -> None:
    fake_reader.answer("FROM /.*/", [_ha((T1, None, None), (T2, 1.0, None))])
    settings = DrivingSettings(presence_entities={"tesla": "device_tracker.tesla"})
    history = await _load(fake_reader, engine, cfg, settings=settings)
    assert history.presence == [PresencePoint(T2, True)]


async def test_tessie_at_home_from_status(fake_reader, engine, cfg) -> None:
    fake_reader.answer("LIMIT 1", [_status("value_bool", (START - timedelta(hours=1), True))])
    fake_reader.answer(
        'FROM "status"',
        [_status("value_bool", (T2, True), (T1, False), (T1 + timedelta(hours=1), "x"))],
    )
    history = await _load(fake_reader, engine, cfg)
    assert history.source == "tessie"
    assert history.seed is True
    assert history.presence == [PresencePoint(T1, False), PresencePoint(T2, True)]  # sortiert
    query, database = fake_reader.queries[0]
    assert database is None  # BindaEMS-Datenbank
    assert """FROM "status" WHERE "id" = 'vehicle.tesla.at_home'""" in query


async def test_home_state_from_status_without_ha_database(fake_reader, engine, cfg) -> None:
    influx = cfg.influxdb.model_copy(update={"ha_database": None})
    plain = cfg.model_copy(update={"influxdb": influx, "homeassistant": cfg.homeassistant})
    fake_reader.answer('FROM "status"', [_status("value_str", (T1, "not_home"), (T2, "home"))])
    history = await _load(fake_reader, engine, plain, "egolf")
    assert history.source == "home_entity"
    assert history.presence == [PresencePoint(T1, False), PresencePoint(T2, True)]
    assert "'vehicle.egolf.home_state'" in fake_reader.queries[0][0]
    assert fake_reader.queries[0][1] is None


async def test_configured_entity_without_ha_database_falls_through(
    fake_reader, engine, cfg
) -> None:
    influx = cfg.influxdb.model_copy(update={"ha_database": None})
    plain = cfg.model_copy(update={"influxdb": influx})
    settings = DrivingSettings(presence_entities={"tesla": "device_tracker.tesla"})
    assert (await _load(fake_reader, engine, plain, settings=settings)).source == "tessie"


async def test_seed_is_last_point_before_window(fake_reader, engine, cfg) -> None:
    fake_reader.answer(
        "LIMIT 1",
        [
            _ha((START - timedelta(days=9), 1.0, None), name="a"),
            _ha((START - timedelta(days=2), 0.0, None), name="b"),
        ],
    )
    settings = DrivingSettings(presence_entities={"tesla": "device_tracker.tesla"})
    history = await _load(fake_reader, engine, cfg, settings=settings)
    assert history.seed is False  # der jüngste Punkt vor dem Fenster
    assert history.presence == []
    seed_query = fake_reader.texts("LIMIT 1")[0]
    assert f"time < {epoch_ms(START)}ms" in seed_query and "ORDER BY time DESC" in seed_query

    fake_reader.queries.clear()
    fake_reader._answers.clear()
    assert (await _load(fake_reader, engine, cfg, settings=settings)).seed is None


def _add_session(engine: Engine, vehicle: str, start: datetime, end: datetime | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(charging_session).values(
                wallbox="twc", vehicle=vehicle, start=start, end=end, last_seen=end or END
            )
        )


async def test_sessions_only_without_presence(fake_reader, engine, cfg) -> None:
    plain = cfg.model_copy(
        update={
            "vehicles": {
                **cfg.vehicles,
                "egolf": cfg.vehicles["egolf"].model_copy(update={"home_entity": None}),
            }
        }
    )
    _add_session(engine, "egolf", T1, T2)
    _add_session(engine, "egolf", END - timedelta(hours=2), None)  # offen
    _add_session(engine, "egolf", START - timedelta(days=5), START - timedelta(days=4))  # davor
    history = await _load(fake_reader, engine, plain, "egolf")
    assert history.source == "sessions"
    assert history.presence == [] and history.seed is None
    assert history.sessions == [(T1, T2), (END - timedelta(hours=2), None)]
    assert [q for q in fake_reader.queries if "status" in q[0]] == []


async def test_session_started_before_window_is_kept(fake_reader, engine, cfg) -> None:
    _add_session(engine, "tesla", START - timedelta(hours=3), START + timedelta(hours=2))
    history = await _load(fake_reader, engine, cfg)
    assert history.sessions == [(START - timedelta(hours=3), START + timedelta(hours=2))]


async def test_guest_sessions_are_ignored(fake_reader, engine, cfg) -> None:
    _add_session(engine, "guest", T1, T2)
    _add_session(engine, "unknown", T1, T2)
    _add_session(engine, "egolf", T1, T2)
    _add_session(engine, "tesla", T2, None)
    history = await _load(fake_reader, engine, cfg)
    assert history.sessions == [(T2, None)]


async def test_soc_from_own_database(fake_reader, engine, cfg) -> None:
    fake_reader.answer(
        'FROM "soc"',
        [_soc((T2, 55.5), (T1, 80.0)), Series("soc", {}, ["time", "last"], [[epoch_ms(T1), None]])],
    )
    history = await _load(fake_reader, engine, cfg)
    assert history.soc == [(T1, 80.0), (T2, 55.5)]
    query, database = fake_reader.queries[-1]
    assert database is None
    assert """SELECT last("pct") FROM "soc" WHERE "id" = 'tesla'""" in query
    assert "GROUP BY time(5m) fill(none)" in query
    assert [q for q in fake_reader.queries if q[1] == "homeassistant"] == []


async def test_soc_falls_back_to_ha_entity(fake_reader, engine, cfg) -> None:
    fake_reader.answer(
        "FROM /.*/ WHERE \"domain\" = 'sensor'",
        [
            Series(
                "%",
                {"domain": "sensor", "entity_id": "egolf_soc"},
                ["time", "last"],
                [[epoch_ms(T1), 62.0], [epoch_ms(T2), 48.0]],
            )
        ],
    )
    history = await _load(fake_reader, engine, cfg, "egolf")
    assert history.soc == [(T1, 62.0), (T2, 48.0)]
    query, database = fake_reader.queries[-1]
    assert database == "homeassistant"
    assert "'sensor'" in query and "'egolf_soc'" in query and 'last("value")' in query


async def test_soc_without_entity_stays_empty(fake_reader, engine, cfg) -> None:
    assert (await _load(fake_reader, engine, cfg)).soc == []  # der Tesla hat keine soc_entity


async def test_read_error_raises_driving_data_error(fake_reader, engine, cfg, unreadable) -> None:
    fake_reader.answer('FROM "status"', unreadable())
    with pytest.raises(DrivingDataError, match="nicht lesbar"):
        await _load(fake_reader, engine, cfg)

    fake_reader._answers.clear()
    fake_reader.answer('FROM "soc"', unreadable())
    with pytest.raises(DrivingDataError):
        await _load(fake_reader, engine, cfg)


async def test_database_error_raises_driving_data_error(fake_reader, engine, cfg) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE charging_session")
    with pytest.raises(DrivingDataError, match="Ansteckvorgänge"):
        await _load(fake_reader, engine, cfg)


async def test_unknown_vehicle_raises(fake_reader, engine, cfg) -> None:
    with pytest.raises(DrivingDataError, match="nicht konfiguriert"):
        await _load(fake_reader, engine, cfg, "nirgends")


async def test_malformed_entities_are_not_used(fake_reader, engine, cfg) -> None:
    settings = DrivingSettings.model_construct(presence_entities={"tesla": "ohnepunkt"})
    assert (await _load(fake_reader, engine, cfg, settings=settings)).source == "tessie"


async def test_queries_are_select_only(fake_reader, engine, cfg) -> None:
    egolf_plain = cfg.vehicles["egolf"]
    quoted = cfg.model_copy(
        update={
            "vehicles": {
                **cfg.vehicles,
                "egolf": egolf_plain.model_copy(update={"home_entity": "device_tracker.o'neil"}),
            }
        }
    )
    for key in ("tesla", "egolf"):
        await _load(fake_reader, engine, quoted, key)
    assert len(fake_reader.queries) >= 6
    for query, _ in fake_reader.queries:
        check_reading(query)  # SELECT ohne INTO, eine Anweisung
        assert query.startswith("SELECT") and "INTO" not in query
    assert any("'o\\'neil'" in query for query, _ in fake_reader.queries)
