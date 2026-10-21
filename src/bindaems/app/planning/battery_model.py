"""Akkumodell im Viertelstundenraster (Plan 3b, Präzisierung 8).

Reine Rechnung ohne Zugriff auf Zeit oder Konfiguration: Sie rechnet einen Plan nach,
prüft ihn auf Plausibilität, bildet den Ersatzplan und wertet die Szenarien aus.
Leistungen sind Mittelwerte über den Slot in W, Energien in kWh.

Geplante Autos (Plan 4b, Präzisierung 12) stehen als Summe ihrer Ladeleistung im Slot
(``SimSlot.ev_w``): PV deckt zuerst das Haus, dann die Autos, dann den Akku; der Akku entlädt nur
für das Haus, der Rest der Autos kommt aus dem Netz.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import sqrt
from typing import TYPE_CHECKING, Final

from bindaems.shared.plan import BatteryIntent, BatteryIntentKind

if TYPE_CHECKING:  # ``inputs`` importiert ``eta`` von hier
    from bindaems.app.planning.inputs import PlanInputs

SLOT_H: Final = 0.25
LIMIT_NOW_SLOTS: Final = (
    4  # so viele Slots gelten die BMS-Grenzen (CCL/DCL); ``inputs`` führt sie mit
)


@dataclass(frozen=True)
class BatteryParams:
    """Grenzen und Wirkungsgrade des Akkus; ``lo``/``hi`` sind Reserve und Obergrenze in kWh."""

    capacity_kwh: float
    lo_kwh: float
    hi_kwh: float
    eta_ch: float
    eta_dis: float


@dataclass(frozen=True)
class SimSlot:
    """Eingabe eines Slots: Leistungen in W, Preise in ct/kWh (Einspeisepreis kann fehlen).

    ``load_w`` ist das Haus ohne Autos, ``ev_w`` die Ladeleistung aller geplanten Autos.
    """

    start: datetime
    pv_w: float
    load_w: float
    import_ct: float
    feed_in_ct: float | None
    max_charge_w: float
    max_discharge_w: float
    ev_w: float = 0.0


@dataclass(frozen=True)
class SimStep:
    """Ergebnis eines Slots; ``cost_ct`` ist Bezug minus Einspeisung.

    ``ev_pv_w`` und ``ev_grid_w`` sind PV und Netz in die Autos; ``import_w`` enthält
    ``ev_grid_w``.
    """

    soc_start_kwh: float
    soc_end_kwh: float
    import_w: float
    export_w: float
    charge_pv_w: float
    charge_grid_w: float
    discharge_w: float
    cost_ct: float
    ev_pv_w: float = 0.0
    ev_grid_w: float = 0.0

    @property
    def battery_w(self) -> float:
        """Netto-Akkuleistung: positiv beim Laden, negativ beim Entladen."""
        return self.charge_pv_w + self.charge_grid_w - self.discharge_w


def eta(roundtrip_pct: float) -> float:
    """Wirkungsgrad je Richtung aus dem Roundtrip-Wirkungsgrad in Prozent."""
    return sqrt(roundtrip_pct / 100)


def step(soc_kwh: float, slot: SimSlot, intent: BatteryIntent, params: BatteryParams) -> SimStep:
    """Rechnet einen Slot mit der gegebenen Absicht aus dem SOC ``soc_kwh``.

    PV deckt zuerst das Haus, dann die Autos (``ev_w``), dann den Akku; der Akku entlädt nur für
    das Haus, und was den Autos an PV fehlt, kommt aus dem Netz.
    """
    room_w = max(0.0, params.hi_kwh - soc_kwh) / params.eta_ch / SLOT_H * 1000
    deliverable_w = max(0.0, soc_kwh - params.lo_kwh) * params.eta_dis / SLOT_H * 1000
    ev_pv_w = min(max(0.0, slot.pv_w - slot.load_w), slot.ev_w)
    ev_grid_w = slot.ev_w - ev_pv_w
    surplus_w = slot.pv_w - slot.load_w - ev_pv_w

    charge_pv_w = 0.0
    discharge_w = 0.0
    export_w = 0.0
    import_w = ev_grid_w
    if surplus_w >= 0:
        charge_pv_w = min(surplus_w, slot.max_charge_w, room_w)
        export_w = surplus_w - charge_pv_w
    else:
        deficit_w = -surplus_w
        if intent.kind is BatteryIntentKind.EIGENVERBRAUCH:
            discharge_w = min(deficit_w, slot.max_discharge_w, deliverable_w)
        import_w += deficit_w - discharge_w

    charge_grid_w = 0.0
    if intent.kind is BatteryIntentKind.NETZLADEN:
        charge_grid_w = max(
            0.0,
            min(
                intent.grid_charge_w,
                slot.max_charge_w - charge_pv_w,
                room_w - charge_pv_w,
            ),
        )
        import_w += charge_grid_w

    soc_end_kwh = soc_kwh + SLOT_H / 1000 * (
        (charge_pv_w + charge_grid_w) * params.eta_ch - discharge_w / params.eta_dis
    )
    cost_ct = SLOT_H / 1000 * (import_w * slot.import_ct - export_w * (slot.feed_in_ct or 0.0))
    return SimStep(
        soc_start_kwh=soc_kwh,
        soc_end_kwh=soc_end_kwh,
        import_w=import_w,
        export_w=export_w,
        charge_pv_w=charge_pv_w,
        charge_grid_w=charge_grid_w,
        discharge_w=discharge_w,
        cost_ct=cost_ct,
        ev_pv_w=ev_pv_w,
        ev_grid_w=ev_grid_w,
    )


def simulate(
    soc0_kwh: float,
    slots: Sequence[SimSlot],
    intents: Sequence[BatteryIntent],
    params: BatteryParams,
) -> list[SimStep]:
    """Rechnet Slots nacheinander; der End-SOC eines Slots ist der Start des nächsten."""
    if len(slots) != len(intents):
        raise ValueError("Slots und Absichten müssen gleich viele sein")
    steps: list[SimStep] = []
    soc = soc0_kwh
    for slot, intent in zip(slots, intents, strict=True):
        result = step(soc, slot, intent, params)
        steps.append(result)
        soc = result.soc_end_kwh
    return steps


def params_of(inputs: PlanInputs) -> BatteryParams:
    """Akkugrenzen des Laufs (Präzisierung 5): ein SOC außerhalb von Reserve und Obergrenze
    verschiebt die jeweilige Grenze, damit das Modell lösbar bleibt."""
    battery = inputs.battery
    capacity = battery.usable_kwh
    soc0 = battery.soc_pct / 100 * capacity
    efficiency = eta(inputs.planning.roundtrip_pct)
    return BatteryParams(
        capacity_kwh=capacity,
        lo_kwh=min(battery.reserve_pct / 100 * capacity, soc0),
        hi_kwh=max(battery.soc_max_pct / 100 * capacity, soc0),
        eta_ch=efficiency,
        eta_dis=efficiency,
    )


def sim_slots(inputs: PlanInputs, ev_w: Sequence[Mapping[str, float]] = ()) -> list[SimSlot]:
    """Slots für das Akkumodell; in den ersten ``LIMIT_NOW_SLOTS`` gelten zusätzlich die
    BMS-Grenzen, soweit bekannt.

    ``ev_w`` ist die geplante Ladeleistung je Auto und Slot (eine Zuordnung je Slot); jeder Slot
    bekommt ihre Summe. Leer heißt: keine Autos.
    """
    if ev_w and len(ev_w) != len(inputs.slots):
        raise ValueError("Ladeleistung der Autos muss zu den Slots passen")
    battery = inputs.battery
    result: list[SimSlot] = []
    for index, slot in enumerate(inputs.slots):
        max_charge_w = battery.max_charge_w
        max_discharge_w = battery.max_discharge_w
        if index < LIMIT_NOW_SLOTS:
            if battery.limit_now_charge_w is not None:
                max_charge_w = min(max_charge_w, battery.limit_now_charge_w)
            if battery.limit_now_discharge_w is not None:
                max_discharge_w = min(max_discharge_w, battery.limit_now_discharge_w)
        result.append(
            SimSlot(
                start=slot.start,
                pv_w=slot.pv_w,
                load_w=slot.load_w,
                import_ct=slot.import_ct,
                feed_in_ct=slot.feed_in_ct,
                max_charge_w=max_charge_w,
                max_discharge_w=max_discharge_w,
                ev_w=sum(ev_w[index].values()) if ev_w else 0.0,
            )
        )
    return result
