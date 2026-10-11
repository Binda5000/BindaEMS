from collections.abc import Iterable, Iterator
from dataclasses import replace

import pytest
from bindaems_watchdog.config import Settings
from bindaems_watchdog.logic import ARMED, DISABLED, TRIPPED, WAITING, WatchdogLogic
from hypothesis import example, given
from hypothesis import strategies as st

SETTINGS = Settings(
    enabled=True,
    timeout_s=90.0,
    rearm_s=30.0,
    gap_s=25.0,
    retries=6,
    retry_s=5.0,
    state_file="state.json",
    actions=(),
)


def beats(logic: WatchdogLogic, times: Iterable[float]) -> None:
    for t in times:
        logic.heartbeat(1, t)


def armed() -> WatchdogLogic:
    logic = WatchdogLogic(SETTINGS, armed_at_start=False, now=0.0)
    beats(logic, [0, 10, 20, 30])
    logic.tick(30.0)
    return logic


def tripped() -> WatchdogLogic:
    logic = armed()
    logic.tick(120.5)
    return logic


def test_starts_waiting() -> None:
    assert WatchdogLogic(SETTINGS, armed_at_start=False, now=0.0).state == WAITING


def test_arms_after_30s_of_stable_heartbeats() -> None:
    logic = WatchdogLogic(SETTINGS, armed_at_start=False, now=0.0)
    beats(logic, [0, 10, 20])
    assert not logic.tick(29.0) and logic.state == WAITING
    beats(logic, [30])
    assert not logic.tick(30.0) and logic.state == ARMED


def test_gap_restarts_the_arming_run() -> None:
    logic = WatchdogLogic(SETTINGS, armed_at_start=False, now=0.0)
    beats(logic, [0, 10, 40])
    logic.tick(45.0)
    assert logic.state == WAITING
    beats(logic, [50, 60, 70])
    logic.tick(70.0)
    assert logic.state == ARMED


def test_trips_once_after_timeout() -> None:
    logic = armed()
    assert not logic.tick(120.0)
    assert logic.tick(120.5) and logic.state == TRIPPED
    assert not logic.tick(200.0)


def test_rearms_after_trip_with_stable_heartbeats() -> None:
    logic = tripped()
    beats(logic, [130, 140, 150])
    logic.tick(155.0)
    assert logic.state == TRIPPED
    beats(logic, [160])
    logic.tick(160.0)
    assert logic.state == ARMED


def test_release_returns_to_waiting_without_trip() -> None:
    logic = armed()
    logic.heartbeat(0, 40.0)
    assert logic.state == WAITING and not logic.tick(1000.0)


def test_restart_while_armed_trips_after_timeout() -> None:
    logic = WatchdogLogic(SETTINGS, armed_at_start=True, now=0.0)
    assert logic.state == ARMED
    assert not logic.tick(90.0)
    assert logic.tick(90.5)


def test_disabled_never_trips() -> None:
    logic = WatchdogLogic(replace(SETTINGS, enabled=False), armed_at_start=True, now=0.0)
    beats(logic, [0, 10])
    assert logic.state == DISABLED and not logic.tick(500.0)


def test_heartbeats_while_tripped_do_not_retrigger() -> None:
    logic = tripped()
    beats(logic, [130])
    assert not logic.tick(400.0) and logic.state == TRIPPED


# Weitere Randfälle


def test_regular_heartbeats_keep_it_armed() -> None:
    logic = armed()
    for t in range(31, 3000):
        if t % 10 == 0:
            beats(logic, [t])
        assert not logic.tick(float(t))
    assert logic.state == ARMED


def test_each_heartbeat_restarts_the_timeout() -> None:
    logic = armed()
    beats(logic, [100])
    assert not logic.tick(190.0)
    assert logic.tick(190.5)


def test_restart_while_armed_stays_armed_if_the_core_returns_in_time() -> None:
    logic = WatchdogLogic(SETTINGS, armed_at_start=True, now=0.0)
    beats(logic, [60])
    assert not logic.tick(100.0)
    assert not logic.tick(150.0)
    assert logic.tick(150.5)


def test_restart_while_armed_counts_from_the_start_time() -> None:
    # Die monotone Uhr zählt Sekunden seit dem Systemstart, nicht ab 0.
    logic = WatchdogLogic(SETTINGS, armed_at_start=True, now=5000.0)
    assert not logic.tick(5090.0)
    assert logic.tick(5090.5)


def test_trips_again_after_rearming() -> None:
    logic = tripped()
    beats(logic, [130, 140, 150, 160])
    logic.tick(160.0)
    assert logic.state == ARMED
    assert not logic.tick(250.0)
    assert logic.tick(250.5) and logic.state == TRIPPED


def test_release_while_tripped_returns_to_waiting() -> None:
    logic = tripped()
    logic.heartbeat(0, 130.0)
    assert logic.state == WAITING and not logic.tick(1000.0)


def test_release_forgets_the_arming_run() -> None:
    logic = WatchdogLogic(SETTINGS, armed_at_start=False, now=0.0)
    beats(logic, [0, 10, 20])
    logic.heartbeat(0, 25.0)
    beats(logic, [30])
    logic.tick(31.0)  # Der Lauf von 0 an wäre 31 s alt; nach der Freigabe beginnt er bei 30.
    assert logic.state == WAITING
    beats(logic, [40, 50, 60])
    logic.tick(60.0)
    assert logic.state == ARMED


def test_single_heartbeat_does_not_arm() -> None:
    logic = WatchdogLogic(SETTINGS, armed_at_start=False, now=0.0)
    beats(logic, [0])
    for t in range(1, 200):
        assert not logic.tick(float(t))
        assert logic.state == WAITING


@pytest.mark.parametrize(("gap", "state"), [(25.0, ARMED), (25.5, WAITING)])
def test_gap_limit(gap: float, state: int) -> None:
    logic = WatchdogLogic(SETTINGS, armed_at_start=False, now=0.0)
    beats(logic, [0.0, gap, 2 * gap])
    logic.tick(2 * gap)
    assert logic.state == state


def test_negative_values_are_ignored() -> None:
    logic = armed()
    logic.heartbeat(-1, 100.0)
    assert logic.state == ARMED
    assert logic.tick(120.5)


def test_disabled_ignores_everything() -> None:
    logic = WatchdogLogic(replace(SETTINGS, enabled=False), armed_at_start=False, now=0.0)
    beats(logic, [0, 10, 20, 30, 40])
    assert not logic.tick(40.0) and logic.state == DISABLED
    logic.heartbeat(0, 50.0)
    assert not logic.tick(1000.0) and logic.state == DISABLED


# Gegenprobe: Abläufe wie im Dienst (Takt 1 s) gegen ein zweites, unabhängig formuliertes Modell.

SEGMENTS = st.lists(
    st.tuples(
        st.sampled_from(["beat", "silence", "release"]),
        st.integers(min_value=1, max_value=200),  # Dauer in s
        st.sampled_from([5, 10, 20, 25, 26, 30]),  # Abstand der Heartbeats in s
    ),
    max_size=30,
)


def play(segments: list[tuple[str, int, int]]) -> Iterator[tuple[str, float]]:
    """Der Ablauf als Ereignisse (Art, Sekunde); der Dienst prüft jede Sekunde danach."""
    second = 0
    for mode, duration, period in segments:
        for i in range(duration):
            second += 1
            if mode == "release" and i == 0:
                yield "release", float(second)
            elif mode == "beat" and i % period == 0:
                yield "beat", float(second)
            yield "tick", float(second)


def stable_run(beat_times: list[float], now: float) -> bool:
    """Läuft der Heartbeat seit ``rearm_s`` stabil? Rückwärts von der letzten Meldung gelesen."""
    if not beat_times or now - beat_times[-1] > SETTINGS.gap_s:
        return False
    start = len(beat_times) - 1
    while start > 0 and beat_times[start] - beat_times[start - 1] <= SETTINGS.gap_s:
        start -= 1
    return now - beat_times[start] >= SETTINGS.rearm_s


# Zwei Grenzfälle laufen immer mit:
# - Meldungen in Sekunde 1 und 6: In Sekunde 31 ist der Lauf genau 30 s alt und die letzte Meldung
#   genau 25 s her, das ist nicht „über gap_s“. Der Watchdog wird scharf.
# - Meldungen bis Sekunde 31: Sekunde 121 hat genau 90 s Stille (kein Auslösen), Sekunde 122 mehr.
@example([("beat", 6, 5), ("silence", 30, 5)], False)
@example([("beat", 31, 10), ("silence", 100, 10)], False)
@given(SEGMENTS, st.booleans())
def test_state_machine_matches_the_reference_model(
    segments: list[tuple[str, int, int]], armed_at_start: bool
) -> None:
    logic = WatchdogLogic(SETTINGS, armed_at_start, now=0.0)
    last_beat, beat_times = 0.0, []
    for kind, now in play(segments):
        if kind == "beat":
            logic.heartbeat(1, now)
            last_beat = now
            beat_times.append(now)
        elif kind == "release":
            logic.heartbeat(0, now)
            beat_times.clear()
            assert logic.state == WAITING
        else:
            before = logic.state
            result = logic.tick(now)
            if before == ARMED:
                expected = now - last_beat > SETTINGS.timeout_s
                assert result == expected
                assert logic.state == (TRIPPED if expected else ARMED)
            else:
                assert not result
                assert logic.state == (ARMED if stable_run(beat_times, now) else before)


@given(SEGMENTS)
def test_disabled_never_changes_state(segments: list[tuple[str, int, int]]) -> None:
    logic = WatchdogLogic(replace(SETTINGS, enabled=False), armed_at_start=True, now=0.0)
    for kind, now in play(segments):
        if kind == "tick":
            assert not logic.tick(now)
        else:
            logic.heartbeat(1 if kind == "beat" else 0, now)
        assert logic.state == DISABLED
