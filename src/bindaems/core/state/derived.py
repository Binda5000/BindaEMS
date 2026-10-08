"""Abgeleitete Größen aus einem Snapshot: Bilanz, Hauslast und Phasenströme.

Es werden nur Werte mit Qualität ``OK`` verwendet. Fehlt eine Eingangsgröße, ist das
Ergebnis ``None`` – geschätzt wird nicht. Vorzeichen: Netz positiv = Bezug, Akku positiv = Laden.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from bindaems.shared.config import Config, EvcsConfig, Phase, TwcConfig
from bindaems.shared.domain import Snapshot

PHASES: tuple[Phase, Phase, Phase] = ("L1", "L2", "L3")
MIN_VOLTAGE_FOR_CURRENT_V = 100.0
_PV_TOTAL = re.compile(r"pv\.(?P<id>[^.]+)\.power_w")


@dataclass(frozen=True)
class Derived:
    grid_w: float | None
    pv_total_w: float | None
    consumption_w: float | None
    battery_ac_w: float | None
    battery_w: float | None
    house_load_w: float | None
    consumption_l: Mapping[Phase, float | None]
    wallbox_w: Mapping[str, float | None]
    wallbox_phase_a: Mapping[str, Mapping[Phase, float]]
    phase_import_a: Mapping[Phase, float | None]
    balance_residual_w: float | None


def number(snap: Snapshot, signal: str) -> float | None:
    """Zahlenwert eines Signals mit Qualität ``OK``, sonst ``None``."""
    value = snap.ok(signal)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _total(values: Iterable[float | None]) -> float | None:
    result = 0.0
    for value in values:
        if value is None:
            return None
        result += value
    return result


def _n(phase: Phase) -> str:
    return phase[1]


def _pv_ids(snap: Snapshot, cfg: Config) -> list[str]:
    """Konfigurierte und gesehene PV-Wechselrichter – alle sind Pflicht-Eingänge."""
    seen = {m["id"] for name in snap.readings if (m := _PV_TOTAL.fullmatch(name))}
    return sorted(seen | set(cfg.victron.instances.pv.values()))


def _wallbox_phase_a(
    snap: Snapshot, name: str, wallbox: EvcsConfig | TwcConfig, nominal_v: float
) -> dict[Phase, float] | None:
    if isinstance(wallbox, TwcConfig):
        currents = {p: number(snap, f"wallbox.{name}.l{_n(p)}.current_a") for p in PHASES}
        if any(current is None for current in currents.values()):
            return None
        return {p: current for p, current in currents.items() if current is not None}
    power = number(snap, f"wallbox.{name}.power_w")
    current = number(snap, f"wallbox.{name}.current_a")
    if current is None:
        current = number(snap, f"wallbox.{name}.gx_current_a")
    if power is None or current is None:
        return None
    if current <= 0:
        return dict.fromkeys(PHASES, 0.0)
    phases = min(3, max(1, round(power / (current * nominal_v))))
    active = set(wallbox.phase_map[:phases])
    return {p: current if p in active else 0.0 for p in PHASES}


def derive(snap: Snapshot, cfg: Config) -> Derived:
    grid_l = {p: number(snap, f"grid.l{_n(p)}.power_w") for p in PHASES}
    voltage = {p: number(snap, f"grid.l{_n(p)}.voltage_v") for p in PHASES}
    grid_w = number(snap, "grid.power_w")
    if grid_w is None:
        grid_w = _total(grid_l.values())

    phase_import_a: dict[Phase, float | None] = {}
    for p in PHASES:
        power, volts = grid_l[p], voltage[p]
        if power is not None and volts is not None and volts >= MIN_VOLTAGE_FOR_CURRENT_V:
            phase_import_a[p] = power / volts  # Vorzeichen aus der Leistung
        else:
            phase_import_a[p] = None

    pv_ids = _pv_ids(snap, cfg)
    pv_ac_w = _total(number(snap, f"pv.{pv}.power_w") for pv in pv_ids)
    pv_ac_l = {
        p: _total(number(snap, f"pv.{pv}.l{_n(p)}.power_w") for pv in pv_ids) for p in PHASES
    }
    pv_dc_w = number(snap, "pv_dc.power_w") or 0.0
    pv_total_w = pv_ac_w + pv_dc_w if pv_ac_w is not None else None

    ac_in = {p: number(snap, f"vebus.l{_n(p)}.ac_in_power_w") for p in PHASES}
    ac_out = {p: number(snap, f"vebus.l{_n(p)}.ac_out_power_w") for p in PHASES}
    ac_in_w, ac_out_w = _total(ac_in.values()), _total(ac_out.values())

    consumption_w = None
    if grid_w is not None and ac_in_w is not None and ac_out_w is not None and pv_ac_w is not None:
        consumption_w = (grid_w - ac_in_w) + (ac_out_w + pv_ac_w)
    consumption_l: dict[Phase, float | None] = {}
    for p in PHASES:
        g, i, o, pv = grid_l[p], ac_in[p], ac_out[p], pv_ac_l[p]
        if g is not None and i is not None and o is not None and pv is not None:
            consumption_l[p] = (g - i) + (o + pv)
        else:
            consumption_l[p] = None

    battery_ac_w = None
    if ac_in_w is not None and ac_out_w is not None:
        battery_ac_w = ac_in_w - ac_out_w
    battery_w = battery_ac_w + pv_dc_w if battery_ac_w is not None else None

    wallbox_w = {name: number(snap, f"wallbox.{name}.power_w") for name in cfg.wallboxes}
    wallbox_phase_a: dict[str, Mapping[Phase, float]] = {}
    for name, wallbox in cfg.wallboxes.items():
        currents = _wallbox_phase_a(snap, name, wallbox, cfg.grid.voltage_nominal_v)
        if currents is not None:
            wallbox_phase_a[name] = currents

    wallboxes_w = _total(wallbox_w.values())
    house_load_w = (
        consumption_w - wallboxes_w
        if consumption_w is not None and wallboxes_w is not None
        else None
    )

    battery_dc_w = number(snap, "battery.power_w")
    balance_residual_w = None
    if (
        grid_w is not None
        and pv_total_w is not None
        and battery_dc_w is not None
        and consumption_w is not None
    ):
        balance_residual_w = grid_w + pv_total_w - battery_dc_w - consumption_w

    return Derived(
        grid_w=grid_w,
        pv_total_w=pv_total_w,
        consumption_w=consumption_w,
        battery_ac_w=battery_ac_w,
        battery_w=battery_w,
        house_load_w=house_load_w,
        consumption_l=consumption_l,
        wallbox_w=wallbox_w,
        wallbox_phase_a=wallbox_phase_a,
        phase_import_a=phase_import_a,
        balance_residual_w=balance_residual_w,
    )
