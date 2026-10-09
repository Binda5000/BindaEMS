"""Laufzeit-Einstellungen (Spec 11.5): im UI änderbar, versioniert in SQLite, als YAML im- und
exportierbar. Harte Grenzen stehen nie hier, sondern nur in ``config.yaml``.

Tarif v1 (Spec 9.1): nur Energiebestandteile je kWh mit Zeitfenstern, Gültigkeit und USt.
"""

from __future__ import annotations

from datetime import date, time
from typing import Annotated, Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from bindaems.shared.config import _Model

Ct = Annotated[float, Field(allow_inf_nan=False)]
NonNegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Month = Annotated[int, Field(ge=1, le=12)]
Weekday = Annotated[int, Field(ge=0, le=6)]  # 0 = Montag


class TimeWindow(_Model):
    """Zeitfenster in Ortszeit; im Fenster gilt ``Basis × factor`` oder ``value_ct``."""

    months: Annotated[list[Month], Field(min_length=1)] = Field(
        default_factory=lambda: list(range(1, 13))
    )
    weekdays: Annotated[list[Weekday], Field(min_length=1)] = Field(
        default_factory=lambda: list(range(7))
    )
    start: time = time(0)
    end: time = time(0)  # 00:00 als Ende heißt Tagesende
    factor: NonNegative | None = None
    value_ct: Ct | None = None

    @field_validator("months", "weekdays")
    @classmethod
    def _unique(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("Einträge dürfen nicht doppelt vorkommen")
        return value

    @field_validator("start", "end", mode="before")
    @classmethod
    def _time_as_text(cls, value: Any) -> Any:
        # YAML liest 10:00 ohne Anführungszeichen als Zahl (Sexagesimal) – das wäre 00:10
        if isinstance(value, int | float):
            raise ValueError("Uhrzeit als Text angeben, z. B. '10:00'")
        return value

    @field_validator("start", "end")
    @classmethod
    def _quarter_hour(cls, value: time) -> time:
        if value.minute % 15 or value.second or value.microsecond or value.tzinfo is not None:
            raise ValueError("Uhrzeit muss auf einer Viertelstunde liegen (:00, :15, :30, :45)")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.factor is None) == (self.value_ct is None):
            raise ValueError("Zeitfenster braucht genau eines von factor oder value_ct")
        if self.end != time(0) and self.end <= self.start:
            raise ValueError("Ende muss nach dem Beginn liegen (00:00 = Tagesende)")
        return self


class PriceComponent(_Model):
    id: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,40}$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    kind: Literal["energy_per_kwh"] = "energy_per_kwh"
    source: Literal["fixed", "spot"] = "fixed"
    value_ct: Ct | None = None  # netto; None = noch nicht eingetragen
    vat: bool = True
    valid_from: date | None = None
    valid_until: date | None = None  # inklusive
    windows: list[TimeWindow] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.source == "spot" and self.value_ct is not None:
            raise ValueError("Der Börsenpreis hat keinen festen Wert (value_ct leer lassen)")
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_from > self.valid_until
        ):
            raise ValueError("valid_from liegt nach valid_until")
        return self


SNAP_WINDOW = TimeWindow(months=[4, 5, 6, 7, 8, 9], start=time(10), end=time(16), factor=0.8)


def _default_components() -> list[PriceComponent]:
    """Spec 9.1; Werte nur, wo die Spec sie nennt – der Rest kommt bei der Inbetriebnahme."""
    return [
        PriceComponent(id="spot", name="Börsenpreis", source="spot"),
        PriceComponent(id="supplier_markup", name="Lieferantenaufschlag", value_ct=1.2),
        PriceComponent(
            id="grid_usage",
            name="Netznutzungsentgelt Arbeitspreis (Netzebene 7)",
            windows=[SNAP_WINDOW],
        ),
        PriceComponent(id="grid_loss", name="Netzverlustentgelt"),
        PriceComponent(id="electricity_tax", name="Elektrizitätsabgabe"),
        PriceComponent(id="renewables_levy", name="Erneuerbaren-Förderbeitrag"),
    ]


class TariffSettings(_Model):
    vat_pct: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] = 20.0
    components: list[PriceComponent] = Field(default_factory=_default_components)
    fixed_price_gross_ct: NonNegative = 30.0

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        ids = [component.id for component in self.components]
        if len(set(ids)) != len(ids):
            raise ValueError("Die IDs der Bestandteile müssen eindeutig sein")
        if sum(component.source == "spot" for component in self.components) != 1:
            raise ValueError("Genau ein Bestandteil muss den Börsenpreis liefern (source: spot)")
        return self


class FeedInSettings(_Model):
    """OeMAG-Marktpreis je Monat („JJJJ-MM“ → ct/kWh), nie negativ (Spec 3.10)."""

    monthly_ct: dict[Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")], NonNegative] = (
        Field(default_factory=dict)
    )


class PriceSettings(_Model):
    vat_mode: Literal["auto", "net", "gross"] = "auto"
    vat_fallback: Literal["net", "gross"] = "gross"
    reference_source: Literal["energy_charts", "awattar"] = "energy_charts"


class PvModelSettings(_Model):
    performance_ratio: Annotated[float, Field(gt=0, le=1.2)] = 0.85
    temp_coeff_pct_per_k: Annotated[float, Field(ge=-2, le=0)] = -0.35
    noct_c: Annotated[float, Field(ge=20, le=80)] = 45.0


class RuntimeSettings(_Model):
    prices: PriceSettings = Field(default_factory=PriceSettings)
    tariff: TariffSettings = Field(default_factory=TariffSettings)
    feed_in: FeedInSettings = Field(default_factory=FeedInSettings)
    pv_model: PvModelSettings = Field(default_factory=PvModelSettings)


def settings_warnings(settings: RuntimeSettings) -> list[str]:
    """Hinweise auf noch fehlende Werte (Inbetriebnahme)."""
    warnings = [
        f"Tarifbestandteil „{component.name}“ hat noch keinen Wert."
        for component in settings.tariff.components
        if component.source == "fixed" and component.value_ct is None
    ]
    if not settings.feed_in.monthly_ct:
        warnings.append("Noch kein OeMAG-Monatswert eingetragen.")
    return warnings
