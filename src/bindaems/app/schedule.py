"""Termine in Ortszeit, sicher über die Zeitumstellung hinweg."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from bindaems.shared.timeutil import LOCAL_TZ, ensure_utc

MINUTE = timedelta(minutes=1)


def next_local(
    now: datetime, accept: Callable[[datetime], bool], *, limit_minutes: int = 2880
) -> datetime:
    """Erste volle Minute nach ``now`` (UTC), deren Ortszeit ``accept`` erfüllt.

    Gezählt wird in UTC; so kommt eine doppelte Ortszeit (Herbst) zweimal und eine fehlende
    (Frühjahr) gar nicht vor.
    """
    candidate = ensure_utc(now).replace(second=0, microsecond=0) + MINUTE
    for _ in range(limit_minutes):
        if accept(candidate.astimezone(LOCAL_TZ)):
            return candidate
        candidate += MINUTE
    raise ValueError(f"kein passender Zeitpunkt in den nächsten {limit_minutes} Minuten")
