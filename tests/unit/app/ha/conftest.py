import asyncio
from collections.abc import Iterable
from datetime import date

import pytest
from tests.app_helpers import T_APP, price_row, sample_inputs

from bindaems.app.core_link import LiveState
from bindaems.app.forecast.service import PvForecast
from bindaems.app.ha.entities import build_entities
from bindaems.app.ha.publisher import HaPublisher
from bindaems.app.prices.service import PriceService
from bindaems.app.prices.store import PriceStore
from bindaems.shared.timeutil import local_day_slots


class FakeForecast:
    def __init__(self, forecast: PvForecast | None) -> None:
        self._forecast = forecast

    def latest(self) -> PvForecast | None:
        return self._forecast


class FakeTransport:
    """Zeichnet Veröffentlichungen, Abos, Verbindungen und Wartezeiten auf."""

    def __init__(
        self, incoming: Iterable[tuple[str, bytes]], fail_first_connection: bool, interval: float
    ) -> None:
        self.published: list[tuple[str, str, bool]] = []
        self.subscriptions: list[str] = []
        self.connections = 0
        self.sleeps: list[float] = []
        self.ticks = 0
        self._incoming = list(incoming)
        self._fail_first = fail_first_connection
        self._interval = interval

    async def __aenter__(self) -> "FakeTransport":
        self.connections += 1
        if self._fail_first and self.connections == 1:
            raise ConnectionRefusedError("Mosquitto nicht erreichbar")
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def publish(self, topic: str, payload: str, *, retain: bool) -> None:
        self.published.append((topic, payload, retain))

    async def subscribe(self, topic: str) -> None:
        self.subscriptions.append(topic)

    async def messages(self):
        incoming, self._incoming = self._incoming, []
        for item in incoming:
            yield item
        await asyncio.Event().wait()  # Verbindung bleibt offen

    async def sleep(self, seconds: float) -> None:
        if seconds == self._interval:
            self.ticks += 1
        else:
            self.sleeps.append(seconds)
        await asyncio.sleep(0)

    def published_state(self, object_id: str) -> str | None:
        payloads = [p for t, p, _ in self.published if t == f"bindaems/state/{object_id}"]
        return payloads[-1] if payloads else None

    def topics_with_retain(self) -> set[tuple[str, bool]]:
        return {(topic, retain) for topic, _, retain in self.published}

    def count(self, topic: str) -> int:
        return sum(1 for t, _, _ in self.published if t == topic)


@pytest.fixture
def live() -> LiveState:
    state = LiveState()
    state.connected = True
    state.state = {
        "signals": {
            "battery.soc_pct": {"v": 55.0, "ts": "…", "q": "ok"},
            "vehicle.tesla.soc_pct": {"v": 70.0, "ts": "…", "q": "stale"},
        },
        "derived": {"grid_w": 512.0, "wallbox_w": {"evcs": 0.0}},
    }
    state.alarms = [{"id": "competitor.dess", "severity": "warning"}]
    state.health = {
        "mode": "OBSERVE",
        "adapters": [{"name": "victron", "connected": True}, {"name": "twc", "connected": False}],
    }
    return state


@pytest.fixture
def store(engine, clock) -> PriceStore:
    return PriceStore(engine, clock)


@pytest.fixture
def price_service(store, settings_service) -> PriceService:
    store.save([price_row(T_APP, 10.0, "primary")])
    return PriceService(store, settings_service)


@pytest.fixture
def forecast_service() -> FakeForecast:
    today = local_day_slots(date(2026, 10, 9))
    return FakeForecast(PvForecast(T_APP, "open_meteo", dict.fromkeys(today[40:44], 2000.0)))


@pytest.fixture
def publisher_env(cfg):
    def build(
        incoming: Iterable[tuple[str, bytes]] = (), fail_first_connection: bool = False
    ) -> tuple[HaPublisher, FakeTransport]:
        mqtt = cfg.homeassistant.mqtt
        transport = FakeTransport(incoming, fail_first_connection, mqtt.publish_interval_s)
        publisher = HaPublisher(
            mqtt,
            build_entities(cfg),
            sample_inputs,
            transport_factory=lambda: transport,
            sleep=transport.sleep,
        )
        return publisher, transport

    return build
