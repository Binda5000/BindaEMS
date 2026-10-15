"""Indizien der Zuordnungserkennung: wer an einer Wallbox mit Vorgang stehen kann (Design 7.2).

- ``Clue`` und ``Evidence``: welches Indiz es ist und was es an einer Wallbox über ein Fahrzeug sagt
- ``ClueTracker``: wertet die Indizien je Zyklus aus und führt ihre Zeiten fort
- ``home_and_plugged``: ob Tessie ein Fahrzeug „zu Hause und angesteckt“ sieht; darauf stützen
  sich die Indizien ``TESSIE`` und ``ELSEWHERE``

Indizien gibt es je Wallbox mit laufendem Ansteckvorgang und je Fahrzeug der Konfiguration. Jedes
zählt für sich: Es stützt das Fahrzeug an der Wallbox, schließt es dort aus oder sagt nichts.
Unbekannte Werte sprechen für nichts und schließen nichts aus. Der Ausschluss stützt ein
Fahrzeug, das Tessie zu Hause und angesteckt sieht, an der Wallbox, wenn jede andere sicher
leer ist: Ein Auto an einer fremden Ladesäule steht an keiner Wallbox der Anlage. Was aus den
Indizien folgt, entscheidet der nächste Baustein.

**Elimination:** Tessie („zu Hause und angesteckt“) und Home Assistant („zu Hause“) stützen ein
Fahrzeug an einer Wallbox mit Vorgang nur, wenn Stromvergleich oder Phasen-Signatur es an jeder
anderen Wallbox mit Vorgang ausschließen; bei genau einem Vorgang gilt das immer. Ausschließen tun
beide an jeder Wallbox mit Vorgang. Das weicht von Design 7.2 und von Präzisierung 2 ab: „Zu
Hause“ sagt nicht, an welcher Wallbox das Fahrzeug steht. Mit der Phasen-Signatur erkannten sie
sonst einen dreiphasigen Gast an der EVCS als den Tesla, der nicht ladend am Wall Connector steckt,
und einen zweiphasigen Gast am Wall Connector als den e-Golf, der „geladen“ an der EVCS steckt.
Lädt dagegen der e-Golf zweiphasig am Wall Connector und der Tesla dreiphasig an der EVCS,
schließen die Phasen jeden an der Wallbox des anderen aus, und beide sind erkannt. Kosten, falls
die Regel falsch ist: Kommt ein zweiter Vorgang hinzu, fällt eine Erkennung, die auf Tessie oder
Home Assistant beruht, auf „angenommen“ zurück, bis die andere Wallbox das Fahrzeug ausschließt
(``HOLD_S`` Laden dort) und es ``HOLD_S`` Kandidat war, und so lange, wie dort nichts lädt.

Tessie fragt ein abgestecktes Auto nur alle 600 s ab und erfährt vom Anstecken mit Verzug
(Präzisierung 3): Tessie, Stromvergleich und Ausschluss zählen an einer Wallbox mit bekanntem Beginn
des Vorgangs nur mit Werten, die mindestens ``TESSIE_SETTLE_S`` danach abgefragt wurden. Die
zeitliche Korrelation misst dagegen gerade das Intervall zwischen zwei Meldungen von Tessie.

**Ende eines Vorgangs:** Präzisierung 3 gilt auch für Enden. Endet an einer Wallbox ein Vorgang,
stützen Tessie und Ausschluss ein Fahrzeug an einer anderen Wallbox erst wieder mit einem Wert, der
mindestens ``TESSIE_SETTLE_S`` nach diesem Ende abgefragt wurde (``plugged_at``, Wanduhr); ohne
``plugged_at`` stützen sie nach einem gemerkten Ende nicht mehr. Ein Ende ist der Wegfall der
Wallbox aus ``sessions`` oder ein neuer Beginn an ihr; gemerkt wird je Wallbox das letzte. Ein
Ausschluss durch Tessie gilt sofort; Stromvergleich, zeitliche Korrelation und Home Assistant, das
jede Änderung sofort meldet, bleiben, wie sie sind. Grund: Fährt der Tesla vom Wall Connector weg,
meldet Tessie ihn bis zur nächsten Abfrage (alle 60 s, solange er angesteckt nicht lädt) noch zu
Hause und angesteckt. An der EVCS, an der der e-Golf steckt und nicht lädt, stützten ihn dann
Tessie und Ausschluss; nach 60 s wäre er dort erkannt, bekäme ``max_a``, und die EVCS lüde in
seinem Modus. Kosten, falls die Regel falsch ist: Eine Erkennung, die auf Tessie und Ausschluss
beruht, kommt nach dem Ende eines Vorgangs an einer anderen Wallbox erst 60 s nach der ersten
Abfrage, die mindestens ``TESSIE_SETTLE_S`` nach dem Ende liegt; bis dahin bleibt das Fahrzeug
angenommen.

**Stromvergleich:** Meldet Tessie eine andere Phasenzahl als ``phases`` des Fahrzeugs in der
Konfiguration, sagt er nichts; ein unplausibler Wert zählt als unbekannt. „Passt nicht“ zählt an
einer Wallbox erst mit einem Wert, den Tessie mindestens ``TESSIE_SETTLE_S`` nach der letzten
Änderung der Ströme dieser Wallbox abgefragt hat (``plugged_at``, Wanduhr); erst dann beginnen die
``HOLD_S`` seines Ausschlusses. Eine Änderung ist: Eine Phase weicht um mehr als ``MATCH_A`` vom
vorigen Zyklus ab, eine Phase beginnt oder endet zu laden (``PHASE_ON_A``), Laden beginnt oder
endet, die Ströme werden bekannt oder unbekannt, oder es ist der erste beobachtete Zyklus der
Wallbox. „Passt“ zählt wie bisher sofort. Grund: Tessies Ist-Strom hinkt dem Wall Connector 60 s
und mehr nach (Abfrage alle 30 s aus Tessies Cache), und welche Phasenzahl Tessie beim dreiphasigen
Laden meldet, ist an der Anlage nicht geprüft. Ohne die Regeln schloss der Stromvergleich den Tesla
an seinem eigenen Wall Connector aus, wenn Tessie eine Stromänderung der Regelung erst nach 60 s
meldete oder eine falsche Phasenzahl: Dort stand dann ein Gast, 60 s später war die Zuordnung
unklar, und die Befehle an den Tesla pausierten. Kosten, falls die Regeln falsch sind: Ein
Fahrzeug, das nur der Stromvergleich an einer Wallbox ausschließt, ist dort frühestens 60 s nach der
ersten Abfrage ausgeschlossen, die mindestens ``TESSIE_SETTLE_S`` nach der letzten Stromänderung
liegt, und nie, solange sich der Strom öfter ändert; bei falscher Phasenzahl gibt es für das
Fahrzeug keinen Stromvergleich.

Stromvergleich und zeitliche Korrelation sagen nichts über ein Fahrzeug, das Tessie nicht zu Hause
sieht (``at_home`` falsch; unbekannt zählt nicht als woanders): Es steht an keiner Wallbox der
Anlage, und Tessie schließt es dort ohnehin aus. Lädt ein Auto an einer fremden Säule mit demselben
Strom wie ein Gast bei uns, stützten es sonst Strom und Phasen an der Wallbox des Gastes, während
Tessie es ausschließt: ein falscher Widerspruch.

Die 60 s von Stromvergleich und Phasen-Signatur laufen auf der Uhr der Safety-Schicht, die
Zeitstempel von Tessie und die Beginne der Vorgänge auf der Wanduhr (Präzisierung 10). „60 s ohne
Unterbrechung“ (Design 7.3) heißt beobachtete Zeit: Liegen zwischen zwei Aufrufen mehr als
``HOLD_S`` auf der Uhr der Safety-Schicht (die Regelung ruhte), beginnen beide neu, und der erste
Zyklus danach gilt für den Stromvergleich wie der erste beobachtete Zyklus jeder Wallbox: Was sich
in der Pause geändert hat, ist unbekannt. Beginne und Enden der Vorgänge und Tessies Fenster
bleiben. Die Schicht ist rein: Die Zeit kommt als Argument.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from bindaems.core.control.charge_inputs import NO_VEHICLE_DATA, VehicleObs, WallboxObs
from bindaems.core.control.timing import held_since, interrupted, lasted
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

    Der Wert ist sein Name im Health. ``CURRENT`` und ``TIMING`` gibt es nicht für ein Fahrzeug, das
    Tessie nicht zu Hause sieht. ``TESSIE`` und ``HOME`` stützen nur nach der Elimination: wenn
    ``CURRENT`` oder ``PHASES`` das Fahrzeug an jeder anderen Wallbox mit Vorgang ausschließen.
    ``TESSIE`` und ``ELSEWHERE`` stützen nach dem Ende eines Vorgangs an einer anderen Wallbox erst
    mit einem Wert, der ``TESSIE_SETTLE_S`` danach abgefragt wurde.
    """

    TESSIE = "tessie"  # Tessie sieht das Fahrzeug zu Hause und angesteckt, oder sicher nicht
    CURRENT = "current"  # Stromvergleich: Tessies Ist-Strom gegen die Ströme der Wallbox
    TIMING = "timing"  # zeitliche Korrelation: Der Vorgang begann, als das Auto angesteckt wurde
    PHASES = "phases"  # Phasen-Signatur: auf wie vielen Phasen die Wallbox lädt
    HOME = "home"  # Home Assistant meldet das Fahrzeug zu Hause, oder nicht
    ELSEWHERE = "elsewhere"  # Ausschluss: zu Hause und angesteckt, die andere Wallbox leer


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
    Fahrzeug mit Tessie das Fenster, in dem es angesteckt wurde, je Wallbox die Beginne ihrer
    Vorgänge der letzten ``STARTS_KEEP_S``, die Vorgänge des vorigen Zyklus, je Wallbox das letzte
    Ende eines Vorgangs, ihre Beobachtung im vorigen Zyklus und die letzte Änderung ihrer Ströme,
    dazu die Zeit des vorigen Aufrufs. Nicht für mehrere Threads gedacht.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._currents: dict[tuple[str, str, bool], datetime | None] = {}
        self._phases: dict[tuple[str, int], datetime | None] = {}
        self._windows = {
            key: _Window() for key, vehicle in cfg.vehicles.items() if vehicle.tessie is not None
        }
        self._starts: dict[str, list[datetime]] = {key: [] for key in cfg.wallboxes}
        self._running: dict[str, datetime | None] = {}  # die Vorgänge des vorigen Zyklus
        self._ends: dict[str, datetime] = {}  # je Wallbox das letzte Ende auf der Wanduhr
        self._seen: dict[str, WallboxObs] = {}  # je Wallbox die Beobachtung des vorigen Zyklus
        self._changed: dict[str, datetime] = {}  # je Wallbox die letzte Stromänderung, Wanduhr
        self._last: datetime | None = None  # der vorige Aufruf, Uhr der Safety-Schicht

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

        0. Nach einer Pause (mehr als ``HOLD_S`` seit dem vorigen Aufruf) die Zeiten von
           Stromvergleich und Phasen-Signatur und die Beobachtungen der Wallboxen vergessen.
        1. Die Beginne und Enden der Vorgänge und die Änderungen der Ströme merken und das Fenster
           jedes Fahrzeugs mit Tessie fortführen.
        2. Die Zeiten von Stromvergleich und Phasen-Signatur fortführen. Fehlt die Bedingung einen
           Zyklus lang oder ändert sich ihr Wert, beginnen sie neu; ohne Vorgang gilt keine. Für
           ein Fahrzeug, das Tessie nicht zu Hause sieht, gilt kein Stromvergleich, und „passt
           nicht“ zählt erst ``TESSIE_SETTLE_S`` nach der letzten Änderung der Ströme.
        3. Je Wallbox mit Vorgang und je Fahrzeug die Indizien nennen, die etwas sagen: die
           Wallboxen und Fahrzeuge in der Reihenfolge von ``config.yaml``, je Paar die Indizien in
           der von ``Clue``. Die zeitliche Korrelation sagt für ein Fahrzeug, das Tessie nicht zu
           Hause sieht, nichts. Tessie und Home Assistant stützen nur, wenn Stromvergleich oder
           Phasen-Signatur das Fahrzeug an jeder anderen Wallbox mit Vorgang ausschließen
           (Elimination, ``_measured_out`` und ``_home_verdict``); die Zeiten dafür sind in Schritt
           2 schon fortgeführt. Tessie und Ausschluss stützen nach dem Ende eines Vorgangs an einer
           anderen Wallbox erst mit einem Wert, der ``TESSIE_SETTLE_S`` danach abgefragt wurde
           (``_after_ends``).
        """
        if interrupted(self._last, now, HOLD_S):
            self._currents.clear()
            self._phases.clear()
            self._seen.clear()
        self._last = now
        self._remember_starts(wall_now, sessions)
        self._remember_ends(wall_now, sessions)
        self._note_changes(wall_now, wallboxes)
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
                at_home = home_and_plugged(tessie) if _settled(start, tessie) else None
                if at_home is True and not self._after_ends(wallbox_key, tessie):
                    at_home = None  # vor dem Ende eines Vorgangs anderswo abgefragt: stützt nicht
                # Ausschluss: zu Hause und angesteckt, und jede andere Wallbox ist leer
                elsewhere = others_empty and at_home is True
                # Elimination: Strom oder Phasen schließen es an jeder anderen Wallbox aus
                eliminated = all(
                    self._measured_out(now, other, vehicle_key, vehicle.phases)
                    for other in self._cfg.wallboxes
                    if other != wallbox_key and other in sessions
                )
                verdicts = (
                    (Clue.TESSIE, _home_verdict(at_home, eliminated)),
                    (Clue.CURRENT, self._current_verdict(now, wallbox_key, vehicle_key)),
                    (Clue.TIMING, self._timing_verdict(wallbox_key, vehicle_key, start, tessie)),
                    (Clue.PHASES, self._phases_verdict(now, wallbox_key, vehicle.phases)),
                    (Clue.HOME, _home_verdict(homes.get(vehicle_key), eliminated)),
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

    def _remember_ends(self, wall_now: datetime, sessions: Mapping[str, datetime | None]) -> None:
        """Merkt sich je Wallbox das letzte Ende eines Vorgangs, auf der Wanduhr ``wall_now``.

        Ein Vorgang endet, wenn seine Wallbox aus ``sessions`` fällt oder dort ein anderer Beginn
        steht als im vorigen Zyklus.
        """
        for key, start in self._running.items():
            if key not in sessions or sessions[key] != start:
                self._ends[key] = wall_now
        self._running = dict(sessions)

    def _after_ends(self, wallbox_key: str, tessie: VehicleObs) -> bool:
        """Ob Tessies Wert nach jedem gemerkten Ende eines Vorgangs an einer anderen Wallbox zählt.

        Das tut er, wenn seine Abfrage (``plugged_at``) mindestens ``TESSIE_SETTLE_S`` nach dem
        Ende lag; ohne ``plugged_at`` zählt er nach keinem Ende.
        """
        ends = [end for key, end in self._ends.items() if key != wallbox_key]
        if not ends:
            return True
        if tessie.plugged_at is None:
            return False
        return (tessie.plugged_at - max(ends)).total_seconds() >= TESSIE_SETTLE_S

    def _note_changes(self, wall_now: datetime, wallboxes: Mapping[str, WallboxObs]) -> None:
        """Merkt sich je Wallbox die letzte Änderung ihrer Ströme, auf der Wanduhr ``wall_now``.

        Der erste beobachtete Zyklus einer Wallbox ist eine Änderung, sonst ``_currents_changed``.
        """
        for key in self._cfg.wallboxes:
            wallbox, before = wallboxes[key], self._seen.get(key)
            if before is None or _currents_changed(before, wallbox):
                self._changed[key] = wall_now
            self._seen[key] = wallbox

    def _after_change(self, wallbox_key: str, tessie: VehicleObs) -> bool:
        """Ob Tessies Wert mindestens ``TESSIE_SETTLE_S`` nach der letzten Änderung der Ströme der
        Wallbox abgefragt wurde (``plugged_at``); ohne ``plugged_at`` nicht."""
        changed = self._changed.get(wallbox_key)
        if tessie.plugged_at is None or changed is None:
            return False
        return (tessie.plugged_at - changed).total_seconds() >= TESSIE_SETTLE_S

    def _advance(
        self,
        now: datetime,
        sessions: Mapping[str, datetime | None],
        wallboxes: Mapping[str, WallboxObs],
        vehicles: Mapping[str, VehicleObs],
    ) -> None:
        """Führt die Zeiten von Stromvergleich und Phasen-Signatur fort (Präzisierung 2).

        Eine Zeit läuft, solange ihre Bedingung gilt, und nur an einer Wallbox mit Vorgang. Der
        Stromvergleich zählt Tessies Werte nur, wenn sie für den Vorgang gelten (``_settled``), und
        nicht für ein Auto, das Tessie nicht zu Hause sieht oder mit einer anderen Phasenzahl als
        der Konfiguration meldet (``_currents_fit``). „Passt nicht“ zählt erst mit einem Wert, der
        nach der letzten Änderung der Ströme abgefragt wurde (``_after_change``); vorher sagt der
        Vergleich nichts.
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
                fits = _currents_fit(wallbox, tessie, vehicle.phases) if counts else None
                if fits is False and not self._after_change(wallbox_key, tessie):
                    fits = None  # Tessie kennt die neuen Ströme vielleicht noch nicht
                for matches in (True, False):
                    current_key = (wallbox_key, vehicle_key, matches)
                    self._currents[current_key] = held_since(
                        self._currents.get(current_key), fits is matches, now
                    )

    def _measured_out(
        self, now: datetime, wallbox_key: str, vehicle_key: str, vehicle_phases: int
    ) -> bool:
        """Ob Stromvergleich oder Phasen-Signatur das Fahrzeug an der Wallbox ausschließen.

        Nur diese beiden zählen für die Elimination: Die zeitliche Korrelation schließt nie aus,
        und Tessie und Home Assistant schließen an jeder Wallbox gleich aus. Den Stromvergleich gibt
        es nur für ein Fahrzeug mit Tessie.
        """
        return (
            self._current_verdict(now, wallbox_key, vehicle_key) is False
            or self._phases_verdict(now, wallbox_key, vehicle_phases) is False
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
        self, wallbox_key: str, vehicle_key: str, start: datetime | None, tessie: VehicleObs
    ) -> bool | None:
        """Zeitliche Korrelation: ``True``, wenn der Vorgang als einziger im Fenster begann.

        Das Fenster kommt von Tessie (``_Window.bounds``). Ohne beide Grenzen, ohne bekannten Beginn
        des Vorgangs oder mit einem Beginn außerhalb des Fensters gibt es kein Indiz, ebenso wenn in
        derselben Zeit ein Vorgang an einer anderen Wallbox begann, auch ein schon beendeter. Sie
        schließt nie aus. Sieht Tessie das Auto nicht zu Hause, gibt es sie nicht; ist der Ort
        unbekannt, gilt sie wie sonst.
        """
        window = self._windows.get(vehicle_key)
        bounds = window.bounds() if window is not None else None
        if start is None or bounds is None or tessie.at_home is False:
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


def _home_verdict(verdict: bool | None, eliminated: bool) -> bool | None:
    """Was Tessie oder Home Assistant über ein Fahrzeug an einer Wallbox mit Vorgang sagt.

    „Zu Hause (und angesteckt)“ sagt nicht, an welcher Wallbox das Fahrzeug steht. Es stützt das
    Fahrzeug darum nur, wenn ``eliminated`` gilt: Stromvergleich oder Phasen-Signatur schließen es
    an jeder anderen Wallbox mit Vorgang aus; bei genau einem Vorgang gilt das immer. Ein
    Ausschluss gilt an jeder Wallbox mit Vorgang.
    """
    if verdict is True and not eliminated:
        return None
    return verdict


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


def _currents_fit(wallbox: WallboxObs, tessie: VehicleObs, phases: int) -> bool | None:
    """Ob die Ströme von Wallbox und Auto passen (Stromvergleich); ``None``, wenn nicht beide laden.

    Das Auto lädt laut Tessie, wenn es lädt, mindestens ``PHASE_ON_A`` zieht und seine Phasenzahl
    bekannt ist: Tessie meldet „Charging“ schon, bevor Strom fließt. Die Ströme passen, wenn die
    Wallbox auf genau so vielen Phasen wie das Auto mindestens ``PHASE_ON_A`` misst und jeder dieser
    Ströme höchstens ``MATCH_A`` vom Strom des Autos abweicht.

    Auch ``None``, wenn Tessie eine andere Phasenzahl meldet als ``phases`` aus der Konfiguration:
    Der Wert ist unplausibel und zählt als unbekannt.

    Auch ``None``, wenn Tessie das Auto nicht zu Hause sieht: Es lädt woanders, und sein Strom sagt
    nichts über unsere Wallbox. Ist der Ort unbekannt, gilt der Vergleich wie sonst.
    """
    live, amps = _live_currents(wallbox), tessie.actual_current_a
    if live is None or tessie.charging is not True or tessie.phases != phases:
        return None
    if tessie.at_home is False:
        return None
    if amps is None or amps < PHASE_ON_A:
        return None
    return len(live) == tessie.phases and all(abs(current - amps) <= MATCH_A for current in live)


def _currents_changed(before: WallboxObs, now: WallboxObs) -> bool:
    """Ob sich die Ströme der Wallbox seit dem vorigen Zyklus geändert haben (Stromvergleich).

    Das haben sie, wenn Laden beginnt oder endet, die Ströme bekannt oder unbekannt werden, eine
    Phase um mehr als ``MATCH_A`` abweicht oder eine Phase beginnt oder endet zu laden
    (``PHASE_ON_A``).
    """
    if (before.charging is True) != (now.charging is True):
        return True
    if before.phase_a is None or now.phase_a is None:
        return (before.phase_a is None) != (now.phase_a is None)
    return any(
        abs(now.phase_a[phase] - before.phase_a[phase]) > MATCH_A
        or (before.phase_a[phase] >= PHASE_ON_A) != (now.phase_a[phase] >= PHASE_ON_A)
        for phase in PHASES
    )
