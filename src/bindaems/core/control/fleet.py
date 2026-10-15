"""Die Autos der Laderegelung: die Fahrzeuge der Konfiguration und je Wallbox ein Gast (Design 7).

- ``GUEST``: der Name des Gastes in den Schlüsseln und im Health
- ``guest_key`` und ``guest_wallbox``: der Schlüssel des Gastes einer Wallbox und umgekehrt
- ``fleet``: die Schlüssel aller Autos, die Zuordnung und Laderegelung kennen

Ein Gast ist ein Auto, das die Konfiguration nicht kennt (Plan 2c-2b, Präzisierung 6). Je Wallbox
gibt es einen, mit dem Schlüssel ``guest@<Wallbox>``; so steht ein Gast nie an zwei Wallboxen. Die
Schicht ist rein: Sie liest nur ihre Argumente.
"""

from __future__ import annotations

from typing import Final

from bindaems.shared.config import Config

GUEST: Final = "guest"
_PREFIX: Final = f"{GUEST}@"  # so beginnt der Schlüssel eines Gastes


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
