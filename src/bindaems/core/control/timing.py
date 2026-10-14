"""Zeit-Hilfen, die mehrere Bausteine der Regelung teilen.

- ``held_since``: seit wann eine Bedingung ununterbrochen gilt

Die Schicht ist rein: Die Zeit kommt als Argument.
"""

from __future__ import annotations

from datetime import datetime


def held_since(since: datetime | None, holds: bool, now: datetime) -> datetime | None:
    """Seit wann eine Bedingung ununterbrochen gilt: ``since`` bleibt, solange sie gilt."""
    if not holds:
        return None
    return now if since is None else since
