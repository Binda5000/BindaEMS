"""Wärmebedarf der Ölheizung: ``GET /api/heating/demand?tag=JJJJ-MM-TT`` (Tage der Heizsaison mit
der Anpassung gegen die Heizgradtage).

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from dataclasses import asdict
from datetime import date
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Query, status

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.heating.days import HeatingDays
from bindaems.app.heating.fit import (
    FIT_MIN_HDD,
    HEATING_LIMIT_C,
    DayHeat,
    HeatDay,
    HeatFit,
    day_heat,
    fit_demand,
    known_total,
    season_of,
)
from bindaems.app.ledger.routes import rounded
from bindaems.app.settings.service import SettingsService
from bindaems.shared.timeutil import LOCAL_TZ, Clock

NO_BURNER: Final = "Kein Brennerstatus eingerichtet."


def heating_router(
    heating_days: HeatingDays | None,
    settings: SettingsService,
    clock: Clock,
    guard: Guard,
    *,
    ac_power_known: bool,
) -> APIRouter:
    router = APIRouter(prefix="/api/heating")
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]

    @router.get("/demand")
    def demand(session: Viewer, tag: Annotated[date | None, Query()] = None) -> dict[str, Any]:
        if heating_days is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NO_BURNER)
        day = tag or clock.now().astimezone(LOCAL_TZ).date()
        first, last, label = season_of(day)
        config = settings.current().settings.heating
        days = heating_days.period(first, last)
        heats = [day_heat(d, config, ac_power_known=ac_power_known) for d in days]
        result = fit_demand(days, config, ac_power_known=ac_power_known)
        fit = result if isinstance(result, HeatFit) else None
        points = [
            _point(d, heat, _used(d, heat.demand_kwh)) for d, heat in zip(days, heats, strict=True)
        ]
        by_month: dict[str, float] = {}
        for d in days:
            if d.ac_heat_el_kwh is not None:
                key = f"{d.day.year:04d}-{d.day.month:02d}"
                by_month[key] = by_month.get(key, 0.0) + d.ac_heat_el_kwh
        body: dict[str, Any] = {
            "season": label,
            "first": first.isoformat(),
            "last": last.isoformat(),
            "points": points,
            "fit": asdict(fit) if fit is not None else None,
            "reason": result if isinstance(result, str) else None,
            "totals": {
                "oil_heat_kwh": known_total(h.oil_heat_kwh for h in heats),
                "oil_l": known_total(h.oil_l for h in heats),
                "oil_eur": known_total(h.oil_eur for h in heats),
                "ac_heat_el_kwh": known_total(d.ac_heat_el_kwh for d in days),
                "demand_kwh": known_total(h.demand_kwh for h in heats),
                "hdd_kd": known_total(d.hdd_kd for d in days),
            },
            "ac_by_month": by_month,
            "heating_limit_c": HEATING_LIMIT_C,
            "ac_cop": config.ac_cop,
        }
        out: dict[str, Any] = rounded(body)
        return out

    return router


def _used(day: HeatDay, demand_kwh: float | None) -> bool:
    """Der Tag geht in die Anpassung ein (wie in ``fit_demand``)."""
    return (
        day.complete
        and day.hdd_kd is not None
        and day.hdd_kd >= FIT_MIN_HDD
        and demand_kwh is not None
    )


def _point(day: HeatDay, heat: DayHeat, used: bool) -> dict[str, Any]:
    return {
        "day": day.day.isoformat(),
        "hdd_kd": day.hdd_kd,
        "outdoor_mean_c": day.outdoor_mean_c,
        "burner_h": day.burner_h,
        "oil_heat_kwh": heat.oil_heat_kwh,
        "oil_l": heat.oil_l,
        "ac_heat_el_kwh": day.ac_heat_el_kwh,
        "ac_heat_kwh": heat.ac_heat_kwh,
        "demand_kwh": heat.demand_kwh,
        "complete": day.complete,
        "used": used,
        "note": day.note,
    }
