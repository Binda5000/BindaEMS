"""Zustandsspeicher: letzte Werte aller Signale mit Zeitstempel und Qualität."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from bindaems.shared.domain import Quality, Reading, SignalKind, Snapshot, Value
from bindaems.shared.timeutil import Clock, ensure_utc


@dataclass
class _Entry:
    value: Value
    ts: datetime
    source: str
    kind: SignalKind
    invalid: bool


class StateStore:
    """Hält die Signale aller Adapter; die Qualität wird bei jedem ``snapshot()`` neu bewertet.

    * ``MEASUREMENT`` veraltet, wenn das Alter die Frischegrenze der Quelle überschreitet
      oder die Quelle getrennt ist. Bei Quellen mit ``activity_based=True`` zählt das letzte
      Lebenszeichen der Quelle statt der letzten Wertänderung – für Quellen, die nur
      Änderungen senden (Victron-MQTT mit ``suppress-republish``).
    * ``STATE`` gilt, solange die Quelle verbunden ist.
    * ``None``, ``NaN`` und ``±inf`` sind ``INVALID``.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._freshness: dict[str, float | None] = {}
        self._connected: dict[str, bool] = {}
        self._entries: dict[str, _Entry] = {}
        self._ok_since: dict[str, datetime] = {}
        self._activity_based: dict[str, bool] = {}
        self._last_activity: dict[str, datetime] = {}

    def register_source(
        self, name: str, freshness_s: float | None, *, activity_based: bool = False
    ) -> None:
        self._freshness[name] = freshness_s
        self._activity_based[name] = activity_based
        self._connected.setdefault(name, False)

    def touch(self, source: str) -> None:
        """Lebenszeichen einer Quelle, auch wenn sich kein Wert geändert hat."""
        if source not in self._freshness:
            raise KeyError(f"unbekannte Quelle: {source}")
        self._last_activity[source] = self._clock.now()

    def update(
        self,
        signal: str,
        value: Value,
        *,
        source: str,
        kind: SignalKind,
        ts: datetime | None = None,
    ) -> None:
        if source not in self._freshness:
            raise KeyError(f"unbekannte Quelle: {source}")
        invalid = value is None or (isinstance(value, float) and not math.isfinite(value))
        self._last_activity[source] = self._clock.now()
        self._entries[signal] = _Entry(
            value=None if invalid else value,
            ts=ensure_utc(ts) if ts is not None else self._clock.now(),
            source=source,
            kind=kind,
            invalid=invalid,
        )

    def set_connected(self, source: str, connected: bool) -> None:
        if source not in self._freshness:
            raise KeyError(f"unbekannte Quelle: {source}")
        self._connected[source] = connected

    def snapshot(self) -> Snapshot:
        now = self._clock.now()
        readings: dict[str, Reading] = {}
        for signal, entry in self._entries.items():
            quality = self._quality(entry, now)
            readings[signal] = Reading(entry.value, entry.ts, quality, entry.source, entry.kind)
            if quality is Quality.OK:
                self._ok_since.setdefault(signal, now)
            else:
                self._ok_since.pop(signal, None)
        return Snapshot(now, MappingProxyType(readings))

    def ok_since(self, signal: str) -> datetime | None:
        """Beginn der aktuellen ununterbrochenen OK-Phase (Stand des letzten ``snapshot()``)."""
        return self._ok_since.get(signal)

    def _quality(self, entry: _Entry, now: datetime) -> Quality:
        if entry.invalid:
            return Quality.INVALID
        if not self._connected.get(entry.source, False):
            return Quality.STALE
        if entry.kind is SignalKind.MEASUREMENT:
            freshness = self._freshness[entry.source]
            reference = entry.ts
            if self._activity_based[entry.source]:
                reference = max(reference, self._last_activity.get(entry.source, reference))
            if freshness is not None and (now - reference).total_seconds() > freshness:
                return Quality.STALE
        return Quality.OK
