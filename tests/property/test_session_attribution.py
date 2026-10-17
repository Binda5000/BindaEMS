"""Die Zuordnung Slot → Session über den Index stimmt mit der einfachen Suche über alle Sessions
überein (größte Überdeckung, bei Gleichstand die spätere; offene Sessions enden bei „jetzt“)."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from bindaems.app.charging.sessions import SessionLookup, slot_sessions
from bindaems.app.charging.tracker import StoredSession
from bindaems.shared.timeutil import SLOT

T0 = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)
WALLBOXES = ("twc", "evcs", "go")


def naive_slot_sessions(
    sessions: Sequence[StoredSession], wallbox: str, slot: datetime, now: datetime
) -> StoredSession | None:
    """Der frühere Algorithmus: jede Session für jeden Slot prüfen."""
    slot_end = slot + SLOT
    best: StoredSession | None = None
    best_key: tuple[float, datetime, int] | None = None
    for session in sessions:
        if session.wallbox != wallbox:
            continue
        end = session.end if session.end is not None else now
        overlap = (min(end, slot_end) - max(session.start, slot)).total_seconds()
        if overlap <= 0:
            continue
        key = (overlap, session.start, session.id)
        if best_key is None or key > best_key:
            best, best_key = session, key
    return best


# Minuten-Raster auf wenigen Stunden: Gleichstände und überlappende Sessions sind häufig
_minutes = st.integers(min_value=0, max_value=8 * 60)
_session = st.tuples(
    st.sampled_from(WALLBOXES),
    _minutes,  # Beginn
    st.one_of(st.none(), st.integers(min_value=0, max_value=4 * 60)),  # Dauer, None: offen
)


@given(
    raw=st.lists(_session, max_size=25),
    ids=st.permutations(range(25)),
    now_min=st.integers(min_value=-60, max_value=10 * 60),
    slots=st.lists(st.integers(min_value=-60, max_value=10 * 60), min_size=1, max_size=40),
)
def test_lookup_matches_the_naive_search(
    raw: list[tuple[str, int, int | None]], ids: list[int], now_min: int, slots: list[int]
) -> None:
    sessions = [
        StoredSession(
            ids[index],
            wallbox,
            "tesla",
            T0 + timedelta(minutes=start),
            None if duration is None else T0 + timedelta(minutes=start + duration),
            T0 + timedelta(minutes=start),
        )
        for index, (wallbox, start, duration) in enumerate(raw)
    ]
    now = T0 + timedelta(minutes=now_min)
    lookup = SessionLookup(sessions, now)
    for minute in slots:
        slot = T0 + timedelta(minutes=minute)
        for wallbox in (*WALLBOXES, "other"):
            expected = naive_slot_sessions(sessions, wallbox, slot, now)
            assert lookup.at(wallbox, slot) == expected
            assert slot_sessions(sessions, wallbox, slot, now) == expected
