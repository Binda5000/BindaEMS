"""Statistik je Tag, Woche, Monat und Jahr (Plan 4a, Entscheidungen 9 und 10, Präzisierung 11).

Die Kennzahlen eines Zeitraums entstehen aus den Summen der Tage, nie als Mittel der
Tageskennzahlen: Autarkie ``1 − Bezug ÷ Verbrauch``, Eigenverbrauch ``(PV − Einspeisung) ÷ PV``,
Ersparnis wie in ``ledger.service.summarize_slots``. Ein Geldbetrag ist ``None``, sobald ein Tag ihn
nicht kennt; ein Verhältnis ist ``None``, wenn der Nenner 0 ist (z. B. in der Zukunft).

Grundgebühren zählen anteilig je Tag bis einschließlich heute; der Leistungspreis zählt je Monat mit
Abrechnungsdaten aus der Monatsspitze, nur in Monat und Jahr und nur, wo ein Leistungspreis mit Satz
gilt (wie im Planer). Die Ersparnis bleibt ohne beides (Entscheidung 9).

``planned`` zählt die geplanten Ladungen mit Abfahrt im Zeitraum für die Abnahme „Ziel-SOC“
(Plan 4b, Entscheidung 15; ``PlannedCharges.summary``).

``StatsService`` liest blockierend: aus einem Thread bzw. einem synchronen Route-Handler aufrufen.
"""

from __future__ import annotations

import calendar
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any, Final, Literal, get_args

from bindaems.app.charging.planned import PlannedCharges
from bindaems.app.charging.sessions import (
    GUEST,
    UNKNOWN,
    FlowSplit,
    SessionService,
    merge_splits,
    vehicle_name,
)
from bindaems.app.consumers.days import NO_DAY, ConsumerDays
from bindaems.app.consumers.energy import EnergyCache
from bindaems.app.consumers.service import ConsumerService
from bindaems.app.ledger.checks import LedgerChecks
from bindaems.app.ledger.service import DaySummary, LedgerService, LedgerSlot, summarize_slots
from bindaems.app.settings.service import SettingsService
from bindaems.app.tariff.calc import monthly_fees_eur, power_cost_eur, power_rate
from bindaems.shared.config import Config
from bindaems.shared.settings import RuntimeSettings, TariffSettings
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, Clock, local_day_slots

Period = Literal["day", "week", "month", "year"]
PERIODS: Final = get_args(Period)

NOT_YET: Final = "Noch keine Daten"
NOT_COMPUTED: Final = "Noch nicht berechnet"


def period_range(period: Period, day: date) -> tuple[date, date]:
    """Erster und letzter Tag des Zeitraums, der ``day`` enthält (Woche: Montag bis Sonntag)."""
    if period == "day":
        return day, day
    if period == "week":
        monday = day - timedelta(days=day.weekday())
        return monday, monday + timedelta(days=6)
    if period == "month":
        return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])
    return date(day.year, 1, 1), date(day.year, 12, 31)


def fees_eur(first: date, last: date, tariff: TariffSettings) -> float:
    """Grundgebühren der Tage ``first … last``, je Tag ``Monatsgebühr ÷ Tage im Monat``."""
    total: list[float] = []
    day = first
    while day <= last:
        total.append(monthly_fees_eur(day, tariff) / calendar.monthrange(day.year, day.month)[1])
        day += timedelta(days=1)
    return math.fsum(total)


@dataclass(frozen=True)
class TodayStats:
    """Werte für Home Assistant (Task 10); ``None``: unbekannt."""

    savings_today_eur: float | None
    savings_same_import_today_eur: float | None
    savings_month_eur: float | None
    savings_same_import_month_eur: float | None
    autarky_today: float | None
    autarky_month: float | None


def _sum_known(values: Sequence[float | None]) -> float | None:
    """Summe; ``None``, sobald ein Wert unbekannt ist."""
    if any(value is None for value in values):
        return None
    return math.fsum(value for value in values if value is not None)


@dataclass(frozen=True)
class _Sums:
    """Summen über Tage; Geldbeträge ``None``, sobald ein Tag sie nicht kennt."""

    slots: int
    expected_slots: int
    estimated_slots: int
    complete: bool
    coverage: float
    pv_kwh: float
    import_kwh: float
    export_kwh: float
    house_kwh: float
    wallbox_kwh: float
    battery_charge_kwh: float
    battery_discharge_kwh: float
    consumption_kwh: float
    cost_eur: float | None
    revenue_eur: float | None
    net_eur: float | None
    autarky: float | None
    self_consumption: float | None
    savings_no_plant_eur: float | None
    savings_same_import_eur: float | None


def _sums(days: Sequence[DaySummary], fixed_eur: float) -> _Sums:
    def total(values: Sequence[float]) -> float:
        return math.fsum(values)

    pv = total([d.pv_kwh for d in days])
    imported = total([d.import_kwh for d in days])
    exported = total([d.export_kwh for d in days])
    consumption = total([d.consumption_kwh for d in days])
    expected = sum(d.expected_slots for d in days)
    net = _sum_known([d.net_cost_eur for d in days])
    revenue = _sum_known([d.revenue_eur for d in days])
    return _Sums(
        slots=sum(d.slots for d in days),
        expected_slots=expected,
        estimated_slots=sum(d.estimated_slots for d in days),
        complete=all(d.complete for d in days),
        coverage=(
            total([d.coverage * d.expected_slots for d in days]) / expected if expected else 0.0
        ),
        pv_kwh=pv,
        import_kwh=imported,
        export_kwh=exported,
        house_kwh=total([d.house_kwh for d in days]),
        wallbox_kwh=total([kwh for d in days for kwh in d.wallbox_kwh.values()]),
        battery_charge_kwh=total([d.battery_charge_kwh for d in days]),
        battery_discharge_kwh=total([d.battery_discharge_kwh for d in days]),
        consumption_kwh=consumption,
        cost_eur=_sum_known([d.cost_eur for d in days]),
        revenue_eur=revenue,
        net_eur=net,
        autarky=1 - imported / consumption if consumption > 0 else None,
        self_consumption=(pv - exported) / pv if pv > 0 else None,
        savings_no_plant_eur=consumption * fixed_eur - net if net is not None else None,
        savings_same_import_eur=(
            imported * fixed_eur - revenue - net
            if revenue is not None and net is not None
            else None
        ),
    )


def _month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


class StatsService:
    def __init__(
        self,
        ledger: LedgerService,
        sessions: SessionService,
        consumers: ConsumerService,
        consumer_days: ConsumerDays,
        energy: EnergyCache,
        checks: LedgerChecks,
        settings: SettingsService,
        cfg: Config,
        clock: Clock,
        planned: PlannedCharges,
    ) -> None:
        self._ledger = ledger
        self._sessions = sessions
        self._consumers = consumers
        self._consumer_days = consumer_days
        self._energy = energy
        self._checks = checks
        self._settings = settings
        self._cfg = cfg
        self._clock = clock
        self._planned = planned

    def local_today(self) -> date:
        return self._clock.now().astimezone(LOCAL_TZ).date()

    def today(self) -> TodayStats:
        """Ersparnis und Autarkie von heute und vom Monat bis heute."""
        today = self.local_today()
        fixed_eur = self._settings.current().settings.tariff.fixed_price_gross_ct / 100
        days = self._ledger.days(today.replace(day=1), today)
        day, month = _sums(days[-1:], fixed_eur), _sums(days, fixed_eur)
        return TodayStats(
            savings_today_eur=day.savings_no_plant_eur,
            savings_same_import_today_eur=day.savings_same_import_eur,
            savings_month_eur=month.savings_no_plant_eur,
            savings_same_import_month_eur=month.savings_same_import_eur,
            autarky_today=day.autarky,
            autarky_month=month.autarky,
        )

    def period(self, period: Period, day: date) -> dict[str, Any]:
        """Statistik des Zeitraums, der ``day`` enthält (Antwort von ``GET /api/stats``)."""
        first, last = period_range(period, day)
        settings = self._settings.current().settings
        tariff = settings.tariff
        today = self.local_today()
        days = self._ledger.days(first, last)
        sums = _sums(days, tariff.fixed_price_gross_ct / 100)
        fees = fees_eur(first, min(last, today), tariff)
        peaks = self._month_peaks(period, first, last, tariff)
        priced = [peak["power_eur"] for peak in peaks if peak["power_eur"] is not None]
        power = math.fsum(priced) if priced else None
        buckets = self._buckets(period, first, days, settings)
        vehicles, battery_to_vehicles, vehicle_days, vehicle_order = self._vehicles(first, last)
        return {
            "period": period,
            "first": first.isoformat(),
            "last": last.isoformat(),
            "complete": sums.complete,
            "coverage": sums.coverage,
            "estimated_slots": sums.estimated_slots,
            "energy": {
                "pv_kwh": sums.pv_kwh,
                "import_kwh": sums.import_kwh,
                "export_kwh": sums.export_kwh,
                "house_kwh": sums.house_kwh,
                "wallbox_kwh": sums.wallbox_kwh,
                "battery_charge_kwh": sums.battery_charge_kwh,
                "battery_discharge_kwh": sums.battery_discharge_kwh,
                "consumption_kwh": sums.consumption_kwh,
            },
            "money": {
                "import_eur": sums.cost_eur,
                "revenue_eur": sums.revenue_eur,
                "net_eur": sums.net_eur,
                "fees_eur": fees,
                "power_eur": power,
                "total_eur": (
                    sums.net_eur + fees + (power or 0.0) if sums.net_eur is not None else None
                ),
                "savings_no_plant_eur": sums.savings_no_plant_eur,
                "savings_same_import_eur": sums.savings_same_import_eur,
            },
            "ratios": {"autarky": sums.autarky, "self_consumption": sums.self_consumption},
            "buckets": buckets,
            "vehicles": vehicles,
            "battery_to_vehicles_kwh": battery_to_vehicles,
            "vehicle_buckets": self._vehicle_buckets(period, buckets, vehicle_days, vehicle_order),
            "planned": asdict(self._planned.summary(first, last)),
            "consumers": self._consumer_energy(first, last, today),
            "month_peaks": peaks,
            "checks": self._check_summary(first, last),
        }

    # Buckets

    def _buckets(
        self, period: Period, first: date, days: Sequence[DaySummary], settings: RuntimeSettings
    ) -> list[dict[str, Any]]:
        if period == "day":
            return self._hours(first, settings)
        if period in ("week", "month"):
            return [_bucket(day.date.isoformat(), [day]) for day in days]
        months: dict[str, list[DaySummary]] = {}
        for day in days:
            months.setdefault(_month_key(day.date), []).append(day)
        return [_bucket(key, group) for key, group in months.items()]

    def _hours(self, day: date, settings: RuntimeSettings) -> list[dict[str, Any]]:
        """Ein Bucket je lokaler Stunde: 23, 24 oder 25 an Tagen der Zeitumstellung."""
        starts = local_day_slots(day)
        origin = starts[0]
        groups: list[list[LedgerSlot]] = [[] for _ in range(len(starts) // 4)]
        for slot in self._ledger.slots(origin, starts[-1] + SLOT):
            groups[int((slot.slot_start - origin) / timedelta(hours=1))].append(slot)
        now = self._clock.now()
        return [
            {
                "start": (origin + timedelta(hours=hour)).astimezone(LOCAL_TZ).isoformat(),
                "pv_kwh": summary.pv_kwh,
                "import_kwh": summary.import_kwh,
                "export_kwh": summary.export_kwh,
                "consumption_kwh": summary.consumption_kwh,
                "net_eur": summary.net_cost_eur,
            }
            for hour, group in enumerate(groups)
            for summary in [summarize_slots(day, group, settings, now)]
        ]

    # Monatsspitzen und Leistungspreis

    def _month_peaks(
        self, period: Period, first: date, last: date, tariff: TariffSettings
    ) -> list[dict[str, Any]]:
        if period not in ("month", "year"):
            return []
        peaks: list[dict[str, Any]] = []
        for peak in self._ledger.month_peaks(first, last):
            local = peak.slot_start.astimezone(LOCAL_TZ)
            rate = power_rate(local.date(), tariff)
            peaks.append(
                {
                    "month": _month_key(local.date()),
                    "kw": peak.kw,
                    "slot_start": peak.slot_start.isoformat(),
                    "power_eur": power_cost_eur(peak.kw, rate)
                    if rate is not None and not rate.missing
                    else None,
                }
            )
        return peaks

    # Autos

    def _vehicles(
        self, first: date, last: date
    ) -> tuple[list[dict[str, Any]], float, dict[date, dict[str, FlowSplit]], list[str]]:
        """Autos mit Energie im Zeitraum: Reihenfolge der ``config.yaml``, dann sonstige
        Schlüssel, ``guest``, ``unknown``. Einträge ohne Energie (Session ohne Ladung) entfallen.
        Dazu die Akku-Energie ins Auto, die Aufteilung je Tag und die Reihenfolge der Schlüssel."""
        days = self._sessions.by_vehicle_days(first, last)
        grouped: dict[str, list[FlowSplit]] = {}
        for splits in days.values():
            for key, day_split in splits.items():
                grouped.setdefault(key, []).append(day_split)
        by_vehicle = {key: merge_splits(parts) for key, parts in grouped.items()}
        sessions = self._sessions.count_by_vehicle(first, last)
        configured = list(self._cfg.vehicles)
        extra = sorted(key for key in by_vehicle if key not in (*configured, GUEST, UNKNOWN))
        order = [*configured, *extra, GUEST, UNKNOWN]
        vehicles = [
            {
                "key": key,
                "name": vehicle_name(self._cfg, key),
                "kwh": split.kwh,
                "pv_kwh": split.pv_kwh,
                "battery_kwh": split.battery_kwh,
                "grid_kwh": split.grid_kwh,
                "cost_eur": split.cost_eur,
                "sessions": sessions.get(key, 0),
            }
            for key in order
            if (split := by_vehicle.get(key)) is not None and split.kwh > 0
        ]
        battery = math.fsum(split.battery_kwh for split in by_vehicle.values())
        return vehicles, battery, days, order

    def _vehicle_buckets(
        self,
        period: Period,
        buckets: Sequence[Mapping[str, Any]],
        days: Mapping[date, Mapping[str, FlowSplit]],
        order: Sequence[str],
    ) -> list[dict[str, Any]]:
        """Je Bucket die Autos mit Energie (Reihenfolge ``order``): Woche und Monat je Tag, Jahr je
        Monat; Tag liefert keine Einträge."""
        if period == "day":
            return []
        keys = _month_key if period == "year" else date.isoformat
        grouped: dict[str, dict[str, list[FlowSplit]]] = {}
        for day, splits in days.items():
            for key, day_split in splits.items():
                grouped.setdefault(keys(day), {}).setdefault(key, []).append(day_split)
        result: list[dict[str, Any]] = []
        for bucket in buckets:
            parts = grouped.get(bucket["start"], {})
            vehicles = [
                {
                    "key": key,
                    "kwh": total.kwh,
                    "pv_kwh": total.pv_kwh,
                    "battery_kwh": total.battery_kwh,
                    "grid_kwh": total.grid_kwh,
                    "cost_eur": total.cost_eur,
                }
                for key in order
                if key in parts and (total := merge_splits(parts[key])).kwh > 0
            ]
            result.append({"start": bucket["start"], "vehicles": vehicles})
        return result

    # Verbraucher

    def _consumer_energy(self, first: date, last: date, today: date) -> list[dict[str, Any]]:
        """Energie je Verbraucher: gespeicherte Tage bis gestern, heute aus dem Cache; Tage in der
        Zukunft zählen nicht. Ein unbekannter Tag macht die Summe ``None`` mit seinem Grund."""
        consumers = self._consumers.list()
        stored_last = min(last, today - timedelta(days=1))
        stored = self._consumer_days.energy(first, stored_last) if first <= stored_last else {}
        result: list[dict[str, Any]] = []
        for consumer in consumers:
            parts: list[float] = []
            note: str | None = None
            if first <= stored_last:
                kwh, reason = stored.get(consumer.id, (None, NO_DAY))
                if kwh is None:
                    note = reason or NO_DAY
                else:
                    parts.append(kwh)
            if first <= today <= last and note is None:
                kwh = self._energy.energy_kwh(consumer)
                if kwh is None:
                    note = self._energy.note(consumer) or NOT_COMPUTED
                else:
                    parts.append(kwh)
            if first > today:
                note = NOT_YET
            result.append(
                {
                    "id": consumer.id,
                    "name": consumer.name,
                    "kwh": math.fsum(parts) if note is None else None,
                    "note": note,
                }
            )
        return result

    # Abgleich

    def _check_summary(self, first: date, last: date) -> dict[str, Any]:
        found = self._checks.checks(first, last)
        return {
            "days": len({check.day for check in found}),
            "flagged_days": sorted({check.day.isoformat() for check in found if check.flagged}),
            "totals": self._checks.totals(found),
        }


def _bucket(start: str, days: Sequence[DaySummary]) -> dict[str, Any]:
    return {
        "start": start,
        "pv_kwh": math.fsum(d.pv_kwh for d in days),
        "import_kwh": math.fsum(d.import_kwh for d in days),
        "export_kwh": math.fsum(d.export_kwh for d in days),
        "consumption_kwh": math.fsum(d.consumption_kwh for d in days),
        "net_eur": _sum_known([d.net_cost_eur for d in days]),
    }
