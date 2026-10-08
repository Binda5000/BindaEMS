"""Gemeinsame Domänentypen für ems-core und ems-app."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

Value = float | int | str | bool | None


class Quality(StrEnum):
    OK = "ok"
    STALE = "stale"
    INVALID = "invalid"


class SignalKind(StrEnum):
    """MEASUREMENT veraltet nach Zeit; STATE gilt, solange die Quelle verbunden ist."""

    MEASUREMENT = "measurement"
    STATE = "state"


@dataclass(frozen=True, slots=True)
class Reading:
    value: Value
    ts: datetime
    quality: Quality
    source: str
    kind: SignalKind


@dataclass(frozen=True)
class Snapshot:
    """Unveränderlicher Zustand aller Signale zu einem Zeitpunkt."""

    ts: datetime
    readings: Mapping[str, Reading]

    def get(self, signal: str) -> Reading | None:
        return self.readings.get(signal)

    def ok(self, signal: str) -> Value:
        """Wert des Signals, aber nur bei Qualität ``OK`` – sonst ``None``."""
        reading = self.readings.get(signal)
        if reading is None or reading.quality is not Quality.OK:
            return None
        return reading.value

    def matching(self, prefix: str) -> dict[str, Reading]:
        return {name: r for name, r in self.readings.items() if name.startswith(prefix)}


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Alarm:
    id: str
    severity: Severity
    message: str
    since: datetime


@dataclass(frozen=True)
class SlotFlows:
    """Energieflüsse eines Viertelstunden-Slots und die Zählerstände an seinen Grenzen.

    ``counters`` bildet je Zählersignal ``(Start, Ende)`` ab; ``None`` heißt unbekannt.
    """

    slot_start: datetime
    covered_s: float
    flows_wh: Mapping[str, float]
    counters: Mapping[str, tuple[float | None, float | None]]
