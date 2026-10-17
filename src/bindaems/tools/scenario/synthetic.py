"""Erzeugte Tage für die Szenarien der CI (Plan 3b, Task 15).

Vier Arten, je in lokaler Zeit (Europe/Vienna) und mit ``random.Random(seed)`` reproduzierbar:

- ``sommer-sonnig``: PV 9000 W von 05:30 bis 20:30; Börsenpreis 9 ct, 10–16 Uhr 2 ct (am zweiten
  Tag 13–14 Uhr −1 ct), 18–21 Uhr 18 ct.
- ``winter-spread``: PV 800 W von 08:00 bis 16:30; Börsenpreis 0–5 Uhr 7 ct, 17–20 Uhr 32 ct,
  sonst 12 ct. Der Plan nennt 2000 W; damit lädt der PV-Überschuss (rund 6 kWh am Tag) den Akku
  jeden Tag weiter, als der Abend von 17 bis 20 Uhr braucht (rund 5 kWh): „Victron allein“ bezieht
  dann nie zum Spitzenpreis, und Netzladen für die übrigen Slots verbietet die Spread-Regel
  (0,88 · (26,4 − 2) − 20,4 = 1,07 < 2 ct). Unter Verschleiß und Marge ließe ein solcher Tag keinen
  Vorteil des Netzladens zu.
- ``winter-leistung``: wie ``winter-spread``, dazu ein Leistungspreis von 30/60 €/kW/Jahr ab
  1. 1. 2027.
- ``wechselhaft``: PV 1500 W von 06:30 bis 19:30, je Slot mal einem Wolkenfaktor 0,2–1 als
  Zufallsweg; Börsenpreis 0–5 Uhr 10 ct, 17–21 Uhr 22 ct, sonst 6 ct. Der Plan nennt 6000 W; damit
  (und bis 1600 W bei einer der Saaten 1–5) füllt die PV den Akku für beide Abende, „Victron allein“
  bezieht nie zum Spitzenpreis, und der Tag zeigte nur, dass der Plan nicht schadet. 1500 W ist die
  höchste Spitze, bei der „Victron allein“ für jede dieser Saaten mehr als 1 kWh zum Spitzenpreis
  bezieht.

PV hat die Form ``Spitze · sin(π · Anteil des Tagbogens)^1,5`` (in der Mitte des Slots); die
Last ist 350 W, 06:30–08:00 800 W mehr und 17:30–21:30 1500 W mehr, je Slot ±15 %. Prognosen
sind Ist mal ``1 + e · k`` mit ``e`` je Stunde gleichverteilt in ±0,2 (PV) bzw. ±0,1 (Last) und
``k`` = 0,5, 1 und 1,5 für ``h0_6``, ``h6_24`` und ``h24``.

Preise mit einem erfundenen Beispieltarif: Börsenpreis + 10 ct netto, USt. 20 %, OeMAG 6 ct.
Netzladen ist erlaubt; der Akku ist der aus ``deploy/config.example.yaml`` mit Start-SOC 50 %.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from types import MappingProxyType
from typing import Final

from bindaems.app.forecast.archive import HorizonClass
from bindaems.app.tariff.calc import feed_in_price, import_price
from bindaems.shared.settings import (
    EnergyComponent,
    FeedInSettings,
    PlanningSettings,
    PowerComponent,
    PriceComponent,
    TariffSettings,
)
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, local_day_slots
from bindaems.tools.scenario.data import ScenarioBattery, ScenarioFile, ScenarioSlot

DAY_TYPES: Final = ("sommer-sonnig", "winter-spread", "winter-leistung", "wechselhaft")

# Beispieltarif (erfunden): Börsenpreis + 10 ct netto, USt. 20 %, OeMAG 6 ct
MARKUP_NET_CT: Final = 10.0
VAT_PCT: Final = 20.0
OEMAG_CT: Final = 6.0
POWER_EUR_KW_YEAR: Final = (30.0, 60.0)  # bis und über der Stufe (Beispielwerte)
POWER_FROM: Final = date(2027, 1, 1)

# Akku und Netz wie ``deploy/config.example.yaml``: 30 kWh, Reserve 20 %, 10 kW / 12 kW,
# Sollwert beim Netzladen höchstens 8000 W, Netzbezug (35 A − 2 A) · 230 V · 3
BATTERY: Final = ScenarioBattery(
    usable_kwh=30.0,
    reserve_soc_pct=20.0,
    max_charge_w=10000.0,
    max_discharge_w=12000.0,
    soc_max_pct=100.0,
    max_grid_charge_setpoint_w=8000.0,
    grid_max_import_w=(35 - 2) * 230.0 * 3,
    start_soc_pct=50.0,
)

BASE_LOAD_W: Final = 350.0
LOAD_EXTRA: Final = ((time(6, 30), time(8), 800.0), (time(17, 30), time(21, 30), 1500.0))
LOAD_NOISE: Final = 0.15
PV_EXPONENT: Final = 1.5
PV_ERROR: Final = 0.2
LOAD_ERROR: Final = 0.1
LEAD_FACTOR: Mapping[HorizonClass, float] = MappingProxyType(
    {"h0_6": 0.5, "h6_24": 1.0, "h24": 1.5}
)
CLOUD_RANGE: Final = (0.2, 1.0)
CLOUD_START: Final = 0.6
CLOUD_STEP: Final = 0.15  # größter Schritt des Wolkenfaktors je Slot


@dataclass(frozen=True)
class _Kind:
    peak_w: float
    sunrise: time
    sunset: time
    spot_ct: tuple[tuple[time, time, float], ...]  # Fenster [von, bis) mit Börsenpreis netto
    other_ct: float  # Börsenpreis außerhalb der Fenster
    clouds: bool = False
    power_price: bool = False


# 800 W statt 2000 W aus dem Plan (siehe oben): Der PV-Überschuss deckt den Abend nicht mehr.
_WINTER = _Kind(
    peak_w=800.0,
    sunrise=time(8),
    sunset=time(16, 30),
    spot_ct=((time(0), time(5), 7.0), (time(17), time(20), 32.0)),
    other_ct=12.0,
)
KINDS: Mapping[str, _Kind] = MappingProxyType(
    {
        "sommer-sonnig": _Kind(
            peak_w=9000.0,
            sunrise=time(5, 30),
            sunset=time(20, 30),
            spot_ct=((time(10), time(16), 2.0), (time(18), time(21), 18.0)),
            other_ct=9.0,
        ),
        "winter-spread": _WINTER,
        "winter-leistung": replace(_WINTER, power_price=True),
        "wechselhaft": _Kind(
            peak_w=1500.0,  # 1500 W statt 6000 W aus dem Plan (siehe oben)
            sunrise=time(6, 30),
            sunset=time(19, 30),
            spot_ct=((time(0), time(5), 10.0), (time(17), time(21), 22.0)),
            other_ct=6.0,
            clouds=True,
        ),
    }
)
NEGATIVE_SPOT: Final = ("sommer-sonnig", 1, time(13), time(14), -1.0)  # Art, Tag, von, bis, ct


def example_tariff(*, power_price: bool) -> TariffSettings:
    """Beispieltarif der erzeugten Tage; mit ``power_price`` samt Leistungspreis."""
    components: list[PriceComponent] = [
        EnergyComponent(id="spot", name="Börsenpreis", source="spot"),
        EnergyComponent(id="aufschlag", name="Aufschlag (Beispiel)", value_ct=MARKUP_NET_CT),
    ]
    if power_price:
        below, above = POWER_EUR_KW_YEAR
        components.append(
            PowerComponent(
                id="power_price",
                name="Leistungspreis (Beispiel)",
                value_eur=below,
                value_above_eur=above,
                valid_from=POWER_FROM,
            )
        )
    return TariffSettings(vat_pct=VAT_PCT, components=components)


def _within(clock: time, start: time, end: time) -> bool:
    return start <= clock < end


def _spot_ct(kind_name: str, kind: _Kind, day_index: int, clock: time) -> float:
    name, special_day, start, end, value = NEGATIVE_SPOT
    if kind_name == name and day_index == special_day and _within(clock, start, end):
        return value
    for window_start, window_end, value in kind.spot_ct:
        if _within(clock, window_start, window_end):
            return value
    return kind.other_ct


def _pv_shape_w(kind: _Kind, local_mid: datetime) -> float:
    """PV in der Mitte des Slots: ``Spitze · sin(π · Anteil)^1,5`` zwischen Auf- und Untergang."""
    day = local_mid.date()
    rise = datetime.combine(day, kind.sunrise, tzinfo=LOCAL_TZ)
    down = datetime.combine(day, kind.sunset, tzinfo=LOCAL_TZ)
    share = (local_mid - rise) / (down - rise)
    if not 0 < share < 1:
        return 0.0
    return kind.peak_w * float(math.sin(math.pi * share) ** PV_EXPONENT)


def _base_load_w(clock: time) -> float:
    return BASE_LOAD_W + sum(
        extra for start, end, extra in LOAD_EXTRA if _within(clock, start, end)
    )


def synthetic(kind: str, first: date, days: int, *, seed: int = 1) -> ScenarioFile:
    """``days`` erzeugte Tage der Art ``kind`` ab dem lokalen Tag ``first``."""
    spec = KINDS.get(kind)
    if spec is None:
        raise ValueError(f"Unbekannte Art: {kind} (bekannt: {', '.join(DAY_TYPES)})")
    if days < 1:
        raise ValueError("Mindestens ein Tag nötig")
    rng = random.Random(seed)  # noqa: S311 - Saat der erzeugten Tage, kein Geheimnis
    tariff = example_tariff(power_price=spec.power_price)
    feed_in = FeedInSettings(monthly_ct={first.strftime("%Y-%m"): OEMAG_CT})
    cloud = CLOUD_START
    errors: dict[datetime, tuple[float, float]] = {}  # Fehler je Stunde (UTC): PV, Last
    slots: list[ScenarioSlot] = []
    for day_index in range(days):
        for start in local_day_slots(first + timedelta(days=day_index)):
            local = start.astimezone(LOCAL_TZ)
            clock = local.time()
            if spec.clouds:
                low, high = CLOUD_RANGE
                cloud = min(high, max(low, cloud + rng.uniform(-CLOUD_STEP, CLOUD_STEP)))
            pv_w = _pv_shape_w(spec, local + SLOT / 2) * (cloud if spec.clouds else 1.0)
            load_w = _base_load_w(clock) * (1 + rng.uniform(-LOAD_NOISE, LOAD_NOISE))
            hour = start.replace(minute=0)
            if hour not in errors:
                errors[hour] = (
                    rng.uniform(-PV_ERROR, PV_ERROR),
                    rng.uniform(-LOAD_ERROR, LOAD_ERROR),
                )
            pv_error, load_error = errors[hour]
            spot = _spot_ct(kind, spec, day_index, clock)
            slots.append(
                ScenarioSlot(
                    start=start,
                    pv_w=pv_w,
                    load_w=load_w,
                    import_ct=import_price(start, spot, tariff).gross_ct,
                    feed_in_ct=feed_in_price(start, feed_in, tariff),
                    pv_forecast_w={
                        cls: pv_w * (1 + pv_error * k) for cls, k in LEAD_FACTOR.items()
                    },
                    load_forecast_w={
                        cls: load_w * (1 + load_error * k) for cls, k in LEAD_FACTOR.items()
                    },
                )
            )
    return ScenarioFile(
        version=1,
        created_at=datetime.combine(first, time(0), tzinfo=LOCAL_TZ),
        source="synthetic",
        name=f"{kind} ab {first.isoformat()}, {days} Tag{'e' if days > 1 else ''}",
        battery=BATTERY,
        planning=PlanningSettings(grid_charge_allowed=True),
        tariff=tariff,
        month_peak_kw=None,
        slots=slots,
    )
