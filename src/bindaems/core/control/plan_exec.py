"""Gültiger Slot des Akkuplans im core (Plan 3c, Präzisierung 5).

- ``ActiveSlot``: der Slot, der jetzt bindet, mit der Nummer seines Plans
- ``active_slot``: der gültige Slot zur Wanduhr, sonst ``None``
- ``plan_health``: der Eintrag ``plan`` in Health

Der core prüft Gültigkeit und Slot auf der Wanduhr, wie der 15-min-Wächter. Ein Plan, der nicht
gilt, wirkt nicht: Es gibt dann keinen Slot, und der Eigenverbrauch regelt wie ohne Plan. Die
Schicht ist rein: Die Zeit kommt mit dem Aufruf.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from bindaems.shared.plan import BatteryIntent, IntentPlan, ThermalHint
from bindaems.shared.timeutil import ensure_utc


@dataclass(frozen=True)
class ActiveSlot:
    """Der Slot des Plans ``plan_id``, der zur Wanduhr gilt (``start`` ist sein Beginn).

    ``budget_kw``, ``import_ct`` und ``support_soc_pct`` sind die Werte des Slots; ``None`` heißt,
    der Plan nennt sie nicht. ``vehicles`` ist die Ladeleistung je Fahrzeug-Schlüssel in W (Plan 4b,
    Entscheidung 9); ein Fahrzeug, das der Slot nicht nennt, kennt der Plan nicht, und für es gilt
    kein Slot (Ruling R22). ``thermal`` ist der Hinweis des Plans für die Klimaanlage (Plan 6a,
    Entscheidung 7), ``soc_end_pct`` der geplante SOC am Ende des Slots (``None``: nicht genannt).
    """

    plan_id: int
    start: datetime
    intent: BatteryIntent
    budget_kw: float | None
    import_ct: float | None
    support_soc_pct: float | None
    vehicles: Mapping[str, int] = field(default_factory=dict)
    thermal: ThermalHint = "neutral"
    soc_end_pct: float | None = None


def active_slot(plan: IntentPlan | None, wall_now: datetime) -> ActiveSlot | None:
    """Der Slot, der zur Wanduhr ``wall_now`` bindet.

    Ein Slot gilt nur, wenn der Plan gilt (``created_at ≤ wall_now < valid_until``) und seine
    Viertelstunde den Zeitpunkt enthält. Ohne Plan, vor ``created_at`` (Uhrabweichung), ab
    ``valid_until`` und ohne Slot für diese Viertelstunde ist es ``None``.
    """
    if plan is None or not plan.valid_at(wall_now):
        return None
    slot = plan.slot_at(wall_now)
    if slot is None:
        return None
    return ActiveSlot(
        plan_id=plan.id,
        start=slot.start,
        intent=slot.intent,
        budget_kw=slot.budget_kw,
        import_ct=slot.import_ct,
        support_soc_pct=slot.support_soc_pct,
        vehicles=dict(slot.vehicles),
        thermal=slot.thermal,
        soc_end_pct=slot.soc_end_pct,
    )


def plan_health(
    plan: IntentPlan | None, active: ActiveSlot | None, wall_now: datetime
) -> dict[str, Any]:
    """Der Eintrag ``plan`` in Health: ``id``, ``state``, ``valid_until`` und, solange ein Slot
    bindet, ``slot`` (Beginn, ISO in UTC), ``intent`` (die Art), ``grid_charge_w``, ``budget_kw``,
    ``support_soc_pct`` und ``price_ct`` (``import_ct`` des Slots); was fehlt, ist ``None``.

    ``state`` ist ``none`` ohne Plan, ``valid`` mit ``active`` oder solange der Plan zur Wanduhr
    ``wall_now`` gilt, und sonst ``expired``. Ruht die Regelung, bindet kein Slot (``active`` ist
    ``None``), und der Stand folgt der Gültigkeit allein; die Felder des Slots sind dann ``None``.
    Auch ein Plan, der wegen einer Uhrabweichung (``wall_now`` vor ``created_at``) noch nicht gilt,
    steht auf ``expired``; er wirkt dann ebenso wenig wie ein abgelaufener.
    """
    if plan is None:
        state = "none"
    elif active is not None or plan.valid_at(wall_now):
        state = "valid"
    else:
        state = "expired"
    return {
        "id": None if plan is None else plan.id,
        "state": state,
        "valid_until": None if plan is None else ensure_utc(plan.valid_until).isoformat(),
        "slot": None if active is None else ensure_utc(active.start).isoformat(),
        "intent": None if active is None else active.intent.kind.value,
        "grid_charge_w": None if active is None else active.intent.grid_charge_w,
        "budget_kw": None if active is None else active.budget_kw,
        "support_soc_pct": None if active is None else active.support_soc_pct,
        "price_ct": None if active is None else active.import_ct,
    }
