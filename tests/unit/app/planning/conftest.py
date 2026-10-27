"""Fixtures für den Planungsdienst mit echten Diensten der app (Plan 3b, Task 9).

Die Uhr steht auf 2026-10-16T10:01Z (12:01 Ortszeit); Preise gibt es für 24 h ab dem aktuellen
Slot, PV- und Lastprognose sind Fakes mit ``latest()``, der core meldet SOC 50 %. Gelöst wird im
selben Prozess.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import Engine
from tests.app_helpers import price_row
from tests.helpers import FakeSink
from tests.plan_helpers import live_signals

from bindaems.app.audit import AuditLog
from bindaems.app.core_link import LiveState
from bindaems.app.forecast.load import LoadForecast
from bindaems.app.forecast.service import PvForecast
from bindaems.app.ledger.service import LedgerService
from bindaems.app.planning.process import solve_inline
from bindaems.app.planning.service import PlanningService
from bindaems.app.planning.store import PlanStore
from bindaems.app.prices.service import PriceService
from bindaems.app.prices.store import PriceStore
from bindaems.app.settings.service import SettingsService
from bindaems.shared.config import Config
from bindaems.shared.timeutil import SLOT, ManualClock, slot_start

PLAN_NOW = datetime.fromisoformat("2026-10-16T10:01:00+00:00")  # 12:01 Ortszeit
PRICE_SLOTS = 96  # 24 h
FORECAST_SLOTS = 192  # 48 h


def _price_net_ct(slot: datetime) -> float:
    """Erfundene Börsenpreise: abends (18–21 Uhr Ortszeit) teuer, sonst billig."""
    return 30.0 if 16 <= slot.hour < 19 else 10.0


def _pv_w(slot: datetime) -> float:
    return 2000.0 if 6 <= slot.hour < 14 else 0.0


class FakeForecast:
    """Ersatz für PV- und Lastprognose: ``latest()`` und die Lufttemperatur je Slot
    (``temperatures()``, anfangs leer)."""

    def __init__(self, forecast: Any) -> None:
        self.forecast = forecast
        self.temps: dict[datetime, float] = {}

    def latest(self) -> Any:
        return self.forecast

    def temperatures(self) -> dict[datetime, float]:
        return self.temps


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(PLAN_NOW)


@pytest.fixture
def price_store(engine: Engine, clock: ManualClock) -> PriceStore:
    store = PriceStore(engine, clock)
    first = slot_start(PLAN_NOW)
    slots = [first + i * SLOT for i in range(PRICE_SLOTS)]
    store.save([price_row(slot, _price_net_ct(slot), "primary") for slot in slots])
    return store


@pytest.fixture
def prices(price_store: PriceStore, settings_service: SettingsService) -> PriceService:
    return PriceService(price_store, settings_service)


@pytest.fixture
def forecast() -> FakeForecast:
    first = slot_start(PLAN_NOW)
    p50 = {first + i * SLOT: _pv_w(first + i * SLOT) for i in range(FORECAST_SLOTS)}
    return FakeForecast(
        PvForecast(
            issued_at=PLAN_NOW,
            source="test",
            p50_w=p50,
            raw_w=p50,
            p25_w=p50,
            p75_w=p50,
            clearness=dict.fromkeys(p50),
        )
    )


@pytest.fixture
def load_forecast() -> FakeForecast:
    first = slot_start(PLAN_NOW)
    p50 = {first + i * SLOT: 600.0 for i in range(FORECAST_SLOTS)}
    return FakeForecast(LoadForecast(issued_at=PLAN_NOW, p50_w=p50, basis={"ledger": 0, "ha": 0}))


@pytest.fixture
def ledger(
    engine: Engine,
    clock: ManualClock,
    prices: PriceService,
    settings_service: SettingsService,
    audit: AuditLog,
) -> LedgerService:
    return LedgerService(engine, clock, prices, settings_service, audit, FakeSink())


@pytest.fixture
def live() -> LiveState:
    state = LiveState()
    live_signals(state, {"battery.soc_pct": 50.0})
    return state


@pytest.fixture
def store(engine: Engine) -> PlanStore:
    return PlanStore(engine)


@pytest.fixture
def service_with(
    cfg: Config,
    settings_service: SettingsService,
    prices: PriceService,
    forecast: FakeForecast,
    load_forecast: FakeForecast,
    ledger: LedgerService,
    live: LiveState,
    store: PlanStore,
    clock: ManualClock,
) -> Callable[..., PlanningService]:
    """Baut den Dienst; Schlüsselwortargumente ersetzen Argumente wie ``solver`` oder ``cfg``."""

    def build(**overrides: Any) -> PlanningService:
        args: dict[str, Any] = {
            "cfg": cfg,
            "settings": settings_service,
            "prices": prices,
            "forecast": forecast,
            "load_forecast": load_forecast,
            "ledger": ledger,
            "live": live,
            "store": store,
            "clock": clock,
            "solver": solve_inline,
        }
        return PlanningService(**{**args, **overrides})

    return build


@pytest.fixture
def service(service_with: Callable[..., PlanningService]) -> PlanningService:
    return service_with()
