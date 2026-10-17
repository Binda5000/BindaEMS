"""Eigenschaften von Fehlbetrag, erlaubtem Akku-Anteil und Unterstützung (Design 5.2, 5.3).

hypothesis erzeugt Leistungen, Autos und Verläufe. Die Orakel rechnen aus der Definition noch
einmal, nicht schrittweise wie der Code:

- ``deficits``: Die Fehlbeträge ergeben zusammen, was die Autos über den Rest der PV hinaus ziehen,
  und ein Fehlbetrag hängt nicht von den Autos hinter ihm ab.
- ``allowed``: Der Anteil folgt dem Modus, und das Limit geht in der Reihenfolge der Autos weg.
- der gewollte Netzbezug ``W``: nie negativ, nie über dem Fehlbetrag, nie mehr durch mehr Akku.
- ``Support``: der SOC-Stand aus dem letzten Wert, der die Schwelle erreicht oder unter die
  Hysterese fällt; die Überbrückung aus den Läufen ohne Fehlbetrag im ganzen Verlauf, nicht mit den
  zwei Zeitpunkten, die die Unterstützung sich merkt.
- der Bedarf aus ``BatteryController.assess`` über Verläufe von Zyklen (Design 10.4): ``wanted_w``
  nie negativ, nie über dem Fehlbetrag der Autos, nie mehr mit höherem ``support_max_w`` oder mit
  geltender Unterstützung.
- der gewollte Netzbezug eines Slots mit ``HALTEN`` oder ``NETZLADEN`` (``plan_wanted_w``, Plan 3c,
  Präzisierung 7): nie negativ, nie unter ``W_hold`` (Haus und Autos über PV und Basis), bei
  ``NETZLADEN`` genau ``W_hold + P``.
"""

from datetime import timedelta
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from tests.helpers import T0

from bindaems.core.control.battery import (
    BRIDGE_END_S,
    SUPPORT_MODES,
    BatteryCar,
    BatteryController,
    BatteryNeed,
    Support,
    allowed,
    deficits,
    plan_wanted_w,
)
from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.plan_exec import ActiveSlot
from bindaems.shared.config import ControlConfig, load_config
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind

# --- Fehlbetrag und erlaubter Anteil --------------------------------------------------------------

WATTS = st.floats(-1000.0, 20000.0)  # PV und Haus; ein negativer Wert ist Messrauschen
LIMITS_W = st.floats(0.0, 12000.0)  # support_max_w
CARS = st.lists(
    st.tuples(st.sampled_from(list(Effective)), st.floats(0.0, 11000.0)), min_size=1, max_size=3
).map(
    lambda parts: [  # die Wallboxen sind eindeutig, weil ihr Name aus der Stelle entsteht
        BatteryCar(f"wb{index}", mode, power, True) for index, (mode, power) in enumerate(parts)
    ]
)


def wanted_grid_w(cars, deficit, shares) -> float:
    """``W``: die Summe über die Autos von max(0, Fehlbetrag − erlaubt) (Design 5.3)."""
    return sum(max(0.0, deficit[car.wallbox] - shares[car.wallbox]) for car in cars)


def drawn_beyond_pv_w(pv, house, cars) -> float:
    """Was die Autos zusammen über den Rest der PV hinaus ziehen: ihr Fehlbetrag zusammen."""
    pv_rest_w = max(0.0, max(pv, 0.0) - max(house, 0.0))  # was nach dem Haus von der PV bleibt
    return max(0.0, sum(car.power_w for car in cars) - pv_rest_w)


@given(pv=WATTS, house=WATTS, cars=CARS, support_on=st.booleans(), limit=LIMITS_W)
def test_wanted_grid_is_never_negative_nor_above_the_deficit(
    pv, house, cars, support_on, limit
) -> None:
    deficit = deficits(pv, house, cars)
    assert deficit is not None
    shares = allowed(cars, deficit, support_on=support_on, support_max_w=limit)
    wanted = wanted_grid_w(cars, deficit, shares)
    assert 0.0 <= wanted <= sum(deficit.values()) + 1e-6


@given(
    pv=WATTS,
    house=WATTS,
    cars=CARS,
    limits=st.tuples(LIMITS_W, LIMITS_W).map(sorted),  # support_max_w: a ≤ b
    switches=st.sampled_from([(False, False), (False, True), (True, True)]),  # falsch ≤ wahr
)
def test_more_allowed_battery_never_means_more_grid(pv, house, cars, limits, switches) -> None:
    deficit = deficits(pv, house, cars)
    assert deficit is not None
    (low_w, high_w), (low_on, high_on) = limits, switches
    low = allowed(cars, deficit, support_on=low_on, support_max_w=low_w)
    high = allowed(cars, deficit, support_on=high_on, support_max_w=high_w)
    assert wanted_grid_w(cars, deficit, high) <= wanted_grid_w(cars, deficit, low) + 1e-6


@given(pv=WATTS, house=WATTS, cars=CARS)
def test_the_deficits_add_up_to_what_the_cars_draw_beyond_the_pv(pv, house, cars) -> None:
    deficit = deficits(pv, house, cars)
    assert deficit is not None
    assert list(deficit) == [car.wallbox for car in cars]
    for car in cars:
        assert 0.0 <= deficit[car.wallbox] <= car.power_w + 1e-9
    assert sum(deficit.values()) == pytest.approx(drawn_beyond_pv_w(pv, house, cars), abs=1e-6)


@given(pv=WATTS, house=WATTS, cars=CARS)
def test_a_deficit_does_not_depend_on_the_cars_behind_it(pv, house, cars) -> None:
    deficit = deficits(pv, house, cars)
    assert deficit is not None
    for count in range(1, len(cars) + 1):
        assert deficits(pv, house, cars[:count]) == dict(list(deficit.items())[:count])


@given(pv=WATTS, extra=st.floats(0.0, 5000.0), house=WATTS, cars=CARS)
def test_more_pv_never_raises_a_deficit(pv, extra, house, cars) -> None:
    less, more = deficits(pv, house, cars), deficits(pv + extra, house, cars)
    assert less is not None
    assert more is not None
    for wallbox, watts in more.items():
        assert watts <= less[wallbox] + 1e-9


@given(
    pv=WATTS,
    house=WATTS,
    parts=st.lists(
        st.tuples(st.none() | st.floats(0.0, 11000.0), st.sampled_from([True, False, None])),
        max_size=3,
    ),
)
def test_the_need_is_unknown_exactly_when_a_car_may_charge_without_a_power(
    pv, house, parts
) -> None:
    cars = [
        BatteryCar(f"wb{index}", Effective.PV, power, charging)
        for index, (power, charging) in enumerate(parts)
    ]
    unknown = any(power is None and charging is not False for power, charging in parts)
    assert (deficits(pv, house, cars) is None) is unknown


@given(pv=WATTS, house=WATTS, cars=CARS, support_on=st.booleans(), limit=LIMITS_W)
def test_allowed_follows_the_mode_and_hands_out_the_limit_in_order(
    pv, house, cars, support_on, limit
) -> None:
    deficit = deficits(pv, house, cars)
    assert deficit is not None
    shares = allowed(cars, deficit, support_on=support_on, support_max_w=limit)
    assert list(shares) == [car.wallbox for car in cars]
    handed_w = 0.0  # was die Autos in PV-Überschuss und Min+PV bis hierher bekommen haben
    for car in cars:
        wanted, share = deficit[car.wallbox], shares[car.wallbox]
        if car.mode is Effective.BOOST:
            assert share == wanted
        elif car.mode in SUPPORT_MODES and support_on:
            assert 0.0 <= share <= wanted
            handed_w += share
            assert handed_w <= limit + 1e-6
            if share < wanted - 1e-6:  # weniger als der Fehlbetrag: Das Limit ist aufgebraucht
                assert handed_w == pytest.approx(limit, abs=1e-6)
        else:
            assert share == 0.0


# --- Unterstützung --------------------------------------------------------------------------------

CONTROL = ControlConfig()
START_PCT = CONTROL.support_soc_pct
STOP_PCT = CONTROL.support_soc_pct - CONTROL.support_hysteresis_pct
THRESHOLD_W = CONTROL.setpoint_deadband_w / 2

# Eine Szene: der SOC, der Modus des Autos an der Wallbox und sein Fehlbetrag. Daneben steht ein
# Auto in Boost mit großem Fehlbetrag; es zählt nie mit.
SCENES = st.tuples(
    st.sampled_from([None, 50.0, 64.9, 65.0, 67.0, 69.9, 70.0, 85.0]),
    st.sampled_from([Effective.PV, Effective.MIN_PV, Effective.NOW]),
    st.sampled_from([0.0, 99.0, 100.0, 500.0]),
)
# Eine Szene hält mehrere Werte lang, damit Läufe von 60 s und 900 s zustande kommen.
SEGMENTS = st.lists(
    st.tuples(SCENES, st.integers(1, 15), st.sampled_from([1, 5, 30, 60, 100])),
    min_size=1,
    max_size=10,
)
LONG_BRIDGING = [  # 900 s Fehlbetrag, dann 60 s ohne
    ((80.0, Effective.PV, 500.0), 16, 60),
    ((80.0, Effective.PV, 0.0), 3, 30),
]


def samples_from(segments, start=0):
    """Die Werte (Sekunden, SOC, Modus, Fehlbetrag) der Segmente, ab der Sekunde ``start``."""
    samples, second = [], start
    for (soc, mode, deficit_w), repeats, gap in segments:
        for _ in range(repeats):
            samples.append((second, soc, mode, deficit_w))
            second += gap
    return samples


def run(support, samples):
    """Die Antwort von ``update`` auf jeden Wert."""
    answers = []
    for second, soc, mode, deficit_w in samples:
        cars = [BatteryCar("evcs", mode, 0.0, True), BatteryCar("twc", Effective.BOOST, 0.0, True)]
        deficit = {"evcs": deficit_w, "twc": 5000.0}
        answers.append(support.update(T0 + timedelta(seconds=second), soc, cars, deficit))
    return answers


def clear_s(seconds, flags, index):
    """Wie lange es bis einschließlich ``index`` ohne Unterbrechung keinen Fehlbetrag gab.

    ``None``, wenn dort einer ist.
    """
    if flags[index]:
        return None
    first = index
    while first > 0 and not flags[first - 1]:
        first -= 1
    return float(seconds[index] - seconds[first])


def expected(samples):
    """Die Antwort von ``update`` auf jeden Wert, aus dem ganzen Verlauf gerechnet.

    - SOC: Der letzte Wert, der die Schwelle erreicht oder unter die Hysterese fällt, entscheidet.
    - Überbrückung: Ein Lauf ohne Fehlbetrag beendet sie, sobald er 60 s dauert; der erste
      Fehlbetrag danach beginnt eine neue.
    """
    seconds = [second for second, *_ in samples]
    has_deficit = [
        mode in SUPPORT_MODES and deficit_w >= THRESHOLD_W for _, _, mode, deficit_w in samples
    ]
    endings = []
    for index in range(len(samples)):
        found = clear_s(seconds, has_deficit, index)
        if found is not None and found >= BRIDGE_END_S:
            endings.append(index)

    answers = []
    for index, (second, soc, mode, _) in enumerate(samples):
        decisive = [
            earlier
            for _, earlier, _, _ in samples[: index + 1]
            if earlier is not None and (earlier >= START_PCT or earlier < STOP_PCT)
        ]
        soc_ok = bool(decisive) and decisive[-1] >= START_PCT
        last_ending = max((e for e in endings if e <= index), default=-1)
        began = next((k for k in range(last_ending + 1, index + 1) if has_deficit[k]), None)
        bridging_s = None if began is None else float(second - seconds[began])
        if mode not in SUPPORT_MODES:
            reason = "mode"
        elif soc is None or not soc_ok:
            reason = "soc"
        elif bridging_s is not None and bridging_s >= CONTROL.support_max_s:
            reason = "time"
        else:
            reason = "on"
        answers.append((reason, bridging_s))
    return answers


@settings(max_examples=200, deadline=None)
@given(SEGMENTS)
@example(LONG_BRIDGING)
def test_support_follows_the_soc_and_the_bridging_over_the_whole_history(segments) -> None:
    samples = samples_from(segments)
    assert run(Support(CONTROL), samples) == expected(samples)


@settings(max_examples=150, deadline=None)
@given(before=SEGMENTS, after=SEGMENTS)
@example(before=LONG_BRIDGING, after=LONG_BRIDGING)
def test_reset_forgets_the_whole_history(before, after) -> None:
    support = Support(CONTROL)
    earlier = samples_from(before)
    run(support, earlier)
    support.reset()
    later = samples_from(after, start=earlier[-1][0] + 1)
    assert run(support, later) == expected(later)


# --- Bedarf aus assess (Design 10.4) --------------------------------------------------------------

CFG = load_config(Path("deploy/config.example.yaml"))

# Ein Zyklus: Sekunden seit dem vorigen, SOC, PV, Haus und Autos. Die langen Abstände lassen die
# Überbrückung über support_max_s laufen; dann ist die Unterstützung wegen der Zeit gesperrt.
CYCLES = st.lists(
    st.tuples(
        st.sampled_from([1, 30, 120, 600]),
        st.none() | st.floats(0.0, 100.0),
        WATTS,
        WATTS,
        CARS,
    ),
    min_size=1,
    max_size=8,
)


def controller(support_max_w: float) -> BatteryController:
    control = CFG.control.model_copy(update={"support_max_w": support_max_w})
    return BatteryController(CFG.model_copy(update={"control": control}))


def assessed(battery, cycles, socs=None) -> list[BatteryNeed]:
    """Der Bedarf aus ``assess`` je Zyklus; ``socs`` ersetzt, wenn angegeben, den SOC der Zyklen."""
    if socs is None:
        socs = [soc for _, soc, *_ in cycles]
    found, second = [], 0
    for (gap, _, pv, house, cars), soc in zip(cycles, socs, strict=True):
        second += gap
        need = battery.assess(T0 + timedelta(seconds=second), soc, pv, house, cars)
        assert need is not None
        found.append(need)
    return found


@settings(deadline=None)
@given(cycles=CYCLES, limit=LIMITS_W)
def test_need_from_assess_is_never_negative_nor_above_the_deficit(cycles, limit) -> None:
    for (_, _, pv, house, cars), need in zip(
        cycles, assessed(controller(limit), cycles), strict=True
    ):
        assert all(watts >= 0.0 for watts in need.grid_w.values())
        assert 0.0 <= need.wanted_w <= drawn_beyond_pv_w(pv, house, cars) + 1e-6


@settings(deadline=None)
@given(cycles=CYCLES, limits=st.tuples(LIMITS_W, LIMITS_W).map(sorted))  # support_max_w: a ≤ b
def test_a_higher_support_limit_never_means_more_grid_from_assess(cycles, limits) -> None:
    low_w, high_w = limits
    low, high = assessed(controller(low_w), cycles), assessed(controller(high_w), cycles)
    for less, more in zip(high, low, strict=True):
        assert less.wanted_w <= more.wanted_w + 1e-6


@settings(deadline=None)
@given(
    cycles=CYCLES,
    limit=LIMITS_W,
    soc_on=st.floats(70.0, 100.0),  # erreicht support_soc_pct
    soc_off=st.none() | st.floats(0.0, 70.0, exclude_max=True),  # erreicht ihn nie
)
def test_support_never_means_more_grid_from_assess(cycles, limit, soc_on, soc_off) -> None:
    supported = assessed(controller(limit), cycles, socs=[soc_on] * len(cycles))
    alone = assessed(controller(limit), cycles, socs=[soc_off] * len(cycles))
    for with_battery, without in zip(supported, alone, strict=True):
        assert without.support != "on"
        assert with_battery.wanted_w <= without.wanted_w + 1e-6


# --- Gewollter Netzbezug eines Slots (Plan 3c, Präzisierung 7) ------------------------------------

PLANNED = st.one_of(
    st.just(BatteryIntent(kind=BatteryIntentKind.HALTEN)),
    st.floats(1.0, 8000.0).map(
        lambda watts: BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=watts)
    ),
)


def hold_w(pv, house, cars, base) -> float:
    """``W_hold``: was Haus und Autos über PV und Basis ziehen; Messrauschen unter 0 zählt als 0."""
    drawn_w = max(house, 0.0) + sum(car.power_w for car in cars)
    return max(0.0, drawn_w - max(pv, 0.0) - base)


@settings(deadline=None)
@given(
    pv=WATTS,
    house=WATTS,
    cars=CARS,
    base=st.floats(-500.0, 500.0),
    soc=st.none() | st.floats(0.0, 100.0),
    intent=PLANNED,
)
def test_the_plan_target_is_never_below_holding(pv, house, cars, base, soc, intent) -> None:
    need = BatteryController(CFG).assess(T0, soc, pv, house, cars)
    assert need is not None
    wanted = plan_wanted_w(need, base, ActiveSlot(1, T0, intent, None, None, None))
    held = hold_w(pv, house, cars, base)
    assert wanted >= 0.0
    assert wanted >= held - 1e-6
    if intent.kind is BatteryIntentKind.NETZLADEN:
        assert wanted == pytest.approx(held + intent.grid_charge_w, abs=1e-6)
    else:
        assert wanted == pytest.approx(held, abs=1e-6)
