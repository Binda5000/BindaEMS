"""Eigenschaften des 15-min-Wächters, Teil 1 (Design 6.1, 6.2; Präzisierungen 9, 10, 12, 14).

hypothesis erzeugt Energien, Zeitpunkte, Ziele und Verläufe von Schritten. Die Orakel rechnen aus
der Definition noch einmal, nicht schrittweise wie der Code:

- das Budget ``P_zul``: immer endlich und gleich ``(Ziel · 250 − E) · 3600 / max(1, Rest) − Marge``
- beliebige Verläufe von Schritten, auch mit Lücken, fehlender Viertelstunde, Werten, die keine
  Zahlen sind, und einer Wanduhr, die springt: Der Wächter wirft nie, die Werte in Health sind
  endlich und nie negativ, wo sie das nicht sein dürfen, und
  - „lückenhaft“ gilt in derselben Viertelstunde, bis sie endet,
  - „überschritten“ gilt in derselben Viertelstunde, bis sie endet, und nur dann steht der Alarm,
  - eine neue Viertelstunde beginnt nicht überschritten.
"""

import math
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.peak_guard import NO_LIMITS, PeakCar, PeakGuard, QuarterObs
from bindaems.shared.config import ControlConfig
from bindaems.shared.timeutil import SLOT, slot_end, slot_start

QUARTER = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)  # 18:00 Wiener Zeit


def at(seconds: float) -> datetime:
    return QUARTER + timedelta(seconds=seconds)


# --- Budget ---------------------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(
    energy_wh=st.floats(0.0, 10000.0),
    millis=st.integers(0, 899_999),
    target_kw=st.floats(1.0, 50.0),
    margin_kw=st.floats(0.0, 2.0),
)
@example(energy_wh=1000.0, millis=300_000, target_kw=8.0, margin_kw=0.3)  # 5700 W
@example(energy_wh=0.0, millis=899_999, target_kw=1.0, margin_kw=2.0)  # Rest unter 1 s
@example(energy_wh=10000.0, millis=0, target_kw=1.0, margin_kw=0.0)  # weit über dem Budget
def test_budget_is_finite_and_follows_the_formula(energy_wh, millis, target_kw, margin_kw) -> None:
    second = millis / 1000
    control = ControlConfig(peak_target_kw=target_kw, peak_margin_kw=margin_kw)
    guard = PeakGuard(control, reserve_soc_pct=20.0)
    # Ein Schritt alle 10 s hält die Viertelstunde frei von Lücken; der letzte trägt die Energie.
    moments = [float(moment) for moment in range(0, 900, 10) if moment < second] + [second]
    limits = NO_LIMITS
    for moment in moments:
        now = at(moment)
        quarter = QuarterObs(slot_start(now), energy_wh if moment == second else 0.0, 0.0, {})
        limits = guard.step(now, quarter, (), 0.0, 0.0, 60.0)

    expected_w = (target_kw * 250 - energy_wh) * 3600 / max(1.0, 900 - second) - margin_kw * 1000
    assert guard.health()["state"] == "active"
    assert limits.budget_w is not None
    assert math.isfinite(limits.budget_w)
    assert limits.budget_w == pytest.approx(expected_w, rel=1e-9, abs=1e-6)
    assert guard.health()["budget_kw"] * 1000 == pytest.approx(expected_w, rel=1e-9, abs=1e-6)


# --- Verläufe -------------------------------------------------------------------------------------

EGOLF_BOOST = PeakCar("egolf", "evcs", Effective.BOOST, 0, 3600.0, 3600.0, 1380.0)

# Ein Schritt: Sekunden seit dem vorigen (negativ: die Wanduhr springt zurück), die Viertelstunde
# (richtig, fehlend oder die eines anderen Slots), Bilanz, Energie bisher und Netzanteil der
# Wallbox in Boost. Die Werte, die keine Zahlen sind, kommen vor.
STEPS = st.lists(
    st.tuples(
        st.sampled_from([-3600, -30, -1, 0, 1, 1, 1, 2, 9, 10, 11, 25, 300, 900, 3600]),
        st.sampled_from(["right", "right", "right", "missing", "other slot"]),
        st.sampled_from([None, 0.0, 1500.0, 9000.0, 12300.0, 12300.0, 12300.0, math.nan]),
        st.sampled_from([0.0, 500.0, 1000.0, 1000.0, 2500.0, 5000.0, math.nan]),
        st.sampled_from([0.0, 1000.0, 3600.0, math.nan]),
    ),
    min_size=1,
    max_size=40,
)


# Überschritten, dann eine Lücke (11 s ohne Bilanz), dann die nächste Viertelstunde; der Zufall
# kommt selten auf 60 s ununterbrochen über dem Ziel.
OVER = (9, "right", 12300.0, 1000.0, 0.0)
EXCEEDED_THEN_GAP = [(0, "right", 12300.0, 1000.0, 0.0), *[OVER] * 7]
EXCEEDED_THEN_GAP += [(11, "right", None, 1000.0, 0.0), (900, "right", 12300.0, 0.0, 0.0)]
# Überschritten, dann ein Schritt ohne Viertelstunde, dann Sprünge der Uhr zurück.
EXCEEDED_THEN_MISSING = [(0, "right", 12300.0, 1000.0, 0.0), *[OVER] * 7]
EXCEEDED_THEN_MISSING += [(1, "missing", None, 0.0, 0.0), (1, "right", 12300.0, 1000.0, 0.0)]
EXCEEDED_THEN_MISSING += [(-30, "right", 12300.0, 1000.0, 0.0), (-3600, "right", 0.0, 0.0, 0.0)]


def observation(kind, now, grid_w, wh, share) -> QuarterObs | None:
    """Die Viertelstunde, die ein Schritt bekommt: richtig, fehlend oder die eines anderen Slots."""
    if kind == "missing":
        return None
    start = slot_start(now) if kind == "right" else slot_start(now) - SLOT
    return QuarterObs(start, wh, grid_w, {"evcs": share})


@settings(max_examples=300, deadline=None)
@given(STEPS)
@example(EXCEEDED_THEN_GAP)
@example(EXCEEDED_THEN_MISSING)
def test_any_course_of_steps_keeps_the_guard_consistent(steps) -> None:
    control = ControlConfig(peak_target_kw=8.0)
    guard = PeakGuard(control, reserve_soc_pct=20.0)
    now = QUARTER
    before = None  # (Zeitpunkt, Zustand, Alarme, Viertelstunde brauchbar) des vorigen Schritts
    for seconds, kind, grid_w, wh, share in steps:
        now += timedelta(seconds=seconds)
        quarter = observation(kind, now, grid_w, wh, share)
        usable = kind == "right" and math.isfinite(wh)
        limits = guard.step(now, quarter, [EGOLF_BOOST], 0.0, 0.0, 60.0)  # darf nie werfen
        health, alarms = guard.health(), guard.alarms()
        state = health["state"]

        assert state in {"gap", "active", "exceeded"}
        assert health["quarter_end"] == slot_end(now).isoformat()
        assert bool(alarms) == (state == "exceeded")
        assert limits.caps_w == {}  # Drosselung und Start-Sperre folgen in Task 10
        assert limits.no_start == frozenset()
        for key in ("energy_kwh", "projected_kw", "budget_kw", "reduction_kw"):
            assert health[key] is None or math.isfinite(health[key])
        for key in ("energy_kwh", "reduction_kw"):
            assert health[key] is None or health[key] >= 0.0
        if limits.budget_w is not None:  # der SOC liegt über der Schwelle: das Budget ist P_zul
            assert health["budget_kw"] * 1000 == pytest.approx(limits.budget_w)
        if state == "gap":  # ein Schritt ohne Rechnung hat weder Werte noch Grenzen
            assert limits == NO_LIMITS
            assert [health[key] for key in ("energy_kwh", "projected_kw")] == [None, None]
            assert [health[key] for key in ("budget_kw", "reduction_kw")] == [None, None]
        if state == "active":  # ein aktiver Schritt rechnet: Energie, Budget und Grenzen sind da
            assert health["energy_kwh"] is not None
            assert health["budget_kw"] is not None
            assert limits.budget_w is not None  # der SOC 60 % liegt über der Schwelle

        if before is not None:
            then, was, then_alarms, then_usable = before
            if slot_start(now) != slot_start(then):
                assert state != "exceeded"  # eine neue Viertelstunde beginnt nicht überschritten
            else:
                if was == "exceeded":
                    assert state == "exceeded"
                    assert alarms == then_alarms
                if was == "gap" and then_usable and usable:  # die Lücke gilt bis zum Ende
                    assert state == "gap"
        before = (now, state, alarms, usable)
