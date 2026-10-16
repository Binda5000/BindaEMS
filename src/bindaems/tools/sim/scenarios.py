"""Szenarien des Simulators (Design 13.2): Anlage, Absicht, Wetter, Ereignisse und Prüfungen.

Ein ``Scenario`` beschreibt einen Lauf (``runner.run_scenario``): Start in UTC, Dauer, Saat, den
Aufbau des Modells (``setup``), die Absicht (``intent``; ``live`` schaltet EVCS und Fahrzeuge mit
Tessie live, ``victron_live`` dazu Victron), Werte für die Regelung (``control``), die Ereignisse je
Sekunde und die eigenen Prüfungen am Ergebnis (``checks``). Ort, PV-Wechselrichter und alle Grenzen
kommen aus der Konfiguration des Laufs, in der CI aus ``deploy/config.example.yaml``; die Szenarien
nennen deren Fahrzeuge ``tesla`` und ``egolf`` und Wallboxen ``twc`` und ``evcs``.

**Prüfungen** sind ``Checks``: je ``Check`` ein Satz, was er verlangt, und eine Funktion, die je
Verstoß einen Satz liefert. So zeigt der Bericht jede Prüfung mit OK oder FEHLER.

**Wetter und Hauslast** sind in jeder Sekunde ein Ereignis (``_Weather``): die PV und die Hauslast
je Phase, ``HOUSE_W`` mit einem gleichverteilten Rauschen von ± ``HOUSE_NOISE_W`` und den Zuschlägen
des Szenarios. Die Sonne ist ein halber Sinus über ``DAYLIGHT_H`` Stunden mit ``SUN_PEAK_W`` um
``SOLAR_NOON_H`` Uhr Wiener Zeit (``sun_w``). Rauschen und Wolken zieht je ein eigener Generator aus
der Saat des Szenarios; dieselbe Saat ergibt dieselben Werte, auch in einem zweiten Lauf.

Zu Beginn stecken die Autos an ihrer Standard-Wallbox, laden aber nicht, und der Tesla ist wach,
soweit ein Szenario nichts anderes sagt. Der Gast des Modells (``GUEST_CAR``) steht abgesteckt, und
Home Assistant meldet den e-Golf zu Hause; ein Szenario ändert das mit ``_setup`` (``guest``,
``away``). Die Absicht verlangt den Regelbetrieb. Andere Ereignisse sind Ausfälle (``_Outage``),
eine neue Version der Absicht mit Übersteuerung (``_Override``) oder mit Boost (``_Boost``) und das
Abstecken (``_Unplug``).

Abweichungen von der Tabelle des Plans (Entscheidung des Koordinators zu Task 14): In ``sonnig``
und ``wolkig`` steht der Tesla knapp unter seinem Ladelimit, und die Prüfungen verlangen, dass die
Autos nacheinander laden; ``reserve`` hat 1 kW mehr Hauslast auf L1, damit das Importziel greift.

Die Szenarien für Akku und Spitzen (Plan 2c-2a, Design 10.2) schalten Victron live. Abweichungen
von der Tabelle des Plans 2c-2a, weil ihre Erwartung im Modell nicht zu schaffen ist:

- In ``akku-minpv`` steht der Tesla zu Beginn auf seinem Mindeststrom von 5 A (``TESLA_MIN_A``).
  Sonst lädt er nach dem Start 90 s mit 16 A: Der erste Befehl auf 5 A geht über Tessie verloren,
  und der Neuversuch folgt im Abstand von ``current_interval_s`` (Plan 2c-1, Design 7.2). Der Akku
  liefert dann bis zu 5,8 kW in die Autos, denn Regel 2 begrenzt den Override auf
  ``max_grid_charge_setpoint_w`` (8 kW).
- In ``schieflast-pv`` kommt die schwere Last auf L1 erst nach 10 min dazu (``UNBALANCED_FROM_S``),
  wenn der e-Golf schon lädt. Von Anfang an startete er nie: Der Phasenwächter zählt die
  Ladeleistung des Akkus zur Last der Phase (Plan 2c-1, Design 5.1) und lässt auf L1 nur etwa 3 A
  frei, weniger als den Mindeststrom. Lädt der e-Golf, lädt der Akku auf jeder Phase je Ampere des
  e-Golf 2/3 A weniger, und das Importziel bindet ihn zwischen Mindest- und Höchststrom.

``spitze-15min`` startet den Tesla wie im Plan mit 16 A. Die Prüfung der Reihenfolge belegt dort
trotzdem wenig: In der ersten Viertelstunde senkt der Wächter den Tesla in Min+PV um rund 270 W
(seine Grenze bindet nicht, denn ohne PV geht er ohnehin auf 5 A), danach deckt die gemerkte
Senkung das Band unter der festen Grenze von Regel 2 (Plan 2c-2a, Präzisierung 10). Den e-Golf in
Sofort drosselt er erst ab 18:15, wenn der Tesla mit 5 A lädt. Auch ein Wächter, der Sofort vor
Min+PV drosselte, bestünde die Prüfung: Seine Senkung des e-Golf um 1 A fiele um 18:00 mit der des
Phasenwächters zusammen, und die Entscheidung nennte ``phase_limit``, nicht ``peak_guard``. Die
Reihenfolge der Drosselung sichern ``tests/unit/core/control/test_peak_throttle.py`` und
``tests/property/test_peak_guard.py``.

Die Szenarien für die Zuordnung (Plan 2c-2b, Design 10.3) lassen Victron im Dry-Run. ``zuordnung``
ändert sich bewusst (Design 10.3): Das Bild „Tesla an der EVCS“ ist jetzt erkannt und steht in
``tesla-evcs``; ``zuordnung`` prüft „unklar“ an einem echten Widerspruch. Der Tesla steckt dort
woanders (``tesla_elsewhere``), Tessie meldet ihn zu Hause und angesteckt, und weil der Wall
Connector leer ist, stützt ihn an der EVCS auch der Ausschluss; dort lädt aber der e-Golf auf zwei
Phasen, und die Phasen-Signatur schließt den Tesla aus. Die EVCS lädt den e-Golf schon vor dem
Start des core (``evcs_on``): So zählt die Phasen-Signatur im selben Zyklus, in dem die 60 s des
Tesla als Kandidat um wären, und er wird nie kurz erkannt. Lüde die EVCS erst auf den ersten
Befehl der Regelung, gälte der Tesla dort einige Sekunden als erkannt, und die EVCS regelte so
lange in seinem Modus. Unklar ist die Zuordnung so ab rund 3 min: Nach dem Start beobachtet der
core rund 60 s, dann folgen 60 s Phasen-Signatur und 60 s Widerspruch.

Abweichung von der Tabelle des Plans 2c-2b (Entscheidung des Koordinators zu Task 8):
``tesla-evcs`` verlangt „erkannt“ vor 3 min statt binnen 2 min (Design 10.3). Die Erkennung beginnt
erst mit der Regelung, rund 60 s nach dem Start, und braucht 60 s; die 2 min des Designs zählen ab
dem Beginn der Regelung.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from bindaems.core.control.fleet import GUEST
from bindaems.shared.control import (
    VICTRON,
    ChargeMode,
    CommandKind,
    GuestIntent,
    Intent,
    VehicleIntent,
    vehicle_actuator,
    wallbox_actuator,
)
from bindaems.shared.decisions import DecisionResult
from bindaems.shared.numbers import de, finite_number
from bindaems.shared.timeutil import LOCAL_TZ
from bindaems.tools.sim.cars import Evcs
from bindaems.tools.sim.metrics import (
    MINIMUM_MARGIN_A,
    PV_MODES,
    TRANSITION_S,
    battery_into_car_pct,
    cars_w,
    shown_number,
)
from bindaems.tools.sim.model import CERBO, EVCS, GRID, GUEST_CAR, TESSIE, TWC, PlantModel

if TYPE_CHECKING:
    from bindaems.core.runtime import CoreRuntime
    from bindaems.shared.decisions import Decision
    from bindaems.tools.sim.metrics import Sample
    from bindaems.tools.sim.runner import SimResult

Action = Callable[[PlantModel, "CoreRuntime"], None]  # ein Ereignis

MINUTE_S: Final = 60
HOUR_S: Final = 3600
HOUSE_W: Final = 300.0  # Hauslast je Phase in W
HOUSE_NOISE_W: Final = 50.0  # Rauschen der Hauslast je Phase und Sekunde, ± in W
SUN_PEAK_W: Final = 9000.0  # PV eines sonnigen Tages am Mittag
SOLAR_NOON_H: Final = 13.0  # Mittag in Wiener Zeit (Sommerzeit)
DAYLIGHT_H: Final = 16.0  # Tageslänge Ende Juni: Sonne von 5 bis 21 Uhr
CLOUD_FACTORS: Final = (1.0, 0.25)  # Sonne und Wolke im Wechsel, Anteil an der PV
CLOUD_SWITCH_S: Final = (30, 180)  # so lange bleibt es sonnig bzw. bewölkt, gleichverteilt

MORNING: Final = datetime(2026, 6, 21, 9, tzinfo=LOCAL_TZ).astimezone(UTC)
SUMMER: Final = datetime(2026, 6, 21, 10, tzinfo=LOCAL_TZ).astimezone(UTC)
EVENING: Final = datetime(2026, 10, 12, 18, tzinfo=LOCAL_TZ).astimezone(UTC)
PEAK_W: Final = 2500.0  # Lastspitze auf L1 am Abend …
PEAK_S: Final = (20 * MINUTE_S, 25 * MINUTE_S)  # … von 20 bis 25 min
PEAK_REACTION_S: Final = 10  # so lange darf die Regelung auf die Spitze reagieren
RAMP_W: Final = 3000.0  # Rampe auf L1 bei „Nur Laden“ …
RAMP_S: Final = (20 * MINUTE_S, 40 * MINUTE_S)  # … von 20 bis 40 min, danach bleibt die Last
RESERVE_PCT: Final = 20.0  # unter diese USV-Reserve entlädt das Modell nie (Szenario „reserve“)
RESERVE_L1_W: Final = 1000.0  # dort mehr Hauslast auf L1: frei ≈ 13 A unter dem Importziel
SUNNY_TESLA_PCT: Final = 72.0  # SOC des Tesla in „sonnig“: „Complete“ nach etwa einer Stunde
CLOUDY_TESLA_PCT: Final = 75.0  # dasselbe in „wolkig“, wo er im Mittel weniger Strom bekommt
# „sonnig“: höchstens so viele Strombefehle an die EVCS je Stunde Laden des e-Golf (Design 6.5)
EVCS_CURRENTS_PER_HOUR: Final = 10

FIVE_A_PV_W: Final = 4600.0  # konstante PV in „tesla-5a“: Der Anteil des Tesla ist 5 A …
FIVE_A: Final = 5.0  # … und mit diesem Strom lädt er am Ende
TESLA_START_A: Final = 10  # Strom im Tesla zu Beginn von „tesla-5a“ und „tesla-evcs“
TESLA_MAX_A: Final = 16.0  # Höchststrom des Tesla in der Beispielkonfiguration („tesla-evcs“)
SLEEPY_PV_W: Final = 6000.0  # „tesla-schlaeft“: Die PV steigt linear auf 6 kW …
SLEEPY_RAMP_S: Final = 30 * MINUTE_S  # … in 30 min und bleibt dann
# „ausfaelle“: je Ausfall Name, Beginn und Ende in Minuten, einer nach dem anderen
OUTAGE_MIN: Final = (
    (EVCS, 10, 15),
    (TWC, 20, 25),
    (TESSIE, 30, 35),
    (CERBO, 40, 41),
    (GRID, 50, 53),
)
SAFE_OUTAGES: Final = (CERBO, GRID)  # bei diesen Ausfällen ist der core im sicheren Zustand
RESUMED_S: Final = 60 * MINUTE_S  # ab hier laden in „ausfaelle“ wieder beide Autos
OVERRIDE_S: Final = 10 * MINUTE_S  # „zuordnung“: Übersteuerung „egolf“ an „evcs“ …
UNPLUG_S: Final = 40 * MINUTE_S  # … Abstecken …
EVCS_OFF_S: Final = 41 * MINUTE_S  # … und ab hier lädt die EVCS nicht mehr

# Szenarien für Akku und Spitzen (Plan 2c-2a, Design 10.2)
SETTLED_S: Final = 2 * MINUTE_S  # ab hier prüfen „akku-sofort“, „boost-abend“, „override-stunde“
CAR_FROM_BATTERY_PCT: Final = 5.0  # „akku-wolkig“: so viel der Energie des e-Golf aus dem Akku
TESLA_MIN_A: Final = 5  # „akku-minpv“: Strom im Tesla zu Beginn (Mindeststrom)
# „akku-minpv“: Akku in die Autos höchstens SUPPORT_W von SUPPORT_FROM_S bis SUPPORT_UNTIL_S nach
# dem ersten Laden (die Unterstützung endet nach support_max_s, 900 s), ab GRID_ONLY_S danach
# höchstens GRID_ONLY_W. SUPPORT_W ist support_max_w (3000 W) plus das Totband des Overrides
# (setpoint_deadband_w, 200 W): So weit darf der Bedarf wandern, ehe der Override folgt.
# GRID_ONLY_W ist das Totband allein.
SUPPORT_W: Final = 3200.0
SUPPORT_FROM_S: Final = 10
SUPPORT_UNTIL_S: Final = 890
GRID_ONLY_W: Final = 200.0
GRID_ONLY_S: Final = 930
LOADS_ABOVE_W: Final = 8300.0  # „akku-sofort“: Ziehen Autos und Haus mehr, …
GRID_BETWEEN_W: Final = (7800.0, 8200.0)  # … liegt der Netzbezug hier, um 8 kW (Regel 2)
BOOST_SOC_PCT: Final = 25.0  # „boost-abend“: Über diesem SOC (Reserve + reserve_guard_pct) …
BOOST_GRID_W: Final = 300.0  # … zieht das Haus höchstens so viel aus dem Netz
OVERRIDE_AGE_S: Final = 245.0  # „override-stunde“: höchstens so lange seit dem letzten Schreiben
VICTRON_DECISIONS: Final = 5  # … und höchstens so viele Entscheidungen an Victron
LOCKED_FROM_S: Final = MINUTE_S  # „dess-an“: ab hier ist Victron für den Akku-Sollwert gesperrt
PEAK_TARGET_KW: Final = 8.0  # „spitze-15min“: das 15-min-Ziel; der Lauf beginnt …
PEAK_LEAD_S: Final = MINUTE_S  # … so lange vor 18:00, der core regelt ab der Viertelstunde
QUARTER_S: Final = 15 * MINUTE_S
PEAK_QUARTERS: Final = 3  # … so viele Viertelstunden ab 18:00 prüft er, je mit höchstens …
QUARTER_KWH: Final = 2.02  # … so viel Netzbezug (8 kW · 0,25 h + 1 %) …
PEAK_BOOST_S: Final = 46 * MINUTE_S  # … und ab hier (18:45) lädt der e-Golf in Boost
UNBALANCED_PV_W: Final = 12000.0  # „schieflast-pv“: konstante PV …
UNBALANCED_L1_W: Final = 5500.0  # … und so viel mehr Last auf L1 …
UNBALANCED_FROM_S: Final = 10 * MINUTE_S  # … ab hier, wenn der e-Golf schon lädt
# Szenarien „Aus“ und Selbststart des Tesla (Plan 2d, Task 6)
OFF_AT_S: Final = 45 * MINUTE_S  # „tesla-aus“: der Tesla geht auf „Aus“ …
OFF_WITHIN_S: Final = 10  # … und bekommt binnen so vielen Sekunden „Laden aus“
OFF_TESLA_PCT: Final = 45.0  # SOC des Tesla in „tesla-aus“: Er lädt auch nach 45 min noch
SELF_START_S: Final = 5 * MINUTE_S  # „tesla-selbststart“: der Tesla startet selbst …
# … und bekommt binnen 120 s (control.tesla_against_s) plus 60 s Abfrage von Tessie plus dem
# Verzug des Befehls „Laden aus“
SELF_START_WITHIN_S: Final = 210
IDLE_FROM_S: Final = 9 * MINUTE_S  # … und lädt ab hier nicht mehr
SELF_START_TESLA_PCT: Final = 50.0
PEAK_GUARD: Final = "peak_guard"  # Grund einer Drosselung durch den 15-min-Wächter

TESLA: Final = "tesla"  # Schlüssel der Fahrzeuge und Wallboxen in der Konfiguration
EGOLF: Final = "egolf"
WALL_CONNECTOR: Final = "twc"
EVCS_BOX: Final = "evcs"
WAKE: Final = "wake"  # Grund eines Weckstarts
UNCLEAR: Final = "unclear"  # Zuordnung „unklar“ in der Gesundheit
DETECTED: Final = "detected"  # Zuordnung „erkannt“ in der Gesundheit
_SHOWN: Final = {UNCLEAR: "unklar", DETECTED: "erkannt"}  # so nennen die Prüfungen die Zuordnung
SAFE: Final = "SAFE"
FOREIGN_ALARM: Final = "competitor.foreign."  # Anfang der Alarme fremder Änderungen
# Entscheidungen ohne Befehl der Regelung an den Aktor: abgelehnt, wegen eines Stundenlimits
# zurückgestellt oder eine fremde Änderung (sie nennt den erwarteten Wert, aber das EMS sendet
# nichts); neue Ergebnisse zählen so nicht ungefragt als Befehl
_HELD_BACK: Final = frozenset(
    {DecisionResult.REJECTED, DecisionResult.DEFERRED, DecisionResult.FOREIGN}
)
# dieselben ohne die abgelehnten: Eine abgelehnte Entscheidung zählt als Entscheidung, eine
# zurückgestellte nicht (ihr Befehl kommt noch einmal), eine fremde Änderung auch nicht
_NOT_THE_COMMAND: Final = frozenset({DecisionResult.DEFERRED, DecisionResult.FOREIGN})


@dataclass(frozen=True)
class Check:
    """Eine Prüfung eines Szenarios: ``text`` sagt, was sie verlangt; ``find`` liefert je Verstoß
    einen Satz, bestanden ist sie ohne."""

    text: str
    find: Callable[[SimResult], list[str]]


@dataclass(frozen=True)
class Checks:
    """Die Prüfungen eines Szenarios; der Aufruf liefert alle Verstöße in ihrer Reihenfolge."""

    items: tuple[Check, ...]

    def __call__(self, result: SimResult) -> list[str]:
        return [found for check in self.items for found in check.find(result)]


@dataclass(frozen=True)
class Scenario:
    """Ein Szenario des Simulators.

    - ``name``: Name für den Aufruf, ``title``: Überschrift des Berichts
    - ``start``: Beginn in UTC, ``duration_s``: Dauer in s, ``seed``: Saat aller Zufallswerte
    - ``setup``: stellt das Modell vor der ersten Sekunde ein (Akku, Autos, Multi)
    - ``intent``: die Absicht; mit ``live`` sind EVCS und Fahrzeuge mit Tessie live, sonst im
      Dry-Run
    - ``events``: Sekunde und Aktion, vor der Physik dieser Sekunde, in ihrer Reihenfolge
    - ``checks``: die eigenen Prüfungen, meist ``Checks``; je Verstoß ein Satz
    - ``victron_live``: Victron (der Akku-Sollwert) ist zusätzlich live; ohne bleibt es im Dry-Run
    - ``control``: Werte für ``ControlConfig``, die die Konfiguration des Laufs überschreiben, etwa
      ``{"peak_target_kw": 8.0}``; ein ungültiger Wert ist ein ``ValidationError``
    """

    name: str
    title: str
    start: datetime
    duration_s: int
    seed: int
    setup: Callable[[PlantModel], None]
    intent: Intent
    live: bool
    events: tuple[tuple[int, Action], ...]
    checks: Callable[[SimResult], list[str]]
    victron_live: bool = False
    control: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class _Weather:
    """PV und Hauslast je Phase einer Sekunde, als Ereignis."""

    pv_w: float
    house_w: tuple[float, float, float]

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        model.pv_w = self.pv_w
        model.house_w = self.house_w


@dataclass(frozen=True, slots=True)
class _Outage:
    """Beginn (``begins``) oder Ende eines Ausfalls aus ``model.OUTAGES``, als Ereignis."""

    name: str
    begins: bool

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        if self.begins:
            model.outages.add(self.name)
        else:
            model.outages.discard(self.name)


def _new_version(
    rt: CoreRuntime, version: int, vehicle: str, change: Mapping[str, Any], event: str
) -> None:
    """Reicht die gültige Absicht als Version ``version`` ein, mit dem Eintrag des Fahrzeugs
    ``vehicle`` um ``change`` geändert (``event`` nennt das Ereignis in den Fehlern).

    Sonst bleibt die gültige Absicht, wie sie ist, auch ihre Betriebsarten. Ohne gültige Absicht
    und bei einer Ablehnung bricht der Lauf ab (``ValueError``).
    """
    current = rt.intent()
    if current is None:
        raise ValueError(f"{event} ohne gültige Absicht.")
    entry = current.vehicles.get(vehicle, VehicleIntent())
    vehicles = current.vehicles | {vehicle: entry.model_copy(update=change)}
    reply = rt.submit_intent(current.model_copy(update={"version": version, "vehicles": vehicles}))
    if reply.result == "rejected":
        raise ValueError(f"{event} abgelehnt: {' '.join(reply.details)}")


@dataclass(frozen=True, slots=True)
class _Override:
    """Eine neue Version der Absicht: Das Fahrzeug ``vehicle`` hängt an der Wallbox ``wallbox``.

    Sonst bleibt die gültige Absicht, wie sie ist, auch ihre Betriebsarten. Ohne gültige Absicht
    und bei einer Ablehnung bricht der Lauf ab (``ValueError``).
    """

    version: int
    vehicle: str
    wallbox: str

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        _new_version(rt, self.version, self.vehicle, {"wallbox": self.wallbox}, "Übersteuerung")


@dataclass(frozen=True, slots=True)
class _Boost:
    """Eine neue Version der Absicht: Das Fahrzeug ``vehicle`` lädt in Boost bis ``until``.

    Das Ende ist absolut (UTC), denn ein Ereignis kennt den Beginn des Szenarios nicht; das
    Szenario rechnet es aus seinem ``start``. Sonst wie ``_Override``: Die gültige Absicht bleibt,
    wie sie ist; ohne gültige Absicht und bei einer Ablehnung bricht der Lauf ab (``ValueError``).
    """

    version: int
    vehicle: str
    until: datetime

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        _new_version(rt, self.version, self.vehicle, {"boost_until": self.until}, "Boost")


@dataclass(frozen=True, slots=True)
class _Mode:
    """Eine neue Version der Absicht: Das Fahrzeug ``vehicle`` lädt im Modus ``mode``.

    Sonst wie ``_Override``: Die gültige Absicht bleibt, wie sie ist; ohne gültige Absicht und bei
    einer Ablehnung bricht der Lauf ab (``ValueError``).
    """

    version: int
    vehicle: str
    mode: ChargeMode

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        _new_version(rt, self.version, self.vehicle, {"mode": self.mode}, "Moduswechsel")


@dataclass(frozen=True, slots=True)
class _SelfStart:
    """Der Tesla ``vehicle`` startet selbst, etwa aus der App: „Laden an“ im Auto, als Ereignis.

    Der core sendet nichts dazu; das Auto lädt, sobald die Wallbox Strom gibt.
    """

    vehicle: str

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        model.teslas[self.vehicle].charge_enabled = True


@dataclass(frozen=True, slots=True)
class _Unplug:
    """Das Fahrzeug ``vehicle`` wird abgesteckt, als Ereignis."""

    vehicle: str

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        model.unplug(self.vehicle)


def sun_w(at: datetime) -> float:
    """Die PV eines sonnigen Tages zur Zeit ``at``: ein halber Sinus von Auf- bis Untergang."""
    local = at.astimezone(LOCAL_TZ)
    hours = local.hour + local.minute / 60 + local.second / 3600
    since_rise = hours - (SOLAR_NOON_H - DAYLIGHT_H / 2)
    if not 0 < since_rise < DAYLIGHT_H:
        return 0.0
    return SUN_PEAK_W * math.sin(math.pi * since_rise / DAYLIGHT_H)


def _clouds(seed: int, duration_s: int) -> list[float]:
    """Je Sekunde der Anteil der Sonne: ``CLOUD_FACTORS`` im Wechsel, sonnig zuerst."""
    rng = random.Random(f"wolken-{seed}")  # noqa: S311 - Saat der Simulation, kein Geheimnis
    factors: list[float] = []
    switches = 0
    while len(factors) < duration_s:
        factors += [CLOUD_FACTORS[switches % 2]] * rng.randint(*CLOUD_SWITCH_S)
        switches += 1
    return factors[:duration_s]


def _dark(t: int) -> float:
    return 0.0


def _weather(
    start: datetime,
    duration_s: int,
    seed: int,
    *,
    pv: Callable[[int], float] = _dark,
    l1: Callable[[int], float] = _dark,
) -> tuple[tuple[int, Action], ...]:
    """Je Sekunde ``t`` ein ``_Weather``: PV ``pv(t)`` und die Hauslast, auf L1 mit ``l1(t)``."""
    rng = random.Random(f"haus-{seed}")  # noqa: S311 - Saat der Simulation, kein Geheimnis

    def house() -> float:
        return HOUSE_W + rng.uniform(-HOUSE_NOISE_W, HOUSE_NOISE_W)

    return tuple(
        (t, _Weather(pv(t), (house() + l1(t), house(), house()))) for t in range(duration_s)
    )


def _sun(start: datetime) -> Callable[[int], float]:
    """Die Sonne ``t`` Sekunden nach ``start``."""

    def pv(t: int) -> float:
        return sun_w(start + timedelta(seconds=t))

    return pv


def _cloudy(start: datetime, duration_s: int, seed: int) -> Callable[[int], float]:
    """Die Sonne mit Wolken (``_clouds``), ``t`` Sekunden nach ``start``."""
    sun, clouds = _sun(start), _clouds(seed, duration_s)

    def pv(t: int) -> float:
        return sun(t) * clouds[t]

    return pv


def _setup(
    model: PlantModel,
    *,
    soc_pct: float,
    tesla: str | None = WALL_CONNECTOR,
    egolf: str | None = EVCS_BOX,
    charger_only: bool = False,
    foreign: bool = False,
    tesla_soc_pct: float | None = None,
    tesla_amps: int | None = None,
    tesla_starts: bool = False,
    asleep: bool = False,
    dess: bool = False,
    guest: str | None = None,
    guest_phases: int = 3,
    away: frozenset[str] = frozenset(),
    tesla_elsewhere: bool = False,
    evcs_on: bool = False,
) -> None:
    """Akku, Multi, fremder Regler und Dynamic ESS (``dess``); der Tesla an der Wallbox ``tesla``,
    der e-Golf an ``egolf`` (``None``: abgesteckt).

    Für den Tesla, wenn angegeben: SOC ``tesla_soc_pct`` und Strom ``tesla_amps``. Mit
    ``tesla_starts`` hat er „Laden an“ im Auto und lädt, sobald die Wallbox Strom gibt; mit
    ``asleep`` schläft er. Mit ``tesla_elsewhere`` steckt er woanders (``Tesla.elsewhere``): Tessie
    meldet ihn „Stopped“, obwohl er an keiner Wallbox steckt.

    Der Gast steckt an der Wallbox ``guest`` (``None``: abgesteckt) und lädt auf ``guest_phases``
    Phasen (1 bis 3). ``away`` nennt die Fahrzeuge, die Home Assistant nicht zu Hause meldet; ohne
    sind alle zu Hause. Mit ``evcs_on`` steht die EVCS ``EVCS_BOX`` schon vor dem Start des core auf
    „Laden an“ (ihr Sollstrom ist noch ihr sicherer Strom): Ein Auto an ihr lädt von der ersten
    Sekunde an.
    """
    model.soc_pct = soc_pct
    model.charger_only = charger_only
    model.foreign = foreign
    model.dess = dess
    car = model.teslas[TESLA]
    if tesla is not None:
        model.plug(TESLA, tesla)
    if tesla_soc_pct is not None:
        car.soc_pct = tesla_soc_pct
    if tesla_amps is not None:
        car.charge_amps = tesla_amps
    car.charge_enabled = tesla_starts
    car.asleep = asleep
    car.elsewhere = tesla_elsewhere
    if egolf is not None:
        model.plug(EGOLF, egolf)
    model.cars[GUEST_CAR].phases = guest_phases
    if guest is not None:
        model.plug(GUEST_CAR, guest)
    model.away = set(away)
    if evcs_on:
        box = model.wallboxes[EVCS_BOX]
        if not isinstance(box, Evcs):
            raise ValueError(f"Wallbox „{EVCS_BOX}“ ist keine EVCS.")
        box.start_stop = 1


def _intent(*, guest_mode: ChargeMode = ChargeMode.PV, **modes: ChargeMode) -> Intent:
    """Die Absicht mit Regelbetrieb, den Modi der Fahrzeuge und dem Modus des Gastes."""
    vehicles = {key: VehicleIntent(mode=mode) for key, mode in modes.items()}
    return Intent(version=1, control=True, vehicles=vehicles, guest=GuestIntent(mode=guest_mode))


# --- Prüfungen -----------------------------------------------------------------------------------


def _minutes(samples: Sequence[Sample], key: str) -> float:
    """So viele Minuten lädt das Auto ``key`` in ``samples``."""
    return sum(1 for s in samples if s.cars[key].charging) / MINUTE_S


def _one_after_the_other(result: SimResult, *, tesla_min: float, egolf_min: float) -> list[str]:
    """Erst lädt der Tesla bis „Complete“, mindestens ``tesla_min`` Minuten; danach lädt der
    e-Golf mindestens ``egolf_min`` Minuten. Der e-Golf startet erst, wenn der Tesla „Complete“
    erreicht hat."""
    samples = result.samples
    found: list[str] = []
    done = next((s.t for s in samples if s.cars[TESLA].complete), None)
    if done is None:
        found.append("„tesla“ erreicht „Complete“ nicht.")
    minutes = _minutes(samples, TESLA)
    if minutes < tesla_min:
        found.append(f"„tesla“ lädt {de(round(minutes, 1))} min, verlangt {de(tesla_min)} min.")
    started = next((s.t for s in samples if s.cars[EGOLF].charging), None)
    if started is not None and (done is None or started <= done):
        found.append(f"„egolf“ startet bei {started} s, bevor „tesla“ „Complete“ erreicht.")
    after = _minutes([s for s in samples if done is not None and s.t > done], EGOLF)
    if after < egolf_min:
        shown = de(round(after, 1))
        found.append(f"„egolf“ lädt danach {shown} min, verlangt {de(egolf_min)} min.")
    return found


def _one_after_the_other_check(*, tesla_min: float, egolf_min: float) -> Check:
    text = (
        f"Erst lädt „tesla“ bis „Complete“, mindestens {de(tesla_min)} min; danach lädt „egolf“ "
        f"mindestens {de(egolf_min)} min."
    )
    return Check(text, partial(_one_after_the_other, tesla_min=tesla_min, egolf_min=egolf_min))


def _few_evcs_currents(result: SimResult) -> list[str]:
    """Höchstens ``EVCS_CURRENTS_PER_HOUR`` Strombefehle an die EVCS je Stunde, in der der e-Golf
    lädt (Hysterese, Design 6.5). Es zählen Entscheidungen mit Wert, nicht zurückgestellte oder
    abgelehnte. Lädt er nicht, gibt es nichts zu prüfen; das meldet ``_one_after_the_other``."""
    minutes = _minutes(result.samples, EGOLF)
    if minutes == 0:
        return []
    evcs = wallbox_actuator(EVCS_BOX)
    count = sum(
        1
        for d in result.decisions
        if d.actuator == evcs and d.kind is CommandKind.CURRENT and d.result not in _HELD_BACK
    )
    per_hour = count * MINUTE_S / minutes
    if per_hour <= EVCS_CURRENTS_PER_HOUR:
        return []
    return [
        f"{count} Strombefehle an die EVCS in {de(round(minutes, 1))} min Laden von „egolf“: "
        f"{de(round(per_hour, 1))} je Stunde, verlangt höchstens {EVCS_CURRENTS_PER_HOUR}."
    ]


def _evcs_lower_during_the_peak(result: SimResult) -> list[str]:
    """Ab ``PEAK_REACTION_S`` nach Beginn der Spitze liegt der Strom der EVCS unter dem davor."""
    begin, end = PEAK_S
    before = [s.cars[EGOLF].current_a for s in result.samples if s.t == begin - 1]
    during = [
        s.cars[EGOLF].current_a for s in result.samples if begin + PEAK_REACTION_S <= s.t < end
    ]
    if not before or not during:
        return ["Die Lastspitze liegt nicht im Lauf."]
    if max(during) < before[0]:
        return []
    return [f"EVCS-Strom während der Spitze bis {de(max(during))} A, davor {de(before[0])} A."]


def _named(code: str, *, at: str | None = None) -> Check:
    """Mindestens eine Entscheidung nennt den Grund ``code``, mit ``at`` eine an diesen Aktor."""
    where = "" if at is None else f" an „{at}“"

    def find(result: SimResult) -> list[str]:
        if any(code in d.codes and at in (None, d.actuator) for d in result.decisions):
            return []
        return [f"Keine Entscheidung{where} mit „{code}“."]

    return Check(f"Eine Entscheidung{where} nennt „{code}“.", find)


def _reserve_kept(result: SimResult) -> list[str]:
    """Der SOC sinkt nie unter ``RESERVE_PCT``: eine Invariante des Modells, keine Prüfung der
    Regelung. Am Netz entlädt das Modell den Akku nie unter die USV-Reserve (``PlantModel``, ESS
    und Peak Shaving), was immer die Regelung tut; scheitern kann die Prüfung nur an einem Fehler
    des Modells. Ob die Regelung die Reserve schützt, prüft ``_import_target_kept``."""
    lowest = min((s.soc_pct for s in result.samples), default=RESERVE_PCT)
    if lowest < RESERVE_PCT:
        shown = de(round(lowest, 4))
        return [f"SOC bis {shown} %, unter der USV-Reserve von {de(RESERVE_PCT)} %."]
    return []


def _import_target_kept(result: SimResult) -> list[str]:
    """Unter der Reserve-Absicherung hält die Regelung das Importziel (Design 5.2), auch in Sofort.

    Solange der SOC unter USV-Reserve + ``control.reserve_guard_pct`` liegt und ein Auto live über
    seinem Mindeststrom lädt, liegt außerhalb von Übergängen (``TRANSITION_S``) keine Phase mit der
    Last ohne Akku-Hilfe über dem Ziel am Importlimit samt Toleranz
    (``Metrics.reserve_over_target_s``). An der Anlage zöge Peak Shaving den Akku sonst unter die
    Reserve; im Modell hält der Akku sie selbst ein (``_reserve_kept``), darum prüft erst diese
    Prüfung die Regelung.
    """
    longest = result.metrics.reserve_over_target_s
    if longest <= TRANSITION_S:
        return []
    return [
        f"Unter der Reserve-Absicherung {longest} s am Stück über dem Importziel, verlangt "
        f"höchstens {TRANSITION_S} s."
    ]


def _multi_mode_warns(result: SimResult) -> list[str]:
    """Die Selbstprüfung ``multi_mode`` warnt."""
    warned = any(
        check["id"] == "multi_mode" and check["status"] == "warn"
        for _, health in result.health
        for check in health.selfcheck
    )
    return [] if warned else ["Die Selbstprüfung „multi_mode“ warnt nicht."]


def _charges_with(key: str, amps: float) -> Check:
    """Das Auto ``key`` lädt in der letzten Sekunde des Laufs mit ``amps`` je Phase."""

    def find(result: SimResult) -> list[str]:
        last = result.samples[-1].cars[key] if result.samples else None
        if last is None or not last.charging:
            return [f"„{key}“ lädt am Ende nicht, verlangt {de(amps)} A."]
        if last.current_a != amps:
            return [f"„{key}“ lädt am Ende mit {de(last.current_a)} A, verlangt {de(amps)} A."]
        return []

    return Check(f"„{key}“ lädt am Ende mit {de(amps)} A.", find)


def _charges_at_the_end(key: str) -> Check:
    """Das Auto ``key`` lädt in der letzten Sekunde des Laufs."""

    def find(result: SimResult) -> list[str]:
        if result.samples and result.samples[-1].cars[key].charging:
            return []
        return [f"„{key}“ lädt am Ende nicht."]

    return Check(f"„{key}“ lädt am Ende.", find)


def _one_wake(key: str) -> Check:
    """Genau ein Weckvorgang: eine Entscheidung mit dem Grund ``wake`` und einem Wert, also weder
    abgelehnt noch zurückgestellt."""
    actuator = vehicle_actuator(key)

    def find(result: SimResult) -> list[str]:
        wakes = [
            d
            for d in result.decisions
            if d.actuator == actuator and WAKE in d.codes and d.result not in _HELD_BACK
        ]
        if len(wakes) == 1:
            return []
        return [f"{len(wakes)} Weckvorgänge an „{key}“, verlangt genau einer."]

    return Check(f"Genau ein Weckvorgang an „{key}“.", find)


def _safe_during(name: str, begin_min: int, end_min: int) -> Check:
    """Jede Gesundheit während des Ausfalls ``name`` zeigt SAFE, und es gibt mindestens eine."""
    begin, end = begin_min * MINUTE_S, end_min * MINUTE_S

    def find(result: SimResult) -> list[str]:
        during = [(t, health.mode) for t, health in result.health if begin <= t < end]
        if not during:
            return [f"Der Ausfall „{name}“ liegt nicht im Lauf."]
        return [
            f"Während des Ausfalls „{name}“ bei {t} s {mode}, verlangt SAFE."
            for t, mode in during
            if mode != SAFE
        ][:1]

    return Check(f"SAFE während des Ausfalls „{name}“ ({begin_min}–{end_min} min).", find)


def _all_charge_from(begin_s: int) -> Check:
    """Ab ``begin_s`` lädt jedes Fahrzeug der Konfiguration in jeder Sekunde des Laufs. Der Gast
    (``GUEST_CAR``) steht in jeder Probe, ist aber kein Fahrzeug der Konfiguration und zählt nicht:
    Abgesteckt lädt er nie."""

    def find(result: SimResult) -> list[str]:
        after = [s for s in result.samples if s.t >= begin_s]
        if not after:
            return [f"Der Lauf endet vor {begin_s} s."]
        found: list[str] = []
        for key in [key for key in after[0].cars if key != GUEST_CAR]:
            idle = next((s.t for s in after if not s.cars[key].charging), None)
            if idle is not None:
                found.append(f"„{key}“ lädt bei {idle} s nicht.")
        return found

    return Check(f"Ab {de(begin_s / MINUTE_S)} min laden beide Autos.", find)


def _shown_before(key: str, kind: str, until_s: int) -> Check:
    """Vor ``until_s`` zeigt die Gesundheit für ``key`` mindestens einmal die Zuordnung ``kind``,
    ``UNCLEAR`` oder ``DETECTED``."""
    word = _SHOWN[kind]

    def find(result: SimResult) -> list[str]:
        shown = any(
            vehicle["id"] == key and vehicle["assignment"] == kind
            for t, health in result.health
            if t < until_s
            for vehicle in health.vehicles
        )
        return [] if shown else [f"Die Gesundheit zeigt „{key}“ vor {until_s} s nie „{word}“."]

    return Check(f"Die Gesundheit zeigt „{key}“ vor {de(until_s / MINUTE_S)} min „{word}“.", find)


def _one_current(key: str, amps: float) -> Check:
    """Genau eine Entscheidung über den Strom ``amps`` an das Fahrzeug ``key``; eine
    zurückgestellte zählt nicht, denn ihr Befehl kommt danach noch einmal, und eine fremde
    Änderung auch nicht."""
    actuator = vehicle_actuator(key)

    def find(result: SimResult) -> list[str]:
        found = [
            d
            for d in result.decisions
            if d.actuator == actuator
            and d.kind is CommandKind.CURRENT
            and d.requested == amps
            and d.result not in _NOT_THE_COMMAND
        ]
        if len(found) == 1:
            return []
        return [
            f"{len(found)} Entscheidungen „Strom {de(amps)} A“ an „{key}“, verlangt genau eine."
        ]

    return Check(f"Genau eine Entscheidung „Strom {de(amps)} A“ an „{key}“.", find)


def _evcs_off_from(key: str, begin_s: int) -> Check:
    """Ab ``begin_s`` lädt die EVCS ``key`` nicht: Kein Auto lädt, ihr letzter Befehl „Laden“
    mit Wert davor ist „aus“, und danach kommt kein „Laden an“.

    Die Proben kennen Autos, nicht Ladepunkte: „Kein Auto lädt“ heißt darum gar keines, auch an
    einer anderen Wallbox. Das passt nur zu Szenarien, in denen ab ``begin_s`` an keiner anderen
    Wallbox ein Auto lädt, wie in „zuordnung“: Dort steckt der Tesla an keiner Wallbox, der e-Golf
    ab ``UNPLUG_S`` nicht mehr."""
    actuator = wallbox_actuator(key)
    valued = frozenset({DecisionResult.OK, DecisionResult.DRY_RUN})

    def find(result: SimResult) -> list[str]:
        found: list[str] = []
        charging = next((s.t for s in result.samples if s.t >= begin_s and _any_charging(s)), None)
        if charging is not None:
            found.append(f"Bei {charging} s lädt ein Auto.")
        switches = [
            (d.ts, d.applied)
            for d in result.decisions
            if d.actuator == actuator and d.kind is CommandKind.CHARGING and d.result in valued
        ]
        moment = result.scenario.start + timedelta(seconds=begin_s)
        before = [value for ts, value in switches if ts <= moment]
        if not before or before[-1] is not False:
            found.append(f"Die EVCS „{key}“ ist bei {begin_s} s nicht auf „Laden aus“.")
        if any(value is True for ts, value in switches if ts > moment):
            found.append(f"Die EVCS „{key}“ bekommt nach {begin_s} s „Laden an“.")
        return found

    return Check(f"Ab {de(begin_s / MINUTE_S)} min lädt die EVCS „{key}“ nicht.", find)


def _stops_within(actuator: str, at_s: int, within_s: int) -> Check:
    """Der Aktor ``actuator`` bekommt ab der Sekunde ``at_s`` binnen ``within_s`` s „Laden aus“:
    eine Entscheidung über ``charging`` mit dem Wert falsch, die ein Befehl war (nicht abgelehnt,
    zurückgestellt oder eine fremde Änderung); auch ein Dry-Run oder ein Fehlschlag zählt, denn der
    Befehl ging hinaus."""

    def find(result: SimResult) -> list[str]:
        begin = result.scenario.start + timedelta(seconds=at_s)
        end = begin + timedelta(seconds=within_s)
        stopped = any(
            d.actuator == actuator
            and d.kind is CommandKind.CHARGING
            and d.requested is False
            and d.result not in _HELD_BACK
            and begin <= d.ts <= end
            for d in result.decisions
        )
        if stopped:
            return []
        return [f"„{actuator}“ bekam nach {at_s} s kein „Laden aus“ binnen {within_s} s."]

    text = f"„{actuator}“ bekommt „Laden aus“ binnen {within_s} s nach {de(at_s / MINUTE_S)} min."
    return Check(text, find)


def _no_deferred(actuator: str, kind: CommandKind) -> Check:
    """Keine zurückgestellte Entscheidung der Art ``kind`` an den Aktor ``actuator``: Ein
    Stundenlimit hielt keinen solchen Befehl zurück."""

    def find(result: SimResult) -> list[str]:
        deferred = [
            d
            for d in result.decisions
            if d.actuator == actuator and d.kind is kind and d.result is DecisionResult.DEFERRED
        ]
        if not deferred:
            return []
        first = _second(result, min(d.ts for d in deferred))
        return [
            f"{len(deferred)} zurückgestellte Befehle „{kind.value}“ an „{actuator}“, "
            f"der erste bei {first} s."
        ]

    return Check(f"Kein zurückgestellter Befehl „{kind.value}“ an „{actuator}“.", find)


def _idle_from(key: str, begin_s: int) -> Check:
    """Das Auto ``key`` lädt in keiner Sekunde ab ``begin_s``; der Befund nennt die erste."""

    def find(result: SimResult) -> list[str]:
        charging = next(
            (s.t for s in result.samples if s.t >= begin_s and s.cars[key].charging), None
        )
        return [] if charging is None else [f"„{key}“ lädt bei {charging} s."]

    return Check(f"Ab {de(begin_s / MINUTE_S)} min lädt „{key}“ nicht.", find)


def _any_charging(sample: Sample) -> bool:
    return any(car.charging for car in sample.cars.values())


def _dry_runs_at(*actuators: str) -> Check:
    """Mindestens eine Dry-Run-Entscheidung an jedem der ``actuators``."""

    def find(result: SimResult) -> list[str]:
        decided = {d.actuator for d in result.decisions if d.result is DecisionResult.DRY_RUN}
        return [f"Keine Dry-Run-Entscheidung an „{a}“." for a in actuators if a not in decided]

    shown = " und ".join(f"„{actuator}“" for actuator in actuators)
    return Check(f"Mindestens eine Dry-Run-Entscheidung an {shown}.", find)


def _no_repeated_dry_runs(result: SimResult) -> list[str]:
    repeated = result.metrics.repeated_dry_runs
    return (
        []
        if repeated == 0
        else [f"Wiederholte Dry-Run-Entscheidungen: {repeated}, verlangt keine."]
    )


def _no_foreign_alarm(result: SimResult) -> list[str]:
    """Keine Gesundheit nennt einen Alarm ``competitor.foreign.*``."""
    for t, health in result.health:
        for alarm in health.alarms:
            if alarm["id"].startswith(FOREIGN_ALARM):
                return [f"Alarm „{alarm['id']}“ bei {t} s."]
    return []


# --- Prüfungen für Akku und Spitzen --------------------------------------------------------------


def _second(result: SimResult, ts: datetime) -> int:
    """Die Sekunde des Laufs, in der der Zeitpunkt ``ts`` liegt."""
    return math.floor((ts - result.scenario.start).total_seconds())


def _hhmm(start: datetime, t: int) -> str:
    """Die Sekunde ``t`` eines Laufs ab ``start`` als Wiener Uhrzeit, etwa „18:45“."""
    return f"{(start + timedelta(seconds=t)).astimezone(LOCAL_TZ):%H:%M}"


def _at(decisions: Sequence[Decision], actuator: str) -> list[Decision]:
    return [d for d in decisions if d.actuator == actuator]


def _above(
    samples: Sequence[Sample], value: Callable[[Sample], float], limit: float, what: str
) -> list[str]:
    """Ein Satz, wenn ``value`` in einer der ``samples`` über ``limit`` W liegt: die erste solche
    Sekunde, wie viele es sind und der höchste Wert, auf ganze W gerundet (``shown_number``)."""
    over = [(s.t, value(s)) for s in samples if value(s) > limit]
    if not over:
        return []
    (t, watts), (_, worst) = over[0], max(over, key=lambda found: found[1])

    def shown(found: float) -> str:
        return shown_number(found, 0, lambda number: number <= limit)

    return [
        f"{what} bei {t} s {shown(watts)} W, verlangt höchstens {de(limit)} W "
        f"({len(over)} s darüber, bis {shown(worst)} W)."
    ]


def _from_battery_at_most(key: str, limit_pct: float) -> Check:
    """Höchstens ``limit_pct`` der Energie des Autos ``key`` kommen aus dem Akku
    (``battery_into_car_pct``, ohne Übergänge). Lädt es nie, ist das ein Verstoß: Die Prüfung
    hätte nichts gemessen."""

    def find(result: SimResult) -> list[str]:
        pct = battery_into_car_pct(result.samples, key)
        if pct is None:
            return [f"„{key}“ lädt nie."]
        if pct <= limit_pct:
            return []
        shown = de(round(pct, 2))
        return [f"„{key}“ lädt zu {shown} % aus dem Akku, verlangt höchstens {de(limit_pct)} %."]

    text = (
        f"Höchstens {de(limit_pct)} % der Energie von „{key}“ kommen aus dem Akku (ohne Übergänge)."
    )
    return Check(text, find)


def _battery_into_cars_w(sample: Sample) -> float:
    """Was der Akku in die Autos liefert: ``max(0, Entladung − Haus)``, ohne PV."""
    return max(0.0, max(0.0, -sample.battery_w) - sample.house_w)


def _support_then_grid(result: SimResult) -> list[str]:
    """Der Akku liefert in die Autos ab ``SUPPORT_FROM_S`` nach dem ersten Laden bis
    ``SUPPORT_UNTIL_S`` danach höchstens ``SUPPORT_W``, ab ``GRID_ONLY_S`` danach höchstens
    ``GRID_ONLY_W`` (``_battery_into_cars_w``). Das erste Laden ist die erste Sekunde, in der ein
    Auto lädt."""
    samples = result.samples
    first = next((s.t for s in samples if _any_charging(s)), None)
    if first is None:
        return ["Kein Auto lädt."]
    begin, end = first + SUPPORT_FROM_S, first + SUPPORT_UNTIL_S
    support = [s for s in samples if begin <= s.t <= end]
    grid = [s for s in samples if s.t >= first + GRID_ONLY_S]
    if not grid:
        return [f"Der Lauf endet vor {first + GRID_ONLY_S} s."]
    what = "Akku in die Autos"
    return [
        *_above(support, _battery_into_cars_w, SUPPORT_W, what),
        *_above(grid, _battery_into_cars_w, GRID_ONLY_W, what),
    ]


def _grid_at_the_setpoint_cap(result: SimResult) -> list[str]:
    """Ab ``SETTLED_S`` liegt der Netzbezug in jeder Sekunde, in der Autos und Haus mehr als
    ``LOADS_ABOVE_W`` ziehen, in ``GRID_BETWEEN_W``; es muss solche Sekunden geben."""
    low, high = GRID_BETWEEN_W
    loaded = [
        s for s in result.samples if s.t >= SETTLED_S and cars_w(s) + s.house_w > LOADS_ABOVE_W
    ]
    if not loaded:
        return [f"Autos und Haus ziehen ab {SETTLED_S} s nie mehr als {de(LOADS_ABOVE_W)} W."]
    outside = [s for s in loaded if not low <= s.grid_w <= high]
    if not outside:
        return []
    first = outside[0]
    shown = shown_number(first.grid_w, 0, lambda number: low <= number <= high)
    return [
        f"Netzbezug bei {first.t} s {shown} W, verlangt {de(low)} bis {de(high)} W "
        f"({len(outside)} s außerhalb)."
    ]


def _no_victron_number(result: SimResult) -> list[str]:
    """Keine Entscheidung an Victron nennt eine Zahl, also einen Override."""
    for d in _at(result.decisions, VICTRON):
        watts = finite_number(d.requested)
        if watts is not None:
            return [f"Entscheidung an „{VICTRON}“ bei {_second(result, d.ts)} s: {de(watts)} W."]
    return []


def _battery_carries_the_house(result: SimResult) -> list[str]:
    """Ab ``SETTLED_S`` liegt der Netzbezug höchstens bei ``BOOST_GRID_W``, solange der SOC über
    ``BOOST_SOC_PCT`` liegt; es muss solche Sekunden geben."""
    above = [s for s in result.samples if s.t >= SETTLED_S and s.soc_pct > BOOST_SOC_PCT]
    if not above:
        return [f"Der SOC liegt ab {SETTLED_S} s nie über {de(BOOST_SOC_PCT)} %."]
    return _above(above, lambda s: s.grid_w, BOOST_GRID_W, "Netzbezug")


def _override_kept(result: SimResult) -> list[str]:
    """Ab ``SETTLED_S`` gilt in jeder Probe ein Override, zuletzt vor höchstens
    ``OVERRIDE_AGE_S`` geschrieben; gemeldet wird der erste Verstoß."""
    after = [s for s in result.samples if s.t >= SETTLED_S]
    if not after:
        return [f"Der Lauf endet vor {SETTLED_S} s."]
    for s in after:
        if s.override_w is None or s.override_age_s is None:
            return [f"Bei {s.t} s gilt kein Override."]
        if s.override_age_s > OVERRIDE_AGE_S:
            age = de(round(s.override_age_s))
            return [
                f"Bei {s.t} s ist der Override vor {age} s geschrieben, verlangt höchstens "
                f"{de(OVERRIDE_AGE_S)} s."
            ]
    return []


def _few_victron_decisions(result: SimResult) -> list[str]:
    """Höchstens ``VICTRON_DECISIONS`` Entscheidungen an Victron."""
    count = len(_at(result.decisions, VICTRON))
    if count <= VICTRON_DECISIONS:
        return []
    return [f"{count} Entscheidungen an „{VICTRON}“, verlangt höchstens {VICTRON_DECISIONS}."]


def _no_victron_decision(result: SimResult) -> list[str]:
    """Keine Entscheidung an Victron; gemeldet wird die erste."""
    found = _at(result.decisions, VICTRON)
    if not found:
        return []
    first = found[0]
    watts = finite_number(first.requested)
    shown = "kein Override" if watts is None else f"{de(watts)} W"
    return [
        f"Entscheidungen an „{VICTRON}“: {len(found)}, die erste bei "
        f"{_second(result, first.ts)} s ({shown})."
    ]


def _battery_locked(result: SimResult) -> list[str]:
    """Ab ``LOCKED_FROM_S`` zeigt jede Gesundheit den Akku-Sollwert gesperrt (``locked``)."""
    states = [(t, health.battery.get("state")) for t, health in result.health if t >= LOCKED_FROM_S]
    if not states:
        return [f"Der Lauf endet vor {LOCKED_FROM_S} s."]
    for t, state in states:
        if state != "locked":
            shown = "ohne Wert" if state is None else f"„{state}“"
            return [f"„battery.state“ bei {t} s {shown}, verlangt „locked“."]
    return []


def _quarters_within(start: datetime) -> Check:
    """Der Netzbezug der ``PEAK_QUARTERS`` Viertelstunden ab ``PEAK_LEAD_S`` liegt je höchstens
    bei ``QUARTER_KWH``: ``Σ max(0, Netzbezug) · 1 s`` über die Proben der Viertelstunde.
    ``start`` ist der Beginn des Szenarios, für die Uhrzeiten im Text."""
    begins = [PEAK_LEAD_S + k * QUARTER_S for k in range(PEAK_QUARTERS)]  # Sekunden des Laufs

    def find(result: SimResult) -> list[str]:
        found: list[str] = []
        for begin in begins:
            shown = _hhmm(result.scenario.start, begin)
            inside = [s for s in result.samples if begin <= s.t < begin + QUARTER_S]
            if len(inside) < QUARTER_S:
                found.append(f"Die Viertelstunde ab {shown} liegt nicht ganz im Lauf.")
                continue
            kwh = sum(max(0.0, s.grid_w) for s in inside) / 3_600_000
            if kwh > QUARTER_KWH:
                found.append(
                    f"Netzbezug der Viertelstunde ab {shown}: {de(round(kwh, 3))} kWh, verlangt "
                    f"höchstens {de(QUARTER_KWH)} kWh."
                )
        return found

    *first, last = (_hhmm(start, begin) for begin in begins)
    text = (
        f"Netzbezug der Viertelstunden ab {', '.join(first)} und {last} je höchstens "
        f"{de(QUARTER_KWH)} kWh."
    )
    return Check(text, find)


def _named_before(code: str, until_s: int, start: datetime) -> Check:
    """Vor ``until_s`` nennt mindestens eine Entscheidung den Grund ``code``. ``start`` ist der
    Beginn des Szenarios, für die Uhrzeit im Text."""

    def find(result: SimResult) -> list[str]:
        if any(code in d.codes and _second(result, d.ts) < until_s for d in result.decisions):
            return []
        return [f"Keine Entscheidung mit „{code}“ vor {until_s} s."]

    return Check(f"Eine Entscheidung vor {_hhmm(start, until_s)} nennt „{code}“.", find)


def _not_named_from(code: str, actuator: str, from_s: int, start: datetime) -> Check:
    """Ab ``from_s`` nennt keine Entscheidung an ``actuator`` den Grund ``code``. ``start`` ist der
    Beginn des Szenarios, für die Uhrzeit im Text."""

    def find(result: SimResult) -> list[str]:
        found = [
            d
            for d in _at(result.decisions, actuator)
            if code in d.codes and _second(result, d.ts) >= from_s
        ]
        if not found:
            return []
        return [
            f"Entscheidungen an „{actuator}“ mit „{code}“ ab {from_s} s: {len(found)}, die erste "
            f"bei {_second(result, found[0].ts)} s."
        ]

    shown = _hhmm(start, from_s)
    return Check(f"Ab {shown} nennt keine Entscheidung an „{actuator}“ „{code}“.", find)


def _now_throttled_last(key: str, actuator: str) -> Check:
    """Bei der ersten Drosselung des Autos ``key`` in Sofort (der ersten Entscheidung an seinen
    Aktor ``actuator`` mit ``peak_guard``, während die Probe es in Sofort zeigt) lädt kein Auto in
    PV-Überschuss oder Min+PV über seinem Mindeststrom + ``MINIMUM_MARGIN_A`` (Design 6.3). Ohne
    eine solche Drosselung ist das ein Verstoß: Die Prüfung hätte nichts gemessen."""

    def find(result: SimResult) -> list[str]:
        by_second = {s.t: s for s in result.samples}
        for d in _at(result.decisions, actuator):
            sample = by_second.get(_second(result, d.ts))
            if PEAK_GUARD in d.codes and sample is not None and sample.cars[key].mode == "now":
                return [
                    f"Bei der ersten Drosselung von „{key}“ ({sample.t} s) lädt „{other}“ "
                    f"({car.mode}) mit {de(car.current_a)} A, Mindeststrom {car.min_a} A."
                    for other, car in sample.cars.items()
                    if car.mode in PV_MODES
                    and car.charging
                    and car.current_a > car.min_a + MINIMUM_MARGIN_A
                ]
        return [f"Keine Drosselung von „{key}“ in Sofort."]

    text = (
        f"Bei der ersten Drosselung von „{key}“ in Sofort lädt kein Auto in PV-Überschuss oder "
        f"Min+PV über Mindeststrom + {de(MINIMUM_MARGIN_A)} A."
    )
    return Check(text, find)


# --- Prüfungen für die Zuordnung -----------------------------------------------------------------


def _clear_from(from_s: int) -> Check:
    """Ab ``from_s`` zeigt keine Gesundheit ein Fahrzeug mit der Zuordnung ``UNCLEAR``; der Befund
    nennt die erste, die eins zeigt. Gibt es ab ``from_s`` keine Gesundheit, ist das ein Verstoß:
    Die Prüfung hätte nichts gesehen."""
    word = _SHOWN[UNCLEAR]

    def find(result: SimResult) -> list[str]:
        after = [(t, health) for t, health in result.health if t >= from_s]
        if not after:
            return [f"Der Lauf endet vor {from_s} s."]
        for t, health in after:
            for vehicle in health.vehicles:
                if vehicle["assignment"] == UNCLEAR:
                    return [f"Bei {t} s zeigt die Gesundheit „{vehicle['id']}“ „{word}“."]
        return []

    text = f"Ab {de(from_s / MINUTE_S)} min zeigt die Gesundheit kein Fahrzeug „{word}“."
    return Check(text, find)


def _occupied_from(wallbox: str, occupant: str, from_s: int) -> Check:
    """Ab ``from_s`` zeigt jede Gesundheit an der Wallbox ``wallbox`` (``health.wallboxes``) das
    Auto ``occupant``, den Schlüssel eines Fahrzeugs oder ``GUEST``, erkannt (``DETECTED``);
    gemeldet wird die erste, die es nicht tut. Gibt es ab ``from_s`` keine Gesundheit, ist das ein
    Verstoß: Die Prüfung hätte nichts gesehen."""

    def find(result: SimResult) -> list[str]:
        after = [(t, health) for t, health in result.health if t >= from_s]
        if not after:
            return [f"Der Lauf endet vor {from_s} s."]
        for t, health in after:
            if not any(
                entry["id"] == wallbox
                and entry["occupant"] == occupant
                and entry["assignment"] == DETECTED
                for entry in health.wallboxes
            ):
                return [f"Bei {t} s steht an „{wallbox}“ nicht „{occupant}“, erkannt."]
        return []

    minutes = de(from_s / MINUTE_S)
    text = f"Ab {minutes} min steht an „{wallbox}“ laut Gesundheit „{occupant}“, erkannt."
    return Check(text, find)


def _no_decision_at(actuator: str) -> Check:
    """Keine Entscheidung an den Aktor ``actuator``, mit welchem Ergebnis auch immer; der Befund
    nennt ihre Zahl und die Sekunde der ersten."""

    def find(result: SimResult) -> list[str]:
        found = _at(result.decisions, actuator)
        if not found:
            return []
        first = _second(result, found[0].ts)
        return [f"{len(found)} Entscheidungen an „{actuator}“, die erste bei {first} s."]

    return Check(f"Keine Entscheidung an „{actuator}“.", find)


# --- Szenarien 1 bis 5 ----------------------------------------------------------------------------


def _sonnig() -> Scenario:
    """Die Verteilung ist gierig nach Priorität (Design 6.2): Mit höchstens 10 kW PV laden die
    Autos nacheinander, erst der Tesla bis „Complete“, dann der e-Golf. Dem Rauschen der Hauslast
    folgt die EVCS dank der Hysterese nicht (Design 6.5)."""
    start, duration_s, seed = SUMMER, 2 * HOUR_S, 1
    few = Check(
        f"Höchstens {EVCS_CURRENTS_PER_HOUR} Strombefehle an die EVCS je Stunde, in der „egolf“ "
        "lädt (Hysterese).",
        _few_evcs_currents,
    )
    return Scenario(
        name="sonnig",
        title="Sonniger Tag, beide Autos im PV-Modus",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla_soc_pct=SUNNY_TESLA_PCT),
        intent=_intent(tesla=ChargeMode.PV, egolf=ChargeMode.PV),
        live=True,
        events=_weather(start, duration_s, seed, pv=_sun(start)),
        checks=Checks((_one_after_the_other_check(tesla_min=45.0, egolf_min=45.0), few)),
    )


def _wolkig() -> Scenario:
    """Die Verteilung ist gierig nach Priorität (Design 6.2): Mit höchstens 10 kW PV laden die
    Autos nacheinander, erst der Tesla bis „Complete“, dann der e-Golf."""
    start, duration_s, seed = SUMMER, 2 * HOUR_S, 2
    return Scenario(
        name="wolkig",
        title="Wolkiger Tag, beide Autos im PV-Modus",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla_soc_pct=CLOUDY_TESLA_PCT),
        intent=_intent(tesla=ChargeMode.PV, egolf=ChargeMode.PV),
        live=True,
        events=_weather(start, duration_s, seed, pv=_cloudy(start, duration_s, seed)),
        checks=Checks((_one_after_the_other_check(tesla_min=20.0, egolf_min=20.0),)),
    )


def _abend_sofort() -> Scenario:
    start, duration_s, seed = EVENING, HOUR_S, 3

    def peak(t: int) -> float:
        return PEAK_W if PEAK_S[0] <= t < PEAK_S[1] else 0.0

    lower = Check(
        "Während der Lastspitze liegt der Strom der EVCS unter dem davor.",
        _evcs_lower_during_the_peak,
    )
    return Scenario(
        name="abend-sofort",
        title="Abends beide Autos auf Sofort, mit Lastspitze",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0),
        intent=_intent(tesla=ChargeMode.NOW, egolf=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed, l1=peak),
        checks=Checks((lower,)),
    )


def _reserve() -> Scenario:
    start, duration_s, seed = EVENING, HOUR_S, 4

    def more_on_l1(t: int) -> float:
        return RESERVE_L1_W

    target = Check(
        "Unter der Reserve-Absicherung liegt keine Phase länger als "
        f"{TRANSITION_S} s über dem Importziel (Last ohne Akku-Hilfe).",
        _import_target_kept,
    )
    kept = Check(
        f"Der SOC sinkt nie unter {de(RESERVE_PCT)} % (Invariante des Modells).", _reserve_kept
    )
    return Scenario(
        name="reserve",
        title="Sofort knapp über der USV-Reserve",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=23.0, tesla=None),
        intent=_intent(egolf=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed, l1=more_on_l1),
        checks=Checks((_named("reserve_guard"), target, kept)),
    )


def _nur_laden() -> Scenario:
    start, duration_s, seed = EVENING, HOUR_S, 5

    def ramp(t: int) -> float:
        begin, end = RAMP_S
        return RAMP_W * min(max(t - begin, 0) / (end - begin), 1.0)

    warns = Check("Die Selbstprüfung „multi_mode“ warnt.", _multi_mode_warns)
    return Scenario(
        name="nur-laden",
        title="Multi auf „Nur Laden“ mit Sofort",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, charger_only=True),
        intent=_intent(tesla=ChargeMode.NOW, egolf=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed, l1=ramp),
        checks=Checks((warns,)),
    )


# --- Szenarien 6 bis 10 ---------------------------------------------------------------------------


def _tesla_5a() -> Scenario:
    """Die PV trägt den Tesla mit 5 A (Design 6.2); sein erster Befehl von 10 auf 5 A geht verloren,
    und die Regelung fordert ihn nach „nicht übernommen“ erneut an (Design 7.2)."""
    start, duration_s, seed = SUMMER, HOUR_S, 6

    def pv(t: int) -> float:
        return FIVE_A_PV_W

    return Scenario(
        name="tesla-5a",
        title="Tesla auf 5 A mit verlorenem erstem Befehl",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=90.0, egolf=None, tesla_amps=TESLA_START_A),
        intent=_intent(tesla=ChargeMode.PV),
        live=True,
        events=_weather(start, duration_s, seed, pv=pv),
        checks=Checks((_named("not_taken_retry"), _charges_with(TESLA, FIVE_A))),
    )


def _tesla_schlaeft() -> Scenario:
    """Der Tesla schläft, bis der Überschuss seinen Mindeststrom trägt; dann weckt ihn der Start
    mit Weckerlaubnis (Design 7.1)."""
    start, duration_s, seed = MORNING, HOUR_S, 7

    def pv(t: int) -> float:
        return SLEEPY_PV_W * min(t / SLEEPY_RAMP_S, 1.0)

    return Scenario(
        name="tesla-schlaeft",
        title="Schlafender Tesla im PV-Modus",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=90.0, egolf=None, asleep=True),
        intent=_intent(tesla=ChargeMode.PV),
        live=True,
        events=_weather(start, duration_s, seed, pv=pv),
        checks=Checks((_one_wake(TESLA), _charges_at_the_end(TESLA))),
    )


def _ausfaelle() -> Scenario:
    """Jeder Ausfall einzeln (Design 9); nach dem Netzausfall nimmt die Regelung das Laden nach
    dem Modus wieder auf."""
    start, duration_s, seed = EVENING, HOUR_S + 30 * MINUTE_S, 8
    outages = tuple(
        event
        for name, begin, end in OUTAGE_MIN
        for event in (
            (begin * MINUTE_S, _Outage(name, begins=True)),
            (end * MINUTE_S, _Outage(name, begins=False)),
        )
    )
    safe = tuple(_safe_during(n, b, e) for n, b, e in OUTAGE_MIN if n in SAFE_OUTAGES)
    return Scenario(
        name="ausfaelle",
        title="Ausfälle einzeln, Netzausfall mit Wiederaufnahme",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0),
        intent=_intent(tesla=ChargeMode.NOW, egolf=ChargeMode.NOW),
        live=True,
        events=(*_weather(start, duration_s, seed), *outages),
        checks=Checks((*safe, _all_charge_from(RESUMED_S), _named("resume_after_outage"))),
    )


def _zuordnung() -> Scenario:
    """Ein echter Widerspruch macht die Zuordnung unklar (Plan 2c-2b, Präzisierung 4): Der Tesla
    steckt woanders, Tessie meldet ihn aber zu Hause und angesteckt; Tessie und der Ausschluss
    stützen ihn so an der EVCS, wo die Phasen-Signatur des e-Golf ihn ausschließt. Die
    Übersteuerung des e-Golf an die EVCS hebt „unklar“ sofort auf (Plan 2c-1, Präzisierung 5), das
    Abstecken beendet sie (Plan 2c-1, Präzisierung 3), und danach lädt die EVCS nicht mehr. Warum
    die EVCS schon vor dem Start des core lädt, steht im Docstring des Moduls."""
    start, duration_s, seed = EVENING, HOUR_S, 9
    events = (
        (OVERRIDE_S, _Override(2, EGOLF, EVCS_BOX)),
        (UNPLUG_S, _Unplug(EGOLF)),
    )
    checks = (
        _shown_before(EGOLF, UNCLEAR, OVERRIDE_S),
        _clear_from(OVERRIDE_S + MINUTE_S),
        _evcs_off_from(EVCS_BOX, EVCS_OFF_S),
    )
    return Scenario(
        name="zuordnung",
        title="Zuordnung unklar und Übersteuerung",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla=None, tesla_elsewhere=True, evcs_on=True),
        intent=_intent(egolf=ChargeMode.NOW),
        live=True,
        events=(*_weather(start, duration_s, seed), *events),
        checks=Checks(checks),
    )


def _dry_run() -> Scenario:
    """Ein fremder Regler wie evcc stellt die Ladepunkte (Modell: ``foreign``), die Regelung
    entscheidet daneben im Dry-Run (Design 12). Die PV-Kennzahlen zählen seine Autos nicht, denn
    ihre Ladepunkte steuert die Regelung nicht live (``CarSample.steered``)."""
    start, duration_s, seed = SUMMER, HOUR_S, 10
    checks = (
        Check("Keine wiederholte Dry-Run-Entscheidung.", _no_repeated_dry_runs),
        _dry_runs_at(wallbox_actuator(EVCS_BOX), vehicle_actuator(TESLA)),
        Check("Kein Alarm „competitor.foreign.*“.", _no_foreign_alarm),
    )
    return Scenario(
        name="dry-run",
        title="Dry-Run neben einem fremden Regler",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, foreign=True, tesla_starts=True),
        intent=_intent(tesla=ChargeMode.PV, egolf=ChargeMode.PV),
        live=False,
        events=_weather(start, duration_s, seed, pv=_cloudy(start, duration_s, seed)),
        checks=Checks(checks),
    )


# --- Szenarien 11 bis 18: Akku und Spitzen, Victron live ------------------------------------------


def _akku_wolkig() -> Scenario:
    """Ohne Unterstützung (SOC unter ``support_soc_pct``) liefert der Akku dem e-Golf nichts
    (Design 5.1, 5.2): Was ihm unter Wolken fehlt, holt der Override aus dem Netz."""
    start, duration_s, seed = SUMMER, HOUR_S, 11
    return Scenario(
        name="akku-wolkig",
        title="PV unter Wolken, SOC 60 %",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla=None),
        intent=_intent(egolf=ChargeMode.PV),
        live=True,
        events=_weather(start, duration_s, seed, pv=_cloudy(start, duration_s, seed)),
        checks=Checks(
            (
                _from_battery_at_most(EGOLF, CAR_FROM_BATTERY_PCT),
                _named("battery_comp", at=VICTRON),
            )
        ),
        victron_live=True,
    )


def _akku_minpv() -> Scenario:
    """Mit Unterstützung liefert der Akku den Autos in Min+PV zusammen höchstens
    ``support_max_w``, nach ``support_max_s`` nichts mehr (Design 5.2). Der Tesla steht zu Beginn
    auf 5 A (Abweichung, Docstring des Moduls)."""
    start, duration_s, seed = EVENING, 30 * MINUTE_S, 12
    supported = Check(
        f"Akku in die Autos (Entladung − Haus): ab {SUPPORT_FROM_S} s nach dem ersten Laden bis "
        f"{SUPPORT_UNTIL_S} s danach höchstens {de(SUPPORT_W)} W, ab {GRID_ONLY_S} s danach "
        f"höchstens {de(GRID_ONLY_W)} W.",
        _support_then_grid,
    )
    return Scenario(
        name="akku-minpv",
        title="Min+PV ohne Sonne, SOC 80 %",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=80.0, tesla_amps=TESLA_MIN_A),
        intent=_intent(tesla=ChargeMode.MIN_PV, egolf=ChargeMode.MIN_PV),
        live=True,
        events=_weather(start, duration_s, seed),
        checks=Checks((supported,)),
        victron_live=True,
    )


def _akku_sofort() -> Scenario:
    """In Sofort liefert der Akku nur, was über ``max_grid_charge_setpoint_w`` hinausgeht
    (Design 5.4, Regel 2)."""
    start, duration_s, seed = EVENING, 30 * MINUTE_S, 13
    low, high = GRID_BETWEEN_W
    capped = Check(
        f"Ab {de(SETTLED_S / MINUTE_S)} min liegt der Netzbezug in jeder Sekunde, in der Autos und "
        f"Haus mehr als {de(LOADS_ABOVE_W)} W ziehen, zwischen {de(low)} und {de(high)} W.",
        _grid_at_the_setpoint_cap,
    )
    return Scenario(
        name="akku-sofort",
        title="Sofort am Abend, beide Autos",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0),
        intent=_intent(tesla=ChargeMode.NOW, egolf=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed),
        checks=Checks((capped,)),
        victron_live=True,
    )


def _boost_abend() -> Scenario:
    """In Boost darf der Akku alles liefern (Design 5.2, Entscheidung 2): Es gibt keinen Override.
    Über der Reserve-Absicherung trägt der Akku Haus und Auto; unter die USV-Reserve entlädt das
    Modell nie."""
    start, duration_s, seed = EVENING, 45 * MINUTE_S, 14
    boost = VehicleIntent(boost_until=start + timedelta(seconds=duration_s))
    house = Check(
        f"Solange der SOC über {de(BOOST_SOC_PCT)} % liegt, ab {de(SETTLED_S / MINUTE_S)} min "
        f"Netzbezug höchstens {de(BOOST_GRID_W)} W.",
        _battery_carries_the_house,
    )
    kept = Check(
        f"Der SOC sinkt nie unter {de(RESERVE_PCT)} % (Invariante des Modells).", _reserve_kept
    )
    return Scenario(
        name="boost-abend",
        title="Boost am Abend",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=35.0, tesla=None),
        intent=Intent(version=1, control=True, vehicles={EGOLF: boost}),
        live=True,
        events=_weather(start, duration_s, seed),
        checks=Checks(
            (
                Check(f"Keine Entscheidung an „{VICTRON}“ mit einer Zahl.", _no_victron_number),
                house,
                kept,
            )
        ),
        victron_live=True,
    )


def _override_stunde() -> Scenario:
    """Ein Override bleibt eine Stunde lang: Die Regelung frischt ihn alle ``override_refresh_s``
    auf, ohne Eintrag im Log und ohne Alarm einer fremden Änderung (Design 5.5)."""
    start, duration_s, seed = EVENING, HOUR_S, 15
    kept = Check(
        f"Ab {de(SETTLED_S / MINUTE_S)} min gilt in jeder Sekunde ein Override, zuletzt vor "
        f"höchstens {de(OVERRIDE_AGE_S)} s geschrieben.",
        _override_kept,
    )
    few = Check(
        f"Höchstens {VICTRON_DECISIONS} Entscheidungen an „{VICTRON}“.", _few_victron_decisions
    )
    return Scenario(
        name="override-stunde",
        title="Eine Stunde mit aktivem Override",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=50.0, tesla=None),
        intent=_intent(egolf=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed),
        checks=Checks((kept, Check("Kein Alarm „competitor.foreign.*“.", _no_foreign_alarm), few)),
        victron_live=True,
    )


def _dess_an() -> Scenario:
    """Mit Dynamic ESS ist Victron gesperrt: Der Akku-Sollwert sendet nichts, auch keine
    Ablehnung, und die Wallbox lädt weiter (Design 5.6)."""
    start, duration_s, seed = EVENING, 30 * MINUTE_S, 16
    locked = Check(
        f"Ab {de(LOCKED_FROM_S / MINUTE_S)} min zeigt jede Gesundheit „battery.state“ „locked“.",
        _battery_locked,
    )
    return Scenario(
        name="dess-an",
        title="Dynamic ESS an",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla=None, dess=True),
        intent=_intent(egolf=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed),
        checks=Checks(
            (
                Check(f"Keine Entscheidung an „{VICTRON}“.", _no_victron_decision),
                locked,
                _charges_at_the_end(EGOLF),
            )
        ),
        victron_live=True,
    )


def _spitze_15min() -> Scenario:
    """Der 15-min-Wächter hält das Ziel von 8 kW (Design 6): Er drosselt in der Reihenfolge der
    Modi, der Akku überbrückt den Rest; Boost drosselt er nie. Der Lauf beginnt ``PEAK_LEAD_S``
    vor 18:00: Nach dem Start beobachtet der core rund 60 s und regelt so ab der Viertelstunde um
    18:00; die angebrochene davor ist lückenhaft. Von 18:45 an lädt der e-Golf in Boost, darum
    prüft der Lauf die Viertelstunde ab 18:45 nicht. Der Tesla startet mit 16 A; was die Prüfung
    der Reihenfolge damit belegt, steht im Docstring des Moduls."""
    start, duration_s, seed = EVENING - timedelta(seconds=PEAK_LEAD_S), 75 * MINUTE_S, 17
    boost = _Boost(2, EGOLF, start + timedelta(seconds=duration_s))
    return Scenario(
        name="spitze-15min",
        title="15-min-Ziel 8 kW",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0),
        intent=_intent(tesla=ChargeMode.MIN_PV, egolf=ChargeMode.NOW),
        live=True,
        events=(*_weather(start, duration_s, seed), (PEAK_BOOST_S, boost)),
        checks=Checks(
            (
                _quarters_within(start),
                _named_before(PEAK_GUARD, PEAK_BOOST_S, start),
                _not_named_from(PEAK_GUARD, wallbox_actuator(EVCS_BOX), PEAK_BOOST_S, start),
                _now_throttled_last(EGOLF, wallbox_actuator(EVCS_BOX)),
            )
        ),
        victron_live=True,
        control=MappingProxyType({"peak_target_kw": PEAK_TARGET_KW}),
    )


def _schieflast_pv() -> Scenario:
    """Mit schwerer Last auf L1 bindet das Importziel den e-Golf in PV zwischen Mindest- und
    Höchststrom (Punkt 6 der Liste „Vor der Live-Schaltung“). Die Last kommt erst nach 10 min dazu,
    wenn der e-Golf lädt (Abweichung, Docstring des Moduls)."""
    start, duration_s, seed = SUMMER, HOUR_S, 18

    def pv(t: int) -> float:
        return UNBALANCED_PV_W

    def heavy(t: int) -> float:
        return UNBALANCED_L1_W if t >= UNBALANCED_FROM_S else 0.0

    return Scenario(
        name="schieflast-pv",
        title="PV mit schwerer Last auf L1",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla=None),
        intent=_intent(egolf=ChargeMode.PV),
        live=True,
        events=_weather(start, duration_s, seed, pv=pv, l1=heavy),
        checks=Checks((_named("import_limit", at=wallbox_actuator(EVCS_BOX)),)),
        victron_live=True,
    )


# --- Szenarien 19 bis 21: Zuordnung, Victron im Dry-Run -------------------------------------------


def _tesla_evcs() -> Scenario:
    """Der Tesla steht ohne Übersteuerung an der EVCS: Die Erkennung findet ihn dort (Plan 2c-2b,
    Präzisierung 4), er bekommt einmal seinen Höchststrom, und die EVCS regelt in seinem Modus
    (Plan 2c-2b, Präzisierung 7). Abends ohne PV lüde er in PV-Überschuss nicht; mit 16 A am Ende
    lädt er in Sofort. „Erkannt“ verlangt die Prüfung vor 3 min statt binnen 2 min (Abweichung,
    Docstring des Moduls)."""
    start, duration_s, seed = EVENING, 30 * MINUTE_S, 19
    checks = (
        _shown_before(TESLA, DETECTED, 3 * MINUTE_S),
        _one_current(TESLA, TESLA_MAX_A),
        _charges_with(TESLA, TESLA_MAX_A),
    )
    return Scenario(
        name="tesla-evcs",
        title="Tesla an der EVCS ohne Übersteuerung",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(
            _setup,
            soc_pct=60.0,
            tesla=EVCS_BOX,
            egolf=None,
            tesla_amps=TESLA_START_A,
            tesla_starts=True,
        ),
        intent=_intent(tesla=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed),
        checks=Checks(checks),
    )


def _egolf_twc() -> Scenario:
    """Der e-Golf steht am Wall Connector: Home Assistant und die Phasen-Signatur stützen ihn dort,
    Tessie schließt den Tesla aus, und die Erkennung findet den e-Golf (Plan 2c-2b, Präzisierung 4).
    Der Tesla bekommt keinen Befehl. Der Wall Connector hat für den e-Golf keinen Aktor: Der e-Golf
    lädt als Last mit 16 A, obwohl er in PV-Überschuss steht und abends keine PV scheint. Bis zur
    Erkennung steht dort ein angenommener Gast."""
    start, duration_s, seed = EVENING, 30 * MINUTE_S, 20
    checks = (
        _occupied_from(WALL_CONNECTOR, EGOLF, 4 * MINUTE_S),
        _no_decision_at(vehicle_actuator(TESLA)),
        _charges_with(EGOLF, 16.0),
    )
    return Scenario(
        name="egolf-twc",
        title="e-Golf am Wall Connector",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla=None, egolf=WALL_CONNECTOR),
        intent=_intent(egolf=ChargeMode.PV),
        live=True,
        events=_weather(start, duration_s, seed),
        checks=Checks(checks),
    )


def _gast_evcs() -> Scenario:
    """Ein Gast mit 3 Phasen steht an der EVCS, der e-Golf ist weg: Tessie schließt den Tesla aus,
    Home Assistant den e-Golf, und die Erkennung findet den Gast (Plan 2c-2b, Präzisierung 4). Die
    EVCS lädt ihn im Modus des Gastes (Plan 2c-2b, Präzisierung 6): Abends ohne PV lüde er in
    PV-Überschuss nicht; mit 16 A am Ende lädt er in Sofort."""
    start, duration_s, seed = EVENING, 30 * MINUTE_S, 21
    return Scenario(
        name="gast-evcs",
        title="Gast an der EVCS, e-Golf weg",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(
            _setup,
            soc_pct=60.0,
            tesla=None,
            egolf=None,
            guest=EVCS_BOX,
            guest_phases=3,
            away=frozenset({EGOLF}),
        ),
        intent=_intent(guest_mode=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed),
        checks=Checks(
            (_occupied_from(EVCS_BOX, GUEST, 3 * MINUTE_S), _charges_with(GUEST_CAR, 16.0))
        ),
    )


# --- Szenarien 22 und 23: „Aus“ und Selbststart des Tesla -----------------------------------------


def _tesla_aus() -> Scenario:
    """Wie ``wolkig``, doch der Tesla wechselt bei 45 min auf „Aus“ (Plan 2d, Präzisierung 9): Er
    bekommt binnen 10 s „Laden aus“, und kein Befehl „charging“ an ihn wird zurückgestellt.

    Dass der dringende Stopp das Stundenlimit umgeht, belegt das Szenario nicht: Die Reserve
    (``control.tesla_reserve_commands``) hält das Stundenbudget unter den Wolken offen, es ist nie
    ausgeschöpft. Das Umgehen prüft der Test ``test_off_stops_urgently`` der Regelung."""
    start, duration_s, seed = SUMMER, HOUR_S, 22
    stop = OFF_AT_S, _Mode(2, TESLA, ChargeMode.OFF)
    checks = (
        _stops_within(vehicle_actuator(TESLA), OFF_AT_S, OFF_WITHIN_S),
        _no_deferred(vehicle_actuator(TESLA), CommandKind.CHARGING),
    )
    return Scenario(
        name="tesla-aus",
        title="Tesla wird unter Wolken auf Aus gestellt",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla_soc_pct=OFF_TESLA_PCT),
        intent=_intent(tesla=ChargeMode.PV, egolf=ChargeMode.PV),
        live=True,
        events=(*_weather(start, duration_s, seed, pv=_cloudy(start, duration_s, seed)), stop),
        checks=Checks(checks),
    )


def _tesla_selbststart() -> Scenario:
    """Der Tesla steht am Wall Connector im Modus „Aus“ und lädt nicht; bei 5 min startet er selbst
    (Plan 2d, Präzisierung 10). Die Regelung sieht es mit der nächsten Abfrage von Tessie (60 s,
    angesteckt), wartet 120 s und stoppt ihn dann; mit dem Verzug des Befehls bleiben 210 s."""
    start, duration_s, seed = EVENING, 30 * MINUTE_S, 23
    checks = (
        _stops_within(vehicle_actuator(TESLA), SELF_START_S, SELF_START_WITHIN_S),
        _idle_from(TESLA, IDLE_FROM_S),
    )
    return Scenario(
        name="tesla-selbststart",
        title="Tesla startet selbst im Modus Aus",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, egolf=None, tesla_soc_pct=SELF_START_TESLA_PCT),
        intent=_intent(tesla=ChargeMode.OFF),
        live=True,
        events=(
            *_weather(start, duration_s, seed),
            (SELF_START_S, _SelfStart(TESLA)),
        ),
        checks=Checks(checks),
    )


SCENARIOS: Final[dict[str, Scenario]] = {
    scenario.name: scenario
    for scenario in (
        _sonnig(),
        _wolkig(),
        _abend_sofort(),
        _reserve(),
        _nur_laden(),
        _tesla_5a(),
        _tesla_schlaeft(),
        _ausfaelle(),
        _zuordnung(),
        _dry_run(),
        _akku_wolkig(),
        _akku_minpv(),
        _akku_sofort(),
        _boost_abend(),
        _override_stunde(),
        _dess_an(),
        _spitze_15min(),
        _schieflast_pv(),
        _tesla_evcs(),
        _egolf_twc(),
        _gast_evcs(),
        _tesla_aus(),
        _tesla_selbststart(),
    )
}
