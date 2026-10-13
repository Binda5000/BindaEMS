"""Hilfen der Simulator-Tests: das Anlagenmodell an der Beispielkonfiguration, Uhr und Speicher."""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx
import pytest

from bindaems.core.adapters.tessie import TESSIE_BASE_URL
from bindaems.core.state.store import StateStore
from bindaems.shared.config import Config
from bindaems.shared.timeutil import ManualClock
from bindaems.tools.sim.model import PlantModel

T0: Final = datetime(2026, 10, 12, 16, 0, tzinfo=UTC)  # 18:00 Wiener Zeit
VIN: Final = "5YJ3E7EB0MF000000"  # die VIN der Beispielkonfiguration
WAIT: Final = {"wait_for_completion": "true"}  # wie der Tesla-Aktor


def at(seconds: float) -> datetime:
    """Der Zeitpunkt ``seconds`` Sekunden nach ``T0``."""
    return T0 + timedelta(seconds=seconds)


def command_url(name: str) -> str:
    """Die URL eines Tessie-Befehls an den Tesla der Beispielkonfiguration."""
    return f"{TESSIE_BASE_URL}/{VIN}/command/{name}"


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(T0)


@pytest.fixture
def store(clock: ManualClock) -> StateStore:
    return StateStore(clock)


@pytest.fixture
def model(cfg: Config, store: StateStore) -> PlantModel:
    """Beispielkonfiguration, Saat 1, SOC 60 %, Multi „Ein“, PV 0, keine Autos angesteckt."""
    plant = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    plant.soc_pct = 60.0
    plant.charger_only = False
    plant.pv_w = 0.0
    plant.attach(store)
    return plant


def run(model: PlantModel, store: StateStore, clock: ManualClock, seconds: int) -> None:
    """``seconds`` simulierte Sekunden: je Sekunde ``step`` und ``publish``, dann die Uhr weiter."""
    for _ in range(seconds):
        now = clock.now()
        model.step(now)
        model.publish(store, now)
        clock.advance(1)


async def settle() -> None:
    """Lässt die Aufgaben im Event-Loop laufen, bis sie warten (wie der Lauf des Simulators)."""
    for _ in range(10):
        await asyncio.sleep(0)


async def tick(model: PlantModel, store: StateStore, clock: ManualClock, seconds: int) -> None:
    """Wie ``run``, dazu je Sekunde die Aufgaben und ``resolve`` für fällige Tessie-Antworten."""
    for _ in range(seconds):
        now = clock.now()
        model.step(now)
        model.publish(store, now)
        await settle()
        model.resolve(now)
        await settle()
        clock.advance(1)


async def at_once(request: Coroutine[Any, Any, httpx.Response]) -> httpx.Response:
    """Eine Anfrage, die das Modell sofort beantwortet, ohne auf ``resolve`` zu warten."""
    answer = asyncio.create_task(request)
    await settle()
    assert answer.done(), "Tessie wartet auf resolve"
    return answer.result()


@asynccontextmanager
async def tessie_client(model: PlantModel) -> AsyncIterator[httpx.AsyncClient]:
    """Ein HTTP-Client, dessen Anfragen an Tessie das Modell beantwortet."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(model.tessie)) as client:
        yield client
