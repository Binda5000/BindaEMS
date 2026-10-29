"""Akkuplan (Plan 3b): Absicht, Slot und Plan, wie ihn Optimierer und Ersatzplan liefern.

Ein Plan trägt einen Kopf (Zeit, Auslöser, Quelle, Solver-Status, Gültigkeit, Version der
Einstellungen) und je Viertelstunde einen Slot mit Absicht, erwartetem SOC, Flüssen, Preisen,
Kosten, Budget und Grundcodes für die Erklärung.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from statistics import quantiles
from typing import Annotated, Final, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from bindaems.shared.config import _Model
from bindaems.shared.timeutil import SLOT, ensure_utc, slot_start

PLAN_VALID: Final = timedelta(minutes=45)
MAX_INTENT_PLAN_SLOTS: Final = 8
# Hinweis für die Klimaanlage (Plan 6a, Entscheidung 7): Schwellen und Perzentil der teuren Slots
THERMAL_MORE_EXPORT_W: Final = 500.0  # ab dieser geplanten Einspeisung „mehr“
THERMAL_LESS_EXPORT_W: Final = 100.0  # „weniger“ nur unter dieser geplanten Einspeisung
THERMAL_MIN_SLOTS: Final = 4  # kürzere Pläne sagen über „teuer“ nichts aus
HIGH_PERCENTILE_INDEX: Final = 7  # 80. Perzentil aus ``quantiles(n=10)``

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
    "AKKU_AN_UNTERGRENZE",
    "EIGENVERBRAUCH",
    "UNTERSTUETZUNG_RESERVIERT",
    "GESCHAETZT",
    "ERSATZPLAN",
    "ZIEL_SOC_ABFAHRT",
    "AUTO_WARTET_AUF_PV",
    "ZIEL_SOC_UNERREICHBAR",
    "AUTO_ERWARTET",
    "KLIMA_MEHR",
    "KLIMA_WENIGER",
    "AUTO_AUS_AKKU",
    "SOFORT_AUS_AKKU",
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
    "thermal",
]
SolverStatus = Literal["optimal", "time_limit", "infeasible", "timeout", "error"]


class Reason(_Model):
    """Grundcode eines Slots mit den Zahlen und Zeiten, die sein Satz braucht."""

    code: ReasonCode
    params: dict[str, float | str] = Field(default_factory=dict)


class PlanSlot(_Model):
    """Eine Viertelstunde des Plans; Leistungen sind Mittelwerte in W.

    ``ev_w`` ist die geplante Ladeleistung je Fahrzeug-Schlüssel (nur Autos mit Leistung über 0).
    ``ev_battery_w`` ist der Teil davon, der aus dem Akku kommt (Akku ins Auto, je Fahrzeug-
    Schlüssel, in W); ``now_battery`` sagt, dass Autos in Sofort in diesem Slot aus dem Akku laden
    dürfen. Alte Pläne laden ohne beide Felder mit ``{}`` und ``False``.
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
    ev_battery_w: dict[str, Finite] = Field(default_factory=dict)
    now_battery: bool = False
    # Vorheizen und Vorkühlen (Plan 5c, Präzisierung 7): Mehr- (positiv) oder Minderverbrauch der
    # Klimaanlage in W, davon aus dem Netz, und der Versatz in Stufen am Slotende; ohne
    # Verschiebung 0, 0 und ``None``. ``import_w``, ``export_w`` und ``cost_ct`` bleiben die des
    # ersten Planungsschritts. Alte Pläne laden mit diesen Werten.
    thermal_w: Finite = 0.0
    thermal_grid_w: NonNegativeW = 0.0
    thermal_steps: int | None = None
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
    - ``expected``: ein erwartetes Auto (Plan 5b, Präzisierung 10), noch nicht angesteckt und ab
      ``arrival_at`` laut Anwesenheitsprognose da; ``soc_start_pct`` ist dann der gelernte
      Ladestand bei der Ankunft. Es steht nur im Plan: Die Absicht (``intent_plan``), HA und die
      Zählung der geplanten Ladungen übergehen es. Pläne ohne beide Felder laden mit ``False``
      und ``None``.
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
    expected: bool = False
    arrival_at: AwareDatetime | None = None


# Betriebsart der Klimaanlage (Plan 6a, Entscheidung 4); ``unbekannt``, wenn die Bits nicht reichen.
ThermalMode = Literal["heizen", "kuehlen", "lueften", "aus", "unbekannt"]

# Grund der Verschiebung (Plan 5c, Präzisierung 7)
ThermalReason = Literal["ok", "off", "no_model", "mode", "not_worth", "solver"]


class PlanThermal(_Model):
    """Vorheizen und Vorkühlen im Plan (Plan 5c, Präzisierung 7).

    - ``device``: die Klimaanlage (erstes Gerät der Art ``coe_offset``)
    - ``mode``: Betriebsart aus Health beim Planen
    - ``active``: die Verschiebung geht an den core (Schalter an und ``reason`` ``ok``); sonst ist
      sie mit ``reason`` ``ok`` nur Vorschau
    - ``saving_ct``: Ersparnis der Verschiebung über den Horizont, nie negativ
    - ``model_computed_at``: Stand des Wärmemodells der Betriebsart, ``None`` ohne
    - ``reason``: ``ok``, ``off`` (Schalter aus und keine Vorschau), ``no_model``, ``mode``
      (heizt oder kühlt nicht), ``not_worth``, ``solver``
    """

    device: str
    mode: ThermalMode
    active: bool
    saving_ct: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    model_computed_at: AwareDatetime | None
    reason: ThermalReason


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
    # ``None`` ohne Klimaanlage der Art ``coe_offset``; alte Pläne laden mit ``None`` (Plan 5c)
    thermal: PlanThermal | None = None
    slots: Annotated[list[PlanSlot], Field(min_length=1)]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.valid_until <= self.created_at:
            raise ValueError("valid_until muss nach created_at liegen")
        starts = [slot.start for slot in self.slots]
        if any(later <= earlier for earlier, later in pairwise(starts)):
            raise ValueError("Die Slots müssen streng aufsteigend und eindeutig nach start sein")
        return self

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


# Hinweis des Plans für die Klimaanlage in einem Slot (Plan 6a, Entscheidung 7): „mehr“ erwartet
# Einspeisung, „weniger“ gehört zu den teuersten Stunden, sonst „neutral“.
ThermalHint = Literal["mehr", "neutral", "weniger"]

MAX_THERMAL_STEPS: Final = 6  # Planwert der Klimaanlage in Stufen, im Betrag höchstens


class IntentPlanSlot(_Model):
    """Eine Viertelstunde des Plans in der Absicht: nur, was der core zum Ausführen braucht.

    ``ev_battery_w`` ist der Teil der Autoleistung (``vehicles``) je Fahrzeug, der aus dem Akku
    kommt (ganze W, nur Autos mit mindestens 1 W); ``now_battery`` erlaubt Autos in Sofort, aus
    dem Akku zu laden. Beides steht nur in Slots mit Eigenverbrauch; alte Absichten laden mit
    ``{}`` und ``False``. Jedes Fahrzeug in ``ev_battery_w`` steht auch in ``vehicles``, und sein
    Akkuanteil ist höchstens seine Leistung (Fix-Welle F5); sonst ist der Slot ungültig. Ohne
    Toleranz: ``intent_plan`` rundet beide mit ``round``, das monoton ist, und der Akkuanteil des
    Plans ist nie größer als die Leistung.
    """

    start: AwareDatetime
    intent: BatteryIntent
    budget_kw: NonNegativeW | None = None
    import_ct: Finite | None = None
    support_soc_pct: Annotated[float, Field(ge=0, le=100)] | None = None
    vehicles: dict[str, Annotated[int, Field(ge=0)]] = Field(default_factory=dict)
    ev_battery_w: dict[str, Annotated[int, Field(ge=0)]] = Field(default_factory=dict)
    now_battery: bool = False
    # Alte gespeicherte Absichten kennen beide Felder nicht und laden mit den Standardwerten.
    thermal: ThermalHint = "neutral"
    soc_end_pct: Annotated[float, Field(ge=0, le=100)] | None = None
    # Planwert der Klimaanlage (Plan 5c, Präzisierung 7): Leistungswunsch in Stufen (positiv mehr)
    # und der geplante Netzbezug dafür in W; nur bei ``Plan.thermal.active``, sonst ``None`` und
    # 0. Alte Absichten laden so.
    thermal_steps: Annotated[int, Field(ge=-MAX_THERMAL_STEPS, le=MAX_THERMAL_STEPS)] | None = None
    thermal_grid_w: NonNegativeW = 0.0
    # Betriebsart, für die der Planwert gilt (``Plan.thermal.mode``, mit ``thermal_steps``
    # gesetzt); der core folgt ihm nur in derselben Betriebsart. Alte Absichten: ``None``.
    thermal_mode: ThermalMode | None = None

    @model_validator(mode="after")
    def _battery_within_power(self) -> IntentPlanSlot:
        for key, share_w in self.ev_battery_w.items():
            if key not in self.vehicles:
                raise ValueError(f"ev_battery_w nennt {key}, das in vehicles fehlt")
            if share_w > self.vehicles[key]:
                raise ValueError(f"ev_battery_w von {key} liegt über seiner Leistung in vehicles")
        return self


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


def high_percentile(prices: Sequence[float]) -> float:
    """Das 80. Perzentil (inklusiv) der ``prices``: ab diesem Preis ist ein Slot teuer. Der Plan
    und der Ersatzplan rechnen damit gleich; es braucht mindestens zwei Preise."""
    return quantiles(prices, n=10, method="inclusive")[HIGH_PERCENTILE_INDEX]


def expensive_import_ct(slots: Sequence[PlanSlot]) -> float | None:
    """Der Bezugspreis, ab dem ein Slot zu den teuersten 20 % des Plans gehört: das 80. Perzentil
    von ``import_ct`` über alle ``slots`` (wie ``expensive_today`` im Ersatzplan, inklusiv).

    ``None`` bei weniger als ``THERMAL_MIN_SLOTS`` Slots und bei einheitlichem Preis: Dann gibt es
    keine teuren Slots, und es wäre nichts zu verschieben."""
    prices = [slot.import_ct for slot in slots]
    if len(prices) < THERMAL_MIN_SLOTS or min(prices) == max(prices):
        return None
    return high_percentile(prices)


def thermal_hint(slot: PlanSlot, expensive_ct: float | None) -> ThermalHint:
    """Hinweis des Plans für die Klimaanlage in ``slot`` (Plan 6a, Entscheidung 7).

    - ``mehr``: der Slot plant Einspeisung von mindestens 500 W (``export_w``)
    - ``weniger``: der Bezugspreis erreicht ``expensive_ct`` (genau am Perzentil zählt mit, wie
      im Ersatzplan) und die geplante Einspeisung liegt unter 100 W
    - sonst ``neutral``, auch ohne ``expensive_ct`` (``None``)
    """
    if slot.export_w >= THERMAL_MORE_EXPORT_W:
        return "mehr"
    if (
        expensive_ct is not None
        and slot.import_ct >= expensive_ct
        and slot.export_w < THERMAL_LESS_EXPORT_W
    ):
        return "weniger"
    return "neutral"


def intent_plan(
    plan: Plan, *, grid_charge_allowed: bool, thermal_allowed: bool = True
) -> IntentPlan:
    """Der bindende Teil von ``plan`` für die Absicht: die Slots vor ``valid_until``, höchstens
    ``MAX_INTENT_PLAN_SLOTS``. Ist ``grid_charge_allowed`` falsch, wird ``NETZLADEN`` zu
    ``EIGENVERBRAUCH``. Die Ladeleistung je Auto geht auf ganze W gerundet mit. Ebenso der Akku-
    Anteil daran (``ev_battery_w``, ohne erwartete Autos und ohne Anteile unter 1 W) und
    ``now_battery`` (Akku ins Auto): beides nur, wenn die Absicht des Slots nach dieser Umwandlung
    ``EIGENVERBRAUCH`` ist, sonst ``{}`` und ``False``. Jedes Auto aus
    ``plan.vehicles`` steht in jedem Slot vor seiner Abfahrt, auch mit 0 W (Ruling R22): Ein Auto,
    das ein gültiger Slot nicht nennt, kennt der Plan nicht, und der core lädt es nach der
    spätesten Startzeit. Erwartete Autos (``expected``, Plan 5b) fehlen ganz, ohne Leistung und
    ohne Eintrag: Der core bekommt für sie nichts. Je Slot gehen der Hinweis für die Klimaanlage
    (Perzentil über alle Slots des Plans, nicht nur über die gesendeten) und der geplante SOC am
    Ende (auf 0,1 % gerundet, in 0 bis 100 geklemmt) mit. Nur wenn ``plan.thermal.active`` ist
    und ``thermal_allowed`` (der Schalter „Vorheizen und Vorkühlen“, wie er jetzt steht), gehen
    der Planwert der Klimaanlage (``thermal_steps``), sein Netzbezug (auf ganze W gerundet) und
    die Betriebsart des Plans (``thermal_mode``) mit; sonst bleiben sie ``None``, 0 und ``None``,
    und der core regelt wie in Plan 6a.
    ``plan.id`` muss gesetzt sein (der Plan ist gespeichert)."""
    if plan.id is None:
        raise ValueError("Nur ein gespeicherter Plan (mit id) geht in die Absicht")

    expensive_ct = expensive_import_ct(plan.slots)
    expected = {car.key for car in plan.vehicles if car.expected}
    cars = [car for car in plan.vehicles if not car.expected]
    thermal = plan.thermal if plan.thermal is not None and plan.thermal.active else None
    if not thermal_allowed:
        thermal = None

    def binding(slot: PlanSlot) -> IntentPlanSlot:
        intent = slot.intent
        if not grid_charge_allowed and intent.kind is BatteryIntentKind.NETZLADEN:
            intent = BatteryIntent()
        planned = {car.key: 0 for car in cars if slot.start < car.departure_at}
        powers = {key: round(w) for key, w in slot.ev_w.items() if key not in expected}
        from_battery = intent.kind is BatteryIntentKind.EIGENVERBRAUCH
        battery = {
            key: round(w)
            for key, w in slot.ev_battery_w.items()
            if from_battery and key not in expected and round(w) >= 1
        }
        return IntentPlanSlot(
            start=slot.start,
            intent=intent,
            budget_kw=slot.budget_kw,
            import_ct=slot.import_ct,
            support_soc_pct=slot.support_soc_pct,
            vehicles=planned | powers,
            ev_battery_w=battery,
            now_battery=slot.now_battery and from_battery,
            thermal=thermal_hint(slot, expensive_ct),
            soc_end_pct=min(100.0, max(0.0, round(slot.soc_end_pct, 1))),
            thermal_steps=slot.thermal_steps if thermal is not None else None,
            thermal_grid_w=float(round(slot.thermal_grid_w)) if thermal is not None else 0.0,
            thermal_mode=thermal.mode if thermal is not None else None,
        )

    kept = [slot for slot in plan.slots if slot.start < plan.valid_until]
    return IntentPlan(
        id=plan.id,
        created_at=plan.created_at,
        valid_until=plan.valid_until,
        grid_charge_allowed=grid_charge_allowed,
        slots=[binding(slot) for slot in kept[:MAX_INTENT_PLAN_SLOTS]],
    )
