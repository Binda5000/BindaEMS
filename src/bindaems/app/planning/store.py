"""Speicherung der Pläne und ihrer Eingangsdaten mit Aufbewahrung (Plan 3b, Präzisierung 15).

Nach ``KEEP_INPUTS`` entfallen die Eingangsdaten und der Plan behält nur die Slots vor seiner
Gültigkeit, nach ``KEEP_PLANS`` entfällt der Plan. Die Methoden blockieren; asynchroner Code ruft
sie über ``asyncio.to_thread``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import Engine, delete, insert, select, update

from bindaems.app.db.schema import plan_inputs_table, plan_table
from bindaems.app.planning.inputs import PlanInputs
from bindaems.shared.plan import PLAN_VALID, Plan
from bindaems.shared.timeutil import ensure_utc

KEEP_PLANS: Final = timedelta(days=30)
KEEP_INPUTS: Final = timedelta(days=7)

_REASON_LENGTH: Final = 200


def _plan_of(row_id: int, trimmed: bool, data: dict[str, Any]) -> Plan:
    return Plan.model_validate({**data, "id": row_id, "trimmed": trimmed})


class PlanStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(self, plan: Plan, inputs: PlanInputs) -> Plan:
        """Speichert Plan und Eingangsdaten in einer Transaktion; liefert den Plan mit ``id``."""
        data = plan.model_dump(mode="json", exclude={"id"})
        reason = plan.fallback_reason
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(plan_table).values(
                    created_at=plan.created_at,
                    trigger=plan.trigger,
                    source=plan.source,
                    solver_status=plan.solver_status,
                    fallback_reason=None if reason is None else reason[:_REASON_LENGTH],
                    valid_until=plan.valid_until,
                    settings_version=plan.settings_version,
                    expected_cost_ct=plan.expected_cost_ct,
                    trimmed=plan.trimmed,
                    data=data,
                )
            )
            key = result.inserted_primary_key
            if key is None:
                raise RuntimeError("Plan ohne Schlüssel gespeichert")
            plan_id = int(key[0])
            conn.execute(
                insert(plan_inputs_table).values(
                    plan_id=plan_id, data=inputs.model_dump(mode="json")
                )
            )
        return plan.model_copy(update={"id": plan_id})

    def latest(self) -> Plan | None:
        """Der zuletzt erzeugte Plan, ``None`` ohne Pläne."""
        query = (
            select(plan_table.c.id, plan_table.c.trimmed, plan_table.c.data)
            .order_by(plan_table.c.created_at.desc(), plan_table.c.id.desc())
            .limit(1)
        )
        with self._engine.connect() as conn:
            row = conn.execute(query).first()
        return None if row is None else _plan_of(row.id, row.trimmed, row.data)

    def plans_between(self, start: datetime, end: datetime) -> list[Plan]:
        """Pläne mit ``created_at`` in ``[start − PLAN_VALID, end)``, älteste zuerst.

        Das sind alle Pläne, die für einen Slot im Fenster ``[start, end)`` gegolten haben können
        (Plan 3c, Präzisierung 13); gekürzte Pläne gehören dazu, sie behalten ihre bindenden Slots.
        """
        query = (
            select(plan_table.c.id, plan_table.c.trimmed, plan_table.c.data)
            .where(plan_table.c.created_at >= ensure_utc(start) - PLAN_VALID)
            .where(plan_table.c.created_at < ensure_utc(end))
            .order_by(plan_table.c.created_at, plan_table.c.id)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(query).all()
        return [_plan_of(row.id, row.trimmed, row.data) for row in rows]

    def inputs_of(self, plan_id: int) -> PlanInputs | None:
        """Eingangsdaten des Plans; ``None``, wenn sie fehlen oder schon gelöscht sind."""
        query = select(plan_inputs_table.c.data).where(plan_inputs_table.c.plan_id == plan_id)
        with self._engine.connect() as conn:
            data = conn.execute(query).scalar_one_or_none()
        return None if data is None else PlanInputs.model_validate(data)

    def cleanup(self, now: datetime) -> tuple[int, int]:
        """Räumt auf; liefert die Zahl der gelöschten und der gekürzten Pläne."""
        moment = ensure_utc(now)
        with self._engine.begin() as conn:
            expired = list(
                conn.execute(
                    select(plan_table.c.id).where(plan_table.c.created_at < moment - KEEP_PLANS)
                ).scalars()
            )
            if expired:
                conn.execute(
                    delete(plan_inputs_table).where(plan_inputs_table.c.plan_id.in_(expired))
                )
                conn.execute(delete(plan_table).where(plan_table.c.id.in_(expired)))
            aged = conn.execute(
                select(plan_table.c.id, plan_table.c.valid_until, plan_table.c.data).where(
                    plan_table.c.created_at < moment - KEEP_INPUTS,
                    plan_table.c.trimmed.is_(False),
                )
            ).all()
            for row in aged:
                plan = _plan_of(row.id, False, row.data)
                kept = [slot for slot in plan.slots if slot.start < plan.valid_until]
                trimmed = plan.model_copy(update={"slots": kept or plan.slots[:1], "trimmed": True})
                conn.execute(
                    update(plan_table)
                    .where(plan_table.c.id == row.id)
                    .values(trimmed=True, data=trimmed.model_dump(mode="json", exclude={"id"}))
                )
                conn.execute(delete(plan_inputs_table).where(plan_inputs_table.c.plan_id == row.id))
        return len(expired), len(aged)
