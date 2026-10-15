"""Zeit-Hilfen, die mehrere Bausteine der Regelung teilen.

- ``held_since``: seit wann eine Bedingung ununterbrochen gilt
- ``lasted``: ob etwas seit einem Zeitpunkt mindestens so lange gilt
- ``interrupted``: ob zwischen zwei Aufrufen mehr Zeit lag als erlaubt

Die Schicht ist rein: Die Zeit kommt als Argument.
"""

from __future__ import annotations

from datetime import datetime


def held_since(since: datetime | None, holds: bool, now: datetime) -> datetime | None:
    """Seit wann eine Bedingung ununterbrochen gilt: ``since`` bleibt, solange sie gilt."""
    if not holds:
        return None
    return now if since is None else since


def lasted(since: datetime | None, now: datetime, limit_s: float) -> bool:
    """Ob etwas seit ``since`` mindestens ``limit_s`` Sekunden gilt; ohne Zeitpunkt nein."""
    return since is not None and (now - since).total_seconds() >= limit_s


def interrupted(last: datetime | None, now: datetime, limit_s: float) -> bool:
    """Ob seit dem vorigen Aufruf ``last`` mehr als ``limit_s`` Sekunden vergangen sind.

    Beim ersten Aufruf (``last`` ist ``None``) nicht.
    """
    return last is not None and (now - last).total_seconds() > limit_s
