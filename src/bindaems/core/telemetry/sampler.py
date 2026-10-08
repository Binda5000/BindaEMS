"""Abtastung des Zustands für InfluxDB: Abbildung der Signale auf Messungen und ihre Takte.

Zahlen werden immer als Float geschrieben – InfluxDB 1.x lehnt sonst Zeilen wegen
Feldtyp-Konflikten ab (Victron sendet mal ``500``, mal ``500.5``). Zustände gehen nach Typ
getrennt als ``value_num``/``value_str``/``value_bool`` in ``status``, nur bei Änderung.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from bindaems.core.state.derived import Derived
from bindaems.core.telemetry.lineprotocol import FieldValue, Point
from bindaems.shared.domain import Quality, Reading, SignalKind, SlotFlows, Snapshot, Value
from bindaems.shared.timeutil import Clock

DUE_TOLERANCE_S = 0.05
DERIVED_CADENCE_S = 2.0
DERIVED_FIELDS = ("house_load_w", "battery_ac_w", "pv_total_w", "consumption_w")


class PointSink(Protocol):
    def write(self, point: Point) -> None: ...


@dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern[str]
    measurement: str
    tags: Mapping[str, str]  # Vorlagen mit {id}, {n}, {source}, {direction}
    field: str
    cadence_s: float
    scale: float = 1.0


_P = r"l(?P<n>[123])"
_ID = r"(?P<id>[^.]+)"
_TOTAL = {"phase": "total"}
_PHASE = {"phase": "L{n}"}


def _power(
    pattern: str,
    source: str,
    id_: str,
    phase: Mapping[str, str],
    field: str,
    cadence_s: float,
    scale: float = 1.0,
) -> _Rule:
    tags = {"source": source, "id": id_, **phase}
    return _Rule(re.compile(pattern), "power", tags, field, cadence_s, scale)


_ENERGY_TAGS = {"source": "{source}", "id": "{id}", "direction": "{direction}"}
_ENERGY_PREFIX = r"(?P<source>[^.]+)(?:\.(?P<id>[^.]+))?"

RULES: tuple[_Rule, ...] = (
    _power(rf"grid\.{_P}\.power_w", "grid", "grid", _PHASE, "p_w", 2.0),
    _power(rf"grid\.{_P}\.current_a", "grid", "grid", _PHASE, "i_a", 2.0),
    _power(rf"grid\.{_P}\.voltage_v", "grid", "grid", _PHASE, "u_v", 10.0),
    _power(r"grid\.power_w", "grid", "grid", _TOTAL, "p_w", 2.0),
    _power(rf"pv\.{_ID}\.power_w", "pv", "{id}", _TOTAL, "p_w", 2.0),
    _power(rf"pv\.{_ID}\.{_P}\.power_w", "pv", "{id}", _PHASE, "p_w", 2.0),
    _power(r"pv_dc\.power_w", "pv", "dc", _TOTAL, "p_w", 2.0),
    _power(r"battery\.power_w", "battery", "battery", _TOTAL, "p_w", 2.0),
    _power(r"battery\.current_a", "battery", "battery", _TOTAL, "i_a", 2.0),
    _power(r"battery\.voltage_v", "battery", "battery", _TOTAL, "u_v", 10.0),
    _power(rf"wallbox\.{_ID}\.power_w", "wallbox", "{id}", _TOTAL, "p_w", 2.0),
    _power(rf"wallbox\.{_ID}\.{_P}\.power_w", "wallbox", "{id}", _PHASE, "p_w", 2.0),
    _power(rf"wallbox\.{_ID}\.{_P}\.current_a", "wallbox", "{id}", _PHASE, "i_a", 2.0),
    _power(rf"wallbox\.{_ID}\.{_P}\.voltage_v", "wallbox", "{id}", _PHASE, "u_v", 10.0),
    _power(rf"wallbox\.{_ID}\.current_a", "wallbox", "{id}", _TOTAL, "i_a", 2.0),
    _power(
        rf"(?P<id>consumption|consumption_input|consumption_output)\.{_P}\.power_w",
        "load",
        "{id}",
        _PHASE,
        "p_w",
        10.0,
    ),
    _power(rf"load\.{_ID}\.power_w", "load", "{id}", _TOTAL, "p_w", 10.0),
    _power(rf"load\.{_ID}\.{_P}\.power_w", "load", "{id}", _PHASE, "p_w", 10.0),
    _power(rf"vebus\.{_P}\.(?P<id>ac_in|ac_out)_power_w", "vebus", "{id}", _PHASE, "p_w", 10.0),
    _power(rf"vehicle\.{_ID}\.charger_power_kw", "vehicle", "{id}", _TOTAL, "p_w", 10.0, 1000.0),
    _Rule(
        re.compile(rf"{_ENERGY_PREFIX}\.energy_(?P<direction>import|export)_kwh"),
        "energy",
        _ENERGY_TAGS,
        "kwh",
        60.0,
    ),
    _Rule(
        re.compile(rf"{_ENERGY_PREFIX}\.(?P<direction>energy|total|session)_kwh"),
        "energy",
        _ENERGY_TAGS,
        "kwh",
        60.0,
    ),
    _Rule(re.compile(r"(?P<id>battery)\.soc_pct"), "soc", {"id": "{id}"}, "pct", 30.0),
    _Rule(re.compile(rf"vehicle\.{_ID}\.soc_pct"), "soc", {"id": "{id}"}, "pct", 30.0),
)

_DIRECTIONS = {"energy": "forward"}


def _float(value: Value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _match(signal: str) -> tuple[_Rule, dict[str, str]] | None:
    for rule in RULES:
        found = rule.pattern.fullmatch(signal)
        if found:
            groups = {key: text for key, text in found.groupdict().items() if text is not None}
            if "source" in groups:
                groups.setdefault("id", groups["source"])
            if "direction" in groups:
                groups["direction"] = _DIRECTIONS.get(groups["direction"], groups["direction"])
            return rule, groups
    return None


def _status(signal: str, value: Value, ts: datetime) -> Point | None:
    field: dict[str, FieldValue]
    if isinstance(value, bool):
        field = {"value_bool": value}
    elif isinstance(value, int | float):
        field = {"value_num": float(value)}
    elif isinstance(value, str):
        field = {"value_str": value}
    else:
        return None
    return Point("status", {"id": signal}, field, ts)


def signal_to_point(signal: str, reading: Reading) -> Point | None:
    """Punkt laut Abbildungstabelle; Zustände (auch Text-/Wahrheitswerte) als ``status``."""
    matched = _match(signal)
    if matched is not None:
        rule, groups = matched
        number = _float(reading.value)
        if number is None:
            return None
        tags = {key: template.format(**groups) for key, template in rule.tags.items()}
        return Point(rule.measurement, tags, {rule.field: number * rule.scale}, reading.ts)
    if reading.kind is SignalKind.STATE or isinstance(reading.value, str | bool):
        return _status(signal, reading.value, reading.ts)
    return None  # Messwert ohne Regel


def cadence_for(signal: str, kind: SignalKind) -> float | None:
    """Abtasttakt in Sekunden; ``None`` heißt: nur bei Änderung schreiben."""
    if kind is SignalKind.STATE:
        return None
    matched = _match(signal)
    return matched[0].cadence_s if matched is not None else None


class TelemetrySampler:
    def __init__(self, sink: PointSink, clock: Clock) -> None:
        self._sink = sink
        self._clock = clock
        self._written_at: dict[str, datetime] = {}
        self._last_fields: dict[str, Mapping[str, FieldValue]] = {}

    def on_cycle(self, snap: Snapshot, derived: Derived) -> None:
        now = self._clock.now()
        for signal, reading in snap.readings.items():
            if reading.quality is not Quality.OK:
                continue
            point = signal_to_point(signal, reading)
            if point is None:
                continue
            cadence = cadence_for(signal, reading.kind)
            if cadence is None:
                if self._last_fields.get(signal) == point.fields:
                    continue
                self._last_fields[signal] = point.fields
                self._sink.write(point)
            elif self._due(signal, cadence, now):
                # Abtastzeitpunkt statt Änderungszeitpunkt: unveränderte Werte (z. B. PV nachts)
                # sendet der Cerbo nicht erneut – mit altem Zeitstempel überschriebe InfluxDB sie
                self._sink.write(replace(point, ts=snap.ts))
        for name in DERIVED_FIELDS:
            value = getattr(derived, name)
            if value is None or not self._due(f"derived.{name}", DERIVED_CADENCE_S, now):
                continue
            tags = {"source": "derived", "id": name.removesuffix("_w"), "phase": "total"}
            self._sink.write(Point("power", tags, {"p_w": float(value)}, snap.ts))

    def write_slot_flows(self, flows: SlotFlows) -> None:
        for key, wh in flows.flows_wh.items():
            self._sink.write(Point("flows", {"flow": key}, {"wh": float(wh)}, flows.slot_start))
        self._sink.write(
            Point(
                "flows",
                {"flow": "_coverage"},
                {"covered_s": float(flows.covered_s)},
                flows.slot_start,
            )
        )

    def _due(self, key: str, cadence_s: float, now: datetime) -> bool:
        last = self._written_at.get(key)
        if last is not None and (now - last).total_seconds() < cadence_s - DUE_TOLERANCE_S:
            return False
        self._written_at[key] = now
        return True
