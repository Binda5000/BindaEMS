import math
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from tests.helpers import snap

from bindaems.core.accounting.flows import SlotAccumulator
from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.peak_guard import (
    EXCEEDED_AFTER_S,
    GAP_S,
    MEAN_S,
    NO_LIMITS,
    PEAK_EXCEEDED,
    PeakCar,
    PeakGuard,
    PeakLimits,
    QuarterObs,
    guard_target_kw,
)
from bindaems.core.control.plan_exec import ActiveSlot
from bindaems.shared.config import ControlConfig
from bindaems.shared.domain import Alarm, Severity
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind
from bindaems.shared.timeutil import HOLD_MAX_S, LOCAL_TZ, slot_start

QUARTER = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)  # 18:00 Wiener Zeit
NEXT_QUARTER = QUARTER + timedelta(minutes=15)  # 18:15
NEXT_S = 900  # die Sekunde, in der die nächste Viertelstunde beginnt
SHARE = {"evcs": 3600.0}  # Netzanteil der Wallbox „evcs“ im Zyklus
EGOLF_BOOST = PeakCar("egolf", "evcs", Effective.BOOST, 0, 3600.0, 3600.0, 1380.0)
NO_VALUES = {"energy_kwh": None, "projected_kw": None, "budget_kw": None, "reduction_kw": None}
# Die feste Grenze C von Regel 2 allein aus der Absicherung (3 · 230 V · 33 A): Sie liegt über
# Basis + W jedes Tests, der sie nicht selbst wählt.
FUSE_CEILING_W = 22770.0


def at(seconds: float) -> datetime:
    """Der Zeitpunkt ``seconds`` nach 18:00:00 Wiener Zeit, dem Beginn der Viertelstunde."""
    return QUARTER + timedelta(seconds=seconds)


def guard(
    target: float | None = 8.0, *, ceiling_w: float = FUSE_CEILING_W, **control: float
) -> PeakGuard:
    """Ein Wächter mit USV-Reserve 20 % und der festen Grenze ``ceiling_w``.

    ``target`` ist ``control.peak_target_kw``, das Ziel vor dem ersten Schritt; ab dem ersten gilt
    das ``target_kw`` des Schritts (``step``).
    """
    config = ControlConfig(peak_target_kw=target, **control)
    return PeakGuard(config, reserve_soc_pct=20.0, ceiling_w=ceiling_w)


def step(
    g: PeakGuard,
    seconds: float,
    *,
    grid_w: float | None = 0.0,
    wh: float = 0.0,
    wallbox_grid_w: Mapping[str, float] | None = None,
    cars: Sequence[PeakCar] = (),
    wanted_w: float | None = 0.0,
    base_w: float | None = 0.0,
    soc: float | None = 60.0,
    observed: bool = True,
    target_kw: float | None = 8.0,
) -> PeakLimits:
    """Ein Schritt ``seconds`` nach 18:00:00 mit dem Ziel ``target_kw``; ohne ``observed`` fehlt
    ``quarter`` (``None``)."""
    now = at(seconds)
    quarter = QuarterObs(slot_start(now), wh, grid_w, wallbox_grid_w or {}) if observed else None
    return g.step(now, quarter, cars, wanted_w, base_w, soc, target_kw=target_kw)


def run(
    g: PeakGuard,
    first_s: int,
    last_s: int,
    *,
    wh: Callable[[int], float] = lambda s: 0.0,
    every: int = 1,
    **kw: Any,
) -> PeakLimits:
    """Ein Schritt je ``every`` Sekunden von 18:00:00 + ``first_s`` bis + ``last_s``, beide dabei.

    Zurück kommen die Grenzen des letzten Schritts; ``wh`` gibt je Sekunde die Energie bisher an,
    die übrigen Schlüsselwörter gehen an ``step``.
    """
    limits = NO_LIMITS
    for second in range(first_s, last_s + 1, every):
        limits = step(g, second, wh=wh(second), **kw)
    return limits


def values(g: PeakGuard) -> dict[str, Any]:
    """Die vier Werte von Health, die nur ein rechnender Schritt hat."""
    health = g.health(QUARTER)
    return {key: health[key] for key in NO_VALUES}


def draw(watts: float) -> Callable[[int], float]:
    """Die Energie bisher bei einem Netzbezug von ``watts`` seit 18:00:00."""
    return lambda second: watts * second / 3600


def test_constants() -> None:
    assert GAP_S == 10.0
    assert HOLD_MAX_S == 5.0  # so weit integriert die Abrechnung, und der Wächter mit ihr
    assert MEAN_S == 30.0
    assert EXCEEDED_AFTER_S == 60.0
    assert PEAK_EXCEEDED == "peak.exceeded"
    assert NO_LIMITS == PeakLimits(budget_w=None, caps_w={}, no_start=frozenset())


# --- Ruhen und Anfang -----------------------------------------------------------------------------


def test_without_a_target_the_guard_rests() -> None:
    g = guard()
    assert (
        run(g, 0, 10, grid_w=50000.0, wh=lambda s: 5000.0, target_kw=None) == NO_LIMITS
    )  # auch bei großem Bezug
    assert g.health(QUARTER) == {
        "state": "off",
        "target_kw": None,
        **NO_VALUES,
        "quarter_end": None,
    }
    assert g.alarms() == []
    g.idle()  # es gibt nichts, was ruhen könnte
    assert g.health(QUARTER)["state"] == "off"


def test_before_the_first_step_the_state_is_a_gap_without_values() -> None:
    g = guard()  # vor dem ersten Schritt gilt das Ziel der Konfiguration
    assert g.target_kw() == 8.0
    assert g.health(QUARTER) == {"state": "gap", "target_kw": 8.0, **NO_VALUES, "quarter_end": None}
    assert g.alarms() == []
    assert guard(None).health(QUARTER)["state"] == "off"


# --- Ziel je Schritt ------------------------------------------------------------------------------


def slot_with(budget_kw: float | None) -> ActiveSlot:
    """Der gültige Slot eines Plans mit dem Budget ``budget_kw``."""
    intent = BatteryIntent(kind=BatteryIntentKind.EIGENVERBRAUCH)
    return ActiveSlot(1, QUARTER, intent, budget_kw, None, None)


def test_plan_budget_replaces_the_target() -> None:  # Plan 3c, Präzisierung 9
    configured = ControlConfig(peak_target_kw=8.0)
    assert guard_target_kw(configured, slot_with(budget_kw=5.5)) == 5.5
    assert guard_target_kw(configured, slot_with(budget_kw=None)) == 8.0
    assert guard_target_kw(configured, None) == 8.0
    # ein Budget des Plans schaltet den Wächter auch ohne Ziel in der Konfiguration ein
    assert guard_target_kw(ControlConfig(), slot_with(budget_kw=5.5)) == 5.5
    assert guard_target_kw(ControlConfig(), slot_with(budget_kw=None)) is None
    assert guard_target_kw(ControlConfig(), None) is None


def test_target_change_mid_quarter_changes_the_allowance() -> None:
    g = guard()
    run(g, 0, 300, wh=lambda s: 1000.0 if s == 300 else 0.0)
    assert g.health(QUARTER)["budget_kw"] == 5.7  # (2000 Wh − 1000 Wh) · 3600 / 600 s − 300 W
    limits = step(g, 301, wh=1000.0, target_kw=5.0)
    # 18:05:01 mit Ziel 5 kW: (1250 Wh − 1000 Wh) · 3600 / 599 s − 300 W
    expected_w = 250.0 * 3600 / 599 - 300.0
    assert limits.budget_w == pytest.approx(expected_w)
    assert g.health(QUARTER)["state"] == "active"  # dieselbe Viertelstunde, keine Lücke
    assert g.health(QUARTER)["target_kw"] == 5.0
    assert g.health(QUARTER)["budget_kw"] == pytest.approx(expected_w / 1000.0)


def test_health_target_follows_the_last_step() -> None:
    g = guard()
    step(g, 0, target_kw=8.0)
    assert (g.target_kw(), g.health(QUARTER)["target_kw"], g.health(QUARTER)["state"]) == (
        8.0,
        8.0,
        "active",
    )
    assert step(g, 1, target_kw=None) == NO_LIMITS
    assert g.target_kw() is None
    assert g.health(QUARTER) == {
        "state": "off",
        "target_kw": None,
        **NO_VALUES,
        "quarter_end": None,
    }
    step(g, NEXT_S, target_kw=5.0)  # die nächste Viertelstunde beginnt mit dem Ziel des Plans
    assert (g.target_kw(), g.health(QUARTER)["target_kw"], g.health(QUARTER)["state"]) == (
        5.0,
        5.0,
        "active",
    )


def test_without_a_target_a_step_drops_the_exceeded_alarm() -> None:
    g = guard()
    run(g, 0, 60, grid_w=12300.0, wh=draw(12300.0))
    assert len(g.alarms()) == 1
    step(g, 61, grid_w=12300.0, wh=draw(12300.0), target_kw=None)
    assert g.alarms() == []
    assert g.health(QUARTER)["state"] == "off"


# --- Budget ---------------------------------------------------------------------------------------


def test_budget_after_the_formula() -> None:  # Design 6.2
    g = guard()
    limits = run(g, 0, 300, wh=lambda s: 1000.0 if s == 300 else 0.0)
    # 18:05: (2000 Wh − 1000 Wh) · 3600 / 600 s − 300 W
    assert limits.budget_w == 5700.0
    assert limits.caps_w == {}  # ohne Autos keine Grenzen und keine Start-Sperre
    assert limits.no_start == frozenset()
    health = g.health(QUARTER)
    assert health["budget_kw"] == 5.7
    assert health["energy_kwh"] == 1.0
    assert health["state"] == "active"
    assert health["target_kw"] == 8.0
    assert health["quarter_end"] == NEXT_QUARTER.isoformat()


@pytest.mark.parametrize(
    ("soc", "budget_w"),
    [(25.0, None), (25.1, 5700.0), (None, None), (math.nan, None), (0.0, None), (100.0, 5700.0)],
)
def test_budget_reaches_the_battery_only_above_the_reserve_guard(soc, budget_w) -> None:
    # Präzisierung 12: Reserve 20 % + reserve_guard_pct 5 %; Health zeigt P_zul in jedem Fall
    g = guard()
    limits = run(g, 0, 300, wh=lambda s: 1000.0 if s == 300 else 0.0, soc=soc)
    assert limits.budget_w == budget_w
    assert g.health(QUARTER)["budget_kw"] == 5.7


def test_the_threshold_of_the_budget_follows_the_configuration() -> None:
    g = guard(reserve_guard_pct=10.0)  # 20 % + 10 %
    assert run(g, 0, 300, soc=30.0).budget_w is None
    assert run(g, 301, 301, soc=30.1).budget_w is not None


def test_the_budget_can_be_used_up() -> None:
    # Design 6.2: P_zul < 0, Regel 2 drückt den Override auf 0. E = 2500 Wh liegt über dem Budget
    # von 8 kW · 0,25 h = 2000 Wh.
    g = guard()
    limits = run(g, 0, 300, wh=lambda s: 2500.0 if s == 300 else 0.0)
    assert limits.budget_w == -3300.0  # (2000 Wh − 2500 Wh) · 3600 / 600 s − 300 W
    assert g.health(QUARTER)["budget_kw"] == -3.3


def test_the_remaining_time_is_at_least_one_second() -> None:
    g = guard()
    run(g, 0, 890, every=10)
    assert step(g, 899).budget_w == 2000.0 * 3600.0 - 300.0  # noch 1 s
    assert step(g, 899.5).budget_w == 2000.0 * 3600.0 - 300.0  # noch 0,5 s: gerechnet wird mit 1 s


def test_a_budget_that_is_not_finite_makes_the_step_a_gap() -> None:
    # Global Constraint: grid_budget_w ist nie NaN oder ±∞. Mit absurdem grid_wh läuft
    # (2000 Wh − 1e306 Wh) · 3600 über, und der Schritt rechnet nicht.
    g = guard()
    run(g, 0, 5)
    assert step(g, 6, wh=1e306) == NO_LIMITS
    assert g.health(QUARTER) == {
        "state": "gap",
        "target_kw": 8.0,
        **NO_VALUES,
        "quarter_end": NEXT_QUARTER.isoformat(),
    }
    assert step(g, 7).budget_w is not None  # nur dieser Schritt: Die Viertelstunde ist keine Lücke


def test_a_budget_that_is_not_finite_does_not_end_exceeded() -> None:
    g = guard()
    run(g, 0, 60, grid_w=12300.0, wh=draw(12300.0))
    assert step(g, 61, grid_w=12300.0, wh=1e306) == NO_LIMITS
    assert g.health(QUARTER)["state"] == "exceeded"
    assert values(g) == NO_VALUES


def test_the_margin_comes_from_the_configuration() -> None:
    assert (
        run(guard(peak_margin_kw=0.0), 0, 300, wh=lambda s: 1000.0 if s == 300 else 0.0).budget_w
        == 6000.0
    )
    assert (
        run(guard(peak_margin_kw=1.0), 0, 300, wh=lambda s: 1000.0 if s == 300 else 0.0).budget_w
        == 5000.0
    )


def test_without_a_mean_the_budget_is_known_but_not_the_projection() -> None:
    g = guard()
    # Der erste Schritt hat noch keine Bilanz, liegt aber im Anfang der Viertelstunde.
    limits = step(g, 0, grid_w=None)
    assert limits.budget_w == 7700.0  # (2000 Wh − 0 Wh) · 3600 / 900 s − 300 W
    assert g.health(QUARTER)["state"] == "active"
    assert values(g) == {
        "energy_kwh": 0.0,
        "projected_kw": None,
        "budget_kw": 7.7,
        "reduction_kw": None,
    }


# --- Boost ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "energy_kwh", "projected_kw"),
    [
        # 61 Schritte zu 3600 W, 60 Intervalle zu 3600 W · 1 s = 60 Wh Boost; Rest 840 s
        (Effective.BOOST, 0.44, 1.76),  # (500 − 60) Wh, Mittel 0: (440 + 0) · 4 / 1000
        (Effective.NOW, 0.5, 5.36),  # nichts abgezogen: (500 + 3600 · 840 / 3600) · 4 / 1000
    ],
)
def test_boost_energy_and_power_are_left_out(mode, energy_kwh, projected_kw) -> None:
    # Design 6.1, 6.4: Das Auto „egolf“ an „evcs“ bezieht 3600 W aus dem Netz
    car = PeakCar("egolf", "evcs", mode, 0, 3600.0, 3600.0, 1380.0)
    g = guard()
    run(g, 0, 60, grid_w=3600.0, wallbox_grid_w=SHARE, cars=[car], wh=lambda s: 500.0)
    health = g.health(QUARTER)
    assert health["energy_kwh"] == pytest.approx(energy_kwh)
    assert health["projected_kw"] == pytest.approx(projected_kw)
    assert health["reduction_kw"] == 0.0
    assert health["state"] == "active"


def test_the_energy_so_far_is_not_below_zero() -> None:
    g = guard()
    run(g, 0, 60, grid_w=3600.0, wallbox_grid_w=SHARE, cars=[EGOLF_BOOST], wh=lambda s: 10.0)
    # 60 Wh Boost gegen 10 Wh im Zähler der Viertelstunde (Messfehler): nie weniger als 0
    assert g.health(QUARTER)["energy_kwh"] == 0.0
    assert g.health(QUARTER)["budget_kw"] == pytest.approx(2000.0 * 3600.0 / 840.0 / 1000.0 - 0.3)


def test_only_the_cars_in_boost_are_left_out() -> None:
    twc = PeakCar("tesla", "twc", Effective.PV, 1, 1000.0, 1000.0, 1380.0)
    g = guard()
    run(
        g,
        0,
        60,
        grid_w=4600.0,
        wh=lambda s: 500.0,
        cars=[EGOLF_BOOST, twc],
        wallbox_grid_w={"evcs": 3600.0, "twc": 1000.0},
    )
    health = g.health(QUARTER)
    assert health["energy_kwh"] == pytest.approx(0.44)  # nur die 60 Wh der Wallbox in Boost
    # Das Mittel bleibt bei 1000 W, dem Anteil des Autos in PV-Überschuss.
    assert health["projected_kw"] == pytest.approx((440 + 1000 * 840 / 3600) * 4 / 1000)


@pytest.mark.parametrize("shares", [{}, {"other": 3600.0}, {"evcs": math.nan}, {"evcs": math.inf}])
def test_a_boost_car_without_a_usable_share_leaves_nothing_out(shares) -> None:
    g = guard()
    run(g, 0, 60, grid_w=3600.0, wallbox_grid_w=shares, cars=[EGOLF_BOOST], wh=lambda s: 500.0)
    health = g.health(QUARTER)
    assert health["energy_kwh"] == 0.5
    assert health["projected_kw"] == pytest.approx(5.36)  # das Mittel bleibt bei 3600 W


def test_a_boost_interval_across_the_quarter_boundary_is_not_integrated() -> None:  # Präzisierung 9
    g = guard()
    kw = {"grid_w": 3600.0, "wallbox_grid_w": SHARE, "cars": [EGOLF_BOOST], "wh": 100.0}
    step(g, -1, **kw)  # 17:59:59, die alte Viertelstunde; ihr Intervall bis 18:00:00 gehört ihr
    step(g, 0, **kw)
    assert g.health(QUARTER)["energy_kwh"] == 0.1  # nichts abgezogen: das Intervall zählt nicht
    step(g, 1, **kw)  # das Intervall 18:00:00 bis 18:00:01 gehört der neuen: 1 Wh
    assert g.health(QUARTER)["energy_kwh"] == pytest.approx(0.099)


def test_a_boost_interval_after_a_step_without_a_balance_is_not_integrated() -> None:
    g = guard()
    kw = {"wallbox_grid_w": SHARE, "cars": [EGOLF_BOOST], "wh": 100.0}
    step(g, 0, grid_w=3600.0, **kw)
    step(g, 1, grid_w=None, **kw)  # das Intervall 0 bis 1 zählt: 1 Wh
    # 1 bis 2 zählt nicht: Der Schritt davor hatte keine bekannte Leistung.
    step(g, 2, grid_w=3600.0, **kw)
    assert g.health(QUARTER)["energy_kwh"] == pytest.approx(0.099)


@pytest.mark.parametrize(("stall_s", "boost_wh"), [(1, 1.0), (5, 5.0), (6, 0.0), (10, 0.0)])
def test_a_boost_interval_counts_only_up_to_the_hold_limit(stall_s, boost_wh) -> None:
    # HOLD_MAX_S = 5 s: So weit integriert auch die Abrechnung. Was sie nicht zählt, steht nicht in
    # grid_wh und darf nicht als Boost davon abgezogen werden.
    g = guard()
    kw = {"grid_w": 3600.0, "wallbox_grid_w": SHARE, "cars": [EGOLF_BOOST], "wh": 100.0}
    step(g, 0, **kw)
    step(g, stall_s, **kw)  # 3600 W über stall_s Sekunden
    assert g.health(QUARTER)["state"] == "active"  # bis 10 s ist es keine Lücke
    assert g.health(QUARTER)["energy_kwh"] == pytest.approx((100.0 - boost_wh) / 1000.0)


def accounted_wh(seconds: Sequence[int], flows: Mapping[str, float]) -> tuple[float, float]:
    """Was die Abrechnung nach den Abtastpunkten ``seconds`` zählt, in Wh.

    Der Netzbezug und davon der der Wallbox „evcs“. Der Integrator hat die Standardgrenze, wie in
    der Laufzeit.
    """
    accumulator = SlotAccumulator([])
    for second in seconds:
        accumulator.add(at(second), flows, snap({}))
    counted = accumulator.flush().flows_wh
    grid_wh = sum(wh for key, wh in counted.items() if key.startswith("grid>"))
    return grid_wh, counted.get("grid>wb:evcs", 0.0)


def test_the_boost_energy_lines_up_with_the_accumulator_across_a_stall() -> None:
    # Dieselben Abtastpunkte für die Abrechnung und den Wächter, mit einer Pause von 6 s (3 bis 9)
    # und einer von 5 s (11 bis 16): Die Abrechnung zählt die lange nicht, die kurze schon.
    seconds = [0, 1, 2, 3, 9, 10, 11, 16, 17, 18]
    flows = {"grid>house": 500.0, "grid>wb:evcs": 3600.0}  # Netz 4100 W, davon 3600 W ins Auto
    g = guard()
    for index, second in enumerate(seconds):
        grid_wh, boost_wh = accounted_wh(seconds[: index + 1], flows)
        step(g, second, grid_w=4100.0, wh=grid_wh, wallbox_grid_w=SHARE, cars=[EGOLF_BOOST])
        # E_bisher des Wächters = Netzbezug der Abrechnung − ihre Energie an der Wallbox in Boost
        assert g.health(QUARTER)["energy_kwh"] * 1000 == pytest.approx(grid_wh - boost_wh, abs=1e-9)
    assert boost_wh == pytest.approx(12.0)  # 3 + 2 + 5 + 2 s zu 3600 W; die 6 s fehlen


def test_the_mean_is_current_when_the_next_quarter_starts() -> None:
    g = guard()
    run(g, 300, 899, grid_w=9000.0)  # die Viertelstunde ist lückenhaft, das Mittel läuft trotzdem
    assert g.health(QUARTER)["state"] == "gap"
    step(g, NEXT_S, grid_w=0.0)
    assert g.health(QUARTER)["state"] == "active"
    # Das Mittel der 31 Werte von 18:14:30 bis 18:15:00: 30 · 9000 W und 0 W. Mit E = 0 und dem
    # ganzen Rest von 900 s ist die Hochrechnung gleich dem Mittel.
    assert g.health(QUARTER)["projected_kw"] == pytest.approx(30 * 9000 / 31 / 1000)


# --- Hochrechnung und Senkung ---------------------------------------------------------------------


def test_projection_and_reduction() -> None:
    g = guard()
    run(g, 0, 300, grid_w=9000.0, wh=lambda s: 1000.0 if s == 300 else 0.0)
    health = g.health(QUARTER)
    # 18:05, E 1000 Wh, Mittel 9000 W, Rest 600 s
    assert health["projected_kw"] == 10.0  # (1000 + 9000 · 600 / 3600) · 4 / 1000
    assert health["reduction_kw"] == pytest.approx(3.3)  # 9000 W − P_zul 5700 W
    assert health["budget_kw"] == 5.7


@pytest.mark.parametrize(
    ("grid_w", "wanted_w", "base_w", "reduction_kw"),
    [
        (0.0, 10700.0, 0.0, 5.0),  # Präzisierung 10: W über dem Mittel zählt
        (0.0, 9700.0, 1000.0, 5.0),  # und die Basis kommt dazu
        (9000.0, 3000.0, 0.0, 3.3),  # liegt Basis + W unter dem Mittel, zählt das Mittel
    ],
)
def test_wanted_grid_counts_when_it_is_higher_than_the_mean(
    grid_w, wanted_w, base_w, reduction_kw
) -> None:
    g = guard()
    run(
        g,
        0,
        300,
        grid_w=grid_w,
        wanted_w=wanted_w,
        base_w=base_w,
        wh=lambda s: 1000.0 if s == 300 else 0.0,
    )
    assert g.health(QUARTER)["reduction_kw"] == pytest.approx(
        reduction_kw
    )  # max(...) − P_zul 5700 W


@pytest.mark.parametrize(
    ("ceiling_w", "soc", "reduction_kw"),
    [
        (8000.0, 60.0, 2.3),  # Budget für den Akku: min(Basis + W, C) − P_zul = 8000 W − 5700 W
        (8000.0, 25.0, 5.0),  # keins, P_zul unter C: Basis + W − P_zul = 10700 W − 5700 W
        (8000.0, None, 5.0),  # ein unbekannter SOC gibt keins
        (5700.5, 25.0, 5.0),  # P_zul knapp unter C: noch die ganze Senkung
        (5700.0, 25.0, 0.0),  # P_zul gleich C: min(Basis + W, C) − P_zul = 0
        (5000.0, None, 0.0),  # P_zul über C: min(Basis + W, C) − P_zul unter 0
    ],
)
def test_the_wanted_grid_above_the_ceiling_counts_only_without_a_budget_below_it(
    ceiling_w, soc, reduction_kw
) -> None:
    # Präzisierung 10: W 10700 W liegt über der festen Grenze C, ohne Grenzen (S = 0); P_zul ist
    # 5700 W. Was die Autos über C wollen, liefert mit Budget der Akku. Ohne Budget hält Regel 2
    # den Override bei C: Liegt P_zul darunter, brächte erst die ganze Senkung den Netzbezug unter
    # P_zul; liegt es nicht darunter, bleibt der Netzbezug ohnehin bei höchstens P_zul. Health
    # zeigt die Senkung aus diesem Bedarf.
    g = guard(ceiling_w=ceiling_w)
    run(g, 0, 300, wanted_w=10700.0, soc=soc, wh=lambda s: 1000.0 if s == 300 else 0.0)
    assert g.health(QUARTER)["reduction_kw"] == pytest.approx(reduction_kw)


@pytest.mark.parametrize(
    ("wanted_w", "base_w"),
    [
        (None, 0.0),
        (10700.0, None),
        (None, None),
        (math.nan, 0.0),
        (10700.0, math.inf),
        (1e308, 1e308),  # beide endlich, aber die Summe läuft über: gilt wie unbekannt
    ],
)
def test_an_unknown_wanted_grid_or_base_leaves_only_the_mean(wanted_w, base_w) -> None:
    g = guard()
    run(
        g,
        0,
        300,
        grid_w=6000.0,
        wanted_w=wanted_w,
        base_w=base_w,
        wh=lambda s: 1000.0 if s == 300 else 0.0,
    )
    assert g.health(QUARTER)["reduction_kw"] == pytest.approx(0.3)  # 6000 W − 5700 W


def test_the_reduction_shown_is_never_negative() -> None:
    g = guard()
    run(g, 0, 300, grid_w=1000.0, wh=lambda s: 1000.0 if s == 300 else 0.0)  # weit unter P_zul
    assert g.health(QUARTER)["reduction_kw"] == 0.0


def test_a_short_dropout_of_the_balance_keeps_computing_with_the_mean() -> None:
    g = guard()
    run(g, 0, 100, grid_w=3000.0)
    limits = run(g, 101, 105, grid_w=None, wh=lambda s: 1000.0)  # 5 s ohne Bilanz
    assert g.health(QUARTER)["state"] == "active"
    assert limits.budget_w == pytest.approx((2000.0 - 1000.0) * 3600.0 / 795.0 - 300.0)
    assert g.health(QUARTER)["projected_kw"] == pytest.approx((1000 + 3000 * 795 / 3600) * 4 / 1000)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_a_quarter_without_a_finite_energy_counts_as_missing(bad) -> None:
    g = guard()
    run(g, 0, 5)
    assert step(g, 6, wh=bad) == NO_LIMITS
    assert g.health(QUARTER)["state"] == "gap"
    assert values(g) == NO_VALUES
    assert step(g, 7).budget_w is not None  # eine kurze Lücke ruht die Viertelstunde nicht


@pytest.mark.parametrize("bad", [None, math.nan, math.inf])
def test_a_balance_that_is_no_finite_number_is_unknown(bad) -> None:
    g = guard()
    run(g, 0, 4, grid_w=9000.0)
    run(g, 5, 8, grid_w=bad)  # das Mittel nimmt den Wert nicht auf
    assert g.health(QUARTER)["projected_kw"] == pytest.approx((0 + 9000 * 892 / 3600) * 4 / 1000)
    run(g, 9, 14, grid_w=bad)  # 10 s nach dem letzten bekannten Schritt: noch keine Lücke
    assert g.health(QUARTER)["state"] == "active"
    step(g, 15, grid_w=bad)
    assert g.health(QUARTER)["state"] == "gap"


# --- Lücken ---------------------------------------------------------------------------------------


def test_gap_over_10_s_rests_until_the_next_quarter() -> None:  # Präzisierung 9
    g = guard()
    run(g, 0, 4)
    limits = run(g, 5, 14, grid_w=None)  # 10 Schritte ohne Bilanz: 10 s nach dem letzten bekannten
    assert g.health(QUARTER)["state"] == "active"  # genau 10 s sind keine Lücke („mehr als 10 s“)
    assert limits.budget_w is not None
    limits = step(g, 15, grid_w=None)  # der 11. Schritt: 11 s
    assert limits == NO_LIMITS
    assert g.health(QUARTER) == {
        "state": "gap",
        "target_kw": 8.0,
        **NO_VALUES,
        "quarter_end": NEXT_QUARTER.isoformat(),
    }
    # Die Werte sind wieder da, die Viertelstunde bleibt bis zu ihrem Ende lückenhaft.
    assert run(g, 16, 899) == NO_LIMITS
    assert g.health(QUARTER)["state"] == "gap"
    # Ab 18:15:00 gilt die neue Viertelstunde, mit Werten ist sie aktiv.
    limits = step(g, NEXT_S)
    assert g.health(QUARTER)["state"] == "active"
    assert limits.budget_w == 7700.0


def test_exactly_10_s_between_known_steps_is_no_gap() -> None:
    g = guard()
    step(g, 0)
    step(g, 10)
    assert g.health(QUARTER)["state"] == "active"
    step(g, 20.001)  # 10,001 s nach dem letzten bekannten Schritt
    assert g.health(QUARTER)["state"] == "gap"


def test_start_mid_quarter_is_a_gap() -> None:  # Review Focus 4
    g = guard()
    assert step(g, 300) == NO_LIMITS  # 18:05:00, der erste Schritt
    assert g.health(QUARTER)["state"] == "gap"
    assert run(g, 301, 899) == NO_LIMITS  # trotz Werten jede Sekunde, bis 18:14:59
    assert g.health(QUARTER)["state"] == "gap"
    step(g, NEXT_S)  # 18:15:00
    assert g.health(QUARTER)["state"] == "active"


def test_the_first_step_up_to_10_s_after_the_quarter_start_is_no_gap() -> None:
    first = guard()
    step(first, 10)
    assert first.health(QUARTER)["state"] == "active"
    late = guard()
    step(late, 10.001)
    assert late.health(QUARTER)["state"] == "gap"


def test_idle_makes_the_quarter_a_gap() -> None:  # Review Focus 4
    g = guard()
    run(g, 0, 300)
    assert g.health(QUARTER)["state"] == "active"
    g.idle()
    assert g.health(QUARTER) == {
        "state": "gap",
        "target_kw": 8.0,
        **NO_VALUES,
        "quarter_end": NEXT_QUARTER.isoformat(),
    }
    assert run(g, 301, 899) == NO_LIMITS  # Schritte ab 18:05:01: lückenhaft bis zum Ende
    assert g.health(QUARTER)["state"] == "gap"
    step(g, NEXT_S)  # eine spätere Viertelstunde beginnt normal
    assert g.health(QUARTER)["state"] == "active"


def test_health_drops_exceeded_after_the_quarter_while_idle() -> None:
    g = guard()
    run(g, 0, 60, grid_w=12300.0, wh=draw(12300.0))
    g.idle()
    assert g.health(at(NEXT_S - 1))["state"] == "exceeded"  # die Viertelstunde läuft noch
    after = g.health(at(NEXT_S))
    assert after["state"] == "gap"  # das Ende der Viertelstunde: Der Alarm ist schon weg
    assert after == {**g.health(QUARTER), "state": "gap"}  # sonst bleibt die Form


def test_idle_forgets_the_plan_budget() -> None:
    g = guard(5.0)
    step(g, 0, target_kw=3.5)
    assert g.health(QUARTER)["target_kw"] == 3.5
    g.idle()
    assert g.health(QUARTER)["target_kw"] == 5.0  # control.peak_target_kw
    assert g.target_kw() == 5.0
    assert g.health(QUARTER)["state"] == "gap"  # der Zustand bleibt, wie ``idle`` ihn setzt


def test_idle_before_the_first_step_changes_nothing() -> None:
    g = guard()
    g.idle()
    assert g.health(QUARTER)["state"] == "gap"  # wie vor dem ersten Schritt
    step(g, 0)
    assert g.health(QUARTER)["state"] == "active"


def test_a_step_without_a_quarter_is_a_gap_in_that_step_only() -> None:
    g = guard()
    run(g, 0, 20)
    assert step(g, 21, observed=False) == NO_LIMITS
    assert g.health(QUARTER) == {
        "state": "gap",
        "target_kw": 8.0,
        **NO_VALUES,
        "quarter_end": NEXT_QUARTER.isoformat(),
    }
    assert step(g, 22).budget_w is not None  # der Ausfall dauerte keine 10 s
    assert g.health(QUARTER)["state"] == "active"


def test_a_long_dropout_of_the_quarter_makes_the_quarter_a_gap() -> None:
    g = guard()
    run(g, 0, 20)
    run(g, 21, 31, observed=False)  # 11 s nach dem letzten bekannten Schritt
    assert run(g, 32, 899) == NO_LIMITS
    assert g.health(QUARTER)["state"] == "gap"


def test_a_quarter_of_another_slot_counts_as_missing() -> None:
    g = guard()
    run(g, 0, 5)
    stale = QuarterObs(QUARTER - timedelta(minutes=15), 0.0, 0.0, {})  # die Viertelstunde 17:45
    assert g.step(at(6), stale, (), 0.0, 0.0, 60.0, target_kw=8.0) == NO_LIMITS
    assert g.health(QUARTER)["state"] == "gap"
    assert values(g) == NO_VALUES
    assert step(g, 7).budget_w is not None
    # derselbe Zeitpunkt in Wiener Zeit ist dieselbe Viertelstunde
    local = QuarterObs(QUARTER.astimezone(LOCAL_TZ), 0.0, 0.0, {})
    assert g.step(at(8), local, (), 0.0, 0.0, 60.0, target_kw=8.0).budget_w is not None
    assert g.health(QUARTER)["state"] == "active"


# --- Sprünge der Wanduhr --------------------------------------------------------------------------


def test_two_steps_at_the_same_instant_count_no_interval() -> None:
    g = guard()
    boost = {"grid_w": 3600.0, "wallbox_grid_w": SHARE, "cars": [EGOLF_BOOST]}
    run(g, 0, 20, wh=lambda s: 100.0, **boost)  # 20 Wh Boost
    assert g.health(QUARTER)["energy_kwh"] == pytest.approx(0.08)
    # derselbe Zeitpunkt noch einmal: Ein Intervall von 0 zählt nichts
    step(g, 20, wh=100.0, **boost)
    assert g.health(QUARTER)["energy_kwh"] == pytest.approx(0.08)
    step(g, 21, wh=100.0, **boost)  # von hier an läuft sie weiter: 1 Wh
    assert g.health(QUARTER)["energy_kwh"] == pytest.approx(0.079)
    assert g.health(QUARTER)["state"] == "active"


def test_a_clock_that_jumps_back_within_the_quarter_makes_it_a_gap() -> None:
    g = guard()
    run(g, 0, 20)
    assert g.health(QUARTER)["state"] == "active"
    # Die Wanduhr springt 5 s zurück (NTP): Die Viertelstunde ist lückenhaft bis zu ihrem Ende.
    assert step(g, 15) == NO_LIMITS
    assert g.health(QUARTER) == {
        "state": "gap",
        "target_kw": 8.0,
        **NO_VALUES,
        "quarter_end": NEXT_QUARTER.isoformat(),
    }
    assert run(g, 16, 899) == NO_LIMITS  # trotz Werten jede Sekunde
    assert g.health(QUARTER)["state"] == "gap"
    step(g, NEXT_S)  # die nächste Viertelstunde beginnt normal
    assert g.health(QUARTER)["state"] == "active"


def test_a_clock_that_jumps_back_starts_the_mean_anew() -> None:
    g = guard()
    run(g, 860, 880, grid_w=9000.0)  # der erste Schritt mitten drin: lückenhaft, das Mittel läuft
    # Die Wanduhr springt auf 18:14:30 zurück: Das Mittel beginnt neu.
    run(g, 870, 899, grid_w=3000.0)
    step(g, NEXT_S, grid_w=0.0)
    # 30 Werte zu 3000 W und 0 W; die 9000 W von vor dem Sprung sind weg. Mit E = 0 und dem ganzen
    # Rest von 900 s ist die Hochrechnung gleich dem Mittel.
    assert g.health(QUARTER)["projected_kw"] == pytest.approx(30 * 3000 / 31 / 1000)


def test_a_step_in_another_quarter_than_the_previous_starts_that_quarter_anew() -> None:
    g = guard()
    run(g, 0, 60, grid_w=12300.0, wh=draw(12300.0))
    assert g.health(QUARTER)["state"] == "exceeded"
    # Die Uhr springt eine Stunde zurück, auf den Beginn einer Viertelstunde: Sie beginnt neu.
    step(g, -3600)
    assert g.alarms() == []
    assert g.health(QUARTER)["state"] == "active"
    assert g.health(QUARTER)["quarter_end"] == (QUARTER - timedelta(minutes=45)).isoformat()
    # Zwei Stunden vor, 5 s nach dem Beginn einer Viertelstunde: aktiv; 200 s danach: lückenhaft.
    step(g, 7200 + 5)
    assert g.health(QUARTER)["state"] == "active"
    step(g, 10800 + 200)
    assert g.health(QUARTER)["state"] == "gap"


# --- Überschritten --------------------------------------------------------------------------------


def test_exceeded_after_60_s_warns_until_the_quarter_end() -> None:  # Präzisierung 14
    g = guard()
    run(g, 0, 59, grid_w=12300.0, wh=draw(12300.0))
    assert g.alarms() == []
    assert g.health(QUARTER)["state"] == "active"
    limits = run(g, 60, 60, grid_w=12300.0, wh=draw(12300.0))
    alarm = Alarm(
        "peak.exceeded",
        Severity.WARNING,
        "15-min-Ziel überschritten: 12,3 kW in der Viertelstunde bis 18:15.",
        QUARTER + timedelta(seconds=60),
    )
    assert g.alarms() == [alarm]
    assert g.health(QUARTER)["state"] == "exceeded"
    # Der Wächter ruht nicht: Auch überschritten rechnet er weiter und gibt das Budget aus.
    assert limits.budget_w == pytest.approx(7392.857142857143)  # (2000 − 205) · 3600 / 840 − 300
    # Danach bleibt der Alarm bis 18:14:59 gleich ...
    run(g, 61, 899, grid_w=12300.0, wh=draw(12300.0))
    assert g.alarms() == [alarm]
    assert g.health(QUARTER)["state"] == "exceeded"
    # ... und ab 18:15:00 ist er weg.
    step(g, NEXT_S, grid_w=12300.0)
    assert g.alarms() == []
    assert g.health(QUARTER)["state"] == "active"


@pytest.mark.parametrize(
    ("grid_w", "text"),
    [
        (12300.0, "12,3"),
        (12340.0, "12,3"),  # auf 0,1 kW gerundet
        (12360.0, "12,4"),
        (10060.0, "10,1"),
        (10040.0, "10"),  # ``de`` schreibt keine Nachkommastelle, die 0 ist
    ],
)
def test_the_alarm_names_the_rounded_projection(grid_w, text) -> None:
    g = guard()
    run(g, 0, 60, grid_w=grid_w, wh=draw(grid_w))
    (alarm,) = g.alarms()
    assert alarm.message == f"15-min-Ziel überschritten: {text} kW in der Viertelstunde bis 18:15."


@pytest.mark.parametrize(
    ("start", "until"),
    [
        (datetime(2026, 10, 14, 16, 0, tzinfo=UTC), "18:15"),  # Sommerzeit, UTC+2
        (datetime(2026, 12, 1, 10, 30, tzinfo=UTC), "11:45"),  # Winterzeit, UTC+1
        (datetime(2026, 12, 1, 22, 45, tzinfo=UTC), "00:00"),  # das Ende fällt auf Mitternacht
    ],
)
def test_the_alarm_names_the_end_of_the_quarter_in_vienna_time(start, until) -> None:
    g = guard()
    for second in range(61):
        now = start + timedelta(seconds=second)
        quarter = QuarterObs(slot_start(now), draw(12300.0)(second), 12300.0, {})
        g.step(now, quarter, (), 0.0, 0.0, 60.0, target_kw=8.0)
    (alarm,) = g.alarms()
    assert alarm.message.endswith(f"in der Viertelstunde bis {until}.")
    assert alarm.since == start + timedelta(seconds=60)


def test_the_alarm_text_and_since_stay_fixed() -> None:
    g = guard()
    run(g, 0, 60, grid_w=12300.0, wh=draw(12300.0))
    (first,) = g.alarms()
    run(g, 61, 150, grid_w=20000.0, wh=draw(20000.0))  # die Hochrechnung steigt weiter
    assert g.health(QUARTER)["projected_kw"] > 12.3
    assert g.alarms() == [first]


def test_exceeded_needs_60_s_without_interruption() -> None:  # Präzisierung 14
    # Mittel 6000 W: Mit E 1000 Wh liegt die Hochrechnung über dem Ziel, mit E 0 Wh darunter.
    g = guard()
    run(g, 0, 58, grid_w=6000.0, wh=lambda s: 1000.0)  # 59 s darüber
    run(g, 59, 59, grid_w=6000.0, wh=lambda s: 0.0)  # ein Schritt darunter beginnt die Zählung neu
    assert g.health(QUARTER)["state"] == "active"
    run(g, 60, 119, grid_w=6000.0, wh=lambda s: 1000.0)  # wieder 59 s darüber
    assert g.alarms() == []
    run(g, 120, 120, grid_w=6000.0, wh=lambda s: 1000.0)
    (alarm,) = g.alarms()
    assert alarm.since == at(120)
    assert alarm.message == "15-min-Ziel überschritten: 9,2 kW in der Viertelstunde bis 18:15."


def test_a_step_that_does_not_compute_interrupts_the_count() -> None:
    g = guard()
    run(g, 0, 58, grid_w=12300.0, wh=draw(12300.0))
    step(g, 59, observed=False)  # kein Schritt mit Rechnung
    run(g, 60, 119, grid_w=12300.0, wh=draw(12300.0))
    assert g.alarms() == []
    run(g, 120, 120, grid_w=12300.0, wh=draw(12300.0))
    assert [alarm.since for alarm in g.alarms()] == [at(120)]


def test_a_projection_at_the_target_is_not_above_it() -> None:
    # Mittel 3600 W und E = s Wh: Die Hochrechnung ist in jeder Sekunde genau 3,6 kW (ganze Zahlen)
    at_target = guard(3.6)
    run(at_target, 0, 899, grid_w=3600.0, wh=float, target_kw=3.6)
    assert at_target.health(QUARTER)["projected_kw"] == 3.6
    assert at_target.health(QUARTER)["state"] == "active"
    assert at_target.alarms() == []
    below_target = guard(3.5)
    run(below_target, 0, 60, grid_w=3600.0, wh=float, target_kw=3.5)
    assert below_target.health(QUARTER)["state"] == "exceeded"


def test_exceeded_stays_until_the_quarter_end_through_a_gap() -> None:
    g = guard()
    run(g, 0, 60, grid_w=12300.0, wh=draw(12300.0))
    (alarm,) = g.alarms()
    # Die Bilanz fällt aus; ab 18:01:11, mehr als 10 s nach dem letzten bekannten Schritt, ist
    # die Viertelstunde lückenhaft. „Überschritten“ geht vor.
    run(g, 61, 70, grid_w=None, wh=draw(12300.0))
    assert run(g, 71, 71, grid_w=None, wh=draw(12300.0)) == NO_LIMITS
    assert g.health(QUARTER) == {
        "state": "exceeded",
        "target_kw": 8.0,
        **NO_VALUES,
        "quarter_end": NEXT_QUARTER.isoformat(),
    }
    assert g.alarms() == [alarm]
    # Die Werte kommen zurück, die Viertelstunde bleibt lückenhaft: keine Grenzen, überschritten.
    assert run(g, 72, 899, grid_w=12300.0, wh=draw(12300.0)) == NO_LIMITS
    assert g.health(QUARTER)["state"] == "exceeded"
    assert g.alarms() == [alarm]
    step(g, NEXT_S)
    assert g.alarms() == []
    assert g.health(QUARTER)["state"] == "active"


def test_exceeded_stays_when_a_step_has_no_quarter() -> None:
    g = guard()
    run(g, 0, 60, grid_w=12300.0, wh=draw(12300.0))
    (alarm,) = g.alarms()
    assert step(g, 61, observed=False) == NO_LIMITS
    assert g.health(QUARTER) == {
        "state": "exceeded",
        "target_kw": 8.0,
        **NO_VALUES,
        "quarter_end": NEXT_QUARTER.isoformat(),
    }
    assert g.alarms() == [alarm]
    # Mit dem nächsten Schritt rechnet der Wächter wieder; die Viertelstunde bleibt überschritten.
    assert step(g, 62, grid_w=12300.0, wh=draw(12300.0)(62)).budget_w is not None
    assert g.health(QUARTER)["state"] == "exceeded"


def test_idle_keeps_exceeded_but_has_no_values() -> None:
    g = guard()
    run(g, 0, 60, grid_w=12300.0, wh=draw(12300.0))
    (alarm,) = g.alarms()
    g.idle()
    assert g.health(QUARTER) == {
        "state": "exceeded",
        "target_kw": 8.0,
        **NO_VALUES,
        "quarter_end": NEXT_QUARTER.isoformat(),
    }
    assert g.alarms() == [alarm]
    assert run(g, 61, 899, grid_w=12300.0, wh=draw(12300.0)) == NO_LIMITS  # lückenhaft bis zum Ende
    assert g.health(QUARTER)["state"] == "exceeded"


def test_health_names_the_end_of_the_current_quarter() -> None:
    g = guard()
    step(g, 7 * 60)
    assert g.health(QUARTER)["quarter_end"] == "2026-10-14T16:15:00+00:00"
    step(g, NEXT_S + 60)
    assert g.health(QUARTER)["quarter_end"] == "2026-10-14T16:30:00+00:00"


# --- Druck für die Klimaanlage (Plan 6a, Entscheidung 5.2) ---------------------------------------


def test_peak_pressure_flag() -> None:
    g = guard()
    assert not g.pressure()  # vor dem ersten Schritt
    run(g, 0, 60, grid_w=2000.0, wh=draw(2000.0))
    assert g.health(QUARTER)["state"] == "active"
    assert g.health(QUARTER)["reduction_kw"] == 0.0
    assert not g.pressure()
    # 18:05 mit 9000 W: verlangte Senkung 3,3 kW
    pressed = guard()
    run(pressed, 0, 300, grid_w=9000.0, wh=lambda s: 1000.0 if s == 300 else 0.0)
    assert pressed.health(QUARTER)["reduction_kw"] == pytest.approx(3.3)
    assert pressed.pressure()
    # Überschritten zählt auch ohne Senkung: Nach 60 s mit 12,3 kW fällt der Bezug auf 0, das
    # Mittel ist nach ``MEAN_S`` leer, die Viertelstunde bleibt überschritten.
    over = guard()
    run(over, 0, 60, grid_w=12300.0, wh=draw(12300.0))
    run(over, 61, 120, grid_w=0.0, wh=lambda s: 12300.0 * 60 / 3600)
    assert over.health(QUARTER)["state"] == "exceeded"
    assert over.health(QUARTER)["reduction_kw"] == 0.0
    assert over.pressure()
    # Ohne Ziel ruht der Wächter: kein Druck.
    step(over, 121, target_kw=None)
    assert not over.pressure()
    # Ein Schritt, der nicht rechnet (``gap``), macht keinen Druck.
    gap = guard()
    step(gap, 0, observed=False)
    assert gap.health(QUARTER)["state"] == "gap"
    assert not gap.pressure()
