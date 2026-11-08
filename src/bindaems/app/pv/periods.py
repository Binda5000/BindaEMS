"""Zeiträume der PV-Seite: wie weit der Start fehlende Tage nachholt."""

from __future__ import annotations

from datetime import date
from typing import Final

MAX_FILL_DAYS: Final = 800


def pv_fill_days(today: date) -> int:
    """Tage zurück bis zum 1. Januar des Vorjahres, höchstens ``MAX_FILL_DAYS``."""
    return min((today - date(today.year - 1, 1, 1)).days, MAX_FILL_DAYS)
