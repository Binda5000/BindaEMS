"""Preise in SQLite: Archiv der Rohantworten und der gültige Börsenpreis je Slot."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import Engine, Row, and_, case, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from bindaems.app.db.schema import price_raw, price_slot
from bindaems.app.fetch import RawResponse
from bindaems.shared.timeutil import SLOT, Clock, ensure_utc, local_day_slots

Origin = Literal["primary", "fallback"]

_Row = Row[*tuple[Any, ...]]


@dataclass(frozen=True)
class SlotRow:
    slot_start: datetime
    spot_raw_ct: float | None  # Wert von smartENERGY, wie geliefert (nur primary)
    spot_net_ct: float
    reference_ct: float | None
    origin: Origin
    raw_id: int | None


@dataclass(frozen=True)
class StoredPrice:
    slot_start: datetime
    spot_raw_ct: float | None
    spot_net_ct: float
    reference_ct: float | None
    origin: Origin
    raw_id: int | None
    updated_at: datetime


def _origin(value: str) -> Origin:
    return "primary" if value == "primary" else "fallback"


def _stored(row: _Row) -> StoredPrice:
    return StoredPrice(
        slot_start=row.slot_start,
        spot_raw_ct=row.spot_raw_ct,
        spot_net_ct=row.spot_net_ct,
        reference_ct=row.reference_ct,
        origin=_origin(row.origin),
        raw_id=row.raw_id,
        updated_at=row.updated_at,
    )


class PriceStore:
    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock

    def archive(self, raw: RawResponse) -> int:
        """Speichert die Rohantwort; dieselbe Antwort wie zuletzt nur mit neuer Abrufzeit."""
        digest = hashlib.sha256(raw.body.encode()).hexdigest()
        with self._engine.begin() as conn:
            last = conn.execute(
                select(price_raw.c.id, price_raw.c.sha256, price_raw.c.status)
                .where(price_raw.c.source == raw.source)
                .order_by(price_raw.c.id.desc())
                .limit(1)
            ).first()
            if last is not None and (last.sha256, last.status) == (digest, raw.status):
                conn.execute(
                    update(price_raw)
                    .where(price_raw.c.id == last.id)
                    .values(last_fetched_at=raw.fetched_at)
                )
                return int(last.id)
            raw_id: int = conn.execute(
                insert(price_raw)
                .values(
                    source=raw.source,
                    url=raw.url,
                    fetched_at=raw.fetched_at,
                    last_fetched_at=raw.fetched_at,
                    status=raw.status,
                    sha256=digest,
                    body=raw.body,
                )
                .returning(price_raw.c.id)
            ).scalar_one()
        return raw_id

    def save(self, rows: Sequence[SlotRow]) -> int:
        """Upsert je Slot; ``primary`` wird nur von ``primary`` ersetzt."""
        if not rows:
            return 0
        now = self._clock.now()
        statement = sqlite_insert(price_slot)
        new = statement.excluded
        keep = and_(price_slot.c.origin == "primary", new.origin != "primary")

        def kept(column: str) -> Any:
            return case((keep, price_slot.c[column]), else_=new[column])

        statement = statement.on_conflict_do_update(
            index_elements=[price_slot.c.slot_start],
            set_={
                "spot_raw_ct": kept("spot_raw_ct"),
                "spot_net_ct": kept("spot_net_ct"),
                "origin": kept("origin"),
                "raw_id": kept("raw_id"),
                "reference_ct": func.coalesce(new.reference_ct, price_slot.c.reference_ct),
                "updated_at": new.updated_at,
            },
        )
        params = [
            {
                "slot_start": ensure_utc(row.slot_start),
                "spot_raw_ct": row.spot_raw_ct,
                "spot_net_ct": row.spot_net_ct,
                "reference_ct": row.reference_ct,
                "origin": row.origin,
                "raw_id": row.raw_id,
                "updated_at": now,
            }
            for row in rows
        ]
        with self._engine.begin() as conn:
            conn.execute(statement, params)
        return len(rows)

    def get(self, start: datetime, end: datetime) -> list[StoredPrice]:
        """Gespeicherte Slots in ``[start, end)``, zeitlich sortiert."""
        query = (
            select(price_slot)
            .where(price_slot.c.slot_start >= ensure_utc(start))
            .where(price_slot.c.slot_start < ensure_utc(end))
            .order_by(price_slot.c.slot_start)
        )
        with self._engine.connect() as conn:
            return [_stored(row) for row in conn.execute(query).all()]

    def complete_day(self, day: date, origin: str | None = None) -> bool:
        """Ob alle Slots des lokalen Tages einen Preis haben (optional mit dieser Herkunft)."""
        slots = local_day_slots(day)
        query = (
            select(func.count())
            .select_from(price_slot)
            .where(price_slot.c.slot_start >= slots[0])
            .where(price_slot.c.slot_start < slots[-1] + SLOT)
        )
        if origin is not None:
            query = query.where(price_slot.c.origin == origin)
        with self._engine.connect() as conn:
            count = conn.execute(query).scalar_one()
        return int(count) == len(slots)
