import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import pytest

from bindaems.app.daytype import day_type
from bindaems.app.forecast.load import LoadSample
from bindaems.app.history.influx import InfluxQueryError, Series
from bindaems.app.learning.features import (
    BASE_FEATURES,
    FeatureInputs,
    TrainingSet,
    profile_cache,
    training_set,
)
from bindaems.shared.timeutil import LOCAL_TZ, slots_between

SYNTHETIC_FIRST_DAY = date(2026, 1, 5)  # ein Montag, mitten im Winter
HEATING_W_PER_K = 40.0
HEATING_BELOW_C = 15.0


class FakeReader:
    """Ersatz für ``InfluxReader``: antwortet je nach Textteil der Abfrage und merkt sich sie."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, str | None]] = []
        self._answers: list[tuple[str, list[Series] | Exception]] = []

    def answer(self, fragment: str, result: list[Series] | Exception) -> None:
        self._answers.append((fragment, result))

    def texts(self, fragment: str) -> list[str]:
        return [q for q, _ in self.queries if fragment in q]

    async def query(self, q: str, *, database: str | None = None) -> list[Series]:
        self.queries.append((q, database))
        for fragment, result in self._answers:
            if fragment in q:
                if isinstance(result, Exception):
                    raise result
                return result
        return []


@pytest.fixture
def fake_reader() -> FakeReader:
    return FakeReader()


@pytest.fixture
def unreadable() -> Callable[[], InfluxQueryError]:
    return lambda: InfluxQueryError('InfluxDB: HTTP 403 {"error":"error authorizing query"}')


@dataclass(frozen=True)
class Synthetic:
    """Künstliche Hauslast über ``days`` volle lokale Tage ab ``SYNTHETIC_FIRST_DAY``."""

    inputs: FeatureInputs
    samples: list[LoadSample]  # dieselbe Last als Stichproben, z. B. für ``build_profile``
    start: datetime  # lokale Mitternacht des ersten Tages (UTC)
    end: datetime  # lokale Mitternacht nach dem letzten Tag (UTC)


def _shape(slot: datetime) -> float:
    """Tagesform des Profils: Grundlast, Morgen- und Abendspitze, am Wochenende höher."""
    local = slot.astimezone(LOCAL_TZ)
    hour = local.hour + local.minute / 60
    base = 250.0 + (120.0 if day_type(local.date()) != "workday" else 0.0)
    morning = 200.0 * math.exp(-(((hour - 7.0) / 1.5) ** 2))
    evening = 450.0 * math.exp(-(((hour - 19.0) / 2.0) ** 2))
    return base + morning + evening


def make_synthetic(
    days: int = 28,
    *,
    temperature_effect: bool = True,
    noise_w: float = 15.0,
    seed: int = 20261020,
) -> Synthetic:
    """Hauslast und Temperatur für ``days`` Tage; der feste Zufallsstart macht jeden Aufruf gleich.

    Die Last ist die Tagesform (``_shape``) plus Rauschen; mit ``temperature_effect`` kommen
    ``HEATING_W_PER_K`` Watt je Kelvin unter ``HEATING_BELOW_C`` hinzu. Der Tagesmittelwert der
    Temperatur springt von Tag zu Tag zwischen −8 und 18 °C, damit die Temperatur sich nicht aus
    der Last des Vortags ablesen lässt."""
    rng = random.Random(seed)  # noqa: S311 - Testdaten, keine Kryptographie
    start = datetime.combine(SYNTHETIC_FIRST_DAY, time(0), tzinfo=LOCAL_TZ).astimezone(UTC)
    end = datetime.combine(
        SYNTHETIC_FIRST_DAY + timedelta(days=days), time(0), tzinfo=LOCAL_TZ
    ).astimezone(UTC)
    day_means = [rng.uniform(-8.0, 18.0) for _ in range(days)]
    temp_c: dict[datetime, float] = {}
    load_w: dict[datetime, float] = {}
    for slot in slots_between(start, end):
        local = slot.astimezone(LOCAL_TZ)
        day = (local.date() - SYNTHETIC_FIRST_DAY).days
        hour = local.hour + local.minute / 60
        temp = day_means[day] + 3.0 * math.sin((hour - 9.0) / 24 * 2 * math.pi)
        temp_c[slot] = temp
        heating = HEATING_W_PER_K * max(0.0, HEATING_BELOW_C - temp) if temperature_effect else 0.0
        load_w[slot] = max(0.0, _shape(slot) + heating + rng.gauss(0.0, noise_w))
    samples = [LoadSample(slot, value, "ledger") for slot, value in load_w.items()]
    inputs = FeatureInputs(
        load_w=load_w, temp_c=temp_c, sensors={}, profile_for=profile_cache(samples)
    )
    return Synthetic(inputs, samples, start, end)


@pytest.fixture(scope="session")
def synthetic_factory() -> Callable[..., Synthetic]:
    """``make_synthetic`` als Fixture für Tests in anderen Dateien (Backtest, Training)."""
    return make_synthetic


@pytest.fixture(scope="session")
def synthetic() -> Synthetic:
    """28 Tage Last, die von der Temperatur abhängt (Standard der Modelltests)."""
    return make_synthetic(28)


@pytest.fixture(scope="session")
def synthetic_set(synthetic: Synthetic) -> TrainingSet:
    """Trainingsmenge mit allen Basismerkmalen über die 28 Tage von ``synthetic``."""
    return training_set(synthetic.inputs, synthetic.start, synthetic.end, BASE_FEATURES)
