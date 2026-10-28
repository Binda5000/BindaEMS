import asyncio
import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import insert, select
from structlog.testing import capture_logs
from tests.helpers import stop
from tests.unit.app.charging.charging_helpers import T0

from bindaems.app.charging.tracker import (
    ChargingTracker,
    StoredSession,
    Unplugged,
    WallboxSeen,
    observe,
    session_vehicle,
)
from bindaems.app.db.schema import assignment_log, charging_session


def seen(occupant: str | None = None, kind: str | None = None, connected: bool | None = True):
    return [WallboxSeen("evcs", connected, occupant, kind)]


def log_rows(engine) -> list[tuple]:
    with engine.connect() as conn:
        rows = conn.execute(select(assignment_log).order_by(assignment_log.c.id)).all()
    return [(r.ts, r.wallbox, r.occupant, r.kind) for r in rows]


def test_assignment_change_is_logged_once(tracker, engine) -> None:
    tracker.record(T0, seen("polestar", "default"))
    tracker.record(T0 + timedelta(seconds=10), seen("polestar", "default"))
    assert log_rows(engine) == [(T0, "evcs", "polestar", "default")]
    later = T0 + timedelta(minutes=5)
    tracker.record(later, seen("tesla", "detected"))
    assert log_rows(engine) == [
        (T0, "evcs", "polestar", "default"),
        (later, "evcs", "tesla", "detected"),
    ]


def test_first_observation_after_start_is_always_logged(engine, clock, cfg, live) -> None:
    first = ChargingTracker(engine, clock, cfg, live)
    first.record(T0, seen("tesla", "detected"))
    second = ChargingTracker(engine, clock, cfg, live)  # Neustart, dieselbe Belegung
    second.record(T0 + timedelta(minutes=1), seen("tesla", "detected"))
    assert len(log_rows(engine)) == 2


def test_empty_occupant_is_stored_as_empty_text(tracker, engine) -> None:
    tracker.record(T0, seen(None, None))
    assert log_rows(engine) == [(T0, "evcs", "", "")]


def test_observation_without_health_has_no_occupant(live, cfg) -> None:
    live.health = None
    live.state = {"signals": {"wallbox.evcs.status": {"v": 1, "q": "ok"}}}
    result = {s.wallbox: s for s in observe(cfg, live)}
    assert result["evcs"] == WallboxSeen("evcs", True, None, None)
    assert result["twc"] == WallboxSeen("twc", None, None, None)


def test_observation_takes_the_occupant_from_health(live, cfg) -> None:
    live.health = {
        "wallboxes": [
            {"id": "evcs", "occupant": "tesla", "name": "Tesla", "assignment": "detected"},
            {"id": "twc", "occupant": None, "name": None, "assignment": "unclear"},
        ]
    }
    result = {s.wallbox: s for s in observe(cfg, live)}
    assert result["evcs"].occupant == "tesla" and result["evcs"].kind == "detected"
    assert result["twc"].occupant is None and result["twc"].kind == "unclear"


async def test_run_records_on_wake(tracker, live, engine) -> None:
    live.health = {"wallboxes": [{"id": "evcs", "occupant": "tesla", "assignment": "default"}]}
    task = asyncio.create_task(tracker.run())
    try:
        tracker.wake()
        for _ in range(200):
            if log_rows(engine):
                break
            await asyncio.sleep(0.01)
    finally:
        await stop(task)
    assert {(row[1], row[2]) for row in log_rows(engine)} == {("evcs", "tesla"), ("twc", "")}


async def test_run_records_outside_the_event_loop(tracker, live, monkeypatch) -> None:
    threads: list[int] = []
    original = tracker.record

    def record(now, seen_now) -> None:
        threads.append(threading.get_ident())
        original(now, seen_now)

    monkeypatch.setattr(tracker, "record", record)
    task = asyncio.create_task(tracker.run())
    try:
        tracker.wake()
        for _ in range(200):
            if threads:
                break
            await asyncio.sleep(0.01)
    finally:
        await stop(task)
    assert threads and threads[0] != threading.get_ident()  # Worker-Thread, nicht die Schleife


async def test_run_survives_a_failing_observation(tracker, live, engine, monkeypatch) -> None:
    calls: list[int] = []
    original = tracker.record

    def flaky(now, seen_now) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Datenbank gesperrt")
        original(now, seen_now)

    monkeypatch.setattr(tracker, "record", flaky)
    live.health = {"wallboxes": [{"id": "evcs", "occupant": "tesla", "assignment": "default"}]}
    task = asyncio.create_task(tracker.run())
    try:
        with capture_logs() as logs:
            tracker.wake()
            for _ in range(200):
                if calls:
                    break
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            tracker.wake()
            for _ in range(200):
                if log_rows(engine):
                    break
                await asyncio.sleep(0.01)
    finally:
        await stop(task)
    assert any(entry["log_level"] == "error" for entry in logs)
    assert log_rows(engine)


# --- Sessions (Task 4) ---------------------------------------------------------------------


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def plugged(occupant: str | None = None, kind: str | None = None):
    return seen(occupant, kind, True)


def unplugged():
    return seen(None, None, False)


def unknown():
    return seen(None, None, None)


def hold(tracker: ChargingTracker, first: float, last: float, obs) -> None:
    """Beobachtet alle 10 min von ``first`` bis ``last`` (ohne Pause über ``SESSION_GAP``)."""
    minute = first
    while minute <= last:
        tracker.record(at(minute), obs)
        minute += 10


def all_sessions(tracker: ChargingTracker) -> list[StoredSession]:
    return tracker.sessions(T0 - timedelta(days=1), T0 + timedelta(days=1))


def test_plug_in_opens_and_unplug_closes_a_session(tracker) -> None:
    assert all_sessions(tracker) == []
    tracker.record(at(0), unplugged())
    assert all_sessions(tracker) == []
    tracker.record(at(1), plugged("tesla", "default"))
    (session,) = all_sessions(tracker)
    assert (session.wallbox, session.start, session.end) == ("evcs", at(1), None)
    assert session.last_seen == at(1)
    hold(tracker, 11, 51, plugged("tesla", "default"))
    tracker.record(at(61), unplugged())
    (session,) = all_sessions(tracker)
    assert (session.start, session.end, session.last_seen) == (at(1), at(61), at(61))


def test_unknown_plug_state_keeps_the_session_open(tracker) -> None:
    tracker.record(at(0), plugged("tesla", "default"))
    tracker.record(at(5), unknown())
    tracker.record(at(10), unknown())
    (session,) = all_sessions(tracker)
    assert session.end is None
    assert session.last_seen == at(0)  # unbekannt zählt nicht als gesehen
    tracker.record(at(12), plugged("tesla", "default"))
    assert len(all_sessions(tracker)) == 1


def test_unknown_plug_state_without_session_opens_nothing(tracker) -> None:
    tracker.record(at(0), unknown())
    assert all_sessions(tracker) == []


def test_session_survives_short_gap(tracker) -> None:
    tracker.record(at(0), plugged("tesla", "default"))
    tracker.record(at(25), plugged("tesla", "default"))
    (session,) = all_sessions(tracker)
    assert (session.start, session.end) == (at(0), None)
    assert session.last_seen == at(25)


def test_gap_of_exactly_thirty_minutes_still_continues(tracker) -> None:
    tracker.record(at(0), plugged("tesla", "default"))
    tracker.record(at(30), plugged("tesla", "default"))
    assert len(all_sessions(tracker)) == 1


def test_long_gap_splits_the_session(tracker) -> None:
    tracker.record(at(0), plugged("tesla", "default"))
    tracker.record(at(45), plugged("tesla", "default"))
    newest, oldest = all_sessions(tracker)  # neueste zuerst
    assert (oldest.start, oldest.end) == (at(0), at(0))
    assert (newest.start, newest.end) == (at(45), None)


def test_unplugged_during_gap_ends_at_last_seen(tracker) -> None:
    tracker.record(at(0), plugged("tesla", "default"))
    tracker.record(at(45), unplugged())
    (session,) = all_sessions(tracker)
    assert (session.start, session.end, session.last_seen) == (at(0), at(0), at(0))


def test_long_unknown_state_ends_the_session_at_last_seen(tracker) -> None:
    tracker.record(at(0), plugged("tesla", "default"))
    tracker.record(at(45), unknown())
    (session,) = all_sessions(tracker)
    assert session.end == at(0)


def test_open_session_is_resumed_after_restart(engine, clock, cfg, live) -> None:
    first = ChargingTracker(engine, clock, cfg, live)
    first.record(at(0), plugged("tesla", "default"))
    (before,) = all_sessions(first)
    second = ChargingTracker(engine, clock, cfg, live)  # Neustart, das Auto steckt noch
    second.record(at(20), plugged("tesla", "default"))
    (after,) = all_sessions(second)
    assert after.id == before.id and after.end is None and after.last_seen == at(20)


def test_open_session_ends_after_a_long_restart(engine, clock, cfg, live) -> None:
    first = ChargingTracker(engine, clock, cfg, live)
    first.record(at(0), plugged("tesla", "default"))
    second = ChargingTracker(engine, clock, cfg, live)
    second.record(at(120), plugged("tesla", "default"))
    newest, oldest = all_sessions(second)
    assert oldest.end == at(0) and newest.start == at(120) and newest.end is None


def test_open_session_of_a_removed_wallbox_ends_after_the_gap(tracker) -> None:
    tracker.record(at(0), plugged("tesla", "default"))
    tracker.record(at(45), [WallboxSeen("twc", None, None, None)])
    (session,) = all_sessions(tracker)
    assert session.wallbox == "evcs" and session.end == at(0)


def test_two_open_sessions_of_one_wallbox_are_cleaned_up_at_start(engine, clock, cfg, live) -> None:
    with engine.begin() as conn:
        for start in (0, 10):
            conn.execute(
                insert(charging_session).values(
                    wallbox="evcs", vehicle="", start=at(start), end=None, last_seen=at(start + 5)
                )
            )
    tracker = ChargingTracker(engine, clock, cfg, live)
    tracker.record(at(20), plugged())
    newest, oldest = all_sessions(tracker)
    assert (oldest.end, newest.end) == (at(5), None)


def test_vehicle_is_the_longest_occupant(tracker) -> None:
    tracker.record(at(0), plugged(None, "unclear"))
    hold(tracker, 5, 115, plugged("tesla", "detected"))
    tracker.record(at(125), unplugged())
    (session,) = all_sessions(tracker)
    assert session.vehicle == "tesla"


def test_vehicle_is_guest_or_unknown(tracker) -> None:
    tracker.record(at(0), plugged("guest", "default"))
    tracker.record(at(30), unplugged())
    tracker.record(at(40), plugged(None, "unclear"))
    tracker.record(at(50), unplugged())
    unknown_car, guest = all_sessions(tracker)
    assert guest.vehicle == "guest" and unknown_car.vehicle is None


def test_short_session_takes_the_occupant_of_the_closing_observation(tracker) -> None:
    tracker.record(T0, plugged(None, "unclear"))
    # 20 s später abgezogen; der Insasse steht nur in dieser Beobachtung
    tracker.record(T0 + timedelta(seconds=20), seen("tesla", "detected", connected=False))
    (session,) = all_sessions(tracker)
    assert session.end == T0 + timedelta(seconds=20) and session.vehicle == "tesla"


def test_session_without_time_for_any_vehicle_keeps_the_last_known_one(tracker) -> None:
    tracker.record(T0, plugged("tesla", "detected"))
    tracker.record(T0, plugged("polestar", "detected"))  # gleiche Sekunde: tesla hat keine Zeit
    tracker.record(T0, unplugged())
    (session,) = all_sessions(tracker)
    assert session.vehicle == "polestar"


def test_occupant_before_the_session_does_not_count_for_a_short_one(tracker) -> None:
    tracker.record(at(-60), seen("tesla", "default", connected=False))  # nur davor, ohne Session
    tracker.record(T0, plugged(None, "unclear"))
    tracker.record(T0 + timedelta(seconds=20), unplugged())
    (session,) = all_sessions(tracker)
    assert session.vehicle is None


def test_occupant_arriving_in_an_open_short_session_is_saved_at_once(tracker) -> None:
    tracker.record(T0, plugged(None, "unclear"))
    tracker.record(T0 + timedelta(seconds=20), plugged("tesla", "detected"))
    (session,) = all_sessions(tracker)  # noch offen, der Insasse hat noch keine Zeit
    assert session.end is None and session.vehicle == "tesla"


def test_failed_transaction_resets_the_memory_state(tracker, engine, monkeypatch) -> None:
    original = tracker._begin

    def begin_then_fail(*args, **kwargs) -> None:
        original(*args, **kwargs)
        raise RuntimeError("Datenbank gesperrt")

    monkeypatch.setattr(tracker, "_begin", begin_then_fail)
    with pytest.raises(RuntimeError):
        tracker.record(at(0), plugged("tesla", "detected"))
    assert all_sessions(tracker) == [] and log_rows(engine) == []  # zurückgerollt
    monkeypatch.undo()
    tracker.record(at(1), plugged("tesla", "detected"))
    (session,) = all_sessions(tracker)  # sonst fehlte die Session: im Speicher schon „offen“
    assert session.start == at(1) and session.end is None
    assert [row[2] for row in log_rows(engine)] == ["tesla"]  # die Belegung wird neu geschrieben
    tracker.record(at(2), unplugged())
    assert all_sessions(tracker)[0].end == at(2)


def test_running_session_shows_the_current_occupant(tracker) -> None:
    tracker.record(at(0), plugged(None, "unclear"))
    tracker.record(at(5), plugged("tesla", "detected"))
    (session,) = all_sessions(tracker)
    assert session.vehicle == "tesla"  # vorläufig: noch hat nur „unklar“ Zeit gehabt


def test_vehicle_is_redetermined_once_a_minute(tracker) -> None:
    tracker.record(at(0), plugged("polestar", "default"))
    tracker.record(at(5), plugged("tesla", "detected"))
    assert all_sessions(tracker)[0].vehicle == "polestar"
    tracker.record(at(11), plugged("tesla", "detected"))
    assert all_sessions(tracker)[0].vehicle == "tesla"


def test_last_seen_is_written_at_most_every_minute(tracker) -> None:
    tracker.record(at(0), plugged("tesla", "default"))
    for seconds in (10, 20, 50):
        tracker.record(T0 + timedelta(seconds=seconds), plugged("tesla", "default"))
    assert all_sessions(tracker)[0].last_seen == T0
    tracker.record(T0 + timedelta(seconds=60), plugged("tesla", "default"))
    assert all_sessions(tracker)[0].last_seen == T0 + timedelta(seconds=60)
    tracker.record(T0 + timedelta(seconds=80), plugged("tesla", "default"))
    assert all_sessions(tracker)[0].last_seen == T0 + timedelta(seconds=60)


def test_sessions_cover_the_period_and_filter_by_vehicle(tracker) -> None:
    tracker.record(at(0), plugged("tesla", "default"))
    hold(tracker, 10, 50, plugged("tesla", "default"))
    tracker.record(at(60), unplugged())
    tracker.record(at(120), plugged("polestar", "default"))  # läuft
    assert [s.start for s in tracker.sessions(at(-10), at(10))] == [at(0)]
    assert [s.start for s in tracker.sessions(at(60), at(120))] == []  # [start, end) ohne Rand
    assert [s.start for s in tracker.sessions(at(30), at(500))] == [at(120), at(0)]
    assert [s.vehicle for s in tracker.sessions(at(0), at(500), "polestar")] == ["polestar"]
    assert tracker.sessions(at(0), at(500), "guest") == []


def test_session_vehicle_picks_the_longest_and_the_later_on_a_tie() -> None:
    log = [(at(0), "a"), (at(10), "b"), (at(20), None)]
    assert session_vehicle(log, at(0), at(30)) == "b"  # 10 min gegen 10 min: das spätere
    assert session_vehicle(log, at(0), at(15)) == "a"
    assert session_vehicle(log, at(12), at(30)) == "b"  # Eintrag vor dem Beginn gilt ab Beginn
    assert session_vehicle([(at(0), None)], at(0), at(30)) is None
    assert session_vehicle([], at(0), at(30)) is None
    assert session_vehicle([(at(0), "a")], at(10), at(10)) is None


# --- Abstecken melden ----------------------------------------------------------------------


def test_unplug_reports_session_vehicle(tracker) -> None:
    assert tracker.record(at(0), plugged("tesla", "detected")) == []
    assert tracker.record(at(10), plugged("tesla", "detected")) == []
    assert tracker.record(at(20), unplugged()) == [Unplugged("evcs", "tesla", at(20))]


def test_unplug_of_guest_reports_guest(tracker) -> None:
    tracker.record(at(0), plugged("guest", "default"))
    tracker.record(at(10), plugged("guest", "default"))
    assert tracker.record(at(20), unplugged()) == [Unplugged("evcs", "guest", at(20))]


def test_unplug_without_known_vehicle_reports_none(tracker) -> None:
    tracker.record(at(0), plugged(None, "unclear"))
    (report,) = tracker.record(at(10), unplugged())
    assert report == Unplugged("evcs", None, at(10)) and report.vehicle is None


def test_unplug_without_a_session_reports_nothing(tracker) -> None:
    assert tracker.record(at(0), unplugged()) == []
    assert tracker.record(at(10), unplugged()) == []


def test_unknown_plug_state_reports_nothing(tracker) -> None:
    tracker.record(at(0), plugged("tesla", "detected"))
    assert tracker.record(at(10), unknown()) == []
    (session,) = all_sessions(tracker)
    assert session.end is None


def test_unplug_reports_only_once(tracker) -> None:
    tracker.record(at(0), plugged("tesla", "detected"))
    assert len(tracker.record(at(10), unplugged())) == 1
    assert tracker.record(at(20), unplugged()) == []


def test_unplug_after_gap_reports_once_at_last_seen(tracker) -> None:
    tracker.record(at(0), plugged("tesla", "detected"))
    assert tracker.record(at(45), unplugged()) == [Unplugged("evcs", "tesla", at(0))]
    assert tracker.record(at(55), unplugged()) == []


def test_still_plugged_after_gap_reports_nothing(tracker) -> None:
    tracker.record(at(0), plugged("tesla", "detected"))
    assert tracker.record(at(45), plugged("tesla", "detected")) == []
    assert len(all_sessions(tracker)) == 2  # die alte endete, eine neue läuft


def test_unknown_state_after_gap_reports_nothing(tracker) -> None:
    tracker.record(at(0), plugged("tesla", "detected"))
    assert tracker.record(at(45), unknown()) == []
    (session,) = all_sessions(tracker)
    assert session.end == at(0)


def test_failed_transaction_reports_nothing_and_the_next_run_reports(tracker, monkeypatch) -> None:
    tracker.record(at(0), plugged("tesla", "detected"))
    original = tracker._close

    def close_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Datenbank gesperrt")

    monkeypatch.setattr(tracker, "_close", close_then_fail)
    with pytest.raises(RuntimeError):
        tracker.record(at(10), unplugged())
    monkeypatch.undo()
    assert tracker.record(at(20), unplugged()) == [Unplugged("evcs", "tesla", at(20))]


async def test_run_passes_reports_and_observation_to_callback(engine, clock, cfg, live) -> None:
    calls: list[tuple[list[Unplugged], list[WallboxSeen]]] = []

    async def on_observed(unplugged_now: list[Unplugged], observed: list[WallboxSeen]) -> None:
        calls.append((unplugged_now, observed))
        if len(calls) == 1:
            raise RuntimeError("Rückruf kaputt")  # stoppt run nicht

    tracker = ChargingTracker(engine, clock, cfg, live, on_observed)
    live.health = {"wallboxes": [{"id": "evcs", "occupant": "tesla", "assignment": "default"}]}
    live.state = {"signals": {"wallbox.evcs.status": {"v": 1, "q": "ok"}}}

    async def observation(count: int) -> None:
        tracker.wake()
        for _ in range(200):
            if len(calls) >= count:
                break
            await asyncio.sleep(0.01)

    task = asyncio.create_task(tracker.run())
    try:
        with capture_logs() as logs:
            await observation(1)
            await asyncio.sleep(0.05)
        live.state = {"signals": {"wallbox.evcs.status": {"v": 0, "q": "ok"}}}
        await observation(2)
    finally:
        await stop(task)
    assert any(entry["log_level"] == "error" for entry in logs)
    assert calls[0][0] == []  # auch ohne Meldung
    assert calls[0][1] == [
        WallboxSeen("evcs", True, "tesla", "default"),
        WallboxSeen("twc", None, None, None),
    ]
    assert calls[1][0] == [Unplugged("evcs", "tesla", clock.now())]
    assert calls[1][1][0] == WallboxSeen("evcs", False, "tesla", "default")


async def test_run_skips_callback_when_recording_fails(
    engine, clock, cfg, live, monkeypatch
) -> None:
    calls: list[int] = []

    async def on_observed(unplugged_now: list[Unplugged], observed: list[WallboxSeen]) -> None:
        calls.append(1)

    tracker = ChargingTracker(engine, clock, cfg, live, on_observed)

    def failing(now, seen_now):
        raise RuntimeError("Datenbank gesperrt")

    monkeypatch.setattr(tracker, "record", failing)
    task = asyncio.create_task(tracker.run())
    try:
        with capture_logs() as logs:
            tracker.wake()
            await asyncio.sleep(0.1)
    finally:
        await stop(task)
    assert calls == []
    assert any(entry["log_level"] == "error" for entry in logs)
