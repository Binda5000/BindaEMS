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
MAX_INTENT_PLAN_SLOTS: Final = 8

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
    "UNTERSTUETZUNG_RESERVIERT",
    "GESCHAETZT",
    "ERSATZPLAN",
    "ZIEL_SOC_ABFAHRT",
    "AUTO_WARTET_AUF_PV",
    "ZIEL_SOC_UNERREICHBAR",
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
    "vehicles",
]
SolverStatus = Literal["optimal", "time_limit", "infeasible", "timeout", "error"]


class Reason(_Model):
    """Grundcode eines Slots mit den Zahlen und Zeiten, die sein Satz braucht."""

    code: ReasonCode
    params: dict[str, float | str] = Field(default_factory=dict)


class PlanSlot(_Model):
    """Eine Viertelstunde des Plans; Leistungen sind Mittelwerte in W.

    ``ev_w`` ist die geplante Ladeleistung je Fahrzeug-Schlüssel (nur Autos mit Leistung über 0).
    """

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
    support_soc_pct: Finite | None = None
    ev_w: dict[str, Finite] = Field(default_factory=dict)
    reasons: list[Reason]


class PlanVehicle(_Model):
    """Zusammenfassung des Plans für ein geplantes Auto (Plan 4b, Präzisierung 3 und 14).

    - ``target_soc_pct``: das wirksame Ziel (unter dem Ladelimit des Autos, wenn das Limit nicht
      angeglichen wird); ``limit_pct``: das Ladelimit im Auto, ``None`` wenn unbekannt
    - ``soc_start_pct`` und ``soc_assumed``: Ladestand zu Beginn; angenommen heißt, er ist nicht
      gemessen und nicht geschätzt
    - ``need_kwh``, ``planned_kwh`` und ``shortfall_kwh``: Bedarf, im Plan geladene Energie (am
      Fahrzeug) und Fehlmenge
    - ``expected_soc_pct``: Ladestand an der Abfahrt, wenn der Plan so läuft
    - ``finish_at``: Ende des letzten Slots mit Laden, ``None`` ohne Laden
    - ``reachable``: erreicht der Plan das Ziel (Fehlmenge höchstens 0,1 kWh)?
    """

    key: str
    name: str
    wallbox: str
    target_soc_pct: Finite
    departure_at: AwareDatetime
    soc_start_pct: Finite
    soc_assumed: bool
    limit_pct: Finite | None
    need_kwh: Finite
    planned_kwh: Finite
    shortfall_kwh: Finite
    expected_soc_pct: Finite
    finish_at: AwareDatetime | None
    reachable: bool


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
    vehicles: list[PlanVehicle] = Field(default_factory=list)
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


class IntentPlanSlot(_Model):
    """Eine Viertelstunde des Plans in der Absicht: nur, was der core zum Ausführen braucht."""

    start: AwareDatetime
    intent: BatteryIntent
    budget_kw: NonNegativeW | None = None
    import_ct: Finite | None = None
    support_soc_pct: Annotated[float, Field(ge=0, le=100)] | None = None
    vehicles: dict[str, Annotated[int, Field(ge=0)]] = Field(default_factory=dict)


class IntentPlan(_Model):
    """Bindender Teil eines Plans, so wie die app ihn in der Absicht an den core schickt.

    Der core prüft Raster, Lückenlosigkeit und Gültigkeit (Plan 3c); hier steht nur die Form.
    """

    id: int
    created_at: AwareDatetime
    valid_until: AwareDatetime
    grid_charge_allowed: bool
    slots: Annotated[list[IntentPlanSlot], Field(min_length=1)]

    def valid_at(self, ts: datetime) -> bool:
        """Gilt der Plan zum Zeitpunkt ``ts``? (``created_at ≤ ts < valid_until``)"""
        moment = ensure_utc(ts)
        return self.created_at <= moment < self.valid_until

    def slot_at(self, ts: datetime) -> IntentPlanSlot | None:
        """Der Slot, in dem ``ts`` liegt; ``None``, wenn der Plan ihn nicht enthält."""
        wanted = slot_start(ts)
        return next((slot for slot in self.slots if slot.start == wanted), None)


def intent_plan(plan: Plan, *, grid_charge_allowed: bool) -> IntentPlan:
    """Der bindende Teil von ``plan`` für die Absicht: die Slots vor ``valid_until``, höchstens
    ``MAX_INTENT_PLAN_SLOTS``. Ist ``grid_charge_allowed`` falsch, wird ``NETZLADEN`` zu
    ``EIGENVERBRAUCH``. Die Ladeleistung je Auto geht auf ganze W gerundet mit. ``plan.id`` muss
    gesetzt sein (der Plan ist gespeichert)."""
    if plan.id is None:
        raise ValueError("Nur ein gespeicherter Plan (mit id) geht in die Absicht")

    def binding(slot: PlanSlot) -> IntentPlanSlot:
        intent = slot.intent
        if not grid_charge_allowed and intent.kind is BatteryIntentKind.NETZLADEN:
            intent = BatteryIntent()
        return IntentPlanSlot(
            start=slot.start,
            intent=intent,
            budget_kw=slot.budget_kw,
            import_ct=slot.import_ct,
            support_soc_pct=slot.support_soc_pct,
            vehicles={key: round(watts) for key, watts in slot.ev_w.items()},
        )

    kept = [slot for slot in plan.slots if slot.start < plan.valid_until]
    return IntentPlan(
        id=plan.id,
        created_at=plan.created_at,
        valid_until=plan.valid_until,
        grid_charge_allowed=grid_charge_allowed,
        slots=[binding(slot) for slot in kept[:MAX_INTENT_PLAN_SLOTS]],
    )
