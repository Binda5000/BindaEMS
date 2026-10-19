"""Eigenschaften der SafetyLayer (Spec 8.3): Kein ausgehender Befehl verletzt je eine harte Grenze.

hypothesis erzeugt beliebige Befehle und Kontexte. Die Orakel setzen die Regeltabellen unabhängig
von der Implementierung noch einmal um und lesen die Grenzen direkt aus der Konfiguration, nicht aus
``HardLimits``:

- ``in_range``: Wertebereiche (Regeln 1–3)
- ``safer``: „Richtung sicherer Zustand“ (``toward_safe``)
- ``is_increase`` und ``predicted``: was als Erhöhung gilt und welche Phasenströme danach zu
  erwarten sind (Regel 4)
- ``rate_allows`` und ``count``: Mindestabstände und Stundenlimits (Regel 5), Schreibbudget
  (Regel 6)

Jeder geprüfte Befehl bekommt eine frische SafetyLayer mit eigenem Schreibbudget. Die Budgets eines
Falls liegen in dessen eigenem temporären Verzeichnis, denn hypothesis verträgt keine
funktionsbezogenen Fixtures wie ``tmp_path``.

Ein Befehl mit ``refresh`` (Auffrischen eines Overrides, Plan 2c-2a) kommt in denselben Strategien
vor. Beim Netz-Sollwert ist er nie dringend; sonst gelten für ihn alle Regeln wie ohne ``refresh``,
nur das Totband fällt weg. Bei jeder anderen Art hat ``refresh`` keine Wirkung: Das Urteil ist
dasselbe wie ohne, auch für einen dringenden Befehl. Das prüft der Vergleich mit demselben Befehl
ohne ``refresh`` in ``test_refresh_differs_from_a_plain_command_only_by_the_deadband``.
"""

import json
import math
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from hypothesis import example, given, settings
from hypothesis import strategies as st
from tests.helpers import T0
from tests.unit.core.safety.conftest import PHASES, make_ctx

from bindaems.core.safety.budget import PersistentWriteBudget
from bindaems.core.safety.layer import SafetyLayer
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import SENDABLE, InputStatus, Outcome, SafetyContext, Verdict
from bindaems.shared.config import load_config
from bindaems.shared.control import ActuatorMode, Command, CommandKind, OperatingState

K = CommandKind
S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731

CFG = load_config(Path("deploy/config.example.yaml"))
LIMITS = HardLimits.from_config(CFG)

VICTRON, EVCS, TESLA, TWC = "victron", "wallbox.evcs", "vehicle.tesla", "wallbox.twc"
TARGETS = (VICTRON, EVCS, TESLA, TWC)

# --- Grenzen und Tabellen fürs Orakel, direkt aus der Konfiguration und den Regeltabellen ---------

KINDS = {  # Aktorkatalog: Der Wall Connector ist kein Aktor
    VICTRON: (K.GRID_SETPOINT, K.MAX_DISCHARGE, K.MIN_SOC),
    EVCS: (K.CURRENT, K.CHARGING),
    TESLA: (K.CURRENT, K.CHARGING, K.CHARGE_LIMIT),
}
PHASE_LIMIT_A = CFG.grid.fuse_a - CFG.grid.fuse_margin_a
VOLTAGE_V = CFG.grid.voltage_nominal_v
RESERVE = CFG.battery.reserve_soc_pct
MAX_DISCHARGE_W = CFG.battery.max_discharge_w
CURRENTS = {  # (min_a, max_a, safe_a); beim Tesla ist der sichere Strom der Mindeststrom
    EVCS: (CFG.wallboxes["evcs"].min_a, CFG.wallboxes["evcs"].max_a, CFG.wallboxes["evcs"].safe_a),
    TESLA: (CFG.vehicles["tesla"].min_a, CFG.vehicles["tesla"].max_a, CFG.vehicles["tesla"].min_a),
}
WALLBOX_PHASES = {key: tuple(wallbox.phase_map) for key, wallbox in CFG.wallboxes.items()}
ACTIVE_A = 0.5  # ab diesem Strom zieht eine Wallbox auf einer Phase
TESLA_LIMITS = CFG.vehicles["tesla"].commands
PER_HOUR = CFG.victron.persistent_writes.per_hour
PER_DAY = CFG.victron.persistent_writes.per_day
INTERVAL_S = {  # Mindestabstände je Aktor und Art; Tesla-Start/Stopp und persistente Arten: keiner
    (EVCS, K.CURRENT): 5.0,
    (EVCS, K.CHARGING): 5.0,
    (VICTRON, K.GRID_SETPOINT): 2.0,
    (TESLA, K.CURRENT): TESLA_LIMITS.current_interval_s,
    (TESLA, K.CHARGE_LIMIT): 600.0,  # Ladelimit (Plan 4b, Präzisierung 9)
}
CHARGE_LIMIT_PCT = (50, 100)  # ganze Prozent; darunter abgelehnt, darüber geklemmt
HOUR_S, DAY_S = 3600.0, 86400.0


# --- Orakel ---------------------------------------------------------------------------------------


def refreshing(command: Command) -> bool:
    """Frischt der Befehl einen Override auf? ``refresh`` zählt nur beim Netz-Sollwert."""
    return command.refresh and command.kind == K.GRID_SETPOINT


def number(value: object) -> float | None:
    """Der Wert als endliche Zahl; ``bool``, ``None``, NaN, ±∞ und zu große ints sind keine."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        result = float(value)
    except OverflowError:
        return None
    return result if math.isfinite(result) else None


def setpoint_ceiling(budget_w: float | None) -> float:
    """Obergrenze des Netz-Sollwerts (Regel 2): Netzladesetpoint, Absicherung, Bezug, Budget.

    Sie ist nie negativ, auch nicht bei einem aufgebrauchten Budget.
    """
    ceilings = [CFG.victron.max_grid_charge_setpoint_w, 3 * VOLTAGE_V * PHASE_LIMIT_A]
    if CFG.grid.max_import_kw is not None:
        ceilings.append(CFG.grid.max_import_kw * 1000)
    if budget_w is not None:
        ceilings.append(budget_w)
    return max(min(ceilings), 0.0)


def in_range(command: Command, value: object, budget_w: float | None) -> bool:
    """Liegt der Wert in den harten Grenzen seiner Art (Regeln 1–3)? Nur Aktoren haben Grenzen."""
    kind = command.kind
    if kind not in KINDS.get(command.actuator, ()):
        return False
    if kind == K.CHARGING:
        return isinstance(value, bool)
    if kind == K.GRID_SETPOINT and value is None:
        return True  # hebt den Override auf
    number_ = number(value)
    if number_ is None:
        return False
    if kind == K.CURRENT:
        min_a, max_a, _ = CURRENTS[command.actuator]
        return number_.is_integer() and min_a <= number_ <= max_a
    if kind == K.GRID_SETPOINT:
        return 0 <= number_ <= setpoint_ceiling(budget_w)
    if kind == K.MAX_DISCHARGE:
        return number_ == -1 or 0 <= number_ <= MAX_DISCHARGE_W
    if kind == K.MIN_SOC:
        return RESERVE <= number_ <= 100
    if kind == K.CHARGE_LIMIT:
        low, high = CHARGE_LIMIT_PCT
        return number_.is_integer() and low <= number_ <= high
    return False


def safer(command: Command, ctx: SafetyContext) -> bool:
    """Geht der Befehl Richtung sicherer Zustand (Tabelle zu ``toward_safe``)?

    Der Ist-Wert ist bekannt, wenn ``(Aktor, Art)`` in ``ctx.present`` steht.
    """
    kind, key = command.kind, (command.actuator, command.kind)
    if kind == K.CHARGING:
        return command.value is False
    if kind == K.GRID_SETPOINT and command.value is None:
        return True
    value = number(command.value)
    if value is None:
        return False
    known, actual = key in ctx.present, ctx.present.get(key)
    if kind == K.CURRENT:
        return value <= actual if known else value <= CURRENTS[command.actuator][2]
    if kind == K.GRID_SETPOINT:  # ohne Override zählt die Basis, und die kann unbekannt sein
        base = number(ctx.base_grid_setpoint_w)
        effective = base if actual is None else actual
        return known and effective is not None and value <= effective
    if kind == K.MAX_DISCHARGE:
        return value == -1 or (known and actual >= 0 and value >= actual)
    if kind == K.MIN_SOC:
        return value == RESERVE or (known and RESERVE <= value <= actual)
    return False  # das Ladelimit ist nie Richtung sicher: nie dringend


def setpoint_before(ctx: SafetyContext) -> float | None:
    """Wirksamer Netz-Sollwert vor dem Befehl: der Override, ohne Override die Basis.

    Ist der Override unbekannt, gilt der niedrigste mögliche Wert ``min(Basis, 0)``. ``None``
    heißt unbekannt: Es kommt auf die Basis an, und sie ist unbekannt (``None``, NaN, ±∞).
    """
    base = number(ctx.base_grid_setpoint_w)
    key = (VICTRON, K.GRID_SETPOINT)
    if key not in ctx.present:
        return None if base is None else min(base, 0.0)
    override = ctx.present[key]
    return base if override is None else override


def is_increase(command: Command, value: object, ctx: SafetyContext) -> bool:
    """Kann der gesendete Wert den Netzbezug erhöhen (Spalte „Erhöhung“ zu Regel 4)?"""
    kind, key = command.kind, (command.actuator, command.kind)
    known, actual = key in ctx.present, ctx.present.get(key)
    if kind == K.CHARGING:
        return value is True and actual is not True
    if kind == K.GRID_SETPOINT:  # bei unbekanntem Sollwert ist jede Zahl eine mögliche Erhöhung
        before = setpoint_before(ctx)
        return value is not None and (before is None or value > before)
    if kind == K.CURRENT:  # bei unbekanntem Ist-Strom nur über dem sicheren Strom
        return actual < value if known else value > CURRENTS[command.actuator][2]
    if kind == K.MAX_DISCHARGE:
        if value < 0:
            return False  # −1: unbegrenzt
        if ctx.battery_discharge_w is not None:
            return value < ctx.battery_discharge_w
        return not known or actual == -1 or actual > value  # Verschärfung bei unbekanntem D
    if kind == K.MIN_SOC:
        return value > RESERVE and (ctx.soc_pct is None or value > ctx.soc_pct)
    return False  # das Ladelimit zieht keinen Strom


def wallbox(actuator: str, ctx: SafetyContext) -> tuple[list[str], dict[str, float]]:
    """Die Phasen der Wallbox hinter dem Aktor und ihr gemessener Strom je Phase.

    Beim Tesla steht die Wallbox im Kontext. Ist sie unbekannt, kann er auf allen drei Phasen
    ziehen, und es gibt keine Messwerte.
    """
    key = "evcs" if actuator == EVCS else ctx.vehicle_wallbox.get(actuator)
    if key not in WALLBOX_PHASES:
        return list(PHASES), dict.fromkeys(PHASES, 0.0)
    measured = ctx.wallbox_phase_a.get(key, {})
    phases = [p for p in PHASES if p in WALLBOX_PHASES[key]]
    return phases, {p: measured.get(p, 0.0) for p in PHASES}


def predicted(command: Command, value: object, ctx: SafetyContext) -> list[float | None]:
    """Netzbezug je betroffener Phase nach dem Befehl (Regel 4); ``None`` heißt unbekannt."""
    imported = {p: ctx.phase_import_a.get(p) for p in PHASES}
    kind = command.kind
    if kind in (K.CURRENT, K.CHARGING):
        phases, measured = wallbox(command.actuator, ctx)
        if kind == K.CURRENT:  # lädt die Wallbox, zählen nur ihre aktiven Phasen
            phases = [p for p in phases if measured[p] >= ACTIVE_A] or phases
            draw = value
        else:  # Start: Ist-Strom, aber mindestens der Mindeststrom; unbekannt der Höchststrom
            min_a, max_a, _ = CURRENTS[command.actuator]
            actual = ctx.present.get((command.actuator, K.CURRENT))
            draw = max_a if actual is None else max(actual, min_a)
        return [None if imported[p] is None else imported[p] - measured[p] + draw for p in phases]
    if kind == K.GRID_SETPOINT:  # die Änderung verteilt sich gleichmäßig auf die drei Phasen
        before = setpoint_before(ctx)
        if before is None:
            return [None]
        extra = (value - before) / (3 * VOLTAGE_V)
    elif kind == K.MAX_DISCHARGE:  # was der Akku nicht mehr entlädt, kommt aus dem Netz
        if ctx.battery_discharge_w is None:
            return [None]
        extra = (ctx.battery_discharge_w - value) / (3 * VOLTAGE_V)
    else:  # Min-SOC über dem SOC: der Akku stoppt, seine Entladeleistung kommt aus dem Netz
        if ctx.battery_discharge_w is None or ctx.soc_pct is None:
            return [None]
        extra = ctx.battery_discharge_w / (3 * VOLTAGE_V)
    return [None if imported[p] is None else imported[p] + extra for p in PHASES]


def rate_allows(command: Command, at: float, sent: list[tuple[float, Command]]) -> bool:
    """Lässt Regel 5 den Befehl zum Zeitpunkt ``at`` zu, nach den bisher sendbaren Befehlen?"""
    key = (command.actuator, command.kind)
    same = [t for t, c in sent if (c.actuator, c.kind) == key]
    if key in INTERVAL_S and same and at - same[-1] < INTERVAL_S[key]:
        return False
    if command.actuator != TESLA:
        return True
    recent = [c for t, c in sent if c.actuator == TESLA and at - t < HOUR_S]
    if len(recent) >= TESLA_LIMITS.per_hour:
        return False
    starts_stops = sum(1 for c in recent if c.kind == K.CHARGING)
    return command.kind != K.CHARGING or starts_stops < TESLA_LIMITS.start_stop_per_hour


def count(times: list[float], at: float, window_s: float) -> int:
    """Zahl der Zeitpunkte bis ``at``, die weniger als ``window_s`` vor ``at`` liegen."""
    return sum(1 for t in times if 0 <= at - t < window_s)


def fullest_window(times: list[float], window_s: float) -> int:
    """Die meisten Zeitpunkte, die alle weniger als ``window_s`` auseinanderliegen."""
    return max((count(times, at, window_s) for at in times), default=0)


# --- Erzeugte Fälle -------------------------------------------------------------------------------
# Alle Strategien entstehen einmal hier, nicht bei jedem Ziehen. hypothesis zieht nicht jeden Fall
# neu, sondern variiert gefundene Fälle oft nur leicht. Damit seltene Kombinationen trotzdem in
# jedem Lauf vorkommen, prüft ein Fall mehrere Befehle: je einen für jede Art jedes Aktors, bei den
# Gates einen Befehl in allen Stellungen der Gates. Jeder Befehl bekommt eine frische SafetyLayer.


def weighted(*choices: tuple[int, Any]) -> st.SearchStrategy[Any]:
    """Eine der Möglichkeiten, ungefähr im Verhältnis der Gewichte; geschrumpft wird zur ersten."""
    return st.sampled_from([choice for weight, choice in choices for _ in range(weight)])


def mixed(*choices: tuple[int, st.SearchStrategy[Any]]) -> st.SearchStrategy[Any]:
    """Ein Wert aus einer der Strategien, ungefähr im Verhältnis der Gewichte."""
    return weighted(*choices).flatmap(lambda strategy: strategy)


# Befehle: alle Arten an die drei Aktoren und an den Wall Connector, dringend oder nicht
SPECIAL_VALUES = (-1, -1.0, math.nan, math.inf, -math.inf, 10**400, -(10**400))
ANY_VALUE = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-50_000, 50_000),
    st.floats(-50_000, 50_000),
    st.sampled_from(SPECIAL_VALUES),
)
VALUES = {  # meist ein Wert in der Nähe der Grenzen der Art, sonst irgendeiner
    K.CURRENT: mixed((3, st.integers(5, 32)), (2, st.floats(4, 40)), (1, ANY_VALUE)),
    K.CHARGING: mixed((4, st.booleans()), (1, ANY_VALUE)),
    K.GRID_SETPOINT: mixed(
        (1, st.none()),
        (3, st.floats(1, 9_000)),
        (1, st.floats(-1_000, -1)),  # unter 0: die Regeln 1–3 heben auf 0
        (1, st.floats(-1_000, 25_000)),
        (1, ANY_VALUE),
    ),
    K.MAX_DISCHARGE: mixed(
        (1, st.just(-1.0)),
        (3, st.floats(0, 3_000)),
        (1, st.floats(0, 15_000)),
        (
            1,
            st.floats(MAX_DISCHARGE_W, 2 * MAX_DISCHARGE_W),
        ),  # die Regeln 1–3 senken auf die Grenze
        (1, st.floats(-10, 0)),
        (1, ANY_VALUE),
    ),
    K.MIN_SOC: mixed((3, st.integers(20, 100)), (2, st.floats(15, 105)), (1, ANY_VALUE)),
    K.CHARGE_LIMIT: mixed((3, st.integers(40, 110)), (2, st.floats(45, 105)), (1, ANY_VALUE)),
    K.SETPOINT_OFFSET: mixed((3, st.floats(-3.5, 3.5)), (1, ANY_VALUE)),
}
PAIRS = [(actuator, kind) for actuator, kinds in KINDS.items() for kind in kinds]
STRAYS = [  # an den Wall Connector (kein Aktor) oder in einer Art, die der Aktor nicht kennt
    (actuator, kind)
    for actuator in TARGETS
    for kind in CommandKind
    if kind not in KINDS.get(actuator, ())
]
URGENT = st.booleans()
REFRESH = weighted((3, False), (1, True))  # etwa jeder vierte Befehl frischt einen Override auf


@st.composite
def commands(draw: st.DrawFn, pairs: st.SearchStrategy[tuple[str, CommandKind]]) -> Command:
    """Ein Befehl an Aktor und Art aus ``pairs``, mit einem Wert meist nahe den Grenzen der Art."""
    actuator, kind = draw(pairs)
    return Command(actuator, kind, draw(VALUES[kind]), urgent=draw(URGENT), refresh=draw(REFRESH))


ANY_COMMAND = commands(mixed((8, st.sampled_from(PAIRS)), (1, st.sampled_from(STRAYS))))
STRAY_COMMAND = commands(st.sampled_from(STRAYS))

# Gates: Betriebszustand, Sperren und Eingangsdaten; dazu die Betriebsart je Aktor
OPEN_STATE = weighted((2, OperatingState.CONTROL), (1, OperatingState.DEGRADED))
CLOSED_STATE = weighted(
    (2, OperatingState.SAFE), (1, OperatingState.OBSERVE), (1, OperatingState.START)
)
INACTIVE_STATE = st.sampled_from([OperatingState.OBSERVE, OperatingState.START])
REASONS = (
    "EVCS „evcs“ ist nicht im manuellen Modus (Modus 1).",
    "Dynamic ESS ist aktiv (Modus 1). Das EMS darf Victron nicht steuern.",
)
LOCK_REASONS = st.sampled_from([REASONS[:1], REASONS[1:], REASONS])
OTHER_LOCK = {  # höchstens eine Sperre an einem anderen Aktor; eine ohne Grund sperrt nicht
    target: st.dictionaries(
        st.sampled_from([other for other in TARGETS if other != target]),
        st.sampled_from([REASONS[:1], REASONS[1:], ()]),
        max_size=1,
    )
    for target in TARGETS
}
EMPTY_LOCK = weighted((4, False), (1, True))  # der Aktor steht ohne Grund in den Sperren
OK, NOT_OK = (
    InputStatus(True),
    InputStatus(False, ("Messwert fehlt oder ist veraltet: grid.l1.power_w.",)),
)
MODE = weighted((6, ActuatorMode.LIVE), (3, ActuatorMode.DRY_RUN), (1, None))  # None: kein Eintrag
CLOSED_GATE = weighted((7, None), (1, "state"), (1, "lock"), (1, "inputs"))
LOCKED_TARGET = st.sampled_from(TARGETS)

# Messwerte, Ist-Werte und Akku; die Phasen oft knapp unter der Grenze, damit Regel 4 eingreift
PHASE_IMPORT_A = mixed(
    (3, st.floats(0, PHASE_LIMIT_A)),
    (3, st.floats(15, PHASE_LIMIT_A)),
    (1, st.floats(-60, 60)),
    (1, st.none()),
)
WALLBOX_PHASE_A = st.dictionaries(
    st.sampled_from(["evcs", "twc", "garage"]),  # garage: keine konfigurierte Wallbox
    st.dictionaries(st.sampled_from(PHASES), st.floats(0, 32) | st.sampled_from([0.49, ACTIVE_A])),
)
VEHICLE_WALLBOX = st.sampled_from(
    [{TESLA: "twc"}, {TESLA: "evcs"}, {TESLA: None}, {TESLA: "garage"}, {}]
)
PRESENT = {  # typgerecht wie die Rücklesewerte der Aktoren ab 2b; None nur beim Netz-Sollwert
    (VICTRON, K.GRID_SETPOINT): st.none() | st.integers(0, 10_000) | st.floats(-1_000, 25_000),
    (VICTRON, K.MAX_DISCHARGE): st.just(-1) | st.integers(0, 15_000) | st.floats(-10, 15_000),
    (VICTRON, K.MIN_SOC): st.integers(0, 100) | st.floats(0, 100),
    (EVCS, K.CURRENT): st.integers(0, 32) | st.floats(-5, 40),
    (EVCS, K.CHARGING): st.booleans(),
    (TESLA, K.CURRENT): st.integers(0, 32) | st.floats(-5, 40),
    (TESLA, K.CHARGING): st.booleans(),
    (TESLA, K.CHARGE_LIMIT): st.integers(50, 100) | st.floats(0, 100),
}
KNOWN = st.booleans()
DISCHARGE_W = mixed((4, st.floats(1, 15_000)), (1, st.floats(0, 15_000)), (1, st.none()))
SOC_PCT = mixed((5, st.floats(0, 100)), (1, st.none()))
BASE_SETPOINT_W = mixed(  # manchmal unbekannt: None, NaN oder ±∞ (Plan 2b, Präzisierung 13)
    (6, st.floats(-500, 500)), (1, st.none()), (1, st.sampled_from([math.nan, math.inf, -math.inf]))
)
BUDGET_W = mixed((1, st.floats(0, 30_000)), (1, st.none()))
HISTORY = weighted((4, False), (1, True))

Checks = list[tuple[Command, SafetyContext]]


def context(draw: st.DrawFn, closed: str | None, actuator: str) -> SafetyContext:
    """Ein Kontext mit zufälligen Messwerten, Ist-Werten und Betriebsarten.

    ``closed`` ist das Gate, das zu ist: ``state`` (SAFE, OBSERVE oder START statt Regelbetrieb),
    ``lock`` (``actuator`` ist gesperrt) oder ``inputs`` (unplausible Eingangsdaten). Ein anderer
    Aktor kann gesperrt sein, und ``actuator`` kann ohne Grund in den Sperren stehen.
    """
    locks = dict(draw(OTHER_LOCK[actuator]))
    if closed == "lock":
        locks[actuator] = draw(LOCK_REASONS)
    elif draw(EMPTY_LOCK):
        locks[actuator] = ()
    return make_ctx(
        operating=draw(CLOSED_STATE if closed == "state" else OPEN_STATE),
        modes={target: mode for target in TARGETS if (mode := draw(MODE)) is not None},
        locks=locks,
        inputs=NOT_OK if closed == "inputs" else OK,
        phase_import_a={p: draw(PHASE_IMPORT_A) for p in PHASES},
        wallbox_phase_a=draw(WALLBOX_PHASE_A),
        vehicle_wallbox=draw(VEHICLE_WALLBOX),
        present={key: draw(values) for key, values in PRESENT.items() if draw(KNOWN)},
        battery_discharge_w=draw(DISCHARGE_W),
        soc_pct=draw(SOC_PCT),
        base_grid_setpoint_w=draw(BASE_SETPOINT_W),
        grid_budget_w=draw(BUDGET_W),
    )


@dataclass(frozen=True)
class Batch:
    """Je ein Befehl für jede Art jedes Aktors und einer daneben, alle im selben Kontext.

    ``recent``: Eben erst ging ein Befehl derselben Art durch, die Mindestabstände greifen.
    ``exhausted``: Das Schreibbudget ist aufgebraucht.
    """

    commands: tuple[Command, ...]
    ctx: SafetyContext
    recent: bool
    exhausted: bool

    def checks(self) -> Checks:
        return [(command, self.ctx) for command in self.commands]


@st.composite
def batches(draw: st.DrawFn) -> Batch:
    """Die Gates sind meist offen; sonst ist eines zu, eine Sperre trifft einen zufälligen Aktor."""
    batch = [
        Command(actuator, kind, draw(VALUES[kind]), urgent=draw(URGENT), refresh=draw(REFRESH))
        for actuator, kind in PAIRS
    ]
    batch.append(draw(STRAY_COMMAND))
    ctx = context(draw, draw(CLOSED_GATE), draw(LOCKED_TARGET))
    return Batch(tuple(batch), ctx, draw(HISTORY), draw(HISTORY))


DISTANCE = mixed((1, st.just(0.5)), (2, st.floats(1, 100)), (1, st.floats(100, 5_000)))


def safe_side(command: Command, distance: float) -> float | None:
    """Ein Ist-Wert ``distance`` auf der sicheren Seite des Werts: Der Befehl ginge Richtung sicher.

    Er liegt über dem Strom, dem Netz-Sollwert und dem Min-SOC und unter der Entladegrenze, nie
    unter 0. ``None`` bei ``charging`` und bei Werten, die keine endliche Zahl sind.
    """
    value = number(command.value)
    if value is None or command.kind == K.CHARGING:
        return None
    if command.kind == K.MAX_DISCHARGE:
        return max(value - distance, 0.0)
    return value + distance


def gate_settings(
    command: Command,
    ctx: SafetyContext,
    states: tuple[OperatingState, ...],
    reasons: tuple[str, ...],
) -> Checks:
    """Der Befehl in jeder Stellung der Gates: jeder Zustand aus ``states``, sein Aktor frei oder
    gesperrt, Eingangsdaten plausibel oder nicht."""
    locked = {**ctx.locks, command.actuator: reasons}
    return [
        (command, replace(ctx, operating=state, locks=locks, inputs=inputs))
        for state in states
        for locks in (ctx.locks, locked)
        for inputs in (OK, NOT_OK)
    ]


@dataclass(frozen=True)
class GateCase:
    """Ein Befehl in einem Kontext mit offenen Gates und was es braucht, um sie zu schließen.

    ``inactive``: OBSERVE oder START. ``reasons``: die Sperrgründe, wenn sein Aktor gesperrt ist.
    ``distance``: Abstand des Ist-Werts in der Gegenprobe. ``recent`` und ``exhausted`` wie bei
    ``Batch``.
    """

    command: Command
    ctx: SafetyContext
    inactive: OperatingState
    reasons: tuple[str, ...]
    distance: float
    recent: bool
    exhausted: bool

    def checks(self) -> Checks:
        """Der Befehl in allen Stellungen der Gates, dringend und nicht dringend.

        Die Stellungen: Regelbetrieb (CONTROL oder DEGRADED), SAFE, OBSERVE oder START; sein Aktor
        frei oder gesperrt; Eingangsdaten plausibel oder nicht. Dazu die Gegenprobe: der Befehl
        dringend, mit einem Ist-Wert knapp auf der sicheren Seite seines Werts. So kommt auch ein
        Wert vor, der Richtung sicher ginge, den die Regeln 1–3 aber wieder davon wegbringen.
        """
        command, ctx, reasons = self.command, self.ctx, self.reasons
        states = (ctx.operating, OperatingState.SAFE, self.inactive)
        checks = [
            *gate_settings(command, ctx, states, reasons),
            *gate_settings(replace(command, urgent=not command.urgent), ctx, states, reasons),
        ]
        near = safe_side(command, self.distance)
        if near is not None:
            present = {**ctx.present, (command.actuator, command.kind): near}
            urgent = replace(command, urgent=True)
            checks += gate_settings(urgent, replace(ctx, present=present), states, reasons)
        return checks


@st.composite
def gate_cases(draw: st.DrawFn) -> GateCase:
    """Ein Befehl, meist in einer Art seines Aktors, in einem Kontext mit offenen Gates."""
    command = draw(ANY_COMMAND)
    ctx = context(draw, None, command.actuator)
    return GateCase(
        command,
        ctx,
        draw(INACTIVE_STATE),
        draw(LOCK_REASONS),
        draw(DISTANCE),
        draw(HISTORY),
        draw(HISTORY),
    )


# Abstand des Ist-Werts vom Wert des Befehls je Art: gleich, im Totband, an dessen Rand, darüber
NEAR_OFFSETS = {
    K.GRID_SETPOINT: (0.0, 10.0, -10.0, 49.0, -49.0, 50.0, -50.0, 300.0),  # W, Totband 50 W
    K.CURRENT: (0.0, 0.5, -0.5, 1.0, -1.0, 2.0, -2.0, 3.0),  # A, Totband EVCS 1 A, Tesla 2 A
    K.MAX_DISCHARGE: (0.0, 10.0, -10.0, 300.0),  # W, ohne Totband, aber „gleicher Wert“
    K.MIN_SOC: (0.0, 0.5, -0.5, 5.0),  # %, ohne Totband, aber „gleicher Wert“
    K.CHARGE_LIMIT: (0.0, 1.0, -1.0, 10.0),  # %, ohne Totband, aber „gleicher Wert“
}
SETPOINT = (VICTRON, K.GRID_SETPOINT)


@dataclass(frozen=True)
class RefreshCase:
    """Ein Befehl, im selben Kontext ohne und mit ``refresh`` geprüft.

    ``command`` trägt ``urgent`` wie gezogen. Die Probe ist derselbe Befehl ohne ``refresh``, beim
    Netz-Sollwert zudem nicht dringend; das Auffrischen ist derselbe mit ``refresh``. Hat der Befehl
    einen Wert, neben den ein Ist-Wert passt, prüft der Fall beide mit mehreren Ist-Werten
    (``contexts``: gleich, im Totband, am Rand, darüber), damit ``rate.unchanged`` und
    ``rate.deadband`` in jedem Lauf vorkommen. ``recent`` und ``exhausted`` wie bei ``Batch``.
    """

    command: Command
    ctx: SafetyContext
    recent: bool
    exhausted: bool

    def contexts(self) -> list[SafetyContext]:
        """Der Kontext mit mehreren Ist-Werten nahe dem Wert des Befehls; sonst nur er selbst."""
        command, value = self.command, self.command.value
        key, actual = (command.actuator, command.kind), number(value)
        if command.kind == K.CHARGING and isinstance(value, bool):
            actuals = [value, not value]
        elif actual is not None and command.kind in NEAR_OFFSETS:
            actuals = [actual + offset for offset in NEAR_OFFSETS[command.kind]]
        else:
            return [self.ctx]
        return [replace(self.ctx, present={**self.ctx.present, key: near}) for near in actuals]

    def checks(self) -> Checks:
        """Je Kontext die Probe, dann dasselbe als Auffrischen.

        Beim Netz-Sollwert ist die Probe nicht dringend, denn ein Auffrischen ist es nie. Bei den
        anderen Arten bleibt ``urgent``, wie gezogen: ``refresh`` hat dort keine Wirkung.
        """
        urgent = self.command.urgent and self.command.kind != K.GRID_SETPOINT
        plain = replace(self.command, urgent=urgent, refresh=False)
        refreshed = replace(self.command, refresh=True)
        return [(command, ctx) for ctx in self.contexts() for command in (plain, refreshed)]


REFRESH_COMMAND = commands(
    mixed((3, st.just(SETPOINT)), (2, st.sampled_from(PAIRS)), (1, st.sampled_from(STRAYS)))
)


@st.composite
def refresh_cases(draw: st.DrawFn) -> RefreshCase:
    """Ein Befehl, meist ein Netz-Sollwert, in einem Kontext mit meist offenen Gates."""
    command = draw(REFRESH_COMMAND)
    ctx = context(draw, draw(CLOSED_GATE), command.actuator)
    return RefreshCase(command, ctx, draw(HISTORY), draw(HISTORY))


def generous(at: datetime) -> SafetyContext:
    """Regelbetrieb, alles live, ohne Netzbezug (33 A Spielraum je Phase), ohne Ist-Werte."""
    return make_ctx(now=at, phase_import_a=dict.fromkeys(PHASES, 0.0))


PREVIOUS = {  # ein harmloser Befehl derselben Art, der im großzügigen Kontext sendbar ist
    (EVCS, K.CURRENT): Command(EVCS, K.CURRENT, 6.0),
    (EVCS, K.CHARGING): Command(EVCS, K.CHARGING, False),
    (TESLA, K.CURRENT): Command(TESLA, K.CURRENT, 5.0),
    (TESLA, K.CHARGING): Command(TESLA, K.CHARGING, False),
    (TESLA, K.CHARGE_LIMIT): Command(TESLA, K.CHARGE_LIMIT, 80.0),
    (VICTRON, K.GRID_SETPOINT): Command(VICTRON, K.GRID_SETPOINT, None),
}


def authorize_each(
    case: Batch | GateCase | RefreshCase,
) -> list[tuple[Command, SafetyContext, Verdict]]:
    """Prüft jeden Befehl des Falls in seinem Kontext an einer frischen SafetyLayer.

    Jede hat ihr eigenes Schreibbudget, eine eigene Datei im temporären Verzeichnis des Falls. Mit
    ``recent`` und ``exhausted`` kommen auch die Wege über ``DEFERRED``, ``urgent.bypass`` und die
    Ausnahme beim Schreibbudget vor.
    """
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for index, (command, ctx) in enumerate(case.checks()):
            path = Path(tmp) / f"write_budget_{index}.json"
            if case.exhausted:
                path.write_text(json.dumps({"writes": [ctx.now.isoformat()] * PER_HOUR}))
            layer = SafetyLayer(LIMITS, PersistentWriteBudget(PER_HOUR, PER_DAY, path, ctx.now))
            previous = PREVIOUS.get((command.actuator, command.kind))
            if case.recent and previous is not None:
                assert layer.authorize(previous, generous(ctx.now)).sendable
            results.append((command, ctx, layer.authorize(command, ctx)))
    return results


@contextmanager
def fresh_layer(now: datetime) -> Iterator[SafetyLayer]:
    """Eine frische SafetyLayer mit eigenem Schreibbudget in einem temporären Verzeichnis."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "write_budget.json"
        yield SafetyLayer(LIMITS, PersistentWriteBudget(PER_HOUR, PER_DAY, path, now))


# --- Eigenschaften einzelner Befehle --------------------------------------------------------------


@settings(max_examples=500, deadline=None)
@given(batches())
def test_sendable_values_stay_within_hard_limits(batch: Batch) -> None:
    for command, ctx, verdict in authorize_each(batch):
        if verdict.sendable:
            assert in_range(command, verdict.value, ctx.grid_budget_w), (verdict, ctx)
        if verdict.outcome is Outcome.REJECTED:
            assert verdict.value is None


@settings(max_examples=500, deadline=None)
@given(batches())
def test_sendable_increases_keep_phases_below_limit(batch: Batch) -> None:
    for command, ctx, verdict in authorize_each(batch):
        if not verdict.sendable or command.kind not in KINDS.get(command.actuator, ()):
            continue  # nur Aktoren haben Phasen; alles andere ist nie sendbar (Test 1)
        if is_increase(command, verdict.value, ctx):
            currents = predicted(command, verdict.value, ctx)
            assert all(c is not None and c <= PHASE_LIMIT_A + 1e-6 for c in currents), (
                verdict,
                ctx,
            )


@settings(max_examples=500, deadline=None)
@given(gate_cases())
def test_gate_lock_inputs_and_dry_run(case: GateCase) -> None:
    for command, ctx, verdict in authorize_each(case):
        if verdict.sendable:
            assert command.kind in KINDS.get(command.actuator, ()), verdict  # nie an Nicht-Aktoren
            assert not ctx.locks.get(command.actuator), (verdict, ctx)
            # dringend zählt nur, wenn der angeforderte und der gesendete Wert Richtung sicher
            # gehen; ein Auffrischen des Overrides ist nie dringend
            sent = replace(command, value=verdict.value)
            urgent_safe = (
                command.urgent
                and not refreshing(command)
                and safer(command, ctx)
                and safer(sent, ctx)
            )
            assert ctx.operating in (OperatingState.CONTROL, OperatingState.DEGRADED) or (
                urgent_safe and ctx.operating == OperatingState.SAFE
            ), (verdict, ctx)
            assert ctx.inputs.ok or urgent_safe, (verdict, ctx)
        live = ctx.modes.get(command.actuator) is ActuatorMode.LIVE
        assert verdict.send == (verdict.sendable and live), (verdict, ctx)


BAND_CODES = frozenset({"rate.unchanged", "rate.deadband"})  # die Befunde des Totbands


def setpoint_case(*, recent: bool, urgent: bool = False) -> RefreshCase:
    """Ein Netz-Sollwert von 2000 W im großzügigen Kontext: Die Gates sind offen."""
    command = Command(VICTRON, K.GRID_SETPOINT, 2000.0, urgent=urgent)
    return RefreshCase(command, generous(T0), recent=recent, exhausted=False)


def current_case(*, recent: bool, **changes: Any) -> RefreshCase:
    """Ein dringender Strom von 6 A an die EVCS; ab 6 A Ist-Strom geht er Richtung sicher.

    ``changes`` ersetzt Felder des großzügigen Kontexts.
    """
    command = Command(EVCS, K.CURRENT, 6.0, urgent=True)
    return RefreshCase(command, replace(generous(T0), **changes), recent=recent, exhausted=False)


@settings(max_examples=300, deadline=None)
@given(refresh_cases())
# Netz-Sollwert: Totband und gleicher Wert fallen weg, der Wert geht hinaus; der Mindestabstand von
# 2 s gilt weiter; ein Auffrischen ist nie dringend, auch wenn der Befehl es verlangt
@example(setpoint_case(recent=False))
@example(setpoint_case(recent=True))
@example(setpoint_case(recent=False, urgent=True))
# andere Arten: Ein dringender Strom umgeht den Mindestabstand und darf in SAFE senden, auch mit
# refresh, denn dort hat es keine Wirkung
@example(current_case(recent=True))
@example(current_case(recent=False, operating=OperatingState.SAFE))
def test_refresh_differs_from_a_plain_command_only_by_the_deadband(case: RefreshCase) -> None:
    results = authorize_each(case)
    for (_, _, plain), (_, _, refreshed) in zip(results[::2], results[1::2], strict=True):
        if case.command.kind != K.GRID_SETPOINT:
            # Bei jeder anderen Art hat refresh keine Wirkung, auch nicht auf dringende Befehle.
            assert (refreshed.outcome, refreshed.value, refreshed.findings, refreshed.dry_run) == (
                plain.outcome,
                plain.value,
                plain.findings,
                plain.dry_run,
            )
            continue
        codes = [finding.code for finding in plain.findings]
        # Ein Auffrischen ist nie dringend: kein ``urgent.bypass``, ``urgent.ignored`` ist erlaubt.
        got = [finding.code for finding in refreshed.findings if finding.code != "urgent.ignored"]
        assert "urgent.bypass" not in got, refreshed
        if plain.outcome is Outcome.UNCHANGED:
            # Allein das Totband fällt weg; Mindestabstand, Erfassen und Ergebnis laufen weiter.
            assert codes[-1] in BAND_CODES, plain
            assert refreshed.value == plain.value, (plain, refreshed)
            if case.recent:  # eben erst ging ein Befehl durch: der Mindestabstand von 2 s greift
                assert refreshed.outcome is Outcome.DEFERRED, refreshed
                assert got == [*codes[:-1], "rate.interval"], refreshed
            else:
                assert refreshed.outcome in SENDABLE, refreshed
                assert got == codes[:-1], refreshed
        else:  # sonst gelten Sperre, Zustand, Betriebsart, Regeln 1–4 und Mindestabstand wie ohne
            assert (refreshed.outcome, refreshed.value, got) == (plain.outcome, plain.value, codes)


# --- Folgen ---------------------------------------------------------------------------------------
# Lange Folgen (100 bis 200 Schritte), damit sich die Fenster der Stunden- und Tageslimits füllen.

RATE_STEP_S = mixed((2, st.integers(0, 120)), (1, st.integers(0, 6)))  # oft kurz hintereinander
RATE_COMMAND = mixed(
    (2, st.floats(6, 16).map(lambda a: Command(EVCS, K.CURRENT, a))),
    (1, st.booleans().map(lambda on: Command(EVCS, K.CHARGING, on))),
    (3, st.floats(5, 16).map(lambda a: Command(TESLA, K.CURRENT, a))),
    (2, st.booleans().map(lambda on: Command(TESLA, K.CHARGING, on))),
    (1, st.integers(50, 100).map(lambda pct: Command(TESLA, K.CHARGE_LIMIT, pct))),
    (2, st.floats(0, 8_000).map(lambda w: Command(VICTRON, K.GRID_SETPOINT, w))),
    (1, st.floats(0, 8_000).map(lambda w: Command(VICTRON, K.GRID_SETPOINT, w, refresh=True))),
)
BUDGET_COMMAND = mixed(
    (1, st.floats(RESERVE, 100).map(lambda soc: Command(VICTRON, K.MIN_SOC, soc))),
    (1, st.just(-1.0).map(lambda w: Command(VICTRON, K.MAX_DISCHARGE, w))),
    (1, st.floats(0, MAX_DISCHARGE_W).map(lambda w: Command(VICTRON, K.MAX_DISCHARGE, w))),
)


@settings(max_examples=100, deadline=None)
@given(st.lists(st.tuples(RATE_STEP_S, RATE_COMMAND), min_size=100, max_size=200))
def test_rate_limits_hold_over_sequences(steps) -> None:
    sent: list[tuple[float, Command]] = []
    at = 0.0
    with fresh_layer(T0) as layer:
        for step, command in steps:
            at += step
            allowed = rate_allows(command, at, sent)
            verdict = layer.authorize(command, generous(S(at)))
            assert verdict.outcome in (SENDABLE if allowed else {Outcome.DEFERRED}), verdict
            if verdict.sendable:
                sent.append((at, command))
    for key, interval in INTERVAL_S.items():
        times = [t for t, c in sent if (c.actuator, c.kind) == key]
        assert all(later - earlier >= interval for earlier, later in pairwise(times))
    tesla = [t for t, c in sent if c.actuator == TESLA]
    starts_stops = [t for t, c in sent if c.actuator == TESLA and c.kind == K.CHARGING]
    assert fullest_window(tesla, HOUR_S) <= TESLA_LIMITS.per_hour
    assert fullest_window(starts_stops, HOUR_S) <= TESLA_LIMITS.start_stop_per_hour


@settings(max_examples=100, deadline=None)
@given(st.lists(st.tuples(st.integers(0, 600), BUDGET_COMMAND), min_size=100, max_size=200))
def test_budget_holds_over_sequences(steps) -> None:
    sent: list[float] = []
    at = 0.0
    with fresh_layer(T0) as layer:
        for step, command in steps:
            at += step
            allowed = count(sent, at, HOUR_S) < PER_HOUR and count(sent, at, DAY_S) < PER_DAY
            verdict = layer.authorize(command, generous(S(at)))
            assert verdict.outcome in (SENDABLE if allowed else {Outcome.REJECTED}), verdict
            if verdict.sendable:
                sent.append(at)
    assert fullest_window(sent, HOUR_S) <= PER_HOUR
    assert fullest_window(sent, DAY_S) <= PER_DAY
