"""Helfer für Tests rund um den Akkuplan (Plan 3b); spätere Tasks erweitern sie."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

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
from bindaems.app.planning.optimizer import OptimizerResult, SlotFlows
from bindaems.shared.plan import PLAN_VALID, BatteryIntent, Plan, PlanSlot
from bindaems.shared.settings import PlanningSettings, PowerComponent, TariffSettings
from bindaems.shared.timeutil import SLOT, slot_start

DAY = "2026-10-16"
NOW = datetime.fromisoformat("2026-10-16T10:05:00+00:00")


def at(text: str) -> datetime:
    """Zeitpunkt aus ISO-Text mit Zeitzone."""
    return datetime.fromisoformat(text)


def sample_slot(start: str, *, soc_pct: float = 50.0, price_ct: float = 20.0) -> PlanSlot:
    """Slot ab ``start`` (``HH:MM`` als UTC am 16. 10. 2026): Eigenverbrauch, sonst Nullen."""
    return PlanSlot(
        start=at(f"{DAY}T{start}:00+00:00"),
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


def sample_plan(created: str, slots: list[str]) -> Plan:
    """Plan des Optimierers, erzeugt zu ``created`` (ISO mit Zeitzone), mit Slots ``HH:MM``."""
    created_at = at(created)
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
        month_peak_kw=None,
        power_price=False,
        expected_cost_ct=0.0,
        baseline_cost_ct=0.0,
        issues=[],
        slots=[sample_slot(start) for start in slots],
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
) -> PlanInputs:
    """``PlanInputs`` direkt aus Reihen je Slot (Standardakku wie ``state``), ohne Restwert und
    ohne Leistungspreis, wenn nicht angegeben; ``prev_battery_w`` gilt für die ersten Slots,
    ``origins`` ist die Herkunft der Preise je Slot (Standard ``primary``)."""
    if not len(prices) == len(pv) == len(load):
        raise ValueError("Preise, PV und Last müssen gleich lang sein")
    if origins is not None and len(origins) != len(prices):
        raise ValueError("Herkunft je Preis nötig")
    first = at(start)
    planning_values: dict[str, Any] = {"grid_charge_allowed": grid_charge}
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
        slots=[
            SlotInput(
                start=first + i * SLOT,
                import_ct=ct,
                feed_in_ct=feed_in,
                origin=origins[i] if origins is not None else "primary",
                pv_w=pv_w,
                load_w=load_w,
                prev_battery_w=prev[i] if i < len(prev) else None,
            )
            for i, (ct, pv_w, load_w) in enumerate(zip(prices, pv, load, strict=True))
        ],
    )


def flow(**flows: float) -> SlotFlows:
    """Flüsse eines Slots in W; fehlende sind 0. ``soc_end_kwh`` (kWh) ist, wenn nicht
    angegeben, die Energie, die der Akku in diesem Slot netto aufnimmt (ohne Verluste)."""
    values = {"pv_h": 0.0, "pv_b": 0.0, "pv_x": 0.0, "gr_h": 0.0, "gr_b": 0.0, "bt_h": 0.0}
    soc_end_kwh = flows.pop("soc_end_kwh", None)
    values.update(flows)
    if soc_end_kwh is None:
        soc_end_kwh = SLOT_H / 1000 * (values["pv_b"] + values["gr_b"] - values["bt_h"])
    return SlotFlows(soc_end_kwh=soc_end_kwh, **values)


def flows_result(flows: Sequence[SlotFlows]) -> OptimizerResult:
    """Ergebnis des Optimierers mit diesen Flüssen (Status ``optimal``, ohne Spitzen)."""
    return OptimizerResult(
        status="optimal",
        flows=tuple(flows),
        peaks_kw={},
        objective_ct=0.0,
        solve_ms=0,
    )


def resimulate(inputs: PlanInputs, intents: Sequence[BatteryIntent]) -> list[SimStep]:
    """Rechnet die Absichten mit dem Akkumodell nach, ab dem SOC des Laufs."""
    params = params_of(inputs)
    soc0_kwh = inputs.battery.soc_pct / 100 * params.capacity_kwh
    return simulate(soc0_kwh, sim_slots(inputs), intents, params)


def flows_of(steps: Sequence[SimStep], inputs: PlanInputs | None = None) -> OptimizerResult:
    """Flüsse einer Nachrechnung als Ergebnis des Optimierers. ``pv_h`` (PV ins Haus) steht in
    den Schritten nicht; es ist 0, mit ``inputs`` ``min(PV, Last)``."""
    house_w = [min(s.pv_w, s.load_w) for s in inputs.slots] if inputs else [0.0] * len(steps)
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
