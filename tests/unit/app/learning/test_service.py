from __future__ import annotations

import asyncio
import math
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from structlog.testing import capture_logs
from tests.app_helpers import StopScheduler
from tests.plan_helpers import until
from tests.unit.app.learning.conftest import SYNTHETIC_FIRST_DAY, FakeReader, make_synthetic

from bindaems.app.forecast.archive import ForecastArchive, RecordValue
from bindaems.app.forecast.load import LoadForecastService, LoadProfile, LoadSample, build_profile
from bindaems.app.history.influx import InfluxQueryError, Series
from bindaems.app.learning import service as service_module
from bindaems.app.learning.backtest import BacktestResult, DaySums
from bindaems.app.learning.features import (
    BASE_FEATURES,
    TrainingSet,
    feature_names,
    training_set,
)
from bindaems.app.learning.ha_sensors import HaSensorReader
from bindaems.app.learning.model import TrainedModel, TrainingError, train
from bindaems.app.learning.registry import ModelRegistry
from bindaems.app.learning.service import (
    MIN_DAYS,
    MIN_SLOTS_PER_DAY,
    TRAIN_AT,
    TRAIN_TIMEOUT_S,
    LoadModelService,
)
from bindaems.app.ledger.service import LedgerSlot
from bindaems.app.settings.service import SettingsService
from bindaems.shared.config import Config
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.timeutil import (
    LOCAL_TZ,
    ManualClock,
    local_day_slots,
    slot_start,
    slots_between,
)

TODAY = SYNTHETIC_FIRST_DAY + timedelta(days=28)  # Tag nach 28 Tagen synthetischer Last
NOW = datetime.combine(TODAY, TRAIN_AT, tzinfo=LOCAL_TZ).astimezone(UTC)
SENSOR = "sensor.anwesenheit"
DAY = timedelta(days=1)


def local_at(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=LOCAL_TZ).astimezone(UTC)


# --- Ersatz für die Quellen ---------------------------------------------------------------


class FakeLoad:
    """Ersatz für ``LoadForecastService.history``."""

    def __init__(self, samples: Sequence[LoadSample]) -> None:
        self.samples = list(samples)
        self.calls: list[tuple[datetime, datetime]] = []

    async def history(self, start: datetime, end: datetime) -> list[LoadSample]:
        self.calls.append((start, end))
        return [s for s in self.samples if start <= s.slot_start < end]


class FakeWeather:
    def __init__(self, temps: Mapping[datetime, float]) -> None:
        self.temps = dict(temps)

    def temperatures(self, start: datetime, end: datetime) -> dict[datetime, float]:
        return {slot: t for slot, t in self.temps.items() if start <= slot < end}


class FakeForecast:
    def __init__(self) -> None:
        self.temps: dict[datetime, float] = {}

    def temperatures(self) -> Mapping[datetime, float]:
        return self.temps


class FakeLedger:
    def __init__(self) -> None:
        self.rows: list[LedgerSlot] = []

    def slots(self, start: datetime, end: datetime) -> list[LedgerSlot]:
        return [row for row in self.rows if start <= row.slot_start < end]

    def add(self, slot: datetime, house_w: float) -> None:
        self.rows.append(
            LedgerSlot(slot, 900.0, {"grid>house": house_w / 4.0}, {}, 0.0, 0.0, None, "stream")
        )


class FakeSensors:
    """Ersatz für ``HaSensorReader``: Werte je Entität und Slot."""

    def __init__(self, values: Mapping[str, Mapping[datetime, float]] | None = None) -> None:
        self.values = {key: dict(series) for key, series in (values or {}).items()}
        self.candidate_calls = 0
        self.queries: list[tuple[tuple[str, ...], datetime, datetime]] = []

    async def candidates(self) -> tuple[list[Any], str | None]:
        self.candidate_calls += 1
        return [], None

    async def slot_values(
        self, entities: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, dict[datetime, float]]:
        assert self.candidate_calls, "candidates() muss vor slot_values() laufen"
        self.queries.append((tuple(entities), start, end))
        result = {}
        for entity in entities:
            found = {s: v for s, v in self.values.get(entity, {}).items() if start <= s < end}
            if found:
                result[entity] = found
        return result


class FakeModel:
    """Ersatzmodell: merkt sich die Zeilen der Vorhersage und sagt 500 W voraus."""

    def __init__(self, features: Sequence[str]) -> None:
        self.features = tuple(features)
        self.max_w = 2000.0
        self.text = "Ersatzmodell"
        self.rows: list[list[float]] = []

    def importance(self) -> dict[str, float]:
        gains = {"temp_c": 0.75, f"ha:{SENSOR}": 0.25}
        return {name: gains.get(name, 0.0) for name in self.features}

    def predict(self, rows: Sequence[Sequence[float]]) -> list[float]:
        self.rows += [list(row) for row in rows]
        return [500.0] * len(rows)


@dataclass
class Trainer:
    """Ersatz für ``train``: liefert ``FakeModel``, wirft auf Wunsch oder wartet auf ein Signal."""

    error: Exception | None = None
    gate: threading.Event | None = None
    seen: list[tuple[TrainingSet, tuple[str, ...]]] = field(default_factory=list)
    models: list[FakeModel] = field(default_factory=list)

    def __call__(self, data: TrainingSet, features: Sequence[str]) -> FakeModel:
        self.seen.append((data, tuple(features)))
        if self.gate is not None:
            assert self.gate.wait(timeout=5)
        if self.error is not None:
            raise self.error
        model = FakeModel(features)
        self.models.append(model)
        return model


def better_backtest(today: date = TODAY) -> BacktestResult:
    """Backtest mit 14 Tagen, an denen das Modell 20 % genauer ist als das Profil."""
    daily = {today - timedelta(days=k): DaySums(100, 8000.0, 10000.0) for k in range(1, 15)}
    return BacktestResult([], daily)


# --- Aufbau -------------------------------------------------------------------------------


@dataclass
class Env:
    service: LoadModelService
    registry: ModelRegistry
    archive: ForecastArchive
    load: FakeLoad
    sensors: Any
    settings: SettingsService
    clock: ManualClock
    ledger: FakeLedger
    forecast: FakeForecast
    sleeps: list[float]


def set_learning(settings: SettingsService, **changes: Any) -> None:
    current = settings.current()
    learning = current.settings.learning.model_copy(update=changes)
    settings.update(
        current.settings.model_copy(update={"learning": learning}),
        base_version=current.version,
        actor="test",
        source="ui",
    )


def without(samples: Sequence[LoadSample], day: date, count: int) -> list[LoadSample]:
    """Die Stichproben ohne die ersten ``count`` Slots des lokalen Tages ``day``."""
    dropped = set(local_day_slots(day)[:count])
    return [s for s in samples if s.slot_start not in dropped]


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(NOW)


@pytest.fixture
def registry(engine: Engine, tmp_path: Path) -> ModelRegistry:
    return ModelRegistry(engine, tmp_path / "models" / "load")


@pytest.fixture
def trainer(monkeypatch: pytest.MonkeyPatch) -> Trainer:
    """Ersatz für das Training; damit die Tests schnell bleiben, nur Zeilen der letzten 2 Tage."""
    fake = Trainer()

    def short_set(inputs: Any, start: datetime, end: datetime, names: Any) -> TrainingSet:
        return training_set(inputs, max(start, end - 2 * DAY), end, names)

    monkeypatch.setattr(service_module, "train", fake)
    monkeypatch.setattr(service_module, "training_set", short_set)
    monkeypatch.setattr(service_module, "rolling_backtest", lambda *args: None)
    return fake


@pytest.fixture
def make_env(
    engine: Engine,
    registry: ModelRegistry,
    settings_service: SettingsService,
    clock: ManualClock,
) -> Callable[..., Env]:
    def build(
        days: int = 28,
        *,
        samples: Sequence[LoadSample] | None = None,
        sensors: Any = None,
        timeout_s: float = TRAIN_TIMEOUT_S,
        ready: Callable[[], Any] | None = None,
        stop_after: int = 2,
    ) -> Env:
        data = make_synthetic(days)
        load = FakeLoad(data.samples if samples is None else samples)
        archive = ForecastArchive(engine)
        ledger = FakeLedger()
        forecast = FakeForecast()
        sleeps: list[float] = []

        async def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock.advance(timedelta(seconds=seconds))
            if len(sleeps) >= stop_after:
                raise StopScheduler

        service = LoadModelService(
            load=load,
            ledger=ledger,
            weather=FakeWeather(data.inputs.temp_c),  # type: ignore[arg-type]
            forecast=forecast,
            sensors=sensors if sensors is not None else FakeSensors(),
            archive=archive,
            registry=registry,
            settings=settings_service,
            clock=clock,
            ready=ready,
            sleep=sleep,
            timeout_s=timeout_s,
        )
        return Env(
            service,
            registry,
            archive,
            load,
            service._sensors,
            settings_service,
            clock,
            ledger,
            forecast,
            sleeps,
        )

    return build


@pytest.fixture
def env(make_env: Callable[..., Env]) -> Env:
    return make_env()


@pytest.fixture
def service(env: Env) -> LoadModelService:
    return env.service


@pytest.fixture(scope="module")
def real_model() -> TrainedModel:
    data = make_synthetic(28)
    return train(training_set(data.inputs, data.start, data.end, BASE_FEATURES), BASE_FEATURES)


def profile_of(env: Env) -> LoadProfile:
    return build_profile(env.load.samples, NOW)


def next_slots(start: datetime, hours: int = 48) -> list[datetime]:
    first = start.replace(minute=start.minute // 15 * 15, second=0, microsecond=0)
    return slots_between(first, first + timedelta(hours=hours))


# --- Tests --------------------------------------------------------------------------------


def test_constants() -> None:
    assert (TRAIN_AT, MIN_DAYS, MIN_SLOTS_PER_DAY, TRAIN_TIMEOUT_S) == (time(3, 15), 28, 80, 600)


async def test_too_little_data_keeps_profile(
    make_env: Callable[..., Env], trainer: Trainer
) -> None:
    env = make_env(27)
    result = await env.service.train()
    assert result.outcome == "too_little_data"
    assert result.text == "Zu wenig Daten: 27 von 28 Tagen."
    assert result.at == NOW
    assert trainer.seen == []
    assert env.registry.latest_usable() is None
    assert not env.service.active()
    assert await env.service.predict(NOW, next_slots(NOW), profile_of(env)) is None
    training = env.service.status()["training"]
    assert training == {
        "running": False,
        "last_at": NOW.isoformat(),
        "outcome": "too_little_data",
        "text": "Zu wenig Daten: 27 von 28 Tagen.",
    }


async def test_history_reaches_back_for_the_backtest(env: Env, trainer: Trainer) -> None:
    await env.service.train()
    midnight = local_at(TODAY, "00:00")
    assert env.load.calls[0] == (local_at(TODAY - timedelta(days=56 + 7 * 8), "00:00"), midnight)


@pytest.mark.parametrize(("missing", "outcome"), [(17, "too_little_data"), (16, "trained")])
async def test_days_with_gaps_do_not_count(
    make_env: Callable[..., Env], trainer: Trainer, missing: int, outcome: str
) -> None:
    data = make_synthetic(28)
    gap_day = SYNTHETIC_FIRST_DAY + timedelta(days=10)
    env = make_env(samples=without(data.samples, gap_day, missing))  # 79 bzw. 80 Slots
    result = await env.service.train()
    assert result.outcome == outcome
    if outcome == "too_little_data":
        assert result.text == "Zu wenig Daten: 27 von 28 Tagen."


async def test_training_registers_and_evaluates(env: Env, registry: ModelRegistry) -> None:
    result = await env.service.train()  # echtes Training und echter Backtest (2 Wochen)
    assert result.outcome == "trained"
    entry = registry.latest_usable()
    assert entry is not None
    assert result.text == f"Trainiert am 02.02. 03:15 mit {entry.samples} Viertelstunden."
    assert entry.trained_at == NOW
    assert entry.data_from == local_at(SYNTHETIC_FIRST_DAY, "00:00")
    assert entry.data_to == local_at(TODAY - DAY, "23:45")
    assert entry.features == BASE_FEATURES
    assert entry.backtest is not None and entry.backtest["weeks"]
    # der Backtest der letzten 14 Tage ist die Grundlage; das Modell schlägt das Profil klar
    assert entry.decided_at == NOW
    assert entry.decision is not None
    assert (entry.decision["basis"], entry.decision["reason"]) == ("backtest", "better")
    assert entry.active and env.service.active()
    values = await env.service.predict(NOW, next_slots(NOW), profile_of(env))
    assert values is not None and len(values) == 192
    assert all(value >= 0.0 for value in values.values())


async def test_training_prunes_old_versions(
    env: Env, trainer: Trainer, registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    pruned: list[int] = []
    monkeypatch.setattr(registry, "prune", lambda keep=14: pruned.append(keep) or 0)
    await env.service.train()
    assert pruned == [14]


async def test_failed_prune_keeps_the_new_version(
    env: Env, trainer: Trainer, registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(keep: int = 14) -> int:
        raise OSError("Datenträger voll")

    monkeypatch.setattr(registry, "prune", broken)
    with capture_logs() as logs:
        result = await env.service.train()
    assert result.outcome == "trained"
    assert "Alte Versionen des Lastmodells nicht gelöscht" in [e["event"] for e in logs]


async def test_training_runs_when_a_sensor_has_no_data(
    make_env: Callable[..., Env],
    settings_service: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_module, "rolling_backtest", lambda *args: None)
    set_learning(settings_service, ha_sensors=[SENSOR])
    reader = FakeReader()  # die HA-Datenbank kennt den Sensor nicht
    env = make_env(sensors=HaSensorReader(reader, "homeassistant"))  # type: ignore[arg-type]
    with capture_logs() as logs:
        result = await env.service.train()  # echtes Training mit leerer Spalte
    assert result.outcome == "trained"
    assert [e for e in logs if e["log_level"] in ("error", "critical")] == []
    entry = env.registry.latest_usable()
    assert entry is not None and entry.features == feature_names([SENSOR])
    assert reader.texts("GROUP BY")  # Abfragen gingen hinaus
    assert env.service.status()["sensors"] == [{"entity_id": SENSOR, "in_model": False}]


async def test_sensors_are_read_in_weekly_chunks(
    make_env: Callable[..., Env], trainer: Trainer, settings_service: SettingsService
) -> None:
    set_learning(settings_service, ha_sensors=[SENSOR])
    data = make_synthetic(28)
    env = make_env(sensors=FakeSensors({SENSOR: dict.fromkeys(data.inputs.load_w, 1.0)}))
    await env.service.train()
    queries = env.sensors.queries
    assert env.sensors.candidate_calls == 1
    assert len(queries) == 16  # 112 Tage in Stücken von 7 Tagen
    # je 7 lokale Tage; über die Umstellung im Oktober eine Stunde mehr
    assert all(end - start <= timedelta(days=7, hours=1) for _, start, end in queries)
    assert queries[0][1] == env.load.calls[0][0] and queries[-1][2] == env.load.calls[0][1]
    seen_data, names = trainer.seen[0]
    column = names.index(f"ha:{SENSOR}")
    known = [row[column] for row in seen_data.rows if not math.isnan(row[column])]
    assert len(known) > len(seen_data.rows) // 2 and set(known) == {1.0}


async def test_model_keeps_its_feature_list_after_settings_change(
    make_env: Callable[..., Env], trainer: Trainer, settings_service: SettingsService
) -> None:
    set_learning(settings_service, ha_sensors=[SENSOR])
    values = {slot: 1.0 for slot in slots_between(local_at(SYNTHETIC_FIRST_DAY, "00:00"), NOW)}
    env = make_env(sensors=FakeSensors({SENSOR: values, "sensor.neu": values}))
    await env.service.train()
    model = trainer.models[0]
    names = feature_names([SENSOR])
    column = names.index(f"ha:{SENSOR}")
    slots = next_slots(NOW)

    await env.service.predict(NOW, slots, profile_of(env))
    assert len(model.rows) == len(slots)
    assert all(len(row) == len(names) and row[column] == 1.0 for row in model.rows)

    set_learning(settings_service, ha_sensors=["sensor.neu"])  # Sensor entfernt, ein neuer dazu
    model.rows.clear()
    env.sensors.queries.clear()
    predicted = await env.service.predict(NOW, slots, profile_of(env))
    assert predicted == dict.fromkeys(slots, 500.0)
    assert all(len(row) == len(names) and math.isnan(row[column]) for row in model.rows)
    assert env.sensors.queries == []  # weder der entfernte noch der neue Sensor wird gelesen


async def test_predict_uses_the_last_hour_of_sensors_and_eight_days_of_load(
    make_env: Callable[..., Env], trainer: Trainer, settings_service: SettingsService
) -> None:
    set_learning(settings_service, ha_sensors=[SENSOR])
    values = {slot: 1.0 for slot in slots_between(local_at(SYNTHETIC_FIRST_DAY, "00:00"), NOW)}
    env = make_env(sensors=FakeSensors({SENSOR: values}))
    await env.service.train()
    env.sensors.queries.clear()
    env.load.calls.clear()
    issued = NOW + timedelta(minutes=5)  # 03:20, der Slot der Ausgabe beginnt 03:15
    await env.service.predict(issued, next_slots(issued), profile_of(env))
    cutoff = local_at(TODAY, "03:15")
    assert env.sensors.queries == [((SENSOR,), cutoff - timedelta(hours=1), cutoff)]
    assert env.load.calls == [(cutoff - timedelta(days=8), cutoff)]


async def test_predict_takes_forecast_temperatures_before_the_archive(
    env: Env, trainer: Trainer
) -> None:
    await env.service.train()
    slots = next_slots(NOW, hours=1)
    env.forecast.temps = {slots[0]: 30.0}  # Prognose für den ersten Slot, sonst das Archiv
    await env.service.predict(NOW, slots, profile_of(env))
    rows = trainer.models[0].rows
    column = BASE_FEATURES.index("temp_c")
    assert rows[0][column] == 30.0
    assert all(math.isnan(row[column]) for row in rows[1:])  # die Zukunft fehlt im Archiv
    profile_column = BASE_FEATURES.index("profile_w")
    assert rows[0][profile_column] == profile_of(env).at(slots[0])


async def test_predict_without_model_is_none(service: LoadModelService) -> None:
    assert await service.predict(NOW, next_slots(NOW), build_profile([], NOW)) is None


async def test_training_failure_keeps_previous_version(
    env: Env, trainer: Trainer, registry: ModelRegistry
) -> None:
    await env.service.train()
    first = registry.latest_usable()
    assert first is not None
    env.clock.advance(DAY)
    trainer.error = TrainingError("zu wenige Zeilen: 12 (mindestens 1000)")
    result = await env.service.train()
    assert result.outcome == "failed"
    assert result.text == "Training fehlgeschlagen: zu wenige Zeilen: 12 (mindestens 1000)"
    latest = registry.latest_usable()
    assert latest is not None and latest.id == first.id
    assert latest.decided_at == NOW + DAY  # die Aktivierung wurde trotzdem neu bewertet
    assert await env.service.predict(NOW + DAY, next_slots(NOW + DAY), profile_of(env))
    status = env.service.component_status()
    assert (status.name, status.ok) == ("load-model", False)
    assert status.message == "Lastmodell fehlgeschlagen: zu wenige Zeilen: 12 (mindestens 1000)"


async def test_unexpected_error_fails_the_run(env: Env, trainer: Trainer) -> None:
    trainer.error = RuntimeError("kaputt")
    with capture_logs() as logs:
        result = await env.service.train()
    assert (result.outcome, result.text) == ("failed", "Training fehlgeschlagen: kaputt")
    assert "Training des Lastmodells fehlgeschlagen" in [e["event"] for e in logs]


async def test_too_little_data_after_a_version_keeps_it(
    env: Env, trainer: Trainer, registry: ModelRegistry
) -> None:
    await env.service.train()
    env.load.samples = env.load.samples[: 96 * 5]
    env.clock.advance(DAY)
    result = await env.service.train()
    assert result.outcome == "too_little_data"
    assert env.service.component_status().message == "Profil aktiv, Modell bereit"
    entry = registry.latest_usable()
    assert entry is not None and entry.decided_at == NOW + DAY


async def test_slow_training_times_out(make_env: Callable[..., Env], trainer: Trainer) -> None:
    trainer.gate = threading.Event()
    env = make_env(timeout_s=0.05)
    try:
        result = await env.service.train()
    finally:
        trainer.gate.set()
    assert result.outcome == "failed"
    assert result.text == "Training fehlgeschlagen: Zeitlimit von 0.05 s überschritten"
    assert env.registry.latest_usable() is None


async def test_second_start_while_running_is_refused(env: Env, trainer: Trainer) -> None:
    trainer.gate = threading.Event()
    assert env.service.start_training()
    assert not env.service.start_training()
    await until(lambda: bool(trainer.seen))
    assert env.service.status()["training"]["running"]
    assert not env.service.start_training()
    trainer.gate.set()
    await until(lambda: not env.service.status()["training"]["running"])
    assert env.service.status()["training"]["outcome"] == "trained"
    assert env.service.start_training()  # danach wieder möglich
    await until(lambda: not env.service.status()["training"]["running"])
    assert len(trainer.seen) == 2


async def test_parallel_runs_take_turns(env: Env, trainer: Trainer) -> None:
    results = await asyncio.gather(env.service.train(), env.service.train())
    assert [r.outcome for r in results] == ["trained", "trained"]
    assert len(trainer.seen) == 2


async def test_nightly_run_at_quarter_past_three(
    make_env: Callable[..., Env],
    trainer: Trainer,
    registry: ModelRegistry,
    real_model: TrainedModel,
    clock: ManualClock,
) -> None:
    registry.add(
        real_model,
        trained_at=NOW - DAY,
        data_from=NOW - 30 * DAY,
        data_to=NOW - DAY,
        samples=1234,
        backtest=None,
    )
    clock.advance(timedelta(hours=7))  # 10:15 Ortszeit: ein Modell ist da, also kein Training
    env = make_env()
    with pytest.raises(StopScheduler):
        await env.service.run()
    assert env.service.status()["model"] is not None
    assert env.sleeps[0] == timedelta(hours=17).total_seconds()  # bis 03:15 am nächsten Tag
    assert env.sleeps[1] == timedelta(hours=24).total_seconds()
    assert len(trainer.seen) == 1  # um 03:15, nicht beim Start
    last_at = env.service.status()["training"]["last_at"]
    assert last_at == local_at(TODAY + DAY, "03:15").isoformat()


async def test_run_without_model_trains_after_the_weather_backfill(
    make_env: Callable[..., Env], trainer: Trainer
) -> None:
    backfilled = asyncio.Event()
    order: list[str] = []

    async def ready() -> None:
        await backfilled.wait()
        order.append("ready")

    env = make_env(ready=ready, stop_after=1)
    task = asyncio.create_task(env.service.run())
    await asyncio.sleep(0.05)
    assert trainer.seen == []  # wartet noch auf die Nachholung
    backfilled.set()
    with pytest.raises(StopScheduler):
        await task
    assert order == ["ready"] and len(trainer.seen) == 1
    assert env.service.status()["model"] is not None


async def test_run_starts_training_when_ready_hangs(
    make_env: Callable[..., Env], trainer: Trainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "READY_WAIT_S", 0.01)
    env = make_env(ready=asyncio.Event().wait, stop_after=1)
    with capture_logs() as logs, pytest.raises(StopScheduler):
        await env.service.run()
    assert len(trainer.seen) == 1
    assert "Nachholung des Wetterarchivs dauert zu lange, das Training beginnt ohne sie" in [
        e["event"] for e in logs
    ]


async def test_run_continues_after_a_failed_training(
    make_env: Callable[..., Env], trainer: Trainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom() -> service_module.TrainingResult:
        raise RuntimeError("Datenbank weg")

    env = make_env(stop_after=2)
    monkeypatch.setattr(env.service, "train", boom)
    with capture_logs() as logs, pytest.raises(StopScheduler):
        await env.service.run()
    assert len(env.sleeps) == 2  # weiter im Tagesrhythmus
    assert "Training des Lastmodells fehlgeschlagen" in [e["event"] for e in logs]


async def test_unreadable_model_file_trains_a_new_version(
    make_env: Callable[..., Env], trainer: Trainer, registry: ModelRegistry
) -> None:
    broken = registry.add(
        FakeModel(BASE_FEATURES),  # type: ignore[arg-type]
        trained_at=NOW - DAY,
        data_from=NOW - 30 * DAY,
        data_to=NOW - DAY,
        samples=1234,
        backtest=None,
    )
    env = make_env(stop_after=1)
    with pytest.raises(StopScheduler):
        await env.service.run()
    stored = registry.get(broken.id)
    assert stored is not None and not stored.usable
    assert len(trainer.seen) == 1  # ohne brauchbare Version gleich beim Start
    latest = registry.latest_usable()
    assert latest is not None and latest.id != broken.id


async def test_loaded_version_keeps_its_decision(
    make_env: Callable[..., Env], registry: ModelRegistry, real_model: TrainedModel
) -> None:
    entry = registry.add(
        real_model,
        trained_at=NOW - DAY,
        data_from=NOW - 30 * DAY,
        data_to=NOW - DAY,
        samples=1234,
        backtest=None,
    )
    decision = {
        "active": True,
        "basis": "backtest",
        "days": 14,
        "model_mae_w": 80.0,
        "profile_mae_w": 100.0,
        "improvement_pct": 20.0,
        "reason": "better",
        "text": "Modell aktiv: in den letzten 14 Tagen 20 % genauer als das Profil (im Backtest).",
    }
    registry.decide(entry.id, decision, True, NOW - DAY)
    env = make_env(stop_after=1)
    with pytest.raises(StopScheduler):
        await env.service.run()
    assert env.service.active()
    assert env.service.status()["activation"] == {**decision, "decided_at": (NOW - DAY).isoformat()}
    assert env.service.component_status().message == "Lastmodell aktiv"


async def test_evaluate_uses_switch_from_settings(
    env: Env, trainer: Trainer, settings_service: SettingsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "rolling_backtest", lambda *args: better_backtest())
    await env.service.train()
    assert env.service.active()
    set_learning(settings_service, load_model_enabled=False)
    assert not env.service.active()  # aus wirkt sofort, auch vor der nächsten Bewertung
    shown = env.service.status()["activation"]
    assert (shown["reason"], shown["active"], shown["basis"]) == ("disabled", False, "backtest")
    assert shown["text"] == "Profil aktiv: Das Lastmodell ist in den Einstellungen ausgeschaltet."
    activation = await env.service.evaluate()
    assert (activation.reason, activation.active) == ("disabled", False)
    entry = env.registry.latest_usable()
    assert entry is not None and not entry.active
    assert entry.decision is not None and entry.decision["reason"] == "disabled"
    assert env.service.status()["enabled"] is False


async def test_evaluate_prefers_the_shadow_operation(
    env: Env, trainer: Trainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "rolling_backtest", lambda *args: better_backtest())
    await env.service.train()
    for back in range(1, 11):  # zehn Tage Schatten: das Modell ist schlechter als das Profil
        day = TODAY - timedelta(days=back)
        issued = local_at(day, "00:00")
        slots = local_day_slots(day)[4:8]  # 01:00 bis 02:00, Vorlauf 0–6 h
        env.archive.record("load_model", issued, {s: RecordValue(700.0, 700.0) for s in slots})
        env.archive.record("load_profile", issued, {s: RecordValue(550.0, 550.0) for s in slots})
        for slot in slots:
            env.ledger.add(slot, 500.0)
    activation = await env.service.evaluate()
    assert (activation.basis, activation.reason, activation.days) == ("shadow", "not_better", 10)
    assert activation.model_mae_w == pytest.approx(200.0)
    assert activation.profile_mae_w == pytest.approx(50.0)
    assert not env.service.active()


async def test_evaluate_without_model_decides_nothing(env: Env) -> None:
    activation = await env.service.evaluate()
    assert (activation.reason, activation.active) == ("no_model", False)
    assert env.service.status()["activation"]["decided_at"] == NOW.isoformat()


def test_status_before_anything(service: LoadModelService) -> None:
    status = service.status()
    assert status == {
        "enabled": True,
        "activation": {
            "active": False,
            "basis": "none",
            "days": 0,
            "model_mae_w": None,
            "profile_mae_w": None,
            "improvement_pct": None,
            "reason": "no_model",
            "text": "Profil aktiv: Es gibt noch kein brauchbares Modell.",
            "decided_at": None,
        },
        "model": None,
        "backtest": None,
        "training": {"running": False, "last_at": None, "outcome": None, "text": None},
        "sensors": [],
        "status": {"ok": True, "text": "Noch kein Modell"},
    }


async def test_status_describes_the_model(
    env: Env, trainer: Trainer, settings_service: SettingsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = BacktestResult([], better_backtest().daily)
    monkeypatch.setattr(service_module, "rolling_backtest", lambda *args: result)
    set_learning(settings_service, ha_sensors=[SENSOR])
    await env.service.train()
    set_learning(settings_service, ha_sensors=[SENSOR, "sensor.neu"])
    status = env.service.status()
    model = status["model"]
    entry = env.registry.latest_usable()
    assert entry is not None
    assert model["id"] == entry.id
    assert model["trained_at"] == NOW.isoformat()
    assert model["data_from"] == entry.data_from.isoformat()
    assert model["data_to"] == entry.data_to.isoformat()
    assert model["samples"] == entry.samples
    assert model["features"][:2] == [
        {"name": "temp_c", "label": "Temperatur", "importance": 0.75},
        {"name": f"ha:{SENSOR}", "label": SENSOR, "importance": 0.25},
    ]
    labels = {f["name"]: f["label"] for f in model["features"]}
    assert labels["lead_h"] == "Vorlauf"
    assert labels["profile_w"] == "Profil"
    assert labels[f"ha:{SENSOR}"] == SENSOR
    assert len(model["features"]) == len(BASE_FEATURES) + 1
    assert status["backtest"] == entry.backtest
    assert status["sensors"] == [
        {"entity_id": SENSOR, "in_model": True},
        {"entity_id": "sensor.neu", "in_model": False},  # zählt erst ab dem nächsten Training
    ]
    assert status["status"] == {"ok": True, "text": "Lastmodell aktiv"}


async def test_component_status_texts(env: Env, trainer: Trainer) -> None:
    status = env.service.component_status()
    assert (status.name, status.ok, status.message) == ("load-model", True, "Noch kein Modell")
    await env.service.train()  # ohne Backtest und Schatten: Profil aktiv
    status = env.service.component_status()
    assert (status.ok, status.message) == (True, "Profil aktiv, Modell bereit")
    env.service._active = True
    status = env.service.component_status()
    assert (status.ok, status.message) == (True, "Lastmodell aktiv")
    trainer.error = TrainingError("kaputt")
    await env.service.train()
    status = env.service.component_status()
    assert (status.ok, status.message) == (False, "Lastmodell fehlgeschlagen: kaputt")


async def test_predict_without_slots_is_empty(env: Env, trainer: Trainer) -> None:
    await env.service.train()
    assert await env.service.predict(NOW, [], profile_of(env)) == {}


async def test_predict_reads_the_sensor_units_once(
    make_env: Callable[..., Env], trainer: Trainer, settings_service: SettingsService
) -> None:
    set_learning(settings_service, ha_sensors=[SENSOR])
    env = make_env(sensors=FakeSensors({SENSOR: {}}))
    await env.service.train()
    env.service._units_known = False  # wie nach einem Neustart mit geladener Version
    calls = env.sensors.candidate_calls
    await env.service.predict(NOW, next_slots(NOW), profile_of(env))
    await env.service.predict(NOW, next_slots(NOW), profile_of(env))
    assert env.sensors.candidate_calls == calls + 1


class FlakyUnitsReader(FakeReader):
    """HA-Datenbank mit dem Schema je Einheit (``°C``); die erste Abfrage der Kandidaten
    scheitert, danach antwortet sie."""

    def __init__(self) -> None:
        super().__init__()
        self.candidate_calls = 0

    async def query(self, q: str, *, database: str | None = None) -> list[Series]:
        self.queries.append((q, database))
        tags = {"domain": "sensor", "entity_id": "aussentemperatur"}
        if "FROM /.*/" in q:
            self.candidate_calls += 1
            if self.candidate_calls == 1:
                raise InfluxQueryError("InfluxDB: Zeitüberschreitung")
            return [Series("°C", tags, ["time", "v", "u"], [[epoch_ms(NOW), 7.5, None]])]
        start = slot_start(NOW) - timedelta(hours=2)
        rows = [[epoch_ms(start + k * timedelta(minutes=1)), 7.5, None] for k in range(180)]
        return [Series("°C", tags, ["time", "w", "u"], rows)]


async def test_failed_unit_lookup_is_retried(
    make_env: Callable[..., Env], trainer: Trainer, settings_service: SettingsService
) -> None:
    temp = "sensor.aussentemperatur"
    set_learning(settings_service, ha_sensors=[temp])
    reader = FlakyUnitsReader()
    env = make_env(sensors=HaSensorReader(reader, "homeassistant"))  # type: ignore[arg-type]
    await env.service.train()  # die Kandidaten scheitern: die Einheiten bleiben unbekannt
    assert reader.candidate_calls == 1
    model = trainer.models[0]
    column = model.features.index(f"ha:{temp}")
    await env.service.predict(NOW, next_slots(NOW), profile_of(env))
    assert reader.candidate_calls == 2  # neuer Versuch, diesmal mit Erfolg
    assert model.rows and all(row[column] == 7.5 for row in model.rows)
    await env.service.predict(NOW, next_slots(NOW), profile_of(env))
    assert reader.candidate_calls == 2  # danach bekannt


async def test_failed_evaluation_keeps_the_training_result(
    env: Env, trainer: Trainer, registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any) -> None:
        raise OSError("Datenbank gesperrt")

    monkeypatch.setattr(registry, "decide", broken)
    with capture_logs() as logs:
        result = await env.service.train()
    assert result.outcome == "trained"
    assert "Aktivierung des Lastmodells nicht bewertet" in [e["event"] for e in logs]


async def test_failed_load_at_start_trains_right_away(
    make_env: Callable[..., Env],
    trainer: Trainer,
    registry: ModelRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> None:
        raise OSError("Datenbank gesperrt")

    env = make_env(stop_after=1)
    monkeypatch.setattr(registry, "latest_usable", broken)
    with capture_logs() as logs, pytest.raises(StopScheduler):
        await env.service.run()
    assert "Lastmodell nicht geladen" in [e["event"] for e in logs]
    assert len(trainer.seen) == 1


async def test_unreadable_stored_decision_is_ignored(
    make_env: Callable[..., Env], registry: ModelRegistry, real_model: TrainedModel
) -> None:
    entry = registry.add(
        real_model,
        trained_at=NOW - DAY,
        data_from=NOW - 30 * DAY,
        data_to=NOW - DAY,
        samples=1234,
        backtest=None,
    )
    registry.decide(entry.id, {"kaputt": True}, True, NOW - DAY)  # aktiv, aber unlesbar
    env = make_env(stop_after=1)
    with pytest.raises(StopScheduler):
        await env.service.run()
    assert not env.service.active()
    activation = env.service.status()["activation"]
    assert (activation["reason"], activation["decided_at"]) == ("too_few_days", None)


# --- Nacharbeit 1 -------------------------------------------------------------------------


async def test_min_days_count_only_the_training_window(
    make_env: Callable[..., Env], trainer: Trainer
) -> None:
    # 30 volle Tage, davon nur 20 in den 56 Tagen vor heute
    data = make_synthetic(30, first_day=TODAY - timedelta(days=66))
    env = make_env(samples=data.samples)
    result = await env.service.train()
    assert (result.outcome, result.text) == ("too_little_data", "Zu wenig Daten: 20 von 28 Tagen.")
    assert trainer.seen == []


async def test_backtest_gets_the_days_of_the_whole_window(
    make_env: Callable[..., Env], trainer: Trainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    def backtest(inputs: Any, names: Any, today: date, days: int) -> None:
        seen.append(days)

    monkeypatch.setattr(service_module, "rolling_backtest", backtest)
    data = make_synthetic(70, first_day=TODAY - timedelta(days=70))
    env = make_env(samples=data.samples)
    assert (await env.service.train()).outcome == "trained"
    assert seen == [70]  # 56 davon im Trainingsfenster


async def test_switching_the_model_back_on_evaluates_at_once(
    env: Env, trainer: Trainer, settings_service: SettingsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "rolling_backtest", lambda *args: better_backtest())
    await env.service.train()
    assert env.service.active()
    set_learning(settings_service, load_model_enabled=False)
    await until(lambda: env.service.status()["activation"]["decided_at"] is not None)
    await until(lambda: _stored_reason(env) == "disabled")
    await asyncio.to_thread(set_learning, settings_service, load_model_enabled=True)  # Route
    await until(lambda: _stored_reason(env) == "better")
    assert env.service.active()
    shown = env.service.status()["activation"]
    assert (shown["reason"], shown["active"]) == ("better", True)


def _stored_reason(env: Env) -> str | None:
    entry = env.registry.latest_usable()
    return entry.decision["reason"] if entry is not None and entry.decision else None


async def test_other_settings_changes_do_not_evaluate(
    env: Env, trainer: Trainer, settings_service: SettingsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    await env.service.train()
    calls: list[int] = []

    async def counting() -> None:
        calls.append(1)

    monkeypatch.setattr(env.service, "evaluate", counting)
    set_learning(settings_service, ha_sensors=[SENSOR])
    await asyncio.sleep(0.05)
    assert calls == []
    set_learning(settings_service, load_model_enabled=False)
    await until(lambda: calls == [1])


async def test_switch_change_before_run_does_nothing(
    service: LoadModelService, settings_service: SettingsService
) -> None:
    set_learning(settings_service, load_model_enabled=False)  # noch keine Ereignisschleife gebunden
    assert service.status()["activation"]["reason"] == "disabled"


async def test_timed_out_thread_keeps_the_service_busy(
    make_env: Callable[..., Env], trainer: Trainer
) -> None:
    trainer.gate = threading.Event()
    env = make_env(timeout_s=0.05)
    try:
        assert (await env.service.train()).outcome == "failed"
        assert env.service.running()  # der Thread rechnet noch
        assert not env.service.start_training()
        result = await env.service.train()  # nachts: kein zweites Training daneben
        assert result.outcome == "failed"
        assert result.text == "Training fehlgeschlagen: Das vorige Training rechnet noch."
        # der Thread braucht unter ``--cov`` länger als das Zeitlimit, bis er den Trainer erreicht
        await until(lambda: bool(trainer.seen))
        assert len(trainer.seen) == 1
    finally:
        trainer.gate.set()
    await until(lambda: not env.service.running())
    assert env.service.start_training()
    await until(lambda: not env.service.running())
    assert len(trainer.seen) == 2


async def test_close_cancels_the_button_run_and_waits_for_the_thread(
    env: Env, trainer: Trainer
) -> None:
    trainer.gate = threading.Event()
    assert env.service.start_training()
    await until(lambda: bool(trainer.seen))
    task = env.service._task
    assert task is not None
    assert not await env.service.close(0.05)  # der Thread rechnet noch
    assert task.cancelled()
    threading.Timer(0.1, trainer.gate.set).start()
    assert await env.service.close(5.0)
    assert not env.service.running()
    assert env.registry.latest_usable() is None  # abgebrochen: keine Version


async def test_close_without_anything_running(service: LoadModelService) -> None:
    assert await service.close(0.01)


async def test_no_button_run_while_waiting_for_the_backfill(
    make_env: Callable[..., Env], trainer: Trainer
) -> None:
    backfilled = asyncio.Event()
    env = make_env(ready=backfilled.wait, stop_after=1)
    task = asyncio.create_task(env.service.run())
    await asyncio.sleep(0.05)
    assert env.service.running()
    assert not env.service.start_training()
    backfilled.set()
    with pytest.raises(StopScheduler):
        await task
    assert len(trainer.seen) == 1


async def test_evaluate_waits_for_a_running_training(env: Env, trainer: Trainer) -> None:
    trainer.gate = threading.Event()
    assert env.service.start_training()
    await until(lambda: bool(trainer.seen))
    evaluation = asyncio.create_task(env.service.evaluate())
    await asyncio.sleep(0.05)
    assert not evaluation.done()
    trainer.gate.set()
    activation = await asyncio.wait_for(evaluation, timeout=5)
    assert activation.reason == "too_few_days"


def test_ready_wait_is_final() -> None:
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(service_module))
    annotated = {
        node.target.id: ast.unparse(node.annotation)
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert annotated.get("READY_WAIT_S") == "Final"


async def test_failed_evaluation_after_the_switch_is_logged(
    env: Env, trainer: Trainer, settings_service: SettingsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    await env.service.train()

    def broken(*args: Any) -> None:
        raise OSError("Datenbank gesperrt")

    monkeypatch.setattr(env.registry, "decide", broken)
    with capture_logs() as logs:
        set_learning(settings_service, load_model_enabled=False)
        await until(
            lambda: "Aktivierung des Lastmodells nicht bewertet" in [e["event"] for e in logs]
        )


def equal_backtest(today: date = TODAY) -> BacktestResult:
    """Backtest mit 14 Tagen, an denen Modell und Profil gleich genau sind."""
    daily = {today - timedelta(days=k): DaySums(100, 10000.0, 10000.0) for k in range(1, 15)}
    return BacktestResult([], daily)


def record_changes(service: LoadModelService) -> list[bool]:
    """Merkt sich bei jeder Meldung, ob das Modell danach aktiv ist."""
    seen: list[bool] = []
    service.on_active_change(lambda: seen.append(service.active()))
    return seen


async def test_switch_announces_the_change_at_once(
    env: Env, trainer: Trainer, settings_service: SettingsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "rolling_backtest", lambda *args: better_backtest())
    await env.service.train()
    assert env.service.active()
    seen = record_changes(env.service)
    set_learning(settings_service, load_model_enabled=False)
    assert seen == [False]  # sofort, nicht erst nach der Bewertung
    await until(lambda: _stored_reason(env) == "disabled")
    assert seen == [False]
    set_learning(settings_service, load_model_enabled=True)
    await until(lambda: _stored_reason(env) == "better")
    assert seen == [False, True]  # aktiv erst nach der Bewertung


async def test_decision_flip_announces_the_change(
    env: Env, trainer: Trainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "rolling_backtest", lambda *args: better_backtest())
    seen = record_changes(env.service)
    await env.service.train()
    assert seen == [True]
    await env.service.train()  # neue Version, wieder aktiv: keine Meldung
    await env.service.evaluate()
    assert seen == [True]
    monkeypatch.setattr(service_module, "rolling_backtest", lambda *args: equal_backtest())
    await env.service.train()
    assert seen == [True, False]


async def test_loaded_active_version_is_announced(
    make_env: Callable[..., Env], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "rolling_backtest", lambda *args: better_backtest())
    assert (await make_env().service.train()).outcome == "trained"  # echtes Modell als Datei
    env = make_env(stop_after=1)  # Neustart: lädt die aktive Version aus der Registry
    seen = record_changes(env.service)
    with pytest.raises(StopScheduler):
        await env.service.run()
    assert seen == [True]
    assert env.service.status()["training"]["outcome"] is None  # geladen, nicht neu trainiert


async def test_failing_listener_is_logged(
    env: Env, trainer: Trainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "rolling_backtest", lambda *args: better_backtest())

    def broken() -> None:
        raise RuntimeError("kaputt")

    env.service.on_active_change(broken)
    seen = record_changes(env.service)
    with capture_logs() as logs:
        result = await env.service.train()
    assert result.outcome == "trained" and seen == [True]
    assert "Meldung des Lastmodells fehlgeschlagen" in [e["event"] for e in logs]


async def test_switching_off_reissues_the_load_forecast_at_once(
    env: Env, trainer: Trainer, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "rolling_backtest", lambda *args: better_backtest())
    for slot in slots_between(NOW - 7 * DAY, slot_start(NOW)):
        env.ledger.add(slot, 400.0)  # Profil aus der Abrechnung
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.Event().wait()  # die Minute 9 kommt in diesem Test nie

    load_forecast = LoadForecastService(env.ledger, cfg, None, env.archive, env.clock, sleep=sleep)
    load_forecast.attach_model(env.service)
    env.service.on_active_change(load_forecast.request_refresh)  # wie in der Laufzeit
    await env.service.train()
    task = asyncio.create_task(load_forecast.run())
    try:
        await until(lambda: len(sleeps) == 1)
        first = load_forecast.latest()
        assert first is not None and first.method == "model"
        env.clock.advance(timedelta(minutes=1))
        set_learning(env.settings, load_model_enabled=False)
        await until(lambda: load_forecast.latest() is not first)
        second = load_forecast.latest()
        assert second is not None and second.method == "profile"
        assert set(second.p50_w.values()) == {400.0}
        await until(lambda: len(sleeps) == 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
