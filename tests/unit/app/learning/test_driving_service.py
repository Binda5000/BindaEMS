from __future__ import annotations

import asyncio
import threading
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import StatementError
from structlog.testing import capture_logs
from tests.app_helpers import StopScheduler, record_then_stop
from tests.plan_helpers import until
from tests.unit.app.learning.conftest import FakeReader

from bindaems.app.db.schema import driving_profile
from bindaems.app.history.influx import InfluxQueryError, Series
from bindaems.app.learning.driving_profile import VehicleProfile
from bindaems.app.learning.driving_service import COMPONENT, KEEP_DAYS, RUN_AT, DrivingService
from bindaems.app.settings.service import SettingsService
from bindaems.shared.config import Config
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.settings import DrivingSettings
from bindaems.shared.timeutil import LOCAL_TZ, ManualClock

TODAY = date(2026, 12, 1)  # ein Dienstag
NOW = datetime.combine(TODAY, RUN_AT, tzinfo=LOCAL_TZ).astimezone(UTC)
DOWN = "InfluxDB: HTTP 503 nicht erreichbar"


class SwitchReader(FakeReader):
    """Antwortet wie ``FakeReader``; ``down`` lässt jede Abfrage scheitern, ``gate`` hält sie an."""

    def __init__(self) -> None:
        super().__init__()
        self.down = False
        self.gate: asyncio.Event | None = None

    async def query(self, q: str, *, database: str | None = None) -> list[Series]:
        if self.gate is not None:
            await self.gate.wait()
        if self.down:
            raise InfluxQueryError(DOWN)
        return await super().query(q, database=database)


def local(day: date, clock: str) -> datetime:
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=LOCAL_TZ).astimezone(UTC)


def commuter(reader: FakeReader, *, last: date = TODAY - timedelta(days=1)) -> None:
    """Das Tessie-Auto fährt werktags um 07:30 weg (80 %) und kommt um 17:30 zurück (60 %)."""
    first = TODAY - timedelta(days=56)
    presence: list[list[object]] = []
    soc: list[list[object]] = []
    day = first
    while day <= last:
        if day.weekday() < 5:
            presence += [
                [epoch_ms(local(day, "07:30")), False],
                [epoch_ms(local(day, "17:30")), True],
            ]
            soc += [[epoch_ms(local(day, "07:00")), 80.0], [epoch_ms(local(day, "17:40")), 60.0]]
        day += timedelta(days=1)
    seed = [[epoch_ms(local(first - timedelta(days=1), "12:00")), True]]
    reader.answer("DESC LIMIT 1", [Series("status", {}, ["time", "value_bool"], seed)])
    reader.answer("vehicle.tesla.at_home", [Series("status", {}, ["time", "value_bool"], presence)])
    reader.answer('FROM "soc"', [Series("soc", {}, ["time", "last"], soc)])


@pytest.fixture
def reader() -> SwitchReader:
    return SwitchReader()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(NOW)


@pytest.fixture
def make_service(
    reader: SwitchReader, engine: Engine, cfg: Config, settings_service: SettingsService, clock
):
    def make(**kwargs) -> DrivingService:
        return DrivingService(
            reader=reader,
            engine=engine,
            cfg=cfg,
            settings=settings_service,
            clock=clock,
            **kwargs,
        )

    return make


@pytest.fixture
def service(make_service) -> DrivingService:
    return make_service()


def rows(engine: Engine, vehicle: str | None = None):
    statement = select(driving_profile).order_by(driving_profile.c.computed_at)
    if vehicle is not None:
        statement = statement.where(driving_profile.c.vehicle == vehicle)
    with engine.connect() as conn:
        return conn.execute(statement).all()


def put_row(engine: Engine, vehicle: str, computed_at: datetime, profile: VehicleProfile) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(driving_profile).values(
                vehicle=vehicle,
                computed_at=computed_at,
                data_from=profile.data_from,
                data_to=profile.data_to,
                source=profile.source,
                profile=profile.model_dump(mode="json"),
            )
        )


def set_driving(settings_service: SettingsService, driving: DrivingSettings) -> None:
    current = settings_service.current()
    learning = current.settings.learning.model_copy(update={"driving": driving})
    settings_service.update(
        current.settings.model_copy(update={"learning": learning}),
        base_version=current.version,
        actor="test",
        source="ui",
    )


async def test_run_stores_one_row_per_vehicle(service, reader, engine, cfg) -> None:
    commuter(reader)
    assert await service.recompute() is True
    stored = rows(engine)
    assert sorted(r.vehicle for r in stored) == sorted(cfg.vehicles)
    tesla = rows(engine, "tesla")[0]
    assert tesla.computed_at == NOW
    assert tesla.source == "tessie"
    profile = VehicleProfile.model_validate(tesla.profile)
    assert (profile.key, profile.days > 0) == ("tesla", True)
    assert tesla.data_to == profile.data_to
    assert service.profile("tesla") == profile
    assert set(service.profiles()) == set(cfg.vehicles)
    assert service.running() is False


async def test_rows_older_than_70_days_are_deleted(service, reader, engine) -> None:
    commuter(reader)
    assert await service.recompute() is True
    template = VehicleProfile.model_validate(rows(engine, "tesla")[0].profile)
    keep = timedelta(days=KEEP_DAYS)
    put_row(engine, "tesla", NOW - keep - timedelta(seconds=1), template)  # gerade zu alt
    put_row(engine, "egolf", NOW - timedelta(days=100), template)
    put_row(engine, "ghost", NOW - timedelta(days=90), template)  # Auto, das es nicht mehr gibt
    put_row(engine, "tesla", NOW - keep, template)  # genau 70 Tage: bleibt
    put_row(engine, "tesla", NOW - timedelta(days=69), template)
    assert await service.recompute() is True
    ages = [(r.vehicle, NOW - r.computed_at) for r in rows(engine)]
    assert max(age for _, age in ages) == keep
    assert not [v for v, age in ages if age > keep]
    assert len(rows(engine, "tesla")) == 4  # 70 Tage, 69 Tage und zwei Läufe


async def test_insert_and_delete_share_one_transaction(service, reader, engine) -> None:
    commuter(reader)
    await service.recompute()
    old = rows(engine, "tesla")[0]
    template = VehicleProfile.model_validate(old.profile)
    put_row(engine, "tesla", NOW - timedelta(days=80), template)
    broken = template.model_dump(mode="json")
    broken["unserializable"] = object()  # lässt das Einfügen scheitern, nachdem gelöscht wurde
    profile = template.model_copy()
    object.__setattr__(profile, "model_dump", lambda **_: broken)
    with pytest.raises(StatementError):
        service._store(profile, NOW + timedelta(days=1))
    assert len(rows(engine, "tesla")) == 2  # das Löschen wurde zurückgenommen


async def test_failed_run_keeps_last_profile(service, reader, engine) -> None:
    commuter(reader)
    await service.recompute()
    before = service.profile("tesla")
    assert before is not None
    assert service.component_status().ok
    reader.down = True
    with capture_logs() as logs:
        assert await service.recompute() is True  # der Lauf selbst gelang, die Autos nicht
    assert service.profile("tesla") == before
    assert len(rows(engine, "tesla")) == 1  # kein neuer Stand
    status = service.component_status()
    assert (status.name, status.ok) == (COMPONENT, False)
    assert (
        status.message == f"Fahrverhalten fehlgeschlagen: Die Fahrdaten sind nicht lesbar: {DOWN}"
    )
    text = service.status()["status"]["text"]
    assert text == (
        f"Fahrverhalten fehlgeschlagen: Die Fahrdaten sind nicht lesbar: {DOWN}. "
        "Es gilt der Stand vom 01.12. 03:40."
    )
    assert any(e["log_level"] == "warning" for e in logs)
    reader.down = False  # beim nächsten Lauf ist der Fehler weg
    await service.recompute()
    assert service.component_status().ok
    assert service.status()["status"]["text"] == "Fahrverhalten berechnet am 01.12. 03:40."


async def test_failure_without_earlier_profile_has_no_tail(service, reader) -> None:
    reader.down = True
    await service.recompute()
    assert service.profile("tesla") is None
    text = service.status()["status"]["text"]
    assert text == f"Fahrverhalten fehlgeschlagen: Die Fahrdaten sind nicht lesbar: {DOWN}"


async def test_unexpected_error_of_one_vehicle_does_not_stop_the_others(
    service, reader, engine, monkeypatch
) -> None:
    commuter(reader)
    original = service._store

    def store(profile: VehicleProfile, now: datetime) -> None:
        if profile.key == "tesla":
            raise RuntimeError("Platte voll")
        original(profile, now)

    monkeypatch.setattr(service, "_store", store)
    with capture_logs() as logs:
        await service.recompute()
    assert [r.vehicle for r in rows(engine)] == ["egolf"]
    assert service.component_status().message == "Fahrverhalten fehlgeschlagen: Platte voll"
    assert any(e["log_level"] == "error" for e in logs)


async def test_profiles_load_from_database_at_start(
    make_service, service, reader, engine, cfg
) -> None:
    commuter(reader)
    await service.recompute()
    template = VehicleProfile.model_validate(rows(engine, "tesla")[0].profile)
    put_row(engine, "ghost", NOW + timedelta(hours=1), template)  # unbekanntes Auto: übergangen
    later = make_service()
    assert later.profile("tesla") is None
    await later.load_stored()
    assert later.profile("tesla") == service.profile("tesla")
    assert set(later.profiles()) == set(cfg.vehicles)
    assert later.status()["computed_at"] == NOW.isoformat()


async def test_load_prefers_the_newest_readable_row(make_service, service, reader, engine) -> None:
    commuter(reader)
    await service.recompute()
    good = rows(engine, "tesla")[0]
    with engine.begin() as conn:
        conn.execute(
            insert(driving_profile).values(
                vehicle="tesla",
                computed_at=NOW + timedelta(days=1),
                data_from=good.data_from,
                data_to=good.data_to,
                source="tessie",
                profile={"key": "tesla"},  # unlesbar
            )
        )
    later = make_service()
    with capture_logs() as logs:
        await later.load_stored()
    assert later.profile("tesla") == service.profile("tesla")
    assert any("unlesbar" in e["event"] for e in logs)


async def test_load_survives_an_unreadable_database(make_service, engine) -> None:
    later = make_service()
    engine.dispose()
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE driving_profile")
    with capture_logs() as logs:
        await later.load_stored()
    assert later.profiles() == {}
    assert any(e["log_level"] == "error" for e in logs)


async def test_run_loads_computes_and_waits_for_the_night(
    make_service, service, reader, engine, cfg
) -> None:
    commuter(reader)
    await service.recompute()
    reader.down = True
    sleeps: list[float] = []
    later = make_service(sleep=record_then_stop(sleeps, 1))
    with pytest.raises(StopScheduler):
        await later.run()
    # der Stand aus der Datenbank gilt trotz des Fehlers; bis morgen 03:40 Uhr
    assert later.profile("tesla") == service.profile("tesla")
    assert later.component_status().ok is False
    assert sleeps == [86400.0]


async def test_run_computes_at_start_and_every_night(make_service, reader, engine, cfg) -> None:
    commuter(reader)
    sleeps: list[float] = []
    later = make_service(sleep=record_then_stop(sleeps, 2))
    with pytest.raises(StopScheduler):
        await later.run()
    assert len(rows(engine)) == 2 * len(cfg.vehicles)  # beim Start und nach der ersten Wartezeit
    assert sleeps == [86400.0, 86400.0]


async def test_night_run_waits_until_the_next_0340_local(make_service, clock) -> None:
    clock.advance(timedelta(hours=1))  # 04:40 Uhr: der nächste Lauf ist morgen um 03:40
    sleeps: list[float] = []
    later = make_service(sleep=record_then_stop(sleeps, 1))
    with pytest.raises(StopScheduler):
        await later.run()
    assert sleeps == [(24 - 1) * 3600.0]


async def test_a_failing_night_run_does_not_end_the_loop(make_service, monkeypatch, engine) -> None:
    sleeps: list[float] = []
    later = make_service(sleep=record_then_stop(sleeps, 2))

    async def broken() -> None:
        raise RuntimeError("kaputt")

    monkeypatch.setattr(later, "_compute_all", broken)
    with capture_logs() as logs, pytest.raises(StopScheduler):
        await later.run()
    assert len(sleeps) == 2
    assert [e["log_level"] for e in logs].count("error") == 2


async def test_recompute_while_running_returns_false(service, reader) -> None:
    commuter(reader)
    reader.gate = asyncio.Event()
    first = asyncio.create_task(service.recompute())
    await until(service.running)
    assert service.status()["running"] is True
    assert await service.recompute() is False
    reader.gate.set()
    assert await first is True
    assert service.running() is False
    assert await service.recompute() is True  # danach wieder frei


async def test_running_is_cleared_after_a_crash(service, monkeypatch) -> None:
    async def broken() -> None:
        raise RuntimeError("kaputt")

    monkeypatch.setattr(service, "_compute_all", broken)
    with pytest.raises(RuntimeError):
        await service.recompute()
    assert service.running() is False


async def test_each_completed_run_calls_the_hooks(service, reader, monkeypatch) -> None:
    commuter(reader)
    calls: list[str] = []

    def broken() -> None:
        calls.append("kaputt")
        raise RuntimeError("kaputt")

    service.on_run_done(broken)
    service.on_run_done(lambda: calls.append(f"danach {service.running()}"))
    with capture_logs() as logs:
        assert await service.recompute() is True
    # nach dem Lauf, in der Reihenfolge der Anmeldung; ein Fehler hält die übrigen nicht auf
    assert calls == ["kaputt", "danach False"]
    assert "Rückmeldung nach dem Lauf des Fahrverhaltens fehlgeschlagen" in [
        e["event"] for e in logs
    ]

    async def crashed() -> None:
        raise RuntimeError("kaputt")

    monkeypatch.setattr(service, "_compute_all", crashed)
    with pytest.raises(RuntimeError):
        await service.recompute()
    assert len(calls) == 2  # ein abgebrochener Lauf meldet sich nicht


async def test_unknown_vehicle_in_settings_is_ignored(
    service, reader, engine, cfg, settings_service
) -> None:
    commuter(reader)
    set_driving(
        settings_service,
        DrivingSettings(
            automation={"ghost": True, "tesla": False},
            presence_entities={"ghost": "device_tracker.ghost"},
        ),
    )
    with capture_logs() as logs:
        await service.recompute()
    assert sorted(r.vehicle for r in rows(engine)) == sorted(cfg.vehicles)
    assert service.component_status().ok
    assert not any("ghost" in q for q, _ in reader.queries)
    assert not [e for e in logs if e["log_level"] in ("warning", "error")]


async def test_settings_of_the_run_reach_the_profile(service, reader, settings_service) -> None:
    commuter(reader)
    set_driving(settings_service, DrivingSettings(return_reserve_pct=30, target_min_pct=20))
    await service.recompute()
    profile = service.profile("tesla")
    assert profile is not None
    assert profile.weekdays[1].target_soc_pct == 50  # 30 Reserve + 20 Verbrauch


async def test_status_before_the_first_run(service, cfg) -> None:
    status = service.status()
    assert status["computed_at"] is None
    assert status["running"] is False
    assert status["status"] == {"ok": True, "text": "Noch keine Fahrdaten."}
    assert [v["key"] for v in status["vehicles"]] == list(cfg.vehicles)
    tesla = status["vehicles"][0]
    assert tesla["name"] == "Tesla Model 3"
    assert (tesla["days"], tesla["weekdays"], tesla["presence"], tesla["next_trip"]) == (
        0,
        [],
        [],
        None,
    )
    assert tesla["quality"] == {
        "days": 28,
        "departure_n": 0,
        "departure_hit_pct": None,
        "target_n": 0,
        "target_hit_pct": None,
    }
    assert service.component_status().message == "Noch keine Fahrdaten"
    assert service.component_status().ok


async def test_status_without_any_data_after_a_run(service) -> None:
    await service.recompute()  # die Quellen antworten leer
    status = service.status()
    assert status["status"] == {"ok": True, "text": "Noch keine Fahrdaten."}
    assert all(v["days"] == 0 for v in status["vehicles"])
    assert status["vehicles"][0]["weekdays"] != []  # der Stand ist da, nur ohne Tage


async def test_status_shape_after_a_run(service, reader) -> None:
    commuter(reader)
    await service.recompute()
    status = service.status()
    assert status["computed_at"] == NOW.isoformat()
    assert status["status"] == {"ok": True, "text": "Fahrverhalten berechnet am 01.12. 03:40."}
    assert service.component_status().message == "Fahrverhalten aktuell"
    tesla = next(v for v in status["vehicles"] if v["key"] == "tesla")
    assert set(tesla) == {
        "key",
        "name",
        "source",
        "data_from",
        "data_to",
        "days",
        "weekdays",
        "next_trip",
        "presence",
        "quality",
    }
    assert (tesla["source"], tesla["days"]) == ("tessie", 56)
    assert len(tesla["weekdays"]) == 7
    monday = tesla["weekdays"][0]
    assert monday == {
        "weekday": 0,
        "basis": "weekday",
        "days": 7,  # acht Montage im Fenster, der 26.10. ist ein Feiertag und zählt als Sonntag
        "trips": 7,
        "trip_pct": 100,
        "departure": "07:30",
        "departure_p50": "07:30",
        "departure_p80": "07:30",
        "departure_confidence": "high",
        "arrival_p50": "17:30",
        "arrival_soc_p50": 60.0,
        "consumption_p50_pp": 20.0,
        "consumption_p80_pp": 20.0,
        "target_soc_pct": 50,
        "target_clamped": True,
        "target_confidence": "high",
    }
    saturday = tesla["weekdays"][5]
    assert (saturday["trips"], saturday["trip_pct"], saturday["departure"]) == (0, 0, None)
    # heute (Dienstag) um 07:30 liegt nach 03:40 Uhr: die nächste Fahrt ist heute
    assert tesla["next_trip"] == {
        "date": "2026-12-01",
        "departure": "07:30",
        "departure_confidence": "high",
        "target_soc_pct": 50,
        "target_confidence": "high",
    }
    presence = tesla["presence"]
    assert len(presence) == 192
    first = NOW - timedelta(minutes=10)  # die laufende Viertelstunde (03:30 lokal)
    assert presence[0]["start"] == first.isoformat()
    assert presence[1]["start"] == (first + timedelta(minutes=15)).isoformat()
    assert (
        presence[-1]["start"] == (first + timedelta(hours=48) - timedelta(minutes=15)).isoformat()
    )
    assert all(set(p) == {"start", "p"} and 0.0 <= p["p"] <= 1.0 for p in presence)
    other = next(v for v in status["vehicles"] if v["key"] == "egolf")
    assert other["days"] == 0 and other["next_trip"] is None


async def test_quality_uses_the_row_of_the_evening_before(service, reader, engine) -> None:
    commuter(reader)
    await service.recompute()
    template = VehicleProfile.model_validate(rows(engine, "tesla")[0].profile)
    # ein Stand von vor 40 Tagen gilt für alle Tage der letzten 28 Tage
    put_row(engine, "tesla", NOW - timedelta(days=40), template)
    await service.recompute()
    quality = next(v for v in service.status()["vehicles"] if v["key"] == "tesla")["quality"]
    assert quality == {
        "days": 28,
        "departure_n": 20,  # 20 Werktage von 03.11. bis 30.11.
        "departure_hit_pct": 100,
        "target_n": 20,
        "target_hit_pct": 100,
    }


async def test_quality_counts_misses_and_rounds(service, reader, engine) -> None:
    commuter(reader)
    await service.recompute()
    template = VehicleProfile.model_validate(rows(engine, "tesla")[0].profile)
    late = template.model_copy(
        update={
            "weekdays": [
                w.model_copy(update={"departure_min": 480, "target_soc_pct": 55})
                for w in template.weekdays
            ]
        }
    )
    put_row(engine, "tesla", NOW - timedelta(days=40), late)
    with engine.begin() as conn:  # die Stände von heute früh entfallen: nur der verspätete gilt
        conn.execute(driving_profile.delete().where(driving_profile.c.computed_at == NOW))
    await service.recompute()
    quality = next(v for v in service.status()["vehicles"] if v["key"] == "tesla")["quality"]
    assert quality["departure_n"] == 20
    assert quality["departure_hit_pct"] == 0  # Abfahrt 07:30 liegt vor dem Vorschlag 08:00
    assert quality["target_hit_pct"] == 100  # 55 - 20 = 35 >= 20


async def test_quality_failure_keeps_the_stored_profile(
    service, reader, engine, monkeypatch
) -> None:
    commuter(reader)

    def broken(key: str):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(service, "_snapshots", broken)
    with capture_logs() as logs:
        await service.recompute()
    assert len(rows(engine, "tesla")) == 1
    assert service.component_status().ok
    assert any(e["log_level"] == "error" for e in logs)


async def test_load_takes_the_newest_row_not_the_last_inserted(
    make_service, service, reader, engine
) -> None:
    commuter(reader)
    await service.recompute()
    template = VehicleProfile.model_validate(rows(engine, "tesla")[0].profile)
    with engine.begin() as conn:
        conn.execute(driving_profile.delete())
    # das jüngste Datum zuerst eingefügt: die Reihenfolge der Zeilen sagt nichts
    put_row(engine, "tesla", NOW - timedelta(days=1), template.model_copy(update={"days": 22}))
    put_row(engine, "tesla", NOW - timedelta(days=3), template.model_copy(update={"days": 11}))
    put_row(engine, "tesla", NOW - timedelta(days=2), template.model_copy(update={"days": 33}))
    later = make_service()
    await later.load_stored()
    profile = later.profile("tesla")
    assert profile is not None
    assert profile.days == 22
    assert later.status()["computed_at"] == (NOW - timedelta(days=1)).isoformat()


async def test_load_checks_no_older_row_than_the_one_used(
    make_service, service, reader, engine
) -> None:
    commuter(reader)
    await service.recompute()
    good = rows(engine, "tesla")[0]
    with engine.begin() as conn:
        conn.execute(
            insert(driving_profile).values(
                vehicle="tesla",
                computed_at=NOW - timedelta(days=5),
                data_from=good.data_from,
                data_to=good.data_to,
                source="tessie",
                profile={"key": "tesla"},  # älter als der gültige Stand: bleibt ungeprüft
            )
        )
    later = make_service()
    with capture_logs() as logs:
        await later.load_stored()
    assert later.profile("tesla") == service.profile("tesla")
    assert not [e for e in logs if e["log_level"] == "warning"]


async def test_close_cancels_the_run_and_waits_for_the_thread(service, reader, monkeypatch) -> None:
    commuter(reader)
    entered, release = threading.Event(), threading.Event()
    original = service._store

    def blocked(profile: VehicleProfile, now: datetime) -> None:
        entered.set()
        release.wait(5)
        original(profile, now)

    monkeypatch.setattr(service, "_store", blocked)
    run = asyncio.create_task(service.recompute())
    await until(entered.is_set)
    closing = asyncio.create_task(service.close(5.0))
    await until(run.done)  # der Lauf ist abgebrochen, der Thread blockiert noch
    assert run.cancelled()
    await asyncio.sleep(0.05)
    assert not closing.done()  # ``close`` wartet auf den Thread
    release.set()
    assert await closing is True
    assert service.running() is False
    assert service._threads == set()


async def test_close_gives_up_after_the_timeout(service, reader, monkeypatch) -> None:
    commuter(reader)
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(service, "_store", lambda profile, now: (entered.set(), release.wait(5)))
    run = asyncio.create_task(service.recompute())
    await until(entered.is_set)
    assert await service.close(0.05) is False  # der Thread rechnet noch
    release.set()
    await until(lambda: not service._threads)
    assert run.cancelled()


async def test_close_without_work_returns_at_once(service) -> None:
    assert await service.close(0.0) is True


async def test_close_cancels_the_run_loop(make_service, reader) -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.sleep(3600)

    later = make_service(sleep=sleep)
    task = asyncio.create_task(later.run())
    await until(lambda: bool(sleeps))
    assert await later.close(1.0) is True
    assert task.cancelled()


async def test_skipped_run_is_logged_and_not_queued(service, reader, engine) -> None:
    commuter(reader)
    reader.gate = asyncio.Event()
    first = asyncio.create_task(service.recompute())
    await until(service.running)
    with capture_logs() as logs:
        await service._recompute_logged()
    skipped = [
        e for e in logs if e["event"].startswith("Berechnung des Fahrverhaltens übersprungen")
    ]
    assert [e["log_level"] for e in skipped] == ["warning"]
    reader.gate.set()
    assert await first is True
    assert len(rows(engine, "tesla")) == 1  # kein zweiter Lauf hinterher
    with capture_logs() as logs:
        await service._recompute_logged()  # frei: kein Hinweis
    assert not [e for e in logs if "übersprungen" in e["event"]]


def test_the_night_run_is_at_0340() -> None:
    assert RUN_AT == time(3, 40)


@pytest.mark.parametrize(
    ("now", "due"),
    [
        # Winterzeit (MEZ, UTC+1): 03:40 Uhr sind 02:40 UTC
        (datetime(2026, 12, 1, 1, 0, tzinfo=UTC), datetime(2026, 12, 1, 2, 40, tzinfo=UTC)),
        (datetime(2026, 12, 1, 2, 40, tzinfo=UTC), datetime(2026, 12, 2, 2, 40, tzinfo=UTC)),
        # Sommerzeit (MESZ, UTC+2): 03:40 Uhr sind 01:40 UTC
        (datetime(2026, 7, 1, 0, 0, tzinfo=UTC), datetime(2026, 7, 1, 1, 40, tzinfo=UTC)),
        # Umstellung auf Sommerzeit, Nacht zum 29.03.2026 (02:00 wird 03:00): nur 23 h von 03:40
        # MEZ bis 03:40 MESZ
        (datetime(2026, 3, 28, 2, 40, tzinfo=UTC), datetime(2026, 3, 29, 1, 40, tzinfo=UTC)),
        (datetime(2026, 3, 29, 0, 0, tzinfo=UTC), datetime(2026, 3, 29, 1, 40, tzinfo=UTC)),
        (datetime(2026, 3, 29, 1, 40, tzinfo=UTC), datetime(2026, 3, 30, 1, 40, tzinfo=UTC)),
        # Umstellung auf Winterzeit, Nacht zum 25.10.2026 (03:00 wird 02:00): 25 h von 03:40 MESZ
        # bis 03:40 MEZ
        (datetime(2026, 10, 24, 1, 40, tzinfo=UTC), datetime(2026, 10, 25, 2, 40, tzinfo=UTC)),
        (datetime(2026, 10, 25, 0, 0, tzinfo=UTC), datetime(2026, 10, 25, 2, 40, tzinfo=UTC)),
        (datetime(2026, 10, 25, 2, 40, tzinfo=UTC), datetime(2026, 10, 26, 2, 40, tzinfo=UTC)),
    ],
)
async def test_next_night_run_is_0340_local_as_an_exact_instant(make_service, now, due) -> None:
    sleeps: list[float] = []
    later = make_service(sleep=record_then_stop(sleeps, 1))
    later._clock = ManualClock(now)
    with pytest.raises(StopScheduler):
        await later.run()
    assert now + timedelta(seconds=sleeps[0]) == due
    assert due.astimezone(LOCAL_TZ).time() == time(3, 40)
