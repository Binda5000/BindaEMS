"""Zeitfenster der Unwetter-Reserve: aus amtlichen Warnungen wird je Viertelstunde-Slot eine
Untergrenze für den Akku (Plan Unwetter-Reserve, Entscheidungen 2 bis 4)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final

from bindaems.shared.plan import StormWarning
from bindaems.shared.settings import StormSettings

STORM_LEAD: Final = timedelta(hours=1)  # Vorlauf: der Akku soll vor der Warnung gefüllt sein
SLOT: Final = timedelta(minutes=15)


def relevant(warnings: Sequence[StormWarning], settings: StormSettings) -> list[StormWarning]:
    """Warnungen, die laut Einstellung zählen (Art, Stufe), nach Beginn sortiert; bei
    ausgeschaltetem Schalter keine."""
    if not settings.enabled:
        return []
    return sorted(
        (w for w in warnings if w.kind in settings.kinds and w.level >= settings.min_level),
        key=lambda w: w.begin,
    )


def _overlaps(start: datetime, warning: StormWarning) -> bool:
    return start + SLOT > warning.begin - STORM_LEAD and start < warning.end


def storm_floors(
    starts: Sequence[datetime],
    warnings: Sequence[StormWarning],
    settings: StormSettings,
    *,
    reserve_pct: float,
    soc_max_pct: float,
) -> list[float | None]:
    """Je Slotbeginn die Untergrenze in Prozent oder ``None``. Ein Slot bekommt sie, wenn er
    ``[Beginn - Vorlauf, Ende)`` einer relevanten Warnung schneidet. Das Ziel wird auf den
    höchsten Ladestand begrenzt; liegt es nicht über der Reserve, wirkt es nicht."""
    floor = float(min(settings.target_soc_pct, soc_max_pct))
    active = relevant(warnings, settings)
    if floor <= reserve_pct or not active:
        return [None] * len(starts)
    return [floor if any(_overlaps(s, w) for w in active) else None for s in starts]


def warning_at(start: datetime, warnings: Sequence[StormWarning]) -> StormWarning | None:
    """Die Warnung mit dem spätesten Ende, deren Fenster (mit Vorlauf) den Slot schneidet. Filtert
    nicht nach Einstellungen: Aufrufer übergeben schon die mit ``relevant`` gefilterten."""
    hits = [w for w in warnings if _overlaps(start, w)]
    return max(hits, key=lambda w: w.end, default=None)
