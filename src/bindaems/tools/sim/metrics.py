"""Kennzahlen eines Simulatorlaufs (Design 13.2, Plan 2c-1 Präzisierung 16).

Gemessen wird von außen: am Modell je Sekunde (``Sample``) und an den Entscheidungen des core. Die
Kennzahlen und ihre Grenzen:

- **Höchster Phasenstrom:** der größte Netzstrom einer Phase, höchstens ``grid.fuse_a``.
- **Längste Zeit über dem Ziel an der Sicherung** (``fuse_a − fuse_margin_a``): die längste Folge
  von Sekunden mit einer Phase darüber, höchstens ``OVER_FUSE_TARGET_S``.
- **Längste Zeit über dem Ziel in PV-Modi:** Gezählt werden Sekunden, in denen mindestens ein Auto
  lädt, alle ladenden Autos laut Absicht in PV-Überschuss oder Min+PV stehen, ihren Ladepunkt die
  Regelung live steuert (``CarSample.steered``; im Dry-Run stellt ihn ein anderer) und keines mit
  weniger als Mindeststrom + ``MINIMUM_MARGIN_A`` lädt (Stopp-Minuten, Mindeststrom von Min+PV).
  Der Wert ist die längste Folge solcher Sekunden mit einer Phase, deren
  ``grid_a + battery_feed_a`` über dem Ziel am Importlimit + ``TARGET_TOLERANCE_A`` liegt.
  Höchstens ein Übergang (siehe unten).
- **Akku-Energie in PV-Autos:** ``Σ min(Entladung, Leistung der Autos)`` geteilt durch die
  ``Σ Leistung der Autos``, in denselben Sekunden; höchstens ``BATTERY_SHARE_PCT``. In jeder
  ununterbrochenen Folge dieser Sekunden mit Entladung zählt die Entladung erst nach einem Übergang.
- **Starts je Auto und Stunde:** Wechsel „lädt nicht“ → „lädt“ im Modell, in einem gleitenden
  Fenster von einer Stunde; höchstens ``STARTS_PER_HOUR``.
- **Kürzester Abstand der Tesla-Strombefehle:** Entscheidungen über den Strom eines Fahrzeugs mit
  Tessie mit Ergebnis ``ok`` oder Dry-Run, ohne Befund ``urgent.bypass``; mindestens
  ``commands.current_interval_s``.
- **Weckvorgänge:** Entscheidungen mit dem Grund ``wake`` und einem Wert (nicht abgelehnt, nicht
  zurückgestellt); höchstens ``wake.per_day`` je Kalendertag in Wiener Zeit, im Abstand von
  mindestens ``wake.interval_s``.
- **Wiederholte Dry-Run-Entscheidungen:** Dry-Run-Entscheidungen, deren Wert gleich dem vorigen
  eigenen Wert der Regelung für Aktor und Art ist (``OwnValue``); keine. Eigene Werte setzt jedes
  Urteil mit Wert und ``UNCHANGED`` (Präzisierung 10); ``UNCHANGED`` steht in keiner Entscheidung,
  darum bringt der Lauf die eigenen Werte mit. Vergisst die Regelung sie (``Forgotten``: Pause,
  ``forget``, „nicht gesendet“), ist die nächste Entscheidung keine Wiederholung. Was sie in sich
  vergisst (Ladepunkt wieder geregelt, neuer Ansteckvorgang, höhere Stufe der Überlast), sieht die
  Kennzahl nicht; in den Szenarien im Dry-Run kommt das nach der ersten Entscheidung nicht vor.
  Ohne eigene Werte zählen nur die Dry-Run-Entscheidungen selbst. Eine Entscheidung
  „zurückgestellt“ ist weder eine Dry-Run-Entscheidung noch ein eigener Wert.

**Informationszahlen** ohne Grenze (``Metrics.info``): der rohe Anteil der Akku-Energie in PV-Autos,
in denselben Sekunden, aber ohne die Ausnahme der Übergänge (eine Kennzahl von 0 % kann ganz aus
Übergängen bestehen), und je Fahrzeug mit Tessie die Entscheidungen „zurückgestellt“ wegen eines
Stundenlimits (``rate.per_hour``, ``rate.start_stop``). Das Log hält dieselbe Zurückstellung
höchstens einmal je Minute fest.

Für die Prüfung des Szenarios „reserve“ misst ``measure`` außerdem die längste Zeit über dem Ziel am
Importlimit, solange die Reserve-Absicherung gilt (SOC unter ``battery.reserve_soc_pct +
control.reserve_guard_pct``; dann richten sich auch Sofort und Boost am Importlimit aus). Gerechnet
wird wie bei der Zeit über dem Ziel in PV-Modi, nur in jedem Modus.

**Akku ins Auto** (``battery_into_car_pct``, Plan 2c-2a, Präzisierung 17) ist keine Kennzahl des
Berichts, sondern eine Funktion für die Prüfungen eines Szenarios: der Anteil der Energie eines
Autos, die aus dem Akku kam. Gezählt wird in allen Sekunden, in denen das Auto lädt, auch am
Mindeststrom und in jedem Modus; die PV deckt zuerst das Haus, der Akku dann dessen Rest, und was
von der Entladung bleibt, geht an die ladenden Autos in der Reihenfolge von ``Sample.cars``.

**Übergänge** (Design 13.2: „abgesehen von Übergängen“): Nach einer Änderung darf die Regelung
``TRANSITION_S`` über dem Ziel liegen und Akku-Energie in PV-Autos fließen lassen. Lädt in der
Folge ein Fahrzeug mit Tessie, gilt ``tesla_transition_s``: Ein Wechsel auf oder von 5 A braucht
zwei Strombefehle im Abstand von ``commands.current_interval_s`` (Design 7.2; der Neuversuch nach
„nicht übernommen“ ist ein normaler Befehl), dazu ``TESLA_SETTLE_S`` für Antwort und Prüfung im
Tessie-Cache. Die 100 s aus Präzisierung 16 zählten nur einen Befehl.

Beim Abstand der Befehle an den Tesla ist der Abstand am Fahrzeug gemeint. Zeitpunkt eines Befehls
ist darum der, zu dem er gesendet wurde (``sent``), und nur ohne Angabe der seiner Entscheidung:
Die Entscheidung eines gesendeten Befehls trägt den Zeitpunkt seines Ergebnisses, beim Tesla bis zu
einer Minute nach dem Senden. Gilt für mehrere Fahrzeuge mit Tessie, gilt die strengste ihrer
Grenzen.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Final

from bindaems.core.safety.rate import HOURLY_LIMITS
from bindaems.shared.config import Config, Phase, VehicleConfig
from bindaems.shared.control import CommandKind, CommandValue, vehicle_actuator
from bindaems.shared.decisions import Decision, DecisionResult
from bindaems.shared.numbers import de
from bindaems.shared.timeutil import LOCAL_TZ

PV_MODES: Final = frozenset({"pv", "min_pv"})  # Modi, die nur PV-Überschuss nehmen sollen
MINIMUM_MARGIN_A: Final = 0.5  # darunter lädt ein Auto am Mindeststrom
TARGET_TOLERANCE_A: Final = 0.5  # über dem Ziel am Importlimit zählt, was das übersteigt
OVER_FUSE_TARGET_S: Final = 10  # über dem Ziel an der Sicherung (Design 5.4: Stopp aller nach 10 s)
TRANSITION_S: Final = 10  # ein Übergang in PV-Modi (Design 13.2)
TESLA_SETTLE_S: Final = 20  # Antwort von Tessie und Prüfung im Cache nach einem Strombefehl
BATTERY_SHARE_PCT: Final = 5.0  # Akku-Energie in PV-Autos (Präzisierung 16)
STARTS_PER_HOUR: Final = 4  # Start/Stopp-Zyklen je Auto und Stunde (Spec 1.3)
HOUR_S: Final = 3600
WAKE: Final = "wake"  # Grund eines Weckstarts
URGENT_BYPASS: Final = "urgent.bypass"  # Befund eines dringenden Befehls ohne Mindestabstand

SHOWN_DIGITS: Final = 2  # Nachkommastellen der Werte in Texten

_COMMANDED: Final = frozenset({DecisionResult.OK, DecisionResult.DRY_RUN})
# Entscheidungen ohne Urteil mit Wert: Ihr Befehl ging weder hinaus noch in den Dry-Run.
_HELD_BACK: Final = frozenset({DecisionResult.REJECTED, DecisionResult.DEFERRED})


@dataclass(frozen=True)
class CarSample:
    """Ein Auto in einer Probe.

    ``charging`` heißt, es zieht Strom; ``current_a`` ist sein Strom je Phase, ``power_w`` seine
    Ladeleistung. ``mode`` ist der Modus laut Absicht (``pv``, ``min_pv``, ``now``, ``off``,
    ``planned`` oder ``boost``), ``min_a`` der Mindeststrom seines Ladepunkts. ``complete`` heißt,
    es hat sein Ladelimit erreicht („Complete“). ``steered`` heißt, die Regelung steuert seinen
    Ladepunkt live; sonst stellt ihn ein anderer, etwa im Dry-Run der fremde Regler. ``soc_pct``
    ist sein Ladestand im Modell, ungerundet (Plan 4b); ``None`` ohne Angabe.
    """

    charging: bool
    current_a: float
    power_w: float
    mode: str
    min_a: int
    complete: bool = False
    steered: bool = True
    soc_pct: float | None = None


@dataclass(frozen=True)
class OwnValue:
    """Ein eigener Wert der Regelung (Präzisierung 10): das Urteil über einen ihrer Befehle, das
    den letzten eigenen Wert setzt, also eines mit Wert oder ``UNCHANGED``.

    ``value`` ist der angeforderte Wert; ``dry_run`` heißt, das Urteil ergab eine
    Dry-Run-Entscheidung.
    """

    actuator: str
    kind: CommandKind
    value: CommandValue
    dry_run: bool


@dataclass(frozen=True)
class Forgotten:
    """Die Regelung hat letzte eigene Werte vergessen: die des Aktors ``actuator``, mit ``kind``
    nur die dieser Art; ohne ``actuator`` alle."""

    actuator: str | None = None
    kind: CommandKind | None = None

    def covers(self, key: tuple[str, CommandKind]) -> bool:
        """Gilt das Vergessen für Aktor und Art ``key``?"""
        actuator, kind = key
        return (self.actuator is None or actuator == self.actuator) and (
            self.kind is None or kind == self.kind
        )


@dataclass(frozen=True)
class ThermalSample:
    """Die Klimaanlage nach einer Sekunde (Plan 6a): Betriebsart, Leistung in W, der Versatz, den
    die UVR anwendet (``active_k``, ihr Echo), die Versätze, die sie in dieser Sekunde vom core
    bekam (``received_k``), und ob das C.M.I. schweigt (``silent``).

    Aus der Regelung dieser Sekunde: Wunsch in Stufen und Grund (``wish``, ``reason``; ohne Stand
    ``None``) und ob der 15-min-Wächter Druck macht (``peak_pressure``: Zustand ``exceeded`` oder
    eine verlangte Senkung über 0, wie ``PeakGuard.pressure``).
    """

    mode: str
    power_w: float
    active_k: float
    received_k: tuple[float, ...] = ()
    silent: bool = False
    wish: int | None = None
    reason: str | None = None
    peak_pressure: bool = False


@dataclass(frozen=True)
class Sample:
    """Die Anlage nach Sekunde ``t`` des Laufs.

    ``grid_a`` ist der Netzstrom je Phase (positiv beim Bezug), ``battery_feed_a`` was der Multi je
    Phase aus dem Akku liefert, ``battery_w`` die Akkuleistung (positiv beim Laden), ``soc_pct`` der
    SOC und ``cars`` je Fahrzeug seine Probe.

    Dazu, alle in W: ``grid_w`` die Netzleistung (Summe der Phasen, positiv beim Bezug), ``pv_w``
    die PV-Leistung des Schritts (im Inselbetrieb 0) und ``house_w`` die Hauslast ohne die
    Wallboxen, mit der Klimaanlage. ``override_w`` ist der Override des Netz-Sollwerts, der im
    Schritt galt, und ``override_age_s`` die Sekunden seit seinem letzten Schreiben; ohne Override
    sind beide ``None``. ``thermal`` ist die Klimaanlage, wenn das Szenario sie am CoE-Adapter hat
    (``Scenario.thermal``), sonst ``None``.
    """

    t: int
    grid_a: Mapping[Phase, float]
    battery_feed_a: Mapping[Phase, float]
    battery_w: float
    soc_pct: float
    cars: Mapping[str, CarSample]
    grid_w: float = 0.0
    pv_w: float = 0.0
    house_w: float = 0.0
    override_w: float | None = None
    override_age_s: float | None = None
    thermal: ThermalSample | None = None


@dataclass(frozen=True)
class Figure:
    """Eine Kennzahl mit Wert und Grenze, beide als Text mit Einheit; ``ok``: in der Grenze."""

    name: str
    value: str
    limit: str
    ok: bool


@dataclass(frozen=True)
class Info:
    """Eine Informationszahl ohne Grenze: Name und Wert als Text, der Wert mit Einheit."""

    name: str
    value: str


@dataclass(frozen=True)
class Metrics:
    """Die Kennzahlen eines Laufs; ``None`` heißt, es gab nichts zu messen.

    - ``max_phase_a``: höchster Netzstrom einer Phase in A
    - ``over_33_s``: längste Folge von Sekunden über dem Ziel an der Sicherung
    - ``over_target_s``, ``over_target_tesla_s``: längste Folge über dem Ziel in PV-Modi, ohne bzw.
      mit ladendem Tesla
    - ``battery_share_pct``: Akku-Energie in PV-Autos in %
    - ``starts_per_hour``: die meisten Starts eines Autos in einer Stunde
    - ``tesla_interval_s``: kürzester Abstand zweier Tesla-Strombefehle in s
    - ``wakes_per_day``, ``wake_interval_s``: die meisten Weckvorgänge eines Wiener Tages und ihr
      kürzester Abstand in s
    - ``repeated_dry_runs``: wiederholte Dry-Run-Entscheidungen
    - ``battery_share_raw_pct``: Akku-Energie in PV-Autos in %, roh mit den Übergängen (Information)
    - ``hourly_deferrals``: je Fahrzeug mit Tessie die Entscheidungen „zurückgestellt“ wegen eines
      Stundenlimits (Information)
    - ``reserve_over_target_s``: längste Folge über dem Ziel am Importlimit unter der
      Reserve-Absicherung; die Grenze setzt die Prüfung des Szenarios „reserve“
    """

    max_phase_a: float
    over_33_s: int
    over_target_s: int
    over_target_tesla_s: int
    battery_share_pct: float
    starts_per_hour: int
    tesla_interval_s: float | None
    wakes_per_day: int
    wake_interval_s: float | None
    repeated_dry_runs: int
    battery_share_raw_pct: float
    hourly_deferrals: Mapping[str, int]
    reserve_over_target_s: int

    def figures(self, cfg: Config) -> list[Figure]:
        """Jede Kennzahl mit Wert und Grenze, in der Reihenfolge von Design 13.2."""
        grid, teslas = cfg.grid, _tesla_configs(cfg)
        fuse_target = grid.fuse_a - grid.fuse_margin_a
        interval = max((v.commands.current_interval_s for v in teslas), default=0.0)
        per_day = min((v.wake.per_day for v in teslas), default=0)
        wake_s = max((v.wake.interval_s for v in teslas), default=0.0)
        return [
            _at_most("Höchster Phasenstrom", self.max_phase_a, grid.fuse_a, " A"),
            _at_most(f"Längste Zeit über {de(fuse_target)} A", self.over_33_s, OVER_FUSE_TARGET_S),
            _at_most("Längste Zeit über dem Ziel in PV-Modi", self.over_target_s, TRANSITION_S),
            _at_most(
                "Längste Zeit über dem Ziel in PV-Modi, Tesla lädt",
                self.over_target_tesla_s,
                tesla_transition_s(cfg),
            ),
            _at_most("Akku-Energie in PV-Autos", self.battery_share_pct, BATTERY_SHARE_PCT, " %"),
            _at_most("Starts je Auto und Stunde", self.starts_per_hour, STARTS_PER_HOUR, ""),
            _at_least("Kürzester Abstand der Tesla-Strombefehle", self.tesla_interval_s, interval),
            _at_most("Weckvorgänge je Tag", self.wakes_per_day, per_day, ""),
            _at_least("Kürzester Abstand der Weckvorgänge", self.wake_interval_s, wake_s),
            _at_most("Wiederholte Dry-Run-Entscheidungen", self.repeated_dry_runs, 0, ""),
        ]

    def failures(self, cfg: Config) -> list[str]:
        """Je Kennzahl außerhalb ihrer Grenze ein Satz „{Kennzahl}: {Wert}, Grenze {Grenze}.“"""
        return [
            f"{figure.name}: {figure.value}, Grenze {figure.limit}."
            for figure in self.figures(cfg)
            if not figure.ok
        ]

    def info(self) -> list[Info]:
        """Die Informationszahlen ohne Grenze: der rohe Akku-Anteil, dann die Zurückstellungen
        wegen eines Stundenlimits je Fahrzeug mit Tessie, in der Reihenfolge der Konfiguration."""
        raw = Info(
            "Akku-Energie in PV-Autos, roh (mit Übergängen)",
            f"{de(round(self.battery_share_raw_pct, SHOWN_DIGITS))} %",
        )
        deferred = [
            Info(f"Zurückstellungen wegen des Stundenlimits an „{key}“", de(count))
            for key, count in self.hourly_deferrals.items()
        ]
        return [raw, *deferred]


def _at_most(name: str, value: float, limit: float, unit: str = " s") -> Figure:
    """Ein Wert mit Obergrenze, verglichen genau und gezeigt auf ``SHOWN_DIGITS`` Nachkommastellen;
    verdeckte die Rundung einen Verstoß, ungerundet (``shown_number``)."""
    shown = shown_number(value, SHOWN_DIGITS, lambda number: number <= limit)
    return Figure(name, f"{shown}{unit}", f"{de(limit)}{unit}", value <= limit)


def shown_number(value: float, digits: int, within: Callable[[float], bool]) -> str:
    """Ein Wert für einen Text (``de``), auf ``digits`` Nachkommastellen gerundet.

    ``within`` sagt, ob eine Zahl in der Grenze des Werts liegt. Liegt der Wert außerhalb, der
    gerundete aber in ihr, bleibt er ungerundet: Die Rundung verdeckte sonst den Verstoß, etwa
    „3200 W, verlangt höchstens 3200 W“ für 3200,4 W. Auch die Prüfungen der Szenarien zeigen ihre
    Werte so.
    """
    rounded = round(value, digits)
    return de(value if not within(value) and within(rounded) else rounded)


def _at_least(name: str, value: float | None, limit: float) -> Figure:
    """Ein Abstand: ohne zwei Befehle (``None``) gibt es keinen, und das ist in der Grenze."""
    shown = "–" if value is None else f"{de(value)} s"
    return Figure(name, shown, f"{de(limit)} s", value is None or value >= limit)


def measure(
    samples: Sequence[Sample],
    decisions: Sequence[Decision],
    cfg: Config,
    *,
    sent: Mapping[str, datetime] | None = None,
    own: Sequence[OwnValue | Forgotten] | None = None,
) -> Metrics:
    """Die Kennzahlen aus den Proben je Sekunde und den Entscheidungen, aufsteigend nach Zeit.

    ``sent`` nennt je Kennung einer Entscheidung den Zeitpunkt, zu dem ihr Befehl gesendet wurde;
    ohne Eintrag gilt der Zeitpunkt der Entscheidung. ``own`` sind die eigenen Werte der Regelung
    und was sie davon vergaß, in ihrer Reihenfolge, für die Wiederholungen im Dry-Run. Folgen zählen
    über aufeinanderfolgende Sekunden (``Sample.t``).
    """
    grid = cfg.grid
    fuse_target = grid.fuse_a - grid.fuse_margin_a
    target = grid.import_limit_a - cfg.control.import_margin_a + TARGET_TOLERANCE_A
    guard_soc = cfg.battery.reserve_soc_pct + cfg.control.reserve_guard_pct
    teslas = frozenset(key for key, vehicle in cfg.vehicles.items() if vehicle.tessie is not None)
    counted = [s for s in samples if _pv_only(s)]
    over_target = [s for s in counted if any(_helped_a(s, p) > target for p in s.grid_a)]
    runs: dict[bool, list[list[Sample]]] = {False: [], True: []}  # lädt ein Tesla in der Folge?
    for run in _runs(over_target):
        runs[any(_charging(s, teslas) for s in run)].append(run)
    guarded = [s for s in samples if s.soc_pct < guard_soc and _steered_above_minimum(s)]
    over_guarded = [s for s in guarded if any(_helped_a(s, p) > target for p in s.grid_a)]
    times = _times(decisions, sent or {})
    teslas_on = frozenset(vehicle_actuator(key) for key in teslas)
    currents = [
        d
        for d in decisions
        if d.actuator in teslas_on
        and d.kind is CommandKind.CURRENT
        and d.result in _COMMANDED
        and URGENT_BYPASS not in d.codes
    ]
    wakes = [
        d
        for d in decisions
        if d.actuator in teslas_on and WAKE in d.codes and d.result not in _HELD_BACK
    ]
    return Metrics(
        max_phase_a=max((amps for s in samples for amps in s.grid_a.values()), default=0.0),
        over_33_s=_longest(_runs(s for s in samples if max(s.grid_a.values()) > fuse_target)),
        over_target_s=_longest(runs[False]),
        over_target_tesla_s=_longest(runs[True]),
        battery_share_pct=_battery_share_pct(
            counted, teslas, skip_s=TRANSITION_S, tesla_skip_s=tesla_transition_s(cfg)
        ),
        starts_per_hour=_starts_per_hour(samples),
        tesla_interval_s=_shortest_gap(currents, times),
        wakes_per_day=_wakes_per_day(wakes, times),
        wake_interval_s=_shortest_gap(wakes, times),
        repeated_dry_runs=_repeated_dry_runs(decisions, own),
        battery_share_raw_pct=_battery_share_pct(counted, teslas, skip_s=0, tesla_skip_s=0),
        hourly_deferrals={
            key: _hourly_deferrals(decisions, vehicle_actuator(key))
            for key in cfg.vehicles
            if key in teslas
        },
        reserve_over_target_s=_longest(_runs(over_guarded)),
    )


def tesla_transition_s(cfg: Config) -> float:
    """Ein Übergang, wenn ein Fahrzeug mit Tessie lädt: zwei Strombefehle im Abstand von
    ``commands.current_interval_s`` und ``TESLA_SETTLE_S``; mit der Beispielkonfiguration 200 s."""
    interval = max((v.commands.current_interval_s for v in _tesla_configs(cfg)), default=0.0)
    return 2 * interval + TESLA_SETTLE_S


def _tesla_configs(cfg: Config) -> list[VehicleConfig]:
    """Die Fahrzeuge mit Tessie."""
    return [vehicle for vehicle in cfg.vehicles.values() if vehicle.tessie is not None]


def _pv_only(sample: Sample) -> bool:
    """Lädt mindestens ein Auto, alle ladenden live gesteuert, in PV-Modi und keins am
    Mindeststrom?"""
    return _steered_above_minimum(sample) and all(
        car.mode in PV_MODES for car in sample.cars.values() if car.charging
    )


def _steered_above_minimum(sample: Sample) -> bool:
    """Lädt mindestens ein Auto, alle ladenden live gesteuert und keins am Mindeststrom?"""
    charging = [car for car in sample.cars.values() if car.charging]
    return bool(charging) and all(
        car.steered and car.current_a >= car.min_a + MINIMUM_MARGIN_A for car in charging
    )


def _helped_a(sample: Sample, phase: Phase) -> float:
    """Netzstrom der Phase samt dem, was der Akku auf ihr liefert (Design 5.1)."""
    return sample.grid_a[phase] + sample.battery_feed_a.get(phase, 0.0)


def _charging(sample: Sample, keys: Iterable[str]) -> bool:
    return any(sample.cars[key].charging for key in keys if key in sample.cars)


def _runs(samples: Iterable[Sample]) -> list[list[Sample]]:
    """Die Folgen aufeinanderfolgender Sekunden."""
    runs: list[list[Sample]] = []
    for sample in samples:
        if runs and runs[-1][-1].t == sample.t - 1:
            runs[-1].append(sample)
        else:
            runs.append([sample])
    return runs


def _longest(runs: Iterable[Sequence[Sample]]) -> int:
    return max((len(run) for run in runs), default=0)


def _battery_share_pct(
    samples: Sequence[Sample], teslas: Iterable[str], *, skip_s: float, tesla_skip_s: float
) -> float:
    """Der Anteil der Akku-Energie an der Energie der ladenden Autos in %, ohne die Übergänge.

    In jeder Folge aufeinanderfolgender Sekunden mit Entladung zählen die ersten ``skip_s`` nicht,
    lädt darin ein Fahrzeug aus ``teslas``, die ersten ``tesla_skip_s``. Mit 0 und 0 ist der
    Anteil roh.
    """
    cars = sum(cars_w(sample) for sample in samples)
    if cars <= 0:
        return 0.0
    keys = frozenset(teslas)
    discharge = 0.0
    for run in _runs(sample for sample in samples if sample.battery_w < 0):
        tesla = any(_charging(sample, keys) for sample in run)
        skipped = math.ceil(tesla_skip_s if tesla else skip_s)
        discharge += sum(min(-sample.battery_w, cars_w(sample)) for sample in run[skipped:])
    return 100.0 * discharge / cars


def cars_w(sample: Sample) -> float:
    """Die Leistung der ladenden Autos in W; auch für die Prüfungen der Szenarien."""
    return sum(car.power_w for car in sample.cars.values() if car.charging)


def battery_into_car_pct(samples: Sequence[Sample], key: str) -> float | None:
    """Der Anteil der Energie des Autos ``key``, die aus dem Akku kam, in %; ``None``, wenn es nie
    lädt.

    Gezählt wird in allen Sekunden, in denen das Auto lädt, auch am Mindeststrom, in jedem Modus
    und gleich, wer seinen Ladepunkt steuert. In jeder Sekunde nimmt die PV zuerst das Haus
    (``Sample.house_w``), der Akku dann dessen Rest, und von der Entladung bleibt der Rest für die
    Autos: Lädt mehr als ein Auto, bekommt das Auto ``key`` davon, was nach den ladenden Autos vor
    ihm in ``Sample.cars`` übrig ist (nie unter 0). Aus dem Akku kommt ``min(Leistung des Autos,
    Rest)``. In jeder Folge aufeinanderfolgender Sekunden, in denen das Auto lädt und der Akku
    entlädt, zählen die ersten ``TRANSITION_S`` nicht (wie bei der Akku-Energie in PV-Autos); einen
    längeren Übergang für den Tesla gibt es nicht, denn die Funktion kennt die Konfiguration nicht.
    Der Nenner ist die Energie des Autos in allen seinen Ladesekunden.
    """
    charging = [sample for sample in samples if key in sample.cars and sample.cars[key].charging]
    energy = sum(sample.cars[key].power_w for sample in charging)
    if energy <= 0:
        return None
    from_battery = 0.0
    for run in _runs(sample for sample in charging if sample.battery_w < 0):
        from_battery += sum(_battery_into_car_w(sample, key) for sample in run[TRANSITION_S:])
    return 100.0 * from_battery / energy


def _battery_into_car_w(sample: Sample, key: str) -> float:
    """Die Leistung in W, die in der Probe aus dem Akku ins ladende Auto ``key`` geht."""
    discharge = max(0.0, -sample.battery_w)
    rest = max(0.0, discharge - max(0.0, sample.house_w - sample.pv_w))
    for other, car in sample.cars.items():
        if other == key:
            break
        if car.charging:
            rest = max(0.0, rest - car.power_w)
    return min(sample.cars[key].power_w, rest)


def _starts_per_hour(samples: Sequence[Sample]) -> int:
    """Die meisten Starts eines Autos in einem Fenster ``[t, t + 1 h)``."""
    starts: dict[str, list[int]] = defaultdict(list)
    for before, sample in pairwise(samples):
        for key, car in sample.cars.items():
            previous = before.cars.get(key)
            if car.charging and previous is not None and not previous.charging:
                starts[key].append(sample.t)
    most = 0
    for times in starts.values():
        for i, start in enumerate(times):
            most = max(most, sum(1 for t in times[i:] if t < start + HOUR_S))
    return most


def _times(decisions: Sequence[Decision], sent: Mapping[str, datetime]) -> dict[str, datetime]:
    """Der Zeitpunkt jedes Befehls: gesendet, sonst der seiner Entscheidung."""
    return {d.id: sent.get(d.id, d.ts) for d in decisions}


def _shortest_gap(decisions: Sequence[Decision], times: Mapping[str, datetime]) -> float | None:
    """Der kürzeste Abstand zweier Befehle desselben Aktors in s; ``None`` ohne zwei Befehle."""
    gaps = [
        (later - earlier).total_seconds()
        for moments in _by_actuator(decisions, times).values()
        for earlier, later in pairwise(moments)
    ]
    return min(gaps, default=None)


def _wakes_per_day(decisions: Sequence[Decision], times: Mapping[str, datetime]) -> int:
    """Die meisten Weckvorgänge eines Aktors an einem Kalendertag in Wiener Zeit."""
    days: dict[tuple[str, object], int] = defaultdict(int)
    for actuator, moments in _by_actuator(decisions, times).items():
        for moment in moments:
            days[(actuator, moment.astimezone(LOCAL_TZ).date())] += 1
    return max(days.values(), default=0)


def _by_actuator(
    decisions: Sequence[Decision], times: Mapping[str, datetime]
) -> dict[str, list[datetime]]:
    moments: dict[str, list[datetime]] = defaultdict(list)
    for decision in decisions:
        moments[decision.actuator].append(times[decision.id])
    return {actuator: sorted(found) for actuator, found in moments.items()}


def _hourly_deferrals(decisions: Sequence[Decision], actuator: str) -> int:
    """Die Entscheidungen „zurückgestellt“ des Aktors mit dem Befund eines Stundenlimits."""
    return sum(
        1
        for d in decisions
        if d.actuator == actuator
        and d.result is DecisionResult.DEFERRED
        and not HOURLY_LIMITS.isdisjoint(d.codes)
    )


def _repeated_dry_runs(
    decisions: Sequence[Decision], own: Sequence[OwnValue | Forgotten] | None
) -> int:
    """Dry-Run-Entscheidungen mit demselben Wert wie der vorige eigene Wert für Aktor und Art.

    Ein ``Forgotten`` löscht die vorigen Werte, die es nennt. Ohne ``own`` sind die eigenen Werte
    nur die Dry-Run-Entscheidungen selbst.
    """
    if own is None:
        own = [
            OwnValue(d.actuator, d.kind, d.requested, dry_run=True)
            for d in decisions
            if d.result is DecisionResult.DRY_RUN
        ]
    last: dict[tuple[str, CommandKind], CommandValue] = {}
    repeated = 0
    for event in own:
        if isinstance(event, Forgotten):
            last = {key: value for key, value in last.items() if not event.covers(key)}
            continue
        key = (event.actuator, event.kind)
        if event.dry_run and key in last and last[key] == event.value:
            repeated += 1
        last[key] = event.value
    return repeated
