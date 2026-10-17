"""„Plan gegen Ist“: je Viertelstunde der Slot des damals gültigen Plans neben der Abrechnung
(Plan 3c, Präzisierung 13).

Reine Rechnung ohne Datenzugriff; die Route liest Pläne und Abrechnung und ruft ``review``.

Leistungen sind Mittelwerte der Viertelstunde in W. Bezugskosten sind auf beiden Seiten
``Bezug in kWh · Bezugspreis`` in ct, nicht ``PlanSlot.cost_ct`` (das enthält Einspeisung und
Leistungspreis und ließe sich nicht mit der Abrechnung vergleichen).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Final

from bindaems.app.ledger.service import LedgerSlot
from bindaems.shared.plan import Plan, PlanSlot
from bindaems.shared.timeutil import SLOT, ensure_utc, local_day_slots

DIGITS: Final = 3
_PER_HOUR: Final = 4  # Wh je Viertelstunde → mittlere W
_WH_PER_KWH: Final = 1000.0
_CT_PER_EUR: Final = 100.0


def in_force(
    plans: Sequence[Plan], starts: Sequence[datetime]
) -> dict[datetime, tuple[Plan, PlanSlot]]:
    """Je Slotbeginn der Plan, der damals galt, mit dem Slot daraus.

    Das ist der neueste Plan mit ``created_at < start + SLOT`` und ``valid_until > start``, der
    den Slot enthält. Slotbeginne ohne solchen Plan fehlen im Ergebnis.
    """
    wanted = {ensure_utc(start) for start in starts}
    chosen: dict[datetime, tuple[Plan, PlanSlot]] = {}
    # älteste zuerst, damit der neuere Plan den älteren überschreibt
    for plan in sorted(plans, key=lambda item: (item.created_at, item.id or 0)):
        for slot in plan.slots:
            start = ensure_utc(slot.start)
            if start in wanted and plan.created_at < start + SLOT and plan.valid_until > start:
                chosen[start] = (plan, slot)
    return chosen


def _planned(plan: Plan, slot: PlanSlot) -> dict[str, Any]:
    return {
        "intent": slot.intent.kind.value,
        "grid_charge_w": slot.charge_grid_w,
        "soc_end_pct": slot.soc_end_pct,
        "import_w": slot.import_w,
        "export_w": slot.export_w,
        "battery_w": slot.charge_pv_w + slot.charge_grid_w - slot.discharge_w,
        "import_cost_ct": slot.import_w / _PER_HOUR / _WH_PER_KWH * slot.import_ct,
        "plan_id": plan.id,
        "source": plan.source,
    }


def _actual(slot: LedgerSlot) -> dict[str, Any]:
    flows = slot.flows_wh
    into = sum(wh for name, wh in flows.items() if name.partition(">")[2] == "battery")
    out = sum(wh for name, wh in flows.items() if name.partition(">")[0] == "battery")
    price = slot.import_price_ct
    return {
        "import_w": slot.import_wh * _PER_HOUR,
        "export_w": slot.export_wh * _PER_HOUR,
        "battery_w": (into - out) * _PER_HOUR,
        "grid_charge_w": flows.get("grid>battery", 0.0) * _PER_HOUR,
        "import_cost_ct": None if price is None else slot.import_wh / _WH_PER_KWH * price,
    }


def _rounded(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: round(value, DIGITS) if isinstance(value, float) else value
        for key, value in values.items()
    }


def _totals(items: Sequence[dict[str, Any]], *, priced: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Summen über ``items``; die Kosten nur über ``priced`` (None, wenn leer)."""

    def kwh(key: str) -> float:
        watts: float = sum(item[key] for item in items)
        return round(watts / _PER_HOUR / _WH_PER_KWH, DIGITS)

    cost = (
        round(sum(item["import_cost_ct"] for item in priced) / _CT_PER_EUR, DIGITS)
        if priced
        else None
    )
    return {
        "import_kwh": kwh("import_w"),
        "export_kwh": kwh("export_w"),
        "grid_charge_kwh": kwh("grid_charge_w"),
        "import_cost_eur": cost,
    }


def review(
    day: date, plans: Sequence[Plan], ledger: Sequence[LedgerSlot], now: datetime
) -> dict[str, Any]:
    """Die vergangenen Viertelstunden des lokalen Tages ``day``: geplant gegen Ist.

    ``planned``/``actual`` sind ``None``, wenn es keinen Plan bzw. keine Abrechnung für den Slot
    gibt. Die Summen zählen nur Slots mit beidem; die Kosten nur Slots, deren Bezugspreis bekannt
    ist, auf beiden Seiten über dieselben Slots (sonst ``None``).
    """
    moment = ensure_utc(now)
    starts = [start for start in local_day_slots(day) if start + SLOT <= moment]
    plan_of = in_force(plans, starts)
    actual_of = {ensure_utc(slot.slot_start): slot for slot in ledger}
    rows: list[dict[str, Any]] = []
    both: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for start in starts:
        found = plan_of.get(start)
        slot = actual_of.get(start)
        planned = _planned(*found) if found is not None else None
        actual = _actual(slot) if slot is not None else None
        if planned is not None and actual is not None:
            both.append((planned, actual))
        rows.append(
            {
                "start": start.isoformat(),
                "planned": None if planned is None else _rounded(planned),
                "actual": None if actual is None else _rounded(actual),
            }
        )
    priced = [pair for pair in both if pair[1]["import_cost_ct"] is not None]
    return {
        "day": day.isoformat(),
        "slots": rows,
        "totals": {
            "planned": _totals([p for p, _ in both], priced=[p for p, _ in priced]),
            "actual": _totals([a for _, a in both], priced=[a for _, a in priced]),
        },
    }
