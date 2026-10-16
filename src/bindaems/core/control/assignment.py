"""Zuordnung Auto ↔ Wallbox: wer wo steht, aus Ansteckvorgängen, Übersteuerung und Erkennung.

- ``AssignmentKind``, ``VehicleState``, ``WallboxState`` und ``AssignmentState``: was die Regelung
  je Auto und je Wallbox von der Zuordnung bekommt
- ``Assignment``: der Zustand der Zuordnung; ``update`` wertet einen Zyklus aus

Die Autos sind die Fahrzeuge der Konfiguration und je Wallbox ein Gast (``fleet``). Die Zuordnung
zählt die Ansteckvorgänge je Wallbox (Präzisierung 3) samt dem Beginn des laufenden auf der Wanduhr
(Plan 2c-2b, Präzisierung 9) und bindet Boost und Übersteuerung daran (Design 3.1, 8): eine
Übersteuerung an den Vorgang ihrer Wallbox, einen Boost ohne Übersteuerung an den, an dem das
Fahrzeug steht, und steht es an keinem, an den seiner Standard-Wallbox. Wer an einer Wallbox mit
Vorgang steht, entscheidet die Erkennung (``Detector``) aus den Indizien. Solange die Zuordnung klar
ist, steht je Zyklus (Plan 2c-2b, Präzisierung 5):

1. jede Übersteuerung an ihrer Wallbox (``OVERRIDE``)
2. an jeder Wallbox mit Vorgang, in der Reihenfolge von ``config.yaml``, das dort erkannte
   Fahrzeug, sonst ein dort erkannter Gast (``DETECTED``)
3. an jeder übrigen Wallbox mit Vorgang ihr Standardfahrzeug, das erste der Konfiguration mit
   dieser ``default_wallbox``, wenn es dort nicht ausgeschlossen ist und noch an keiner Wallbox
   steht; sonst ihr Gast. Beide sind angenommen (``DEFAULT``).
4. jedes Fahrzeug, das noch nirgends steht, an seiner Standard-Wallbox, wenn dort kein Vorgang
   läuft (``DEFAULT``; angesteckt ist es dann nicht); sonst an keiner. Ein Gast steht sonst
   nirgends.

Meldet die Erkennung ``UNCLEAR_AFTER_S`` lang ohne Unterbrechung einen Widerspruch, ist die
Zuordnung unklar, bis er ``CLEAR_AFTER_S`` lang fehlt; eine aktive Übersteuerung hebt das sofort
auf (Präzisierung 5; Plan 2c-2b, Präzisierung 4). Solange sie unklar ist, gilt alles wie vor der
Erkennung: Jedes Fahrzeug ohne Übersteuerung steht an seiner Standard-Wallbox, sofern keine
Übersteuerung sie belegt, und kein Gast steht an einer Wallbox.

Je Auto an einer Wallbox sagt die Zuordnung, ob es angesteckt ist (Präzisierung 4) und lädt, und
hält „geladen“ bis zum Ende des Vorgangs. Je Wallbox nennt sie, wer dort steht, und die Indizien.

Die Absicht wird beim Laden nicht gegen ``config.yaml`` geprüft und kann aus einem Lauf mit anderer
Konfiguration stammen: Ein Eintrag für ein Fahrzeug, das die Konfiguration nicht kennt, und eine
Übersteuerung auf eine Wallbox, die sie nicht kennt, zählen nicht. Der ``boost_until`` desselben
Eintrags gilt weiter wie ohne Übersteuerung.

Präzisierungen und Design ohne Angabe des Plans sind die von Plan 2c-1. Die Schicht ist rein: Die
Zeit kommt als Argument und steigt von Aufruf zu Aufruf.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from bindaems.core.control.charge_inputs import NO_VEHICLE_DATA, VehicleObs, WallboxObs
from bindaems.core.control.clues import Evidence, home_and_plugged
from bindaems.core.control.detection import Detection, Detector
from bindaems.core.control.fleet import fleet, guest_key
from bindaems.shared.config import Config
from bindaems.shared.control import Intent, VehicleIntent

UNCLEAR_AFTER_S: Final = 60.0  # so lange widersprechen sich die Indizien bis „unklar“
CLEAR_AFTER_S: Final = 60.0  # so lange widersprechen sie sich nicht mehr bis „wieder klar“


class AssignmentKind(StrEnum):
    """Woher der Platz eines Autos kommt; der Wert ist sein Name im Health.

    - ``DETECTED``: erkannt (Plan 2c-2b, Präzisierung 4)
    - ``DEFAULT``: angenommen: das Standardfahrzeug einer Wallbox oder ihr Gast, solange dort
      niemand erkannt ist, und ein Fahrzeug an seiner Standard-Wallbox, an der kein Vorgang läuft
    - ``OVERRIDE``: laut Übersteuerung
    - ``UNCLEAR``: die Standard-Wallbox, solange die Zuordnung unklar ist
    """

    DETECTED = "detected"
    DEFAULT = "default"
    OVERRIDE = "override"
    UNCLEAR = "unclear"


@dataclass(frozen=True)
class VehicleState:
    """Was die Zuordnung zu einem Auto sagt, Fahrzeug oder Gast; ``None`` heißt unbekannt.

    - ``wallbox``: der Schlüssel der Wallbox, an der das Auto steht; ``None``, wenn es nirgends
      steht. Dann hat es keinen Ladepunkt: ``plugged`` und ``charging`` sind ``None``,
      ``complete``, ``new_session`` und ``fresh`` falsch und ``boost_until`` ist ``None``.
    - ``kind``: woher der Platz kommt (``AssignmentKind``). Ein Fahrzeug, das nirgends steht, ist
      ``UNCLEAR``, solange die Zuordnung unklar ist, sonst ``DEFAULT``; ein Gast, der nirgends
      steht, immer ``DEFAULT``.
    - ``plugged``: ob das Auto an der Wallbox angesteckt ist (Präzisierung 4); für einen Gast der
      Wert der Wallbox, wie für ein Fahrzeug ohne Tessie
    - ``charging``: ob das Auto lädt: der Wert der Wallbox. Fehlt er, gilt Tessie wie bei
      ``plugged``: Sieht Tessie das Fahrzeug „zu Hause und angesteckt“, zählt sein Ladezustand;
      sieht Tessie es sicher nicht dort, lädt es nicht; sonst ist es unbekannt.
    - ``complete``: das Auto war in diesem Ansteckvorgang einmal „geladen“ (laut Tessie oder
      laut Wallbox), bis zum Abstecken. Läuft kein Vorgang, etwa bei einem Ausfall der Wallbox,
      zählt, was beide jetzt melden.
    - ``new_session``: an der Wallbox hat in diesem Aufruf ein neuer Ansteckvorgang begonnen
    - ``boost_until``: bis wann der Boost gilt; ``None`` ohne aktiven Boost und für einen Gast
    - ``fresh``: neu erkannt (Plan 2c-2b, Präzisierung 5): Das Auto ist an seiner Wallbox erkannt,
      und im vorigen Aufruf stand dort ein anderes Auto oder keins. Wird ein angenommenes Auto
      bestätigt, ist es nicht neu erkannt.
    - ``boost_pending``, ``override_pending``: Boost bzw. Übersteuerung der Absicht gelten noch,
      sind aber an einen Ansteckvorgang gebunden, der noch nicht begonnen hat (``_bind`` auf den
      nächsten Vorgang). Sie sind nicht aktiv: ``boost_until`` bleibt ``None``, und die
      Übersteuerung wirkt nicht. Endet der Vorgang, an den sie gebunden waren, sind sie weder aktiv
      noch wartend.
    """

    wallbox: str | None
    kind: AssignmentKind
    plugged: bool | None
    charging: bool | None
    complete: bool
    new_session: bool
    boost_until: datetime | None
    fresh: bool = False
    boost_pending: bool = False
    override_pending: bool = False


@dataclass(frozen=True)
class WallboxState:
    """Wer an einer Wallbox steht und warum (Plan 2c-2b, Präzisierungen 5 und 11).

    - ``occupant``: der Schlüssel des Autos aus ``fleet``, das dort steht; stehen dort mehrere,
      das erste nach ``fleet``
    - ``kind``: woher sein Platz kommt (``VehicleState.kind``)
    - ``evidence``: die Indizien des Zyklus an dieser Wallbox, in der Reihenfolge der Erkennung

    Ohne laufenden Vorgang ist sie ``WallboxState(None, None, ())``. Steht an einer Wallbox mit
    Vorgang niemand, was nur bei unklarer Zuordnung vorkommt, ist ``occupant`` ``None`` und
    ``kind`` ``UNCLEAR``.
    """

    occupant: str | None
    kind: AssignmentKind | None
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class AssignmentState:
    """Das Ergebnis eines Aufrufs.

    - ``vehicles``: der Stand je Auto, in der Reihenfolge von ``fleet``: die Fahrzeuge wie in
      ``config.yaml``, dahinter je Wallbox ihr Gast
    - ``unclear``: Die Indizien widersprechen sich (Plan 2c-2b, Präzisierung 4): Die Erkennung
      meldete ``UNCLEAR_AFTER_S`` lang ohne Unterbrechung einen Widerspruch, und er fehlt noch
      nicht ``CLEAR_AFTER_S`` lang. Eine aktive Übersteuerung hebt es sofort auf.
    - ``wallboxes``: wer an jeder Wallbox steht, in der Reihenfolge von ``config.yaml``
    """

    vehicles: Mapping[str, VehicleState]
    unclear: bool
    wallboxes: Mapping[str, WallboxState]


# wo ein Auto steht und woher das kommt: die Wallbox, ``None`` für nirgends, und die Art
_Place = tuple[str | None, AssignmentKind]
_NOWHERE: Final[_Place] = (None, AssignmentKind.DEFAULT)  # ein Gast, der an keiner Wallbox steht


@dataclass
class _Port:
    """Die Ansteckvorgänge einer Wallbox (Präzisierung 3).

    - ``known``: das letzte bekannte „Fahrzeug“ (``True``) oder „kein Fahrzeug“ (``False``);
      ``None``, solange es nie bekannt war. Ein Vorgang läuft, solange es ``True`` ist.
    - ``count``: so viele Vorgänge haben begonnen; der laufende hat diese Nummer
    - ``new``: in diesem Aufruf hat ein neuer Vorgang begonnen, nach bekanntem „kein Fahrzeug“
    - ``started_at``: der Beginn des laufenden Vorgangs auf der Wanduhr (Plan 2c-2b,
      Präzisierung 9), die des Aufrufs, in dem er neu begann; ``None``, wenn er ohne bekanntes
      „kein Fahrzeug“ davor begann
    """

    known: bool | None = None
    count: int = 0
    new: bool = False
    started_at: datetime | None = None

    @property
    def running(self) -> bool:
        """Ob ein Vorgang läuft."""
        return self.known is True

    def observe(self, connected: bool | None, wall_now: datetime) -> None:
        """Wertet den Wert der Wallbox aus: ``connected`` ist „Fahrzeug“, „keins“ oder unbekannt.

        Ein unbekannter Wert ändert nichts. Ein „Fahrzeug“ ohne ein bekanntes „kein Fahrzeug“ davor
        beginnt einen Vorgang, den die Zuordnung nicht als neu meldet: Das Auto kann schon lange
        dort stehen, sein Beginn ist unbekannt. Ein neuer Vorgang beginnt bei ``wall_now``.
        """
        self.new = False
        if connected is None:
            return
        if connected and not self.running:
            self.count += 1
            self.new = self.known is False
            self.started_at = wall_now if self.new else None
        self.known = connected


@dataclass(frozen=True)
class _Binding:
    """Boost und Übersteuerung eines Fahrzeugs, gebunden an einen Ansteckvorgang einer Wallbox.

    ``session`` ist die Nummer des Vorgangs. Sie liegt über der Zahl der begonnenen Vorgänge,
    solange die Bindung auf den nächsten wartet; sie gilt, solange dieser Vorgang läuft. Danach
    ist sie wirkungslos und bleibt es: Die Nummern steigen nur.
    """

    wallbox: str
    session: int


class Assignment:
    """Der Zustand der Zuordnung: Ansteckvorgänge, Bindungen, Erkennung, „geladen“ und „unklar“.

    Der Zustand lebt nur im Speicher, auch der der Erkennung, wer im vorigen Aufruf an jeder
    Wallbox stand (für ``VehicleState.fresh``) und an welchem Vorgang jedes Fahrzeug stand (für
    ``_bind``). ``started`` ist die Wanduhr beim Start der Regelung: Eine Absicht, die davor
    angenommen wurde, stammt aus einem früheren Lauf. Nicht für mehrere Threads gedacht.
    """

    def __init__(self, cfg: Config, *, started: datetime) -> None:
        self._cfg = cfg
        self._started = started
        self._fleet = fleet(cfg)
        self._ports = {key: _Port() for key in cfg.wallboxes}
        self._detector = Detector(cfg)
        self._defaults: dict[str, str] = {}  # Wallbox → ihr Standardfahrzeug
        for key, vehicle in cfg.vehicles.items():
            self._defaults.setdefault(vehicle.default_wallbox, key)
        self._version: int | None = None  # die letzte Version der Absicht, die gebunden wurde
        self._bindings: dict[str, _Binding] = {}
        self._boosts: dict[str, datetime] = {}  # Fahrzeug → ``boost_until`` seiner Bindung
        # Fahrzeug → ``boost_until`` des Boosts, dessen Vorgang endete; höchstens einer je Fahrzeug
        self._spent: dict[str, datetime] = {}
        self._charged: dict[str, tuple[str, int]] = {}  # Auto → (Wallbox, Vorgang), „geladen“
        self._occupants: dict[str, str | None] = {}  # Wallbox → wer im vorigen Aufruf dort stand
        # Fahrzeug → der laufende Vorgang, an dem es im vorigen Aufruf stand; fehlt ohne einen
        self._standing: dict[str, _Binding] = {}
        self._unclear = False
        self._contradiction_since: datetime | None = None
        self._calm_since: datetime | None = None

    def update(
        self,
        now: datetime,
        wall_now: datetime,
        intent: Intent | None,
        accepted_at: datetime | None,
        wallboxes: Mapping[str, WallboxObs],
        vehicles: Mapping[str, VehicleObs],
        homes: Mapping[str, bool | None] = MappingProxyType({}),
    ) -> AssignmentState:
        """Wertet einen Zyklus aus (Präzisierungen 3 bis 5; Plan 2c-2b, Präzisierungen 4, 5, 9).

        ``now`` ist die Zeit auf der Uhr der Safety-Schicht; auf ihr laufen die Zeiten von „unklar“
        und der Erkennung. ``wall_now`` ist die Wanduhr; nach ihr gelten ``boost_until`` und der
        Beginn eines Vorgangs. ``intent`` und ``accepted_at`` sind die gültige Absicht und ihre
        Annahmezeit (``None``, wenn es sie nicht gibt oder die Zeit unbekannt ist). ``wallboxes``
        und ``vehicles`` sind die Beobachtungen, ``homes`` sagt je Fahrzeug mit ``home_entity``, ob
        Home Assistant es zu Hause meldet; alle haben die Schlüssel aus ``config.yaml``. Der Ablauf:

        1. Ansteckvorgänge je Wallbox: Ein neuer beginnt nur mit dem Wechsel von bekannt „kein
           Fahrzeug“ zu „Fahrzeug“, und nur dann ist sein Beginn bekannt. „Kein Fahrzeug“ beendet
           ihn, ein unbekannter Wert ändert nichts.
        2. Bindungen: Ein Boost, dessen Vorgang endete, ist verbraucht (``_note_spent``). Bei einer
           Version, die der Zuordnung neu ist, bindet sie Boost und
           Übersteuerung jedes Fahrzeugs an einen Vorgang (``_bind``): eine Übersteuerung an den
           ihrer Wallbox, den laufenden oder den nächsten; einen Boost ohne Übersteuerung an den,
           an dem das Fahrzeug im vorigen Aufruf stand, wenn er noch läuft, sonst an den seiner
           Standard-Wallbox, den laufenden oder den nächsten. Eine Bindung endet mit ihrem Vorgang.
        3. Übersteuerungen: die gültigen, deren Vorgang läuft (``_active_overrides``).
        4. Erkennung (``Detector.update``) mit jedem laufenden Vorgang und seinem Beginn, auch bei
           unbekanntem Wert der Wallbox, und mit den Übersteuerungen als ``placed``.
        5. Unklar (``_track_unclear``): Ein Widerspruch der Erkennung macht die Zuordnung unklar,
           wenn er ``UNCLEAR_AFTER_S`` lang ohne Unterbrechung besteht, und wieder klar, wenn er
           ``CLEAR_AFTER_S`` lang fehlt. Eine aktive Übersteuerung hebt „unklar“ sofort auf.
        6. Wer wo steht (``_place``), wie im Docstring des Moduls.
        7. Je Wallbox, wer dort steht (``WallboxState``), und je Auto die Werte (``VehicleState``).
           Neu erkannt ist ein Auto, das an seiner Wallbox erkannt ist, wenn dort im vorigen Aufruf
           ein anderes Auto oder keins stand (``_fresh``).
        """
        for key, port in self._ports.items():
            port.observe(wallboxes[key].connected, wall_now)
        self._note_spent()
        if intent is not None and intent.version != self._version:
            self._version = intent.version
            self._bind(intent, accepted_at)

        entries = intent.vehicles if intent is not None else {}
        overrides = self._active_overrides(entries)
        sessions = {key: port.started_at for key, port in self._ports.items() if port.running}
        detection = self._detector.update(
            now, wall_now, sessions, wallboxes, vehicles, homes, overrides
        )
        unclear = self._track_unclear(now, overrides, detection.contradiction)
        places = self._place(overrides, sessions, detection, unclear)
        self._standing = {  # für ``_bind`` im nächsten Aufruf
            key: _Binding(wallbox, self._ports[wallbox].count)
            for key, (wallbox, _) in places.items()
            if wallbox is not None and wallbox in sessions and key in self._cfg.vehicles
        }
        walls = {
            key: self._wallbox_state(key, places, sessions, detection)
            for key in self._cfg.wallboxes
        }
        fresh = self._fresh(walls)

        states: dict[str, VehicleState] = {}
        for key in self._fleet:
            vehicle = self._cfg.vehicles.get(key)  # ``None`` für einen Gast
            tessie = NO_VEHICLE_DATA
            if vehicle is not None and vehicle.tessie is not None:
                tessie = vehicles[key]
            entry = entries.get(key) if vehicle is not None else None  # ein Gast hat keinen Boost
            states[key] = self._vehicle_state(
                key, places.get(key, _NOWHERE), key in fresh, wallboxes, tessie, entry, wall_now
            )
        return AssignmentState(states, unclear, walls)

    def _bind(self, intent: Intent, accepted_at: datetime | None) -> None:
        """Bindet Boost und Übersteuerung einer neuen Version an Ansteckvorgänge (Präzisierung 3).

        Das gilt für jedes Fahrzeug mit ``boost_until`` oder gültiger Übersteuerung. Ohne
        Übersteuerung bindet der Boost an den Vorgang, an dem das Fahrzeug im vorigen Aufruf stand
        (erkannt, angenommen oder an seiner Standard-Wallbox), solange er in diesem Aufruf noch
        läuft. So gilt der Boost für den Tesla, der an der EVCS erkannt ist, dort und springt nicht
        beim nächsten Anstecken am Wall Connector an. Die Zuordnung dieses Aufrufs steht beim Binden
        noch nicht fest.

        Sonst gilt die Wallbox der Übersteuerung, ohne Übersteuerung die Standard-Wallbox: Die
        Bindung geht an ihren laufenden Vorgang, wenn sie ein Fahrzeug meldet, sonst an den
        nächsten. Eine Absicht, die vor dem Start der Regelung angenommen wurde oder deren
        Annahmezeit unbekannt ist, bindet nur an einen laufenden Vorgang: Ein Boost vom Vortag
        springt nicht beim nächsten Anstecken an. Ein verbrauchter Boost (``_note_spent``) bindet
        nicht; eine Übersteuerung desselben Eintrags bindet ohne ihn. Bindungen früherer Versionen
        entfallen; die neue Version setzt sie, wo sie noch gelten sollen.
        """
        restored = accepted_at is None or accepted_at < self._started
        self._bindings.clear()
        self._boosts.clear()
        for key, vehicle in self._cfg.vehicles.items():
            entry = intent.vehicles.get(key)
            override = self._override_wallbox(entry)
            boost = entry.boost_until if entry is not None else None
            if boost is not None and boost == self._spent.get(key):
                boost = None  # verbraucht: kommt mit keiner Version zurück
            if boost is None and override is None:
                continue
            standing = self._standing.get(key)
            if override is None and standing is not None and self._runs(standing):
                binding: _Binding | None = standing
            else:
                wallbox = override if override is not None else vehicle.default_wallbox
                port = self._ports[wallbox]
                binding = None
                if port.running:
                    binding = _Binding(wallbox, port.count)
                elif not restored:
                    binding = _Binding(wallbox, port.count + 1)
            if binding is not None:
                self._bindings[key] = binding
                if boost is not None:
                    self._boosts[key] = boost

    def _note_spent(self) -> None:
        """Merkt sich den Boost jeder Bindung, deren Vorgang begonnen und geendet hat.

        Ein so verbrauchter Boost bindet nicht wieder, auch wenn eine neue Version denselben
        ``boost_until`` trägt (eine Bedienung mit veraltetem Stand, eine Absicht per ``curl``);
        nur ein anderer ``boost_until``, also ein neuer Boost, bindet. Je Fahrzeug bleibt nur der
        zuletzt verbrauchte.
        """
        for key, binding in self._bindings.items():
            boost = self._boosts.get(key)
            started = binding.session <= self._ports[binding.wallbox].count
            if boost is not None and started and not self._runs(binding):
                self._spent[key] = boost

    def _override_wallbox(self, entry: VehicleIntent | None) -> str | None:
        """Die Wallbox der Übersteuerung im Eintrag, wenn die Konfiguration sie kennt."""
        wallbox = entry.wallbox if entry is not None else None
        return wallbox if wallbox in self._cfg.wallboxes else None

    def _bound(self, key: str) -> bool:
        """Ob die Bindung des Fahrzeugs gilt: Ihr Vorgang läuft an ihrer Wallbox."""
        binding = self._bindings.get(key)
        return binding is not None and self._runs(binding)

    def _waiting(self, key: str) -> bool:
        """Ob die Bindung des Fahrzeugs auf einen Vorgang wartet, der noch nicht begonnen hat."""
        binding = self._bindings.get(key)
        return binding is not None and binding.session > self._ports[binding.wallbox].count

    def _runs(self, binding: _Binding) -> bool:
        """Ob der Vorgang ``binding`` an seiner Wallbox noch läuft."""
        port = self._ports[binding.wallbox]
        return port.running and port.count == binding.session

    def _active_overrides(self, entries: Mapping[str, VehicleIntent]) -> dict[str, str]:
        """Fahrzeug → Wallbox für jede gültige Übersteuerung, deren Bindung gilt.

        Ihre Wallbox hat also immer einen laufenden Vorgang.
        """
        overrides: dict[str, str] = {}
        for key in self._cfg.vehicles:
            wallbox = self._override_wallbox(entries.get(key))
            if wallbox is not None and self._bound(key):
                overrides[key] = wallbox
        return overrides

    def _track_unclear(
        self, now: datetime, overrides: Mapping[str, str], contradiction: bool
    ) -> bool:
        """Führt die Zeiten von „unklar“ fort (Präzisierung 5); sagt, ob die Zuordnung unklar ist.

        ``contradiction`` ist der Widerspruch, den die Erkennung in diesem Zyklus meldet. Mit einer
        aktiven Übersteuerung ist die Zuordnung klar, und die Zeiten beginnen neu: Die Bedienung hat
        entschieden, wo die Autos stehen.
        """
        if overrides:
            self._unclear = False
            self._contradiction_since = self._calm_since = None
        elif contradiction:
            self._calm_since = None
            if self._contradiction_since is None:
                self._contradiction_since = now
            if (now - self._contradiction_since).total_seconds() >= UNCLEAR_AFTER_S:
                self._unclear = True
        else:
            self._contradiction_since = None
            if self._calm_since is None:
                self._calm_since = now
            if (now - self._calm_since).total_seconds() >= CLEAR_AFTER_S:
                self._unclear = False
        return self._unclear

    def _place(
        self,
        overrides: Mapping[str, str],
        sessions: Mapping[str, datetime | None],
        detection: Detection,
        unclear: bool,
    ) -> dict[str, _Place]:
        """Wer wo steht: Auto → (Wallbox, Art), nach den Schritten im Docstring des Moduls.

        Die Fahrzeuge der Konfiguration stehen immer darin, mit ``None`` als Wallbox, wenn sie
        nirgends stehen; ein Gast nur, wenn er an einer Wallbox steht. Solange die Zuordnung unklar
        ist, gelten nur die Übersteuerungen (es gibt dann keine: Eine aktive hebt „unklar“ auf) und
        die Standard-Wallboxen, mit ``UNCLEAR``.
        """
        places: dict[str, _Place] = {
            key: (wallbox, AssignmentKind.OVERRIDE) for key, wallbox in overrides.items()
        }
        if not unclear:
            pinned = set(overrides.values())
            open_ = [key for key in sessions if key not in pinned]
            self._place_at_sessions(places, open_, detection)
        # Belegt ist jetzt jede Wallbox einer Übersteuerung und, solange die Zuordnung klar ist,
        # jede mit Vorgang: Frei ist eine Wallbox dann genau, wenn an ihr kein Vorgang läuft.
        taken = {wallbox for wallbox, _ in places.values()}
        kind = AssignmentKind.UNCLEAR if unclear else AssignmentKind.DEFAULT
        for key, vehicle in self._cfg.vehicles.items():
            if key not in places:
                default = vehicle.default_wallbox
                places[key] = (None if default in taken else default, kind)
        return places

    def _place_at_sessions(
        self, places: dict[str, _Place], open_: Sequence[str], detection: Detection
    ) -> None:
        """Wer an den Wallboxen ``open_`` steht, die einen Vorgang und keine Übersteuerung haben.

        Erst je Wallbox das dort erkannte Fahrzeug, sonst ein erkannter Gast (``DETECTED``); dann
        an den übrigen ihr Standardfahrzeug, wenn es dort nicht ausgeschlossen ist und noch an
        keiner Wallbox steht, sonst ihr Gast (beide ``DEFAULT``). Ein Fahrzeug ist das
        Standardfahrzeug höchstens einer Wallbox.
        """
        assumed: list[str] = []
        for wallbox_key in open_:
            detected = detection.detected.get(wallbox_key)
            if detected is not None:
                places[detected] = (wallbox_key, AssignmentKind.DETECTED)
            elif wallbox_key in detection.guests:
                places[guest_key(wallbox_key)] = (wallbox_key, AssignmentKind.DETECTED)
            else:
                assumed.append(wallbox_key)
        for wallbox_key in assumed:
            occupant = self._defaults.get(wallbox_key)
            if (
                occupant is None
                or occupant in places
                or occupant in detection.excluded[wallbox_key]
            ):
                occupant = guest_key(wallbox_key)
            places[occupant] = (wallbox_key, AssignmentKind.DEFAULT)

    def _wallbox_state(
        self,
        key: str,
        places: Mapping[str, _Place],
        sessions: Mapping[str, datetime | None],
        detection: Detection,
    ) -> WallboxState:
        """Wer an der Wallbox ``key`` steht: das erste Auto nach ``fleet``, mit den Indizien.

        Ohne laufenden Vorgang niemand; steht an einer mit Vorgang niemand (nur, solange die
        Zuordnung unklar ist), ist die Art ``UNCLEAR``.
        """
        if key not in sessions:
            return WallboxState(None, None, ())
        evidence = tuple(found for found in detection.evidence if found.wallbox == key)
        occupant = next((car for car in self._fleet if places.get(car, _NOWHERE)[0] == key), None)
        if occupant is None:
            return WallboxState(None, AssignmentKind.UNCLEAR, evidence)
        return WallboxState(occupant, places[occupant][1], evidence)

    def _fresh(self, walls: Mapping[str, WallboxState]) -> set[str]:
        """Die neu erkannten Autos (Plan 2c-2b, Präzisierung 5); merkt sich, wer wo steht.

        Neu erkannt ist ein Auto, das an seiner Wallbox erkannt ist, wenn dort im vorigen Aufruf
        ein anderes Auto oder keins stand, gleich mit welcher Art.
        """
        fresh = {
            state.occupant
            for key, state in walls.items()
            if state.occupant is not None
            and state.kind is AssignmentKind.DETECTED
            and state.occupant != self._occupants.get(key)
        }
        self._occupants = {key: state.occupant for key, state in walls.items()}
        return fresh

    def _vehicle_state(
        self,
        key: str,
        place: _Place,
        fresh: bool,
        wallboxes: Mapping[str, WallboxObs],
        tessie: VehicleObs,
        entry: VehicleIntent | None,
        wall_now: datetime,
    ) -> VehicleState:
        """Die Werte des Autos ``key`` an seinem Platz ``place``.

        ``tessie`` ist ``NO_VEHICLE_DATA`` für einen Gast und ein Fahrzeug ohne Tessie, ``entry``
        der Eintrag des Fahrzeugs in der Absicht; ``None`` für einen Gast.
        """
        wallbox_key, kind = place
        waiting = self._waiting(key)
        boost = self._boosts.get(key)
        boost_pending = waiting and boost is not None and wall_now < boost
        override_pending = waiting and self._override_wallbox(entry) is not None
        if wallbox_key is None:
            return VehicleState(
                None,
                kind,
                None,
                None,
                False,
                False,
                None,
                boost_pending=boost_pending,
                override_pending=override_pending,
            )
        wallbox = wallboxes[wallbox_key]
        return VehicleState(
            wallbox=wallbox_key,
            kind=kind,
            plugged=_plugged(wallbox, tessie),
            charging=_charging(wallbox, tessie),
            complete=self._is_complete(key, wallbox_key, wallbox, tessie),
            new_session=self._ports[wallbox_key].new,
            boost_until=self._boost_until(key, wall_now),
            fresh=fresh,
            boost_pending=boost_pending,
            override_pending=override_pending,
        )

    def _boost_until(self, key: str, wall_now: datetime) -> datetime | None:
        """Der Boost des Fahrzeugs: mit gültiger Bindung, bis ``boost_until`` auf der Wanduhr."""
        boost = self._boosts.get(key)
        if boost is None or not self._bound(key):
            return None
        return boost if wall_now < boost else None

    def _is_complete(
        self, key: str, wallbox_key: str, wallbox: WallboxObs, tessie: VehicleObs
    ) -> bool:
        """Ob das Auto in diesem Ansteckvorgang einmal „geladen“ war.

        Tessie oder die Wallbox meldet es; die Zuordnung hält es, bis der Vorgang endet. Es gehört
        zum Vorgang an der Wallbox, an der das Auto steht: Wechselt die Wallbox, beginnt es neu.

        Läuft an der Wallbox kein Vorgang (sie meldet kein Fahrzeug, oder sie fiel aus, bevor sie
        eins meldete), hält die Zuordnung nichts: Dann zählt, was die Wallbox und Tessie jetzt
        melden. Tessie zählt dabei nur, wenn es das Fahrzeug „zu Hause und angesteckt“ sieht, wie
        bei ``plugged``. Für den Wall Connector, der nie „geladen“ meldet, ist Tessie die einzige
        Quelle.
        """
        port = self._ports[wallbox_key]
        if not port.running:
            tessie_complete = home_and_plugged(tessie) is True and tessie.complete is True
            return wallbox.complete is True or tessie_complete
        session = (wallbox_key, port.count)
        if wallbox.complete is True or tessie.complete is True:
            self._charged[key] = session
        return self._charged.get(key) == session


def _plugged(wallbox: WallboxObs, tessie: VehicleObs) -> bool | None:
    """Ob das Auto an der Wallbox angesteckt ist (Präzisierung 4).

    ``tessie`` ist ``NO_VEHICLE_DATA`` für einen Gast und ein Fahrzeug ohne Tessie; dann zählt nur
    die Wallbox. Meldet die Wallbox ein Fahrzeug, ist es angesteckt. Meldet sie keins, aber Tessie
    sieht das Fahrzeug „zu Hause und angesteckt“, ist es unbekannt; wo es steht, klärt die
    Erkennung. Ohne Wert der Wallbox gilt Tessie.
    """
    if wallbox.connected is True:
        return True
    at_home = home_and_plugged(tessie)
    if wallbox.connected is False:
        return None if at_home is True else False
    return at_home


def _charging(wallbox: WallboxObs, tessie: VehicleObs) -> bool | None:
    """Ob das Auto lädt: der Wert der Wallbox; fehlt er, gilt Tessie wie bei ``plugged``.

    Sieht Tessie das Fahrzeug „zu Hause und angesteckt“, zählt sein Ladezustand. Sieht es das
    Fahrzeug sicher nicht dort (nicht angesteckt oder nicht zu Hause), lädt es dort nicht. Sonst ist
    es unbekannt. ``tessie`` ist ``NO_VEHICLE_DATA`` für einen Gast und ein Fahrzeug ohne Tessie;
    dann zählt nur die Wallbox.
    """
    if wallbox.charging is not None:
        return wallbox.charging
    at_home = home_and_plugged(tessie)
    if at_home is True:
        return tessie.charging
    return at_home
