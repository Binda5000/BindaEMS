"""Bausteine, die core und app brauchen, liegen in shared – nicht doppelt im core."""

import importlib.util

import pytest

MOVED = {
    "bindaems.core.telemetry.lineprotocol": "bindaems.shared.influx.lineprotocol",
    "bindaems.core.telemetry.spool": "bindaems.shared.influx.spool",
    "bindaems.core.telemetry.influx": "bindaems.shared.influx.writer",
    "bindaems.core.logging": "bindaems.shared.logging",
    "bindaems.core.broadcast": "bindaems.shared.broadcast",
}


@pytest.mark.parametrize(("old", "new"), MOVED.items())
def test_shared_building_blocks_moved(old: str, new: str) -> None:
    assert importlib.util.find_spec(old) is None
    assert importlib.util.find_spec(new) is not None


def test_retry_tls_and_sink_are_defined_in_shared() -> None:
    from bindaems.shared.influx.lineprotocol import PointSink
    from bindaems.shared.mqtt import build_tls_context
    from bindaems.shared.retry import Backoff, StatusLog

    assert Backoff.__module__ == StatusLog.__module__ == "bindaems.shared.retry"
    assert build_tls_context.__module__ == "bindaems.shared.mqtt"
    assert PointSink.__module__ == "bindaems.shared.influx.lineprotocol"
