"""Preis-Pipeline (Spec 6.7, 9.2): abrufen, archivieren, prüfen, Brutto/Netto bestimmen,
speichern und in InfluxDB veröffentlichen.

Gültige Werte von smartENERGY (``primary``) werden nie durch die Ersatzquelle überschrieben.
Datenbankzugriffe laufen in einem Worker-Thread, damit die Ereignisschleife frei bleibt.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal

from bindaems.app.fetch import FetchError, RawResponse
from bindaems.app.history.influx import RP_LONG
from bindaems.app.prices.checks import (
    VatDetection,
    by_local_day,
    detect_vat,
    range_findings,
    structure_findings,
    to_slots,
)
from bindaems.app.prices.service import PriceService
from bindaems.app.prices.sources import (
    PriceInterval,
    PriceParseError,
    ReferenceSource,
    SmartEnergySource,
)
from bindaems.app.prices.store import PriceStore, SlotRow
from bindaems.app.settings.service import SettingsService
from bindaems.app.status import ComponentStatus
from bindaems.shared.influx.lineprotocol import Point, PointSink
from bindaems.shared.settings import RuntimeSettings
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, Clock, local_day_slots, slot_start

VatMode = Literal["net", "gross"]
VAT_WORDS: dict[VatMode, str] = {"net": "netto", "gross": "brutto"}
NO_PRIMARY = "keine Daten von smartENERGY"
PUBLISH_AHEAD = timedelta(days=3)


@dataclass(frozen=True)
class DayResult:
    day: date
    origin: str | None  # gespeicherte Herkunft; None = nichts gespeichert
    findings: list[str]


@dataclass(frozen=True)
class PriceStatus:
    last_attempt: datetime | None
    last_success: datetime | None
    vat_mode: str | None
    vat_detection: VatDetection | None
    days: list[DayResult]
    errors: list[str]


@dataclass
class _Fetched:
    raw_id: int | None = None
    intervals: list[PriceInterval] = field(default_factory=list)


def _local_midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=LOCAL_TZ)


def _write(sink: PointSink, kind: str, ts: datetime, value: float) -> None:
    sink.write(Point("price", {"kind": kind}, {"ct_kwh": value}, ts, rp=RP_LONG))


class PricePipeline:
    def __init__(
        self,
        store: PriceStore,
        primary: SmartEnergySource,
        reference: Callable[[str], ReferenceSource],
        settings: SettingsService,
        prices: PriceService,
        clock: Clock,
        sink: PointSink | None = None,
    ) -> None:
        self._store = store
        self._primary = primary
        self._reference = reference
        self._settings = settings
        self._prices = prices
        self._clock = clock
        self._sink = sink
        self._status = PriceStatus(None, None, None, None, [], [])
        self._detected: VatMode | None = None  # zuletzt erfolgreiche Erkennung
        self._lock = asyncio.Lock()

    def status(self) -> PriceStatus:
        return self._status

    async def refresh(self) -> PriceStatus:
        async with self._lock:
            now = self._clock.now()
            settings = self._settings.current().settings
            today = now.astimezone(LOCAL_TZ).date()
            window = (today, today + timedelta(days=1))
            reference = self._reference(settings.prices.reference_source)
            errors: list[str] = []
            primary = await self._take(self._primary.fetch(), self._primary.parse, errors)
            fetched = await self._take(
                reference.fetch(_local_midnight(today), _local_midnight(today + timedelta(days=2))),
                reference.parse,
                errors,
            )
            self._status = await asyncio.to_thread(
                self._process, now, settings, window, primary, reference.name, fetched, errors
            )
            return self._status

    async def _take(
        self,
        request: Awaitable[RawResponse],
        parse: Callable[[str], list[PriceInterval]],
        errors: list[str],
    ) -> _Fetched:
        """Abrufen, archivieren (auch Fehlerantworten) und lesen; Fehler landen in ``errors``."""
        try:
            raw = await request
        except FetchError as exc:
            errors.append(str(exc))
            return _Fetched()
        raw_id = await asyncio.to_thread(self._store.archive, raw)
        if not raw.ok:
            errors.append(f"{raw.source}: HTTP {raw.status}")
            return _Fetched(raw_id)
        try:
            return _Fetched(raw_id, parse(raw.body))
        except PriceParseError as exc:
            errors.append(str(exc))
            return _Fetched(raw_id)

    def _process(
        self,
        now: datetime,
        settings: RuntimeSettings,
        window: tuple[date, ...],
        primary: _Fetched,
        reference_name: str,
        reference: _Fetched,
        errors: list[str],
    ) -> PriceStatus:
        factor = 1 + settings.tariff.vat_pct / 100
        primary_days = {d: i for d, i in by_local_day(primary.intervals).items() if d in window}
        reference_days = {d for d in by_local_day(reference.intervals) if d in window}
        reference_slots = to_slots(reference.intervals)
        raw_slots = {day: to_slots(intervals) for day, intervals in primary_days.items()}
        structure = {day: structure_findings(day, primary_days[day]) for day in primary_days}
        clean = [day for day, found in structure.items() if not found]

        detection: VatDetection | None = None
        if clean:
            clean_slots = {slot: value for day in clean for slot, value in raw_slots[day].items()}
            detection = detect_vat(clean_slots, reference_slots, factor)
            if detection.result is not None:
                self._detected = detection.result
        vat_mode = self._vat_mode(settings, detection, bool(clean), errors)
        divisor = factor if vat_mode == "gross" else 1.0
        net_slots = {
            day: {slot: value / divisor for slot, value in slots.items()}
            for day, slots in raw_slots.items()
        }

        rows: list[SlotRow] = []
        days: list[DayResult] = []
        for day in sorted(primary_days.keys() | reference_days):
            expected = local_day_slots(day)
            found = structure[day] + range_findings(net_slots[day]) if day in structure else None
            if found == []:
                rows += [
                    SlotRow(
                        slot,
                        raw_slots[day][slot],
                        net_slots[day][slot],
                        reference_slots.get(slot),
                        "primary",
                        primary.raw_id,
                    )
                    for slot in expected
                ]
                days.append(DayResult(day, "primary", []))
                continue
            findings = found if found is not None else [NO_PRIMARY]
            if all(slot in reference_slots for slot in expected):
                rows += [
                    SlotRow(
                        slot,
                        None,
                        reference_slots[slot],
                        reference_slots[slot],
                        "fallback",
                        reference.raw_id,
                    )
                    for slot in expected
                ]
                reason = (
                    NO_PRIMARY if found is None else f"smartENERGY verdächtig ({'; '.join(found)})"
                )
                errors.append(f"{day:%d.%m.}: {reason} – Ersatzquelle {reference_name}")
                days.append(DayResult(day, "fallback", findings))
            else:
                days.append(DayResult(day, None, findings))

        if rows:
            self._store.save(rows)
            self._publish(rows)
        return PriceStatus(
            last_attempt=now,
            last_success=now if rows else self._status.last_success,
            vat_mode=vat_mode,
            vat_detection=detection,
            days=days,
            errors=errors,
        )

    def _vat_mode(
        self,
        settings: RuntimeSettings,
        detection: VatDetection | None,
        primary_used: bool,
        errors: list[str],
    ) -> VatMode:
        """Einstellung, sonst Erkennung, sonst letzte Erkennung, sonst Ersatzeinstellung."""
        if settings.prices.vat_mode == "net":
            return "net"
        if settings.prices.vat_mode == "gross":
            return "gross"
        if detection is not None and detection.result is not None:
            return detection.result
        if self._detected is not None:
            return self._detected
        fallback = settings.prices.vat_fallback
        if primary_used:  # ohne verwendbaren Primärtag spielt die Einstellung keine Rolle
            reason = (
                f"Verhältnis {detection.ratio:.3f}"
                if detection is not None and detection.ratio is not None
                else "zu wenige Vergleichswerte"
            )
            errors.append(
                f"Brutto/Netto nicht erkennbar ({reason}) – "
                f"Ersatzeinstellung „{VAT_WORDS[fallback]}“ aktiv"
            )
        return fallback

    def restore(self) -> None:
        """Erkennt Brutto/Netto aus den gespeicherten Slots der letzten Tage (nach Neustart)."""
        settings = self._settings.current().settings
        today = self._clock.now().astimezone(LOCAL_TZ).date()
        stored = self._store.get(
            _local_midnight(today - timedelta(days=1)), _local_midnight(today + timedelta(days=2))
        )
        primary: dict[datetime, float] = {}
        reference: dict[datetime, float] = {}
        for price in stored:
            if price.spot_raw_ct is not None and price.reference_ct is not None:
                primary[price.slot_start] = price.spot_raw_ct
                reference[price.slot_start] = price.reference_ct
        detection = detect_vat(primary, reference, 1 + settings.tariff.vat_pct / 100)
        if detection.result is not None:
            self._detected = detection.result

    def republish(self) -> None:
        """Bezugs- und Einspeisepreise ab dem aktuellen Slot neu schreiben (Tarif geändert)."""
        start = slot_start(self._clock.now())
        self._publish_tariff(start, start + PUBLISH_AHEAD)

    def _publish(self, rows: Sequence[SlotRow]) -> None:
        if self._sink is None:
            return
        for row in rows:
            if row.origin == "primary" and row.spot_raw_ct is not None:
                _write(self._sink, "spot_raw", row.slot_start, row.spot_raw_ct)
            if row.reference_ct is not None:
                _write(self._sink, "reference", row.slot_start, row.reference_ct)
        starts = [row.slot_start for row in rows]
        self._publish_tariff(min(starts), max(starts) + SLOT)

    def _publish_tariff(self, start: datetime, end: datetime) -> None:
        if self._sink is None:
            return
        for view in self._prices.slots(start, end):
            _write(self._sink, "import_gross", view.start, view.import_gross_ct)
            if view.feed_in_ct is not None:
                _write(self._sink, "feed_in", view.start, view.feed_in_ct)

    def component_status(self) -> ComponentStatus:
        current = slot_start(self._clock.now())
        stored = self._store.get(current, current + PUBLISH_AHEAD)
        errors = self._status.errors
        if stored and stored[0].slot_start == current and not errors:
            last = stored[-1].slot_start.astimezone(LOCAL_TZ)
            return ComponentStatus("prices", True, f"Preise bis {last:%d.%m. %H:%M}")
        return ComponentStatus(
            "prices", False, errors[0] if errors else "Kein Preis für den aktuellen Slot"
        )
