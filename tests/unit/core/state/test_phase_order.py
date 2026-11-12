from datetime import datetime, timedelta

import pytest
from tests.helpers import T0, snap
from tests.unit.core.state.conftest import BASE

from bindaems.core.state.derived import derive
from bindaems.core.state.phase_order import (
    DECISIVE_SAMPLES,
    IDENTITY,
    NEG_W,
    OFF_SHARE,
    ON_SHARE,
    PERMUTATIONS,
    TOL_W,
    VOTE_MAX_AGE,
    MultiPhaseCheck,
    PhaseOrderMonitor,
    PhaseSample,
    Votes,
    WallboxPhaseCheck,
    consumption_w,
    phase_sample,
)
from bindaems.shared.domain import Severity


def sample(grid, ac_in, ac_out, pv=(0, 0, 0)) -> PhaseSample:
    """Messwerte je Phase in der Reihenfolge L1, L2, L3."""

    def per_phase(values) -> dict:
        return {p: float(v) for p, v in zip(IDENTITY, values, strict=True)}

    return PhaseSample(per_phase(grid), per_phase(ac_in), per_phase(ac_out), per_phase(pv))


class Clock:
    """Zeit der Zyklen: jeder Aufruf geht ``step`` weiter (Vorgabe 1 s je Zyklus)."""

    def __init__(self) -> None:
        self.now = T0

    def __call__(self, step: timedelta = timedelta(seconds=1)) -> datetime:
        self.now += step
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


# Lage vom 4.11. 12:00: der Multi hängt gegenüber dem Netzzähler rotiert
MIDDAY = sample(grid=(0, 6000, -2000), ac_in=(3400, -3500, 0), ac_out=(0, 0, 0), pv=(300, 0, 0))
# dieselbe Lage wie MIDDAY, aber mit Multi-Phasen, die zum Zähler passen
FIXED = sample(grid=(0, 6000, -2000), ac_in=(0, 3400, -3500), ac_out=(0, 0, 0), pv=(300, 0, 0))


def test_constants_and_permutations():
    assert (NEG_W, TOL_W, DECISIVE_SAMPLES, ON_SHARE, OFF_SHARE) == (500, 200, 300, 0.8, 0.2)
    assert timedelta(minutes=60) == VOTE_MAX_AGE
    assert PERMUTATIONS[0] == IDENTITY
    assert len(set(PERMUTATIONS)) == 6
    assert all(sorted(p) == ["L1", "L2", "L3"] for p in PERMUTATIONS)


def test_votes_hysteresis(clock):
    votes: Votes[None] = Votes(size=10)
    for _ in range(9):
        votes.add(clock(), True, None)
    assert not votes.active  # Liste noch nicht voll
    votes.add(clock(), True, None)
    assert votes.active
    for _ in range(5):  # 50 % „falsch“: bleibt
        votes.add(clock(), False, None)
    assert votes.active
    for _ in range(3):  # 20 % „falsch“: geht
        votes.add(clock(), False, None)
    assert not votes.active


def test_votes_age_out(clock):
    votes: Votes[str] = Votes(size=10)
    for i in range(9):
        votes.add(clock(), True, f"alt {i}")
    clock(VOTE_MAX_AGE)  # die neun Stimmen sind jetzt älter als VOTE_MAX_AGE
    votes.add(clock(), True, "neu")
    assert not votes.active  # nur eine Stimme in der Liste: nicht voll
    assert votes.bad_payloads() == ["neu"]


def test_votes_cleared_when_clock_goes_back(clock):
    votes: Votes[None] = Votes(size=10)
    for _ in range(9):
        votes.add(clock(), True, None)
    clock(-timedelta(days=1))  # Uhr springt zurück
    votes.add(clock(), True, None)
    assert not votes.active  # alte Stimmen verworfen, Liste nicht voll
    assert len(votes.bad_payloads()) == 1


def test_consumption_identity_matches_derive_formula():
    assert consumption_w(MIDDAY, IDENTITY) == {"L1": -3100.0, "L2": 9500.0, "L3": -2000.0}


def test_rotated_multi_fits():
    # Multi-L1 auf Netz-L2, Multi-L2 auf Netz-L3, Multi-L3 auf Netz-L1
    assert min(consumption_w(MIDDAY, ("L3", "L1", "L2")).values()) >= -TOL_W


def test_alarm_after_full_window_with_suggestion(clock):
    # 300 entscheidende Zyklen am Stück (1 s je Zyklus): Alarm wie bisher
    check = MultiPhaseCheck()
    texts = [check.update(MIDDAY, clock()) for _ in range(DECISIVE_SAMPLES)]
    assert texts[-2] is None
    assert texts[-1] == (
        "Phasen von Multi und Netzzähler passen nicht zusammen: Der Verbrauch auf L1 wäre "
        "rechnerisch -3100 W. Passend wäre: Multi-L1 am Netz auf L2, Multi-L2 auf L3, "
        "Multi-L3 auf L1."
    )


def test_no_suggestion_when_no_permutation_fits(clock):
    # 3 kW Erzeugung am Netz, die im Abgleich fehlt; der Multi reicht nur durch
    unmetered = sample(grid=(-3000, 500, 500), ac_in=(1000, 0, 0), ac_out=(1000, 0, 0))
    check = MultiPhaseCheck()
    for _ in range(DECISIVE_SAMPLES):
        text = check.update(unmetered, clock())
    assert text == (
        "Phasen von Multi und Netzzähler passen nicht zusammen: Der Verbrauch auf L1 wäre "
        "rechnerisch -3000 W. Keine andere Zuordnung der Multi-Phasen passt; fehlt eine "
        "Erzeugung im Abgleich?"
    )


def test_load_step_skew_gives_no_alarm(clock):  # Review Focus 1
    check = MultiPhaseCheck()
    # entscheidend und 1:1 passend: der Multi lädt auf L1 aus dem Netz
    good = sample(grid=(3300, 300, 300), ac_in=(3000, 0, 0), ac_out=(0, 0, 0))
    for i in range(2 * DECISIVE_SAMPLES):
        text = check.update(MIDDAY if i % 10 < 3 else good, clock())
    assert text is None


def test_missing_value_does_not_count(clock):  # Review Focus 2
    check = MultiPhaseCheck()
    for _ in range(DECISIVE_SAMPLES - 1):
        check.update(MIDDAY, clock())
    assert check.update(None, clock()) is None
    assert check.fits_identity is None
    assert check.update(MIDDAY, clock()) is not None


def test_symmetric_operation_is_never_decisive(clock):  # Review Focus 3
    check = MultiPhaseCheck()
    charging = sample(grid=(2500, 2500, 2500), ac_in=(2300, 2300, 2300), ac_out=(100, 100, 100))
    kettle = sample(grid=(300, 2400, 300), ac_in=(2100, 0, 0), ac_out=(2100, 0, 0))
    for _ in range(2 * DECISIVE_SAMPLES):
        assert check.update(charging, clock()) is None
        assert check.update(kettle, clock()) is None


def test_alarm_ends_after_wiring_fixed(clock):  # Review Focus 4
    check = MultiPhaseCheck()
    for _ in range(DECISIVE_SAMPLES):
        check.update(MIDDAY, clock())
    texts = [check.update(FIXED, clock()) for _ in range(DECISIVE_SAMPLES)]
    assert texts[0] is not None  # hält zwischen 20 und 80 %
    assert texts[int(0.8 * DECISIVE_SAMPLES)] is None  # höchstens 20 % „falsch“
    assert check.fits_identity is True


def test_alarm_text_fixed_while_active(clock):
    # Der Text nennt Phase und Wert vom Einschalten und bleibt fest (wie peak.exceeded)
    check = MultiPhaseCheck()
    for _ in range(DECISIVE_SAMPLES):
        text = check.update(MIDDAY, clock())
    assert text is not None and "-3100 W" in text
    worse = sample(grid=(0, 6000, -2000), ac_in=(3400, -3500, 0), ac_out=(0, 0, 0), pv=(500, 0, 0))
    assert consumption_w(worse, IDENTITY)["L1"] == -2900.0
    assert check.update(worse, clock()) == text


def test_sparse_bad_cycles_age_out(clock):
    # ein „falsch“-Zyklus alle 20 s über 2 h, z. B. Lastsprünge bei fast gleicher Multi-Leistung:
    # in 60 min kommen nie 300 entscheidende Zyklen zusammen
    check = MultiPhaseCheck()
    for _ in range(360):
        assert check.update(MIDDAY, clock(timedelta(seconds=20))) is None


def test_alarm_ends_after_fix_once_old_votes_aged_out(clock):
    check = MultiPhaseCheck()
    for _ in range(DECISIVE_SAMPLES):
        check.update(MIDDAY, clock())
    clock(timedelta(hours=2))  # Elektriker war da; die alten Stimmen sind zu alt
    texts = [check.update(FIXED, clock()) for _ in range(DECISIVE_SAMPLES)]
    assert texts[-2] is not None  # Liste noch nicht voll: Stand bleibt
    assert texts[-1] is None


def test_sparse_good_cycles_keep_alarm(clock):
    check = MultiPhaseCheck()
    for _ in range(DECISIVE_SAMPLES):
        text = check.update(MIDDAY, clock())
    assert text is not None
    # 300 passende Zyklen über 3 h verteilt: je Stunde nur 100, die Liste wird nie voll
    for _ in range(DECISIVE_SAMPLES):
        assert check.update(FIXED, clock(timedelta(seconds=36))) == text


def test_tie_prefers_cyclic_rotation(clock):
    # Anlage wirklich rotiert (Multi-L1 auf Netz-L2, L2 auf L3, L3 auf L1); nur Multi-L1 lädt
    # mit 3400 W, Haus 300 W je Phase: Tausch L1/L2 und Rotation passen gleich oft
    rotated = sample(grid=(300, 3700, 300), ac_in=(3400, 0, 0), ac_out=(0, 0, 0))
    for swap_or_rotation in (("L2", "L1", "L3"), ("L3", "L1", "L2")):
        assert min(consumption_w(rotated, swap_or_rotation).values()) >= -TOL_W
    check = MultiPhaseCheck()
    for _ in range(DECISIVE_SAMPLES):
        text = check.update(rotated, clock())
    assert text is not None
    assert text.endswith("Passend wäre: Multi-L1 am Netz auf L2, Multi-L2 auf L3, Multi-L3 auf L1.")


def test_alarm_text_from_last_bad_cycle(clock):
    check = MultiPhaseCheck()
    for _ in range(DECISIVE_SAMPLES - 1):
        check.update(MIDDAY, clock())
    # entscheidend und 1:1 passend: der Multi lädt auf L1 aus dem Netz; schaltet den Alarm ein
    good = sample(grid=(3300, 300, 300), ac_in=(3000, 0, 0), ac_out=(0, 0, 0))
    text = check.update(good, clock())
    assert text is not None
    assert "Der Verbrauch auf L1 wäre rechnerisch -3100 W." in text


def test_phase_sample_sums_pv_and_reads_signals(cfg):
    sampled = phase_sample(snap(BASE), cfg)
    assert sampled == PhaseSample(
        grid_w=dict.fromkeys(IDENTITY, 1000.0),
        ac_in_w=dict.fromkeys(IDENTITY, 500.0),
        ac_out_w=dict.fromkeys(IDENTITY, 300.0),
        pv_w=dict.fromkeys(IDENTITY, 1000.0),
    )


def test_phase_sample_none_on_missing_signal(cfg):
    base_without_l2_out = {k: v for k, v in BASE.items() if k != "vebus.l2.ac_out_power_w"}
    assert phase_sample(snap(base_without_l2_out), cfg) is None


# Multi stimmt, das Auto lädt auf Netz-L2 und L3, Haus 200 W je Phase
EGOLF_OK = sample(grid=(200, 1810, 1810), ac_in=(0, 0, 0), ac_out=(0, 0, 0))


def test_evcs_on_other_phases_alarm_names_phase_map(clock):
    check = WallboxPhaseCheck("evcs", voltage_v=230.0)
    for _ in range(DECISIVE_SAMPLES):
        text = check.update(EGOLF_OK, {"L1": 7.0, "L2": 7.0, "L3": 0.0}, clock())
    assert text == (
        "Phasen der Wallbox „evcs“ passen nicht zum Netzzähler: Das Auto lädt rechnerisch auf "
        "L2 und L3, die Zuordnung nennt L1 und L2. Abhilfe: phase_map in config.yaml mit L2 und "
        "L3 beginnen lassen."
    )


def test_twc_alarm_names_phase_map(clock):
    # auch der Wall Connector bildet seine Klemmen A, B, C über phase_map auf die Netzphasen ab
    check = WallboxPhaseCheck("twc", voltage_v=230.0)
    one_phase = sample(grid=(200, 200, 3420), ac_in=(0, 0, 0), ac_out=(0, 0, 0))
    for _ in range(DECISIVE_SAMPLES):
        text = check.update(one_phase, {"L1": 14.0, "L2": 0.0, "L3": 0.0}, clock())
    assert text == (
        "Phasen der Wallbox „twc“ passen nicht zum Netzzähler: Das Auto lädt rechnerisch auf L3, "
        "die Zuordnung nennt L1. Abhilfe: phase_map in config.yaml mit L3 beginnen lassen."
    )


def test_nearly_equal_currents_are_not_decisive(clock):
    # Wall Connector, richtig angeschlossen, misst 15,9/16,0/16,1 A; der Netzzähler zeigt schon
    # 8 A weniger je Phase (Schritt nach unten, Ströme der Wallbox hinken nach)
    check = WallboxPhaseCheck("twc", voltage_v=230.0)
    currents = {"L1": 15.9, "L2": 16.0, "L3": 16.1}
    lagged = sample(
        grid=tuple(200 + 230.0 * (currents[p] - 8.0) for p in IDENTITY),
        ac_in=(0, 0, 0),
        ac_out=(0, 0, 0),
    )
    for _ in range(400):
        assert check.update(lagged, currents, clock()) is None


def test_three_phase_charging_is_never_decisive(clock):
    check = WallboxPhaseCheck("evcs", voltage_v=230.0)
    # das Haus hängt mit 3 kW an L1, die Last ist auf allen Phasen gleich: nichts zu unterscheiden
    house = sample(grid=(3000 + 1610, 1610, 1610), ac_in=(0, 0, 0), ac_out=(0, 0, 0))
    for _ in range(2 * DECISIVE_SAMPLES):
        assert check.update(house, {"L1": 7.0, "L2": 7.0, "L3": 7.0}, clock()) is None


def test_wallbox_check_ignores_cycles_without_basis(clock):
    check = WallboxPhaseCheck("evcs", voltage_v=230.0)
    currents = {"L1": 7.0, "L2": 7.0, "L3": 0.0}
    for _ in range(DECISIVE_SAMPLES - 1):
        check.update(EGOLF_OK, currents, clock())
    assert check.update(None, currents, clock()) is None  # Messwert fehlt
    assert check.update(EGOLF_OK, None, clock()) is None  # Ströme unbekannt
    assert check.update(EGOLF_OK, dict.fromkeys(IDENTITY, 0.0), clock()) is None  # lädt nicht
    assert check.update(MIDDAY, currents, clock()) is None  # 1:1 passt für den Multi nicht
    assert check.update(EGOLF_OK, currents, clock()) is not None


def test_wallbox_without_fitting_distribution_has_no_suggestion(clock):
    check = WallboxPhaseCheck("evcs", voltage_v=230.0)
    # nur eine Phase hat Last für ein Auto: keine Verteilung auf zwei Phasen passt
    unmetered = sample(grid=(0, 1610, 0), ac_in=(0, 0, 0), ac_out=(0, 0, 0))
    for _ in range(DECISIVE_SAMPLES):
        text = check.update(unmetered, {"L1": 7.0, "L2": 7.0, "L3": 0.0}, clock())
    assert text == (
        "Phasen der Wallbox „evcs“ passen nicht zum Netzzähler: Das Auto lädt rechnerisch nicht "
        "auf L1 und L2."
    )


def _cycle(cfg, grid, ac_in, pv_l1, evcs_w, now):
    values = (
        {f"grid.l{n}.power_w": float(g) for n, g in zip((1, 2, 3), grid, strict=True)}
        | {f"vebus.l{n}.ac_in_power_w": float(a) for n, a in zip((1, 2, 3), ac_in, strict=True)}
        | {f"vebus.l{n}.ac_out_power_w": 0.0 for n in (1, 2, 3)}
        | {"pv.huawei.l1.power_w": float(pv_l1), "pv.huawei.l2.power_w": 0.0}
        | {"pv.huawei.l3.power_w": 0.0, "grid.l1.voltage_v": 230.0}
        | {"wallbox.evcs.power_w": float(evcs_w), "wallbox.evcs.current_a": 16.0}
    )
    snapshot = snap(values)
    return snapshot, derive(snapshot, cfg), now


def test_monitor_skips_wallbox_while_multi_wrong(cfg):  # Review Focus 5
    monitor = PhaseOrderMonitor(cfg)
    # MIDDAY-Werte mit laufender EVCS (2 Phasen, 16 A): der Multi ist falsch angeschlossen
    for i in range(DECISIVE_SAMPLES):
        alarms = monitor.evaluate(
            *_cycle(cfg, (0, 6000, -2000), (3400, -3500, 0), 300, 7360, T0 + timedelta(seconds=i))
        )
    assert [a.id for a in alarms] == ["wiring.multi_phases"]
    # auch mit aktivem Alarm kommt kein Wallbox-Alarm
    alarms = monitor.evaluate(
        *_cycle(cfg, (0, 6000, -2000), (3400, -3500, 0), 300, 7360, T0 + timedelta(hours=1))
    )
    assert [a.id for a in alarms] == ["wiring.multi_phases"]


def test_monitor_keeps_wallbox_quiet_while_multi_alarm_active(cfg):  # Review Focus 5
    monitor = PhaseOrderMonitor(cfg)
    times = (T0 + timedelta(seconds=i) for i in range(2 * DECISIVE_SAMPLES))
    for _ in range(DECISIVE_SAMPLES):  # Multi falsch angeschlossen, EVCS lädt nicht
        alarms = monitor.evaluate(
            *_cycle(cfg, (0, 6000, -2000), (3400, -3500, 0), 300, 0, next(times))
        )
    assert [a.id for a in alarms] == ["wiring.multi_phases"]
    # Multi im Zyklus ohne Leistung (1:1 passt, nicht entscheidend), EVCS (Zuordnung L1, L2)
    # lädt rechnerisch auf L2 und L3: allein würde die Prüfung der Wallbox „falsch“ stimmen
    for _ in range(DECISIVE_SAMPLES):
        alarms = monitor.evaluate(*_cycle(cfg, (200, 3700, 3700), (0, 0, 0), 0, 7360, next(times)))
    assert [a.id for a in alarms] == ["wiring.multi_phases"]


def test_monitor_alarm_ids_severity_and_since(cfg):
    monitor = PhaseOrderMonitor(cfg)
    times = [T0 + timedelta(seconds=i) for i in range(DECISIVE_SAMPLES + 1)]
    for now in times[: DECISIVE_SAMPLES - 1]:
        assert monitor.evaluate(*_cycle(cfg, (0, 6000, -2000), (3400, -3500, 0), 300, 0, now)) == []
    (alarm,) = monitor.evaluate(
        *_cycle(cfg, (0, 6000, -2000), (3400, -3500, 0), 300, 0, times[DECISIVE_SAMPLES - 1])
    )
    assert alarm.id == "wiring.multi_phases"
    assert alarm.severity is Severity.WARNING
    assert alarm.since == times[DECISIVE_SAMPLES - 1]
    (again,) = monitor.evaluate(
        *_cycle(cfg, (0, 6000, -2000), (3400, -3500, 0), 300, 0, times[DECISIVE_SAMPLES])
    )
    assert again.since == times[DECISIVE_SAMPLES - 1]


def test_monitor_wallbox_alarm_id(cfg):
    monitor = PhaseOrderMonitor(cfg)
    # Multi unauffällig, EVCS (Zuordnung L1, L2; 16 A auf 2 Phasen) lädt rechnerisch auf L2 und L3
    for i in range(DECISIVE_SAMPLES):
        alarms = monitor.evaluate(
            *_cycle(cfg, (200, 3700, 3700), (0, 0, 0), 0, 7360, T0 + timedelta(seconds=i))
        )
    assert [a.id for a in alarms] == ["wiring.wallbox_phases.evcs"]
    assert alarms[0].severity is Severity.WARNING
