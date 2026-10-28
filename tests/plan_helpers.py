"""Helfer für Tests rund um den Akkuplan (Plan 3b); spätere Tasks erweitern sie."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date, datetime
from typing import Any, Final

from bindaems.app.core_link import LiveState
from bindaems.app.planning.battery_model import (
    SLOT_H,
    SimStep,
    params_of,
    sim_slots,
    simulate,
)
from bindaems.app.planning.inputs import (
    BatteryState,
    PeakMonth,
    PlanInputs,
    PriceInput,
    PriceOrigin,
    SlotInput,
    assemble_inputs,
)
from bindaems.app.planning.optimizer import OptimizerResult, SlotFlows, solve
from bindaems.app.planning.vehicles import PlanVehicleInput, need_kwh
from bindaems.app.settings.service import SettingsService
from bindaems.shared.plan import (
    PLAN_VALID,
    BatteryIntent,
    BatteryIntentKind,
    Plan,
    PlanSlot,
    Reason,
)
from bindaems.shared.settings import PlanningSettings, PowerComponent, TariffSettings
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, slot_start

DAY = "2026-10-16"
NOW = datetime.fromisoformat("2026-10-16T10:05:00+00:00")


def at(text: str) -> datetime:
    """Zeitpunkt aus ISO-Text mit Zeitzone."""
    return datetime.fromisoformat(text)


def sample_slot(
    start: str, *, soc_pct: float = 50.0, price_ct: float = 20.0, day: str = DAY
) -> PlanSlot:
    """Slot ab ``start`` (``HH:MM`` als UTC am Tag ``day``, Standard 16. 10. 2026):
    Eigenverbrauch, sonst Nullen."""
    return PlanSlot(
        start=at(f"{day}T{start}:00+00:00"),
        intent=BatteryIntent(),
        soc_start_pct=soc_pct,
        soc_end_pct=soc_pct,
        pv_w=0.0,
        load_w=0.0,
        import_w=0.0,
        export_w=0.0,
        charge_pv_w=0.0,
        charge_grid_w=0.0,
        discharge_w=0.0,
        import_ct=price_ct,
        feed_in_ct=None,
        estimated=False,
        cost_ct=0.0,
        budget_kw=None,
        reasons=[],
    )


# Änderungen an einem Slot für ``sample_plan(intents=…)``: Eigenverbrauch und Halten für die Spitze
EV: Final[dict[str, Any]] = {"intent": BatteryIntent()}
HOLD_REASON: Final[dict[str, Any]] = {
    "intent": BatteryIntent(kind=BatteryIntentKind.HALTEN),
    "reasons": [
        Reason(
            code="HALTEN_FUER_SPITZE",
            params={"soc": 50.0, "von": "18:00", "bis": "20:00", "ct": 38.0},
        )
    ],
}


def sample_plan(
    created: str,
    slots: list[str],
    *,
    soc: tuple[float, float] | None = None,
    month_peak_kw: float | None = None,
    intents: Sequence[Mapping[str, Any]] | None = None,
) -> Plan:
    """Plan des Optimierers, erzeugt zu ``created`` (ISO mit Zeitzone), mit Slots ``HH:MM`` am
    Tag (UTC) der Erzeugung; ``soc`` ist der SOC (%) zu Beginn und Ende jedes Slots. ``intents``
    sind Änderungen je Slot (Felder von ``PlanSlot``, z. B. ``HOLD_REASON``), eine je Slot."""
    created_at = at(created)
    day = created_at.date().isoformat()
    plan_slots = [sample_slot(start, day=day) for start in slots]
    if intents is not None:
        if len(intents) != len(plan_slots):
            raise ValueError("Eine Änderung je Slot nötig")
        plan_slots = [
            slot.model_copy(update=dict(change))
            for slot, change in zip(plan_slots, intents, strict=True)
        ]
    if soc is not None:
        update = {"soc_start_pct": soc[0], "soc_end_pct": soc[1]}
        plan_slots = [slot.model_copy(update=update) for slot in plan_slots]
    return Plan(
        created_at=created_at,
        trigger="start",
        source="optimizer",
        solver_status="optimal",
        fallback_reason=None,
        valid_until=created_at + PLAN_VALID,
        settings_version=1,
        grid_charge_allowed=False,
        reserve_pct=10.0,
        soc_max_pct=100.0,
        month_peak_kw=month_peak_kw,
        power_price=False,
        expected_cost_ct=0.0,
        baseline_cost_ct=0.0,
        issues=[],
        slots=plan_slots,
    )


def price(
    ct: float,
    start: datetime = NOW,
    *,
    origin: PriceOrigin = "primary",
    feed_in: float | None = 5.0,
) -> PriceInput:
    """Preis für den Slot, in dem ``start`` liegt."""
    return PriceInput(start=slot_start(start), import_ct=ct, feed_in_ct=feed_in, origin=origin)


def _prices(
    start: datetime, slots: int, ct: float, feed_in: float | None, origin: PriceOrigin
) -> list[PriceInput]:
    first = slot_start(start)
    return [price(ct, first + i * SLOT, origin=origin, feed_in=feed_in) for i in range(slots)]


def known(
    start: datetime, slots: int, *, ct: float = 20.0, feed_in: float | None = 5.0
) -> list[PriceInput]:
    """``slots`` bekannte Preise (Herkunft ``primary``) ab dem Slot von ``start``."""
    return _prices(start, slots, ct, feed_in, "primary")


def estimated(
    start: datetime, slots: int, *, ct: float = 20.0, feed_in: float | None = 5.0
) -> list[PriceInput]:
    """``slots`` geschätzte Preise ab dem Slot von ``start``."""
    return _prices(start, slots, ct, feed_in, "estimated")


def flat(start: datetime, slots: int, value_w: float) -> dict[datetime, float]:
    """Konstante Prognose in W für ``slots`` Slots ab dem Slot von ``start``."""
    first = slot_start(start)
    return {first + i * SLOT: value_w for i in range(slots)}


def state(**overrides: Any) -> BatteryState:
    """Akku wie in ``deploy/config.example.yaml``: 30 kWh, Reserve 20 %, 10 kW / 12 kW."""
    values: dict[str, Any] = {
        "soc_pct": 50.0,
        "usable_kwh": 30.0,
        "reserve_pct": 20.0,
        "soc_max_pct": 100.0,
        "max_charge_w": 10000.0,
        "max_discharge_w": 12000.0,
        "limit_now_charge_w": None,
        "limit_now_discharge_w": None,
        "max_grid_charge_setpoint_w": 8000.0,
        "grid_max_import_w": (35 - 2) * 230.0 * 3,
    }
    values.update(overrides)
    return BatteryState(**values)


def with_power_price(
    *,
    value_eur: float,
    above_eur: float | None = None,
    valid_from: str | None = None,
    tier_kw: float = 10.0,
    minimum_kw: float = 2.0,
) -> TariffSettings:
    """Standardtarif, dessen Leistungspreis diese (erfundenen) Sätze trägt."""
    base = TariffSettings()
    power = PowerComponent(
        id="power_price",
        name="Leistungspreis",
        value_eur=value_eur,
        value_above_eur=above_eur,
        tier_kw=tier_kw,
        minimum_kw=minimum_kw,
        valid_from=date.fromisoformat(valid_from) if valid_from else None,
    )
    others = [c for c in base.components if c.id != "power_price"]
    return TariffSettings(components=[*others, power])


def previous_plan(*, battery_w: float, slots: int) -> Plan:
    """Plan von 10:05 UTC mit ``slots`` Slots ab 10:00 und Netto-Akkuleistung ``battery_w``."""
    times = [f"{10 + i // 4:02d}:{i % 4 * 15:02d}" for i in range(slots)]
    plan = sample_plan("2026-10-16T10:05:00+00:00", times)
    update = {"discharge_w": -battery_w} if battery_w < 0 else {"charge_pv_w": battery_w}
    return plan.model_copy(
        update={"slots": [slot.model_copy(update=update) for slot in plan.slots]}
    )


def assemble(**overrides: Any) -> PlanInputs:
    """``assemble_inputs`` mit Standardwerten; Überschreibungen unter den Kurznamen ``pv``,
    ``load``, ``battery``, ``prices`` ... (``None`` entfernt die Eingabe)."""
    now = overrides.get("now", NOW)
    values: dict[str, Any] = {
        "now": now,
        "trigger": "start",
        "prices": known(now, slots=200),
        "pv": flat(now, 200, 0.0),
        "load": flat(now, 200, 500.0),
        "battery": state(),
        "planning": PlanningSettings(),
        "tariff": TariffSettings(),
        "month_peak_kw": None,
        "previous": None,
        "settings_version": 1,
    }
    values.update(overrides)
    pv: Mapping[datetime, float] | None = values.pop("pv")
    load: Mapping[datetime, float] | None = values.pop("load")
    return assemble_inputs(pv_w=pv, load_w=load, **values)


SCENARIO_START = "2026-10-16T00:00:00+00:00"


def small_battery(**overrides: Any) -> BatteryState:
    """Akku mit 10 kWh, Reserve 20 %, je 5 kW Laden und Entladen."""
    values: dict[str, Any] = {
        "usable_kwh": 10.0,
        "reserve_pct": 20.0,
        "max_charge_w": 5000.0,
        "max_discharge_w": 5000.0,
        "max_grid_charge_setpoint_w": 8000.0,
    }
    values.update(overrides)
    return state(**values)


def month(m: str, peak0: float) -> PeakMonth:
    """Leistungspreis des Monats ``m`` (``JJJJ-MM``) mit erfundenen Sätzen 300/600 ct je kW."""
    return PeakMonth(
        month=m, peak0_kw=peak0, tier_kw=10.0, below_ct_per_kw=300.0, above_ct_per_kw=600.0
    )


def scenario(
    *,
    prices: list[float],
    pv: list[float],
    load: list[float],
    soc_pct: float = 50.0,
    grid_charge: bool = False,
    battery: BatteryState | None = None,
    feed_in: float | None = None,
    peak_months: list[PeakMonth] | None = None,
    start: str = SCENARIO_START,
    end_value_ct: float = 0.0,
    inertia_ct: float | None = None,
    margin_ct: float | None = None,
    prev_battery_w: list[float] | None = None,
    origins: list[PriceOrigin] | None = None,
    support_soc_pct: float = 70.0,
    peak_target_kw: float | None = None,
    car_floor_kw: float = 0.0,
    vehicles: list[PlanVehicleInput] | None = None,
    pv_p25: list[float] | None = None,
    car_battery: bool = False,
) -> PlanInputs:
    """``PlanInputs`` direkt aus Reihen je Slot (Standardakku wie ``state``), ohne Restwert und
    ohne Leistungspreis, wenn nicht angegeben; ``prev_battery_w`` gilt für die ersten Slots,
    ``origins`` ist die Herkunft der Preise je Slot (Standard ``primary``). ``vehicles`` sind die
    geplanten Autos (``planned_car``), ``pv_p25`` die PV-Prognose P25 je Slot (ohne gilt ``pv``),
    ``car_battery`` der Schalter „Akku ins Auto, wenn übrig“."""
    if not len(prices) == len(pv) == len(load):
        raise ValueError("Preise, PV und Last müssen gleich lang sein")
    if origins is not None and len(origins) != len(prices):
        raise ValueError("Herkunft je Preis nötig")
    if pv_p25 is not None and len(pv_p25) != len(prices):
        raise ValueError("P25 je Slot nötig")
    first = at(start)
    planning_values: dict[str, Any] = {
        "grid_charge_allowed": grid_charge,
        "car_battery_allowed": car_battery,
    }
    if inertia_ct is not None:
        planning_values["inertia_ct"] = inertia_ct
    if margin_ct is not None:
        planning_values["margin_ct"] = margin_ct
    prev = prev_battery_w or []
    base = battery if battery is not None else state()
    return PlanInputs(
        created_at=first,
        trigger="start",
        settings_version=1,
        planning=PlanningSettings(**planning_values),
        battery=base.model_copy(update={"soc_pct": soc_pct}),
        month_peak_kw=None,
        peak_months=peak_months or [],
        end_value_ct=end_value_ct,
        support_soc_pct=support_soc_pct,
        peak_target_kw=peak_target_kw,
        car_floor_kw=car_floor_kw,
        vehicles=vehicles or [],
        slots=[
            SlotInput(
                start=first + i * SLOT,
                import_ct=ct,
                feed_in_ct=feed_in,
                origin=origins[i] if origins is not None else "primary",
                pv_w=pv_w,
                pv_p25_w=pv_p25[i] if pv_p25 is not None else None,
                load_w=load_w,
                prev_battery_w=prev[i] if i < len(prev) else None,
            )
            for i, (ct, pv_w, load_w) in enumerate(zip(prices, pv, load, strict=True))
        ],
    )


def planned_car(**overrides: Any) -> PlanVehicleInput:
    """Ein geplantes Auto wie der Tesla in ``deploy/config.example.yaml`` (75 kWh, 3 × 5 bis 16 A
    an 230 V, η 0,9): 40 % → 80 %, Abfahrt 07:00 Ortszeit am 17. 10. 2026. Ohne Angabe sind
    ``need_kwh`` der Bedarf aus Ladestand und Ziel und ``horizon_need_kwh`` und
    ``grid_allowed_kwh`` derselbe Wert (Abfahrt im Horizont, keine PV)."""
    values: dict[str, Any] = {
        "key": "tesla",
        "name": "Tesla Model 3",
        "wallbox": "twc",
        "soc_pct": 40.0,
        "soc_assumed": False,
        "target_soc_pct": 80.0,
        "limit_pct": None,
        "departure_at": at("2026-10-17T05:00:00+00:00"),
        "efficiency": 0.9,
        "usable_kwh": 75.0,
        "min_w": 5 * 230.0 * 3,
        "max_w": 16 * 230.0 * 3,
    }
    values.update(overrides)
    values.setdefault(
        "need_kwh",
        need_kwh(
            values["soc_pct"], values["target_soc_pct"], values["usable_kwh"], values["efficiency"]
        ),
    )
    values.setdefault("horizon_need_kwh", values["need_kwh"])
    values.setdefault("grid_allowed_kwh", values["horizon_need_kwh"])
    return PlanVehicleInput(**values)


def pv_arc(start: str, slots: int, peak_w: float) -> list[float]:
    """PV je Slot ab ``start`` wie im Property-Test: Sinusbogen über den lokalen Tag von 06:00
    bis 18:00 mit der Spitze ``peak_w``, gerechnet zur Mitte des Slots."""
    values: list[float] = []
    for index in range(slots):
        local = (at(start) + index * SLOT).astimezone(LOCAL_TZ)
        hour = local.hour + local.minute / 60 + 7.5 / 60
        inside = 6.0 <= hour <= 18.0
        values.append(peak_w * math.sin(math.pi * (hour - 6.0) / 12.0) if inside else 0.0)
    return values


def flow(ev: Mapping[str, tuple[float, float]] | None = None, **flows: float) -> SlotFlows:
    """Flüsse eines Slots in W; fehlende sind 0. ``soc_end_kwh`` (kWh) ist, wenn nicht
    angegeben, die Energie, die der Akku in diesem Slot netto aufnimmt (ohne Verluste). ``ev``
    ist ``(pv_ev, gr_ev)`` je geplantem Auto."""
    values = {"pv_h": 0.0, "pv_b": 0.0, "pv_x": 0.0, "gr_h": 0.0, "gr_b": 0.0, "bt_h": 0.0}
    soc_end_kwh = flows.pop("soc_end_kwh", None)
    values.update(flows)
    if soc_end_kwh is None:
        soc_end_kwh = SLOT_H / 1000 * (values["pv_b"] + values["gr_b"] - values["bt_h"])
    return SlotFlows(soc_end_kwh=soc_end_kwh, ev=dict(ev or {}), **values)


def flows_result(
    flows: Sequence[SlotFlows], shortfall_kwh: Mapping[str, float] | None = None
) -> OptimizerResult:
    """Ergebnis des Optimierers mit diesen Flüssen (Status ``optimal``, ohne Spitzen);
    ``shortfall_kwh`` ist die Fehlmenge je geplantem Auto."""
    return OptimizerResult(
        status="optimal",
        flows=tuple(flows),
        peaks_kw={},
        objective_ct=0.0,
        solve_ms=0,
        shortfall_kwh=dict(shortfall_kwh or {}),
    )


def resimulate(inputs: PlanInputs, intents: Sequence[BatteryIntent]) -> list[SimStep]:
    """Rechnet die Absichten mit dem Akkumodell nach, ab dem SOC des Laufs."""
    params = params_of(inputs)
    soc0_kwh = inputs.battery.soc_pct / 100 * params.capacity_kwh
    return simulate(soc0_kwh, sim_slots(inputs), intents, params)


def flows_of(steps: Sequence[SimStep], inputs: PlanInputs) -> OptimizerResult:
    """Flüsse einer Nachrechnung als Ergebnis des Optimierers. ``pv_h`` (PV ins Haus) steht in
    den Schritten nicht; es ist ``min(PV, Last)`` der Slots von ``inputs``."""
    house_w = [min(s.pv_w, s.load_w) for s in inputs.slots]
    return flows_result(
        [
            SlotFlows(
                pv_h=pv_h,
                pv_b=item.charge_pv_w,
                pv_x=item.export_w,
                gr_h=item.import_w - item.charge_grid_w,
                gr_b=item.charge_grid_w,
                bt_h=item.discharge_w,
                soc_end_kwh=item.soc_end_kwh,
            )
            for item, pv_h in zip(steps, house_w, strict=True)
        ]
    )


def sample_inputs() -> PlanInputs:
    """Nicht triviale Eingangsdaten für den Roundtrip: Schätzpreise, Slots ohne Einspeisepreis,
    ein Monat mit Leistungspreis, eine Monatsspitze, ein voriger Plan (zeitzonenbehaftet), P25 und
    ein geplantes Auto."""
    prices = [
        *known(NOW, 12, ct=21.5, feed_in=None),
        *estimated(NOW + 12 * SLOT, 8, ct=18.25, feed_in=4.5),
    ]
    return assemble(
        trigger="prices",
        prices=prices,
        pv=flat(NOW, 20, 1200.0),
        load=flat(NOW, 20, 450.0),
        battery=state(soc_pct=42.5, limit_now_charge_w=3000.0, limit_now_discharge_w=None),
        planning=PlanningSettings(grid_charge_allowed=True, margin_ct=1.5),
        tariff=with_power_price(value_eur=60.0, above_eur=120.0),
        month_peak_kw=4.2,
        previous=previous_plan(battery_w=-800.0, slots=4),
        settings_version=7,
        pv_p25_w=flat(NOW, 20, 900.0),
        vehicles=lambda slots: [planned_car(soc_assumed=True, soc_pct=0.0, limit_pct=70.0)],
    )


def live_signals(live: LiveState, values: Mapping[str, float]) -> None:
    """Verbindet ``live`` mit dem core und setzt diese Signale (Qualität ``ok``); vorhandene
    Signale bleiben."""
    live.connected = True
    state = live.state or {}
    signals = dict(state.get("signals") or {})
    signals.update({name: {"v": value, "ts": "…", "q": "ok"} for name, value in values.items()})
    live.state = {**state, "signals": signals}


def returns(result: OptimizerResult) -> Callable[[PlanInputs], Awaitable[OptimizerResult]]:
    """Solver, der unabhängig von den Eingangsdaten ``result`` liefert."""

    async def solver(inputs: PlanInputs) -> OptimizerResult:
        return result

    return solver


def blocking_solver(
    gate: asyncio.Event, calls: list[PlanInputs]
) -> Callable[[PlanInputs], Awaitable[OptimizerResult]]:
    """Solver, der die Eingangsdaten an ``calls`` hängt, beim ersten Aufruf auf ``gate`` wartet
    und dann im selben Prozess löst."""

    async def solver(inputs: PlanInputs) -> OptimizerResult:
        calls.append(inputs)
        if len(calls) == 1:
            await gate.wait()
        return await asyncio.to_thread(solve, inputs)

    return solver


async def until(predicate: Callable[[], bool], max_wait_s: float = 5.0) -> None:
    """Wartet, bis ``predicate`` gilt; sonst ``AssertionError``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_wait_s
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("Bedingung nicht erfüllt")
        await asyncio.sleep(0.01)


def set_planning(settings: SettingsService, planning: PlanningSettings) -> None:
    """Speichert ``planning`` als neue Version der Einstellungen."""
    current = settings.current()
    settings.update(
        current.settings.model_copy(update={"planning": planning}),
        base_version=current.version,
        actor="test",
        source="ui",
    )


PLANNED_DEPARTURE = "2026-10-17T05:00:00+00:00"  # 07:00 Ortszeit


def vehicle_health(key: str = "tesla", **changes: Any) -> dict[str, Any]:
    """Eintrag eines Fahrzeugs in Health wie ``ChargeController.health`` (Werte erfunden): in
    „Geplant“, angesteckt, Tesla am Wall Connector, e-Golf an der EVCS, 40 % gemessen, Ziel 80 %
    bis ``PLANNED_DEPARTURE``. ``target`` und ``departure_at`` ändern den Teil ``planned``, die
    übrigen Schlüssel den Eintrag."""
    planned = {
        "state": "surplus",
        "target_soc_pct": changes.pop("target", 80.0),
        "departure_at": changes.pop("departure_at", PLANNED_DEPARTURE),
        "need_kwh": 0.0,
        "latest_start": None,
        "plan_w": None,
    }
    entry: dict[str, Any] = {
        "id": key,
        "name": "Tesla Model 3" if key == "tesla" else "e-Golf",
        "mode": "planned",
        "wallbox": "twc" if key == "tesla" else "evcs",
        "plugged": True,
        "charging": False,
        "soc_pct": 40.0,
        "soc_source": "measured",
        "charge_limit_pct": None,
        "planned": planned,
    }
    entry.update(changes)
    return entry


def planned_health(*entries: dict[str, Any], version: int = 3) -> dict[str, Any]:
    """Health mit diesen Fahrzeugen (``vehicle_health``), geregelt mit der Absicht ``version``."""
    return {"intent_version": version, "intent": {"version": version}, "vehicles": list(entries)}
