"""Zuordnung Auto ↔ Wallbox mit Ansteckvorgängen, Boost und Übersteuerung (Design 3.1, 8).

- ``AssignmentKind``, ``VehicleState`` und ``AssignmentState``: was die Regelung je Fahrzeug von der
  Zuordnung bekommt
- ``Assignment``: der Zustand der Zuordnung; ``update`` wertet einen Zyklus aus

Die Zuordnung zählt die Ansteckvorgänge je Wallbox (Präzisierung 3), bindet Boost und Übersteuerung
daran, sagt je Fahrzeug, ob es angesteckt ist (Präzisierung 4), und meldet „unklar“, wenn die
Hinweise 60 s lang widersprechen (Präzisierung 5). „Geladen“ hält sie bis zum Ende des Vorgangs.

Die Absicht wird beim Laden nicht gegen ``config.yaml`` geprüft und kann aus einem Lauf mit anderer
Konfiguration stammen: Ein Eintrag für ein Fahrzeug, das die Konfiguration nicht kennt, und eine
Übersteuerung auf eine Wallbox, die sie nicht kennt, zählen nicht. Der ``boost_until`` desselben
Eintrags gilt weiter, an der Standard-Wallbox.

Die Schicht ist rein: Die Zeit kommt als Argument und steigt von Aufruf zu Aufruf.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from bindaems.core.control.charge_inputs import NO_VEHICLE_DATA, VehicleObs, WallboxObs
from bindaems.core.control.clues import home_and_plugged
from bindaems.shared.config import Config, EvcsConfig, TwcConfig
from bindaems.shared.control import Intent, VehicleIntent

UNCLEAR_AFTER_S: Final = 60.0  # so lange widersprechen sich die Hinweise bis „unklar“
CLEAR_AFTER_S: Final = 60.0  # so lange widersprechen sie sich nicht mehr bis „wieder klar“


class AssignmentKind(StrEnum):
    """Woher die Wallbox eines Fahrzeugs kommt: Standard, Übersteuerung oder unklar."""

    DEFAULT = "default"
    OVERRIDE = "override"
    UNCLEAR = "unclear"


@dataclass(frozen=True)
class VehicleState:
    """Was die Zuordnung zu einem Fahrzeug sagt; ``None`` heißt unbekannt.

    - ``wallbox``: der Schlüssel der Wallbox, an der das Fahrzeug hängt; ``None``, wenn ein anderes
      Fahrzeug auf seine Standard-Wallbox übersteuert ist. Dann hat es keinen Ladepunkt:
      ``plugged`` und ``charging`` sind ``None``, ``complete`` und ``new_session`` falsch und
      ``boost_until`` ist ``None``.
    - ``kind``: ``OVERRIDE`` mit gültiger Übersteuerung, sonst ``UNCLEAR``, solange die Zuordnung
      unklar ist, sonst ``DEFAULT``
    - ``plugged``: ob das Fahrzeug an der Wallbox angesteckt ist (Präzisierung 4)
    - ``charging``: ob das Fahrzeug lädt: der Wert der Wallbox. Fehlt er, gilt Tessie wie bei
      ``plugged``: Sieht Tessie das Fahrzeug „zu Hause und angesteckt“, zählt sein Ladezustand;
      sieht Tessie es sicher nicht dort, lädt es nicht; sonst ist es unbekannt.
    - ``complete``: das Fahrzeug war in diesem Ansteckvorgang einmal „geladen“ (laut Tessie oder
      laut Wallbox), bis zum Abstecken. Läuft kein Vorgang, etwa bei einem Ausfall der Wallbox,
      zählt, was beide jetzt melden.
    - ``new_session``: an der Wallbox hat in diesem Aufruf ein neuer Ansteckvorgang begonnen
    - ``boost_until``: bis wann der Boost gilt; ``None`` ohne aktiven Boost
    """

    wallbox: str | None
    kind: AssignmentKind
    plugged: bool | None
    charging: bool | None
    complete: bool
    new_session: bool
    boost_until: datetime | None


@dataclass(frozen=True)
class AssignmentState:
    """Das Ergebnis eines Aufrufs.

    - ``vehicles``: der Stand je Fahrzeug, in der Reihenfolge von ``config.yaml``
    - ``unclear``: Wallboxen und Tessie widersprechen sich (Präzisierung 5): Das Bild „Tesla an der
      EVCS“ bestand ``UNCLEAR_AFTER_S`` lang ohne Unterbrechung und fehlt noch nicht
      ``CLEAR_AFTER_S`` lang. Eine aktive Übersteuerung hebt es sofort auf.
    """

    vehicles: Mapping[str, VehicleState]
    unclear: bool


@dataclass
class _Port:
    """Die Ansteckvorgänge einer Wallbox (Präzisierung 3).

    - ``known``: das letzte bekannte „Fahrzeug“ (``True``) oder „kein Fahrzeug“ (``False``);
      ``None``, solange es nie bekannt war. Ein Vorgang läuft, solange es ``True`` ist.
    - ``count``: so viele Vorgänge haben begonnen; der laufende hat diese Nummer
    - ``new``: in diesem Aufruf hat ein neuer Vorgang begonnen, nach bekanntem „kein Fahrzeug“
    """

    known: bool | None = None
    count: int = 0
    new: bool = False

    @property
    def running(self) -> bool:
        """Ob ein Vorgang läuft."""
        return self.known is True

    def observe(self, connected: bool | None) -> None:
        """Wertet den Wert der Wallbox aus: ``connected`` ist „Fahrzeug“, „keins“ oder unbekannt.

        Ein unbekannter Wert ändert nichts. Ein „Fahrzeug“ ohne ein bekanntes „kein Fahrzeug“ davor
        beginnt einen Vorgang, den die Zuordnung nicht als neu meldet: Das Auto kann schon lange
        dort stehen.
        """
        self.new = False
        if connected is None:
            return
        if connected and not self.running:
            self.count += 1
            self.new = self.known is False
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
    """Der Zustand der Zuordnung: Ansteckvorgänge, Bindungen, „geladen“ und der Hinweis „unklar“.

    Der Zustand lebt nur im Speicher. ``started`` ist die Wanduhr beim Start der Regelung: Eine
    Absicht, die davor angenommen wurde, stammt aus einem früheren Lauf. Nicht für mehrere Threads
    gedacht.
    """

    def __init__(self, cfg: Config, *, started: datetime) -> None:
        self._cfg = cfg
        self._started = started
        self._ports = {key: _Port() for key in cfg.wallboxes}
        self._version: int | None = None  # die letzte Version der Absicht, die gebunden wurde
        self._bindings: dict[str, _Binding] = {}
        self._charged: dict[str, tuple[str, int]] = {}  # Fahrzeug → (Wallbox, Vorgang), „geladen“
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
    ) -> AssignmentState:
        """Wertet einen Zyklus aus (Präzisierungen 3 bis 5).

        ``now`` ist die Zeit auf der Uhr der Safety-Schicht; auf ihr laufen die Zeiten von „unklar“.
        ``wall_now`` ist die Wanduhr; nach ihr gilt ``boost_until``. ``intent`` und ``accepted_at``
        sind die gültige Absicht und ihre Annahmezeit (``None``, wenn es sie nicht gibt oder die
        Zeit unbekannt ist). ``wallboxes`` und ``vehicles`` sind die Beobachtungen mit den
        Schlüsseln aus ``config.yaml``. Der Ablauf:

        1. Ansteckvorgänge je Wallbox: Ein neuer beginnt nur mit dem Wechsel von bekannt „kein
           Fahrzeug“ zu „Fahrzeug“. „Kein Fahrzeug“ beendet ihn, ein unbekannter Wert ändert nichts.
        2. Bindungen: Bei einer Version, die der Zuordnung neu ist, bindet sie Boost und
           Übersteuerung jedes Fahrzeugs an den Vorgang seiner Wallbox, den laufenden oder den
           nächsten (``_bind``). Eine Bindung endet mit ihrem Vorgang.
        3. Wallbox je Fahrzeug: mit gültiger Übersteuerung deren Wallbox, sonst die
           Standard-Wallbox. Eine Wallbox, auf die ein Fahrzeug übersteuert ist, fehlt den anderen
           als Standard; sie haben dann keine (``None``).
        4. Unklar: Das Bild „Tesla an der EVCS“ macht die Zuordnung unklar, wenn es
           ``UNCLEAR_AFTER_S`` lang ohne Unterbrechung besteht, und wieder klar, wenn es
           ``CLEAR_AFTER_S`` lang fehlt. Eine aktive Übersteuerung hebt „unklar“ sofort auf. Solange
           es gilt, sind alle Fahrzeuge ohne Übersteuerung ``UNCLEAR``.
        5. Die Werte je Fahrzeug (``VehicleState``).
        """
        for key, port in self._ports.items():
            port.observe(wallboxes[key].connected)
        if intent is not None and intent.version != self._version:
            self._version = intent.version
            self._bind(intent, accepted_at)

        entries = intent.vehicles if intent is not None else {}
        overrides = self._active_overrides(entries)
        assigned = self._assign(overrides)
        unclear = self._track_unclear(now, overrides, assigned, wallboxes, vehicles)

        states: dict[str, VehicleState] = {}
        for key, vehicle in self._cfg.vehicles.items():
            if key in overrides:
                kind = AssignmentKind.OVERRIDE
            else:
                kind = AssignmentKind.UNCLEAR if unclear else AssignmentKind.DEFAULT
            wallbox_key = assigned[key]
            if wallbox_key is None:
                states[key] = VehicleState(None, kind, None, None, False, False, None)
                continue
            wallbox = wallboxes[wallbox_key]
            tessie = vehicles[key] if vehicle.tessie is not None else NO_VEHICLE_DATA
            states[key] = VehicleState(
                wallbox=wallbox_key,
                kind=kind,
                plugged=_plugged(wallbox, tessie),
                charging=_charging(wallbox, tessie),
                complete=self._is_complete(key, wallbox_key, wallbox, tessie),
                new_session=self._ports[wallbox_key].new,
                boost_until=self._boost_until(key, entries.get(key), wall_now),
            )
        return AssignmentState(states, unclear)

    def _bind(self, intent: Intent, accepted_at: datetime | None) -> None:
        """Bindet Boost und Übersteuerung einer neuen Version an Ansteckvorgänge (Präzisierung 3).

        Das gilt für jedes Fahrzeug mit ``boost_until`` oder gültiger Übersteuerung, an die Wallbox
        der Übersteuerung, sonst an die Standard-Wallbox: an den laufenden Vorgang, wenn sie ein
        Fahrzeug meldet, sonst an den nächsten. Eine Absicht, die vor dem Start der Regelung
        angenommen wurde oder deren Annahmezeit unbekannt ist, bindet nur an den laufenden: Ein
        Boost vom Vortag springt nicht beim nächsten Anstecken an. Bindungen früherer Versionen
        entfallen; die neue Version setzt sie, wo sie noch gelten sollen.
        """
        restored = accepted_at is None or accepted_at < self._started
        self._bindings.clear()
        for key, vehicle in self._cfg.vehicles.items():
            entry = intent.vehicles.get(key)
            override = self._override_wallbox(entry)
            if entry is None or (entry.boost_until is None and override is None):
                continue
            wallbox = override if override is not None else vehicle.default_wallbox
            port = self._ports[wallbox]
            if port.running:
                self._bindings[key] = _Binding(wallbox, port.count)
            elif not restored:
                self._bindings[key] = _Binding(wallbox, port.count + 1)

    def _override_wallbox(self, entry: VehicleIntent | None) -> str | None:
        """Die Wallbox der Übersteuerung im Eintrag, wenn die Konfiguration sie kennt."""
        wallbox = entry.wallbox if entry is not None else None
        return wallbox if wallbox in self._cfg.wallboxes else None

    def _bound(self, key: str) -> bool:
        """Ob die Bindung des Fahrzeugs gilt: Ihr Vorgang läuft an ihrer Wallbox."""
        binding = self._bindings.get(key)
        if binding is None:
            return False
        port = self._ports[binding.wallbox]
        return port.running and port.count == binding.session

    def _active_overrides(self, entries: Mapping[str, VehicleIntent]) -> dict[str, str]:
        """Fahrzeug → Wallbox für jede gültige Übersteuerung, deren Bindung gilt."""
        overrides: dict[str, str] = {}
        for key in self._cfg.vehicles:
            wallbox = self._override_wallbox(entries.get(key))
            if wallbox is not None and self._bound(key):
                overrides[key] = wallbox
        return overrides

    def _assign(self, overrides: Mapping[str, str]) -> dict[str, str | None]:
        """Die Wallbox je Fahrzeug: die übersteuerte, sonst die Standard-Wallbox, wenn sie frei ist.

        Eine Standard-Wallbox ist nicht frei, wenn ein Fahrzeug auf sie übersteuert ist.
        """
        taken = set(overrides.values())
        assigned: dict[str, str | None] = {}
        for key, vehicle in self._cfg.vehicles.items():
            if key in overrides:
                assigned[key] = overrides[key]
            elif vehicle.default_wallbox in taken:
                assigned[key] = None
            else:
                assigned[key] = vehicle.default_wallbox
        return assigned

    def _boost_until(
        self, key: str, entry: VehicleIntent | None, wall_now: datetime
    ) -> datetime | None:
        """Der Boost des Fahrzeugs: mit gültiger Bindung, bis ``boost_until`` auf der Wanduhr."""
        if entry is None or entry.boost_until is None or not self._bound(key):
            return None
        return entry.boost_until if wall_now < entry.boost_until else None

    def _is_complete(
        self, key: str, wallbox_key: str, wallbox: WallboxObs, tessie: VehicleObs
    ) -> bool:
        """Ob das Fahrzeug in diesem Ansteckvorgang einmal „geladen“ war.

        Tessie oder die Wallbox meldet es; die Zuordnung hält es, bis der Vorgang endet. Es gehört
        zum Vorgang an der Wallbox, an der das Fahrzeug hängt: Wechselt die Wallbox, beginnt es neu.

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

    def _track_unclear(
        self,
        now: datetime,
        overrides: Mapping[str, str],
        assigned: Mapping[str, str | None],
        wallboxes: Mapping[str, WallboxObs],
        vehicles: Mapping[str, VehicleObs],
    ) -> bool:
        """Führt die Zeiten von „unklar“ fort (Präzisierung 5); sagt, ob die Zuordnung unklar ist.

        Mit einer aktiven Übersteuerung ist sie klar, und die Zeiten beginnen neu: Die Bedienung hat
        entschieden, wo die Autos stehen.
        """
        if overrides:
            self._unclear = False
            self._contradiction_since = self._calm_since = None
        elif self._contradiction(assigned, wallboxes, vehicles):
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

    def _contradiction(
        self,
        assigned: Mapping[str, str | None],
        wallboxes: Mapping[str, WallboxObs],
        vehicles: Mapping[str, VehicleObs],
    ) -> bool:
        """Ob die Hinweise sich widersprechen: das Bild „Tesla an der EVCS“ (Präzisierung 5).

        Ein Fahrzeug mit Tessie, das an einem Wall Connector stehen sollte, ist laut Tessie zu Hause
        und angesteckt. Der Wall Connector meldet aber kein Fahrzeug, und eine EVCS meldet eins.
        Unbekannte Werte widersprechen nie.
        """
        evcs_has_a_car = any(
            isinstance(wallbox, EvcsConfig) and wallboxes[key].connected is True
            for key, wallbox in self._cfg.wallboxes.items()
        )
        if not evcs_has_a_car:
            return False
        for key, vehicle in self._cfg.vehicles.items():
            wallbox_key = assigned[key]
            if (
                vehicle.tessie is not None
                and wallbox_key is not None
                and isinstance(self._cfg.wallboxes[wallbox_key], TwcConfig)
                and wallboxes[wallbox_key].connected is False
                and home_and_plugged(vehicles[key]) is True
            ):
                return True
        return False


def _plugged(wallbox: WallboxObs, tessie: VehicleObs) -> bool | None:
    """Ob das Fahrzeug an der Wallbox angesteckt ist (Präzisierung 4).

    ``tessie`` ist ``NO_VEHICLE_DATA`` für ein Fahrzeug ohne Tessie; dann zählt nur die Wallbox.
    Meldet die Wallbox ein Fahrzeug, ist es angesteckt. Meldet sie keins, aber Tessie sieht das
    Fahrzeug „zu Hause und angesteckt“, ist es unbekannt; der Widerspruch klärt sich über die
    Zuordnung. Ohne Wert der Wallbox gilt Tessie.
    """
    if wallbox.connected is True:
        return True
    at_home = home_and_plugged(tessie)
    if wallbox.connected is False:
        return None if at_home is True else False
    return at_home


def _charging(wallbox: WallboxObs, tessie: VehicleObs) -> bool | None:
    """Ob das Fahrzeug lädt: der Wert der Wallbox; fehlt er, gilt Tessie wie bei ``plugged``.

    Sieht Tessie das Fahrzeug „zu Hause und angesteckt“, zählt sein Ladezustand. Sieht es das
    Fahrzeug sicher nicht dort (nicht angesteckt oder nicht zu Hause), lädt es dort nicht. Sonst ist
    es unbekannt. ``tessie`` ist ``NO_VEHICLE_DATA`` für ein Fahrzeug ohne Tessie; dann zählt nur
    die Wallbox.
    """
    if wallbox.charging is not None:
        return wallbox.charging
    at_home = home_and_plugged(tessie)
    if at_home is True:
        return tessie.charging
    return at_home
