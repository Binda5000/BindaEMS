"""Schnee auf den Modulen erkennen (rein, zustandslos): Kürzungsfaktor je Prognose-Slot.

Eingaben sind Wetter (Schneefall, Schneehöhe), die vollständigen vergangenen Slots mit Ist-Wert
und ungekürzter P50 sowie der Handschalter; es gibt keine Uhr und keinen Zustand (Entscheidungen
3, 4, 5, 9). Eine Kürzung für die ganze Anlage.

Lesarten, wo Brief und Entscheidungen offen lassen:

- Eine Schneefall-Stunde zählt als vergangen, wenn ``hour_start`` ≤ jetzt, sonst als künftig.
- „Beginn der Bedeckung“ (``since``) ist der ``hour_start`` der ersten Ereignisstunde nach dem
  letzten „frei seit“ (Fall a), der erste der drei Slots (Fall b) oder ``at`` (Knopf „covered“).
- Bedeckt nach (a), wenn die letzte vergangene Ereignisstunde später beginnt als „frei seit“;
  „frei seit“ ist das Maximum aus dem Ende des letzten Frei-Paars und ``at`` des Knopfs „free“.
- Fall (b) braucht ``temp_c`` in allen drei Slots (``None`` heißt nicht kalt) und die letzte
  vergangene Schneehöhe (Stunde mit ``hour_start`` ≤ jetzt und Wert).
- Fall (b) gilt, wenn seit dem letzten „frei seit“ kein Schneefall war (nicht nur, wenn das Wetter
  gar keinen zeigt).
- Jede künftige Ereignisstunde bekommt ihr eigenes Fenster bis zum Ende des Tages nach ihrem
  lokalen Tag; überlappende Fenster ergeben den kleineren Faktor.
- Aufeinanderfolgend heißt: Slotbeginne genau 15 min auseinander, beide messbar.
- Der Faktor gilt ab dem laufenden Slot; vergangene Slots bekommen keinen. „Ende des nächsten
  Tages“ ist die lokale Mitternacht am Beginn von (lokales Datum + 2 Tage).
- Median von vier Werten ist der obere Median (``median_high``): Das Beispiel
  0,3 / 0,35 / 0,4 / 0,3 ergibt 0,35, die Kürzung folgt dem Abtauen eher etwas zu spät als zu früh.
- Bedeckt mit genau einem messbaren Slot seit Beginn: Zustand ``vermutet``, Faktor
  ``COVERED_FACTOR`` (``gemessen`` erst ab zwei Slots).
- Ein Override zählt, wenn ``now - at`` höchstens ``OVERRIDE_MAX`` ist; Messwerte zählen nur für
  vollständige, vergangene Slots.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from itertools import pairwise
from statistics import median_high
from typing import Final, Literal

from bindaems.app.forecast.openmeteo import SnowHour
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, ensure_utc, slot_start

SnowState = Literal["frei", "vermutet", "gemessen"]

MEASURE_MIN_SHARE: Final = 0.05
MEASURE_MIN_FLOOR_W: Final = 300.0
SNOW_EVENT_CM: Final = 1.0
SNOW_HOUR_MIN_CM: Final = 0.2
SNOW_WINDOW_H: Final = 12
FREE_RATIO: Final = 0.6
COVERED_RATIO: Final = 0.15
COLD_C: Final = 3.0
DEPTH_MIN_M: Final = 0.01
COVERED_FACTOR: Final = 0.1
RATIO_SLOTS: Final = 4
OVERRIDE_MAX: Final = timedelta(hours=48)
_HOUR: Final = timedelta(hours=1)


@dataclass(frozen=True)
class Observation:
    """Vollständiger vergangener Slot: Ist-Wert, ungekürzte P50 und Lufttemperatur."""

    slot_start: datetime
    expected_w: float
    actual_w: float
    temp_c: float | None


@dataclass(frozen=True)
class Override:
    mode: Literal["covered", "free"]
    at: datetime


@dataclass(frozen=True)
class SnowCut:
    """``factors`` nur für Slots mit Faktor < 1; ``factor`` ist der Faktor jetzt (1 bei frei)."""

    state: SnowState
    factor: float
    since: datetime | None
    factors: Mapping[datetime, float]


NO_CUT: Final = SnowCut("frei", 1.0, None, {})


def measure_min_w(total_kwp: float) -> float:
    return max(MEASURE_MIN_FLOOR_W, round(MEASURE_MIN_SHARE * total_kwp * 1000.0, 6))


def snow_pct(
    factors: Mapping[datetime, float], uncut_p50_w: Mapping[datetime, float], *, total_kwp: float
) -> dict[datetime, float]:
    """Schneeanteil der PV-Prognose in Prozent je gekürztem Slot (``SlotInput.pv_snow_pct``,
    Entscheidung 8); nur, wo die ungekürzte P50 die Messschwelle erreicht, sonst sagt die Kürzung
    nichts. Für den Planungsdienst und den Szenario-Lauf."""
    floor_w = measure_min_w(total_kwp)
    return {
        slot: round(100 * factor)
        for slot, factor in factors.items()
        if uncut_p50_w.get(slot, 0.0) >= floor_w
    }


def _snow_events(weather: Sequence[SnowHour]) -> list[datetime]:
    """Beginne der Stunden mit Schneefall im Sinn von Entscheidung 4."""
    by_start = {ensure_utc(h.hour_start): h.snowfall_cm for h in weather}
    events = []
    for start, cm in by_start.items():
        if cm < SNOW_HOUR_MIN_CM:
            continue
        window = sum(by_start.get(start - i * _HOUR, 0.0) for i in range(SNOW_WINDOW_H))
        if window >= SNOW_EVENT_CM:
            events.append(start)
    return sorted(events)


def _end_of_next_day(t: datetime) -> datetime:
    day = t.astimezone(LOCAL_TZ).date() + timedelta(days=2)
    return datetime.combine(day, time(0), tzinfo=LOCAL_TZ).astimezone(UTC)


def _covered_since(
    events: Sequence[datetime],
    free_since: datetime | None,
    measurable: Sequence[Observation],
    weather: Sequence[SnowHour],
    now: datetime,
) -> datetime | None:
    """Beginn der Bedeckung jetzt oder ``None``, wenn die Anlage frei ist."""
    past = [e for e in events if e <= now]
    if past and (free_since is None or past[-1] > free_since):
        return next(e for e in past if free_since is None or e > free_since)
    # (b) Messung allein: kein Schneefall im Wetter, Schnee am Boden, kalt, drei Slots fast ohne PV
    depths = [
        h.snow_depth_m
        for h in sorted(weather, key=lambda h: h.hour_start)
        if h.hour_start <= now and h.snow_depth_m is not None
    ]
    if not depths or depths[-1] < DEPTH_MIN_M:
        return None
    run: list[Observation] = []
    for o in measurable:
        if free_since is not None and o.slot_start < free_since:
            continue
        low = (
            o.actual_w / o.expected_w < COVERED_RATIO
            and o.temp_c is not None
            and o.temp_c <= COLD_C
        )
        if low and run and o.slot_start - run[-1].slot_start == SLOT:
            run.append(o)
        else:
            run = [o] if low else []
        if len(run) == 3:
            return run[0].slot_start
    return None


def snow_cut(
    now: datetime,
    slots: Sequence[datetime],
    weather: Sequence[SnowHour],
    observations: Sequence[Observation],
    *,
    total_kwp: float,
    override: Override | None = None,
) -> SnowCut:
    now = ensure_utc(now)
    min_w = measure_min_w(total_kwp)
    measurable = sorted(
        (
            o
            for o in observations
            if o.expected_w >= min_w and ensure_utc(o.slot_start) + SLOT <= now
        ),
        key=lambda o: o.slot_start,
    )
    events = _snow_events(weather)
    free_since: datetime | None = None
    for a, b in pairwise(measurable):
        if (
            b.slot_start - a.slot_start == SLOT
            and a.actual_w / a.expected_w >= FREE_RATIO
            and b.actual_w / b.expected_w >= FREE_RATIO
        ):
            free_since = b.slot_start + SLOT
    if override is not None and now - ensure_utc(override.at) <= OVERRIDE_MAX:
        at = ensure_utc(override.at)
        if override.mode == "covered":
            events = sorted([*events, at])
        elif free_since is None or at > free_since:
            free_since = at

    since = _covered_since(events, free_since, measurable, weather, now)
    now_slot = slot_start(now)
    cuts: dict[datetime, float] = {}
    state: SnowState = "frei"
    factor = 1.0
    if since is not None:
        ratios = [o.actual_w / o.expected_w for o in measurable if o.slot_start >= since][
            -RATIO_SLOTS:
        ]
        if len(ratios) >= 2:
            state = "gemessen"
            factor = min(1.0, max(COVERED_FACTOR, median_high(ratios)))
        else:
            state = "vermutet"
            factor = COVERED_FACTOR
        end = _end_of_next_day(now)
        for s in slots:
            if now_slot <= s < end:
                cuts[s] = factor
    for t_n in (e for e in events if e > now):
        end = _end_of_next_day(t_n)
        for s in slots:
            if t_n <= s < end:
                cuts[s] = min(cuts.get(s, 1.0), COVERED_FACTOR)
    factors = {s: f for s, f in cuts.items() if f < 1.0}
    if state == "frei" and not factors:
        return NO_CUT
    return SnowCut(state, factor, since if state != "frei" else None, factors)
