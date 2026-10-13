from datetime import datetime, timedelta

import pytest
from tests.helpers import T0

from bindaems.core.control.charge_modes import (
    FULL_POWER,
    MODE_CODES,
    Effective,
    ModeDecision,
    ModeInput,
    ModeMachine,
    ModeTimers,
)
from bindaems.shared.config import Config

TIMERS = ModeTimers(start_after_s=120.0, stop_after_s=300.0, min_on_s=600.0, min_off_s=600.0)
ODD = ModeTimers(start_after_s=10.0, stop_after_s=20.0, min_on_s=100.0, min_off_s=50.0)
ALWAYS_ON = [Effective.NOW, Effective.BOOST, Effective.MIN_PV]


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def inp(mode=Effective.PV, *, plugged=True, enough=True, observed=False, **kw) -> ModeInput:
    return ModeInput(
        mode=mode,
        plugged=plugged,
        complete=kw.pop("complete", False),
        enough=enough,
        observed_charging=observed,
        **kw,
    )


def run(machine, start: int, end: int, **kw) -> dict[int, ModeDecision]:
    return {s: machine.step(at(s), inp(**kw)) for s in range(start, end)}


def test_pv_starts_after_120_s_of_enough_surplus() -> None:
    machine = ModeMachine(TIMERS)
    decisions = run(machine, 0, 121)
    assert not any(d.charge for s, d in decisions.items() if s < 120)
    assert decisions[120] == ModeDecision(True, "pv_surplus")


def test_pv_stops_after_300_s_below_but_not_before_600_s_on() -> None:
    machine = ModeMachine(TIMERS)
    run(machine, 0, 121)  # Start bei 120
    decisions = run(machine, 121, 721, enough=False)
    assert all(d.charge for s, d in decisions.items() if s < 720)
    assert decisions[720] == ModeDecision(False, "pv_surplus")


def test_restart_waits_for_the_minimum_pause() -> None:
    machine = ModeMachine(TIMERS)
    run(machine, 0, 121)
    run(machine, 121, 721, enough=False)  # Stopp bei 720
    decisions = run(machine, 721, 1321)
    assert not decisions[1319].charge and decisions[1320].charge


def test_first_step_takes_over_observed_charging() -> None:  # Review Focus 1
    machine = ModeMachine(TIMERS)
    decisions = run(machine, 0, 301, enough=False, observed=True)
    assert all(d.charge for s, d in decisions.items() if s < 300)
    assert decisions[300] == ModeDecision(False, "pv_surplus")


def test_new_session_in_pv_starts_without_charging() -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(plugged=False))
    assert machine.step(at(1), inp(observed=True, new_session=True)) == ModeDecision(False, None)


def test_mode_change_acts_at_once() -> None:
    machine = ModeMachine(TIMERS)
    run(machine, 0, 121)  # PV lädt
    assert not machine.step(at(121), inp(Effective.OFF)).charge
    assert machine.step(at(122), inp(Effective.NOW)).charge
    assert not machine.step(at(123), inp(Effective.PV, enough=False)).charge


def test_complete_stays_stopped() -> None:
    assert not ModeMachine(TIMERS).step(at(0), inp(Effective.NOW, complete=True)).charge


def test_unknown_plug_state_holds_the_decision() -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(Effective.NOW))
    assert machine.step(at(1), inp(Effective.NOW, plugged=None)).charge


def test_force_stop_keeps_now_off_for_the_minimum_pause() -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(Effective.NOW))
    machine.force_stop(at(10))
    assert not machine.step(at(609), inp(Effective.NOW)).charge
    assert machine.step(at(610), inp(Effective.NOW)).charge


# --- Modi, Codes und Zeiten aus der Konfiguration -------------------------------------------------


def test_modes_have_their_values_and_reason_codes() -> None:
    assert [mode.value for mode in Effective] == ["pv", "min_pv", "now", "off", "boost"]
    assert dict(MODE_CODES) == {
        Effective.PV: "mode_pv",
        Effective.MIN_PV: "mode_min_pv",
        Effective.NOW: "mode_now",
        Effective.OFF: "mode_off",
        Effective.BOOST: "boost",
    }


def test_full_power_modes_are_now_and_boost() -> None:
    assert FULL_POWER == {Effective.NOW, Effective.BOOST}


def test_timers_follow_the_configuration(cfg: Config) -> None:
    assert ModeTimers.from_config(cfg.control) == TIMERS
    changed = cfg.control.model_copy(
        update={"start_after_s": 30.0, "stop_after_s": 90.0, "min_on_s": 5.0, "min_off_s": 7.0}
    )
    assert ModeTimers.from_config(changed) == ModeTimers(30.0, 90.0, 5.0, 7.0)


# --- Regel 1: nicht angesteckt --------------------------------------------------------------------


def test_unplugged_is_off_and_forgets_the_pause() -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(Effective.NOW))
    machine.force_stop(at(10))
    assert machine.step(at(20), inp(Effective.NOW, plugged=False)) == ModeDecision(False, None)
    assert machine.step(at(21), inp(Effective.NOW)) == ModeDecision(True, None)
    assert machine.step(at(22), inp(Effective.NOW)) == ModeDecision(True, None)


@pytest.mark.parametrize(("enough", "expected"), [(True, True), (False, False), (None, False)])
def test_plugged_step_after_unplugging_decides_at_once(enough, expected) -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(plugged=False))
    # ohne new_session ist es kein erster Schritt: Das Auto zählt als Moduswechsel
    assert machine.step(at(1), inp(enough=enough, observed=True)) == ModeDecision(expected, None)


def test_unplugging_forgets_the_last_mode() -> None:
    machine = ModeMachine(TIMERS)
    run(machine, 0, 121)  # PV lädt seit 120
    machine.step(at(130), inp(plugged=False))
    # derselbe Modus wie vor dem Abstecken, aber ohne letzten Modus: Moduswechsel, kein Timer
    assert machine.step(at(131), inp()) == ModeDecision(True, None)


# --- Regel 2: Ansteckzustand unbekannt ------------------------------------------------------------


def test_unknown_plug_state_before_the_first_decision_is_off() -> None:
    machine = ModeMachine(TIMERS)
    assert machine.step(at(0), inp(Effective.NOW, plugged=None)) == ModeDecision(False, None)
    assert not machine.charging


def test_unknown_plug_state_holds_a_stop_as_well() -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(Effective.NOW))
    machine.force_stop(at(10))
    assert machine.step(at(11), inp(Effective.NOW, plugged=None)) == ModeDecision(False, None)


def test_unknown_plug_state_leaves_the_timers_alone() -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(enough=False, observed=True))  # übernimmt das ladende Auto
    decisions = run(machine, 1, 300, plugged=None, enough=True)  # unbekannt: zählt nicht
    assert set(decisions.values()) == {ModeDecision(True, None)}
    assert machine.step(at(300), inp(enough=False)) == ModeDecision(False, "pv_surplus")


def test_unknown_plug_state_leaves_a_mode_change_to_the_next_known_step() -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(Effective.NOW))
    machine.force_stop(at(10))
    assert not machine.step(at(11), inp(Effective.PV, plugged=None)).charge
    # erst jetzt gilt der Moduswechsel: sofort, und er hebt die Pause auf
    assert machine.step(at(12), inp(Effective.PV)) == ModeDecision(True, None)


# --- Regel 3: geladen -----------------------------------------------------------------------------


@pytest.mark.parametrize("mode", list(Effective))
def test_complete_beats_the_first_step(mode) -> None:
    machine = ModeMachine(TIMERS)
    assert machine.step(at(0), inp(mode, complete=True, observed=True)) == ModeDecision(False, None)


def test_complete_stops_a_running_car_but_is_not_remembered() -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(Effective.NOW))
    assert machine.step(at(1), inp(Effective.NOW, complete=True)) == ModeDecision(False, None)
    assert not machine.charging
    # „geladen“ hält der Aufrufer bis zum Abstecken; die Maschine merkt es sich nicht
    assert machine.step(at(2), inp(Effective.NOW)) == ModeDecision(True, None)


def test_the_timers_run_while_the_car_is_complete() -> None:
    machine = ModeMachine(TIMERS)
    run(machine, 0, 2, enough=False)
    decisions = run(machine, 2, 122, complete=True)  # der Überschuss reicht ab 2
    assert not any(d.charge for d in decisions.values())
    assert machine.step(at(122), inp()) == ModeDecision(True, "pv_surplus")


# --- Regel 5: erster Schritt und Wiederaufnahme ---------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "observed", "expected"),
    [
        (Effective.PV, True, True),
        (Effective.PV, False, False),
        (Effective.PV, None, False),
        (Effective.OFF, True, False),
        (Effective.NOW, False, True),
        (Effective.BOOST, False, True),
        (Effective.MIN_PV, False, True),
    ],
)
def test_first_step_by_mode(mode, observed, expected) -> None:
    machine = ModeMachine(TIMERS)
    decision = machine.step(at(0), inp(mode, enough=False, observed=observed))
    assert decision == ModeDecision(expected, None)
    assert machine.charging is expected


def test_a_new_session_beats_the_first_step() -> None:
    machine = ModeMachine(TIMERS)
    # ein Auto, das im neuen Ansteckvorgang selbst gestartet hat, übernimmt PV nicht
    assert machine.step(at(0), inp(observed=True, new_session=True)) == ModeDecision(False, None)


def test_a_new_session_beats_resumed() -> None:
    machine = ModeMachine(TIMERS)
    run(machine, 0, 121)  # PV lädt seit 120
    decision = machine.step(at(200), inp(observed=True, new_session=True, resumed=True))
    assert decision == ModeDecision(False, None)  # neuer Ansteckvorgang statt Übernahme


def test_a_restart_sets_both_timers_to_the_current_step() -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(enough=True, observed=True))
    decisions = run(machine, 1, 301, enough=False, observed=True)
    # die Stopp-Zeit zählt ab dem ersten Schritt, obwohl der Überschuss dort noch reichte
    assert decisions[299].charge
    assert decisions[300] == ModeDecision(False, "pv_surplus")


def test_resumed_takes_over_and_counts_the_minimum_run_as_done() -> None:
    machine = ModeMachine(TIMERS)
    run(machine, 0, 121)  # Start bei 120, die Mindestlaufzeit liefe bis 720
    resumed = inp(enough=False, observed=True, resumed=True)
    assert machine.step(at(200), resumed) == ModeDecision(True, None)
    decisions = run(machine, 201, 501, enough=False, observed=True)
    assert all(d.charge for s, d in decisions.items() if s < 500)
    assert decisions[500] == ModeDecision(False, "pv_surplus")


def test_resumed_in_pv_without_a_running_car_waits_for_the_start_time() -> None:
    machine = ModeMachine(TIMERS)
    run(machine, 0, 10)
    assert machine.step(at(130), inp(resumed=True)) == ModeDecision(False, None)
    decisions = run(machine, 131, 251)
    assert not any(d.charge for s, d in decisions.items() if s < 250)
    assert decisions[250] == ModeDecision(True, "pv_surplus")


def test_resumed_forgets_the_pause() -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(Effective.NOW))
    machine.force_stop(at(10))
    assert machine.step(at(100), inp(Effective.NOW, resumed=True)) == ModeDecision(True, None)
    assert machine.step(at(101), inp(Effective.NOW)) == ModeDecision(True, None)


def test_unknown_plug_state_does_not_use_up_the_first_step() -> None:  # Review Focus 3
    machine = ModeMachine(TIMERS)
    run(machine, 0, 5, plugged=None, enough=False)
    assert machine.step(at(5), inp(enough=False, observed=True)) == ModeDecision(True, None)
    decisions = run(machine, 6, 306, enough=False, observed=True)
    assert all(d.charge for s, d in decisions.items() if s < 305)
    assert decisions[305] == ModeDecision(False, "pv_surplus")


def test_resumed_with_unknown_plug_state_applies_to_the_next_known_step() -> None:
    machine = ModeMachine(TIMERS)
    run(machine, 0, 121)  # PV lädt seit 120
    assert machine.step(at(200), inp(plugged=None, resumed=True)).charge  # hält
    # nach der Pause lädt das Auto nicht mehr: PV übernimmt das und wartet auf den Start
    assert machine.step(at(201), inp(observed=False)) == ModeDecision(False, None)


# --- Regel 4: neuer Ansteckvorgang (geht der Übernahme vor) ---------------------------------------


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (Effective.PV, False),
        (Effective.OFF, False),
        (Effective.NOW, True),
        (Effective.BOOST, True),
        (Effective.MIN_PV, True),
    ],
)
def test_new_session_by_mode(mode, expected) -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(mode, plugged=False))
    # auch ein Auto, das selbst gestartet hat, beginnt in PV und Aus ohne Laden
    decision = machine.step(at(1), inp(mode, observed=True, new_session=True))
    assert decision == ModeDecision(expected, None)


def test_pv_starts_120_s_after_a_new_session() -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(plugged=False))
    machine.step(at(1), inp(new_session=True))
    decisions = run(machine, 2, 122)
    assert not any(d.charge for s, d in decisions.items() if s < 121)
    assert decisions[121] == ModeDecision(True, "pv_surplus")


# --- Regel 6: Moduswechsel ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "enough", "expected"),
    [
        (Effective.OFF, True, False),
        (Effective.NOW, False, True),
        (Effective.BOOST, False, True),
        (Effective.MIN_PV, False, True),
        (Effective.PV, True, True),
        (Effective.PV, False, False),
        (Effective.PV, None, False),
    ],
)
def test_mode_change_decides_at_once_by_the_new_mode(mode, enough, expected) -> None:
    machine = ModeMachine(TIMERS)
    before = Effective.OFF if mode is Effective.NOW else Effective.NOW  # ein anderer Modus
    machine.step(at(0), inp(before))
    decision = machine.step(at(1), inp(mode, enough=enough, observed=True))
    assert decision == ModeDecision(expected, None)


def test_mode_change_sets_both_timers_to_the_current_step() -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(Effective.NOW))
    assert not machine.step(at(10), inp(enough=False)).charge  # Wechsel auf PV ohne Überschuss
    decisions = run(machine, 11, 131)  # der Überschuss reicht ab 11
    # die Start-Zeit zählt ab dem Wechsel, obwohl der Überschuss dort noch nicht reichte
    assert not decisions[129].charge
    assert decisions[130] == ModeDecision(True, "pv_surplus")


def test_mode_change_lifts_the_pause_after_force_stop() -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(Effective.NOW))
    machine.force_stop(at(10))
    assert not machine.step(at(11), inp(Effective.NOW)).charge
    assert machine.step(at(12), inp(Effective.BOOST)) == ModeDecision(True, None)
    assert machine.step(at(13), inp(Effective.BOOST)) == ModeDecision(True, None)


def test_mode_change_forgets_the_pause_after_a_stop() -> None:
    machine = ModeMachine(TIMERS)
    run(machine, 0, 121)
    run(machine, 121, 721, enough=False)  # Stopp bei 720, die Pause liefe bis 1320
    machine.step(at(730), inp(Effective.NOW))
    machine.step(at(740), inp(enough=False))  # zurück auf PV
    decisions = run(machine, 741, 861)
    assert not any(d.charge for s, d in decisions.items() if s < 860)
    assert decisions[860] == ModeDecision(True, "pv_surplus")


def test_mode_change_forgets_the_minimum_run() -> None:
    machine = ModeMachine(TIMERS)
    run(machine, 0, 121)  # PV startet bei 120, die Mindestlaufzeit liefe bis 720
    machine.step(at(130), inp(Effective.NOW))
    assert machine.step(at(131), inp(Effective.PV)).charge
    decisions = run(machine, 132, 721, enough=False)
    assert min(s for s, d in decisions.items() if not d.charge) < 720


# --- Regel 7: gleicher Modus ----------------------------------------------------------------------


def test_off_stays_off() -> None:
    decisions = run(ModeMachine(TIMERS), 0, 20, mode=Effective.OFF, observed=True)
    assert set(decisions.values()) == {ModeDecision(False, None)}


@pytest.mark.parametrize("mode", ALWAYS_ON)
def test_always_on_modes_stay_on_without_surplus(mode) -> None:
    decisions = run(ModeMachine(TIMERS), 0, 20, mode=mode, enough=False)
    assert set(decisions.values()) == {ModeDecision(True, None)}


@pytest.mark.parametrize("mode", ALWAYS_ON)
def test_force_stop_holds_every_always_on_mode_back(mode) -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(mode))
    machine.force_stop(at(10))
    assert not machine.charging
    assert machine.step(at(609), inp(mode)) == ModeDecision(False, None)
    assert machine.step(at(610), inp(mode)) == ModeDecision(True, None)
    assert machine.charging


def test_force_stop_holds_pv_back_for_the_minimum_pause() -> None:
    machine = ModeMachine(TIMERS)
    run(machine, 0, 121)  # PV lädt seit 120
    machine.force_stop(at(200))
    assert not machine.charging
    decisions = run(machine, 201, 801)  # der Überschuss reicht durchgehend
    assert not any(d.charge for s, d in decisions.items() if s < 800)
    assert decisions[800] == ModeDecision(True, "pv_surplus")


def test_a_gap_in_the_surplus_restarts_the_start_time() -> None:
    machine = ModeMachine(TIMERS)
    run(machine, 0, 100)
    machine.step(at(100), inp(enough=False))
    decisions = run(machine, 101, 222)
    assert not any(d.charge for s, d in decisions.items() if s < 221)
    assert decisions[221] == ModeDecision(True, "pv_surplus")


def test_a_gap_in_the_shortfall_restarts_the_stop_time() -> None:
    machine = ModeMachine(TIMERS)
    run(machine, 0, 200, enough=False, observed=True)
    machine.step(at(200), inp(enough=True, observed=True))
    decisions = run(machine, 201, 502, enough=False, observed=True)
    assert all(d.charge for s, d in decisions.items() if s < 501)
    assert decisions[501] == ModeDecision(False, "pv_surplus")


def test_unknown_surplus_never_starts_and_counts_as_too_little_to_stop() -> None:
    idle = run(ModeMachine(TIMERS), 0, 400, enough=None)
    assert set(idle.values()) == {ModeDecision(False, None)}
    decisions = run(ModeMachine(TIMERS), 0, 301, enough=None, observed=True)
    assert all(d.charge for s, d in decisions.items() if s < 300)
    assert decisions[300] == ModeDecision(False, "pv_surplus")


def test_reason_names_only_the_start_and_the_stop() -> None:
    machine = ModeMachine(TIMERS)
    decisions = run(machine, 0, 200) | run(machine, 200, 721, enough=False)  # Start 120, Stopp 720
    assert {s for s, d in decisions.items() if d.reason is not None} == {120, 720}
    assert {d.reason for d in decisions.values()} == {None, "pv_surplus"}


def test_each_pv_timer_counts_by_itself() -> None:
    machine = ModeMachine(ODD)
    assert run(machine, 0, 11)[10] == ModeDecision(True, "pv_surplus")
    stopping = run(machine, 11, 111, enough=False)  # 20 s darunter, aber erst nach 100 s Laufzeit
    assert all(d.charge for s, d in stopping.items() if s < 110)
    assert stopping[110] == ModeDecision(False, "pv_surplus")
    starting = run(machine, 111, 161)  # nach 10 s genug Überschuss, aber erst nach 50 s Pause
    assert not any(d.charge for s, d in starting.items() if s < 160)
    assert starting[160] == ModeDecision(True, "pv_surplus")


def test_the_pause_after_force_stop_is_the_minimum_pause() -> None:
    machine = ModeMachine(ODD)
    machine.step(at(0), inp(Effective.NOW))
    machine.force_stop(at(10))
    assert not machine.step(at(59), inp(Effective.NOW)).charge
    assert machine.step(at(60), inp(Effective.NOW)).charge


def test_timers_are_inclusive_and_may_be_zero() -> None:
    machine = ModeMachine(ModeTimers(0.0, 0.0, 0.0, 0.0))
    assert machine.step(at(0), inp()) == ModeDecision(False, None)
    assert machine.step(at(1), inp()) == ModeDecision(True, "pv_surplus")
    assert machine.step(at(2), inp(enough=False)) == ModeDecision(False, "pv_surplus")
    assert machine.step(at(3), inp()) == ModeDecision(True, "pv_surplus")
