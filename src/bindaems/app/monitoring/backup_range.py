"""Reichweite des Akkus bei Netzausfall: wie lange versorgt er die USV-gestützten Lasten.

Die Lasten hängen an Victron AC-Out (``consumption_output``); die Grundlage ist deren Mittel der
letzten sieben lokalen Tage, getrennt nach Tag (alle Viertelstunden) und Nacht (lokal
00:00–04:45). Gerechnet wird ohne PV: ``kWh = (SOC − Untergrenze) / 100 × nutzbar × η`` mit
``η = √(Wirkungsgrad Hin und Zurück)``, ``Stunden = kWh × 1000 / Last``.
"""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

import structlog

from bindaems.app.history.influx import (
    RP_LONG,
    InfluxQueryError,
    InfluxReader,
    Series,
    quote_ident,
    quote_str,
)
from bindaems.app.schedule import next_local
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.timeutil import LOCAL_TZ, Clock, local_day_slots

log = structlog.get_logger(__name__)

BASIS_DAYS = 7
MIN_DAY_SLOTS = 192
MIN_NIGHT_SLOTS = 40
NIGHT_END = time(5, 0)  # Nacht: lokaler Beginn 00:00 bis 04:45
MIN_LOAD_W = 50.0  # darunter ist die Last nicht plausibel
MAX_BASIS_AGE = timedelta(hours=48)
REFRESH_AT = time(0, 20)
RETRY_S = 600.0
PHASES = frozenset({"L1", "L2", "L3"})
_TEXT_LIMIT_H = 10.0
NBSP = chr(0xA0)  # geschütztes Leerzeichen vor der Einheit

LoadSource = Literal["tag", "ausfall"]


@dataclass(frozen=True)
class LoadBasis:
    day_w: float | None
    night_w: float | None
    day_slots: int
    night_slots: int
    first_day: date
    last_day: date


@dataclass(frozen=True)
class BackupRange:
    now_h: float | None
    reserve_h: float | None
    load_w: float | None
    load_source: LoadSource | None
    night_h: float | None


def ac_out_query(start: datetime, end: datetime) -> str:
    """Viertelstundenmittel der drei Phasen an AC-Out in ``[start, end)``."""
    conditions = [
        f"{quote_ident('source')}={quote_str('load')}",
        f"{quote_ident('id')}={quote_str('consumption_output')}",
        f"time >= {epoch_ms(start)}ms",
        f"time < {epoch_ms(end)}ms",
    ]
    # feste Bezeichner und Werte, maskiert; Zeitgrenzen sind ganze Zahlen
    return (
        f"SELECT mean({quote_ident('p_w')}) "  # noqa: S608
        f"FROM {quote_ident(RP_LONG)}.{quote_ident('power')} "
        f"WHERE {' AND '.join(conditions)} "
        f"GROUP BY time(15m),{quote_ident('phase')} fill(none)"
    )


def slot_loads(series: Sequence[Series]) -> dict[datetime, float]:
    """Je Viertelstunde die Summe der drei Phasen; fehlt eine Phase, fällt sie weg."""
    by_slot: defaultdict[datetime, dict[str, float]] = defaultdict(dict)
    for item in series:
        phase = item.tags.get("phase")
        if phase not in PHASES or "time" not in item.columns or "mean" not in item.columns:
            continue
        t_index, v_index = item.columns.index("time"), item.columns.index("mean")
        for row in item.values:
            value = row[v_index]
            if value is None or not math.isfinite(value):
                continue
            slot = datetime.fromtimestamp(row[t_index] / 1000, tz=UTC)
            by_slot[slot][phase] = float(value)
    return {slot: sum(p.values()) for slot, p in by_slot.items() if p.keys() == PHASES}


def load_basis(loads: Mapping[datetime, float], today: date) -> LoadBasis:
    """Mittel über die lokalen Tage ``today−7 … today−1``; zu wenige Viertelstunden ergeben None."""
    first, last = today - timedelta(days=BASIS_DAYS), today - timedelta(days=1)
    day_values: list[float] = []
    night_values: list[float] = []
    for offset in range(BASIS_DAYS):
        for slot in local_day_slots(first + timedelta(days=offset)):
            if slot not in loads:
                continue
            day_values.append(loads[slot])
            if slot.astimezone(LOCAL_TZ).time() < NIGHT_END:
                night_values.append(loads[slot])
    return LoadBasis(
        day_w=sum(day_values) / len(day_values) if len(day_values) >= MIN_DAY_SLOTS else None,
        night_w=(
            sum(night_values) / len(night_values) if len(night_values) >= MIN_NIGHT_SLOTS else None
        ),
        day_slots=len(day_values),
        night_slots=len(night_values),
        first_day=first,
        last_day=last,
    )


def _hours(
    soc_pct: float | None, floor_pct: float, usable_kwh: float, eta: float, load_w: float | None
) -> float | None:
    if soc_pct is None or load_w is None or load_w < MIN_LOAD_W:
        return None
    kwh = max(0.0, soc_pct - floor_pct) / 100 * usable_kwh * eta
    return kwh * 1000 / load_w


def backup_range(
    *,
    soc_pct: float | None,
    reserve_pct: float,
    floor_pct: float,
    usable_kwh: float,
    roundtrip_pct: float,
    basis: LoadBasis | None,
    outage_load_w: float | None,
) -> BackupRange:
    """Reichweite beim aktuellen SOC und bei der Reserve; die Last aus dem Ausfall hat Vorrang."""
    eta = math.sqrt(roundtrip_pct / 100)
    load_w = outage_load_w if outage_load_w is not None else (basis.day_w if basis else None)
    source: LoadSource = "ausfall" if outage_load_w is not None else "tag"
    plausible = load_w is not None and load_w >= MIN_LOAD_W
    night_w = basis.night_w if basis else None
    return BackupRange(
        now_h=_hours(soc_pct, floor_pct, usable_kwh, eta, load_w),
        reserve_h=_hours(reserve_pct, floor_pct, usable_kwh, eta, load_w),
        load_w=load_w if plausible else None,
        load_source=source if plausible else None,
        night_h=_hours(soc_pct, floor_pct, usable_kwh, eta, night_w),
    )


def hours_text(hours: float | None) -> str:
    """„≈ 7,5 h“ unter 10 h, sonst ganze Stunden; ohne Wert „zu wenig Daten“."""
    if hours is None:
        return "zu wenig Daten"
    tenths = round(hours, 1)
    if 0 < tenths < _TEXT_LIMIT_H:
        return f"≈ {tenths:.1f}".replace(".", ",") + f"{NBSP}h"
    return f"≈ {round(hours):d}{NBSP}h"


class BackupRangeService:
    """Hält die Lastgrundlage: beim Start und täglich 00:20 lokal aus InfluxDB gelesen."""

    def __init__(
        self,
        influx: InfluxReader,
        clock: Clock,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._influx = influx
        self._clock = clock
        self._sleep = sleep
        self._basis: LoadBasis | None = None
        self._computed_at: datetime | None = None
        self._ok = False

    def basis(self) -> LoadBasis | None:
        """Die letzte gute Grundlage, höchstens 48 h alt."""
        if self._basis is None or self._computed_at is None:
            return None
        if self._clock.now() - self._computed_at > MAX_BASIS_AGE:
            return None
        return self._basis

    async def refresh(self) -> None:
        now = self._clock.now()
        today = now.astimezone(LOCAL_TZ).date()
        start = datetime.combine(today - timedelta(days=BASIS_DAYS), time(0), tzinfo=LOCAL_TZ)
        end = datetime.combine(today, time(0), tzinfo=LOCAL_TZ)
        try:
            series = await self._influx.query(ac_out_query(start, end))
        except InfluxQueryError as exc:
            self._ok = False
            log.warning("Last an AC-Out nicht lesbar, die letzte Grundlage bleibt", error=str(exc))
            return
        self._basis = load_basis(slot_loads(series), today)
        self._computed_at = now
        self._ok = True
        log.info(
            "Lastgrundlage für die Reichweite berechnet",
            tag_slots=self._basis.day_slots,
            nacht_slots=self._basis.night_slots,
        )

    async def run(self) -> None:
        """Beim Start und täglich 00:20 lokal; nach einem Fehler nach 10 min erneut."""
        while True:
            try:
                await self.refresh()
            except Exception:  # die Schleife darf nie stehen bleiben
                log.exception("Berechnung der Lastgrundlage fehlgeschlagen")
                self._ok = False
            now = self._clock.now()
            seconds = (next_local(now, lambda t: t.time() == REFRESH_AT) - now).total_seconds()
            await self._sleep(max(seconds if self._ok else min(seconds, RETRY_S), 0.0))
