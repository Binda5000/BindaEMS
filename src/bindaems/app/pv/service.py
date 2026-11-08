"""PV-Anlagen und ihre Strings in SQLite; jede Änderung landet im Änderungsprotokoll."""

from __future__ import annotations

import builtins
import re
import threading
from dataclasses import asdict, dataclass
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import Connection, Engine, Row, delete, insert, select, update

from bindaems.app.audit import AuditLog, Source
from bindaems.app.db.schema import pv_source, pv_source_day
from bindaems.shared.timeutil import Clock

PV_CORE_REF_RE = re.compile(r"^(pv\.[a-z0-9_]+|pv_dc)\.power_w$")
HA_REF_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
MAX_ID = 2**63 - 1  # größte Ganzzahl in SQLite

_Row = Row[*tuple[Any, ...]]

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
DbId = Annotated[int, Field(ge=1, le=MAX_ID)]


class PvSourceInput(BaseModel):
    """Eingabe beim Anlegen und Ändern (immer vollständig)."""

    model_config = ConfigDict(extra="forbid")

    name: Name
    parent_id: DbId | None = None
    color: Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
    source_kind: Literal["core", "ha"]
    power_ref: Annotated[str, Field(max_length=200)]
    power_unit: Literal["W", "kW"] | None = None
    kwp: Annotated[float, Field(ge=0.1, le=1000)] | None = None
    sort: Annotated[int, Field(ge=-1_000_000, le=1_000_000)] = 0

    @model_validator(mode="after")
    def _source(self) -> Self:
        if self.source_kind == "core":
            if not PV_CORE_REF_RE.fullmatch(self.power_ref):
                raise ValueError(
                    "core-Signal muss ein PV-Signal sein, z. B. pv.huawei.power_w, pv_dc.power_w"
                )
            if self.power_unit is not None:
                raise ValueError("core-Signale haben keine Einheit (immer W)")
        else:
            if not HA_REF_RE.fullmatch(self.power_ref):
                raise ValueError("HA-Entität als domain.objekt angeben, z. B. sensor.pv1_leistung")
            if self.power_unit is None:
                raise ValueError("Für HA-Entitäten die Einheit angeben (W oder kW)")
        return self


@dataclass(frozen=True)
class PvSource:
    """Anlage (``parent_id`` leer) oder String (``parent_id`` einer Anlage)."""

    id: int
    name: str
    parent_id: int | None
    color: str
    source_kind: str
    power_ref: str
    power_unit: str | None
    kwp: float | None
    sort: int


class PvSourceError(Exception):
    """Abgelehnte Änderung; ``status`` ist der passende HTTP-Status."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def _source(row: _Row) -> PvSource:
    return PvSource(
        id=row.id,
        name=row.name,
        parent_id=row.parent_id,
        color=row.color,
        source_kind=row.source_kind,
        power_ref=row.power_ref,
        power_unit=row.power_unit,
        kwp=row.kwp,
        sort=row.sort,
    )


class PvSourceService:
    def __init__(self, engine: Engine, clock: Clock, audit: AuditLog, *, ha_enabled: bool) -> None:
        self._engine = engine
        self._clock = clock
        self._audit = audit
        self._ha_enabled = ha_enabled
        self._lock = threading.Lock()  # Prüfen und Schreiben ohne fremde Änderung dazwischen

    def list(self) -> builtins.list[PvSource]:
        """Alle Einträge nach ``sort`` und Name."""
        with self._engine.connect() as conn:
            rows = conn.execute(select(pv_source)).all()
        sources = [_source(row) for row in rows]
        return sorted(sources, key=lambda s: (s.sort, s.name.casefold(), s.id))

    def get(self, source_id: int) -> PvSource:
        with self._engine.connect() as conn:
            return _source(self._get(conn, source_id))

    def create(self, data: PvSourceInput, *, actor: str, source: Source) -> PvSource:
        self._check_source(data)
        now = self._clock.now()
        with self._lock, self._engine.begin() as conn:
            if data.parent_id is not None:
                self._check_parent(conn, data.parent_id)
            source_id: int = conn.execute(
                insert(pv_source)
                .values(**data.model_dump(), created_at=now, updated_at=now)
                .returning(pv_source.c.id)
            ).scalar_one()
            created = PvSource(id=source_id, **data.model_dump())
            self._audit.record(
                actor, source, "pv_source.create", data.name, data.model_dump(), conn=conn
            )
        return created

    def update(
        self, source_id: int, data: PvSourceInput, *, actor: str, source: Source
    ) -> PvSource:
        self._check_source(data)
        with self._lock, self._engine.begin() as conn:
            old = _source(self._get(conn, source_id))
            if data.parent_id is not None:
                if data.parent_id == source_id:
                    raise PvSourceError("Ein Eintrag kann nicht unter sich selbst hängen", 422)
                self._check_parent(conn, data.parent_id)
                has_strings = conn.execute(
                    select(pv_source.c.id).where(pv_source.c.parent_id == source_id)
                ).first()
                if has_strings is not None:
                    raise PvSourceError(
                        "Eine Anlage mit Strings kann nicht selbst ein String werden", 422
                    )
            conn.execute(
                update(pv_source)
                .where(pv_source.c.id == source_id)
                .values(**data.model_dump(), updated_at=self._clock.now())
            )
            updated = PvSource(id=source_id, **data.model_dump())
            before, after = asdict(old), asdict(updated)
            changes = {
                key: {"old": before[key], "new": value}
                for key, value in after.items()
                if before[key] != value
            }
            self._audit.record(
                actor, source, "pv_source.update", data.name, {"changes": changes}, conn=conn
            )
        return updated

    def delete(self, source_id: int, *, actor: str, source: Source) -> None:
        with self._lock, self._engine.begin() as conn:
            old = _source(self._get(conn, source_id))
            child = conn.execute(
                select(pv_source.c.id).where(pv_source.c.parent_id == source_id)
            ).first()
            if child is not None:
                raise PvSourceError("Anlage hat Strings: zuerst die Strings löschen", 409)
            conn.execute(delete(pv_source).where(pv_source.c.id == source_id))
            # SQLite vergibt die Nummer des neuesten Eintrags wieder: keine Tage erben
            conn.execute(delete(pv_source_day).where(pv_source_day.c.source_id == source_id))
            self._audit.record(actor, source, "pv_source.delete", old.name, conn=conn)

    def _check_source(self, data: PvSourceInput) -> None:
        if data.source_kind == "ha" and not self._ha_enabled:
            raise PvSourceError("HA-Datenbank nicht eingerichtet (influxdb.ha_database)", 422)

    @staticmethod
    def _get(conn: Connection, source_id: int) -> _Row:
        row = conn.execute(select(pv_source).where(pv_source.c.id == source_id)).first()
        if row is None:
            raise PvSourceError("PV-Eintrag nicht gefunden", 404)
        return row

    @staticmethod
    def _check_parent(conn: Connection, parent_id: int) -> None:
        row = conn.execute(select(pv_source.c.parent_id).where(pv_source.c.id == parent_id)).first()
        if row is None:
            raise PvSourceError(f"Anlage {parent_id} existiert nicht", 404)
        if row.parent_id is not None:
            raise PvSourceError("Ein String kann keine Strings haben", 422)
