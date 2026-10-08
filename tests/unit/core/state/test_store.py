from datetime import UTC, datetime

import pytest

from bindaems.core.state.store import StateStore
from bindaems.shared.domain import Quality, SignalKind
from bindaems.shared.timeutil import ManualClock

T0 = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)
M, S = SignalKind.MEASUREMENT, SignalKind.STATE


def make() -> tuple[ManualClock, StateStore]:
    clock = ManualClock(T0)
    store = StateStore(clock)
    store.register_source("victron", 5.0)
    store.set_connected("victron", True)
    return clock, store


def quality(store: StateStore, signal: str) -> Quality:
    reading = store.snapshot().get(signal)
    assert reading is not None
    return reading.quality


def test_measurement_ok_then_stale() -> None:
    clock, s = make()
    s.update("grid.power_w", 1200.0, source="victron", kind=M)
    assert quality(s, "grid.power_w") is Quality.OK
    clock.advance(5.1)
    assert quality(s, "grid.power_w") is Quality.STALE


def test_state_signal_ok_while_connected() -> None:
    clock, s = make()
    s.update("ess.hub4_mode", 1, source="victron", kind=S)
    clock.advance(3600)
    assert s.snapshot().ok("ess.hub4_mode") == 1
    s.set_connected("victron", False)
    assert quality(s, "ess.hub4_mode") is Quality.STALE


def test_none_and_nan_are_invalid() -> None:
    _, s = make()
    s.update("grid.l1.power_w", None, source="victron", kind=M)
    s.update("grid.l2.power_w", float("nan"), source="victron", kind=M)
    s.update("grid.l3.power_w", float("inf"), source="victron", kind=M)
    snap = s.snapshot()
    assert quality(s, "grid.l1.power_w") is Quality.INVALID
    assert quality(s, "grid.l2.power_w") is Quality.INVALID
    assert quality(s, "grid.l3.power_w") is Quality.INVALID
    assert snap.ok("grid.l1.power_w") is None


def test_unknown_source_rejected() -> None:
    _, s = make()
    with pytest.raises(KeyError):
        s.update("x", 1, source="nope", kind=M)


def test_snapshot_is_immutable() -> None:
    _, s = make()
    with pytest.raises(TypeError):
        s.snapshot().readings["x"] = None  # type: ignore[index]


def test_ok_since_resets_after_stale() -> None:
    clock, s = make()
    s.update("battery.soc_pct", 50.0, source="victron", kind=M)
    s.snapshot()
    assert s.ok_since("battery.soc_pct") == T0
    clock.advance(10)
    s.snapshot()
    assert s.ok_since("battery.soc_pct") is None
    s.update("battery.soc_pct", 51.0, source="victron", kind=M)
    s.snapshot()
    assert s.ok_since("battery.soc_pct") == clock.now()


def test_matching_prefix() -> None:
    _, s = make()
    s.update("grid.l1.power_w", 1.0, source="victron", kind=M)
    s.update("battery.soc_pct", 2.0, source="victron", kind=M)
    assert set(s.snapshot().matching("grid.")) == {"grid.l1.power_w"}


def test_activity_based_source_keeps_unchanged_measurement_valid() -> None:
    clock = ManualClock(T0)
    store = StateStore(clock)
    store.register_source("victron", 5.0, activity_based=True)
    store.set_connected("victron", True)
    store.update("pv.huawei.power_w", 0.0, source="victron", kind=M)  # nachts konstant 0 W
    clock.advance(4)
    store.touch("victron")  # andere Nachricht der Quelle, Wert unverändert
    clock.advance(4)
    assert store.snapshot().ok("pv.huawei.power_w") == 0.0
    clock.advance(1.1)  # 5,1 s ohne Lebenszeichen der Quelle
    assert quality(store, "pv.huawei.power_w") is Quality.STALE


def test_touch_unknown_source_rejected() -> None:
    _, store = make()
    with pytest.raises(KeyError):
        store.touch("unbekannt")
