from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
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
def runtime_env(
    cfg: Config, tmp_path: Path
) -> Callable[..., tuple[CoreRuntime, FakeSink, ManualClock]]:
    def build(
        start: str = "2026-10-08T10:00:00+00:00",
        state: Mapping[str, Value] | None = None,
        omit: Sequence[str] = (),
        fail_first: bool = False,
        extra: Mapping[str, float] | None = None,
        configure: Callable[[Config], Config] | None = None,
        before_cycle: Callable[[], None] | None = None,
        tessie_token: str | None = None,
        **kwargs: Any,
    ) -> tuple[CoreRuntime, FakeSink, ManualClock]:
        """Laufzeit ohne Adapter; ``before_cycle`` läuft vor jedem Zyklus nach den Messwerten.

        Die Uhr der Safety-Schicht ist dieselbe ``ManualClock`` wie die Wanduhr, sofern der Test
        nicht selbst eine ``safety_clock`` übergibt. Ohne ``tessie_token`` ist Tessie für den
        Tesla nie erreichbar.
        """
        clock = ManualClock(datetime.fromisoformat(start))
        sink = FakeSink()
        token = SecretStr(tessie_token) if tessie_token is not None else None
        secrets = Secrets(internal_token=SecretStr(TOKEN), tessie_token=token)
        # kein Test liest oder schreibt unter /data
        core = cfg.core.model_copy(update={"data_dir": tmp_path / "core"})
        local = cfg.model_copy(update={"core": core})
        kwargs.setdefault("safety_clock", clock)
        rt = CoreRuntime(
            configure(local) if configure is not None else local,
            secrets,
            clock=clock,
            adapters=[],
            sink=sink,
            serve_api=False,
            **kwargs,
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
            if before_cycle is not None:  # nach ``state``: Was die Anlage meldet, gewinnt
                before_cycle()
            original()

        rt.cycle_once = cycle_once  # type: ignore[method-assign]
        return rt, sink, clock

    return build
