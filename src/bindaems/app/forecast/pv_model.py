"""PV-Modell (Spec 10.1): P = kWp · GTI/1000 · PR · (1 + gamma · (T_zelle − 25 °C))."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from bindaems.shared.settings import PvModelSettings

STC_IRRADIANCE_W_M2 = 1000.0
STC_CELL_C = 25.0


def cell_temperature_c(temp_air_c: float, gti_w_m2: float, noct_c: float) -> float:
    """NOCT-Näherung: T_zelle = T_luft + GTI · (NOCT − 20) / 800."""
    return temp_air_c + gti_w_m2 * (noct_c - 20.0) / 800.0


def plane_power_w(gti_w_m2: float, temp_air_c: float, kwp: float, model: PvModelSettings) -> float:
    cell = cell_temperature_c(temp_air_c, gti_w_m2, model.noct_c)
    derate = 1 + model.temp_coeff_pct_per_k / 100.0 * (cell - STC_CELL_C)
    power = kwp * 1000.0 * gti_w_m2 / STC_IRRADIANCE_W_M2 * model.performance_ratio * derate
    return max(0.0, power)


def combine(
    planes: Sequence[Mapping[datetime, float]], inverter_ac_max_w: float
) -> dict[datetime, float]:
    """Summe der Flächen je Slot (nur Slots aller Flächen), begrenzt auf den Wechselrichter."""
    if not planes:
        return {}
    common = set(planes[0]).intersection(*planes[1:])
    return {
        slot: min(max(sum(plane[slot] for plane in planes), 0.0), inverter_ac_max_w)
        for slot in sorted(common)
    }
