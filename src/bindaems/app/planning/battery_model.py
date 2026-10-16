"""Akkumodell im Viertelstundenraster (Plan 3b, Präzisierung 8).

Reine Rechnung ohne Zugriff auf Zeit oder Konfiguration: Sie rechnet einen Plan nach,
prüft ihn auf Plausibilität, bildet den Ersatzplan und wertet die Szenarien aus.
Leistungen sind Mittelwerte über den Slot in W, Energien in kWh.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from math import sqrt
from typing import Final

from bindaems.shared.plan import BatteryIntent, BatteryIntentKind

SLOT_H: Final = 0.25


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
    """Eingabe eines Slots: Leistungen in W, Preise in ct/kWh (Einspeisepreis kann fehlen)."""

    start: datetime
    pv_w: float
    load_w: float
    import_ct: float
    feed_in_ct: float | None
    max_charge_w: float
    max_discharge_w: float


@dataclass(frozen=True)
class SimStep:
    """Ergebnis eines Slots; ``cost_ct`` ist Bezug minus Einspeisung."""

    soc_start_kwh: float
    soc_end_kwh: float
    import_w: float
    export_w: float
    charge_pv_w: float
    charge_grid_w: float
    discharge_w: float
    cost_ct: float

    @property
    def battery_w(self) -> float:
        """Netto-Akkuleistung: positiv beim Laden, negativ beim Entladen."""
        return self.charge_pv_w + self.charge_grid_w - self.discharge_w


def eta(roundtrip_pct: float) -> float:
    """Wirkungsgrad je Richtung aus dem Roundtrip-Wirkungsgrad in Prozent."""
    return sqrt(roundtrip_pct / 100)


def step(soc_kwh: float, slot: SimSlot, intent: BatteryIntent, params: BatteryParams) -> SimStep:
    """Rechnet einen Slot mit der gegebenen Absicht aus dem SOC ``soc_kwh``."""
    room_w = max(0.0, params.hi_kwh - soc_kwh) / params.eta_ch / SLOT_H * 1000
    deliverable_w = max(0.0, soc_kwh - params.lo_kwh) * params.eta_dis / SLOT_H * 1000
    surplus_w = slot.pv_w - slot.load_w

    charge_pv_w = 0.0
    discharge_w = 0.0
    export_w = 0.0
    import_w = 0.0
    if surplus_w >= 0:
        charge_pv_w = min(surplus_w, slot.max_charge_w, room_w)
        export_w = surplus_w - charge_pv_w
    else:
        deficit_w = -surplus_w
        if intent.kind is BatteryIntentKind.EIGENVERBRAUCH:
            discharge_w = min(deficit_w, slot.max_discharge_w, deliverable_w)
        import_w = deficit_w - discharge_w

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
