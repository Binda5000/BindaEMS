"""Die Autos der Laderegelung: die Fahrzeuge der Konfiguration und je Wallbox ein Gast (Design 7).

- ``GUEST``: der Name des Gastes in den Schlüsseln und im Health
- ``guest_key`` und ``guest_wallbox``: der Schlüssel des Gastes einer Wallbox und umgekehrt
- ``fleet``: die Schlüssel aller Autos, die Zuordnung und Laderegelung kennen
- ``CarSpec`` und ``car_spec``: was die Laderegelung von einem Auto braucht, Fahrzeug oder Gast;
  dazu ``GUEST_NAME`` und ``GUEST_MIN_A``

Ein Gast ist ein Auto, das die Konfiguration nicht kennt (Plan 2c-2b, Präzisierung 6). Je Wallbox
gibt es einen, mit dem Schlüssel ``guest@<Wallbox>``; so steht ein Gast nie an zwei Wallboxen. Ein
Fahrzeug der Konfiguration heißt nie ``guest`` oder ``guest@…``, das prüft ``shared.config``. Die
Laderegelung behandelt ihn wie ein Fahrzeug ohne Tessie, das auf allen drei Phasen lädt: an einer
EVCS mit deren Strombereich, am Wall Connector von ``GUEST_MIN_A`` bis zu dessen ``max_a``. Die
Schicht ist rein: Sie liest nur ihre Argumente.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from bindaems.shared.config import Config, EvcsConfig

GUEST: Final = "guest"
GUEST_NAME: Final = "Gast"  # der Name eines Gastes, wie der eines Fahrzeugs der Konfiguration
GUEST_MIN_A: Final = 6  # der Mindeststrom eines Gastes am Wall Connector, der keinen kennt
_PREFIX: Final = f"{GUEST}@"  # so beginnt der Schlüssel eines Gastes


@dataclass(frozen=True)
class CarSpec:
    """Was die Laderegelung von einem Auto braucht (Plan 2c-2b, Präzisierung 6).

    - ``name``: der Name des Fahrzeugs, ``GUEST_NAME`` für einen Gast
    - ``phases``: auf wie vielen Phasen es lädt
    - ``min_a``, ``max_a``: sein Strombereich in A
    - ``tessie``: ob Tessie es kennt; ein Gast nie
    """

    name: str
    phases: int
    min_a: int
    max_a: int
    tessie: bool


def guest_key(wallbox: str) -> str:
    """Der Schlüssel des Gastes der Wallbox ``wallbox``: ``guest@<Wallbox>``."""
    return f"{_PREFIX}{wallbox}"


def guest_wallbox(key: str) -> str | None:
    """Die Wallbox des Gastes mit dem Schlüssel ``key``; ``None``, wenn ``key`` kein Gast ist."""
    return key.removeprefix(_PREFIX) if key.startswith(_PREFIX) else None


def fleet(cfg: Config) -> list[str]:
    """Die Fahrzeuge in der Reihenfolge von ``config.yaml``, dahinter je Wallbox ihr Gast.

    Die Gäste folgen der Reihenfolge der Wallboxen in ``config.yaml``, nicht ``wallbox_priority``.
    """
    return [*cfg.vehicles, *(guest_key(wallbox) for wallbox in cfg.wallboxes)]


def car_spec(cfg: Config, key: str) -> CarSpec:
    """Was die Laderegelung vom Auto ``key`` aus ``fleet`` braucht.

    Für ein Fahrzeug die Werte aus ``vehicles`` der Konfiguration. Ein Gast lädt auf allen drei
    Phasen der ``phase_map`` seiner Wallbox, ohne Tessie: an einer EVCS mit deren ``min_a`` und
    ``max_a``, am Wall Connector von ``GUEST_MIN_A`` bis zu dessen ``max_a``.
    """
    wallbox_key = guest_wallbox(key)
    if wallbox_key is None:
        vehicle = cfg.vehicles[key]
        tessie = vehicle.tessie is not None
        return CarSpec(vehicle.name, vehicle.phases, vehicle.min_a, vehicle.max_a, tessie)
    wallbox = cfg.wallboxes[wallbox_key]
    min_a = wallbox.min_a if isinstance(wallbox, EvcsConfig) else GUEST_MIN_A
    return CarSpec(GUEST_NAME, len(wallbox.phase_map), min_a, wallbox.max_a, tessie=False)
