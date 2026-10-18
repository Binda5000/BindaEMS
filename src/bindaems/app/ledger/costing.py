"""Einstandspreis des Akkus je Viertelstunde (Spec 11.4, Plan 4a, Entscheidung 4).

PV-Strom ist kostenlos (0 ct), Netzladung zählt mit dem Bezugspreis des Slots; der Mittelwert über
den Inhalt ist damit der Netzanteil zum Bezugspreis. Eine Entladung kostet den Mittelwert zu
Beginn des Slots plus Verschleiß. Solange Energie aus einer Netzladung ohne Bezugspreis im Akku
ist, ist der Einstandspreis unbekannt (``None``), bis der Preis nachgetragen oder der Akku leer
ist. Die Werte stehen in ``ledger_cost`` und werden ab dem frühesten geänderten Slot neu
gerechnet (``mark_dirty``); ``catch_up`` läuft in einem Thread.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import Engine, delete, desc, func, insert, select

from bindaems.app.db.schema import ledger_cost, ledger_slot
from bindaems.app.ledger.service import LedgerService, LedgerSlot
from bindaems.app.settings.service import SettingsService, SettingsVersion
from bindaems.shared.timeutil import SLOT, ensure_utc

log = structlog.get_logger(__name__)

BLOCK = timedelta(days=7)
EMPTY_KWH = 1e-9  # darunter gilt der Akku als leer (Rundung der Summen)
PV_CHARGE = "pv>battery"
GRID_CHARGE = "grid>battery"
DISCHARGE_PREFIX = "battery>"


@dataclass(frozen=True)
class SlotCost:
    slot_start: datetime
    stock_kwh: float  # Inhalt am Ende des Slots
    # Einstandspreis des Inhalts; leer ohne Inhalt, und solange Netzladung ohne Preis darin ist
    avg_ct: float | None
    # Kosten einer kWh, die in diesem Slot entladen wird (Mittelwert + Verschleiß); leer, solange
    # der Einstandspreis unbekannt ist
    discharge_ct: float | None


def cost_basis(
    slots: Sequence[LedgerSlot],
    start: tuple[float, float | None],
    *,
    wear_ct: float,
    usable_kwh: float,
) -> list[SlotCost]:
    """Rechnet die Slots chronologisch; ``start`` ist ``(Inhalt kWh, Mittelwert ct)`` davor.

    Ein Inhalt über ``EMPTY_KWH`` ohne Mittelwert ist unbekannt: Er stammt (auch) aus einer
    Netzladung ohne Bezugspreis. Er bleibt unbekannt, bis der Akku leer ist.
    """
    stock, avg = start
    result: list[SlotCost] = []
    for slot in slots:
        discharge_ct: float | None
        if stock <= EMPTY_KWH:
            discharge_ct = wear_ct  # leer und bekannt: nur Verschleiß
        else:
            discharge_ct = avg + wear_ct if avg is not None else None
        discharged = math.fsum(
            wh for name, wh in slot.flows_wh.items() if name.startswith(DISCHARGE_PREFIX)
        )
        stock = max(0.0, stock - discharged / 1000)
        if stock <= EMPTY_KWH:
            stock, avg = 0.0, None
        pv_kwh = slot.flows_wh.get(PV_CHARGE, 0.0) / 1000
        grid_kwh = slot.flows_wh.get(GRID_CHARGE, 0.0) / 1000
        added = pv_kwh + grid_kwh
        if added > 0:
            unknown = (stock > 0 and avg is None) or (grid_kwh > 0 and slot.import_price_ct is None)
            if unknown:
                avg = None
            else:
                price = slot.import_price_ct if slot.import_price_ct is not None else 0.0
                parts = [(avg, stock)] if avg is not None and stock > 0 else []
                parts += [(0.0, pv_kwh), (price, grid_kwh)]  # ohne Netzladung zählt price nicht
                avg = math.fsum(ct * kwh for ct, kwh in parts) / math.fsum(kwh for _, kwh in parts)
        stock = min(stock + added, usable_kwh)
        result.append(SlotCost(slot.slot_start, stock, avg, discharge_ct))
    return result


class LedgerCosting:
    def __init__(
        self,
        engine: Engine,
        ledger: LedgerService,
        settings: SettingsService,
        usable_kwh: float,
    ) -> None:
        self._engine = engine
        self._ledger = ledger
        self._settings = settings
        self._usable_kwh = usable_kwh
        self._lock = threading.Lock()  # eine Nachführung zur Zeit
        self._dirty_lock = threading.Lock()  # kurz; ``mark_dirty`` wartet nie auf eine Nachführung
        self._dirty_all = False
        self._dirty_from: datetime | None = None
        self._wear_ct = settings.current().settings.planning.wear_ct
        settings.on_change(self._settings_changed)
        self._mark_unfinished()

    def mark_dirty(self, start: datetime | None) -> None:
        """Rechnet ab ``start`` neu; ``None`` heißt alles."""
        with self._dirty_lock:
            if start is None:
                self._dirty_all = True
                return
            start = ensure_utc(start)
            if self._dirty_from is None or start < self._dirty_from:
                self._dirty_from = start

    def catch_up(self) -> int:
        """Rechnet ab dem frühesten markierten Slot; liefert die Zahl der geschriebenen Slots."""
        with self._lock:
            with self._dirty_lock:
                dirty_all, dirty_from = self._dirty_all, self._dirty_from
                self._dirty_all, self._dirty_from = False, None
            if not dirty_all and dirty_from is None:
                return 0
            start = None if dirty_all else dirty_from
            try:
                return self._recompute(start)
            except BaseException:
                self.mark_dirty(start)  # beim nächsten Mal noch einmal
                raise

    def costs(self, start: datetime, end: datetime) -> dict[datetime, SlotCost]:
        self.catch_up()
        query = (
            select(ledger_cost)
            .where(ledger_cost.c.slot_start >= ensure_utc(start))
            .where(ledger_cost.c.slot_start < ensure_utc(end))
            .order_by(ledger_cost.c.slot_start)
        )
        with self._engine.connect() as conn:
            return {
                row.slot_start: SlotCost(
                    row.slot_start, row.stock_kwh, row.avg_ct, row.discharge_ct
                )
                for row in conn.execute(query).all()
            }

    def _settings_changed(self, version: SettingsVersion) -> None:
        wear_ct = version.settings.planning.wear_ct
        if wear_ct != self._wear_ct:
            self._wear_ct = wear_ct
            self.mark_dirty(None)

    def _mark_unfinished(self) -> None:
        """Nach dem Start ist Ungerechnetes (noch nichts, oder neuer als die letzte Zeile) offen."""
        with self._engine.connect() as conn:
            last_slot = conn.execute(select(func.max(ledger_slot.c.slot_start))).scalar()
            last_cost = conn.execute(select(func.max(ledger_cost.c.slot_start))).scalar()
        if last_slot is None:
            return
        if last_cost is None:
            self.mark_dirty(None)
        elif last_cost < last_slot:
            self.mark_dirty(last_cost + SLOT)

    def _recompute(self, start: datetime | None) -> int:
        with self._engine.connect() as conn:
            last = conn.execute(select(func.max(ledger_slot.c.slot_start))).scalar()
            first = conn.execute(select(func.min(ledger_slot.c.slot_start))).scalar()
            before = (
                conn.execute(
                    select(ledger_cost)
                    .where(ledger_cost.c.slot_start < start)
                    .order_by(desc(ledger_cost.c.slot_start))
                    .limit(1)
                ).first()
                if start is not None
                else None
            )
        if last is None or first is None:
            return 0
        state: tuple[float, float | None] = (0.0, None)
        cursor = first
        clear_from: datetime | None = None  # ohne vorherige Zeile wird alles ersetzt
        if before is not None and start is not None:
            state, cursor, clear_from = (before.stock_kwh, before.avg_ct), start, start
        wear_ct = self._settings.current().settings.planning.wear_ct
        end, written, replaced = last + SLOT, 0, False
        while cursor < end:
            block_end = min(cursor + BLOCK, end)
            costs = cost_basis(
                self._ledger.slots(cursor, block_end),
                state,
                wear_ct=wear_ct,
                usable_kwh=self._usable_kwh,
            )
            with self._engine.begin() as conn:
                if not replaced:
                    stale = delete(ledger_cost)
                    if clear_from is not None:
                        stale = stale.where(ledger_cost.c.slot_start >= clear_from)
                    conn.execute(stale)
                    replaced = True
                if costs:
                    conn.execute(
                        insert(ledger_cost),
                        [
                            {
                                "slot_start": cost.slot_start,
                                "stock_kwh": cost.stock_kwh,
                                "avg_ct": cost.avg_ct,
                                "discharge_ct": cost.discharge_ct,
                            }
                            for cost in costs
                        ],
                    )
            if costs:
                state = (costs[-1].stock_kwh, costs[-1].avg_ct)
                written += len(costs)
            cursor = block_end
        return written
