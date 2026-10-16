"""Archiv der Prognosen je Horizont und Ist-Werte aus der Abrechnung (Plan 3a, Präzisierung 7).

Je Slot und Horizontklasse bleibt die Ausgabe, deren Vorlauf der Klassenmitte am nächsten liegt;
so vergleichen die Kennzahlen typische Vorläufe und nicht nur die letzte Ausgabe vor dem Slot.
Reine Datenbankarbeit, synchron (Aufrufer nehmen ``asyncio.to_thread``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Literal, Protocol, cast

from sqlalchemy import Engine, delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from bindaems.app.db.schema import forecast_record
from bindaems.app.ledger.service import LedgerSlot
from bindaems.shared.timeutil import ensure_utc

HorizonClass = Literal["h0_6", "h6_24", "h24"]
Clearness = Literal["clear", "mixed", "overcast"]
Kind = Literal["pv", "load"]

REPRESENTATIVE_LEAD: Mapping[HorizonClass, timedelta] = MappingProxyType(
    {
        "h0_6": timedelta(hours=3),
        "h6_24": timedelta(hours=15),
        "h24": timedelta(hours=30),
    }
)
COMPLETE_S = 855.0  # ab so viel Abdeckung gilt ein Abrechnungs-Slot als vollständig
_WH_TO_W = 4.0  # Wh je Viertelstunde → W


def horizon_class(lead: timedelta) -> HorizonClass | None:
    """Klasse des Vorlaufs ``slot_start − issued_at``; ``None`` für Slots vor der Ausgabe."""
    if lead < timedelta(0):
        return None
    if lead < timedelta(hours=6):
        return "h0_6"
    if lead < timedelta(hours=24):
        return "h6_24"
    return "h24"


@dataclass(frozen=True)
class RecordValue:
    p50_w: float
    raw_w: float
    clearness: Clearness | None = None


@dataclass(frozen=True)
class ForecastRecord:
    kind: Kind
    slot_start: datetime
    horizon: HorizonClass
    issued_at: datetime
    p50_w: float
    raw_w: float
    clearness: Clearness | None


@dataclass(frozen=True)
class Actual:
    pv_w: float
    house_w: float


class SlotSource(Protocol):
    def slots(self, start: datetime, end: datetime) -> list[LedgerSlot]: ...


def actual_from_ledger(slot: LedgerSlot) -> Actual | None:
    """PV und Hauslast in W aus den Flüssen eines vollständigen Slots, sonst ``None``."""
    if slot.covered_s < COMPLETE_S:
        return None
    pv_wh = sum(wh for name, wh in slot.flows_wh.items() if name.startswith("pv>"))
    house_wh = sum(wh for name, wh in slot.flows_wh.items() if name.endswith(">house"))
    return Actual(pv_w=pv_wh * _WH_TO_W, house_w=house_wh * _WH_TO_W)


def actuals(ledger: SlotSource, start: datetime, end: datetime) -> dict[datetime, Actual]:
    """Ist-Werte der vollständigen Abrechnungs-Slots in ``[start, end)``."""
    result: dict[datetime, Actual] = {}
    for slot in ledger.slots(start, end):
        actual = actual_from_ledger(slot)
        if actual is not None:
            result[slot.slot_start] = actual
    return result


def _distance(lead: timedelta, horizon: HorizonClass) -> timedelta:
    return abs(lead - REPRESENTATIVE_LEAD[horizon])


class ForecastArchive:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(
        self, kind: Kind, issued_at: datetime, values: Mapping[datetime, RecordValue]
    ) -> int:
        """Nimmt eine Ausgabe auf; geschrieben wird je Slot und Klasse nur, wenn ihr Vorlauf der
        Klassenmitte näher liegt als der gespeicherte (bei Gleichstand die neuere Ausgabe).
        Gibt die Zahl der geschriebenen Zeilen zurück."""
        issued = ensure_utc(issued_at)
        candidates: list[tuple[datetime, HorizonClass, timedelta, RecordValue]] = []
        for slot, value in values.items():
            start = ensure_utc(slot)
            lead = start - issued
            horizon = horizon_class(lead)
            if horizon is not None:
                candidates.append((start, horizon, lead, value))
        if not candidates:
            return 0
        first = min(c[0] for c in candidates)
        last = max(c[0] for c in candidates)
        with self._engine.begin() as conn:
            existing = {
                (row.slot_start, row.horizon): row.issued_at
                for row in conn.execute(
                    select(
                        forecast_record.c.slot_start,
                        forecast_record.c.horizon,
                        forecast_record.c.issued_at,
                    )
                    .where(forecast_record.c.kind == kind)
                    .where(forecast_record.c.slot_start >= first)
                    .where(forecast_record.c.slot_start <= last)
                )
            }
            params = []
            for start, horizon, lead, value in candidates:
                old_issue = existing.get((start, horizon))
                if old_issue is not None:
                    old = _distance(start - old_issue, horizon)
                    new = _distance(lead, horizon)
                    if new > old or (new == old and issued < old_issue):
                        continue
                params.append(
                    {
                        "kind": kind,
                        "slot_start": start,
                        "horizon": horizon,
                        "issued_at": issued,
                        "p50_w": float(value.p50_w),
                        "raw_w": float(value.raw_w),
                        "clearness": value.clearness,
                    }
                )
            if not params:
                return 0
            statement = sqlite_insert(forecast_record)
            new_row = statement.excluded
            conn.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        forecast_record.c.kind,
                        forecast_record.c.slot_start,
                        forecast_record.c.horizon,
                    ],
                    set_={
                        "issued_at": new_row.issued_at,
                        "p50_w": new_row.p50_w,
                        "raw_w": new_row.raw_w,
                        "clearness": new_row.clearness,
                    },
                ),
                params,
            )
        return len(params)

    def records(
        self, kind: Kind, start: datetime, end: datetime, horizon: HorizonClass | None = None
    ) -> list[ForecastRecord]:
        """Archivierte Prognosen für Slots in ``[start, end)``, nach Slot und Horizont sortiert."""
        query = (
            select(forecast_record)
            .where(forecast_record.c.kind == kind)
            .where(forecast_record.c.slot_start >= ensure_utc(start))
            .where(forecast_record.c.slot_start < ensure_utc(end))
            .order_by(forecast_record.c.slot_start, forecast_record.c.horizon)
        )
        if horizon is not None:
            query = query.where(forecast_record.c.horizon == horizon)
        with self._engine.connect() as conn:
            return [
                ForecastRecord(
                    kind=cast(Kind, row.kind),
                    slot_start=row.slot_start,
                    horizon=cast(HorizonClass, row.horizon),
                    issued_at=row.issued_at,
                    p50_w=row.p50_w,
                    raw_w=row.raw_w,
                    clearness=cast(Clearness | None, row.clearness),
                )
                for row in conn.execute(query)
            ]

    def cleanup(self, before: datetime) -> int:
        """Löscht Einträge mit ``slot_start < before``; gibt die Zahl der gelöschten zurück."""
        with self._engine.begin() as conn:
            result = conn.execute(
                delete(forecast_record).where(forecast_record.c.slot_start < ensure_utc(before))
            )
        return result.rowcount
