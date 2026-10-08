from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

import pytest
from pydantic import SecretStr
from tests.helpers import FakeSink
from tests.unit.core.state.conftest import BASE

from bindaems.core.runtime import CoreRuntime
from bindaems.shared.config import Config, Secrets
from bindaems.shared.domain import SignalKind, Value
from bindaems.shared.timeutil import ManualClock

TOKEN = "x" * 32
VALUES: dict[str, float] = BASE | {
    "wallbox.evcs.power_w": 0.0,
    "wallbox.twc.power_w": 0.0,
    "battery.power_w": 600.0,
    "battery.soc_pct": 50.0,
}


@pytest.fixture
def runtime_env(cfg: Config) -> Callable[..., tuple[CoreRuntime, FakeSink, ManualClock]]:
    def build(
        start: str = "2026-10-08T10:00:00+00:00",
        state: Mapping[str, Value] | None = None,
        omit: Sequence[str] = (),
        fail_first: bool = False,
        extra: Mapping[str, float] | None = None,
        **kwargs: Any,
    ) -> tuple[CoreRuntime, FakeSink, ManualClock]:
        clock = ManualClock(datetime.fromisoformat(start))
        sink = FakeSink()
        secrets = Secrets(internal_token=SecretStr(TOKEN))
        rt = CoreRuntime(
            cfg, secrets, clock=clock, adapters=[], sink=sink, serve_api=False, **kwargs
        )
        rt.store.register_source("victron", 5.0, activity_based=True)
        rt.store.set_connected("victron", True)
        original = rt.cycle_once
        calls = [0]

        def cycle_once() -> None:  # Hook: frische Messwerte vor jedem Zyklus
            calls[0] += 1
            if fail_first and calls[0] == 1:
                raise RuntimeError("Testfehler im ersten Zyklus")
            for signal, value in (VALUES | dict(extra or {})).items():
                if signal not in omit:
                    rt.store.update(signal, value, source="victron", kind=SignalKind.MEASUREMENT)
            for signal, value in (state or {}).items():
                rt.store.update(signal, value, source="victron", kind=SignalKind.STATE)
            original()

        rt.cycle_once = cycle_once  # type: ignore[method-assign]
        return rt, sink, clock

    return build
