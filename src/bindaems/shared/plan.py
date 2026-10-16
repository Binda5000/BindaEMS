"""Akkuplan (Plan 3b): Absicht, Slot und Plan, wie ihn Optimierer und Ersatzplan liefern.

Ein Plan trägt einen Kopf (Zeit, Auslöser, Quelle, Solver-Status, Gültigkeit, Version der
Einstellungen) und je Viertelstunde einen Slot mit Absicht, erwartetem SOC, Flüssen, Preisen,
Kosten, Budget und Grundcodes für die Erklärung.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from bindaems.shared.config import _Model
from bindaems.shared.timeutil import SLOT, ensure_utc, slot_start

PLAN_VALID: Final = timedelta(minutes=45)

NonNegativeW = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Finite = Annotated[float, Field(allow_inf_nan=False)]


class BatteryIntentKind(StrEnum):
    EIGENVERBRAUCH = "EIGENVERBRAUCH"
    HALTEN = "HALTEN"
    NETZLADEN = "NETZLADEN"


class BatteryIntent(_Model):
    """Absicht für den Akku in einem Slot; ``grid_charge_w`` gilt nur bei ``NETZLADEN``."""

    kind: BatteryIntentKind = BatteryIntentKind.EIGENVERBRAUCH
    grid_charge_w: NonNegativeW = 0.0

    @model_validator(mode="after")
    def _grid_charge_matches_kind(self) -> Self:
        charging = self.kind is BatteryIntentKind.NETZLADEN
        if charging and self.grid_charge_w <= 0:
            raise ValueError("NETZLADEN braucht eine Leistung über 0 W")
        if not charging and self.grid_charge_w != 0:
            raise ValueError("Nur NETZLADEN hat eine Netzladeleistung")
        return self


ReasonCode = Literal[
    "NETZLADEN_GUENSTIG",
    "HALTEN_FUER_SPITZE",
    "BUDGET_SPITZE",
    "HALTEN_RESTWERT",
    "PV_LADEN",
    "PV_UEBERSCHUSS_ERWARTET",
    "REGEL_R_BLOCKIERT_NETZLADEN",
    "NETZLADEN_AUS",
    "EIGENVERBRAUCH",
    "GESCHAETZT",
    "ERSATZPLAN",
]
PlanSource = Literal["optimizer", "fallback"]
PlanTrigger = Literal[
    "start",
    "slot",
    "prices",
    "pv_forecast",
    "load_forecast",
    "soc",
    "month_peak",
    "settings",
    "manual",
]
SolverStatus = Literal["optimal", "time_limit", "infeasible", "timeout", "error"]


class Reason(_Model):
    """Grundcode eines Slots mit den Zahlen und Zeiten, die sein Satz braucht."""

    code: ReasonCode
    params: dict[str, float | str] = Field(default_factory=dict)


class PlanSlot(_Model):
    """Eine Viertelstunde des Plans; Leistungen sind Mittelwerte in W."""

    start: AwareDatetime
    intent: BatteryIntent
    soc_start_pct: Finite
    soc_end_pct: Finite
    pv_w: Finite
    load_w: Finite
    import_w: Finite
    export_w: Finite
    charge_pv_w: Finite
    charge_grid_w: Finite
    discharge_w: Finite
    import_ct: Finite
    feed_in_ct: Finite | None
    estimated: bool
    cost_ct: Finite
    budget_kw: Finite | None
    reasons: list[Reason]


class Plan(_Model):
    id: int | None = None
    created_at: AwareDatetime
    trigger: PlanTrigger
    source: PlanSource
    solver_status: SolverStatus | None
    fallback_reason: str | None
    valid_until: AwareDatetime
    settings_version: int
    grid_charge_allowed: bool
    reserve_pct: Finite
    soc_max_pct: Finite
    month_peak_kw: Finite | None
    power_price: bool
    expected_cost_ct: Finite
    baseline_cost_ct: Finite
    issues: list[str]
    trimmed: bool = False
    slots: Annotated[list[PlanSlot], Field(min_length=1)]

    @property
    def horizon_end(self) -> datetime:
        """Ende des letzten Slots (exklusiv)."""
        return self.slots[-1].start + SLOT

    def valid_at(self, ts: datetime) -> bool:
        """Gilt der Plan zum Zeitpunkt ``ts``? (``created_at ≤ ts < valid_until``)"""
        moment = ensure_utc(ts)
        return self.created_at <= moment < self.valid_until

    def slot_at(self, ts: datetime) -> PlanSlot | None:
        """Der Slot, in dem ``ts`` liegt; ``None``, wenn der Plan ihn nicht enthält."""
        wanted = slot_start(ts)
        return next((slot for slot in self.slots if slot.start == wanted), None)
