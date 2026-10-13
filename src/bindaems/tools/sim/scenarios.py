"""Szenarien des Simulators (Design 13.2): Anlage, Absicht, Wetter, Ereignisse und Prüfungen.

Ein ``Scenario`` beschreibt einen Lauf (``runner.run_scenario``): Start in UTC, Dauer, Saat, den
Aufbau des Modells (``setup``), die Absicht (``intent``; ``live`` schaltet die Aktoren der Regelung
live), die Ereignisse je Sekunde und die eigenen Prüfungen am Ergebnis (``checks``). Ort,
PV-Wechselrichter und alle Grenzen kommen aus der Konfiguration des Laufs, in der CI aus
``deploy/config.example.yaml``; die Szenarien nennen deren Fahrzeuge ``tesla`` und ``egolf`` und
Wallboxen ``twc`` und ``evcs``.

**Prüfungen** sind ``Checks``: je ``Check`` ein Satz, was er verlangt, und eine Funktion, die je
Verstoß einen Satz liefert. So zeigt der Bericht jede Prüfung mit OK oder FEHLER.

**Wetter und Hauslast** sind in jeder Sekunde ein Ereignis (``_Weather``): die PV und die Hauslast
je Phase, ``HOUSE_W`` mit einem gleichverteilten Rauschen von ± ``HOUSE_NOISE_W`` und den Zuschlägen
des Szenarios. Die Sonne ist ein halber Sinus über ``DAYLIGHT_H`` Stunden mit ``SUN_PEAK_W`` um
``SOLAR_NOON_H`` Uhr Wiener Zeit (``sun_w``). Rauschen und Wolken zieht je ein eigener Generator aus
der Saat des Szenarios; dieselbe Saat ergibt dieselben Werte, auch in einem zweiten Lauf.

Zu Beginn stecken die Autos an ihrer Standard-Wallbox, laden aber nicht, und der Tesla ist wach,
soweit ein Szenario nichts anderes sagt. Die Absicht verlangt den Regelbetrieb. Andere Ereignisse
sind Ausfälle (``_Outage``), eine neue Version der Absicht mit Übersteuerung (``_Override``) und
das Abstecken (``_Unplug``).

Abweichungen von der Tabelle des Plans (Entscheidung des Koordinators zu Task 14): In ``sonnig``
und ``wolkig`` steht der Tesla knapp unter seinem Ladelimit, und die Prüfungen verlangen, dass die
Autos nacheinander laden; ``reserve`` hat 1 kW mehr Hauslast auf L1, damit das Importziel greift.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Final

from bindaems.shared.control import (
    ChargeMode,
    CommandKind,
    Intent,
    VehicleIntent,
    vehicle_actuator,
    wallbox_actuator,
)
from bindaems.shared.decisions import DecisionResult
from bindaems.shared.numbers import de
from bindaems.shared.timeutil import LOCAL_TZ
from bindaems.tools.sim.model import CERBO, EVCS, GRID, TESSIE, TWC, PlantModel

if TYPE_CHECKING:
    from bindaems.core.runtime import CoreRuntime
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
RESERVE_PCT: Final = 20.0  # unter diese USV-Reserve sinkt der SOC nie (Szenario „reserve“)
RESERVE_L1_W: Final = 1000.0  # dort mehr Hauslast auf L1: frei ≈ 13 A unter dem Importziel
SUNNY_TESLA_PCT: Final = 72.0  # SOC des Tesla in „sonnig“: „Complete“ nach etwa einer Stunde
CLOUDY_TESLA_PCT: Final = 75.0  # dasselbe in „wolkig“, wo er im Mittel weniger Strom bekommt

FIVE_A_PV_W: Final = 4600.0  # konstante PV in „tesla-5a“: Der Anteil des Tesla ist 5 A …
FIVE_A: Final = 5.0  # … und mit diesem Strom lädt er am Ende
TESLA_START_A: Final = 10  # Strom im Tesla zu Beginn von „tesla-5a“ und „zuordnung“
TESLA_MAX_A: Final = 16.0  # Höchststrom des Tesla in der Beispielkonfiguration („zuordnung“)
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
OVERRIDE_S: Final = 10 * MINUTE_S  # „zuordnung“: Übersteuerung „tesla“ an „evcs“ …
UNPLUG_S: Final = 40 * MINUTE_S  # … Abstecken …
EVCS_OFF_S: Final = 41 * MINUTE_S  # … und ab hier lädt die EVCS nicht mehr

TESLA: Final = "tesla"  # Schlüssel der Fahrzeuge und Wallboxen in der Konfiguration
EGOLF: Final = "egolf"
WALL_CONNECTOR: Final = "twc"
EVCS_BOX: Final = "evcs"
WAKE: Final = "wake"  # Grund eines Weckstarts
UNCLEAR: Final = "unclear"  # Zuordnung „unklar“ in der Gesundheit
SAFE: Final = "SAFE"
FOREIGN_ALARM: Final = "competitor.foreign."  # Anfang der Alarme fremder Änderungen


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
    - ``intent``: die Absicht; mit ``live`` sind die Aktoren der Regelung (EVCS und Fahrzeuge mit
      Tessie) live, sonst im Dry-Run
    - ``events``: Sekunde und Aktion, vor der Physik dieser Sekunde, in ihrer Reihenfolge
    - ``checks``: die eigenen Prüfungen, meist ``Checks``; je Verstoß ein Satz
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
        current = rt.intent()
        if current is None:
            raise ValueError("Übersteuerung ohne gültige Absicht.")
        entry = current.vehicles.get(self.vehicle, VehicleIntent())
        vehicles = current.vehicles | {
            self.vehicle: entry.model_copy(update={"wallbox": self.wallbox})
        }
        reply = rt.submit_intent(
            current.model_copy(update={"version": self.version, "vehicles": vehicles})
        )
        if reply.result == "rejected":
            raise ValueError(f"Übersteuerung abgelehnt: {' '.join(reply.details)}")


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
) -> None:
    """Akku, Multi und fremder Regler; der Tesla an der Wallbox ``tesla``, der e-Golf an
    ``egolf`` (``None``: abgesteckt).

    Für den Tesla, wenn angegeben: SOC ``tesla_soc_pct`` und Strom ``tesla_amps``. Mit
    ``tesla_starts`` hat er „Laden an“ im Auto und lädt, sobald die Wallbox Strom gibt; mit
    ``asleep`` schläft er.
    """
    model.soc_pct = soc_pct
    model.charger_only = charger_only
    model.foreign = foreign
    car = model.teslas[TESLA]
    if tesla is not None:
        model.plug(TESLA, tesla)
    if tesla_soc_pct is not None:
        car.soc_pct = tesla_soc_pct
    if tesla_amps is not None:
        car.charge_amps = tesla_amps
    car.charge_enabled = tesla_starts
    car.asleep = asleep
    if egolf is not None:
        model.plug(EGOLF, egolf)


def _intent(**modes: ChargeMode) -> Intent:
    """Die Absicht mit Regelbetrieb und den Modi der Fahrzeuge."""
    vehicles = {key: VehicleIntent(mode=mode) for key, mode in modes.items()}
    return Intent(version=1, control=True, vehicles=vehicles)


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


def _named(code: str) -> Check:
    """Mindestens eine Entscheidung nennt den Grund ``code``."""

    def find(result: SimResult) -> list[str]:
        if any(code in d.codes for d in result.decisions):
            return []
        return [f"Keine Entscheidung mit „{code}“."]

    return Check(f"Eine Entscheidung nennt „{code}“.", find)


def _reserve_kept(result: SimResult) -> list[str]:
    """Der SOC sinkt nie unter ``RESERVE_PCT``."""
    lowest = min((s.soc_pct for s in result.samples), default=RESERVE_PCT)
    if lowest < RESERVE_PCT:
        shown = de(round(lowest, 4))
        return [f"SOC bis {shown} %, unter der USV-Reserve von {de(RESERVE_PCT)} %."]
    return []


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
    """Genau ein Weckvorgang: eine Entscheidung mit dem Grund ``wake`` und einem Wert."""
    actuator = vehicle_actuator(key)

    def find(result: SimResult) -> list[str]:
        wakes = [
            d
            for d in result.decisions
            if d.actuator == actuator
            and WAKE in d.codes
            and d.result is not DecisionResult.REJECTED
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
    """Ab ``begin_s`` lädt jedes Auto in jeder Sekunde des Laufs."""

    def find(result: SimResult) -> list[str]:
        after = [s for s in result.samples if s.t >= begin_s]
        if not after:
            return [f"Der Lauf endet vor {begin_s} s."]
        found: list[str] = []
        for key in after[0].cars:
            idle = next((s.t for s in after if not s.cars[key].charging), None)
            if idle is not None:
                found.append(f"„{key}“ lädt bei {idle} s nicht.")
        return found

    return Check(f"Ab {de(begin_s / MINUTE_S)} min laden beide Autos.", find)


def _unclear_before(key: str, until_s: int) -> Check:
    """Vor ``until_s`` zeigt die Gesundheit für ``key`` mindestens einmal „unklar“."""

    def find(result: SimResult) -> list[str]:
        shown = any(
            vehicle["id"] == key and vehicle["assignment"] == UNCLEAR
            for t, health in result.health
            if t < until_s
            for vehicle in health.vehicles
        )
        return [] if shown else [f"Die Gesundheit zeigt „{key}“ vor {until_s} s nie „unklar“."]

    return Check(f"Die Gesundheit zeigt „{key}“ vor {de(until_s / MINUTE_S)} min „unklar“.", find)


def _one_current(key: str, amps: float) -> Check:
    """Genau eine Entscheidung über den Strom ``amps`` an das Fahrzeug ``key``."""
    actuator = vehicle_actuator(key)

    def find(result: SimResult) -> list[str]:
        found = [
            d
            for d in result.decisions
            if d.actuator == actuator and d.kind is CommandKind.CURRENT and d.requested == amps
        ]
        if len(found) == 1:
            return []
        return [
            f"{len(found)} Entscheidungen „Strom {de(amps)} A“ an „{key}“, verlangt genau eine."
        ]

    return Check(f"Genau eine Entscheidung „Strom {de(amps)} A“ an „{key}“.", find)


def _evcs_off_from(key: str, begin_s: int) -> Check:
    """Ab ``begin_s`` lädt die EVCS ``key`` nicht: Kein Auto lädt, ihr letzter Befehl „Laden“
    mit Wert davor ist „aus“, und danach kommt kein „Laden an“."""
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


# --- Szenarien 1 bis 5 ----------------------------------------------------------------------------


def _sonnig() -> Scenario:
    """Die Verteilung ist gierig nach Priorität (Design 6.2): Mit höchstens 10 kW PV laden die
    Autos nacheinander, erst der Tesla bis „Complete“, dann der e-Golf."""
    start, duration_s, seed = SUMMER, 2 * HOUR_S, 1
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
        checks=Checks((_one_after_the_other_check(tesla_min=45.0, egolf_min=45.0),)),
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

    kept = Check(f"Der SOC sinkt nie unter {de(RESERVE_PCT)} %.", _reserve_kept)
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
        checks=Checks((_named("reserve_guard"), kept)),
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
    """Der Tesla steht an der EVCS: Die Zuordnung wird unklar (Präzisierung 5), bis die
    Übersteuerung ihn dort festlegt; er bekommt dann einmal seinen Höchststrom (Design 8). Das
    Abstecken beendet die Übersteuerung (Präzisierung 3)."""
    start, duration_s, seed = EVENING, HOUR_S, 9
    events = (
        (OVERRIDE_S, _Override(2, TESLA, EVCS_BOX)),
        (UNPLUG_S, _Unplug(TESLA)),
    )
    checks = (
        _unclear_before(TESLA, OVERRIDE_S),
        _one_current(TESLA, TESLA_MAX_A),
        _evcs_off_from(EVCS_BOX, EVCS_OFF_S),
    )
    return Scenario(
        name="zuordnung",
        title="Zuordnung unklar und Übersteuerung",
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
    )
}
