"""Datei eines Szenarios (Plan 3b, Task 15): Tage mit Ist-Werten, Preisen und Prognosen.

Eine Datei entsteht aus erzeugten Tagen (``synthetic``) oder aus dem Export der Anlage
(``export_days``) und geht als JSON an ``scenario_days``. Sie trägt alles, was ein Lauf braucht:
Akku, Einstellungen der Planung, Tarif (für den Leistungspreis) und je Slot Ist-Werte, Preise
und die Prognosen je Horizontklasse aus dem Archiv. Leistungen in W, Preise in ct/kWh brutto.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from bindaems.app.forecast.archive import HorizonClass
from bindaems.shared.config import BatteryConfig, _Model
from bindaems.shared.settings import PlanningSettings, TariffSettings

Finite = Annotated[float, Field(allow_inf_nan=False)]
NonNegativeFinite = Annotated[float, Field(ge=0, allow_inf_nan=False)]


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


class ScenarioBattery(BatteryConfig):
    """Akku wie in ``config.yaml``, dazu der höchste Sollwert beim Netzladen, der höchste
    Netzbezug und der SOC zu Beginn des Laufs."""

    max_grid_charge_setpoint_w: NonNegativeFinite
    grid_max_import_w: NonNegativeFinite
    start_soc_pct: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class ScenarioFile(_Model):
    version: Literal[1]
    created_at: AwareDatetime
    source: Literal["synthetic", "plant"]
    name: str
    battery: ScenarioBattery
    planning: PlanningSettings
    tariff: TariffSettings
    month_peak_kw: NonNegativeFinite | None  # Monatsspitze vor dem ersten Slot
    skipped_days: list[str] = Field(default_factory=list)
    slots: Annotated[list[ScenarioSlot], Field(min_length=1)]
