from __future__ import annotations

import dataclasses
import math
from collections.abc import Sequence
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from bindaems.app.learning.features import BASE_FEATURES, TrainingSet
from bindaems.app.learning.model import (
    MIN_ROWS,
    PARAMS,
    ROUNDS,
    TrainedModel,
    TrainingError,
    load_model,
    train,
)

if TYPE_CHECKING:
    from tests.unit.app.learning.conftest import Synthetic

SMALL = 3000  # genug Zeilen für ein Modell, aber schnell


def head(data: TrainingSet, count: int) -> TrainingSet:
    return TrainingSet(
        data.rows[:count], data.targets[:count], data.issued[:count], data.slots[:count]
    )


def mae(model: TrainedModel, rows: Sequence[Sequence[float]], targets: Sequence[float]) -> float:
    predicted = model.predict(rows)
    return sum(abs(p - t) for p, t in zip(predicted, targets, strict=True)) / len(targets)


def columns(rows: Sequence[Sequence[float]], keep: Sequence[int]) -> list[list[float]]:
    return [[row[index] for index in keep] for row in rows]


def test_parameters_are_fixed() -> None:
    assert PARAMS == {
        "objective": "l1",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.9,
        "num_threads": 1,
        "deterministic": True,
        "force_row_wise": True,
        "seed": 0,
        "verbosity": -1,
    }
    assert (ROUNDS, MIN_ROWS) == (300, 1000)


def test_model_learns_temperature_effect(synthetic: Synthetic, synthetic_set: TrainingSet) -> None:
    # Last = Profil + 40 W/K unter 15 °C. Angelernt wird auf den ersten 21 Tagen, geprüft auf den
    # Ausgaben der letzten sieben.
    cut = synthetic.start + timedelta(days=21)
    first = [i for i, issued in enumerate(synthetic_set.issued) if issued < cut]
    last = [i for i, issued in enumerate(synthetic_set.issued) if issued >= cut]
    assert len(first) > 5000 and len(last) > 1500
    plain = [index for index, name in enumerate(BASE_FEATURES) if not name.startswith("temp")]
    rows_in = [synthetic_set.rows[i] for i in first]
    targets_in = [synthetic_set.targets[i] for i in first]
    rows_out = [synthetic_set.rows[i] for i in last]
    targets_out = [synthetic_set.targets[i] for i in last]

    with_temp = train(TrainingSet(rows_in, targets_in, [], []), BASE_FEATURES)
    without = train(
        TrainingSet(columns(rows_in, plain), targets_in, [], []), [BASE_FEATURES[i] for i in plain]
    )
    error_with = mae(with_temp, rows_out, targets_out)
    error_without = mae(without, columns(rows_out, plain), targets_out)
    assert error_with < 0.5 * error_without
    shares = with_temp.importance()
    assert shares["temp_c"] + shares["temp_day_c"] > 0.2


def test_training_is_deterministic(synthetic_set: TrainingSet) -> None:
    data = head(synthetic_set, SMALL)
    one = train(data, BASE_FEATURES)
    two = train(data, BASE_FEATURES)
    assert one.predict(data.rows) == pytest.approx(two.predict(data.rows), rel=1e-12)


def test_predictions_are_clamped(synthetic_set: TrainingSet) -> None:
    data = head(synthetic_set, SMALL)
    model = train(data, BASE_FEATURES)
    assert model.max_w == pytest.approx(1.5 * max(data.targets))
    extreme = [[1e12] * len(BASE_FEATURES), [-1e12] * len(BASE_FEATURES)]
    unknown = [[math.nan] * len(BASE_FEATURES)]
    predicted = model.predict([*data.rows, *extreme, *unknown])
    assert all(0.0 <= value <= model.max_w for value in predicted)
    # Obergrenze: mit niedrigem max_w stößt die Vorhersage an die Grenze
    capped = dataclasses.replace(model, max_w=100.0)
    capped_values = capped.predict(data.rows)
    assert max(capped_values) == 100.0
    assert min(capped_values) >= 0.0
    # Untergrenze: Ziele unter null ergeben Vorhersagen unter null, die auf 0 gehen
    shifted = TrainingSet(data.rows, [t - 400.0 for t in data.targets], data.issued, data.slots)
    assert min(shifted.targets) < 0.0 < max(shifted.targets)
    values = train(shifted, BASE_FEATURES).predict(data.rows)
    assert min(values) == 0.0
    assert max(values) > 0.0


def test_too_few_rows_raise(synthetic_set: TrainingSet) -> None:
    with pytest.raises(TrainingError, match=r"zu wenige Zeilen: 999"):
        train(head(synthetic_set, MIN_ROWS - 1), BASE_FEATURES)
    train(head(synthetic_set, MIN_ROWS), BASE_FEATURES)  # genau die Grenze genügt


def test_mismatched_columns_are_rejected(synthetic_set: TrainingSet) -> None:
    with pytest.raises(ValueError, match="Spalten"):
        train(head(synthetic_set, SMALL), BASE_FEATURES[:-1])


def test_text_round_trip_predicts_the_same(synthetic_set: TrainingSet) -> None:
    data = head(synthetic_set, SMALL)
    model = train(data, BASE_FEATURES)
    loaded = load_model(model.text, list(model.features), model.max_w)
    assert loaded.features == model.features == BASE_FEATURES
    assert loaded.max_w == model.max_w
    assert loaded.predict(data.rows) == pytest.approx(model.predict(data.rows), rel=1e-12)
    assert loaded.importance() == pytest.approx(model.importance())


def test_importance_sums_to_one(synthetic_set: TrainingSet) -> None:
    model = train(head(synthetic_set, SMALL), BASE_FEATURES)
    shares = model.importance()
    assert list(shares) == list(BASE_FEATURES)
    assert sum(shares.values()) == pytest.approx(1.0)
    assert all(value >= 0.0 for value in shares.values())


def test_importance_without_gain_is_zero(synthetic_set: TrainingSet) -> None:
    flat = TrainingSet(
        synthetic_set.rows[:SMALL], [300.0] * SMALL, synthetic_set.issued, synthetic_set.slots
    )
    shares = train(flat, BASE_FEATURES).importance()
    assert shares == dict.fromkeys(BASE_FEATURES, 0.0)


def test_sensor_names_with_special_characters(synthetic_set: TrainingSet) -> None:
    # LightGBM kennt keinen Doppelpunkt in Merkmalsnamen; zwei Namen dürfen nicht verschmelzen
    names = (*BASE_FEATURES, "ha:sensor.a", "ha:sensor_a")
    rows = [[*row, float(i % 7), float(i % 5)] for i, row in enumerate(synthetic_set.rows[:SMALL])]
    model = train(TrainingSet(rows, synthetic_set.targets[:SMALL], [], []), names)
    assert model.features == names
    assert set(model.importance()) == set(names)
    assert len(model.predict(rows[:5])) == 5


def test_predict_empty_is_empty(synthetic_set: TrainingSet) -> None:
    assert train(head(synthetic_set, SMALL), BASE_FEATURES).predict([]) == []


@pytest.mark.parametrize("text", ["", "kein Modell", "tree\nversion=v4\nBroken"])
def test_broken_text_raises_training_error(text: str) -> None:
    with pytest.raises(TrainingError):
        load_model(text, BASE_FEATURES, 1000.0)


def test_text_for_other_features_raises_training_error(synthetic_set: TrainingSet) -> None:
    model = train(head(synthetic_set, SMALL), BASE_FEATURES)
    with pytest.raises(TrainingError, match="Merkmale"):
        load_model(model.text, BASE_FEATURES[:-1], model.max_w)
