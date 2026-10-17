from datetime import UTC, datetime

import pytest

from bindaems.app.charging.tracker import ChargingTracker
from bindaems.app.core_link import LiveState

T0 = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)


@pytest.fixture
def live() -> LiveState:
    state = LiveState()
    state.connected = True
    state.state = {"signals": {}}
    return state


@pytest.fixture
def tracker(engine, clock, cfg, live) -> ChargingTracker:
    return ChargingTracker(engine, clock, cfg, live)
