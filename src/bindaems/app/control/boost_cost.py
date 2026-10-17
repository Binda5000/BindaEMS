"""Mehrkosten eines Boosts: der Leistungspreis, den die volle Ladeleistung des Autos kosten
kann (Plan 3c, Präzisierung 14; Spec 7.4: „Zusatz-kW × Monatssatz“)."""

from datetime import date
from typing import Any

from bindaems.app.tariff.calc import power_cost_eur, power_rate
from bindaems.shared.config import Config
from bindaems.shared.settings import TariffSettings


def boost_cost(
    cfg: Config, tariff: TariffSettings, month_peak_kw: float | None, day: date
) -> dict[str, Any]:
    """Mehrkosten je Fahrzeug im Monat von ``day``.

    ``cost_eur`` ist der Leistungspreis (brutto) für Monatsspitze plus volle Ladeleistung minus
    der für die Monatsspitze allein; die Spitze zählt mindestens mit ``minimum_kw``. Ohne Satz im
    Monat ist ``applies`` falsch und ``vehicles`` leer. Die Schlüssel sind die der Grenzen
    (``limits_json``), die Leistung ``max_a · phases · grid.voltage_nominal_v``.
    """
    rate = power_rate(day, tariff)
    if rate is None or rate.missing:
        return {"applies": False, "month_peak_kw": month_peak_kw, "vehicles": []}
    peak = max(month_peak_kw or 0.0, rate.minimum_kw)
    base = power_cost_eur(peak, rate)
    vehicles = []
    for key, vehicle in cfg.vehicles.items():
        extra_kw = vehicle.max_a * vehicle.phases * cfg.grid.voltage_nominal_v / 1000
        vehicles.append(
            {
                "id": key,
                "extra_kw": round(extra_kw, 3),
                "cost_eur": round(power_cost_eur(peak + extra_kw, rate) - base, 2),
            }
        )
    return {"applies": True, "month_peak_kw": month_peak_kw, "vehicles": vehicles}
