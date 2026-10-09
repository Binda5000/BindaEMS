"""Stammdaten der Verbraucher in SQLite; jede Änderung landet im Änderungsprotokoll."""

from __future__ import annotations

import builtins
import re
import threading
from dataclasses import asdict, dataclass
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from sqlalchemy import Connection, Engine, Row, delete, insert, select, update

from bindaems.app.audit import AuditLog, Source
from bindaems.app.db.schema import consumer_table
from bindaems.shared.timeutil import Clock

CORE_REF_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*\.power_w$")
HA_REF_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
MAX_ID = 2**63 - 1  # größte Ganzzahl in SQLite

_Row = Row[*tuple[Any, ...]]

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
DbId = Annotated[int, Field(ge=1, le=MAX_ID)]


class ConsumerInput(BaseModel):
    """Eingabe beim Anlegen und Ändern (immer vollständig)."""

    model_config = ConfigDict(extra="forbid")

    name: Name
    group: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None = None
    parent_id: DbId | None = None
    color: Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
    source_kind: Literal["core", "ha"]
    power_ref: Annotated[str, Field(max_length=200)]
    power_unit: Literal["W", "kW"] | None = None
    sort: Annotated[int, Field(ge=-1_000_000, le=1_000_000)] = 0

    @field_validator("group")
    @classmethod
    def _empty_group_is_none(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def _source(self) -> Self:
        if self.source_kind == "core":
            if not CORE_REF_RE.fullmatch(self.power_ref):
                raise ValueError(
                    "core-Signal muss auf .power_w enden, z. B. load.obergeschoss.power_w"
                )
            if self.power_unit is not None:
                raise ValueError("core-Signale haben keine Einheit (immer W)")
        else:
            if not HA_REF_RE.fullmatch(self.power_ref):
                raise ValueError("HA-Entität als domain.objekt angeben, z. B. sensor.kueche")
            if self.power_unit is None:
                raise ValueError("Für HA-Entitäten die Einheit angeben (W oder kW)")
        return self


@dataclass(frozen=True)
class Consumer:
    id: int
    name: str
    group: str | None
    parent_id: int | None
    color: str
    source_kind: str
    power_ref: str
    power_unit: str | None
    sort: int


class ConsumerError(Exception):
    """Abgelehnte Änderung; ``status`` ist der passende HTTP-Status."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def _consumer(row: _Row) -> Consumer:
    return Consumer(
        id=row.id,
        name=row.name,
        group=row.group_name,
        parent_id=row.parent_id,
        color=row.color,
        source_kind=row.source_kind,
        power_ref=row.power_ref,
        power_unit=row.power_unit,
        sort=row.sort,
    )


def _fields(data: ConsumerInput) -> dict[str, Any]:
    """Felder von ``Consumer`` außer ``id``."""
    return data.model_dump()


def _columns(data: ConsumerInput) -> dict[str, Any]:
    columns = _fields(data)
    columns["group_name"] = columns.pop("group")  # GROUP ist ein SQL-Schlüsselwort
    return columns


class ConsumerService:
    def __init__(self, engine: Engine, clock: Clock, audit: AuditLog, *, ha_enabled: bool) -> None:
        self._engine = engine
        self._clock = clock
        self._audit = audit
        self._ha_enabled = ha_enabled
        self._lock = threading.Lock()  # Prüfen und Schreiben ohne fremde Änderung dazwischen

    def list(self) -> builtins.list[Consumer]:
        """Alle Verbraucher nach ``sort`` und Name."""
        with self._engine.connect() as conn:
            rows = conn.execute(select(consumer_table)).all()
        consumers = [_consumer(row) for row in rows]
        return sorted(consumers, key=lambda c: (c.sort, c.name.casefold(), c.id))

    def get(self, consumer_id: int) -> Consumer:
        with self._engine.connect() as conn:
            return _consumer(self._get(conn, consumer_id))

    def create(self, data: ConsumerInput, *, actor: str, source: Source) -> Consumer:
        self._check_source(data)
        now = self._clock.now()
        with self._lock, self._engine.begin() as conn:
            if data.parent_id is not None:
                self._get_parent(conn, data.parent_id)
            consumer_id: int = conn.execute(
                insert(consumer_table)
                .values(**_columns(data), created_at=now, updated_at=now)
                .returning(consumer_table.c.id)
            ).scalar_one()
            consumer = Consumer(id=consumer_id, **_fields(data))
            self._audit.record(
                actor, source, "consumer.create", data.name, _fields(data), conn=conn
            )
        return consumer

    def update(
        self, consumer_id: int, data: ConsumerInput, *, actor: str, source: Source
    ) -> Consumer:
        self._check_source(data)
        with self._lock, self._engine.begin() as conn:
            old = _consumer(self._get(conn, consumer_id))
            if data.parent_id is not None:
                self._get_parent(conn, data.parent_id)
                if consumer_id in self._ancestors(conn, data.parent_id):
                    raise ConsumerError("Ein Verbraucher kann nicht unter sich selbst hängen", 422)
            conn.execute(
                update(consumer_table)
                .where(consumer_table.c.id == consumer_id)
                .values(**_columns(data), updated_at=self._clock.now())
            )
            consumer = Consumer(id=consumer_id, **_fields(data))
            before, after = asdict(old), asdict(consumer)
            changes = {
                key: {"old": before[key], "new": value}
                for key, value in after.items()
                if before[key] != value
            }
            self._audit.record(
                actor, source, "consumer.update", data.name, {"changes": changes}, conn=conn
            )
        return consumer

    def delete(self, consumer_id: int, *, actor: str, source: Source) -> None:
        with self._lock, self._engine.begin() as conn:
            old = _consumer(self._get(conn, consumer_id))
            child = conn.execute(
                select(consumer_table.c.id).where(consumer_table.c.parent_id == consumer_id)
            ).first()
            if child is not None:
                raise ConsumerError("Verbraucher hat Unterverbraucher", 409)
            conn.execute(delete(consumer_table).where(consumer_table.c.id == consumer_id))
            self._audit.record(actor, source, "consumer.delete", old.name, conn=conn)

    def _check_source(self, data: ConsumerInput) -> None:
        if data.source_kind == "ha" and not self._ha_enabled:
            raise ConsumerError("HA-Datenbank (influxdb.ha_database) ist nicht konfiguriert", 422)

    @staticmethod
    def _get(conn: Connection, consumer_id: int) -> _Row:
        row = conn.execute(select(consumer_table).where(consumer_table.c.id == consumer_id)).first()
        if row is None:
            raise ConsumerError("Verbraucher nicht gefunden", 404)
        return row

    @staticmethod
    def _get_parent(conn: Connection, parent_id: int) -> None:
        found = conn.execute(
            select(consumer_table.c.id).where(consumer_table.c.id == parent_id)
        ).first()
        if found is None:
            raise ConsumerError(f"Elternelement {parent_id} existiert nicht", 422)

    @staticmethod
    def _ancestors(conn: Connection, start: int) -> set[int]:
        """``start`` und alle Elternelemente darüber."""
        rows = conn.execute(select(consumer_table.c.id, consumer_table.c.parent_id)).all()
        parents = {row.id: row.parent_id for row in rows}
        seen: set[int] = set()
        node: int | None = start
        while node is not None and node not in seen:
            seen.add(node)
            node = parents.get(node)
        return seen
