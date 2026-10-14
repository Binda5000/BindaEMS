"""Phasenwächter: Ziele je Modus, Last je Phase, Verteilung nach Priorität und Überlast (Design 5).

Alle Ströme sind in A je Netzphase. Die Laderegelung setzt den Wächter aus diesen Bausteinen
zusammen:

- ``GuardLimits`` und ``phase_target``: das Ziel je Phase, das der Modus vorgibt (5.2)
- ``phase_loads``: die Last je Phase ohne die eigenen Wallboxen, aber mit der Hilfe des Akkus (5.1)
- ``allocate``: die Zuteilung an die Autos in der Reihenfolge ihrer Priorität (5.3)
- ``OverloadGuard``: die Stufen bei Überlast, gemessen am echten Netzstrom (5.4)
- ``SlidingMin``: das gleitende Minimum für die vorsichtige Zuteilung an den Tesla (5.3)

Ein fehlender Wert macht die Phase voll (5.5): ``None`` heißt unbekannt, ebenso ein Wert, der keine
endliche Zahl ist (``finite_number``). Die Schicht ist rein: Die Zeit kommt als Argument.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Final

from bindaems.core.control.timing import held_since
from bindaems.shared.config import Config, Phase
from bindaems.shared.numbers import finite_number

PHASES: Final[tuple[Phase, Phase, Phase]] = ("L1", "L2", "L3")

MIN_AFTER_S: Final = 3.0  # so lange über dem Sicherungsziel, dann Mindeststrom
EVCS_STOP_AFTER_S: Final = 6.0  # so lange über dem Sicherungsziel, dann stoppt die EVCS
ALL_STOP_AFTER_S: Final = 10.0  # so lange über der Sicherung, dann stoppt alles Laden
CLEAR_AFTER_S: Final = 60.0  # so lange alle Phasen höchstens am Sicherungsziel, dann ist es vorbei


class GuardCause(StrEnum):
    """Woran sich das Ziel eines Autos richtet; der Wert ist der Grund-Code im Entscheidungslog.

    Den Grund ``peak_guard`` nennt auch der Akku-Sollwert, wenn das Budget des 15-min-Wächters den
    Override senkt (Plan 2c-2a, Präzisierung 6).
    """

    IMPORT_LIMIT = "import_limit"  # das Importlimit am Cerbo (Peak Shaving)
    RESERVE_GUARD = "reserve_guard"  # der Schutz der USV-Reserve: wie das Importlimit
    PHASE_LIMIT = "phase_limit"  # die Sicherung
    PEAK_GUARD = "peak_guard"  # der 15-min-Wächter


@dataclass(frozen=True)
class GuardLimits:
    """Die Grenzen des Wächters, abgeleitet aus der Konfiguration (Design 5.2).

    - ``import_target_a``: Ziel am Importlimit, ``grid.import_limit_a − control.import_margin_a``
    - ``fuse_target_a``: Ziel an der Sicherung, ``grid.fuse_a − grid.fuse_margin_a``
    - ``fuse_a``: die Hausanschlusssicherung selbst, ``grid.fuse_a``
    - ``reserve_guard_soc_pct``: ab diesem SOC lädt auch Sofort und Boost bis zur Sicherung,
      ``battery.reserve_soc_pct + control.reserve_guard_pct``
    """

    import_target_a: float
    fuse_target_a: float
    fuse_a: float
    reserve_guard_soc_pct: float

    @classmethod
    def from_config(cls, cfg: Config) -> GuardLimits:
        """Liest die Grenzen aus ``grid``, ``control`` und ``battery`` der Konfiguration."""
        return cls(
            import_target_a=cfg.grid.import_limit_a - cfg.control.import_margin_a,
            fuse_target_a=cfg.grid.fuse_a - cfg.grid.fuse_margin_a,
            fuse_a=cfg.grid.fuse_a,
            reserve_guard_soc_pct=cfg.battery.reserve_soc_pct + cfg.control.reserve_guard_pct,
        )


def phase_target(
    limits: GuardLimits, *, full_power: bool, soc_pct: float | None
) -> tuple[float, GuardCause]:
    """Das Ziel je Phase und der Grund, der es setzt (Design 5.2).

    ``full_power`` steht für Sofort und Boost; PV-Überschuss und Min+PV halten das Importziel.
    Sofort und Boost laden bis zur Sicherung, solange der SOC bekannt und mindestens
    ``reserve_guard_soc_pct`` ist. Darunter bleiben sie am Importziel, damit Peak Shaving den Akku
    nicht unter die USV-Reserve zieht; das gilt auch bei unbekanntem SOC.
    """
    if not full_power:
        return limits.import_target_a, GuardCause.IMPORT_LIMIT
    if soc_pct is not None and soc_pct >= limits.reserve_guard_soc_pct:
        return limits.fuse_target_a, GuardCause.PHASE_LIMIT
    return limits.import_target_a, GuardCause.RESERVE_GUARD


def phase_loads(
    grid_a: Mapping[Phase, float | None],
    battery_a: Mapping[Phase, float | None],
    regulated: Sequence[tuple[tuple[Phase, ...], Mapping[Phase, float] | None]],
    reserved: Mapping[Phase, float] = MappingProxyType({}),
) -> dict[Phase, float | None]:
    """Die Last je Phase: ``I_netz + I_akku − Σ Ströme + Zuschlag`` (Design 5.1, Präzisierung 6).

    So läge die Last ohne die eigenen Wallboxen am Netz, wenn der Akku nicht mithilft. ``grid_a``
    ist der Netzbezug, ``battery_a`` das, was der Multi auf der Phase aus dem Akku liefert.

    - ``regulated``: je geregeltem Ladepunkt seine Phasen und seine Ströme je Netzphase. Die Ströme
      werden auf jeder Phase abgezogen, für die er einen Wert hat. Sind sie unbekannt (``None``),
      sind die Phasen des Ladepunkts voll.
    - ``reserved``: der Zuschlag je Phase, für Ladepunkte, die nicht geregelt werden.

    Eine Phase ist ``None`` (voll), wenn ihr Netzbezug oder ihr Akkustrom fehlt oder ein geregelter
    Ladepunkt mit unbekannten Strömen sie hat. Das Ergebnis nennt immer alle drei Phasen.
    """
    full: set[Phase] = set()
    drawn = dict.fromkeys(PHASES, 0.0)
    for phases, currents in regulated:
        if currents is None:
            full.update(phases)
            continue
        for phase, current in currents.items():
            amps = finite_number(current)
            if amps is None:
                full.add(phase)
            else:
                drawn[phase] += amps

    loads: dict[Phase, float | None] = {}
    for phase in PHASES:
        grid, battery = finite_number(grid_a.get(phase)), finite_number(battery_a.get(phase))
        if phase in full or grid is None or battery is None:
            loads[phase] = None
        else:
            loads[phase] = grid + battery - drawn[phase] + reserved.get(phase, 0.0)
    return loads


@dataclass(frozen=True)
class Claim:
    """Der Anspruch eines Autos an die Verteilung (Design 5.3).

    - ``key``: Schlüssel des Anspruchs, in einer Verteilung eindeutig
    - ``phases``: die Netzphasen, auf denen das Auto zieht
    - ``target_a``: das Ziel je Phase für den Modus des Autos (``phase_target``)
    - ``wanted_a``: der Strom, den das Auto haben möchte
    - ``holding_a``: der Strom, den es gerade zieht; er bleibt für die folgenden Autos belegt
    """

    key: str
    phases: tuple[Phase, ...]
    target_a: float
    wanted_a: float
    holding_a: float = 0.0


@dataclass(frozen=True)
class Grant:
    """Die Zuteilung an einen Anspruch.

    ``free_a`` ist, was das Ziel auf seinen Phasen frei lässt (negativ bei Überschreitung);
    ``None`` heißt, dass eine Last unbekannt ist. ``granted_a`` sind ganze Ampere, nie negativ.
    """

    free_a: float | None
    granted_a: int


def allocate(loads: Mapping[Phase, float | None], claims: Sequence[Claim]) -> dict[str, Grant]:
    """Teilt den Ansprüchen der Reihe nach zu, was ihr Ziel auf ihren Phasen frei lässt.

    Die Reihenfolge von ``claims`` ist die Priorität (Design 5.3, Präzisierung 7). Für jeden
    Anspruch gilt, mit ``used_p`` als dem, was die Ansprüche davor auf der Phase ``p`` belegen:

    - ``free_a = min(target_a − load_p − used_p)`` über seine Phasen
    - ``granted_a = max(0, min(int(wanted_a), floor(free_a)))``
    - danach ``used_p += max(granted_a, holding_a)`` auf seinen Phasen: Eine Senkung, die noch nicht
      wirkt, bleibt so reserviert.

    Ist die Last einer seiner Phasen unbekannt (sie fehlt in ``loads`` oder ist ``None``), ist
    ``free_a`` ``None`` und ``granted_a`` 0; sein ``holding_a`` belegt die Phasen trotzdem. Ein
    Anspruch ohne Phasen bekommt ebenso nichts. Die Schlüssel der Ansprüche müssen eindeutig sein.
    """
    used = dict.fromkeys(PHASES, 0.0)
    grants: dict[str, Grant] = {}
    for claim in claims:
        free = _free_a(loads, claim, used)
        granted = 0 if free is None else max(0, min(int(claim.wanted_a), math.floor(free)))
        grants[claim.key] = Grant(free, granted)
        for phase in claim.phases:
            used[phase] += max(granted, claim.holding_a)
    return grants


def _free_a(
    loads: Mapping[Phase, float | None], claim: Claim, used: Mapping[Phase, float]
) -> float | None:
    """Was das Ziel des Anspruchs auf seinen Phasen frei lässt; ``None`` bei unbekannter Last."""
    rooms: list[float] = []
    for phase in claim.phases:
        load = finite_number(loads.get(phase))
        if load is None:
            return None
        rooms.append(claim.target_a - load - used[phase])
    return min(rooms, default=None)


class OverloadLevel(IntEnum):
    """Die Stufe der Überlast; jede höhere schließt die niedrigere ein (Design 5.4)."""

    NONE = 0  # keine Überlast
    MINIMUM = 1  # EVCS und Tesla auf Mindeststrom
    EVCS_STOP = 2  # dazu stoppt die EVCS
    ALL_STOP = 3  # alles Laden stoppt


class OverloadGuard:
    """Überlast am echten Netzstrom: Stufen nach der Dauer über dem Ziel und über der Sicherung.

    Der Wächter merkt sich drei Zeitpunkte, jeweils seit wann etwas ununterbrochen gilt:

    - irgendeine Phase liegt über ``fuse_target_a``: nach ``MIN_AFTER_S`` die Stufe ``MINIMUM``,
      nach ``EVCS_STOP_AFTER_S`` ``EVCS_STOP``
    - irgendeine Phase liegt über ``fuse_a``: nach ``ALL_STOP_AFTER_S`` ``ALL_STOP``
    - alle Phasen liegen höchstens bei ``fuse_target_a``: nach ``CLEAR_AFTER_S`` ist die Stufe
      wieder ``NONE``

    Die Stufe hält dazwischen. Jeder Zeitpunkt beginnt neu, sobald seine Bedingung nicht mehr gilt.
    Eine unbekannte Phase (``None``, keine endliche Zahl oder nicht in ``grid_a``) liegt weder
    darüber noch darunter (Präzisierung 8): Sie erfüllt weder „irgendeine Phase darüber“ noch „alle
    darunter“. Damit beginnt der Lauf „alle darunter“ neu, und ein Lauf „darüber“ endet, wenn keine
    andere Phase darüber liegt. Nicht für mehrere Threads gedacht.
    """

    def __init__(self, limits: GuardLimits) -> None:
        self._limits = limits
        self._level = OverloadLevel.NONE
        self._over_target_since: datetime | None = None
        self._over_fuse_since: datetime | None = None
        self._calm_since: datetime | None = None

    def update(self, now: datetime, grid_a: Mapping[Phase, float | None]) -> OverloadLevel:
        """Nimmt den Netzstrom je Phase zum Zeitpunkt ``now`` und liefert die Stufe danach.

        ``now`` steigt von Aufruf zu Aufruf (die Uhr der Safety-Schicht). Eine Phase, die in
        ``grid_a`` fehlt, ist unbekannt.
        """
        limits = self._limits
        amps = [finite_number(grid_a.get(phase)) for phase in PHASES]
        known = [a for a in amps if a is not None]
        over_target = any(a > limits.fuse_target_a for a in known)
        over_fuse = any(a > limits.fuse_a for a in known)
        calm = len(known) == len(amps) and all(a <= limits.fuse_target_a for a in known)
        self._over_target_since = held_since(self._over_target_since, over_target, now)
        self._over_fuse_since = held_since(self._over_fuse_since, over_fuse, now)
        self._calm_since = held_since(self._calm_since, calm, now)

        over_target_s = _elapsed_s(self._over_target_since, now)
        if _elapsed_s(self._over_fuse_since, now) >= ALL_STOP_AFTER_S:
            reached = OverloadLevel.ALL_STOP
        elif over_target_s >= EVCS_STOP_AFTER_S:
            reached = OverloadLevel.EVCS_STOP
        elif over_target_s >= MIN_AFTER_S:
            reached = OverloadLevel.MINIMUM
        else:
            reached = OverloadLevel.NONE
        self._level = max(self._level, reached)
        if _elapsed_s(self._calm_since, now) >= CLEAR_AFTER_S:
            self._level = OverloadLevel.NONE  # dann hat keine Phase mehr einen Lauf darüber
        return self._level


def _elapsed_s(since: datetime | None, now: datetime) -> float:
    """Die Sekunden seit ``since``; 0 ohne Zeitpunkt."""
    return 0.0 if since is None else (now - since).total_seconds()


class SlidingMin:
    """Der kleinste Wert der letzten ``window_s`` Sekunden (Design 5.3, Spec 7.3).

    ``add`` und ``value`` bekommen die Zeit der Safety-Schicht; sie steigt von Aufruf zu Aufruf.
    Gemerkt werden nur Werte, die noch das Minimum werden können: Ein neuer Wert verdrängt jeden
    älteren, der nicht kleiner ist, denn der läuft früher ab. Zeiten und Werte steigen darin.
    """

    def __init__(self, window_s: float) -> None:
        self._window_s = window_s
        self._entries: deque[tuple[datetime, float]] = deque()

    def add(self, now: datetime, value: float) -> None:
        """Merkt ``value`` zum Zeitpunkt ``now`` und vergisst, was dann schon zu alt ist."""
        entries = self._entries
        while entries and entries[-1][1] >= value:
            entries.pop()
        entries.append((now, value))
        while entries and not self._within(entries[0][0], now):
            entries.popleft()

    def value(self, now: datetime) -> float | None:
        """Der kleinste Wert mit ``now − t ≤ window_s``; ``None``, wenn es keinen gibt.

        Das Lesen ändert nichts: Die Antwort hängt nur von den Werten und von ``now`` ab.
        """
        for added, value in self._entries:
            if self._within(added, now):
                return value
        return None

    def _within(self, added: datetime, now: datetime) -> bool:
        return (now - added).total_seconds() <= self._window_s
