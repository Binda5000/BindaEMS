"""Abrechnung v1 (Spec 11.4, Plan-Präzisierung 8): Energien je Fluss und Viertelstunde aus den
``slot_flows`` des core, Bezugskosten mit dem Preis zum Slotzeitpunkt, Einspeiseerlös beim Lesen
(der OeMAG-Wert steht erst nach Monatsende fest).

Flussnamen des core: ``<quelle>><ziel>`` mit Quelle ``pv``, ``battery``, ``grid`` und Ziel
``house``, ``battery``, ``grid`` oder ``wb:<wallbox>``.
"""

from __future__ import annotations

import asyncio
import math
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import structlog
from sqlalchemy import Engine, Row, func, insert, select, update

from bindaems.app.audit import AuditLog, Source
from bindaems.app.db.schema import ledger_slot
from bindaems.app.history.influx import RP_LONG, RP_RAW, InfluxReader, quote_ident
from bindaems.app.prices.service import PriceService
from bindaems.app.settings.service import SettingsService
from bindaems.app.status import ComponentStatus
from bindaems.app.tariff.calc import feed_in_price
from bindaems.shared.domain import SlotFlows
from bindaems.shared.influx.lineprotocol import Point, PointSink, epoch_ms
from bindaems.shared.settings import RuntimeSettings
from bindaems.shared.timeutil import (
    LOCAL_TZ,
    SLOT,
    Clock,
    ensure_utc,
    local_day_slots,
    slot_start,
)

log = structlog.get_logger(__name__)

Origin = Literal["stream", "influx"]
SLOT_S = SLOT.total_seconds()
FRESH_FOR = timedelta(minutes=30)
COVERAGE_FLOW = "_coverage"
WALLBOX_PREFIX = "wb:"

_Row = Row[*tuple[Any, ...]]
Counters = Mapping[str, tuple[float | None, float | None]]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def parse_slot_flows(data: Mapping[str, Any]) -> SlotFlows:
    """Liest eine ``slot_flows``-Nachricht des core; Fehler als ``ValueError`` (deutsch)."""
    text = data.get("slot_start")
    try:
        start = datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        start = None
    if start is None:
        raise ValueError(f"slot_start ist kein Zeitpunkt: {text!r}")
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError(f"slot_start ohne Zeitzone: {text}")
    start = start.astimezone(UTC)
    if start != slot_start(start):
        raise ValueError(f"slot_start liegt nicht auf einer Viertelstunde: {text}")
    covered = _number(data.get("covered_s"))
    if covered is None or not 0 <= covered <= SLOT_S:
        raise ValueError(f"covered_s liegt außerhalb von 0–900 s: {data.get('covered_s')!r}")
    raw_flows = data.get("flows_wh")
    if not isinstance(raw_flows, Mapping):
        raise ValueError("flows_wh fehlt oder ist kein Objekt")
    flows: dict[str, float] = {}
    for name, value in raw_flows.items():
        wh = _number(value)
        if not isinstance(name, str) or wh is None or wh < 0:
            raise ValueError(f"Fluss {name!r} ist negativ oder keine Zahl: {value!r}")
        flows[name] = wh
    raw_counters = data.get("counters") or {}
    if not isinstance(raw_counters, Mapping):
        raise ValueError("counters ist kein Objekt")
    counters: dict[str, tuple[float | None, float | None]] = {}
    for signal, bounds in raw_counters.items():
        if not isinstance(bounds, list | tuple) or len(bounds) != 2:
            raise ValueError(f"Zähler {signal!r}: [Start, Ende] erwartet")
        values = [None if bound is None else _number(bound) for bound in bounds]
        if any(
            value is None and bound is not None for value, bound in zip(values, bounds, strict=True)
        ):
            raise ValueError(f"Zähler {signal!r}: Werte müssen Zahlen oder null sein")
        counters[str(signal)] = (values[0], values[1])
    return SlotFlows(start, covered, flows, counters)


@dataclass(frozen=True)
class LedgerSlot:
    slot_start: datetime
    covered_s: float
    flows_wh: Mapping[str, float]
    counters: Counters
    import_wh: float
    export_wh: float
    import_price_ct: float | None
    origin: str


@dataclass(frozen=True)
class DaySummary:
    date: date
    slots: int
    expected_slots: int
    coverage: float
    pv_kwh: float
    import_kwh: float
    export_kwh: float
    house_kwh: float
    wallbox_kwh: Mapping[str, float]
    battery_charge_kwh: float
    battery_discharge_kwh: float
    consumption_kwh: float
    counter_kwh: Mapping[str, float]
    cost_eur: float | None
    revenue_eur: float | None
    net_cost_eur: float | None
    autarky: float | None
    self_consumption: float | None
    savings_no_plant_eur: float | None
    savings_same_import_eur: float | None


def _ledger_slot(row: _Row) -> LedgerSlot:
    counters = row.counters or {}
    return LedgerSlot(
        slot_start=row.slot_start,
        covered_s=row.covered_s,
        flows_wh={str(name): float(wh) for name, wh in row.flows_wh.items()},
        counters={str(signal): (bounds[0], bounds[1]) for signal, bounds in counters.items()},
        import_wh=row.import_wh,
        export_wh=row.export_wh,
        import_price_ct=row.import_price_ct,
        origin=row.origin,
    )


def _summary(day: date, slots: Sequence[LedgerSlot], settings: RuntimeSettings) -> DaySummary:
    """Tageswerte; Energien werden in Wh summiert und erst am Ende in kWh umgerechnet."""
    expected = local_day_slots(day)
    totals: dict[str, float] = {}
    for slot in slots:
        for name, wh in slot.flows_wh.items():
            totals[name] = totals.get(name, 0.0) + wh

    def flows_wh(match: Callable[[str, str], bool]) -> float:
        return math.fsum(wh for name, wh in totals.items() if match(*name.partition(">")[::2]))

    wallbox_wh: dict[str, float] = {}
    for name, wh in totals.items():
        target = name.partition(">")[2]
        if target.startswith(WALLBOX_PREFIX):
            key = target.removeprefix(WALLBOX_PREFIX)
            wallbox_wh[key] = wallbox_wh.get(key, 0.0) + wh
    house_wh = flows_wh(lambda _, target: target == "house")
    pv_kwh = flows_wh(lambda source, _: source == "pv") / 1000
    import_kwh = math.fsum(slot.import_wh for slot in slots) / 1000
    export_kwh = math.fsum(slot.export_wh for slot in slots) / 1000
    consumption_kwh = (house_wh + math.fsum(wallbox_wh.values())) / 1000

    counter_kwh: dict[str, float] = {}
    for slot in slots:
        for signal, (first, last) in slot.counters.items():
            if first is not None and last is not None:
                counter_kwh[signal] = counter_kwh.get(signal, 0.0) + (last - first)

    cost_eur: float | None = math.fsum(
        slot.import_wh / 1000 * slot.import_price_ct / 100
        for slot in slots
        if slot.import_wh > 0 and slot.import_price_ct is not None
    )
    if any(slot.import_wh > 0 and slot.import_price_ct is None for slot in slots):
        cost_eur = None  # ein Bezug ohne Preis macht die Tageskosten unbekannt
    feed_in = feed_in_price(expected[0], settings.feed_in)
    revenue_eur = export_kwh * feed_in / 100 if feed_in is not None else None
    net_cost_eur = (
        cost_eur - revenue_eur if cost_eur is not None and revenue_eur is not None else None
    )
    fixed_eur = settings.tariff.fixed_price_gross_ct / 100
    return DaySummary(
        date=day,
        slots=len(slots),
        expected_slots=len(expected),
        coverage=math.fsum(slot.covered_s for slot in slots) / (len(expected) * SLOT_S),
        pv_kwh=pv_kwh,
        import_kwh=import_kwh,
        export_kwh=export_kwh,
        house_kwh=house_wh / 1000,
        wallbox_kwh={name: wh / 1000 for name, wh in sorted(wallbox_wh.items())},
        battery_charge_kwh=flows_wh(lambda _, target: target == "battery") / 1000,
        battery_discharge_kwh=flows_wh(lambda source, _: source == "battery") / 1000,
        consumption_kwh=consumption_kwh,
        counter_kwh=counter_kwh,
        cost_eur=cost_eur,
        revenue_eur=revenue_eur,
        net_cost_eur=net_cost_eur,
        autarky=1 - import_kwh / consumption_kwh if consumption_kwh > 0 else None,
        self_consumption=(pv_kwh - export_kwh) / pv_kwh if pv_kwh > 0 else None,
        savings_no_plant_eur=(
            consumption_kwh * fixed_eur - net_cost_eur if net_cost_eur is not None else None
        ),
        savings_same_import_eur=(
            import_kwh * fixed_eur - revenue_eur - net_cost_eur
            if revenue_eur is not None and net_cost_eur is not None
            else None
        ),
    )


class LedgerService:
    def __init__(
        self,
        engine: Engine,
        clock: Clock,
        prices: PriceService,
        settings: SettingsService,
        audit: AuditLog,
        sink: PointSink | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._prices = prices
        self._settings = settings
        self._audit = audit
        self._sink = sink
        self._lock = threading.Lock()  # Lesen und Schreiben eines Slots ohne fremde Änderung

    def ingest(self, flows: SlotFlows, origin: Origin) -> bool:
        """Speichert den Slot; ein vorhandener wird nur bei größerer Abdeckung ersetzt."""
        start = ensure_utc(flows.slot_start)
        price = self._prices.import_gross_at(start)
        values = {
            "covered_s": flows.covered_s,
            "flows_wh": dict(flows.flows_wh),
            "counters": {signal: list(bounds) for signal, bounds in flows.counters.items()},
            "import_wh": math.fsum(
                wh for name, wh in flows.flows_wh.items() if name.startswith("grid>")
            ),
            "export_wh": math.fsum(
                wh for name, wh in flows.flows_wh.items() if name.endswith(">grid")
            ),
            "import_price_ct": price,
            "origin": origin,
            "updated_at": self._clock.now(),
        }
        with self._lock, self._engine.begin() as conn:
            existing = conn.execute(
                select(ledger_slot.c.covered_s).where(ledger_slot.c.slot_start == start)
            ).first()
            if existing is not None and flows.covered_s <= existing.covered_s:
                return False
            if existing is None:
                conn.execute(insert(ledger_slot).values(slot_start=start, **values))
            else:
                conn.execute(
                    update(ledger_slot).where(ledger_slot.c.slot_start == start).values(**values)
                )
        self._publish(start, flows.flows_wh, price)
        return True

    def _publish(self, start: datetime, flows_wh: Mapping[str, float], price: float | None) -> None:
        """Punkte ``ledger`` je Fluss: kWh und, wo bekannt, Kosten (+) bzw. Erlös (−) in €."""
        if self._sink is None:
            return
        feed_in = feed_in_price(start, self._settings.current().settings.feed_in)
        for name, wh in sorted(flows_wh.items()):
            kwh = wh / 1000
            fields: dict[str, float] = {"kwh": kwh}
            if name.startswith("grid>") and price is not None:
                fields["eur"] = kwh * price / 100
            elif name.endswith(">grid") and feed_in is not None:
                fields["eur"] = -kwh * feed_in / 100
            self._sink.write(Point("ledger", {"flow": name}, fields, start, rp=RP_LONG))

    async def handle_stream_message(self, data: dict[str, Any]) -> None:
        try:
            flows = parse_slot_flows(data)
        except ValueError as exc:
            log.warning("Ungültige slot_flows-Nachricht", error=str(exc))
            return
        await asyncio.to_thread(self.ingest, flows, "stream")

    async def backfill(self, reader: InfluxReader, start: datetime, end: datetime) -> int:
        """Trägt Slots aus den Flüssen in InfluxDB nach (z. B. während die app aus war)."""
        # feste Bezeichner, Zeiten als Zahlen
        query = (
            f'SELECT "wh", "covered_s" FROM {quote_ident(RP_RAW)}.{quote_ident("flows")} '  # noqa: S608
            f'WHERE time >= {epoch_ms(start)}ms AND time < {epoch_ms(end)}ms GROUP BY "flow"'
        )
        coverage: dict[int, float] = {}
        flows: dict[int, dict[str, float]] = {}
        for series in await reader.query(query):
            flow = series.tags.get("flow")
            column = "covered_s" if flow == COVERAGE_FLOW else "wh"
            if flow is None or "time" not in series.columns or column not in series.columns:
                continue
            time_index, value_index = series.columns.index("time"), series.columns.index(column)
            for row in series.values:
                stamp = _number(row[time_index]) if time_index < len(row) else None
                value = _number(row[value_index]) if value_index < len(row) else None
                if stamp is None or value is None:
                    continue
                if flow == COVERAGE_FLOW:
                    coverage[int(stamp)] = value
                else:
                    flows.setdefault(int(stamp), {})[flow] = value
        taken = 0
        for stamp in sorted(coverage):
            message = {
                "slot_start": datetime.fromtimestamp(stamp / 1000, UTC).isoformat(),
                "covered_s": coverage[stamp],
                "flows_wh": flows.get(stamp, {}),
            }
            try:
                slot_flows = parse_slot_flows(message)
            except ValueError as exc:
                log.warning("Ungültiger Slot in InfluxDB", error=str(exc))
                continue
            if await asyncio.to_thread(self.ingest, slot_flows, "influx"):
                taken += 1
        return taken

    def fill_missing_prices(self) -> int:
        """Bezug ohne Preis erhält den inzwischen bekannten Preis."""
        query = (
            select(ledger_slot)
            .where(ledger_slot.c.import_wh > 0)
            .where(ledger_slot.c.import_price_ct.is_(None))
            .order_by(ledger_slot.c.slot_start)
        )
        with self._engine.connect() as conn:
            rows = [_ledger_slot(row) for row in conn.execute(query).all()]
        return self._apply_prices(rows, only_known=True)

    def reprice(self, first: date, last: date, *, actor: str, source: Source) -> int:
        """Bewertet alle Slots der lokalen Tage ``first … last`` mit dem aktuellen Tarif neu."""
        if first > last:
            raise ValueError("Der Beginn liegt nach dem Ende")
        start, end = local_day_slots(first)[0], local_day_slots(last)[-1] + SLOT
        count = self._apply_prices(self.slots(start, end), only_known=False)
        self._audit.record(
            actor,
            source,
            "ledger.reprice",
            "abrechnung",
            {"from": first.isoformat(), "to": last.isoformat(), "slots": count},
        )
        return count

    def _apply_prices(self, slots: Sequence[LedgerSlot], *, only_known: bool) -> int:
        if not slots:
            return 0
        prices = {
            view.start: view.import_gross_ct
            for view in self._prices.slots(slots[0].slot_start, slots[-1].slot_start + SLOT)
        }
        now = self._clock.now()
        changed: list[tuple[LedgerSlot, float | None]] = []
        with self._lock, self._engine.begin() as conn:
            for slot in slots:
                price = prices.get(slot.slot_start)
                if only_known and price is None:
                    continue
                conn.execute(
                    update(ledger_slot)
                    .where(ledger_slot.c.slot_start == slot.slot_start)
                    .values(import_price_ct=price, updated_at=now)
                )
                changed.append((slot, price))
        for slot, price in changed:
            self._publish(slot.slot_start, slot.flows_wh, price)
        return len(changed)

    def slots(self, start: datetime, end: datetime) -> list[LedgerSlot]:
        query = (
            select(ledger_slot)
            .where(ledger_slot.c.slot_start >= ensure_utc(start))
            .where(ledger_slot.c.slot_start < ensure_utc(end))
            .order_by(ledger_slot.c.slot_start)
        )
        with self._engine.connect() as conn:
            return [_ledger_slot(row) for row in conn.execute(query).all()]

    def days(self, first: date, last: date) -> list[DaySummary]:
        if first > last:
            return []
        stored = self.slots(local_day_slots(first)[0], local_day_slots(last)[-1] + SLOT)
        by_day: dict[date, list[LedgerSlot]] = {}
        for slot in stored:
            by_day.setdefault(slot.slot_start.astimezone(LOCAL_TZ).date(), []).append(slot)
        settings = self._settings.current().settings
        summaries: list[DaySummary] = []
        day = first
        while day <= last:
            summaries.append(_summary(day, by_day.get(day, []), settings))
            day += timedelta(days=1)
        return summaries

    def component_status(self) -> ComponentStatus:
        with self._engine.connect() as conn:
            latest = conn.execute(select(func.max(ledger_slot.c.slot_start))).scalar()
        if latest is None:
            return ComponentStatus("ledger", False, "Noch keine Abrechnungsdaten")
        end = latest + SLOT
        if self._clock.now() - end <= FRESH_FOR:
            return ComponentStatus(
                "ledger", True, f"letzte Viertelstunde {latest.astimezone(LOCAL_TZ):%H:%M}"
            )
        return ComponentStatus(
            "ledger", False, f"keine Abrechnungsdaten seit {end.astimezone(LOCAL_TZ):%H:%M}"
        )
