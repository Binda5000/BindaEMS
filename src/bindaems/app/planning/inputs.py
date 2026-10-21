"""Eingangsdaten der Planung (Plan 3b, Präzisierungen 4 und 5; Plan 4b, Präzisierung 10).

Friert Horizont, Restwert, Leistungspreis je Monat, Akkuzustand und die geplanten Autos zu einem
``PlanInputs`` ein, den Optimierer, Ersatzplan und Szenarien gemeinsam verwenden. Reine
Rechnung: Die Zeit kommt als Parameter, nichts wird gelesen oder geschrieben.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from statistics import median
from typing import Annotated, Final, Literal

from pydantic import AwareDatetime, Field

from bindaems.app.planning.battery_model import LIMIT_NOW_SLOTS as LIMIT_NOW_SLOTS
from bindaems.app.planning.battery_model import eta
from bindaems.app.planning.vehicles import PlanVehicleInput
from bindaems.app.tariff.calc import power_rate
from bindaems.shared.config import Config, _Model
from bindaems.shared.plan import Plan, PlanTrigger
from bindaems.shared.settings import PlanningSettings, TariffSettings
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, ensure_utc, slot_start

MAX_SLOTS: Final = 144  # 36 h
MIN_KNOWN_SLOTS: Final = 16  # 4 h: darunter kommen Schätzpreise dazu
MIN_SLOTS: Final = 4  # darunter gibt es keinen Plan
INERTIA_SLOTS: Final = 4  # Planträgheit wirkt auf die ersten Slots
END_WINDOW: Final = timedelta(hours=24)  # Preise nach dem Horizont für den Restwert
PRICE_WINDOW: Final = timedelta(hours=60)  # Horizont (36 h) plus Fenster des Restwerts

# Unterstützungsschwelle wie ``control.support_soc_pct``, wenn der Aufrufer keine angibt
DEFAULT_SUPPORT_SOC_PCT: Final = 70.0

PriceOrigin = Literal["primary", "fallback", "estimated"]

Finite = Annotated[float, Field(allow_inf_nan=False)]
NonNegativeFinite = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class PlanningUnavailable(Exception):
    """Es gibt keinen Plan; ``reason`` ist der Grund wie im Status „Kein Plan: …“."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PriceInput(_Model):
    """Bezugs- und Einspeisepreis eines Slots in ct/kWh brutto; ``feed_in_ct`` kann fehlen."""

    start: AwareDatetime
    import_ct: Finite
    feed_in_ct: Finite | None
    origin: PriceOrigin


class BatteryState(_Model):
    """Zustand und Grenzen des Akkus; ``limit_now_*`` sind die BMS-Grenzen (CCL/DCL · Spannung)."""

    soc_pct: Finite
    usable_kwh: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    reserve_pct: Finite
    soc_max_pct: Finite
    max_charge_w: NonNegativeFinite
    max_discharge_w: NonNegativeFinite
    limit_now_charge_w: NonNegativeFinite | None
    limit_now_discharge_w: NonNegativeFinite | None
    max_grid_charge_setpoint_w: NonNegativeFinite
    grid_max_import_w: NonNegativeFinite


class PeakMonth(_Model):
    """Leistungspreis eines lokalen Monats; Sätze in ct je kW und Monat brutto."""

    month: str  # JJJJ-MM, lokal
    peak0_kw: NonNegativeFinite  # verrechnete Spitze vor dem Horizont, mindestens ``minimum_kw``
    tier_kw: NonNegativeFinite
    below_ct_per_kw: NonNegativeFinite
    above_ct_per_kw: NonNegativeFinite


class SlotInput(_Model):
    """Ein Slot des Horizonts; ``prev_battery_w`` ist die Netto-Akkuleistung des vorigen Plans.

    ``pv_p25_w`` ist die PV-Prognose P25 (Regel R2), ohne Wert gilt ``pv_w``. ``prev_ev_w`` ist
    die Ladeleistung je Auto im vorigen Plan (W, ein fehlendes Auto 0), ``None`` ohne Slot des
    vorigen Plans oder nach den ersten Slots (Planträgheit). Gespeicherte Eingangsdaten ohne die
    beiden Felder laden mit ``None`` (Plan 4b).
    """

    start: AwareDatetime
    import_ct: Finite
    feed_in_ct: Finite | None
    origin: PriceOrigin
    pv_w: Finite
    load_w: Finite
    prev_battery_w: Finite | None
    pv_p25_w: Finite | None = None
    prev_ev_w: dict[str, Finite] | None = None


class PlanInputs(_Model):
    created_at: AwareDatetime
    trigger: PlanTrigger
    settings_version: int
    planning: PlanningSettings
    battery: BatteryState
    month_peak_kw: Finite | None
    peak_months: list[PeakMonth]
    end_value_ct: Finite  # ct je kWh im Akku am Horizontende
    support_soc_pct: Finite = DEFAULT_SUPPORT_SOC_PCT  # Schwelle der Akku-Unterstützung in %
    # Untergrenzen des Budgets (Gesamtreview I2): ``control.peak_target_kw`` und der kleinste
    # Ladestrom des größten Autos (``car_floor_kw``), beide in kW
    peak_target_kw: NonNegativeFinite | None = None
    car_floor_kw: NonNegativeFinite = 0.0
    slots: list[SlotInput]
    # die geplanten Autos in der Reihenfolge ihrer Priorität, die angesteckten vor den erwarteten
    # (Plan 4b, 5b); alte Eingangsdaten: keine
    vehicles: list[PlanVehicleInput] = Field(default_factory=list)


def _by_start(prices: Sequence[PriceInput]) -> dict[datetime, PriceInput]:
    result: dict[datetime, PriceInput] = {}
    for item in prices:
        result.setdefault(ensure_utc(item.start), item)
    return result


def _within(prices: Sequence[PriceInput], start: datetime, window: timedelta) -> list[PriceInput]:
    """Preise mit Slotbeginn in ``[start, start + window)``, nach Beginn sortiert."""
    return sorted(
        (p for p in prices if start <= ensure_utc(p.start) < start + window),
        key=lambda p: ensure_utc(p.start),
    )


def horizon_prices(
    prices: Sequence[PriceInput], now: datetime
) -> tuple[list[PriceInput], list[PriceInput]]:
    """Preise des Horizonts und der 24 h danach.

    Der Horizont beginnt im aktuellen Slot und läuft lückenlos über bekannte Preise (``primary``
    oder ``fallback``), höchstens ``MAX_SLOTS``. Sind es weniger als ``MIN_KNOWN_SLOTS``, kommen
    lückenlos Schätzpreise dazu, bis dieser Wert erreicht ist. Fehlt der Preis des aktuellen
    Slots, ist der Horizont leer.
    """
    by_start = _by_start(prices)
    horizon: list[PriceInput] = []
    current = slot_start(now)
    while len(horizon) < MAX_SLOTS:
        item = by_start.get(current)
        if item is None or item.origin == "estimated":
            break
        horizon.append(item)
        current += SLOT
    while len(horizon) < MIN_KNOWN_SLOTS:
        item = by_start.get(current)
        if item is None or item.origin != "estimated":
            break
        horizon.append(item)
        current += SLOT
    if not horizon:
        return [], []
    return horizon, _within(prices, current, END_WINDOW)


def end_value_ct(after: Sequence[PriceInput], planning: PlanningSettings) -> float:
    """Restwert in ct je kWh im Akku am Horizontende (Präzisierung 4); ohne Preise 0."""
    if not after:
        return 0.0
    value = eta(planning.roundtrip_pct) * median(p.import_ct for p in after) - planning.wear_ct
    return max(0.0, value * planning.end_value_factor)


def peak_months(
    starts: Sequence[datetime], tariff: TariffSettings, month_peak_kw: float | None
) -> list[PeakMonth]:
    """Leistungspreis je lokalem Monat der Slots; Monate ohne eingetragenen Satz fehlen.

    Die verrechnete Spitze vor dem Horizont gilt nur für den Monat des ersten Slots; spätere
    Monate beginnen beim Mindestwert.
    """
    result: list[PeakMonth] = []
    seen: set[str] = set()
    for index, start in enumerate(starts):
        local = ensure_utc(start).astimezone(LOCAL_TZ)
        month = local.strftime("%Y-%m")
        if month in seen:
            continue
        seen.add(month)
        rate = power_rate(local.date(), tariff)
        if rate is None or rate.missing:
            continue
        to_ct = 100 / 12 * rate.vat_factor
        known_peak = (month_peak_kw or 0.0) if index == 0 else 0.0
        result.append(
            PeakMonth(
                month=month,
                peak0_kw=max(known_peak, rate.minimum_kw),
                tier_kw=rate.tier_kw,
                below_ct_per_kw=rate.below_eur_kw_year * to_ct,
                above_ct_per_kw=rate.above_eur_kw_year * to_ct,
            )
        )
    return result


def _previous_battery_w(previous: Plan | None, start: datetime) -> float | None:
    slot = previous.slot_at(start) if previous is not None else None
    if slot is None:
        return None
    return slot.charge_pv_w + slot.charge_grid_w - slot.discharge_w


def _previous_ev_w(previous: Plan | None, start: datetime) -> dict[str, float] | None:
    slot = previous.slot_at(start) if previous is not None else None
    return None if slot is None else dict(slot.ev_w)


def assemble_inputs(
    *,
    now: datetime,
    trigger: PlanTrigger,
    prices: Sequence[PriceInput],
    pv_w: Mapping[datetime, float] | None,
    load_w: Mapping[datetime, float] | None,
    battery: BatteryState | None,
    planning: PlanningSettings,
    tariff: TariffSettings,
    month_peak_kw: float | None,
    previous: Plan | None,
    settings_version: int,
    support_soc_pct: float = DEFAULT_SUPPORT_SOC_PCT,
    peak_target_kw: float | None = None,
    car_floor_kw: float = 0.0,
    pv_p25_w: Mapping[datetime, float] | None = None,
    vehicles: Callable[[list[SlotInput]], list[PlanVehicleInput]] | None = None,
) -> PlanInputs:
    """Eingangsdaten eines Laufs; wirft ``PlanningUnavailable``.

    ``peak_target_kw`` und ``car_floor_kw`` sind die Untergrenzen des Budgets
    (``control.peak_target_kw``, ``car_floor_kw``; Gesamtreview I2). ``pv_p25_w`` ist die
    PV-Prognose P25; ein Slot ohne sie bekommt ``None`` und beendet den Horizont nicht.
    ``vehicles`` ergibt die geplanten Autos zu den Slots des Horizonts (``planned_vehicles``, danach
    ``expected_vehicles``); ohne gibt es keine.

    Prüfreihenfolge: Akkuzustand, Preis des aktuellen Slots, PV, Last, Horizontlänge. Der
    Horizont endet vor dem ersten Slot ohne PV- oder Lastprognose.
    """
    if battery is None:
        raise PlanningUnavailable("kein SOC vom core")
    horizon, _ = horizon_prices(prices, now)
    if not horizon:
        raise PlanningUnavailable("kein Preis für den aktuellen Slot")
    if not pv_w:
        raise PlanningUnavailable("keine PV-Prognose")
    if not load_w:
        raise PlanningUnavailable("keine Lastprognose")

    slots: list[SlotInput] = []
    for index, item in enumerate(horizon):
        start = ensure_utc(item.start)
        if start not in pv_w or start not in load_w:
            break
        slots.append(
            SlotInput(
                start=start,
                import_ct=item.import_ct,
                feed_in_ct=item.feed_in_ct,
                origin=item.origin,
                pv_w=pv_w[start],
                load_w=load_w[start],
                prev_battery_w=(
                    _previous_battery_w(previous, start) if index < INERTIA_SLOTS else None
                ),
                pv_p25_w=pv_p25_w.get(start) if pv_p25_w is not None else None,
                prev_ev_w=_previous_ev_w(previous, start) if index < INERTIA_SLOTS else None,
            )
        )
    if len(slots) < MIN_SLOTS:
        raise PlanningUnavailable("zu wenig Daten für einen Plan")

    horizon_end = slots[-1].start + SLOT
    return PlanInputs(
        created_at=ensure_utc(now),
        trigger=trigger,
        settings_version=settings_version,
        planning=planning,
        battery=battery,
        month_peak_kw=month_peak_kw,
        peak_months=peak_months([s.start for s in slots], tariff, month_peak_kw),
        end_value_ct=end_value_ct(_within(prices, horizon_end, END_WINDOW), planning),
        support_soc_pct=support_soc_pct,
        peak_target_kw=peak_target_kw,
        car_floor_kw=car_floor_kw,
        slots=slots,
        vehicles=vehicles(slots) if vehicles is not None else [],
    )


def car_floor_kw(cfg: Config) -> float:
    """Der kleinste Ladestrom des größten Autos in kW: ``max(min_a · phases ·
    grid.voltage_nominal_v)`` über die Fahrzeuge der Konfiguration, ohne Fahrzeuge 0."""
    volts = cfg.grid.voltage_nominal_v
    return max(
        (vehicle.min_a * vehicle.phases * volts / 1000 for vehicle in cfg.vehicles.values()),
        default=0.0,
    )
