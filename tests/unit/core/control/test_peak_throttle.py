"""15-min-Wächter, Teil 2: Drosselung, Aufheben und Start-Sperre (Design 6.3, 6.4).

Plan 2c-2a, Präzisierungen 11 und 13. Aufbau wie in ``test_peak_guard.py``: Ziel 8 kW, Marge 0,3 kW,
ein Schritt je Sekunde ab 18:00:00 mit Mittel 0 und ohne Autos; bei 18:05:00 ist E 1000 Wh, also
``P_zul`` 5700 W, und die Autos kommen dazu (``at_18_05``). Danach hält ``later`` ``P_zul`` bei
5700 W, sodass die verlangte Senkung ``W − 5700 W`` ist (Basis 0).
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.peak_guard import (
    LAST_S,
    NO_LIMITS,
    RELEASE_W,
    SETTLE_S,
    Cap,
    PeakCar,
    PeakGuard,
    PeakLimits,
    QuarterObs,
    lift,
    no_start,
    pending_w,
    throttle,
)
from bindaems.shared.config import ControlConfig
from bindaems.shared.timeutil import slot_start

QUARTER = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)  # 18:00 Wiener Zeit
ALLOWED_W = 5700.0  # P_zul ab 18:05 (``at_18_05`` und ``later``)

A = PeakCar("a", "twc", Effective.NOW, 0, 7000.0, 7000.0, 3450.0)
B = PeakCar("b", "evcs", Effective.MIN_PV, 1, 4000.0, 4000.0, 2760.0)
C = PeakCar("c", "x", Effective.PV, 2, 3000.0, 3000.0, 1380.0)
CARS = [A, B, C]
FIRST = {"c": 0.0, "b": 2760.0, "a": 6240.0}  # die Grenzen nach der Senkung 5000 W um 18:05:00


def at(seconds: float) -> datetime:
    """Der Zeitpunkt ``seconds`` nach 18:00:00 Wiener Zeit, dem Beginn der Viertelstunde."""
    return QUARTER + timedelta(seconds=seconds)


def guard() -> PeakGuard:
    return PeakGuard(ControlConfig(peak_target_kw=8.0), reserve_soc_pct=20.0)


def step(
    g: PeakGuard,
    seconds: float,
    *,
    cars: Sequence[PeakCar] = (),
    wanted_w: float = 0.0,
    wh: float = 0.0,
    grid_w: float = 0.0,
    soc: float | None = 60.0,
    observed: bool = True,
) -> PeakLimits:
    """Ein Schritt ``seconds`` nach 18:00:00 mit Basis 0; ohne ``observed`` fehlt ``quarter``."""
    now = at(seconds)
    quarter = QuarterObs(slot_start(now), wh, grid_w, {}) if observed else None
    return g.step(now, quarter, cars, wanted_w, 0.0, soc)


def at_18_05(
    cars: Sequence[PeakCar], wanted_w: float, g: PeakGuard | None = None, **kw: Any
) -> PeakLimits:
    """Schritte ab 18:00:00 mit Mittel 0, bei 18:05:00 E 1000 Wh und die Autos: ``P_zul`` 5700 W.

    Zurück kommen die Grenzen des Schritts um 18:05:00; die übrigen Schlüsselwörter gehen an ihn.
    """
    g = guard() if g is None else g
    for second in range(300):
        step(g, second)
    return step(g, 300, cars=cars, wanted_w=wanted_w, wh=1000.0, **kw)


def wh_for(second: float) -> float:
    """Die Energie bisher, mit der ``P_zul`` bei ``second`` 5700 W ist.

    ``(2000 Wh − E) · 3600 / Rest − 300 W = 5700 W``; genau ist das bei einem Rest, der durch 3
    teilbar ist.
    """
    return 2000.0 - (900.0 - second) * 6000.0 / 3600.0


def later(
    g: PeakGuard, second: float, reduction_w: float, cars: Sequence[PeakCar] = CARS
) -> PeakLimits:
    """Ein Schritt mit ``P_zul`` 5700 W, Mittel 0 und der verlangten Senkung ``reduction_w``.

    Die Senkung ist ``max(Mittel, Basis + W) − P_zul``, also ``W = 5700 W + reduction_w``; mit
    Mittel 0 ist sie mindestens −5700 W.
    """
    return step(g, second, cars=cars, wanted_w=ALLOWED_W + reduction_w, wh=wh_for(second))


def hold(
    g: PeakGuard, first: int, last: int, reduction_w: float, cars: Sequence[PeakCar] = CARS
) -> list[Mapping[str, float]]:
    """``later`` alle 3 s zwischen ``first`` und ``last``; zurück kommen die Grenzen je Schritt.

    Die Schritte liegen auf den Sekunden, die durch 3 teilbar sind: Nur dort ist ``P_zul`` genau
    5700 W und die Senkung genau ``reduction_w``; sonst drosselte eine Senkung, die um 1e-12 W über
    dem liegt, was aussteht. 3 s ohne Schritt sind keine Lücke.
    """
    seconds = range(first + (-first) % 3, last + 1, 3)
    return [later(g, second, reduction_w, cars).caps_w for second in seconds]


def throttled() -> PeakGuard:
    """Ein Wächter mit den Grenzen ``FIRST`` seit 18:05:00, bis 18:05:29 ohne Änderung."""
    g = guard()
    assert at_18_05(CARS, 10700.0, g).caps_w == FIRST
    assert all(caps == FIRST for caps in hold(g, 301, 329, 5000.0))
    return g


def test_constants() -> None:
    assert SETTLE_S == 30.0
    assert RELEASE_W == 1000.0
    assert LAST_S == 120.0


# --- Drosselung -----------------------------------------------------------------------------------


def test_throttles_pv_then_min_pv_then_now() -> None:  # Design 6.3
    limits = at_18_05(cars=[A, B, C], wanted_w=10700.0)  # Senkung 5000 W
    # PV-Überschuss bis 0 (3000 W), Min+PV bis zu seiner Mindestleistung (1240 W), Sofort den Rest
    assert limits.caps_w == {"c": 0.0, "b": 2760.0, "a": 6240.0}
    assert limits.budget_w == ALLOWED_W
    assert limits.no_start == frozenset()


@pytest.mark.parametrize(("wanted_w", "caps"), [(6200.0, {"c": 2500.0}), (5700.5, {"c": 2999.5})])
def test_any_reduction_above_zero_throttles_only_what_it_needs(wanted_w, caps) -> None:
    # 500 W und 0,5 W Senkung nimmt „c“ allein, das Auto mit der niedrigsten Priorität in PV.
    assert at_18_05(CARS, wanted_w).caps_w == caps


def test_within_a_level_the_lowest_priority_goes_first() -> None:
    first = PeakCar("first", "twc", Effective.NOW, 0, 7000.0, 7000.0, 3450.0)
    second = PeakCar("second", "evcs", Effective.NOW, 1, 7000.0, 7000.0, 3450.0)
    limits = at_18_05(cars=[first, second], wanted_w=7700.0)  # Senkung 2000 W
    assert limits.caps_w == {"second": 5000.0}


def test_boost_off_and_cars_without_grid_share_stay_free() -> None:
    boost = PeakCar("boost", "w1", Effective.BOOST, 0, 7000.0, 7000.0, 3450.0)
    off = PeakCar("off", "w2", Effective.OFF, 3, 0.0, 0.0, 2760.0)
    pv = PeakCar("pv", "w3", Effective.PV, 2, 3000.0, 0.0, 1380.0)  # ganz aus der PV
    now = PeakCar("now", "w4", Effective.NOW, 1, 7000.0, 7000.0, 3450.0)
    limits = at_18_05(cars=[boost, off, pv, now], wanted_w=7700.0)  # Senkung 2000 W
    assert limits.caps_w == {"now": 5000.0}


def test_next_throttling_waits_30_s_and_counts_what_is_pending() -> None:
    g = guard()
    assert at_18_05(CARS, 10700.0, g).caps_w == FIRST
    assert all(caps == FIRST for caps in hold(g, 301, 328, 5000.0))
    # 18:05:29: 1000 W mehr Senkung, aber seit der Änderung um 18:05:00 sind keine 30 s vergangen.
    assert later(g, 329, 6000.0).caps_w == FIRST
    # 18:05:30: Die Leistung ist unverändert, also stehen 3000 + 1240 + 760 = 5000 W aus.
    assert later(g, 330, 5000.0).caps_w == FIRST
    # Ein zweiter Lauf: um 18:05:30 mit 6000 W Senkung sinkt „a“ um 1000 W, von seiner Grenze aus.
    assert later(throttled(), 330, 6000.0).caps_w == {**FIRST, "a": 5240.0}


def test_a_car_without_a_known_power_counts_as_not_followed() -> None:  # Präzisierung 11
    # Um 18:05:30 sind „b“ und „c“ ihren Grenzen gefolgt; die Leistung von „a“ ist unbekannt: Es
    # steht mit seiner ganzen Senkung von 760 W aus.
    followed = [replace(A, power_w=None), replace(B, power_w=2760.0), replace(C, power_w=0.0)]
    assert later(throttled(), 330, 760.0, followed).caps_w == FIRST  # nichts zu senken
    # 1760 W − 760 W: „a“ sinkt um 1000 W, von seiner Grenze aus, auch ohne bekannte Leistung.
    assert later(throttled(), 330, 1760.0, followed).caps_w == {**FIRST, "a": 5240.0}


def test_a_car_below_its_floor_or_without_a_starting_point_is_skipped() -> None:
    # „below“ lädt unter seinem Boden, „unknown“ hat weder Grenze noch Leistung, also keinen
    # Ausgang: Beide bleiben, wie sie sind, und „other“ trägt die ganze Senkung.
    other = PeakCar("other", "w1", Effective.NOW, 0, 7000.0, 7000.0, 3450.0)
    below = PeakCar("below", "w2", Effective.NOW, 1, 2000.0, 2000.0, 3450.0)
    unknown = PeakCar("unknown", "w3", Effective.NOW, 2, None, 5000.0, 3450.0)
    assert throttle([other, below, unknown], {}, 1000.0) == {"other": Cap(6000.0, 1000.0)}
    # Mit einer Grenze hat das Auto ohne Leistung einen Ausgang, und die Senkung wächst.
    assert throttle([unknown], {"unknown": Cap(5000.0, 500.0)}, 1000.0) == {
        "unknown": Cap(4000.0, 1500.0)
    }


def test_a_grid_share_below_the_way_to_the_floor_leaves_the_car_above_it() -> None:
    # Ein Auto sinkt höchstens um seinen Netzanteil (Präzisierung 11): „c“ hat nur 600 W. Die
    # Senkung nimmt ihm diese 600 W, es bleibt über seinem Boden, und „a“ in Sofort trägt den Rest.
    small = replace(C, grid_w=600.0)
    assert at_18_05([A, small], 8700.0).caps_w == {"c": 2400.0, "a": 4600.0}  # Senkung 3000 W


def test_nothing_to_throttle_changes_nothing() -> None:
    caps = {"a": Cap(6240.0, 760.0)}
    assert throttle(CARS, caps, 0.0) == caps
    assert throttle(CARS, caps, -100.0) == caps
    assert throttle([replace(C, mode=Effective.OFF)], {}, 5000.0) == {}


def test_pending_counts_what_has_not_followed() -> None:  # Präzisierung 11
    caps = {"a": Cap(6240.0, 760.0), "b": Cap(2760.0, 1240.0), "c": Cap(0.0, 3000.0)}
    cars = [replace(A, power_w=6000.0), replace(B, power_w=None), C]
    # „a“ liegt unter seiner Grenze (0 W), „b“ ohne Leistung zählt seine Senkung, „c“ 3000 W − 0 W.
    assert pending_w(cars, caps) == 4240.0
    assert pending_w(cars, {}) == 0.0


def test_caps_do_not_depend_on_the_soc() -> None:  # Präzisierung 12: nur das Budget
    limits = at_18_05(CARS, 10700.0, soc=20.0)  # unter der Schwelle von 25 %
    assert limits.budget_w is None
    assert limits.caps_w == FIRST


def test_an_exceeded_quarter_still_throttles() -> None:  # Präzisierung 14
    g = guard()
    for second in range(60):
        step(g, second, grid_w=12300.0, wh=12300.0 * second / 3600.0)
    limits = step(g, 60, cars=CARS, grid_w=12300.0, wh=205.0)
    assert g.health()["state"] == "exceeded"
    allowed_w = (2000.0 - 205.0) * 3600.0 / 840.0 - 300.0  # P_zul 7392,9 W
    # Senkung Mittel 12300 W − P_zul: „c“ verliert 3000 W, „b“ 1240 W, „a“ den Rest.
    rest_w = 12300.0 - allowed_w - 3000.0 - 1240.0
    assert limits.caps_w == pytest.approx({"c": 0.0, "b": 2760.0, "a": 7000.0 - rest_w})


# --- Aufheben -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headroom_w", "caps"),
    [
        (1760.0, {"b": 2760.0, "c": 0.0}),  # 760 W + 1000 W: „a“ in Sofort verliert seine Grenze
        (1759.0, FIRST),  # 1 W zu wenig
        (761.0, FIRST),  # 999 W weniger: über der Senkung von „a“, aber ohne 1 kW Abstand
    ],
)
def test_a_cap_lifts_with_1_kw_headroom_in_reverse_order(headroom_w, caps) -> None:
    # P_zul − max(Mittel, Basis + W) = 5700 W − (5700 W − headroom_w)
    assert later(throttled(), 330, -headroom_w).caps_w == caps


def test_only_one_cap_lifts_per_step() -> None:  # Präzisierung 11
    # Ohne Bedarf ist der Spielraum P_zul = 5700 W: Er trüge jede Grenze, aber je Schritt fällt
    # eine, in umgekehrter Reihenfolge und frühestens 30 s nach der letzten Änderung.
    g = throttled()
    assert later(g, 330, -ALLOWED_W).caps_w == {"b": 2760.0, "c": 0.0}
    assert all(caps == {"b": 2760.0, "c": 0.0} for caps in hold(g, 331, 359, -ALLOWED_W))
    assert later(g, 360, -ALLOWED_W).caps_w == {"c": 0.0}
    assert all(caps == {"c": 0.0} for caps in hold(g, 361, 389, -ALLOWED_W))
    assert later(g, 390, -ALLOWED_W).caps_w == {}


def test_the_lift_takes_the_first_cap_the_headroom_carries() -> None:
    small = replace(C, grid_w=600.0)  # „c“ verliert 600 W, „a“ 2400 W
    g = guard()
    assert at_18_05([A, small], 8700.0, g).caps_w == {"c": 2400.0, "a": 4600.0}
    assert all(caps == {"c": 2400.0, "a": 4600.0} for caps in hold(g, 301, 329, 3000.0, [A, small]))
    # 1600 W Spielraum: „a“ (Sofort) bräuchte 2400 W + 1000 W, „c“ 600 W + 1000 W.
    assert later(g, 330, -1600.0, [A, small]).caps_w == {"a": 4600.0}


def test_lift_removes_one_whole_cap() -> None:
    caps = {"a": Cap(6240.0, 760.0), "b": Cap(2760.0, 1240.0), "c": Cap(0.0, 3000.0)}
    assert lift(CARS, caps, 10000.0) == {"b": Cap(2760.0, 1240.0), "c": Cap(0.0, 3000.0)}
    assert lift(CARS, caps, 1759.0) == caps
    assert lift(CARS, caps, -100.0) == caps


# --- Ende der Viertelstunde, Lücken und Autos, die gehen -----------------------------------------


def test_quarter_end_lifts_all_caps() -> None:
    g = guard()
    at_18_05(CARS, 10700.0, g)
    assert all(caps == FIRST for caps in hold(g, 301, 899, 5000.0))  # bis 18:14:59
    # 18:15:00 ohne Bedarf: Das Aufheben allein ließe zwei Grenzen stehen (eine je Schritt).
    assert step(g, 900, cars=CARS).caps_w == {}


def test_a_new_quarter_may_throttle_at_once() -> None:
    g = guard()
    at_18_05(CARS, 10700.0, g)
    hold(g, 301, 884, 5000.0)
    assert later(g, 885, 6000.0).caps_w == {**FIRST, "a": 5240.0}  # 18:14:45, die letzte Änderung
    hold(g, 886, 899, 6000.0)
    # 18:15:00: P_zul 7700 W (E 0 Wh, Rest 900 s), Senkung 12700 W − 7700 W = 5000 W. Die neue
    # Viertelstunde beginnt ohne Grenzen und ohne letzte Änderung: Sie drosselt sofort.
    assert step(g, 900, cars=CARS, wanted_w=12700.0).caps_w == FIRST


def test_a_gap_drops_the_caps() -> None:
    g = guard()
    at_18_05(CARS, 10700.0, g)
    assert step(g, 301, cars=CARS, observed=False) == NO_LIMITS  # ein Schritt, der nicht rechnet
    # Der Wächter rechnet wieder (3 s ohne Bilanz sind keine Lücke), doch die Grenzen sind weg.
    assert later(g, 303, 0.0).caps_w == {}
    # Die Uhr der letzten Änderung ist mit ihnen weg: Eine Senkung drosselt sofort.
    assert later(g, 306, 5000.0).caps_w == FIRST


@pytest.mark.parametrize(
    "cars",
    [[replace(A, mode=Effective.BOOST), B, C], [replace(A, mode=Effective.OFF), B, C], [B, C]],
    ids=["boost", "off", "gone"],
)
def test_a_car_in_boost_or_off_or_gone_loses_its_cap_silently(cars) -> None:
    # In der Wartezeit fällt die Grenze von „a“ sofort weg.
    g = guard()
    at_18_05(CARS, 10700.0, g)
    assert all(caps == {"b": 2760.0, "c": 0.0} for caps in hold(g, 303, 329, 5000.0, cars))
    # Nach der Wartezeit, wenn der Wächter drosseln dürfte, gibt es nichts zu senken („b“ und „c“
    # sind an ihrem Boden), und der Wegfall um 18:05:30 ist keine Änderung: Um 18:05:33 ist „a“
    # wieder in Sofort, und der Wächter drosselt es sofort, ohne neue Wartezeit, von seiner Leistung
    # aus (ausstehend 3000 W + 1240 W).
    g = throttled()
    assert later(g, 330, 5000.0, cars).caps_w == {"b": 2760.0, "c": 0.0}
    assert later(g, 333, 5000.0).caps_w == FIRST


def test_a_car_that_changes_its_mode_keeps_its_cap() -> None:
    # „b“ wechselt von Min+PV in PV-Überschuss und behält seine Grenze. Die nächste Drosselung senkt
    # es in der Stufe von PV-Überschuss bis 0: nach „c“ (Rang 2, an seinem Boden), vor „a“.
    switched = [A, replace(B, mode=Effective.PV), C]
    g = guard()
    at_18_05(CARS, 10700.0, g)
    hold(g, 301, 311, 5000.0)
    assert all(caps == FIRST for caps in hold(g, 312, 329, 5000.0, switched))
    assert later(g, 330, 6000.0, switched).caps_w == {"a": 6240.0, "b": 1760.0, "c": 0.0}


# --- Start-Sperre ---------------------------------------------------------------------------------


def test_no_start_in_the_last_120_s() -> None:  # Präzisierung 13
    boost = PeakCar("d", "w1", Effective.BOOST, 3, 0.0, 0.0, 4140.0)
    off = PeakCar("e", "w2", Effective.OFF, 4, 0.0, 0.0, 4140.0)
    cars = [A, B, C, boost, off]
    g = guard()
    for second in range(779):
        step(g, second, grid_w=2100.0)  # Mittel 2100 W
    # 18:12:59, noch 121 s: niemand
    assert step(g, 779, cars=cars, grid_w=2100.0, wh=1820.0).no_start == frozenset()
    # 18:13:00, noch 120 s: P_zul (2000 Wh − 1820 Wh) · 3600 / 120 s − 300 W = 5100 W, minus Mittel
    # 2100 W bleiben 3000 W. „a“ braucht 3450 W, „b“ 2760 W; Boost und Aus stehen nie darin.
    limits = step(g, 780, cars=cars, grid_w=2100.0, wh=1820.0)
    assert limits.no_start == {"a"}
    assert limits.caps_w == {}  # ohne Bedarf keine Drosselung
    # Unabhängig vom SOC: Nur das Budget hängt an ihm (Präzisierung 12).
    assert step(g, 781, cars=cars, grid_w=2100.0, wh=1820.0, soc=20.0).no_start == {"a"}


@pytest.mark.parametrize(
    ("rest_s", "mean_w", "expected"),
    [
        (120.0, 2100.0, {"a"}),
        (121.0, 2100.0, set()),
        (1.0, 2100.0, {"a"}),
        (120.0, None, set()),  # ohne Mittel niemand
        (120.0, 2650.0, {"a", "b"}),  # 2450 W bleiben: „b“ braucht 2760 W
    ],
)
def test_no_start_names_the_cars_whose_minimum_exceeds_the_room(rest_s, mean_w, expected) -> None:
    assert no_start(CARS, rest_s, 5100.0, mean_w) == expected


def test_a_minimum_equal_to_the_room_may_start() -> None:
    assert no_start([replace(A, min_w=3000.0)], 120.0, 5100.0, 2100.0) == frozenset()
