"""Fester Katalog der Verlaufsreihen und die InfluxQL-Abfragen dazu.

Nur Reihen aus dem Katalog sind abfragbar; Tag-Werte stammen aus ``config.yaml`` und werden
trotzdem maskiert.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from bindaems.app.history.influx import RP_LONG, RP_RAW, quote_ident, quote_str
from bindaems.shared.config import Config
from bindaems.shared.influx.lineprotocol import epoch_ms

STEPS_S = (10, 30, 60, 300, 900, 3600, 21600, 86400)
MAX_POINTS = 1000
LONG_MIN_STEP_S = 60  # RP long enthält 1-Minuten-Mittel
RAW_MAX_SPAN = timedelta(days=2)
RAW_RETENTION = timedelta(days=89)  # raw hält 90 Tage


@dataclass(frozen=True)
class SeriesSpec:
    id: str
    label: str
    unit: str
    measurement: str
    field: str
    tags: Mapping[str, str] = dataclasses.field(default_factory=dict)
    raw: bool = True  # False: nur in RP long (von der app geschrieben)
    scale: float = 1.0


def _power(series_id: str, label: str, source: str, id_: str) -> SeriesSpec:
    tags = {"source": source, "id": id_, "phase": "total"}
    return SeriesSpec(series_id, label, "W", "power", "p_w", tags)


def build_catalog(cfg: Config) -> dict[str, SeriesSpec]:
    specs = [
        _power("grid", "Netz", "grid", "grid"),
        _power("pv", "PV", "derived", "pv_total"),
        _power("battery", "Akku", "battery", "battery"),
        _power("house", "Haus", "derived", "house_load"),
        _power("consumption", "Verbrauch gesamt", "derived", "consumption"),
        *(_power(f"wallbox.{name}", f"Wallbox {name}", "wallbox", name) for name in cfg.wallboxes),
        SeriesSpec("soc.battery", "Akku-SOC", "%", "soc", "pct", {"id": "battery"}),
        *(
            SeriesSpec(f"soc.{key}", f"SOC {vehicle.name}", "%", "soc", "pct", {"id": key})
            for key, vehicle in cfg.vehicles.items()
        ),
        SeriesSpec(
            "price", "Bezugspreis", "ct/kWh", "price", "ct_kwh", {"kind": "import_gross"}, raw=False
        ),
        SeriesSpec(
            "forecast.pv",
            "PV-Prognose",
            "W",
            "forecast",
            "kw",
            {"kind": "pv", "quantile": "p50"},
            raw=False,
            scale=1000.0,
        ),
    ]
    return {spec.id: spec for spec in specs}


def choose_rp_and_step(
    spec: SeriesSpec, start: datetime, end: datetime, now: datetime
) -> tuple[str, int]:
    """Rohdaten nur für kurze, junge Zeiträume; höchstens etwa 1000 Punkte je Reihe."""
    span = (end - start).total_seconds()
    use_raw = spec.raw and end - start <= RAW_MAX_SPAN and start >= now - RAW_RETENTION
    step = next((s for s in STEPS_S if span / s <= MAX_POINTS), STEPS_S[-1])
    if not use_raw:
        step = max(step, LONG_MIN_STEP_S)
    return (RP_RAW if use_raw else RP_LONG), step


def build_query(spec: SeriesSpec, rp: str, start: datetime, end: datetime, step_s: int) -> str:
    conditions = [
        f"{quote_ident(key)}={quote_str(value)}" for key, value in sorted(spec.tags.items())
    ]
    conditions += [f"time >= {epoch_ms(start)}ms", f"time < {epoch_ms(end)}ms"]
    # nur Katalogreihen; Bezeichner und Werte sind maskiert
    return (
        f'SELECT mean({quote_ident(spec.field)}) AS "v" '  # noqa: S608
        f"FROM {quote_ident(rp)}.{quote_ident(spec.measurement)} "
        f"WHERE {' AND '.join(conditions)} GROUP BY time({step_s}s) fill(none)"
    )
