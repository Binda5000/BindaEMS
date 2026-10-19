"""Datei eines Szenarios (Plan 3b, Task 15): Tage mit Ist-Werten, Preisen und Prognosen.

Eine Datei entsteht aus erzeugten Tagen (``synthetic``) oder aus dem Export der Anlage
(``export_days``) und geht als JSON an ``scenario_days``. Sie trägt alles, was ein Lauf braucht:
Akku, Einstellungen der Planung, Tarif (für den Leistungspreis) und je Slot Ist-Werte, Preise
und die Prognosen je Horizontklasse aus dem Archiv. Leistungen in W, Preise in ct/kWh brutto.

Version 2 (Plan 4b, Task 13) bringt geplante Autos (``cars``) und je Slot die PV-Prognose P25
(``pv_p25_w``, für Regel R2). Eine Datei der Version 1 lädt unverändert, ohne Autos und ohne P25;
``export_days`` schreibt weiter Version 1.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from bindaems.app.forecast.archive import HorizonClass
from bindaems.shared.config import BatteryConfig, _Model
from bindaems.shared.settings import PlanningSettings, TariffSettings

Finite = Annotated[float, Field(allow_inf_nan=False)]
NonNegativeFinite = Annotated[float, Field(ge=0, allow_inf_nan=False)]
PositiveFinite = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
# lokale Uhrzeit ``HH:MM`` auf einer Viertelstunde: Anstecken und Abfahrt liegen auf Slotgrenzen
QuarterClock = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):(00|15|30|45)$")]


class ScenarioSlot(_Model):
    """Ein Slot: Ist-Werte, Preise und Prognosen je Horizontklasse (fehlende Klassen dürfen
    fehlen; der Lauf nimmt dann die nächste vorhandene, sonst den Ist-Wert)."""

    start: AwareDatetime
    pv_w: NonNegativeFinite
    load_w: NonNegativeFinite
    import_ct: Finite
    feed_in_ct: Finite | None
    pv_forecast_w: dict[HorizonClass, NonNegativeFinite]
    load_forecast_w: dict[HorizonClass, NonNegativeFinite]
    # PV-Prognose P25 je Horizontklasse (Version 2); ohne Wert rechnet der Planer mit P50
    pv_p25_w: dict[HorizonClass, NonNegativeFinite] = Field(default_factory=dict)


class ScenarioCar(_Model):
    """Ein geplantes Auto (Version 2): jeden Tag um ``arrive`` (lokal) mit ``soc_arrive_pct``
    angesteckt, Abfahrt um das nächste ``depart`` mit dem Ziel ``target_pct``.

    ``usable_kwh`` ist die nutzbare Kapazität, ``max_w`` und ``min_w`` die volle und die kleinste
    Ladeleistung an der Wallbox, ``efficiency`` der Wirkungsgrad des Ladens als Anteil.
    """

    key: Annotated[str, Field(min_length=1)]
    arrive: QuarterClock
    depart: QuarterClock
    soc_arrive_pct: Percent
    target_pct: Annotated[float, Field(ge=20, le=100, allow_inf_nan=False)]
    usable_kwh: PositiveFinite
    max_w: PositiveFinite
    min_w: NonNegativeFinite
    efficiency: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.arrive == self.depart:
            raise ValueError("Ankunft und Abfahrt müssen verschieden sein")
        if self.min_w > self.max_w:
            raise ValueError("min_w darf nicht über max_w liegen")
        return self


class ScenarioBattery(BatteryConfig):
    """Akku wie in ``config.yaml``, dazu der höchste Sollwert beim Netzladen, der höchste
    Netzbezug und der SOC zu Beginn des Laufs."""

    max_grid_charge_setpoint_w: NonNegativeFinite
    grid_max_import_w: NonNegativeFinite
    start_soc_pct: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class ScenarioFile(_Model):
    version: Literal[1, 2]
    created_at: AwareDatetime
    source: Literal["synthetic", "plant"]
    name: str
    battery: ScenarioBattery
    planning: PlanningSettings
    tariff: TariffSettings
    month_peak_kw: NonNegativeFinite | None  # Monatsspitze vor dem ersten Slot
    skipped_days: list[str] = Field(default_factory=list)
    slots: Annotated[list[ScenarioSlot], Field(min_length=1)]
    cars: list[ScenarioCar] = Field(default_factory=list)  # in der Reihenfolge der Priorität

    @model_validator(mode="after")
    def _check_cars(self) -> Self:
        if self.cars and self.version < 2:
            raise ValueError("Autos verlangen Version 2")
        keys = [car.key for car in self.cars]
        if len(set(keys)) != len(keys):
            raise ValueError("Jedes Auto braucht einen eigenen Schlüssel")
        return self
