"""Indizien der Zuordnungserkennung: wer an einer Wallbox mit Vorgang stehen kann (Design 7.2).

- ``Clue`` und ``Evidence``: welches Indiz es ist und was es an einer Wallbox über ein Fahrzeug sagt
- ``ClueTracker``: wertet die Indizien je Zyklus aus und führt ihre Zeiten fort
- ``home_and_plugged``: ob Tessie ein Fahrzeug „zu Hause und angesteckt“ sieht

Indizien gibt es je Wallbox mit laufendem Ansteckvorgang und je Fahrzeug der Konfiguration. Jedes
zählt für sich: Es stützt das Fahrzeug an der Wallbox, schließt es dort aus oder sagt nichts.
Unbekannte Werte sprechen für nichts und schließen nichts aus. Was aus den Indizien folgt,
entscheidet der nächste Baustein.

Tessie fragt ein abgestecktes Auto nur alle 600 s ab und erfährt vom Anstecken mit Verzug
(Präzisierung 3): Tessie, Stromvergleich und Ausschluss zählen an einer Wallbox mit bekanntem Beginn
des Vorgangs nur mit Werten, die mindestens ``TESSIE_SETTLE_S`` danach abgefragt wurden. Die
zeitliche Korrelation misst dagegen gerade das Intervall zwischen zwei Meldungen von Tessie.

Die 60 s von Stromvergleich und Phasen-Signatur laufen auf der Uhr der Safety-Schicht, die
Zeitstempel von Tessie und die Beginne der Vorgänge auf der Wanduhr (Präzisierung 10). Die Schicht
ist rein: Die Zeit kommt als Argument.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from bindaems.core.control.charge_inputs import NO_VEHICLE_DATA, VehicleObs, WallboxObs
from bindaems.core.control.timing import held_since, lasted
from bindaems.core.state.derived import PHASES
from bindaems.shared.config import Config

HOLD_S: Final = 60.0  # so lange gelten Stromvergleich und Phasen-Signatur ohne Unterbrechung
MATCH_A: Final = 1.0  # so weit dürfen zwei Ströme höchstens auseinanderliegen und doch passen
PHASE_ON_A: Final = 1.0  # ab diesem Strom lädt eine Phase der Wallbox und lädt das Auto
TESSIE_SETTLE_S: Final = 60.0  # so lange nach dem Beginn eines Vorgangs sagt Tessie noch nichts
STARTS_KEEP_S: Final = 86_400.0  # so lange merkt sich die Erkennung den Beginn eines Vorgangs

_PHASE_COUNTS: Final = tuple(range(1, len(PHASES) + 1))  # auf so vielen Phasen kann etwas laden


class Clue(StrEnum):
    """Welches Indiz es ist, in der Reihenfolge der Tabelle von Design 7.2.

    Der Wert ist sein Name im Health.
    """

    TESSIE = "tessie"  # Tessie sieht das Fahrzeug zu Hause und angesteckt, oder sicher nicht
    CURRENT = "current"  # Stromvergleich: Tessies Ist-Strom gegen die Ströme der Wallbox
    TIMING = "timing"  # zeitliche Korrelation: Der Vorgang begann, als das Auto angesteckt wurde
    PHASES = "phases"  # Phasen-Signatur: auf wie vielen Phasen die Wallbox lädt
    HOME = "home"  # Home Assistant meldet das Fahrzeug zu Hause, oder nicht
    ELSEWHERE = "elsewhere"  # Ausschluss: Die andere Wallbox meldet kein Fahrzeug


@dataclass(frozen=True)
class Evidence:
    """Was ein Indiz an einer Wallbox über ein Fahrzeug sagt.

    ``wallbox`` und ``vehicle`` sind Schlüssel aus ``config.yaml``. ``supports`` ist ``True``, wenn
    das Indiz das Fahrzeug an der Wallbox stützt, und ``False``, wenn es das Fahrzeug dort
    ausschließt.
    """

    wallbox: str
    vehicle: str
    clue: Clue
    supports: bool


def home_and_plugged(tessie: VehicleObs) -> bool | None:
    """Ob Tessie das Fahrzeug „zu Hause und angesteckt“ sieht; ``None``, wenn das offen ist.

    Ist eins von beiden sicher nicht der Fall, ist es das Ganze auch dann nicht, wenn der andere
    Wert fehlt: Ein Auto, das woanders steht, hängt nicht an unserer Wallbox.
    """
    if tessie.plugged is False or tessie.at_home is False:
        return False
    if tessie.plugged is True and tessie.at_home is True:
        return True
    return None


@dataclass
class _Window:
    """Das Fenster, in dem ein Fahrzeug angesteckt wurde, nach den Zeitstempeln von Tessies Werten.

    - ``unplugged``: der Zeitstempel der letzten Meldung „nicht angesteckt“
    - ``plugged``: der der ersten Meldung „angesteckt“ danach; ``None``, solange es sie nicht gibt
    """

    unplugged: datetime | None = None
    plugged: datetime | None = None

    def observe(self, tessie: VehicleObs) -> None:
        """Wertet den Wert aus, den Tessie jetzt meldet.

        Nur ein Wert, der später abgefragt wurde als die letzte Meldung „nicht angesteckt“, ändert
        etwas; derselbe Wert in jedem Zyklus zählt also einmal. Eine neuere Meldung „nicht
        angesteckt“ beginnt das Fenster neu, die erste „angesteckt“ danach schließt es. Ein Wert
        mit früherem Zeitstempel kommt von einer Wanduhr, die zurückgesprungen ist: Er bleibt
        unbeachtet, und ein Fenster, das dadurch rückwärts liefe, entsteht nicht.
        """
        at = tessie.plugged_at
        if at is None or tessie.plugged is None:
            return
        if self.unplugged is not None and at <= self.unplugged:
            return
        if not tessie.plugged:
            self.unplugged, self.plugged = at, None
        elif self.unplugged is not None and self.plugged is None:
            self.plugged = at

    def bounds(self) -> tuple[datetime, datetime] | None:
        """Von wann bis wann das Auto angesteckt wurde; ``None``, solange eine Grenze fehlt.

        Der Anfang zählt ``TESSIE_SETTLE_S`` früher als die letzte Meldung „nicht angesteckt“:
        Tessie erfährt vom Anstecken mit Verzug, die Meldung kann also älter sein als das Anstecken.
        """
        if self.unplugged is None or self.plugged is None:
            return None
        return self.unplugged - timedelta(seconds=TESSIE_SETTLE_S), self.plugged


class ClueTracker:
    """Wertet die Indizien je Zyklus aus und führt ihre Zeiten fort.

    Der Zustand lebt nur im Speicher: seit wann Stromvergleich (je Wallbox und Fahrzeug, mit „passt“
    oder „passt nicht“) und Phasen-Signatur (je Wallbox, mit der Zahl der Phasen) gelten, je
    Fahrzeug mit Tessie das Fenster, in dem es angesteckt wurde, und je Wallbox die Beginne ihrer
    Vorgänge der letzten ``STARTS_KEEP_S``. Nicht für mehrere Threads gedacht.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._currents: dict[tuple[str, str, bool], datetime | None] = {}
        self._phases: dict[tuple[str, int], datetime | None] = {}
        self._windows = {
            key: _Window() for key, vehicle in cfg.vehicles.items() if vehicle.tessie is not None
        }
        self._starts: dict[str, list[datetime]] = {key: [] for key in cfg.wallboxes}

    def update(
        self,
        now: datetime,
        wall_now: datetime,
        sessions: Mapping[str, datetime | None],
        wallboxes: Mapping[str, WallboxObs],
        vehicles: Mapping[str, VehicleObs],
        homes: Mapping[str, bool | None],
    ) -> list[Evidence]:
        """Wertet einen Zyklus aus und liefert die Indizien (Präzisierung 2).

        ``now`` ist die Zeit auf der Uhr der Safety-Schicht; auf ihr laufen die 60 s. ``wall_now``
        ist die Wanduhr, nach der die Beginne alter Vorgänge entfallen. ``sessions`` nennt je
        Wallbox mit laufendem Vorgang seinen Beginn auf der Wanduhr, ``None``, wenn er unbekannt
        ist; eine Wallbox ohne Vorgang fehlt, und für sie gibt es keine Indizien. ``wallboxes`` und
        ``vehicles`` sind die Beobachtungen, ``homes`` sagt je Fahrzeug mit ``home_entity``, ob Home
        Assistant es zu Hause meldet (``None``: unbekannt). Alle haben die Schlüssel aus
        ``config.yaml``. Der Ablauf:

        1. Die Beginne der Vorgänge merken und das Fenster jedes Fahrzeugs mit Tessie fortführen.
        2. Die Zeiten von Stromvergleich und Phasen-Signatur fortführen. Fehlt die Bedingung einen
           Zyklus lang oder ändert sich ihr Wert, beginnen sie neu; ohne Vorgang gilt keine.
        3. Je Wallbox mit Vorgang und je Fahrzeug die Indizien nennen, die etwas sagen: die
           Wallboxen und Fahrzeuge in der Reihenfolge von ``config.yaml``, je Paar die Indizien in
           der von ``Clue``.
        """
        self._remember_starts(wall_now, sessions)
        for key, window in self._windows.items():
            window.observe(vehicles[key])
        self._advance(now, sessions, wallboxes, vehicles)
        found: list[Evidence] = []
        for wallbox_key in self._cfg.wallboxes:
            if wallbox_key not in sessions:
                continue
            start = sessions[wallbox_key]
            others_empty = all(
                wallboxes[other].connected is False
                for other in self._cfg.wallboxes
                if other != wallbox_key
            )
            for vehicle_key, vehicle in self._cfg.vehicles.items():
                tessie = vehicles[vehicle_key] if vehicle.tessie is not None else NO_VEHICLE_DATA
                counts = _settled(start, tessie)
                elsewhere = counts and others_empty and tessie.plugged is True
                verdicts = (
                    (Clue.TESSIE, home_and_plugged(tessie) if counts else None),
                    (Clue.CURRENT, self._current_verdict(now, wallbox_key, vehicle_key)),
                    (Clue.TIMING, self._timing_verdict(wallbox_key, vehicle_key, start)),
                    (Clue.PHASES, self._phases_verdict(now, wallbox_key, vehicle.phases)),
                    (Clue.HOME, homes.get(vehicle_key)),
                    (Clue.ELSEWHERE, True if elsewhere else None),
                )
                found.extend(
                    Evidence(wallbox_key, vehicle_key, clue, supports)
                    for clue, supports in verdicts
                    if supports is not None
                )
        return found

    def _remember_starts(self, wall_now: datetime, sessions: Mapping[str, datetime | None]) -> None:
        """Merkt sich die Beginne der Vorgänge je Wallbox, auch beendeter, für ``STARTS_KEEP_S``.

        Der Beginn eines laufenden Vorgangs entfällt nie: Er gilt, solange der Vorgang läuft, wie
        alt er auch ist. Ein unbekannter Beginn (``None``) wird nicht gemerkt.
        """
        for key, starts in self._starts.items():
            running = sessions.get(key)
            if running is not None and running not in starts:
                starts.append(running)
            starts[:] = [
                start
                for start in starts
                if start == running or (wall_now - start).total_seconds() <= STARTS_KEEP_S
            ]

    def _advance(
        self,
        now: datetime,
        sessions: Mapping[str, datetime | None],
        wallboxes: Mapping[str, WallboxObs],
        vehicles: Mapping[str, VehicleObs],
    ) -> None:
        """Führt die Zeiten von Stromvergleich und Phasen-Signatur fort (Präzisierung 2).

        Eine Zeit läuft, solange ihre Bedingung gilt, und nur an einer Wallbox mit Vorgang. Der
        Stromvergleich zählt Tessies Werte nur, wenn sie für den Vorgang gelten (``_settled``).
        """
        for wallbox_key in self._cfg.wallboxes:
            running = wallbox_key in sessions
            wallbox = wallboxes[wallbox_key]
            counted = _phase_count(wallbox) if running else None
            for count in _PHASE_COUNTS:
                phases_key = (wallbox_key, count)
                self._phases[phases_key] = held_since(
                    self._phases.get(phases_key), counted == count, now
                )
            for vehicle_key, vehicle in self._cfg.vehicles.items():
                if vehicle.tessie is None:
                    continue
                tessie = vehicles[vehicle_key]
                counts = running and _settled(sessions[wallbox_key], tessie)
                fits = _currents_fit(wallbox, tessie) if counts else None
                for matches in (True, False):
                    current_key = (wallbox_key, vehicle_key, matches)
                    self._currents[current_key] = held_since(
                        self._currents.get(current_key), fits is matches, now
                    )

    def _current_verdict(self, now: datetime, wallbox_key: str, vehicle_key: str) -> bool | None:
        """Stromvergleich: ``HOLD_S`` lang passend ist ``True``, so lang nicht passend ``False``.

        Vorher sagt er nichts.
        """
        for matches in (True, False):
            if lasted(self._currents.get((wallbox_key, vehicle_key, matches)), now, HOLD_S):
                return matches
        return None

    def _phases_verdict(self, now: datetime, wallbox_key: str, vehicle_phases: int) -> bool | None:
        """Phasen-Signatur: ``True`` für Fahrzeuge mit der gemessenen Phasenzahl, sonst ``False``.

        Sie gilt nach ``HOLD_S`` mit derselben Zahl; vorher oder ohne Zahl sagt sie nichts.
        """
        for count in _PHASE_COUNTS:
            if lasted(self._phases.get((wallbox_key, count)), now, HOLD_S):
                return vehicle_phases == count
        return None

    def _timing_verdict(
        self, wallbox_key: str, vehicle_key: str, start: datetime | None
    ) -> bool | None:
        """Zeitliche Korrelation: ``True``, wenn der Vorgang als einziger im Fenster begann.

        Das Fenster kommt von Tessie (``_Window.bounds``). Ohne beide Grenzen, ohne bekannten Beginn
        des Vorgangs oder mit einem Beginn außerhalb des Fensters gibt es kein Indiz, ebenso wenn in
        derselben Zeit ein Vorgang an einer anderen Wallbox begann, auch ein schon beendeter. Sie
        schließt nie aus.
        """
        window = self._windows.get(vehicle_key)
        bounds = window.bounds() if window is not None else None
        if start is None or bounds is None:
            return None
        first, last = bounds
        if not first <= start <= last:
            return None
        began_elsewhere = any(
            first <= other <= last
            for key, starts in self._starts.items()
            if key != wallbox_key
            for other in starts
        )
        return None if began_elsewhere else True


def _settled(start: datetime | None, tessie: VehicleObs) -> bool:
    """Ob Tessies Werte für den Vorgang zählen (Präzisierung 3).

    Das tun sie, wenn der Beginn des Vorgangs unbekannt ist (er lief schon beim Start des core) oder
    ihre Abfrage (``plugged_at``) mindestens ``TESSIE_SETTLE_S`` danach lag. Ohne ``plugged_at``
    zählen sie an einer Wallbox mit bekanntem Beginn nie.
    """
    if start is None:
        return True
    if tessie.plugged_at is None:
        return False
    return (tessie.plugged_at - start).total_seconds() >= TESSIE_SETTLE_S


def _live_currents(wallbox: WallboxObs) -> list[float] | None:
    """Die Ströme der Netzphasen, auf denen die ladende Wallbox mindestens ``PHASE_ON_A`` misst.

    ``None``, wenn sie nicht lädt oder ihre Ströme nicht kennt: Dann sagt sie nichts.
    """
    if wallbox.charging is not True or wallbox.phase_a is None:
        return None
    return [amps for amps in wallbox.phase_a.values() if amps >= PHASE_ON_A]


def _phase_count(wallbox: WallboxObs) -> int | None:
    """Auf wie vielen Netzphasen die Wallbox lädt (Phasen-Signatur); ``None``, wenn auf keiner."""
    live = _live_currents(wallbox)
    return len(live) if live else None


def _currents_fit(wallbox: WallboxObs, tessie: VehicleObs) -> bool | None:
    """Ob die Ströme von Wallbox und Auto passen (Stromvergleich); ``None``, wenn nicht beide laden.

    Das Auto lädt laut Tessie, wenn es lädt, mindestens ``PHASE_ON_A`` zieht und seine Phasenzahl
    bekannt ist: Tessie meldet „Charging“ schon, bevor Strom fließt. Die Ströme passen, wenn die
    Wallbox auf genau so vielen Phasen wie das Auto mindestens ``PHASE_ON_A`` misst und jeder dieser
    Ströme höchstens ``MATCH_A`` vom Strom des Autos abweicht.
    """
    live, amps = _live_currents(wallbox), tessie.actual_current_a
    if live is None or tessie.charging is not True or tessie.phases is None:
        return None
    if amps is None or amps < PHASE_ON_A:
        return None
    return len(live) == tessie.phases and all(abs(current - amps) <= MATCH_A for current in live)
