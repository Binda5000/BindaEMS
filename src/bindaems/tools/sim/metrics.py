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
- **Weckvorgänge:** Entscheidungen mit dem Grund ``wake`` und einem Wert (nicht abgelehnt);
  höchstens ``wake.per_day`` je Kalendertag in Wiener Zeit, im Abstand von mindestens
  ``wake.interval_s``.
- **Wiederholte Dry-Run-Entscheidungen:** Dry-Run-Entscheidungen, deren Wert gleich dem vorigen
  eigenen Wert der Regelung für Aktor und Art ist (``OwnValue``); keine. Eigene Werte setzt jedes
  Urteil mit Wert und ``UNCHANGED`` (Präzisierung 10); ``UNCHANGED`` steht in keiner Entscheidung,
  darum bringt der Lauf die eigenen Werte mit. Ohne sie zählen nur die Dry-Run-Entscheidungen
  selbst. Eine Pause der Regelung, nach der sie ihre Werte vergisst, sieht die Kennzahl nicht; in
  den Szenarien im Dry-Run kommt keine vor.

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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Final

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


@dataclass(frozen=True)
class CarSample:
    """Ein Auto in einer Probe.

    ``charging`` heißt, es zieht Strom; ``current_a`` ist sein Strom je Phase, ``power_w`` seine
    Ladeleistung. ``mode`` ist der Modus laut Absicht (``pv``, ``min_pv``, ``now``, ``off`` oder
    ``boost``), ``min_a`` der Mindeststrom seines Ladepunkts. ``complete`` heißt, es hat sein
    Ladelimit erreicht („Complete“). ``steered`` heißt, die Regelung steuert seinen Ladepunkt live;
    sonst stellt ihn ein anderer, etwa im Dry-Run der fremde Regler.
    """

    charging: bool
    current_a: float
    power_w: float
    mode: str
    min_a: int
    complete: bool = False
    steered: bool = True


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
class Sample:
    """Die Anlage nach Sekunde ``t`` des Laufs.

    ``grid_a`` ist der Netzstrom je Phase (positiv beim Bezug), ``battery_feed_a`` was der Multi je
    Phase aus dem Akku liefert, ``battery_w`` die Akkuleistung (positiv beim Laden), ``soc_pct`` der
    SOC und ``cars`` je Fahrzeug seine Probe.
    """

    t: int
    grid_a: Mapping[Phase, float]
    battery_feed_a: Mapping[Phase, float]
    battery_w: float
    soc_pct: float
    cars: Mapping[str, CarSample]


@dataclass(frozen=True)
class Figure:
    """Eine Kennzahl mit Wert und Grenze, beide als Text mit Einheit; ``ok``: in der Grenze."""

    name: str
    value: str
    limit: str
    ok: bool


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


def _at_most(name: str, value: float, limit: float, unit: str = " s") -> Figure:
    """Ein Wert mit Obergrenze, verglichen genau und gezeigt auf ``SHOWN_DIGITS`` Nachkommastellen;
    verdeckte die Rundung einen Verstoß, ungerundet."""
    ok = value <= limit
    shown = round(value, SHOWN_DIGITS)
    if not ok and shown <= limit:
        shown = value
    return Figure(name, f"{de(shown)}{unit}", f"{de(limit)}{unit}", ok)


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
    own: Sequence[OwnValue] | None = None,
) -> Metrics:
    """Die Kennzahlen aus den Proben je Sekunde und den Entscheidungen, aufsteigend nach Zeit.

    ``sent`` nennt je Kennung einer Entscheidung den Zeitpunkt, zu dem ihr Befehl gesendet wurde;
    ohne Eintrag gilt der Zeitpunkt der Entscheidung. ``own`` sind die eigenen Werte der Regelung
    in ihrer Reihenfolge, für die Wiederholungen im Dry-Run. Folgen zählen über
    aufeinanderfolgende Sekunden (``Sample.t``).
    """
    grid = cfg.grid
    fuse_target = grid.fuse_a - grid.fuse_margin_a
    target = grid.import_limit_a - cfg.control.import_margin_a + TARGET_TOLERANCE_A
    teslas = frozenset(key for key, vehicle in cfg.vehicles.items() if vehicle.tessie is not None)
    counted = [s for s in samples if _pv_only(s)]
    over_target = [s for s in counted if any(_helped_a(s, p) > target for p in s.grid_a)]
    runs: dict[bool, list[list[Sample]]] = {False: [], True: []}  # lädt ein Tesla in der Folge?
    for run in _runs(over_target):
        runs[any(_charging(s, teslas) for s in run)].append(run)
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
        if d.actuator in teslas_on and WAKE in d.codes and d.result is not DecisionResult.REJECTED
    ]
    return Metrics(
        max_phase_a=max((amps for s in samples for amps in s.grid_a.values()), default=0.0),
        over_33_s=_longest(_runs(s for s in samples if max(s.grid_a.values()) > fuse_target)),
        over_target_s=_longest(runs[False]),
        over_target_tesla_s=_longest(runs[True]),
        battery_share_pct=_battery_share_pct(counted, teslas, tesla_transition_s(cfg)),
        starts_per_hour=_starts_per_hour(samples),
        tesla_interval_s=_shortest_gap(currents, times),
        wakes_per_day=_wakes_per_day(wakes, times),
        wake_interval_s=_shortest_gap(wakes, times),
        repeated_dry_runs=_repeated_dry_runs(decisions, own),
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
    charging = [car for car in sample.cars.values() if car.charging]
    return bool(charging) and all(
        car.steered and car.mode in PV_MODES and car.current_a >= car.min_a + MINIMUM_MARGIN_A
        for car in charging
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
    samples: Sequence[Sample], teslas: Iterable[str], tesla_transition: float
) -> float:
    """Der Anteil der Akku-Energie an der Energie der ladenden Autos in %, ohne die Übergänge.

    In jeder Folge aufeinanderfolgender Sekunden mit Entladung zählen die ersten ``TRANSITION_S``
    nicht, lädt darin ein Fahrzeug aus ``teslas``, die ersten ``tesla_transition``.
    """
    cars = sum(_cars_w(sample) for sample in samples)
    if cars <= 0:
        return 0.0
    keys = frozenset(teslas)
    discharge = 0.0
    for run in _runs(sample for sample in samples if sample.battery_w < 0):
        tesla = any(_charging(sample, keys) for sample in run)
        skipped = math.ceil(tesla_transition if tesla else TRANSITION_S)
        discharge += sum(min(-sample.battery_w, _cars_w(sample)) for sample in run[skipped:])
    return 100.0 * discharge / cars


def _cars_w(sample: Sample) -> float:
    """Die Leistung der ladenden Autos."""
    return sum(car.power_w for car in sample.cars.values() if car.charging)


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


def _repeated_dry_runs(decisions: Sequence[Decision], own: Sequence[OwnValue] | None) -> int:
    """Dry-Run-Entscheidungen mit demselben Wert wie der vorige eigene Wert für Aktor und Art.

    Ohne ``own`` sind die eigenen Werte nur die Dry-Run-Entscheidungen selbst.
    """
    if own is None:
        own = [
            OwnValue(d.actuator, d.kind, d.requested, dry_run=True)
            for d in decisions
            if d.result is DecisionResult.DRY_RUN
        ]
    last: dict[tuple[str, CommandKind], CommandValue] = {}
    repeated = 0
    for value in own:
        key = (value.actuator, value.kind)
        if value.dry_run and key in last and last[key] == value.value:
            repeated += 1
        last[key] = value.value
    return repeated
