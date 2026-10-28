from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, insert, select
from structlog.testing import capture_logs
from tests.app_helpers import StopScheduler, record_then_stop
from tests.plan_helpers import until
from tests.unit.app.learning.conftest import FakeReader
from tests.unit.app.learning.test_thermal_model import FIRST_DAY, model, synthetic

from bindaems.app.db.schema import thermal_model
from bindaems.app.learning import thermal_service as service_module
from bindaems.app.learning.thermal_data import ThermalDataError, ThermalSlot
from bindaems.app.learning.thermal_model import (
    FIT_DAYS,
    ModeModel,
    ModeProfile,
    fit_mode,
    mode_profile,
)
from bindaems.app.learning.thermal_service import (
    COMPONENT,
    KEEP_DAYS,
    RUN_AT,
    ThermalModelService,
    reason_text,
    thermal_device,
)
from bindaems.app.learning.weather import WeatherArchive
from bindaems.shared.config import Config
from bindaems.shared.timeutil import LOCAL_TZ, ManualClock

DEVICE = "klima"
TODAY = FIRST_DAY + timedelta(days=21)  # der Tag nach drei Wochen des erfundenen Hauses
NOW = datetime.combine(TODAY, RUN_AT, tzinfo=LOCAL_TZ).astimezone(UTC)
HOUSE = synthetic(days=21, tau_h=40, u=120)  # Heizen taugt, Kühlen nicht (keine Tage)
DOWN = "Die Daten der Klimaanlage sind nicht lesbar: InfluxDB: HTTP 503"


class FakeRead:
    """Ersatz für ``read_thermal_slots``: liefert die Slots im Fenster und merkt sich die Aufrufe;
    ``error`` lässt das Lesen scheitern, ``gate`` hält es an."""

    def __init__(self, slots: Sequence[ThermalSlot] = HOUSE) -> None:
        self.slots = list(slots)
        self.calls: list[tuple[object, object, str, datetime, datetime]] = []
        self.error: Exception | None = None
        self.gate: asyncio.Event | None = None

    async def __call__(
        self, reader: object, weather: object, device: str, start: datetime, end: datetime
    ) -> list[ThermalSlot]:
        self.calls.append((reader, weather, device, start, end))
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return [s for s in self.slots if start <= s.start < end]


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(NOW)


@pytest.fixture
def weather(engine: Engine) -> WeatherArchive:
    return WeatherArchive(engine)


@pytest.fixture
def read() -> FakeRead:
    return FakeRead()


@pytest.fixture
def make_service(engine: Engine, weather: WeatherArchive, clock: ManualClock, read: FakeRead):
    reader = FakeReader()

    def make(**kwargs) -> ThermalModelService:
        values = {
            "reader": reader,
            "weather": weather,
            "engine": engine,
            "device": DEVICE,
            "clock": clock,
            "read": read,
        }
        values.update(kwargs)
        return ThermalModelService(**values)

    return make


@pytest.fixture
def service(make_service) -> ThermalModelService:
    return make_service()


def rows(engine: Engine):
    statement = select(thermal_model).order_by(thermal_model.c.computed_at, thermal_model.c.mode)
    with engine.connect() as conn:
        return conn.execute(statement).all()


def put_row(engine: Engine, computed_at: datetime, m: ModeModel, *, params=None) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(thermal_model).values(
                computed_at=computed_at,
                device=DEVICE,
                mode=m.mode,
                params=asdict(m) if params is None else params,
                profile={"setpoint_c": [[0, 28, 21.5]], "on_share": [[0, 28, 0.75]]},
                usable=m.usable,
                reason=m.reason,
            )
        )


# --- Lauf und Ablage --------------------------------------------------------------------------


async def test_run_stores_one_row_per_mode(service, engine, read) -> None:
    assert await service.recompute() is True
    stored = rows(engine)
    assert [(r.device, r.mode) for r in stored] == [(DEVICE, "heizen"), (DEVICE, "kuehlen")]
    assert all(r.computed_at == NOW for r in stored)
    # Fenster 28 Tage bis jetzt, für das erste Gerät der Art ``coe_offset``
    [(_, _, device, start, end)] = read.calls
    assert (device, start, end) == (DEVICE, NOW - timedelta(days=FIT_DAYS), NOW)
    heating, cooling = stored
    expected = fit_mode(HOUSE, "heizen")
    assert heating.params == asdict(expected)
    assert (heating.usable, heating.reason) == (True, "ok")
    assert (cooling.usable, cooling.reason) == (False, "no_data")
    current = service.current("heizen")
    assert current is not None
    m, profile, computed_at = current
    assert m == expected
    assert profile == mode_profile(HOUSE, "heizen", today=TODAY)
    assert computed_at == NOW


async def test_stored_state_is_loaded_at_start(make_service, engine, read) -> None:
    first = make_service()
    await first.recompute()
    later = make_service()
    assert later.current("heizen") is None
    await later.load_stored()
    assert later.current("heizen") == first.current("heizen")
    assert later.current("kuehlen") == first.current("kuehlen")


async def test_loading_skips_unreadable_rows(service, engine) -> None:
    good = model()
    put_row(engine, NOW - timedelta(days=2), good)
    put_row(engine, NOW - timedelta(days=1), good, params={"mode": "heizen", "days": "viele"})
    with capture_logs() as logs:
        await service.load_stored()
    current = service.current("heizen")
    assert current is not None
    assert current[0] == good
    assert current[1] == ModeProfile(setpoint_c={(0, 28): 21.5}, on_share={(0, 28): 0.75})
    assert current[2] == NOW - timedelta(days=2)
    assert "Ein abgelegtes Wärmemodell ist unlesbar" in [e["event"] for e in logs]


@pytest.mark.parametrize(
    ("params", "profile"),
    [
        ({**asdict(model()), "mode": "lueften"}, None),
        ({**asdict(model()), "reason": "zu_kalt"}, None),
        ({**asdict(model()), "usable": 1}, None),
        ({**asdict(model()), "days": 3.5}, None),
        ({**asdict(model()), "events": None}, None),
        ({**asdict(model()), "u_w_per_k": "120"}, None),
        ({**asdict(model()), "r2": True}, None),
        ([1, 2, 3], None),
        (None, {"setpoint_c": [[0, 28, "warm"]], "on_share": []}),
        (None, {"setpoint_c": [[0, 28, True]], "on_share": []}),
        (None, {"setpoint_c": [[0, 28]], "on_share": []}),
        (None, {"setpoint_c": []}),
        (None, {"setpoint_c": 5, "on_share": []}),
        (None, [[0, 28, 21.0]]),
    ],
)
def test_unreadable_rows_are_rejected(service, engine, params, profile) -> None:
    put_row(engine, NOW, model(), params=params)
    if profile is not None:
        with engine.begin() as conn:
            conn.execute(thermal_model.update().values(profile=profile))
    with capture_logs() as logs:
        assert service._newest("heizen") is None
    assert "Ein abgelegtes Wärmemodell ist unlesbar" in [e["event"] for e in logs]


def test_stored_numbers_come_back_as_floats(service, engine) -> None:
    params = {**asdict(model()), "u_w_per_k": 120, "tau_h": None}
    put_row(engine, NOW, model(), params=params)
    state = service._newest("heizen")
    assert state is not None
    assert state.model == model(tau_h=None)
    assert isinstance(state.model.u_w_per_k, float)


async def test_unreadable_database_at_start_is_logged(service, monkeypatch) -> None:
    def broken() -> None:
        raise RuntimeError("Datenbank gesperrt")

    monkeypatch.setattr(service, "_latest", broken)
    with capture_logs() as logs:
        await service.load_stored()
    assert service.current("heizen") is None
    assert "Abgelegte Wärmemodelle nicht gelesen" in [e["event"] for e in logs]


async def test_failed_run_keeps_last_model(service, engine, read, clock) -> None:
    await service.recompute()
    before = service.current("heizen")
    clock.advance(timedelta(days=1))
    read.error = ThermalDataError(DOWN)
    with capture_logs() as logs:
        assert await service.recompute() is True
    assert service.current("heizen") == before
    assert len(rows(engine)) == 2  # kein neuer Stand
    assert "Daten der Klimaanlage nicht gelesen, das letzte Wärmemodell gilt" in [
        e["event"] for e in logs
    ]
    status = service.component_status()
    assert (status.name, status.ok) == (COMPONENT, False)
    assert status.message == f"Wärmemodell fehlgeschlagen: {DOWN}"
    # der nächste gelungene Lauf nimmt den Fehler zurück
    read.error = None
    await service.recompute()
    assert service.component_status().message == "Wärmemodell aktuell"


async def test_failed_fit_keeps_last_model(service, engine, monkeypatch) -> None:
    await service.recompute()
    before = service.current("kuehlen")

    def broken(slots, mode):
        raise ValueError("Matrix kaputt")

    monkeypatch.setattr(service_module, "fit_mode", broken)
    with capture_logs() as logs:
        await service.recompute()
    assert service.current("kuehlen") == before
    assert len(rows(engine)) == 2
    assert "Wärmemodell nicht berechnet, das letzte gilt" in [e["event"] for e in logs]
    assert service.component_status().message == "Wärmemodell fehlgeschlagen: Matrix kaputt"


async def test_rows_older_than_70_days_are_pruned(service, engine) -> None:
    old, kept = NOW - timedelta(days=KEEP_DAYS + 1), NOW - timedelta(days=KEEP_DAYS - 1)
    put_row(engine, old, model())
    put_row(engine, kept, model())
    await service.recompute()
    assert [r.computed_at for r in rows(engine)] == [kept, NOW, NOW]


async def test_failed_run_prunes_nothing(service, engine, read) -> None:
    put_row(engine, NOW - timedelta(days=KEEP_DAYS + 1), model())
    read.error = ThermalDataError(DOWN)
    await service.recompute()
    assert len(rows(engine)) == 1


async def test_new_model_calls_on_change(service, read) -> None:
    calls: list[str] = []

    def broken() -> None:
        raise RuntimeError("Planung weg")

    service.on_change(lambda: calls.append("a"))
    service.on_change(broken)
    service.on_change(lambda: calls.append("b"))
    with capture_logs() as logs:
        await service.recompute()
    assert calls == ["a", "b"]  # ein Fehler im Rückruf hält die übrigen nicht auf
    assert "Rückmeldung nach einem neuen Wärmemodell fehlgeschlagen" in [e["event"] for e in logs]
    read.error = ThermalDataError(DOWN)
    await service.recompute()
    assert calls == ["a", "b"]  # ohne neues Modell kein Rückruf


async def test_run_without_data_keeps_the_last_model(service, engine, read, clock) -> None:
    # Plan-Review, Schwerpunkt 2: ein Lauf ohne Daten behält den letzten Stand
    await service.recompute()
    before = (service.current("heizen"), service.current("kuehlen"))
    assert before[0] is not None and before[0][0].usable
    stamp = f"{NOW.astimezone(LOCAL_TZ):%d.%m. %H:%M}"
    calls: list[str] = []
    service.on_change(lambda: calls.append("neu"))
    clock.advance(timedelta(days=40))  # das ganze Fenster ohne eine Viertelstunde
    with capture_logs() as logs:
        assert await service.recompute() is True
    assert (service.current("heizen"), service.current("kuehlen")) == before
    assert len(rows(engine)) == 2  # keine Zeile „keine Daten“
    assert calls == []  # kein neuer Stand, kein neuer Plan
    assert "Keine Daten der Klimaanlage im Fenster, das letzte Wärmemodell gilt" in [
        e["event"] for e in logs
    ]
    status = service.component_status()
    assert (status.ok, status.message) == (
        True,
        "Keine Daten der Klimaanlage im Fenster, das letzte Wärmemodell gilt",
    )
    assert service.status()["status"] == {
        "ok": True,
        "text": (
            "Keine Daten der Klimaanlage im Fenster, das letzte Wärmemodell gilt: "
            f"Stand vom {stamp}."
        ),
    }
    # der nächste Lauf mit Daten legt wieder ab und meldet „aktuell“
    clock.advance(timedelta(days=-40))
    await service.recompute()
    assert calls == ["neu"]
    assert service.component_status().message == "Wärmemodell aktuell"


async def test_run_without_data_and_without_state_stores_nothing(service, engine, read) -> None:
    read.slots = []
    calls: list[str] = []
    service.on_change(lambda: calls.append("neu"))
    await service.recompute()
    assert rows(engine) == []
    assert calls == []
    assert service.current("heizen") is None
    assert service.component_status().message == "Noch keine Daten der Klimaanlage"
    assert service.status()["status"] == {"ok": True, "text": "Noch keine Daten der Klimaanlage."}


async def test_loading_a_stored_model_calls_on_change(make_service, engine) -> None:
    # nach einem Neustart plant die Planung gleich mit dem abgelegten Modell neu
    await make_service().recompute()
    later = make_service()
    calls: list[str] = []
    later.on_change(lambda: calls.append("neu"))
    await later.load_stored()
    assert calls == ["neu"]


async def test_loading_nothing_calls_no_on_change(service) -> None:
    calls: list[str] = []
    service.on_change(lambda: calls.append("neu"))
    await service.load_stored()
    assert calls == []


async def test_start_run_refuses_while_running(service, read) -> None:
    read.gate = asyncio.Event()
    assert service.running is False
    assert service.start_run() is True
    assert service.running is True  # gilt sofort, auch bevor die Aufgabe beginnt
    assert service.start_run() is False
    assert await service.recompute() is False
    read.gate.set()
    await until(lambda: not service.running)
    assert service.current("heizen") is not None
    assert service.start_run() is True  # danach wieder frei
    await until(lambda: not service.running)


async def test_start_run_logs_a_crash_and_frees_the_run(service, monkeypatch) -> None:
    async def crash() -> None:
        raise RuntimeError("kaputt")

    monkeypatch.setattr(service, "_compute", crash)
    with capture_logs() as logs:
        assert service.start_run() is True
        await until(lambda: not service.running)
        await asyncio.sleep(0)
    assert "Berechnung des Wärmemodells fehlgeschlagen" in [e["event"] for e in logs]


async def test_run_loads_computes_and_then_waits_until_0350(make_service, engine, read) -> None:
    put_row(engine, NOW - timedelta(days=1), model())
    sleeps: list[float] = []
    service = make_service(sleep=record_then_stop(sleeps, after=2))
    read.error = ThermalDataError(DOWN)  # der Start rechnet, scheitert und behält den Stand
    with pytest.raises(StopScheduler):
        await service.run()
    assert len(read.calls) == 2  # beim Start und um 03:50
    assert sleeps == [24 * 3600.0, 24 * 3600.0]  # ManualClock steht: wieder morgen 03:50
    current = service.current("heizen")
    assert current is not None and current[0] == model()


async def test_run_survives_an_unexpected_error(make_service, monkeypatch) -> None:
    sleeps: list[float] = []
    service = make_service(sleep=record_then_stop(sleeps, after=1))

    async def crash() -> None:
        raise RuntimeError("kaputt")

    monkeypatch.setattr(service, "_compute", crash)
    with capture_logs() as logs, pytest.raises(StopScheduler):
        await service.run()
    assert "Berechnung des Wärmemodells fehlgeschlagen" in [e["event"] for e in logs]
    assert service.running is False


async def test_run_skips_the_start_while_a_run_is_going(make_service, read) -> None:
    sleeps: list[float] = []
    service = make_service(sleep=record_then_stop(sleeps, after=1))
    read.gate = asyncio.Event()
    assert service.start_run() is True
    with capture_logs() as logs, pytest.raises(StopScheduler):
        await service.run()
    assert "Berechnung des Wärmemodells übersprungen, weil schon eine läuft" in [
        e["event"] for e in logs
    ]
    read.gate.set()
    await until(lambda: not service.running)


async def test_first_run_waits_for_the_weather_backfill(make_service, read) -> None:
    ready = asyncio.Event()
    sleeps: list[float] = []
    service = make_service(sleep=record_then_stop(sleeps, after=1), ready=ready.wait)
    task = asyncio.create_task(service.run())
    await asyncio.sleep(0.05)
    assert read.calls == []
    ready.set()
    with pytest.raises(StopScheduler):
        await task
    assert len(read.calls) == 1


async def test_first_run_does_not_wait_forever(make_service, read, monkeypatch) -> None:
    monkeypatch.setattr(service_module, "READY_WAIT_S", 0.01)
    sleeps: list[float] = []
    service = make_service(sleep=record_then_stop(sleeps, after=1), ready=asyncio.Event().wait)
    with capture_logs() as logs, pytest.raises(StopScheduler):
        await service.run()
    assert len(read.calls) == 1
    assert "Nachholung des Wetterarchivs dauert zu lange, das Wärmemodell rechnet ohne sie" in [
        e["event"] for e in logs
    ]


async def test_close_without_a_run_is_done_at_once(service) -> None:
    assert await service.close(0) is True


async def test_close_cancels_the_run_and_waits_for_the_thread(service, monkeypatch) -> None:
    release, entered = threading.Event(), threading.Event()
    real_store = service._store

    def slow_store(*args) -> None:
        entered.set()
        release.wait(5)
        real_store(*args)

    monkeypatch.setattr(service, "_store", slow_store)
    assert service.start_run() is True
    # erst warten, bis der Schreibzugriff läuft: Sah ``until`` noch die Rechnung, die gleich
    # endet, meldete ``close`` schon fertig
    await until(entered.is_set)
    assert await service.close(0.01) is False  # der Thread schreibt noch
    release.set()
    assert await service.close(5) is True
    assert service.running is False


# --- Gerät ------------------------------------------------------------------------------------


def test_without_coe_device_service_is_absent(cfg: Config) -> None:
    assert thermal_device(cfg) == "klima"
    empty = cfg.model_copy(update={"thermal": cfg.thermal.model_copy(update={"devices": {}})})
    assert thermal_device(empty) is None


# --- Komponente und Status --------------------------------------------------------------------


async def test_component_texts(service, read) -> None:
    status = service.component_status()
    assert (status.name, status.ok, status.message) == (
        "thermal_model",
        True,
        "Noch keine Daten der Klimaanlage",
    )
    read.slots = []
    await service.recompute()  # ein Lauf ohne Slots und ohne Stand bleibt bei „keine Daten“
    assert service.component_status().message == "Noch keine Daten der Klimaanlage"
    read.slots = list(HOUSE)
    await service.recompute()
    status = service.component_status()
    assert (status.ok, status.message) == (True, "Wärmemodell aktuell")
    read.error = ThermalDataError(DOWN)
    await service.recompute()
    status = service.component_status()
    assert (status.ok, status.message) == (False, f"Wärmemodell fehlgeschlagen: {DOWN}")


def test_reason_texts() -> None:
    assert reason_text(model()) == "Modell taugt."
    assert (
        reason_text(model(usable=False, reason="few_days", days=3))
        == "Noch zu wenig Tage mit Heizen: 3 von 14."
    )
    assert (
        reason_text(model(mode="kuehlen", usable=False, reason="few_days", days=0))
        == "Noch zu wenig Tage mit Kühlen: 0 von 14."
    )
    assert (
        reason_text(model(usable=False, reason="few_episodes", episodes=2))
        == "Noch zu wenig Auskühl-Phasen: 2 von 3."
    )
    assert (
        reason_text(model(usable=False, reason="poor_fit", r2=0.314))
        == "Die Grundleistung passt noch nicht (R² 0,31, nötig 0,5)."
    )
    assert (
        reason_text(model(usable=False, reason="poor_fit", r2=None))
        == "Die Grundleistung passt noch nicht (R² unbekannt, nötig 0,5)."
    )
    assert (
        reason_text(model(usable=False, reason="implausible_tau", tau_h=250.04))
        == "Die Zeitkonstante 250,0 h ist unplausibel (4 bis 200 h)."
    )
    assert (
        reason_text(model(usable=False, reason="implausible_tau", tau_h=None))
        == "Die Zeitkonstante ist unplausibel (4 bis 200 h)."
    )
    assert reason_text(model(usable=False, reason="no_data")) == "Noch keine Daten der Klimaanlage."


async def test_status_names_both_modes(service, read, clock) -> None:
    empty = service.status()
    assert empty["running"] is False
    assert empty["computed_at"] is None
    assert empty["status"] == {"ok": True, "text": "Noch keine Daten der Klimaanlage."}
    assert [m["mode"] for m in empty["modes"]] == ["heizen", "kuehlen"]
    assert empty["modes"][0] == {
        "mode": "heizen",
        "model": None,
        "usable": False,
        "reason": "no_data",
        "text": "Noch keine Daten der Klimaanlage.",
        "computed_at": None,
    }

    await service.recompute()
    body = service.status()
    heating, cooling = body["modes"]
    assert heating["model"] == asdict(fit_mode(HOUSE, "heizen"))
    assert (heating["usable"], heating["reason"], heating["text"]) == (True, "ok", "Modell taugt.")
    assert heating["computed_at"] == NOW.isoformat()
    assert (cooling["usable"], cooling["reason"]) == (False, "no_data")
    assert cooling["text"] == "Noch keine Daten der Klimaanlage."
    assert body["computed_at"] == NOW.isoformat()
    stamp = f"{NOW.astimezone(LOCAL_TZ):%d.%m. %H:%M}"
    assert body["status"] == {"ok": True, "text": f"Wärmemodell berechnet am {stamp}."}

    clock.advance(timedelta(days=1))
    read.error = ThermalDataError(DOWN)
    await service.recompute()
    assert service.status()["status"] == {
        "ok": False,
        "text": f"Wärmemodell fehlgeschlagen: {DOWN}. Es gilt der Stand vom {stamp}.",
    }


async def test_status_failure_without_any_state(service, read) -> None:
    read.error = ThermalDataError(DOWN)
    await service.recompute()
    assert service.status()["status"] == {
        "ok": False,
        "text": f"Wärmemodell fehlgeschlagen: {DOWN}",
    }


async def test_status_model_json_has_no_infinite_numbers(service, engine) -> None:
    put_row(engine, NOW, replace(model(), event_error_pct=float("inf")))
    await service.load_stored()
    assert service.status()["modes"][0]["model"]["event_error_pct"] is None
