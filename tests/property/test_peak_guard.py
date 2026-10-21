"""Eigenschaften des 15-min-Wächters (Design 6.1 bis 6.3; Präzisierungen 9 bis 12 und 14).

hypothesis erzeugt Energien, Zeitpunkte, Ziele, Autos und Verläufe von Schritten. Die Orakel rechnen
aus der Definition noch einmal, nicht schrittweise wie der Code:

- das Budget ``P_zul``: immer endlich und gleich ``(Ziel · 250 − E) · 3600 / max(1, Rest) − Marge``;
  für jede endliche Energie nie NaN oder ±∞ (Global Constraint ``grid_budget_w``), auch wenn die
  Rechnung überläuft
- die verlangte Senkung: endlich, auch wenn Basis und ``W`` es für sich sind und ihre Summe nicht,
  mit und ohne Budget für den Akku
- beliebige Verläufe von Schritten, auch mit Lücken, fehlender Viertelstunde, Werten, die keine
  Zahlen sind, und einer Wanduhr, die springt: Der Wächter wirft nie, die Werte in Health sind
  endlich und nie negativ, wo sie das nicht sein dürfen, und
  - „lückenhaft“ gilt in derselben Viertelstunde, bis sie endet,
  - „überschritten“ gilt in derselben Viertelstunde, bis sie endet, und nur dann steht der Alarm,
  - eine neue Viertelstunde beginnt nicht überschritten,
  - ein Auto in Boost bekommt nie eine Grenze und steht nie in der Start-Sperre.
- die Drosselung (``throttle``) mit beliebigen Autos, Grenzen und Senkungen: Sie hält die
  Reihenfolge von Design 6.3 ein (Stufen und darin die Priorität), lässt Boost und Aus aus, setzt
  keine Grenze unter den Boden und senkt zusammen nie mehr als verlangt, aber so viel, wie sie kann.
- beliebige Verläufe ohne Lücke mit Autos, die ihren Modus wechseln, und einem SOC, der dem Akku
  ein Budget gibt oder nicht: Eine Grenze steigt nie und überlebt keinen Wechsel des Modus, je
  Schritt hebt der Wächter höchstens eine auf, und zwischen zwei Änderungen liegen mindestens
  ``SETTLE_S``.

Die feste Grenze von Regel 2 ist ``CEILING_W`` (8000 W, wie in der Beispielkonfiguration).
"""

import math
import sys
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.peak_guard import (
    NO_LIMITS,
    SETTLE_S,
    Cap,
    PeakCar,
    PeakGuard,
    QuarterObs,
    throttle,
)
from bindaems.shared.config import ControlConfig
from bindaems.shared.timeutil import SLOT, slot_end, slot_start

QUARTER = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)  # 18:00 Wiener Zeit
CEILING_W = 8000.0  # die feste Grenze von Regel 2


def at(seconds: float) -> datetime:
    return QUARTER + timedelta(seconds=seconds)


def new_guard(control: ControlConfig) -> PeakGuard:
    """Ein Wächter mit USV-Reserve 20 % (Budget für den Akku über 25 % SOC) und ``CEILING_W``."""
    return PeakGuard(control, reserve_soc_pct=20.0, ceiling_w=CEILING_W)


# --- Budget ---------------------------------------------------------------------------------------


FINITE = st.floats(allow_nan=False, allow_infinity=False)


def run_to(
    second,
    energy_wh,
    *,
    target_kw=8.0,
    margin_kw=0.3,
    grid_w=0.0,
    wanted_w=0.0,
    base_w=0.0,
    soc=60.0,
):
    """Ein Wächter, der ab 18:00:00 alle 10 s einen Schritt bekam; der letzte bei ``second``.

    Ein Schritt alle 10 s hält die Viertelstunde frei von Lücken; der letzte trägt die Energie.
    Zurück kommen der Wächter und die Grenzen des letzten Schritts.
    """
    guard = new_guard(ControlConfig(peak_target_kw=target_kw, peak_margin_kw=margin_kw))
    moments = [float(moment) for moment in range(0, 900, 10) if moment < second] + [second]
    limits = NO_LIMITS
    for moment in moments:
        now = at(moment)
        quarter = QuarterObs(slot_start(now), energy_wh if moment == second else 0.0, grid_w, {})
        limits = guard.step(now, quarter, (), wanted_w, base_w, soc, target_kw=target_kw)
    return guard, limits


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
    guard, limits = run_to(second, energy_wh, target_kw=target_kw, margin_kw=margin_kw)

    expected_w = (target_kw * 250 - energy_wh) * 3600 / max(1.0, 900 - second) - margin_kw * 1000
    assert guard.health(QUARTER)["state"] == "active"
    assert limits.budget_w is not None
    assert math.isfinite(limits.budget_w)
    assert limits.budget_w == pytest.approx(expected_w, rel=1e-9, abs=1e-6)
    assert guard.health(QUARTER)["budget_kw"] * 1000 == pytest.approx(
        expected_w, rel=1e-9, abs=1e-6
    )


@settings(max_examples=100, deadline=None)
@given(
    energy_wh=st.floats(0.0, 3000.0),
    millis=st.integers(10_000, 899_999),
    first_kw=st.floats(1.0, 50.0),
    second_kw=st.floats(1.0, 50.0),
)
def test_the_budget_follows_the_target_of_the_step(energy_wh, millis, first_kw, second_kw) -> None:
    # Das Ziel wechselt innerhalb der Viertelstunde (Budget des Plans): P_zul rechnet mit dem Ziel
    # des Schritts, ohne Lücke.
    second = millis / 1000
    guard, _ = run_to(second - 10.0, 0.0, target_kw=first_kw)
    now = at(second)
    quarter = QuarterObs(slot_start(now), energy_wh, 0.0, {})
    limits = guard.step(now, quarter, (), 0.0, 0.0, 60.0, target_kw=second_kw)
    expected_w = (second_kw * 250 - energy_wh) * 3600 / max(1.0, 900 - second) - 300.0
    assert guard.health(QUARTER)["state"] == "active"
    assert guard.health(QUARTER)["target_kw"] == second_kw
    assert limits.budget_w == pytest.approx(expected_w, rel=1e-9, abs=1e-6)


@settings(max_examples=200, deadline=None)
@given(energy_wh=st.floats(0.0, sys.float_info.max), millis=st.integers(0, 899_999))
@example(energy_wh=1e306, millis=300_000)  # (2000 − E) · 3600 läuft über
@example(energy_wh=sys.float_info.max, millis=0)
def test_the_budget_is_never_nan_or_infinite(energy_wh, millis) -> None:
    # ``P_zul`` ist endlich, oder der Schritt rechnet nicht
    guard, limits = run_to(millis / 1000, energy_wh)
    assert limits.budget_w is None or math.isfinite(limits.budget_w)
    budget_kw = guard.health(QUARTER)["budget_kw"]
    assert budget_kw is None or math.isfinite(budget_kw)


@settings(max_examples=200, deadline=None)
@given(
    wanted_w=FINITE | st.none(),
    base_w=FINITE | st.none(),
    mean_w=st.floats(0.0, 1e5),
    energy_wh=st.floats(0.0, 10000.0),
    soc=st.sampled_from([60.0, 20.0]),  # mit und ohne Budget für den Akku
)
@example(wanted_w=1e308, base_w=1e308, mean_w=6000.0, energy_wh=1000.0, soc=60.0)  # Überlauf
@example(wanted_w=-1e308, base_w=-1e308, mean_w=6000.0, energy_wh=1000.0, soc=60.0)
@example(wanted_w=1e308, base_w=1e308, mean_w=6000.0, energy_wh=1000.0, soc=20.0)
def test_the_reduction_is_finite_for_any_finite_wanted_grid_and_base(
    wanted_w, base_w, mean_w, energy_wh, soc
) -> None:
    guard, _ = run_to(300, energy_wh, grid_w=mean_w, wanted_w=wanted_w, base_w=base_w, soc=soc)
    reduction_kw = guard.health(QUARTER)["reduction_kw"]
    assert reduction_kw is not None
    assert math.isfinite(reduction_kw)
    assert reduction_kw >= 0.0


# --- Verläufe -------------------------------------------------------------------------------------

EGOLF_BOOST = PeakCar("egolf", "evcs", Effective.BOOST, 0, 3600.0, 3600.0, 1380.0)
TESLA_NOW = PeakCar("tesla", "twc", Effective.NOW, 1, 11040.0, 11040.0, 3450.0)  # drosselbar

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
    guard = new_guard(ControlConfig(peak_target_kw=8.0))
    now = QUARTER
    before = None  # (Zeitpunkt, Zustand, Alarme, Viertelstunde brauchbar) des vorigen Schritts
    for seconds, kind, grid_w, wh, share in steps:
        now += timedelta(seconds=seconds)
        quarter = observation(kind, now, grid_w, wh, share)
        usable = kind == "right" and math.isfinite(wh)
        limits = guard.step(
            now, quarter, [EGOLF_BOOST, TESLA_NOW], 0.0, 0.0, 60.0, target_kw=8.0
        )  # wirft nie
        health, alarms = guard.health(now), guard.alarms()
        state = health["state"]

        assert state in {"gap", "active", "exceeded"}
        assert health["quarter_end"] == slot_end(now).isoformat()
        assert bool(alarms) == (state == "exceeded")
        assert set(limits.caps_w) <= {"tesla"}  # Boost nie
        assert all(3450.0 <= cap_w < 11040.0 for cap_w in limits.caps_w.values())  # über dem Boden
        assert limits.no_start <= {"tesla"}
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


# --- Drosselung -----------------------------------------------------------------------------------

# die Stufen in ihrer Reihenfolge, „Geplant“ nach Min+PV und vor Sofort (Plan 4b, Präzisierung 8)
LEVEL = {Effective.PV: 0, Effective.MIN_PV: 1, Effective.PLANNED: 2, Effective.NOW: 3}
POWER = st.floats(0.0, 11000.0)
TOLERANCE_W = 1e-6  # Rundung der Gleitkommazahlen


def floor_w(car: PeakCar) -> float:
    """Der Boden eines Autos: in PV-Überschuss 0 W, in Min+PV, Geplant und Sofort ``min_w``."""
    return 0.0 if car.mode is Effective.PV else car.min_w


def turn(car: PeakCar) -> tuple[int, int]:
    """Wann ein Auto an der Reihe ist: nach seiner Stufe, darin die niedrigste Priorität zuerst."""
    return LEVEL[car.mode], -car.rank


@st.composite
def throttle_cases(draw):
    """Bis zu sechs Autos mit eindeutigem Rang, zum Teil mit Grenze, und eine Senkung in W.

    Leistung und Netzanteil sind unabhängig voneinander, die Leistung ist auch unbekannt. Eine alte
    Grenze trägt den Modus ihres Autos und ist sonst beliebig, auch unter dem Boden: Die reine
    Rechnung verträgt jede.
    """
    ranks = draw(st.permutations(range(draw(st.integers(0, 6)))))
    cars, caps = [], {}
    for index, rank in enumerate(ranks):
        car = PeakCar(
            f"car{index}",
            f"wb{index}",
            draw(st.sampled_from(list(Effective))),
            rank,
            draw(POWER | st.none()),
            draw(st.just(0.0) | POWER),
            # min_a × U × Phasen, auch mit einer Spannung, deren Produkt nicht glatt aufgeht
            draw(
                st.sampled_from([1380.0, 2760.0, 3450.0, 4140.0, 7360.0])
                | st.floats(1000.0, 8000.0)
            ),
        )
        cars.append(car)
        if draw(st.booleans()):
            caps[car.vehicle] = Cap(draw(POWER), draw(st.floats(1.0, 11000.0)), car.mode)
    return cars, caps, draw(st.floats(-1000.0, 40000.0))


A = PeakCar("a", "twc", Effective.NOW, 0, 7000.0, 7000.0, 3450.0)
B = PeakCar("b", "evcs", Effective.MIN_PV, 1, 4000.0, 4000.0, 2760.0)
C = PeakCar("c", "x", Effective.PV, 2, 3000.0, 3000.0, 1380.0)
SMALL_C = PeakCar("c", "x", Effective.PV, 2, 3000.0, 600.0, 1380.0)  # wenig Netzanteil
FIRST = {
    "a": Cap(6240.0, 760.0, Effective.NOW),
    "b": Cap(2760.0, 1240.0, Effective.MIN_PV),
    "c": Cap(0.0, 3000.0, Effective.PV),
}
# 8 A · 229,7 V · 1 Phase: 6349,103 W − (6349,103 W − 1837,6 W) rundet auf 1837,5999999999995 W.
ODD_FLOOR = PeakCar("odd", "w", Effective.NOW, 0, 6349.103, 11000.0, 8 * 229.7)
# Der Tesla in Min+PV, bei 16 A um 300 W gedrosselt, lädt jetzt an seinem Boden von 5 A.
TESLA_AT_MIN = PeakCar("tesla", "twc", Effective.MIN_PV, 0, 3450.0, 3450.0, 3450.0)
TESLA_CAPPED = {"tesla": Cap(10740.0, 300.0, Effective.MIN_PV)}
EGOLF_NOW = PeakCar("egolf", "evcs", Effective.NOW, 1, 7360.0, 7360.0, 2760.0)


@settings(max_examples=500, deadline=None)
@given(throttle_cases())
@example(([A, B, C], {}, 5000.0))  # Design 6.3: 0 W, 2760 W und 6240 W
@example(([A, SMALL_C], {}, 3000.0))  # „c“ bleibt über seinem Boden, „a“ trägt den Rest
@example(([A, B, C], FIRST, 1000.0))  # von den Grenzen aus: nur „a“ kann noch sinken
@example(([ODD_FLOOR], {}, 40000.0))  # bis zum Boden, nicht ein Bit darunter
@example(([TESLA_AT_MIN, EGOLF_NOW], TESLA_CAPPED, 300.0))  # Ausgang: die Leistung, der Boden
def test_throttling_keeps_the_order_and_leaves_boost_alone(case) -> None:
    # Ein Auto sinkt höchstens um seinen Netzanteil (Präzisierung 11). Wer vor einem gesenkten Auto
    # an der Reihe ist, ist daher an seinem Boden oder um seinen ganzen Netzanteil gesenkt, nicht
    # unbedingt an seinem Boden, wie die Skizze des Tests in Task 10 des Plans sagt.
    cars, before, amount_w = case
    after = throttle(cars, before, amount_w)

    # der Ausgang je Auto: das kleinere von Grenze und Leistung, ohne bekannte Leistung die Grenze,
    # ohne Grenze die Leistung
    start = {}
    for car in cars:
        cap = before.get(car.vehicle)
        if cap is None or car.power_w is None:
            start[car.vehicle] = car.power_w if cap is None else cap.cap_w
        else:
            start[car.vehicle] = min(cap.cap_w, car.power_w)
    cut = {}  # die Senkung dieses Schritts je Auto
    assert set(before) <= set(after)  # Drosseln hebt keine Grenze auf
    for car in cars:
        old, new = before.get(car.vehicle), after.get(car.vehicle)
        if new == old:
            continue
        assert car.mode in LEVEL  # nie Boost oder Aus
        assert start[car.vehicle] is not None
        # Keine Grenze unter dem Boden. Gleich dem Ausgang ist sie nur, wenn die Senkung unter der
        # Rundung liegt (ein Netzanteil von 1e-308 W).
        assert floor_w(car) <= new.cap_w <= start[car.vehicle]
        assert new.mode is car.mode  # die Grenze gilt im Modus, in dem sie entstand
        cut[car.vehicle] = start[car.vehicle] - new.cap_w
        assert cut[car.vehicle] <= car.grid_w + TOLERANCE_W  # höchstens sein Netzanteil
        assert new.cut_w == pytest.approx(cut[car.vehicle] + (0.0 if old is None else old.cut_w))
    assert sum(cut.values()) <= max(0.0, amount_w) + TOLERANCE_W  # nie mehr als verlangt

    def exhausted(car: PeakCar) -> bool:
        """Ob das Auto nicht weiter sinken kann.

        Es hat keinen Netzanteil oder keinen Ausgang, oder es ist an seinem Boden (oder darunter)
        oder um seinen ganzen Netzanteil gesenkt.
        """
        begin = start[car.vehicle]
        if car.grid_w <= 0.0 or begin is None:
            return True
        done_w = cut.get(car.vehicle, 0.0)
        return begin - done_w <= floor_w(car) + TOLERANCE_W or done_w >= car.grid_w - TOLERANCE_W

    throttleable = [car for car in cars if car.mode in LEVEL]
    for car in throttleable:
        if car.vehicle in cut:  # Wer vor ihm an der Reihe ist, kann nicht weiter sinken.
            assert all(exhausted(other) for other in throttleable if turn(other) < turn(car))
    if sum(cut.values()) < amount_w - TOLERANCE_W:  # weniger als verlangt: Keins kann mehr sinken.
        assert all(exhausted(car) for car in throttleable)


# Ein Verlauf ohne Lücke ab 18:00:00 (höchstens 800 s): Sekunden seit dem vorigen Schritt, ``W``,
# der SOC (über der Schwelle des Budgets, darunter oder unbekannt) und der Stand zweier Autos
# (Modus, Leistung, Netzanteil). Mit E 0 Wh und Mittel 0 W steigt ``P_zul`` von 7700 W an: Erst
# drosselt der Wächter, später hebt er auf.
CAR_STATE = st.tuples(st.sampled_from(list(Effective)), POWER | st.none(), POWER)
COURSE = st.lists(
    st.tuples(
        st.sampled_from([1, 1, 1, 2, 5, 10]),
        st.floats(0.0, 20000.0),
        st.sampled_from([60.0, 60.0, 20.0, None]),
        CAR_STATE,
        CAR_STATE,
    ),
    min_size=1,
    max_size=80,
)


@settings(max_examples=300, deadline=None)
@given(COURSE)
def test_caps_never_rise_and_change_only_after_settling(course) -> None:  # Präzisierung 11
    guard = new_guard(ControlConfig(peak_target_kw=8.0))
    now = QUARTER
    before: dict[str, float] = {}  # die Grenzen des vorigen Schritts
    modes_before: dict[str, Effective] = {}  # die Modi des vorigen Schritts
    changed = None  # die letzte Änderung einer Grenze
    for index, (seconds, wanted_w, soc, *states) in enumerate(course):
        if index:
            now += timedelta(seconds=seconds)
        cars = [
            PeakCar(f"car{rank}", f"wb{rank}", mode, rank, power_w, grid_w, 2760.0)
            for rank, (mode, power_w, grid_w) in enumerate(states)
        ]
        quarter = QuarterObs(slot_start(now), 0.0, 0.0, {})
        caps = dict(guard.step(now, quarter, cars, wanted_w, 0.0, soc, target_kw=8.0).caps_w)

        modes = {car.vehicle: car.mode for car in cars}
        assert all(modes[vehicle] in LEVEL for vehicle in caps)  # nie Boost oder Aus
        # Eine Grenze bleibt nur im selben Modus; nach einem Wechsel fällt sie still weg.
        kept = {v: cap for v, cap in before.items() if modes.get(v) is modes_before.get(v)}
        assert len(set(kept) - set(caps)) <= 1  # höchstens ein Aufheben je Schritt
        assert all(caps[vehicle] <= kept[vehicle] for vehicle in caps if vehicle in kept)
        if caps != kept:  # gesetzt, gesenkt oder aufgehoben, nicht still weggefallen
            assert changed is None or (now - changed).total_seconds() >= SETTLE_S
            changed = now
        before, modes_before = caps, modes
