"""Einstandspreis des Akkus je Viertelstunde (Spec 11.4, Plan 4a, Entscheidung 4).

PV-Strom ist kostenlos (0 ct), Netzladung zählt mit dem Bezugspreis des Slots; der Mittelwert über
die bepreiste Energie im Inhalt ist damit der Netzanteil zum Bezugspreis. Eine Entladung kostet den
Mittelwert zu Beginn des Slots plus Verschleiß. Energie aus einer Netzladung ohne Bezugspreis führt
die app als Anteil ohne Preis im Inhalt mit; solange er mindestens 1 % des Inhalts ausmacht, ist der
Einstandspreis unbekannt (``None``), bis der Preis nachgetragen ist oder der Anteil darunter fällt.
Die Werte stehen in ``ledger_cost`` und werden ab dem frühesten geänderten Slot neu gerechnet
(``mark_dirty``); ``catch_up`` läuft in einem Thread.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import Engine, delete, desc, func, insert, or_, select

from bindaems.app.db.schema import ledger_cost, ledger_slot
from bindaems.app.ledger.service import LedgerService, LedgerSlot
from bindaems.app.settings.service import SettingsService, SettingsVersion
from bindaems.shared.timeutil import SLOT, ensure_utc

log = structlog.get_logger(__name__)

BLOCK = timedelta(days=7)
EMPTY_KWH = 1e-9  # darunter gilt der Akku als leer (Rundung der Summen)
UNPRICED_SHARE = 0.01  # ab diesem Anteil ohne Bezugspreis ist der Einstandspreis unbekannt
PV_CHARGE = "pv>battery"
GRID_CHARGE = "grid>battery"
DISCHARGE_PREFIX = "battery>"


@dataclass(frozen=True)
class SlotCost:
    slot_start: datetime
    stock_kwh: float  # Inhalt am Ende des Slots
    # Einstandspreis der bepreisten Energie im Inhalt; leer ohne bepreiste Energie
    avg_ct: float | None
    # Kosten einer kWh, die in diesem Slot entladen wird (Mittelwert + Verschleiß); leer, solange
    # der Einstandspreis unbekannt ist (``UNPRICED_SHARE``)
    discharge_ct: float | None
    # Anteil des Inhalts aus Netzladung ohne Bezugspreis (kWh, am Ende des Slots)
    unpriced_kwh: float = 0.0


StartState = tuple[float, float | None, float]  # (Inhalt kWh, Mittelwert ct, davon ohne Preis kWh)


def _discharge_ct(stock: float, avg: float | None, unpriced: float, wear_ct: float) -> float | None:
    if stock <= EMPTY_KWH:
        return wear_ct  # leer und bekannt: nur Verschleiß
    if unpriced >= UNPRICED_SHARE * stock or avg is None:
        return None
    return avg + wear_ct


def cost_basis(
    slots: Sequence[LedgerSlot],
    start: StartState,
    *,
    wear_ct: float,
    usable_kwh: float,
) -> list[SlotCost]:
    """Rechnet die Slots chronologisch; ``start`` ist der Zustand davor (``StartState``).

    Netzladung ohne Bezugspreis geht in ``unpriced_kwh`` ein, nicht in den Mittelwert. Entladung
    und Kappung auf ``usable_kwh`` nehmen sie anteilig mit heraus, neue bepreiste oder PV-Ladung
    verdünnt sie. Solange sie mindestens ``UNPRICED_SHARE`` des Inhalts ausmacht, ist der
    Einstandspreis unbekannt; darunter gilt der Mittelwert der bepreisten Energie.
    """
    stock, avg, unpriced = start
    result: list[SlotCost] = []
    for slot in slots:
        discharge_ct = _discharge_ct(stock, avg, unpriced, wear_ct)
        discharged = math.fsum(
            wh for name, wh in slot.flows_wh.items() if name.startswith(DISCHARGE_PREFIX)
        )
        if discharged > 0 and stock > 0:
            remaining = max(0.0, stock - discharged / 1000)
            unpriced *= remaining / stock
            stock = remaining
        if stock <= EMPTY_KWH:
            stock, avg, unpriced = 0.0, None, 0.0
        pv_kwh = slot.flows_wh.get(PV_CHARGE, 0.0) / 1000
        grid_kwh = slot.flows_wh.get(GRID_CHARGE, 0.0) / 1000
        added = pv_kwh + grid_kwh
        if added > 0:
            priced = max(0.0, stock - unpriced)
            parts = [(avg, priced)] if avg is not None and priced > 0 else []
            parts.append((0.0, pv_kwh))
            if slot.import_price_ct is None:
                unpriced += grid_kwh
            else:
                parts.append((slot.import_price_ct, grid_kwh))
            weight = math.fsum(kwh for _, kwh in parts)
            if weight > 0:
                avg = math.fsum(ct * kwh for ct, kwh in parts) / weight
            total = stock + added
            if total > usable_kwh:
                unpriced *= usable_kwh / total  # gekappt: anteilig, der Mittelwert bleibt
            stock = min(total, usable_kwh)
        result.append(SlotCost(slot.slot_start, stock, avg, discharge_ct, unpriced))
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
        self._dirty_listeners: list[Callable[[], None]] = []
        self._closing = threading.Event()  # gesetzt von ``close``: keine neue Nachführung
        settings.on_change(self._settings_changed)
        self._mark_stale()

    def on_dirty(self, listener: Callable[[], None]) -> None:
        """``listener`` läuft nach jedem ``mark_dirty``, auch aus einem Worker-Thread (etwa beim
        Neubewerten oder einer Änderung des Verschleißes); er weckt die Nachführung."""
        self._dirty_listeners.append(listener)

    def mark_dirty(self, start: datetime | None) -> None:
        """Rechnet ab ``start`` neu; ``None`` heißt alles."""
        with self._dirty_lock:
            if start is None:
                self._dirty_all = True
            else:
                start = ensure_utc(start)
                if self._dirty_from is None or start < self._dirty_from:
                    self._dirty_from = start
        for listener in self._dirty_listeners:
            try:
                listener()
            except Exception:
                log.exception("Nachführung des Einstandspreises nicht geweckt")

    def close(self, timeout_s: float) -> bool:
        """Beim Herunterfahren: Eine laufende Nachführung hört nach ihrem Block auf (sieben Tage,
        Millisekunden), eine neue beginnt nicht mehr. Wartet höchstens ``timeout_s``; ``True``,
        wenn keine mehr läuft. Was dabei ungerechnet bleibt, findet der nächste Start
        (``_mark_stale``)."""
        self._closing.set()
        if not self._lock.acquire(timeout=timeout_s):
            return False
        self._lock.release()
        return True

    def catch_up(self) -> int:
        """Rechnet ab dem frühesten markierten Slot; liefert die Zahl der geschriebenen Slots."""
        if self._closing.is_set():
            return 0
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
                    row.slot_start, row.stock_kwh, row.avg_ct, row.discharge_ct, row.unpriced_kwh
                )
                for row in conn.execute(query).all()
            }

    def _settings_changed(self, version: SettingsVersion) -> None:
        wear_ct = version.settings.planning.wear_ct
        with self._dirty_lock:  # Vergleich und Merken ohne fremden Aufruf dazwischen
            changed = wear_ct != self._wear_ct
            self._wear_ct = wear_ct
        if changed:
            self.mark_dirty(None)

    def _mark_stale(self) -> None:
        """Nach dem Start: ab dem frühesten Slot neu rechnen, zu dem keine passende Zeile da ist.

        Eine Zeile passt, wenn sie zum Stand des Slots (``updated_at``), zum Verschleiß und zur
        Kapazität gerechnet ist. So überlebt eine Änderung, die vor einem Absturz oder Neustart
        nicht mehr nachgeführt wurde (die Markierung liegt nur im Speicher)."""
        wear_ct = self._settings.current().settings.planning.wear_ct
        query = (
            select(func.min(ledger_slot.c.slot_start))
            .select_from(
                ledger_slot.outerjoin(
                    ledger_cost, ledger_cost.c.slot_start == ledger_slot.c.slot_start
                )
            )
            .where(
                or_(
                    ledger_cost.c.slot_start.is_(None),
                    ledger_cost.c.slot_updated_at != ledger_slot.c.updated_at,
                    ledger_cost.c.wear_ct != wear_ct,
                    ledger_cost.c.usable_kwh != self._usable_kwh,
                )
            )
        )
        with self._engine.connect() as conn:
            stale = conn.execute(query).scalar()
        if stale is not None:
            self.mark_dirty(stale)

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
        state: StartState = (0.0, None, 0.0)
        cursor = first
        clear_from: datetime | None = None  # ohne vorherige Zeile wird alles ersetzt
        if before is not None and start is not None:
            state = (before.stock_kwh, before.avg_ct, before.unpriced_kwh)
            cursor, clear_from = start, start
        wear_ct = self._settings.current().settings.planning.wear_ct
        end, written, replaced = last + SLOT, 0, False
        while cursor < end and not self._closing.is_set():
            block_end = min(cursor + BLOCK, end)
            slots = self._ledger.slots(cursor, block_end)
            costs = cost_basis(slots, state, wear_ct=wear_ct, usable_kwh=self._usable_kwh)
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
                                "unpriced_kwh": cost.unpriced_kwh,
                                "slot_updated_at": slot.updated_at,
                                "wear_ct": wear_ct,
                                "usable_kwh": self._usable_kwh,
                            }
                            for slot, cost in zip(slots, costs, strict=True)
                        ],
                    )
            if costs:
                last_cost = costs[-1]
                state = (last_cost.stock_kwh, last_cost.avg_ct, last_cost.unpriced_kwh)
                written += len(costs)
            cursor = block_end
        return written
