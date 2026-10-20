from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest

from bindaems.app.learning import backtest
from bindaems.app.learning.backtest import (
    MAX_WEEKS,
    BacktestResult,
    rolling_backtest,
    week_count,
)
from bindaems.app.learning.features import BASE_FEATURES, TrainingSet
from bindaems.app.learning.model import TrainedModel, TrainingError
from bindaems.shared.timeutil import LOCAL_TZ

if TYPE_CHECKING:
    from tests.unit.app.learning.conftest import Synthetic

DAYS = 28  # zwei Testwochen, die 14 Tage davor trainieren
HORIZONS = {"h0_6", "h6_24", "h24"}


def _today(data: Synthetic) -> date:
    return data.end.astimezone(LOCAL_TZ).date()


def _run(data: Synthetic) -> BacktestResult:
    result = rolling_backtest(data.inputs, BASE_FEATURES, _today(data), DAYS)
    assert result is not None
    return result


@pytest.fixture(scope="module")
def temperature_run(
    synthetic_factory: Callable[..., Synthetic],
) -> tuple[Synthetic, BacktestResult]:
    data = synthetic_factory(DAYS)
    return data, _run(data)


@pytest.fixture(scope="module")
def profile_like_run(
    synthetic_factory: Callable[..., Synthetic],
) -> BacktestResult:
    # Mit nur 14 Tagen Profil schlägt das Modell das Profil schon durch Glättung des Rauschens;
    # bei 40 W Rauschen (statt 15 W) bleibt dieser Vorteil deutlich unter der Schwelle.
    return _run(synthetic_factory(DAYS, temperature_effect=False, noise_w=40.0))


class _Zero:
    """Ersatzmodell, das nichts lernt."""

    def predict(self, rows: Sequence[Sequence[float]]) -> list[float]:
        return [0.0] * len(rows)


def test_weeks_depend_on_available_days() -> None:
    assert week_count(42) == 4
    assert week_count(100) == MAX_WEEKS == 8
    assert week_count(27) is None
    assert week_count(28) == 2


def test_too_few_days_give_no_backtest(synthetic: Synthetic) -> None:
    assert rolling_backtest(synthetic.inputs, BASE_FEATURES, _today(synthetic), 27) is None


def test_fold_trains_only_before_origin(
    monkeypatch: pytest.MonkeyPatch, synthetic: Synthetic
) -> None:
    seen: list[TrainingSet] = []

    def fake_train(data: TrainingSet, features: Sequence[str]) -> TrainedModel:
        seen.append(data)
        return _Zero()  # type: ignore[return-value]

    monkeypatch.setattr(backtest, "train", fake_train)
    result = _run(synthetic)
    origins = sorted({week.origin for week in result.weeks})
    assert len(seen) == len(origins) == 2
    assert origins[1] - origins[0] == timedelta(days=7)
    for data, origin in zip(seen, origins, strict=True):
        assert data.slots
        assert max(data.slots) < origin
        assert max(data.issued) < origin
        assert min(data.slots) >= origin - timedelta(days=56)


def test_week_with_failed_training_is_skipped(
    monkeypatch: pytest.MonkeyPatch, synthetic: Synthetic
) -> None:
    calls = 0

    def flaky_train(data: TrainingSet, features: Sequence[str]) -> TrainedModel:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TrainingError("zu wenige Zeilen")
        return _Zero()  # type: ignore[return-value]

    monkeypatch.setattr(backtest, "train", flaky_train)
    result = _run(synthetic)
    assert len({week.origin for week in result.weeks}) == 1


def test_no_week_left_gives_none(monkeypatch: pytest.MonkeyPatch, synthetic: Synthetic) -> None:
    def failing_train(data: TrainingSet, features: Sequence[str]) -> TrainedModel:
        raise TrainingError("zu wenige Zeilen")

    monkeypatch.setattr(backtest, "train", failing_train)
    assert rolling_backtest(synthetic.inputs, BASE_FEATURES, _today(synthetic), DAYS) is None


def test_week_results_per_horizon_class(temperature_run: tuple[Synthetic, BacktestResult]) -> None:
    _, result = temperature_run
    assert {week.horizon for week in result.weeks} == HORIZONS
    assert len(result.weeks) == 2 * len(HORIZONS)
    for week in result.weeks:
        assert week.end - week.origin == timedelta(days=7)
        assert week.n > 0
        assert week.model_mae_w >= 0.0
        assert week.profile_mae_w > 0.0


def test_temperature_driven_load_beats_profile(
    temperature_run: tuple[Synthetic, BacktestResult],
) -> None:
    improvement = temperature_run[1].improvement()
    assert improvement is not None
    assert improvement >= 0.10


def test_profile_like_load_does_not_beat_profile(profile_like_run: BacktestResult) -> None:
    improvement = profile_like_run.improvement()
    assert improvement is not None
    assert improvement < 0.10


def test_daily_sums_cover_the_last_fourteen_days(
    temperature_run: tuple[Synthetic, BacktestResult],
) -> None:
    data, result = temperature_run
    today = _today(data)
    assert set(result.daily) == {today - timedelta(days=k) for k in range(1, 15)}
    assert sum(day.n for day in result.daily.values()) == sum(week.n for week in result.weeks)
    model_total = sum(day.model_abs_w for day in result.daily.values())
    weekly_total = sum(week.n * week.model_mae_w for week in result.weeks)
    assert model_total == pytest.approx(weekly_total)


def test_improvement_without_profile_error_is_none() -> None:
    assert BacktestResult([], {}).improvement() is None


def test_weeks_end_at_local_midnight_across_spring_dst(
    monkeypatch: pytest.MonkeyPatch, synthetic_factory: Callable[..., Synthetic]
) -> None:
    # Die Woche ab dem 24.03.2027 enthält die Umstellung am 28.03. (nur 167 h); die Ausgabe um
    # 00:00 Uhr des 31.03. gehört allein zur Folgewoche.
    monkeypatch.setattr(backtest, "train", lambda data, features: _Zero())
    data = synthetic_factory(DAYS, first_day=date(2027, 3, 10))
    result = _run(data)
    for week in result.weeks:
        local_end = week.end.astimezone(LOCAL_TZ)
        assert (local_end.hour, local_end.minute) == (0, 0)
        assert local_end.date() - week.origin.astimezone(LOCAL_TZ).date() == timedelta(days=7)
    assert len({week.end for week in result.weeks}) == 2
    assert sum(day.n for day in result.daily.values()) == sum(week.n for week in result.weeks)
    assert set(result.daily) == {_today(data) - timedelta(days=k) for k in range(1, 15)}
