import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select, update

from bindaems.app.db.schema import model_registry
from bindaems.app.learning.backtest import BacktestResult, WeekResult
from bindaems.app.learning.features import BASE_FEATURES, TrainingSet
from bindaems.app.learning.model import TrainedModel, train
from bindaems.app.learning.registry import ModelRegistry, RegistryEntry, backtest_json

T0 = datetime(2026, 10, 20, 1, 15, tzinfo=UTC)
DATA_FROM = datetime(2026, 8, 25, tzinfo=UTC)
DATA_TO = datetime(2026, 10, 19, 23, tzinfo=UTC)


@pytest.fixture(scope="module")
def model(synthetic_set: TrainingSet) -> TrainedModel:
    return train(synthetic_set, BASE_FEATURES)


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    return tmp_path / "models" / "load"


@pytest.fixture
def registry(engine: Engine, directory: Path) -> ModelRegistry:
    return ModelRegistry(engine, directory)


def _week(model_mae: float = 190.0, profile_mae: float = 230.0, n: int = 1310) -> WeekResult:
    return WeekResult(
        datetime(2026, 10, 12, tzinfo=UTC),
        datetime(2026, 10, 19, tzinfo=UTC),
        "h0_6",
        n,
        model_mae,
        profile_mae,
    )


def _add(
    registry: ModelRegistry,
    model: TrainedModel,
    *,
    offset: int = 0,
    backtest: BacktestResult | None = None,
) -> RegistryEntry:
    return registry.add(
        model,
        trained_at=T0 + timedelta(days=offset),
        data_from=DATA_FROM,
        data_to=DATA_TO,
        samples=61234,
        backtest=backtest,
    )


def test_add_writes_file_and_row(
    registry: ModelRegistry, directory: Path, model: TrainedModel
) -> None:
    entry = _add(registry, model, backtest=BacktestResult([_week()], {}))
    assert entry.file == f"load-{entry.id}.txt"
    assert (directory / entry.file).read_text(encoding="utf-8") == model.text
    assert list(directory.glob("*.tmp")) == []
    assert entry.trained_at == T0
    assert (entry.data_from, entry.data_to, entry.samples) == (DATA_FROM, DATA_TO, 61234)
    assert entry.features == model.features
    assert entry.max_w == pytest.approx(model.max_w)
    assert entry.importance == pytest.approx(model.importance())
    assert entry.usable is True
    assert entry.active is False
    assert (entry.decided_at, entry.decision) == (None, None)
    assert entry.backtest is not None
    assert entry.backtest["weeks"][0]["horizon"] == "h0_6"
    assert registry.get(entry.id) == entry


def test_get_unknown_is_none(registry: ModelRegistry) -> None:
    assert registry.get(99) is None
    assert registry.latest_usable() is None


def test_backtest_json_follows_the_status_shape() -> None:
    result = BacktestResult([_week(190.0, 230.0, 100), _week(200.0, 200.0, 300)], daily={})
    data = backtest_json(result)
    json.dumps(data)  # lässt sich als JSON schreiben
    first = data["weeks"][0]
    assert first["origin"] == "2026-10-12T00:00:00+00:00"
    assert first["end"] == "2026-10-19T00:00:00+00:00"
    assert (first["horizon"], first["n"]) == ("h0_6", 100)
    assert first["improvement_pct"] == pytest.approx((1 - 190 / 230) * 100)
    total = data["total"]
    assert total["model_mae_w"] == pytest.approx(197.5)
    assert total["profile_mae_w"] == pytest.approx(207.5)
    assert total["improvement_pct"] == pytest.approx((1 - 197.5 / 207.5) * 100)
    assert "daily" not in data


def test_backtest_json_without_profile_error_has_no_improvement() -> None:
    data = backtest_json(BacktestResult([_week(10.0, 0.0)], daily={}))
    assert data["weeks"][0]["improvement_pct"] is None
    assert data["total"]["improvement_pct"] is None


def test_backtest_json_of_no_weeks_is_empty() -> None:
    data = backtest_json(BacktestResult([], daily={}))
    assert data["weeks"] == []
    assert data["total"]["improvement_pct"] is None


def test_load_round_trip(
    registry: ModelRegistry, model: TrainedModel, synthetic_set: TrainingSet
) -> None:
    entry = _add(registry, model)
    loaded = registry.load(entry)
    assert loaded is not None
    rows = synthetic_set.rows[:50]
    assert loaded.predict(rows) == pytest.approx(model.predict(rows))
    assert loaded.features == model.features
    assert registry.get(entry.id) == entry  # bleibt brauchbar


def test_missing_file_marks_version_unusable(
    registry: ModelRegistry, directory: Path, model: TrainedModel
) -> None:
    entry = _add(registry, model)
    (directory / entry.file).unlink()
    assert registry.load(entry) is None
    stored = registry.get(entry.id)
    assert stored is not None
    assert stored.usable is False


def test_unparsable_file_marks_version_unusable(
    registry: ModelRegistry, directory: Path, model: TrainedModel
) -> None:
    entry = _add(registry, model)
    (directory / entry.file).write_text("kein Modell", encoding="utf-8")
    assert registry.load(entry) is None
    stored = registry.get(entry.id)
    assert stored is not None
    assert stored.usable is False


def test_binary_file_marks_version_unusable(
    registry: ModelRegistry, directory: Path, model: TrainedModel
) -> None:
    entry = _add(registry, model)
    (directory / entry.file).write_bytes(b"\xff\xfe\x00")
    assert registry.load(entry) is None


def test_latest_usable_skips_unusable(registry: ModelRegistry, model: TrainedModel) -> None:
    old = _add(registry, model, offset=0)
    new = _add(registry, model, offset=1)
    assert registry.latest_usable() == new
    (registry._directory / new.file).unlink()
    assert registry.load(new) is None
    assert registry.latest_usable() == old


def test_latest_usable_breaks_ties_by_id(registry: ModelRegistry, model: TrainedModel) -> None:
    _add(registry, model)
    second = _add(registry, model)
    assert registry.latest_usable() == second


def test_only_one_version_is_active(registry: ModelRegistry, model: TrainedModel) -> None:
    first = _add(registry, model, offset=0)
    second = _add(registry, model, offset=1)
    registry.decide(first.id, {"active": True, "reason": "better"}, True, T0)
    registry.decide(second.id, {"active": True, "reason": "better"}, True, T0 + timedelta(days=1))
    a, b = registry.get(first.id), registry.get(second.id)
    assert a is not None
    assert b is not None
    assert (a.active, b.active) == (False, True)
    assert b.decision == {"active": True, "reason": "better"}
    assert b.decided_at == T0 + timedelta(days=1)
    assert a.decision == {"active": True, "reason": "better"}  # die alte Entscheidung bleibt stehen


def test_inactive_decision_clears_every_active_flag(
    registry: ModelRegistry, model: TrainedModel
) -> None:
    first = _add(registry, model, offset=0)
    second = _add(registry, model, offset=1)
    registry.decide(first.id, {"active": True}, True, T0)
    registry.decide(second.id, {"active": False, "reason": "not_better"}, False, T0)
    a, b = registry.get(first.id), registry.get(second.id)
    assert a is not None
    assert b is not None
    assert (a.active, b.active) == (False, False)
    assert b.decision == {"active": False, "reason": "not_better"}


def test_decide_unknown_version_raises(registry: ModelRegistry, model: TrainedModel) -> None:
    entry = _add(registry, model)
    registry.decide(entry.id, {"active": True}, True, T0)
    with pytest.raises(LookupError):
        registry.decide(entry.id + 5, {"active": True}, True, T0)
    kept = registry.get(entry.id)
    assert kept is not None
    assert kept.active is True  # die abgewiesene Entscheidung ändert nichts


def test_prune_keeps_fourteen_and_the_active_one(
    registry: ModelRegistry, directory: Path, model: TrainedModel
) -> None:
    entries = [_add(registry, model, offset=day) for day in range(20)]
    registry.decide(entries[0].id, {"active": True}, True, T0)  # die älteste ist aktiv
    (directory / entries[1].file).unlink()  # eine fehlende Datei stört nicht
    assert registry.prune() == 5
    assert registry.get(entries[0].id) is not None
    assert [registry.get(e.id) is None for e in entries[1:6]] == [True] * 5
    assert all(registry.get(e.id) is not None for e in entries[6:])
    assert sorted(path.name for path in directory.glob("*.txt")) == sorted(
        e.file for e in [entries[0], *entries[6:]]
    )


def test_prune_with_few_versions_deletes_nothing(
    registry: ModelRegistry, model: TrainedModel
) -> None:
    _add(registry, model)
    assert registry.prune() == 0


def test_prune_breaks_ties_by_id(
    registry: ModelRegistry, directory: Path, model: TrainedModel
) -> None:
    entries = [_add(registry, model) for _ in range(4)]  # alle zur selben Zeit
    assert registry.prune(keep=2) == 2
    assert registry.get(entries[0].id) is None
    assert registry.get(entries[1].id) is None
    assert registry.get(entries[3].id) is not None


def test_failed_file_write_leaves_no_row(
    engine: Engine, registry: ModelRegistry, directory: Path, model: TrainedModel
) -> None:
    directory.mkdir(parents=True)
    # Ein Verzeichnis an der Stelle der Zieldatei lässt das Umbenennen scheitern
    (directory / "load-1.txt").mkdir()
    with pytest.raises(OSError):
        _add(registry, model)
    with engine.connect() as conn:
        assert conn.execute(select(model_registry.c.id)).all() == []
    assert list(directory.glob("*.tmp")) == []


def test_other_kinds_are_ignored(
    registry: ModelRegistry, engine: Engine, model: TrainedModel
) -> None:
    entry = _add(registry, model)
    with engine.begin() as conn:
        conn.execute(update(model_registry).values(kind="pv"))
    assert registry.get(entry.id) is None
    assert registry.latest_usable() is None
    assert registry.prune(keep=0) == 0
