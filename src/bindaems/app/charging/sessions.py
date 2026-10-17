"""Energie und Kosten je Ladesession und je Auto (Plan 4a, Entscheidung 5, Präzisierungen 7, 12).

Die Abrechnung kennt die Flüsse ``pv>wb:<id>``, ``battery>wb:<id>`` und ``grid>wb:<id>`` je
Viertelstunde. Jeder Slot gehört an einer Wallbox der Session mit der größten Überdeckung (bei
Gleichstand der späteren); Energie ohne Session zählt als „Unbekannt“. Eine offene Session endet
für die Zuordnung bei „jetzt“.

Kosten: PV zählt 0 ct, Akku mit dem Einstandspreis der Entladung (``SlotCost.discharge_ct``), Netz
mit dem Bezugspreis des Slots. Fehlt der Bezugspreis bei einem Netzanteil über 0, sind die Kosten
unbekannt (``None``), nie 0.

``SessionService`` liest und rechnet blockierend (``LedgerCosting.costs`` holt zuerst nach):
aus einem Thread bzw. einem synchronen Route-Handler aufrufen.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Final

from bindaems.app.charging.tracker import ChargingTracker, StoredSession
from bindaems.app.ha.entities import wallbox_type_label
from bindaems.app.ledger.costing import LedgerCosting, SlotCost
from bindaems.app.ledger.service import WALLBOX_PREFIX, LedgerService, LedgerSlot
from bindaems.shared.config import Config
from bindaems.shared.timeutil import SLOT, Clock, ensure_utc, local_day_slots, slot_start

GUEST: Final = "guest"
UNKNOWN: Final = "unknown"
GUEST_NAME: Final = "Gast"
UNKNOWN_NAME: Final = "Unbekannt"

# Präfixe der Flüsse in eine Wallbox und die Stelle in (PV, Akku, Netz)
_SOURCES: Final = (
    ("pv>" + WALLBOX_PREFIX, 0),
    ("battery>" + WALLBOX_PREFIX, 1),
    ("grid>" + WALLBOX_PREFIX, 2),
)


@dataclass(frozen=True)
class FlowSplit:
    """Energie in kWh nach Herkunft und Kosten in Euro (``None``: unbekannt)."""

    kwh: float
    pv_kwh: float
    battery_kwh: float
    grid_kwh: float
    cost_eur: float | None


@dataclass(frozen=True)
class ChargingSession:
    id: int
    wallbox: str
    vehicle: str | None  # Schlüssel des Fahrzeugs, ``guest`` oder ``None`` (unbekannt)
    start: datetime
    end: datetime | None  # ``None``: läuft noch
    split: FlowSplit


@dataclass
class _Sum:
    """Summe mehrerer Slots; die Kosten in ct, solange sie bekannt sind."""

    pv_wh: list[float] = field(default_factory=list)
    battery_wh: list[float] = field(default_factory=list)
    grid_wh: list[float] = field(default_factory=list)
    cost_ct: list[float] = field(default_factory=list)
    cost_known: bool = True

    def add(self, pv_wh: float, battery_wh: float, grid_wh: float, cost_ct: float | None) -> None:
        self.pv_wh.append(pv_wh)
        self.battery_wh.append(battery_wh)
        self.grid_wh.append(grid_wh)
        if cost_ct is None:
            self.cost_known = False
        else:
            self.cost_ct.append(cost_ct)

    def split(self) -> FlowSplit:
        pv, battery, grid = (
            math.fsum(self.pv_wh) / 1000,
            math.fsum(self.battery_wh) / 1000,
            math.fsum(self.grid_wh) / 1000,
        )
        cost = math.fsum(self.cost_ct) / 100 if self.cost_known else None
        return FlowSplit(math.fsum((pv, battery, grid)), pv, battery, grid, cost)


def vehicle_key(session: StoredSession) -> str:
    """Schlüssel für die Auswertung je Auto: Fahrzeug, ``guest`` oder ``unknown``."""
    return session.vehicle or UNKNOWN


def vehicle_name(cfg: Config, key: str) -> str:
    """Anzeigename: Name aus ``config.yaml``, „Gast“, „Unbekannt“; sonst der Schlüssel."""
    if key == GUEST:
        return GUEST_NAME
    if key == UNKNOWN:
        return UNKNOWN_NAME
    vehicle = cfg.vehicles.get(key)
    return vehicle.name if vehicle is not None else key


def wallbox_name(cfg: Config, names: Mapping[str, str], key: str) -> str:
    """Anzeigename wie im UI: eigener Name, sonst die Art der Ladestation, sonst der Schlüssel."""
    if key in names:
        return names[key]
    wallbox = cfg.wallboxes.get(key)
    return wallbox_type_label(wallbox) if wallbox is not None else key


def slot_sessions(
    sessions: Sequence[StoredSession], wallbox: str, slot: datetime, now: datetime
) -> StoredSession | None:
    """Die Session an ``wallbox`` mit der größten Überdeckung des Slots (bei Gleichstand die
    spätere); eine offene Session endet bei ``now``. Ohne Überdeckung ``None``."""
    slot_end = slot + SLOT
    best: StoredSession | None = None
    best_key: tuple[float, datetime, int] | None = None
    for session in sessions:
        if session.wallbox != wallbox:
            continue
        end = session.end if session.end is not None else now
        overlap = (min(end, slot_end) - max(session.start, slot)).total_seconds()
        if overlap <= 0:
            continue
        key = (overlap, session.start, session.id)
        if best_key is None or key > best_key:
            best, best_key = session, key
    return best


def attribute(
    slots: Sequence[LedgerSlot],
    costs: Mapping[datetime, SlotCost],
    sessions: Sequence[StoredSession],
    now: datetime,
) -> tuple[dict[int, FlowSplit], dict[str, FlowSplit]]:
    """Energie und Kosten je Session (Schlüssel ``id``) und je Auto (Schlüssel wie
    ``vehicle_key``). Jede übergebene Session kommt vor, auch ohne Energie."""
    per_session: dict[int, _Sum] = {session.id: _Sum() for session in sessions}
    per_vehicle: dict[str, _Sum] = {vehicle_key(session): _Sum() for session in sessions}
    for slot in slots:
        energy: dict[str, list[float]] = {}  # Wallbox → pv, Akku, Netz in Wh
        for name, wh in slot.flows_wh.items():
            for prefix, index in _SOURCES:
                if wh > 0 and name.startswith(prefix):
                    energy.setdefault(name[len(prefix) :], [0.0, 0.0, 0.0])[index] += wh
        for wallbox, (pv_wh, battery_wh, grid_wh) in energy.items():
            session = slot_sessions(sessions, wallbox, slot.slot_start, now)
            cost = _cost_ct(slot, costs.get(slot.slot_start), battery_wh, grid_wh)
            if session is not None:
                per_session[session.id].add(pv_wh, battery_wh, grid_wh, cost)
            key = vehicle_key(session) if session is not None else UNKNOWN
            per_vehicle.setdefault(key, _Sum()).add(pv_wh, battery_wh, grid_wh, cost)
    return (
        {session_id: total.split() for session_id, total in per_session.items()},
        {key: total.split() for key, total in per_vehicle.items()},
    )


def _cost_ct(
    slot: LedgerSlot, cost: SlotCost | None, battery_wh: float, grid_wh: float
) -> float | None:
    """Kosten der Energie in ct (PV 0 ct); ``None``, wenn ein Preis für einen Anteil fehlt."""
    total = 0.0
    if battery_wh > 0:
        if cost is None:
            return None
        total += battery_wh / 1000 * cost.discharge_ct
    if grid_wh > 0:
        if slot.import_price_ct is None:
            return None
        total += grid_wh / 1000 * slot.import_price_ct
    return total


class SessionService:
    """Ladesessions und Auto-Summen aus Tracker, Abrechnung und Einstandspreis."""

    def __init__(
        self,
        tracker: ChargingTracker,
        ledger: LedgerService,
        costing: LedgerCosting,
        clock: Clock,
    ) -> None:
        self._tracker = tracker
        self._ledger = ledger
        self._costing = costing
        self._clock = clock

    def sessions(
        self,
        first: date,
        last: date,
        vehicle: str | None = None,
        limit: int | None = None,
    ) -> list[ChargingSession]:
        """Die Sessions, die die Tage ``first`` bis ``last`` (Wiener Zeit) überdecken, neueste
        zuerst. ``vehicle`` ist ein Schlüssel (``guest`` und ``unknown`` eingeschlossen),
        ``limit`` kürzt vor dem Rechnen auf die neuesten."""
        start, end = local_day_slots(first)[0], local_day_slots(last)[-1] + SLOT
        found = self._tracker.sessions(start, end)
        if vehicle is not None:
            found = [session for session in found if vehicle_key(session) == vehicle]
        if limit is not None:
            found = found[:limit]
        if not found:
            return []
        now = self._clock.now()
        begin = slot_start(min(session.start for session in found))
        until = max((session.end if session.end is not None else now) for session in found)
        stop = slot_start(until) + SLOT
        # Nachbarn an derselben Wallbox entscheiden mit, wem ein Grenzslot gehört
        neighbours = self._tracker.sessions(begin, stop)
        by_session, _ = self._attribute(begin, stop, neighbours, now)
        return [
            ChargingSession(
                session.id,
                session.wallbox,
                session.vehicle,
                session.start,
                session.end,
                by_session[session.id],
            )
            for session in found
        ]

    def by_vehicle(self, start: datetime, end: datetime) -> dict[str, FlowSplit]:
        """Energie und Kosten je Auto (``guest``, ``unknown``) in ``[start, end)``; die Energie
        ohne Session zählt als ``unknown``."""
        first, last = slot_start(ensure_utc(start)), ensure_utc(end)
        if last <= first:
            return {}
        stop = slot_start(last) if slot_start(last) == last else slot_start(last) + SLOT
        sessions = self._tracker.sessions(first, stop)
        return self._attribute(first, stop, sessions, self._clock.now())[1]

    def _attribute(
        self,
        start: datetime,
        end: datetime,
        sessions: Sequence[StoredSession],
        now: datetime,
    ) -> tuple[dict[int, FlowSplit], dict[str, FlowSplit]]:
        slots = self._ledger.slots(start, end)
        return attribute(slots, self._costing.costs(start, end), sessions, now)
