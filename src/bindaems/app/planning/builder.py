"""Plan aus Absichten (Plan 3b, Task 7).

Rechnet die Absichten mit dem Akkumodell nach und füllt daraus Slots, Budget (Präzisierungen 10
und 11), Kosten, Vergleichskosten „Victron allein“ und die Gründe der Erklärung. Reine Rechnung.

Mit geplanten Autos (Plan 4b, Präzisierung 14) bekommt jeder Slot die Ladeleistung je Auto
(``PlanSlot.ev_w``) und der Plan eine Zusammenfassung je Auto (``Plan.vehicles``).

Akku ins Auto (Plan Akku ins Auto, wenn er übrig ist): Der Akkuanteil je Auto
(``PlanSlot.ev_battery_w``) geht in die Nachrechnung und damit in Kosten, Budget und Plan-Schwelle,
nicht in die Vergleichskosten (Entscheidung 10). Ein Plan des Optimierers sagt je Slot, ob Autos in
Sofort übrigen Akku bekommen (``PlanSlot.now_battery``, ``now_battery_flags``); der Ersatzplan nie.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from math import ceil
from typing import Final

from bindaems.app.planning.battery_model import (
    SLOT_H,
    SimStep,
    params_of,
    sim_slots,
    simulate,
)
from bindaems.app.planning.explain import reasons_for
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.spare import now_battery_flags
from bindaems.app.planning.vehicles import EV_ON_W, KWH_EPS, cars_charging
from bindaems.shared.plan import (
    PLAN_VALID,
    BatteryIntent,
    Plan,
    PlanSlot,
    PlanSource,
    PlanVehicle,
    SolverStatus,
)
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, ensure_utc

REACHABLE_TOL_KWH: Final = 0.1  # bis zu dieser Fehlmenge gilt das Ziel als erreichbar
_TENTH_EPS = 1e-9  # Gleitkommafehler vor dem Aufrunden auf 0,1 kW
_SOC_EPS = 1e-9  # Gleitkommafehler vor dem Aufrunden auf ganze Prozent und beim Bedarf null


def _resimulate(
    inputs: PlanInputs,
    intents: Sequence[BatteryIntent],
    ev_w: Sequence[Mapping[str, float]] = (),
    ev_battery_w: Sequence[Mapping[str, float]] = (),
) -> list[SimStep]:
    params = params_of(inputs)
    soc0_kwh = inputs.battery.soc_pct / 100 * params.capacity_kwh
    return simulate(soc0_kwh, sim_slots(inputs, ev_w, ev_battery_w), intents, params)


def baseline_cost_ct(inputs: PlanInputs, ev_w: Sequence[Mapping[str, float]] = ()) -> float:
    """Energiekosten in ct, wenn der Akku in allen Slots ``EIGENVERBRAUCH`` fährt („Victron
    allein“), ab demselben SOC. Die Autos laden darin wie im Plan (``ev_w``), aber nur aus PV und
    Netz, nie aus dem Akku (Entscheidung 10): Der Vergleich zeigt, was der Plan des Akkus spart."""
    steps = _resimulate(inputs, [BatteryIntent() for _ in inputs.slots], ev_w)
    return sum(item.cost_ct for item in steps)


def _car_powers(inputs: PlanInputs, ev_w: Sequence[Mapping[str, float]]) -> list[dict[str, float]]:
    """Ladeleistung je Auto und Slot ab ``EV_ON_W``; ohne Angabe lädt kein Auto."""
    if not ev_w:
        return [{} for _ in inputs.slots]
    if len(ev_w) != len(inputs.slots):
        raise ValueError("Ladeleistung der Autos muss zu den Slots passen")
    return [cars_charging(powers) for powers in ev_w]


def vehicle_summary(
    inputs: PlanInputs,
    ev_w: Sequence[Mapping[str, float]],
    shortfall_kwh: Mapping[str, float] | None = None,
) -> list[PlanVehicle]:
    """Zusammenfassung je geplantem Auto in der Reihenfolge der Priorität (Präzisierung 14).

    Alle Energien auf der Wallbox-Seite (Ruling R10): ``planned_kwh = Σ e · Δ``. Der erwartete
    Ladestand ist der an der Abfahrt: ``soc_start + geladen · η / usable · 100``, höchstens 100.
    ``geladen`` ist ``planned_kwh``, bei einer Abfahrt hinter dem Horizont zusätzlich, was der core
    danach mit voller Leistung noch lädt (``min(need_kwh − planned_kwh, max_w · Stunden nach dem
    Horizont)``, Ruling R24). Deckt das den Bedarf (``geladen ≥ need_kwh``), ist der erwartete
    Ladestand höchstens das Ziel (oder der Ladestand, wenn er schon darüber liegt), denn am Ziel
    stoppt der core, auch wenn der letzte Slot mit ``min_w`` mehr vorsieht. ``finish_at`` ist das
    Ende des letzten Slots mit Laden (``e ≥ EV_ON_W``); bleibt bei einer Abfahrt hinter dem
    Horizont nach dem Plan ein Bedarf, lädt das Auto erst danach fertig, und ``finish_at`` ist
    ``None`` (Ruling R20). Die Fehlmenge kommt aus ``shortfall_kwh`` (Optimierer); fehlt das Auto
    dort, ist sie ``horizon_need_kwh`` minus ``planned_kwh``. ``reachable`` heißt Fehlmenge
    höchstens ``REACHABLE_TOL_KWH``; bei einer Abfahrt hinter dem Horizont gilt sie gegen den
    Bedarf im Horizont. Ein erwartetes Auto (Plan 5b) behält ``expected`` und ``arrival_at``;
    sein Ladestand zu Beginn ist der bei der Ankunft.
    """
    powers = _car_powers(inputs, ev_w)
    known = shortfall_kwh or {}
    horizon_end = inputs.slots[-1].start + SLOT if inputs.slots else None
    result: list[PlanVehicle] = []
    for car in inputs.vehicles:
        watts = [slot.get(car.key, 0.0) for slot in powers]
        planned = sum(watts) * SLOT_H / 1000
        shortfall = known.get(car.key)
        if shortfall is None:
            shortfall = max(0.0, car.horizon_need_kwh - planned)
        after_h = 0.0
        if horizon_end is not None and car.departure_at > horizon_end:
            after_h = (car.departure_at - horizon_end).total_seconds() / 3600
        charged = planned + min(max(0.0, car.need_kwh - planned), car.max_w / 1000 * after_h)
        expected = car.soc_pct + charged * car.efficiency / car.usable_kwh * 100
        if charged >= car.need_kwh - KWH_EPS:
            expected = min(expected, max(car.target_soc_pct, car.soc_pct))
        charging = [index for index, w in enumerate(watts) if w >= EV_ON_W]
        remains = after_h > 0 and planned < car.need_kwh - KWH_EPS
        finish_at = inputs.slots[charging[-1]].start + SLOT if charging and not remains else None
        result.append(
            PlanVehicle(
                key=car.key,
                name=car.name,
                wallbox=car.wallbox,
                target_soc_pct=car.target_soc_pct,
                departure_at=car.departure_at,
                soc_start_pct=car.soc_pct,
                soc_assumed=car.soc_assumed,
                limit_pct=car.limit_pct,
                need_kwh=car.need_kwh,
                planned_kwh=planned,
                shortfall_kwh=shortfall,
                expected_soc_pct=min(100.0, expected),
                finish_at=finish_at,
                reachable=shortfall <= REACHABLE_TOL_KWH + KWH_EPS,
                expected=car.expected,
                arrival_at=car.arrival_at,
            )
        )
    return result


def _month_of(inputs: PlanInputs, index: int) -> str:
    return ensure_utc(inputs.slots[index].start).astimezone(LOCAL_TZ).strftime("%Y-%m")


def _ceil_tenth(value_kw: float) -> float:
    """Auf 0,1 kW aufgerundet; ein Gleitkommafehler knapp über einem Zehntel rundet nicht auf."""
    return ceil(value_kw * 10 - _TENTH_EPS) / 10


def _budgets(
    inputs: PlanInputs,
    steps: Sequence[SimStep],
    *,
    source: PlanSource,
    optimizer_peaks_kw: Mapping[str, float] | None,
) -> list[float | None]:
    """Budget je Slot (akzeptierte Monatsspitze in kW) für Monate mit Leistungspreis.

    Eine Formel für Ersatzplan und Optimierer (Präzisierungen 10 und 11, Gesamtreview I2): auf
    0,1 kW aufgerundet das Größte aus ``peak0_kw`` (bisherige Monatsspitze, mindestens
    ``minimum_kw``), der Spitze des Optimierers für den Monat (nur Optimierer), dem höchsten Bezug
    der Nachrechnung im Monat und ``peak_target_kw`` (ohne Wert 0). So liegt das Budget nie unter
    dem Bezug, den der Plan selbst vorsieht. Ist es dann noch 0 (Monatsbeginn ohne Mindestwert und
    ohne Bezug), gilt ``car_floor_kw``: Darunter könnte der Wächter des core ein ladendes Auto nur
    bis zu seinem kleinsten Strom drosseln und alarmieren.
    """
    peaks = optimizer_peaks_kw if source == "optimizer" and optimizer_peaks_kw else {}
    budget_of: dict[str, float] = {}
    for entry in inputs.peak_months:
        month = entry.month
        import_kw = max(
            (
                item.import_w / 1000
                for index, item in enumerate(steps)
                if _month_of(inputs, index) == month
            ),
            default=0.0,
        )
        budget = _ceil_tenth(
            max(entry.peak0_kw, peaks.get(month, 0.0), import_kw, inputs.peak_target_kw or 0.0)
        )
        budget_of[month] = budget if budget > 0 else _ceil_tenth(inputs.car_floor_kw)
    return [budget_of.get(_month_of(inputs, index)) for index in range(len(inputs.slots))]


def _guard_steps(
    inputs: PlanInputs,
    intents: Sequence[BatteryIntent],
    powers: Sequence[Mapping[str, float]],
    battery_powers: Sequence[Mapping[str, float]],
    steps: list[SimStep],
) -> list[SimStep]:
    """Nachrechnung für das Budget des Wächters (Ruling F-1): ohne die Ladeleistung erwarteter
    Autos (Plan 5b), mit dem Akkuanteil der übrigen. Das Budget geht an den core, und ein Auto,
    das nur die Anwesenheitsprognose kennt, darf es vor seiner Ankunft nicht anheben; steckt es
    an, rechnet der nächste Plan es als angestecktes Auto mit. Ohne erwartete Autos ist es die
    Nachrechnung des Plans."""
    expected = {car.key for car in inputs.vehicles if car.expected}
    if not expected:
        return steps

    def known(slots: Sequence[Mapping[str, float]]) -> list[dict[str, float]]:
        return [{key: w for key, w in slot.items() if key not in expected} for slot in slots]

    return _resimulate(inputs, intents, known(powers), known(battery_powers))


def support_thresholds(
    slots: Sequence[PlanSlot], reserve_pct: float
) -> list[tuple[float, datetime]]:
    """Plan-Schwelle der Akku-Unterstützung je Slot und der Zeitpunkt, bis zu dem der Plan den Akku
    braucht (Präzisierung 6).

    Mit der Entladung ``d_t`` eines Slots in Prozentpunkten (``soc_start_pct - soc_end_pct``) gilt
    rückwärts ``R_N = 0`` und ``R_t = max(0, d_t + R_{t+1})``; die Schwelle ist
    ``min(100, ceil(reserve_pct + R_t))``. „Bis“ ist der Beginn des ersten späteren Slots mit
    ``R = 0``, sonst das Horizontende.
    """
    count = len(slots)
    if count == 0:
        return []
    starts = [slot.start for slot in slots] + [slots[-1].start + SLOT]
    need = [0.0] * (count + 1)
    until = [starts[count]] * (count + 1)
    for index in range(count - 1, -1, -1):
        drop = slots[index].soc_start_pct - slots[index].soc_end_pct
        need[index] = max(0.0, drop + need[index + 1])
        until[index] = starts[index + 1] if need[index + 1] <= _SOC_EPS else until[index + 1]
    return [
        (min(100.0, float(ceil(reserve_pct + need[index] - _SOC_EPS))), until[index])
        for index in range(count)
    ]


def build_plan(
    inputs: PlanInputs,
    intents: Sequence[BatteryIntent],
    *,
    source: PlanSource,
    solver_status: SolverStatus | None,
    fallback_reason: str | None = None,
    issues: Sequence[str] = (),
    optimizer_peaks_kw: Mapping[str, float] | None = None,
    ev_w: Sequence[Mapping[str, float]] = (),
    shortfall_kwh: Mapping[str, float] | None = None,
    ev_battery_w: Sequence[Mapping[str, float]] = (),
) -> Plan:
    """Plan aus den Absichten: Nachrechnung, Budget, Gründe, Kosten und Vergleichskosten.

    ``ev_w`` ist die Ladeleistung je geplantem Auto und Slot (W), ``ev_battery_w`` der Teil davon
    aus dem Akku (Akku ins Auto), ``shortfall_kwh`` die Fehlmenge je Auto aus dem Optimierer
    (``vehicle_summary``); die Nachrechnung, das Budget und die Vergleichskosten rechnen die Autos
    mit, den Akkuanteil nur die Nachrechnung und das Budget (Entscheidung 10). Das Budget rechnet
    ohne die Leistung erwarteter Autos (Ruling F-1, ``_guard_steps``). Nur ein Plan des
    Optimierers (``source``) erlaubt übrigen Akku für Sofort (``now_battery_flags``).
    """
    if len(intents) != len(inputs.slots):
        raise ValueError("Absichten und Slots müssen gleich viele sein")
    params = params_of(inputs)
    powers = _car_powers(inputs, ev_w)
    battery_powers = _car_powers(inputs, ev_battery_w)
    steps = _resimulate(inputs, intents, powers, battery_powers)
    now_battery = (
        now_battery_flags(inputs, intents, steps)
        if source == "optimizer"
        else [False] * len(intents)
    )
    budgets = _budgets(
        inputs,
        _guard_steps(inputs, intents, powers, battery_powers, steps),
        source=source,
        optimizer_peaks_kw=optimizer_peaks_kw,
    )
    vehicles = vehicle_summary(inputs, powers, shortfall_kwh)

    drafts: list[PlanSlot] = []
    for slot, intent, item, budget, cars, from_battery, now in zip(
        inputs.slots, intents, steps, budgets, powers, battery_powers, now_battery, strict=True
    ):
        drafts.append(
            PlanSlot(
                start=slot.start,
                intent=intent,
                soc_start_pct=item.soc_start_kwh / params.capacity_kwh * 100,
                soc_end_pct=item.soc_end_kwh / params.capacity_kwh * 100,
                pv_w=slot.pv_w,
                load_w=slot.load_w,
                import_w=item.import_w,
                export_w=item.export_w,
                charge_pv_w=item.charge_pv_w,
                charge_grid_w=item.charge_grid_w,
                discharge_w=item.discharge_w,
                import_ct=slot.import_ct,
                feed_in_ct=slot.feed_in_ct,
                estimated=slot.origin == "estimated",
                cost_ct=item.cost_ct,
                budget_kw=budget,
                ev_w=cars,
                ev_battery_w=from_battery,
                now_battery=now,
                reasons=[],
            )
        )
    thresholds = support_thresholds(drafts, inputs.battery.reserve_pct)
    reasons = reasons_for(
        inputs,
        intents,
        steps,
        budgets,
        fallback=source == "fallback",
        thresholds=thresholds,
        ev_w=powers,
        vehicles=vehicles,
        ev_battery_w=battery_powers,
        now_battery=now_battery,
    )
    slots = [
        draft.model_copy(update={"support_soc_pct": threshold, "reasons": slot_reasons})
        for draft, (threshold, _), slot_reasons in zip(drafts, thresholds, reasons, strict=True)
    ]
    return Plan(
        created_at=inputs.created_at,
        trigger=inputs.trigger,
        source=source,
        solver_status=solver_status,
        fallback_reason=fallback_reason,
        valid_until=inputs.created_at + PLAN_VALID,
        settings_version=inputs.settings_version,
        grid_charge_allowed=inputs.planning.grid_charge_allowed,
        reserve_pct=inputs.battery.reserve_pct,
        soc_max_pct=inputs.battery.soc_max_pct,
        month_peak_kw=inputs.month_peak_kw,
        power_price=bool(inputs.peak_months),
        expected_cost_ct=sum(item.cost_ct for item in steps),
        baseline_cost_ct=baseline_cost_ct(inputs, powers),
        issues=list(issues),
        vehicles=vehicles,
        slots=slots,
    )
