"""Geplante Autos als Eingangsdaten des Planers (Plan 4b, Entscheidungen 6 und 7, Präzisierung 10).

Im Planer stehen nur Autos, die laut Health in „Geplant“ geregelt werden und angesteckt sind (es
gibt noch keine Anwesenheitsprognose). Ladestand, Quelle, Ladelimit, wirksames Ziel und Abfahrt
kommen aus Health, Wirkungsgrad, Kapazität und Ladeleistung aus der Konfiguration.

- ``need_kwh``, ``full_power_w``, ``latest_start``: dieselbe Formel wie im core
  (``core.control.planned``, Präzisierung 6). Die app importiert nichts aus dem core, darum steht
  sie zweimal; ``tests/unit/test_planned_formula.py`` vergleicht beide.
- ``horizon_need``: der Teil des Bedarfs, der im Horizont geladen werden muss
- ``grid_allowed``: Regel R2, die Netzenergie, die ein Auto bis zur Abfahrt laden darf
- ``planned_vehicles``: die geplanten Autos eines Laufs

Alle Energien der Autos stehen auf der Wallbox-Seite (Ruling R10): Bedarf, Bedarf im Horizont
und die nach Regel R2 erlaubte Netzenergie; der Wirkungsgrad steckt nur in ``need_kwh``.

Reine Rechnung: Health, Absicht und Zeit kommen als Parameter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any, Final

from pydantic import AwareDatetime, Field

from bindaems.app.control.intent import _health_vehicles, _trusted
from bindaems.shared.config import Config, EvcsConfig, TwcConfig, VehicleConfig, _Model
from bindaems.shared.control import ChargeMode, Intent, VehicleIntent, next_departure
from bindaems.shared.numbers import finite_number
from bindaems.shared.timeutil import SLOT, ensure_utc

if TYPE_CHECKING:  # ``inputs`` importiert dieses Modul für ``PlanInputs.vehicles``
    from bindaems.app.planning.inputs import SlotInput

LATEST_START_MARGIN: Final = 1.1  # Zuschlag auf die Ladedauer bei voller Leistung (wie im core)
LATEST_START_BUFFER: Final = timedelta(minutes=15)  # Puffer vor der Abfahrt (wie im core)

_SLOT_H: Final = SLOT.total_seconds() / 3600

Finite = Annotated[float, Field(allow_inf_nan=False)]
NonNegativeFinite = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class PlanVehicleInput(_Model):
    """Ein geplantes Auto im Planer (Präzisierung 10).

    - ``key``, ``name``: Schlüssel und Name aus ``vehicles`` der Konfiguration; ``wallbox`` der
      Schlüssel der Wallbox, an der es steht
    - ``soc_pct``: der Ladestand, mit dem gerechnet wird; ``soc_assumed`` heißt unbekannt, dann 0 %
    - ``target_soc_pct``: das wirksame Ziel (ein Ladelimit im Auto darunter zählt);
      ``limit_pct`` das Ladelimit im Auto, ``None`` unbekannt
    - ``departure_at``: die nächste Abfahrt (UTC)
    - ``efficiency``: Wirkungsgrad des Ladens als Anteil; ``usable_kwh`` nutzbare Kapazität
    - ``min_w``, ``max_w``: kleinste und volle Ladeleistung an der Wallbox
    - ``need_kwh``: der Bedarf bis zum Ziel aus der Wallbox (``need_kwh``)
    - ``horizon_need_kwh``: der Teil davon, der im Horizont geladen werden muss (``horizon_need``)
    - ``grid_allowed_kwh``: so viel davon darf bis zur Abfahrt aus dem Netz kommen (Regel R2)

    Die drei Energien stehen auf der Wallbox-Seite (Ruling R10).
    """

    key: str
    name: str
    wallbox: str
    soc_pct: Finite
    soc_assumed: bool
    target_soc_pct: Finite
    limit_pct: Finite | None
    departure_at: AwareDatetime
    efficiency: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]
    usable_kwh: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    min_w: NonNegativeFinite
    max_w: NonNegativeFinite
    need_kwh: NonNegativeFinite
    horizon_need_kwh: NonNegativeFinite
    grid_allowed_kwh: NonNegativeFinite


def need_kwh(soc_pct: float, target_pct: float, usable_kwh: float, efficiency: float) -> float:
    """Die Energie in kWh, die das Auto aus der Wallbox bis zum Ziel braucht (wie im core).

    ``max(0, Ziel − Ladestand) / 100 · usable_kwh / efficiency``; ``efficiency`` als Anteil.
    """
    return max(0.0, target_pct - soc_pct) / 100.0 * usable_kwh / efficiency


def full_power_w(
    vehicle: VehicleConfig, wallbox: EvcsConfig | TwcConfig, voltage_v: float
) -> float:
    """Die volle Ladeleistung in W: ``min(vehicle.max_a, wallbox.max_a) · U · phases`` (wie im
    core); ``voltage_v`` ist ``grid.voltage_nominal_v``, ``phases`` die des Fahrzeugs."""
    return min(vehicle.max_a, wallbox.max_a) * voltage_v * vehicle.phases


def latest_start(departure_at: datetime, need: float, full_w: float) -> datetime | None:
    """Ab hier lädt das Auto mit voller Leistung, um bis ``departure_at`` am Ziel zu sein (wie im
    core).

    ``departure_at − need / (full_w / 1000) · LATEST_START_MARGIN h − LATEST_START_BUFFER``.
    ``need`` ist der Bedarf aus der Wallbox (``need_kwh``, der Wirkungsgrad steckt schon darin).
    Ohne Bedarf gibt es keine (``None``).
    """
    if need <= 0:
        return None
    hours = need / (full_w / 1000.0) * LATEST_START_MARGIN
    return departure_at - timedelta(hours=hours) - LATEST_START_BUFFER


def _min_power_w(
    vehicle: VehicleConfig, wallbox: EvcsConfig | TwcConfig, voltage_v: float
) -> float:
    """Die kleinste Ladeleistung in W: der größere Mindeststrom von Auto und EVCS (am Wall
    Connector der des Autos) mal ``U · phases``, wie der Ladepunkt im core."""
    min_a = max(vehicle.min_a, wallbox.min_a) if isinstance(wallbox, EvcsConfig) else vehicle.min_a
    return min_a * voltage_v * vehicle.phases


def horizon_need(need: float, max_w: float, departure_at: datetime, horizon_end: datetime) -> float:
    """Der Teil des Bedarfs in kWh, der im Horizont geladen werden muss (Entscheidung 6).

    Liegt die Abfahrt hinter dem Horizontende, muss nur das geladen werden, was danach mit voller
    Leistung nicht mehr ginge: ``max(0, need − max_w / 1000 · Stunden zwischen Horizontende und
    Abfahrt)``; sonst der ganze Bedarf. Gerechnet wird auf der Wallbox-Seite wie ``need_kwh``
    (Ruling R10): Der Wirkungsgrad steckt schon im Bedarf und kommt hier nicht noch einmal dazu.
    """
    after_h = (ensure_utc(departure_at) - ensure_utc(horizon_end)).total_seconds() / 3600
    if after_h <= 0:
        return need
    return max(0.0, need - max_w / 1000 * after_h)


def _slots_before(departure_at: datetime, slots: Sequence[SlotInput]) -> int:
    """Die Zahl der Slots vor dem ersten Slot ab ``departure_at`` (``T_c``, Präzisierung 11)."""
    moment = ensure_utc(departure_at)
    return sum(1 for slot in slots if ensure_utc(slot.start) < moment)


def grid_allowed(
    vehicles: Sequence[PlanVehicleInput], slots: Sequence[SlotInput], priority: Sequence[str]
) -> dict[str, float]:
    """Regel R2: die Netzenergie in kWh, die jedes Auto bis zur Abfahrt laden darf
    (Entscheidung 7).

    Erlaubt ist ``horizon_need_kwh`` minus der Anteil des Autos am PV-Überschuss nach P25
    (``max(0, P25 − Last)``, ohne P25 gilt ``pv_w``) in den Slots vor seiner Abfahrt. Je Slot
    nimmt ein Auto höchstens, was es mit ``max_w`` laden kann (Ruling R11); der Rest bleibt für
    die nächsten Autos. Überschuss unter ``min_w`` zählt mit. Der Überschuss geht in der
    Reihenfolge ``priority`` an die Autos; Autos, die ``priority`` nicht nennt, folgen in der
    Reihenfolge von ``vehicles``. Jedes Auto nimmt seinen Anteil von den letzten Slots vor seiner
    Abfahrt rückwärts: So bleibt der frühe Überschuss für ein späteres Auto, das früher abfährt.
    """
    left = [
        max(0.0, (slot.pv_w if slot.pv_p25_w is None else slot.pv_p25_w) - slot.load_w)
        * _SLOT_H
        / 1000
        for slot in slots
    ]
    rank = {key: index for index, key in enumerate(priority)}
    order = sorted(vehicles, key=lambda car: rank.get(car.key, len(rank)))
    allowed: dict[str, float] = {}
    for car in order:
        wanted = car.horizon_need_kwh
        per_slot = car.max_w / 1000 * _SLOT_H
        for index in reversed(range(_slots_before(car.departure_at, slots))):
            if wanted <= 0:
                break
            take = min(wanted, left[index], per_slot)
            left[index] -= take
            wanted -= take
        allowed[car.key] = max(0.0, wanted)
    return allowed


def _order(cfg: Config, intent: Intent) -> list[str]:
    """Die Fahrzeuge in der Reihenfolge ihrer Priorität wie im core: ``intent.priority``, wenn sie
    jedes Fahrzeug der Konfiguration genau einmal nennt, sonst die von ``config.yaml``."""
    configured = list(cfg.vehicles)
    priority = intent.priority
    if priority is None or sorted(priority) != sorted(configured):
        return configured
    return list(priority)


def _moment(value: Any) -> datetime | None:
    """Zeitpunkt aus ISO-Text mit Zeitzone; sonst ``None``."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ensure_utc(moment) if moment.tzinfo is not None else None


def planned_vehicles(
    cfg: Config,
    health: Mapping[str, Any] | None,
    intent: Intent | None,
    *,
    now: datetime,
    slots: Sequence[SlotInput],
) -> list[PlanVehicleInput]:
    """Die geplanten Autos eines Laufs, in der Reihenfolge ihrer Priorität (Präzisierung 10).

    Aufgenommen wird ein Fahrzeug, wenn Health es mit ``mode`` „planned“, ``plugged`` wahr, einer
    Wallbox der Konfiguration und dem Teil ``planned`` nennt, Health vertraut ist
    (``intent_version ≥ intent.version``; ohne Absicht ist es das nicht) und die Abfahrt nicht vor
    dem Ende des aktuellen Slots (``slots[0]``) liegt. Ein unbekannter Ladestand rechnet mit 0 %
    (``soc_assumed``). Ist die Abfahrt aus Health schon vorbei (Health ist bis zu einer Abfrage
    alt), gilt die nächste nach ``now`` (``next_departure``), wie im nächsten Zyklus des core.
    ``grid_allowed_kwh`` verteilt den Überschuss in der Reihenfolge der Priorität.
    """
    if intent is None or not slots or not _trusted(intent.version, health):
        return []
    entries = _health_vehicles(health)
    volts = cfg.grid.voltage_nominal_v
    current_end = ensure_utc(slots[0].start) + SLOT
    horizon_end = ensure_utc(slots[-1].start) + SLOT
    cars: list[PlanVehicleInput] = []
    for key in _order(cfg, intent):
        entry = entries.get(key)
        planned = entry.get("planned") if entry is not None else None
        if entry is None or not isinstance(planned, dict):
            continue
        if entry.get("mode") != ChargeMode.PLANNED.value or entry.get("plugged") is not True:
            continue
        wallbox_key = entry.get("wallbox")
        if not isinstance(wallbox_key, str) or wallbox_key not in cfg.wallboxes:
            continue
        departure_at = _moment(planned.get("departure_at"))
        target = finite_number(planned.get("target_soc_pct"))
        if departure_at is None or target is None:
            continue
        if departure_at <= ensure_utc(now):
            departure = intent.vehicles.get(key, VehicleIntent()).departure
            departure_at = next_departure(departure, now)
        if departure_at < current_end:
            continue
        vehicle = cfg.vehicles[key]
        wallbox = cfg.wallboxes[wallbox_key]
        soc = finite_number(entry.get("soc_pct"))
        assumed = soc is None or entry.get("soc_source") not in ("measured", "estimated")
        soc_pct = 0.0 if assumed or soc is None else soc
        efficiency = vehicle.charge_efficiency_pct / 100
        max_w = full_power_w(vehicle, wallbox, volts)
        need = need_kwh(soc_pct, target, vehicle.usable_kwh, efficiency)
        within = horizon_need(need, max_w, departure_at, horizon_end)
        cars.append(
            PlanVehicleInput(
                key=key,
                name=vehicle.name,
                wallbox=wallbox_key,
                soc_pct=soc_pct,
                soc_assumed=assumed,
                target_soc_pct=target,
                limit_pct=finite_number(entry.get("charge_limit_pct")),
                departure_at=departure_at,
                efficiency=efficiency,
                usable_kwh=vehicle.usable_kwh,
                min_w=_min_power_w(vehicle, wallbox, volts),
                max_w=max_w,
                need_kwh=need,
                horizon_need_kwh=within,
                grid_allowed_kwh=within,
            )
        )
    allowed = grid_allowed(cars, slots, [car.key for car in cars])
    return [car.model_copy(update={"grid_allowed_kwh": allowed[car.key]}) for car in cars]
