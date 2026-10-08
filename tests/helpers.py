"""Gemeinsame Test-Helfer (nur für Tests)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType

from bindaems.shared.domain import Quality, Reading, SignalKind, Snapshot, Value

T0 = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)


def snap(
    values: Mapping[str, Value],
    *,
    kind: SignalKind = SignalKind.MEASUREMENT,
    quality: Quality = Quality.OK,
    ts: datetime = T0,
    source: str = "victron",
) -> Snapshot:
    """Snapshot, in dem alle ``values`` dieselbe Qualität, Art und Quelle haben."""
    readings = {name: Reading(value, ts, quality, source, kind) for name, value in values.items()}
    return Snapshot(ts, MappingProxyType(readings))


def snap_at(ts: datetime, values: Mapping[str, Value], **kw: object) -> Snapshot:
    return snap(values, ts=ts, **kw)  # type: ignore[arg-type]
