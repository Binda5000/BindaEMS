"""LightGBM-Modell der Hauslast (Plan 5a, Entscheidungen 3, 8 und 12, Präzisierung 5).

Ein Modell für alle Vorläufe bis 72 h, Zielfunktion ``l1`` (Median wie das Profil). Feste
Parameter, ein Thread und feste Zufallsstarts machen das Training bei gleichen Daten
reproduzierbar. Gespeichert wird der Text des Boosters; die Merkmalsliste gehört zum Modell.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import lightgbm as lgb
import numpy as np

from bindaems.app.learning.features import TrainingSet

PARAMS: Final[dict[str, Any]] = {
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
ROUNDS: Final = 300
MIN_ROWS: Final = 1000
MAX_FACTOR: Final = 1.5  # Obergrenze der Vorhersage in Vielfachen des höchsten Ziels


class TrainingError(Exception):
    """Das Modell konnte nicht angelernt oder nicht gelesen werden."""


def _booster_names(features: Sequence[str]) -> list[str]:
    """Namen für LightGBM: ohne Doppelpunkt und Sonderzeichen (``ha:sensor.a`` wird
    ``ha__sensor_a``), je Spalte eindeutig. Die Zuordnung zu ``features`` bleibt über die Spalte."""
    names: list[str] = []
    for index, feature in enumerate(features):
        name = re.sub(r"\W", "_", feature.replace(":", "__"))
        names.append(f"{name}_{index}" if name in names else name)
    return names


def _read(text: str, features: Sequence[str]) -> lgb.Booster:
    try:
        booster = lgb.Booster(model_str=text)
    except Exception as exc:  # LightGBM wirft je nach Schaden verschiedene Fehler
        raise TrainingError(f"Modelltext nicht lesbar: {exc}") from exc
    if booster.num_feature() != len(features):
        raise TrainingError(
            f"Modelltext hat {booster.num_feature()} Merkmale, erwartet sind {len(features)}"
        )
    return booster


@dataclass(frozen=True)
class TrainedModel:
    text: str  # ``Booster.model_to_string()``
    features: tuple[str, ...]  # Spalten der Merkmalszeilen, in dieser Reihenfolge
    max_w: float  # Obergrenze der Vorhersage (Entscheidung 12)
    _booster: lgb.Booster = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_booster", _read(self.text, self.features))

    def predict(self, rows: Sequence[Sequence[float]]) -> list[float]:
        """Erwartete Last in W je Zeile, begrenzt auf ``[0, max_w]``."""
        if not len(rows):
            return []
        values = self._booster.predict(np.asarray(rows, dtype=float), num_threads=1)
        return [max(0.0, min(float(value), self.max_w)) for value in np.asarray(values)]

    def importance(self) -> dict[str, float]:
        """Anteil jedes Merkmals am Gewinn der Splits, Summe 1; ohne Gewinn überall 0."""
        gains = np.asarray(self._booster.feature_importance(importance_type="gain"), dtype=float)
        total = float(gains.sum())
        shares = gains / total if total > 0.0 else np.zeros_like(gains)
        return {name: float(share) for name, share in zip(self.features, shares, strict=True)}


def train(data: TrainingSet, features: Sequence[str]) -> TrainedModel:
    """Lernt das Modell auf ``data`` an; die Spalten der Zeilen entsprechen ``features``."""
    if len(data.rows) < MIN_ROWS:
        raise TrainingError(f"zu wenige Zeilen: {len(data.rows)} (mindestens {MIN_ROWS})")
    matrix = np.asarray(data.rows, dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] != len(features):
        raise ValueError(f"Spalten der Zeilen und Merkmale passen nicht zusammen: {matrix.shape}")
    if len(data.targets) != len(data.rows):
        raise ValueError("Zeilen und Ziele haben verschiedene Längen")
    dataset = lgb.Dataset(
        matrix, label=np.asarray(data.targets, dtype=float), feature_name=_booster_names(features)
    )
    try:
        booster = lgb.train(dict(PARAMS), dataset, num_boost_round=ROUNDS)
    except lgb.basic.LightGBMError as exc:
        raise TrainingError(f"Training fehlgeschlagen: {exc}") from exc
    return TrainedModel(booster.model_to_string(), tuple(features), MAX_FACTOR * max(data.targets))


def load_model(text: str, features: Sequence[str], max_w: float) -> TrainedModel:
    """Liest ein gespeichertes Modell; ``TrainingError``, wenn der Text nicht passt."""
    return TrainedModel(text, tuple(features), max_w)
