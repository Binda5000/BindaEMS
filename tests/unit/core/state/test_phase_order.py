from tests.helpers import snap
from tests.unit.core.state.conftest import BASE

from bindaems.core.state.phase_order import (
    DECISIVE_SAMPLES,
    IDENTITY,
    NEG_W,
    OFF_SHARE,
    ON_SHARE,
    PERMUTATIONS,
    TOL_W,
    MultiPhaseCheck,
    PhaseSample,
    Votes,
    consumption_w,
    phase_sample,
)


def sample(grid, ac_in, ac_out, pv=(0, 0, 0)) -> PhaseSample:
    """Messwerte je Phase in der Reihenfolge L1, L2, L3."""

    def per_phase(values) -> dict:
        return {p: float(v) for p, v in zip(IDENTITY, values, strict=True)}

    return PhaseSample(per_phase(grid), per_phase(ac_in), per_phase(ac_out), per_phase(pv))


# Lage vom 4.11. 12:00: der Multi hängt gegenüber dem Netzzähler rotiert
MIDDAY = sample(grid=(0, 6000, -2000), ac_in=(3400, -3500, 0), ac_out=(0, 0, 0), pv=(300, 0, 0))


def test_constants_and_permutations():
    assert (NEG_W, TOL_W, DECISIVE_SAMPLES, ON_SHARE, OFF_SHARE) == (500, 200, 300, 0.8, 0.2)
    assert PERMUTATIONS[0] == IDENTITY
    assert len(set(PERMUTATIONS)) == 6
    assert all(sorted(p) == ["L1", "L2", "L3"] for p in PERMUTATIONS)


def test_votes_hysteresis():
    votes = Votes(size=10)
    for _ in range(9):
        votes.add(True)
    assert not votes.active  # Liste noch nicht voll
    votes.add(True)
    assert votes.active
    for _ in range(5):  # 50 % „falsch“: bleibt
        votes.add(False)
    assert votes.active
    for _ in range(3):  # 20 % „falsch“: geht
        votes.add(False)
    assert not votes.active


def test_consumption_identity_matches_derive_formula():
    assert consumption_w(MIDDAY, IDENTITY) == {"L1": -3100.0, "L2": 9500.0, "L3": -2000.0}


def test_rotated_multi_fits():
    # Multi-L1 auf Netz-L2, Multi-L2 auf Netz-L3, Multi-L3 auf Netz-L1
    assert min(consumption_w(MIDDAY, ("L3", "L1", "L2")).values()) >= -TOL_W


def test_alarm_after_full_window_with_suggestion():
    check = MultiPhaseCheck()
    texts = [check.update(MIDDAY) for _ in range(DECISIVE_SAMPLES)]
    assert texts[-2] is None
    assert texts[-1] == (
        "Phasen von Multi und Netzzähler passen nicht zusammen: Der Verbrauch auf L1 wäre "
        "rechnerisch -3100 W. Passend wäre: Multi-L1 am Netz auf L2, Multi-L2 auf L3, "
        "Multi-L3 auf L1."
    )


def test_no_suggestion_when_no_permutation_fits():
    # 3 kW Erzeugung am Netz, die im Abgleich fehlt; der Multi reicht nur durch
    unmetered = sample(grid=(-3000, 500, 500), ac_in=(1000, 0, 0), ac_out=(1000, 0, 0))
    check = MultiPhaseCheck()
    for _ in range(DECISIVE_SAMPLES):
        text = check.update(unmetered)
    assert text == (
        "Phasen von Multi und Netzzähler passen nicht zusammen: Der Verbrauch auf L1 wäre "
        "rechnerisch -3000 W. Keine andere Zuordnung der Multi-Phasen passt; fehlt eine "
        "Erzeugung im Abgleich?"
    )


def test_load_step_skew_gives_no_alarm():  # Review Focus 1
    check = MultiPhaseCheck()
    # entscheidend und 1:1 passend: der Multi lädt auf L1 aus dem Netz
    good = sample(grid=(3300, 300, 300), ac_in=(3000, 0, 0), ac_out=(0, 0, 0))
    for i in range(2 * DECISIVE_SAMPLES):
        text = check.update(MIDDAY if i % 10 < 3 else good)
    assert text is None


def test_missing_value_does_not_count():  # Review Focus 2
    check = MultiPhaseCheck()
    for _ in range(DECISIVE_SAMPLES - 1):
        check.update(MIDDAY)
    assert check.update(None) is None
    assert check.fits_identity is None
    assert check.update(MIDDAY) is not None


def test_symmetric_operation_is_never_decisive():  # Review Focus 3
    check = MultiPhaseCheck()
    charging = sample(grid=(2500, 2500, 2500), ac_in=(2300, 2300, 2300), ac_out=(100, 100, 100))
    kettle = sample(grid=(300, 2400, 300), ac_in=(2100, 0, 0), ac_out=(2100, 0, 0))
    for _ in range(2 * DECISIVE_SAMPLES):
        assert check.update(charging) is None
        assert check.update(kettle) is None


def test_alarm_ends_after_wiring_fixed():  # Review Focus 4
    check = MultiPhaseCheck()
    for _ in range(DECISIVE_SAMPLES):
        check.update(MIDDAY)
    # dieselbe Lage wie MIDDAY, aber mit Multi-Phasen, die zum Zähler passen
    fixed = sample(grid=(0, 6000, -2000), ac_in=(0, 3400, -3500), ac_out=(0, 0, 0), pv=(300, 0, 0))
    texts = [check.update(fixed) for _ in range(DECISIVE_SAMPLES)]
    assert texts[0] is not None  # hält zwischen 20 und 80 %
    assert texts[int(0.8 * DECISIVE_SAMPLES)] is None  # höchstens 20 % „falsch“
    assert check.fits_identity is True


def test_alarm_text_fixed_while_active():
    # Der Text nennt Phase und Wert vom Einschalten und bleibt fest (wie peak.exceeded)
    check = MultiPhaseCheck()
    for _ in range(DECISIVE_SAMPLES):
        text = check.update(MIDDAY)
    assert text is not None and "-3100 W" in text
    worse = sample(grid=(0, 6000, -2000), ac_in=(3400, -3500, 0), ac_out=(0, 0, 0), pv=(500, 0, 0))
    assert consumption_w(worse, IDENTITY)["L1"] == -2900.0
    assert check.update(worse) == text


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
