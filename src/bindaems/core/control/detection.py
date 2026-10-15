"""Entscheidung der Zuordnungserkennung: wer an einer Wallbox mit Vorgang steht (Design 7.3).

- ``Detection``: was die Erkennung in einem Zyklus entschieden hat
- ``Detector``: wertet die Indizien eines Zyklus aus und führt die 60 s von „erkannt“ und „Gast“
  fort

Die Indizien kommen von ``clues``. Je Wallbox mit Vorgang und je Fahrzeug der Konfiguration folgt
daraus (Präzisierung 4):

- **Kandidat** ist ein Fahrzeug mit mindestens ``CANDIDATE_SUPPORTS`` stützenden Indizien und
  keinem, das es ausschließt.
- **Erkannt** ist es, wenn es ``HOLD_S`` ohne Unterbrechung Kandidat ist und an keiner anderen
  Wallbox Kandidat ist; das Zweite gilt in jedem Zyklus neu. Sind an einer Wallbox zwei Fahrzeuge
  erkannt, gilt keins.
- **Ausgeschlossen** ist es, wenn ein Indiz es ausschließt oder es laut Übersteuerung an einer
  anderen Wallbox steht. Ein **Gast** ist erkannt, wenn jedes Fahrzeug der Konfiguration ``HOLD_S``
  ohne Unterbrechung ausgeschlossen ist.
- Ein **Widerspruch** ist: Ein Fahrzeug ist an zwei Wallboxen Kandidat, oder es ist nirgends
  Kandidat und hat an einer Wallbox mindestens ``CANDIDATE_SUPPORTS`` stützende Indizien und eins,
  das es ausschließt. Das weicht von Design 7.3 ab: Ein Fahrzeug, das an einer Wallbox Kandidat ist,
  widerspricht sich nicht, weil es an einer anderen Stützen und einen Ausschluss hat. Beschlossen
  wurde das, weil Tessie und Phasen-Signatur den Tesla an jeder Wallbox stützten, an der ein Auto
  mit drei Phasen lädt. Seit der Elimination (``clues``) stützen Tessie und Home Assistant ein
  Fahrzeug an einer Wallbox nur, wenn es an jeder anderen ausgeschlossen ist; an der Wallbox, an
  der es Kandidat ist, ist es das nicht. Der Fall kommt darum nur noch vor, wenn an der anderen
  Wallbox die zeitliche Korrelation stützt.

Die Übersteuerung (``placed``) geht vor: An der Wallbox einer Übersteuerung entscheidet die
Erkennung nicht (kein Fahrzeug, kein Gast, kein Kandidat), und die übersteuerten Fahrzeuge sind an
jeder anderen Wallbox ausgeschlossen. Ein übersteuertes Fahrzeug widerspricht sich nicht, und die
Indizien einer übersteuerten Wallbox widersprechen nicht. Sie stehen trotzdem in
``Detection.evidence``.

Die Reihenfolge je Zyklus: erst die Indizien, dann Kandidaten, Ausschlüsse und Widerspruch, zuletzt
die 60 s. Der Widerspruch hängt nicht von den 60 s ab. Ein Ausschluss, der in einem Zyklus zu
zählen beginnt, verhindert in diesem Zyklus schon die Erkennung. Die 60 s beginnen neu, sobald ihre
Bedingung einen Zyklus lang fehlt, und enden mit dem Vorgang. Sie laufen auf der Uhr der
Safety-Schicht (Präzisierung 10). Die Schicht ist rein: Die Zeit kommt als Argument.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from bindaems.core.control.charge_inputs import VehicleObs, WallboxObs
from bindaems.core.control.clues import HOLD_S, ClueTracker, Evidence
from bindaems.core.control.timing import held_since, lasted
from bindaems.shared.config import Config

# so viele stützende Indizien machen ein Fahrzeug an einer Wallbox zum Kandidaten
CANDIDATE_SUPPORTS: Final = 2

_Pair = tuple[str, str]  # Wallbox und Fahrzeug, beide als Schlüssel aus ``config.yaml``


@dataclass(frozen=True)
class Detection:
    """Was die Erkennung in einem Zyklus entschieden hat (Präzisierung 4).

    - ``evidence``: die Indizien des Zyklus (``ClueTracker.update``)
    - ``detected``: Wallbox → das dort erkannte Fahrzeug; eine Wallbox ohne erkanntes Fahrzeug fehlt
    - ``guests``: die Wallboxen, an denen ein Gast erkannt ist
    - ``excluded``: je Wallbox mit Vorgang die Fahrzeuge, die dort jetzt ausgeschlossen sind
    - ``contradiction``: die Indizien widersprechen sich jetzt; „unklar“ wird es erst nach 60 s
    """

    evidence: tuple[Evidence, ...]
    detected: Mapping[str, str]
    guests: frozenset[str]
    excluded: Mapping[str, frozenset[str]]
    contradiction: bool


@dataclass(frozen=True)
class _Reading:
    """Was die Indizien eines Zyklus ergeben, ohne die 60 s.

    - ``deciding``: die Wallboxen mit Vorgang, an denen die Erkennung entscheidet: die nicht
      übersteuerten
    - ``excluded``: je Wallbox mit Vorgang die Fahrzeuge, die dort ausgeschlossen sind
    - ``candidates``: Wallbox und Fahrzeug der Kandidaten, nur an den Wallboxen von ``deciding``
    - ``contradiction``: ein Fahrzeug ohne Übersteuerung widerspricht sich
    """

    deciding: tuple[str, ...]
    excluded: dict[str, frozenset[str]]
    candidates: frozenset[_Pair]
    contradiction: bool


class Detector:
    """Entscheidet je Zyklus, wer an welcher Wallbox steht, und führt die 60 s fort.

    Der Zustand lebt nur im Speicher: der ``ClueTracker``, seit wann ein Fahrzeug an einer Wallbox
    Kandidat ist und seit wann an einer Wallbox jedes Fahrzeug ausgeschlossen ist. Nicht für mehrere
    Threads gedacht.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._tracker = ClueTracker(cfg)
        self._all_vehicles = frozenset(cfg.vehicles)
        self._candidates: dict[_Pair, datetime | None] = {}
        self._guests: dict[str, datetime | None] = {}

    def update(
        self,
        now: datetime,
        wall_now: datetime,
        sessions: Mapping[str, datetime | None],
        wallboxes: Mapping[str, WallboxObs],
        vehicles: Mapping[str, VehicleObs],
        homes: Mapping[str, bool | None],
        placed: Mapping[str, str],
    ) -> Detection:
        """Wertet einen Zyklus aus (Präzisierung 4).

        ``now`` ist die Zeit auf der Uhr der Safety-Schicht, auf ihr laufen die 60 s. ``wall_now``,
        ``sessions``, ``wallboxes``, ``vehicles`` und ``homes`` gehen an ``ClueTracker.update``, das
        sie erklärt. ``placed`` nennt je Fahrzeug die Wallbox seiner gültigen Übersteuerung. Der
        Ablauf:

        1. Die Indizien auswerten (``ClueTracker.update``).
        2. Kandidaten, Ausschlüsse und Widerspruch bestimmen (``_read``); sie hängen nicht von den
           60 s ab.
        3. Die 60 s fortführen: Kandidat je Wallbox und Fahrzeug, alle ausgeschlossen je Wallbox.
           Ohne Vorgang oder an einer übersteuerten Wallbox fehlt die Bedingung, die Zeiten fallen
           weg.
        4. Wer erkannt ist und wo ein Gast steht.
        """
        found = self._tracker.update(now, wall_now, sessions, wallboxes, vehicles, homes)
        reading = _read(self._cfg, found, sessions, placed)
        self._advance(now, reading)
        return Detection(
            evidence=tuple(found),
            detected=self._detected(now, reading),
            guests=frozenset(
                key for key in self._cfg.wallboxes if lasted(self._guests[key], now, HOLD_S)
            ),
            excluded=reading.excluded,
            contradiction=reading.contradiction,
        )

    def _advance(self, now: datetime, reading: _Reading) -> None:
        """Führt die 60 s fort; fehlt die Bedingung einen Zyklus lang, beginnen sie neu."""
        for wallbox_key in self._cfg.wallboxes:
            for vehicle_key in self._cfg.vehicles:
                pair = (wallbox_key, vehicle_key)
                self._candidates[pair] = held_since(
                    self._candidates.get(pair), pair in reading.candidates, now
                )
            all_excluded = (
                wallbox_key in reading.deciding
                and reading.excluded[wallbox_key] == self._all_vehicles
            )
            self._guests[wallbox_key] = held_since(self._guests.get(wallbox_key), all_excluded, now)

    def _detected(self, now: datetime, reading: _Reading) -> dict[str, str]:
        """Wallbox → erkanntes Fahrzeug: ``HOLD_S`` lang Kandidat und an keiner anderen Wallbox.

        Sind an einer Wallbox zwei Fahrzeuge erkannt, gilt keins.
        """
        detected: dict[str, str] = {}
        for wallbox_key in self._cfg.wallboxes:
            found = [
                vehicle_key
                for vehicle_key in self._cfg.vehicles
                if lasted(self._candidates[(wallbox_key, vehicle_key)], now, HOLD_S)
                and not any(
                    (other, vehicle_key) in reading.candidates
                    for other in self._cfg.wallboxes
                    if other != wallbox_key
                )
            ]
            if len(found) == 1:
                detected[wallbox_key] = found[0]
        return detected


def _read(
    cfg: Config,
    found: Sequence[Evidence],
    sessions: Mapping[str, datetime | None],
    placed: Mapping[str, str],
) -> _Reading:
    """Kandidaten, Ausschlüsse und Widerspruch eines Zyklus (Präzisierung 4).

    Der Tracker nennt je Wallbox, Fahrzeug und Indiz höchstens eins, jedes zählt also einmal. Die
    Wallboxen stehen in der Reihenfolge von ``config.yaml``.
    """
    support = Counter((e.wallbox, e.vehicle) for e in found if e.supports)
    denied = frozenset((e.wallbox, e.vehicle) for e in found if not e.supports)
    excluded = {
        wallbox_key: frozenset(
            vehicle_key
            for vehicle_key in cfg.vehicles
            if (wallbox_key, vehicle_key) in denied
            or _placed_elsewhere(placed, vehicle_key, wallbox_key)
        )
        for wallbox_key in cfg.wallboxes
        if wallbox_key in sessions
    }
    pinned = frozenset(placed.values())
    deciding = tuple(key for key in excluded if key not in pinned)
    # ein übersteuertes Fahrzeug ist an diesen Wallboxen ausgeschlossen, also nirgends Kandidat
    candidates = frozenset(
        (wallbox_key, vehicle_key)
        for wallbox_key in deciding
        for vehicle_key in cfg.vehicles
        if vehicle_key not in excluded[wallbox_key]
        and support[(wallbox_key, vehicle_key)] >= CANDIDATE_SUPPORTS
    )
    contradiction = any(
        _contradicts(vehicle_key, deciding, support, denied, candidates)
        for vehicle_key in cfg.vehicles
        if vehicle_key not in placed
    )
    return _Reading(deciding, excluded, candidates, contradiction)


def _placed_elsewhere(placed: Mapping[str, str], vehicle_key: str, wallbox_key: str) -> bool:
    """Ob das Fahrzeug laut Übersteuerung an einer anderen Wallbox steht."""
    return vehicle_key in placed and placed[vehicle_key] != wallbox_key


def _contradicts(
    vehicle_key: str,
    deciding: tuple[str, ...],
    support: Counter[_Pair],
    denied: frozenset[_Pair],
    candidates: frozenset[_Pair],
) -> bool:
    """Ob sich die Indizien zu einem Fahrzeug widersprechen (Präzisierung 4).

    Das tun sie, wenn es an zwei Wallboxen Kandidat ist, oder wenn es nirgends Kandidat ist und an
    einer Wallbox genug Indizien für sich und eins gegen sich hat.
    """
    where = [key for key in deciding if (key, vehicle_key) in candidates]
    if where:
        return len(where) > 1
    return any(
        support[(key, vehicle_key)] >= CANDIDATE_SUPPORTS and (key, vehicle_key) in denied
        for key in deciding
    )
