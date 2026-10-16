"""Lauf eines Szenarios (Plan 3b, Präzisierungen 18 und 19): Neuplanen auf Ist-Werten.

Zu jedem Neuplanungszeitpunkt (alle ``step_slots`` Slots, zur Minute 1 nach Slotbeginn wie im
Dienst) entsteht ein Plan wie im Planungsdienst:

- **Eingangsdaten:** SOC und Monatsspitze aus dem bisherigen Lauf; je Slot die Prognose der
  Horizontklasse des Vorlaufs (``forecast_at``); Preise bekannt bis Ende heute, ab 14:00 Uhr
  lokal bis Ende morgen (``known_until``), danach geschätzt als Median des Bezugspreises
  derselben lokalen Uhrzeit an bis zu ``ESTIMATE_DAYS`` Vortagen der Daten (mindestens 2 Werte).
- **Plan:** ``assemble_inputs`` mit dem vorigen Plan, ``solve`` im selben Prozess und
  ``plan_once`` (Absichten, Nachrechnung, Plausibilität, sonst Ersatzplan).
- **Ausführung:** Bis zur nächsten Neuplanung gelten die Absichten des Plans, umgesetzt vom
  Akkumodell auf den Ist-Werten; ein Slot ohne gültigen Plan (``valid_until`` ≤ Slotbeginn) läuft
  mit ``EIGENVERBRAUCH``.

„Victron allein“ fährt ``EIGENVERBRAUCH`` in allen Slots auf denselben Ist-Werten ab demselben
SOC. Kosten je Lauf: Energie (Bezug − Einspeisung) und der Leistungspreis je Monat auf den
höchsten Bezug des Laufs im Monat (mindestens die Spitze vor dem Lauf). Die bereinigten Kosten
des Optimierers ziehen den Wert des Mehr-SOC am Ende ab (``end_soc_value_ct``).

Regel R (Präzisierung 19): Ein Verstoß ist ein ausgeführter ``NETZLADEN``-Slot, nach dem der
Plan dieses Zeitpunkts bis zu seinem Horizontende mehr als ``R1_TOL_KWH`` Einspeisung erwartet.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from statistics import median
from types import MappingProxyType
from typing import Final

from bindaems.app.forecast.archive import HorizonClass, horizon_class
from bindaems.app.planning.battery_model import (
    SLOT_H,
    BatteryParams,
    SimSlot,
    SimStep,
    eta,
    params_of,
    sim_slots,
    simulate,
    step,
)
from bindaems.app.planning.inputs import (
    MAX_SLOTS,
    PRICE_WINDOW,
    BatteryState,
    PlanningUnavailable,
    PriceInput,
    assemble_inputs,
)
from bindaems.app.planning.optimizer import solve
from bindaems.app.planning.plausibility import R1_TOL_KWH, export_after_kwh
from bindaems.app.planning.service import plan_once
from bindaems.app.tariff.calc import power_cost_eur, power_rate
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind, Plan
from bindaems.shared.settings import PlanningSettings
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, ensure_utc, slot_start, slots_between
from bindaems.tools.scenario.data import ScenarioFile, ScenarioSlot

DAY_AHEAD_AT: Final = time(14)  # ab dann sind die Preise von morgen bekannt
ESTIMATE_DAYS: Final = 7  # Vortage für den Schätzpreis
ESTIMATE_MIN_VALUES: Final = 2
PLAN_DELAY: Final = timedelta(minutes=1)  # Neuplanung zur Minute 1 nach Slotbeginn (Dienst)

# Fehlt die Klasse eines Vorlaufs, gilt zuerst eine früher ausgegebene (längerer Vorlauf, sie
# gäbe es zu diesem Zeitpunkt schon), dann eine spätere.
_CLASS_ORDER: Mapping[HorizonClass, tuple[HorizonClass, ...]] = MappingProxyType(
    {
        "h0_6": ("h0_6", "h6_24", "h24"),
        "h6_24": ("h6_24", "h24", "h0_6"),
        "h24": ("h24", "h6_24", "h0_6"),
    }
)
_OWN = BatteryIntent()


@dataclass(frozen=True)
class Forecast:
    """Prognose eines Slots in W, wie sie zu einem Vorlauf im Archiv stünde."""

    pv: float
    load: float


@dataclass(frozen=True)
class DayResult:
    """Ein lokaler Tag; Kosten in ct nur für Energie (der Leistungspreis gilt je Monat)."""

    day: date
    baseline_ct: float
    optimizer_ct: float
    grid_charge_kwh: float
    hold_slots: int
    r1_violations: int
    fallbacks: int


@dataclass(frozen=True)
class ScenarioResult:
    """Ergebnis eines Laufs; ``*_ct`` gesamt einschließlich Leistungspreis."""

    name: str
    step_slots: int
    days: list[DayResult]
    baseline_ct: float
    optimizer_ct: float
    adjusted_optimizer_ct: float
    baseline_power_ct: float
    optimizer_power_ct: float
    r1_violations: int
    charge_before_export_kwh: float
    min_soc_kwh: float
    reserve_kwh: float
    plans: int
    fallbacks: int


def _pick(values: Mapping[HorizonClass, float], wanted: HorizonClass, actual: float) -> float:
    for cls in _CLASS_ORDER[wanted]:
        if cls in values:
            return values[cls]
    return actual


def forecast_at(slot: ScenarioSlot, *, lead: timedelta) -> Forecast:
    """Prognose des Slots bei Vorlauf ``lead`` (Slotbeginn − Planungszeitpunkt).

    Es gilt der Archivwert der Horizontklasse des Vorlaufs; ein Slot, der schon begonnen hat,
    zählt zu ``h0_6``. Fehlt die Klasse, die nächste vorhandene (``_CLASS_ORDER``), sonst der
    Ist-Wert.
    """
    cls = horizon_class(max(lead, timedelta(0))) or "h0_6"
    return Forecast(
        pv=_pick(slot.pv_forecast_w, cls, slot.pv_w),
        load=_pick(slot.load_forecast_w, cls, slot.load_w),
    )


def _local(moment: datetime) -> datetime:
    return ensure_utc(moment).astimezone(LOCAL_TZ)


def _data_end(data: ScenarioFile) -> datetime:
    return ensure_utc(data.slots[-1].start) + SLOT


def known_until(data: ScenarioFile, now: datetime) -> datetime:
    """Ende (exklusiv) der bekannten Preise: Ende des heutigen lokalen Tages, ab
    ``DAY_AHEAD_AT`` Ende des morgigen, höchstens bis zum Ende der Daten."""
    local = _local(now)
    days = 2 if local.time() >= DAY_AHEAD_AT else 1
    end = datetime.combine(local.date() + timedelta(days=days), time(0), tzinfo=LOCAL_TZ)
    return min(ensure_utc(end), _data_end(data))


def _by_local(data: ScenarioFile) -> dict[tuple[date, time], ScenarioSlot]:
    """Slots nach lokalem Tag und lokaler Uhrzeit (bei der Herbstumstellung der erste)."""
    result: dict[tuple[date, time], ScenarioSlot] = {}
    for item in data.slots:
        local = _local(item.start)
        result.setdefault((local.date(), local.time()), item)
    return result


def price_inputs(data: ScenarioFile, now: datetime) -> list[PriceInput]:
    """Preise des Planungsfensters ab dem Slot von ``now``: bekannt (``primary``) bis
    ``known_until``, danach geschätzt; Slots ohne Schätzung fehlen.

    Die Schätzung ist der Median der Bezugspreise derselben lokalen Uhrzeit an den bis zu
    ``ESTIMATE_DAYS`` Tagen vor dem heutigen, soweit sie in den Daten liegen, und braucht
    mindestens ``ESTIMATE_MIN_VALUES`` Werte. Der Einspeisepreis kommt vom jüngsten dieser Tage.
    """
    start = slot_start(now)
    known_end = known_until(data, now)
    by_start = {ensure_utc(item.start): item for item in data.slots}
    by_local = _by_local(data)
    today = _local(now).date()
    result: list[PriceInput] = []
    for moment in slots_between(start, start + PRICE_WINDOW):
        item = by_start.get(moment)
        if moment < known_end and item is not None:
            result.append(
                PriceInput(
                    start=moment,
                    import_ct=item.import_ct,
                    feed_in_ct=item.feed_in_ct,
                    origin="primary",
                )
            )
            continue
        clock = _local(moment).time()
        before = [
            by_local[key]
            for back in range(1, ESTIMATE_DAYS + 1)
            if (key := (today - timedelta(days=back), clock)) in by_local
        ]
        if len(before) < ESTIMATE_MIN_VALUES:
            continue
        result.append(
            PriceInput(
                start=moment,
                import_ct=median(item.import_ct for item in before),
                feed_in_ct=before[0].feed_in_ct,
                origin="estimated",
            )
        )
    return result


def end_soc_value_ct(data: ScenarioFile, delta_kwh: float) -> float:
    """Wert von ``delta_kwh`` mehr Energie im Akku am Ende des Laufs:
    ``delta · η_dis · Median der Bezugspreise des letzten lokalen Tages`` in ct."""
    last_day = _local(data.slots[-1].start).date()
    prices = [item.import_ct for item in data.slots if _local(item.start).date() == last_day]
    return delta_kwh * eta(data.planning.roundtrip_pct) * median(prices)


def _battery_state(data: ScenarioFile, soc_kwh: float) -> BatteryState:
    battery = data.battery
    return BatteryState(
        soc_pct=soc_kwh / battery.usable_kwh * 100,
        usable_kwh=battery.usable_kwh,
        reserve_pct=battery.reserve_soc_pct,
        soc_max_pct=battery.soc_max_pct,
        max_charge_w=battery.max_charge_w,
        max_discharge_w=battery.max_discharge_w,
        limit_now_charge_w=None,
        limit_now_discharge_w=None,
        max_grid_charge_setpoint_w=battery.max_grid_charge_setpoint_w,
        grid_max_import_w=battery.grid_max_import_w,
    )


@dataclass(frozen=True)
class _Planned:
    """Ein Plan des Laufs mit seiner Nachrechnung (für die Prüfung von Regel R)."""

    plan: Plan
    steps: list[SimStep]
    index: dict[datetime, int]


def _plan_at(
    data: ScenarioFile,
    planning: PlanningSettings,
    index: int,
    soc_kwh: float,
    month_peak_kw: float | None,
    previous: Plan | None,
) -> _Planned | None:
    """Plan zum Slot ``index`` (Minute 1 nach Slotbeginn); ``None`` ohne Eingangsdaten."""
    now = ensure_utc(data.slots[index].start) + PLAN_DELAY
    horizon = data.slots[index : index + MAX_SLOTS]
    pv_w: dict[datetime, float] = {}
    load_w: dict[datetime, float] = {}
    for item in horizon:
        moment = ensure_utc(item.start)
        forecast = forecast_at(item, lead=moment - now)
        pv_w[moment] = forecast.pv
        load_w[moment] = forecast.load
    try:
        inputs = assemble_inputs(
            now=now,
            trigger="start" if previous is None else "slot",
            prices=price_inputs(data, now),
            pv_w=pv_w,
            load_w=load_w,
            battery=_battery_state(data, soc_kwh),
            planning=planning,
            tariff=data.tariff,
            month_peak_kw=month_peak_kw,
            previous=previous,
            settings_version=1,
        )
    except PlanningUnavailable:
        return None
    plan = plan_once(inputs, solve(inputs))
    params = params_of(inputs)
    soc0_kwh = inputs.battery.soc_pct / 100 * params.capacity_kwh
    steps = simulate(soc0_kwh, sim_slots(inputs), [s.intent for s in plan.slots], params)
    return _Planned(plan, steps, {s.start: i for i, s in enumerate(plan.slots)})


def _month(moment: datetime) -> str:
    return _local(moment).strftime("%Y-%m")


def _power_ct(data: ScenarioFile, peaks_kw: Mapping[str, float]) -> float:
    """Leistungspreis des Laufs in ct: je Monat der Daten auf den höchsten Bezug im Monat,
    im ersten Monat mindestens die Spitze vor dem Lauf."""
    first_month = _month(data.slots[0].start)
    first_day: dict[str, date] = {}
    for item in data.slots:
        first_day.setdefault(_month(item.start), _local(item.start).date())
    total = 0.0
    for month, day in first_day.items():
        rate = power_rate(day, data.tariff)
        if rate is None:
            continue
        peak = peaks_kw.get(month, 0.0)
        if month == first_month and data.month_peak_kw is not None:
            peak = max(peak, data.month_peak_kw)
        total += power_cost_eur(peak, rate) * 100
    return total


def _peaks_kw(data: ScenarioFile, steps: Sequence[SimStep]) -> dict[str, float]:
    peaks: dict[str, float] = {}
    for item, result in zip(data.slots, steps, strict=True):
        month = _month(item.start)
        peaks[month] = max(peaks.get(month, 0.0), result.import_w / 1000)
    return peaks


def _month_peak_kw(data: ScenarioFile, now: datetime, peaks: Mapping[str, float]) -> float | None:
    """Monatsspitze zum Zeitpunkt ``now``: höchster Bezug des Laufs im Monat, im ersten Monat
    mindestens die Spitze vor dem Lauf; ``None``, solange keine bekannt ist."""
    month = _month(now)
    values = [peaks[month]] if month in peaks else []
    if month == _month(data.slots[0].start) and data.month_peak_kw is not None:
        values.append(data.month_peak_kw)
    return max(values) if values else None


def run_scenario(
    data: ScenarioFile, *, step_slots: int = 1, grid_charge: bool | None = None
) -> ScenarioResult:
    """Lässt das Szenario laufen; ``grid_charge`` ersetzt, wenn angegeben, den Schalter
    „Netzladen erlaubt“ der Datei."""
    if step_slots < 1:
        raise ValueError("step_slots muss mindestens 1 sein")
    planning = data.planning
    if grid_charge is not None:
        planning = planning.model_copy(update={"grid_charge_allowed": grid_charge})
    battery = data.battery
    capacity = battery.usable_kwh
    soc0 = battery.start_soc_pct / 100 * capacity
    efficiency = eta(planning.roundtrip_pct)
    params = BatteryParams(
        capacity_kwh=capacity,
        lo_kwh=min(battery.reserve_soc_pct / 100 * capacity, soc0),
        hi_kwh=max(battery.soc_max_pct / 100 * capacity, soc0),
        eta_ch=efficiency,
        eta_dis=efficiency,
    )
    actual = [
        SimSlot(
            start=ensure_utc(item.start),
            pv_w=item.pv_w,
            load_w=item.load_w,
            import_ct=item.import_ct,
            feed_in_ct=item.feed_in_ct,
            max_charge_w=battery.max_charge_w,
            max_discharge_w=battery.max_discharge_w,
        )
        for item in data.slots
    ]
    baseline = simulate(soc0, actual, [_OWN] * len(actual), params)

    soc = soc0
    current: _Planned | None = None
    peaks: dict[str, float] = {}
    steps: list[SimStep] = []
    applied: list[BatteryIntent] = []
    violation: list[bool] = []
    plans: list[Plan] = []
    for index, slot in enumerate(actual):
        if index % step_slots == 0:
            now = slot.start + PLAN_DELAY
            planned = _plan_at(
                data,
                planning,
                index,
                soc,
                _month_peak_kw(data, now, peaks),
                current.plan if current is not None else None,
            )
            if planned is not None:
                current = planned
                plans.append(planned.plan)
        intent, broken = _OWN, False
        if current is not None and current.plan.valid_until > slot.start:
            position = current.index.get(slot.start)
            if position is not None:
                intent = current.plan.slots[position].intent
                broken = (
                    intent.kind is BatteryIntentKind.NETZLADEN
                    and export_after_kwh(current.steps, position) > R1_TOL_KWH
                )
        result = step(soc, slot, intent, params)
        steps.append(result)
        applied.append(intent)
        violation.append(broken)
        soc = result.soc_end_kwh
        month = _month(slot.start)
        peaks[month] = max(peaks.get(month, 0.0), result.import_w / 1000)

    days = _days(data, baseline, steps, applied, violation, plans)
    baseline_power = _power_ct(data, _peaks_kw(data, baseline))
    optimizer_power = _power_ct(data, peaks)
    baseline_ct = sum(item.cost_ct for item in baseline) + baseline_power
    optimizer_ct = sum(item.cost_ct for item in steps) + optimizer_power
    end_delta = steps[-1].soc_end_kwh - baseline[-1].soc_end_kwh
    return ScenarioResult(
        name=data.name,
        step_slots=step_slots,
        days=days,
        baseline_ct=baseline_ct,
        optimizer_ct=optimizer_ct,
        adjusted_optimizer_ct=optimizer_ct - end_soc_value_ct(data, end_delta),
        baseline_power_ct=baseline_power,
        optimizer_power_ct=optimizer_power,
        r1_violations=sum(violation),
        charge_before_export_kwh=_charge_before_export_kwh(data, steps),
        min_soc_kwh=min(soc0, *(item.soc_end_kwh for item in steps)),
        reserve_kwh=battery.reserve_soc_pct / 100 * capacity,
        plans=len(plans),
        fallbacks=sum(1 for plan in plans if plan.source == "fallback"),
    )


def _day_groups(data: ScenarioFile) -> dict[date, list[int]]:
    groups: dict[date, list[int]] = {}
    for index, item in enumerate(data.slots):
        groups.setdefault(_local(item.start).date(), []).append(index)
    return groups


def _days(
    data: ScenarioFile,
    baseline: Sequence[SimStep],
    steps: Sequence[SimStep],
    applied: Sequence[BatteryIntent],
    violation: Sequence[bool],
    plans: Sequence[Plan],
) -> list[DayResult]:
    result: list[DayResult] = []
    for day, indices in _day_groups(data).items():
        result.append(
            DayResult(
                day=day,
                baseline_ct=sum(baseline[i].cost_ct for i in indices),
                optimizer_ct=sum(steps[i].cost_ct for i in indices),
                grid_charge_kwh=sum(steps[i].charge_grid_w for i in indices) * SLOT_H / 1000,
                hold_slots=sum(1 for i in indices if applied[i].kind is BatteryIntentKind.HALTEN),
                r1_violations=sum(1 for i in indices if violation[i]),
                fallbacks=sum(
                    1
                    for plan in plans
                    if plan.source == "fallback" and _local(plan.created_at).date() == day
                ),
            )
        )
    return result


def _charge_before_export_kwh(data: ScenarioFile, steps: Sequence[SimStep]) -> float:
    """Netzgeladene Energie in Slots, nach denen am selben lokalen Tag (den Slot eingeschlossen)
    tatsächlich mehr als ``R1_TOL_KWH`` eingespeist wurde."""
    total = 0.0
    for indices in _day_groups(data).values():
        day_steps = [steps[i] for i in indices]
        for position, item in enumerate(day_steps):
            if item.charge_grid_w > 0 and export_after_kwh(day_steps, position) > R1_TOL_KWH:
                total += item.charge_grid_w * SLOT_H / 1000
    return total
