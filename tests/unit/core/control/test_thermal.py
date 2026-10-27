"""Betriebsart und Strategie der Klimaanlage (Plan 6a, Entscheidungen 4, 5 und 11)."""

import inspect
import re
from dataclasses import replace
from typing import Any

import pytest
from tests.helpers import snap

from bindaems.core.control import thermal as thermal_module
from bindaems.core.control.thermal import (
    ThermalMode,
    ThermalObs,
    ThermalStrategy,
    ThermalWish,
    _reset_reason,
    offset_for,
    thermal_mode,
)
from bindaems.shared.config import CoeOffsetConfig, Config
from bindaems.shared.decisions import _REASON_TEXT
from bindaems.shared.domain import Quality, SignalKind, Value

P = "thermal.klima."


def states(**values: Value) -> dict[str, Value]:
    return {P + name: value for name, value in values.items()}


def mode(**values: Value) -> str:
    return thermal_mode(snap(states(**values), kind=SignalKind.STATE, source="klima"), "klima")


def test_not_running_is_off() -> None:
    assert mode(running=False, cooling=True) == "aus"
    assert mode(running=False) == "aus"


@pytest.mark.parametrize(
    ("heating", "venting", "cooling", "expected"),
    [
        (True, False, False, "heizen"),
        (False, True, False, "lueften"),
        (False, False, True, "kuehlen"),
        # widersprüchliche Bits: die Reihenfolge der Vorgabe entscheidet (Review Focus 2)
        (True, False, True, "heizen"),
        (True, True, True, "heizen"),
        (False, True, True, "lueften"),
        # läuft ohne gesetzte Betriebsart
        (False, False, False, "unbekannt"),
    ],
)
def test_running_follows_the_order(
    heating: bool, venting: bool, cooling: bool, expected: str
) -> None:
    assert mode(running=True, heating=heating, venting=venting, cooling=cooling) == expected


def test_unknown_values_give_unknown() -> None:
    assert mode() == "unbekannt"
    assert mode(heating=True) == "unbekannt"  # ``running`` fehlt
    assert mode(running=True) == "unbekannt"  # alle drei fehlen
    # ein unbekanntes Bit vor dem gesetzten: es könnte vorgehen
    assert mode(running=True, venting=True) == "unbekannt"
    assert mode(running=True, heating=False, venting=False) == "unbekannt"
    # ein gesetztes Bit vor dem unbekannten entscheidet
    assert mode(running=True, heating=True) == "heizen"


def test_only_ok_booleans_count() -> None:
    stale = snap(
        states(running=True, cooling=True),
        kind=SignalKind.STATE,
        quality=Quality.STALE,
        source="klima",
    )
    assert thermal_mode(stale, "klima") == "unbekannt"
    assert mode(running=1, heating=False, venting=False, cooling=True) == "unbekannt"
    assert mode(running=True, heating=0, venting=False, cooling=True) == "unbekannt"


def test_other_device_is_not_read() -> None:
    assert thermal_mode(snap(states(running=True, cooling=True)), "andere") == "unbekannt"


# --- Strategie --------------------------------------------------------------------------------

INTERVAL = 600.0
CODES = {
    "thermal_more",
    "thermal_back",
    "thermal_cars",
    "thermal_battery",
    "thermal_less",
    "thermal_peak",
    "thermal_mode",
    "thermal_stale",
    "thermal_no_echo",
    "thermal_off",
    "thermal_plan",
}


@pytest.fixture
def klima(cfg: Config) -> CoeOffsetConfig:
    device = cfg.thermal.devices["klima"]
    assert isinstance(device, CoeOffsetConfig)
    assert device.step_interval_s == INTERVAL
    assert device.max_offset_k == 3.0
    return device


@pytest.fixture
def strategy(klima: CoeOffsetConfig) -> ThermalStrategy:
    return ThermalStrategy(klima)


def obs(**changes: Any) -> ThermalObs:
    """Eine neutrale Beobachtung: kühlt, frisch, Akku voll, keine Einspeisung, kein Druck."""
    base = ThermalObs(
        now=0.0,
        fresh=True,
        mode="kuehlen",
        degraded=False,
        controllable=True,
        export_w=0.0,
        import_w=0.0,
        soc_pct=95.0,
        soc_gate_pct=90.0,
        hint="neutral",
        peak_pressure=False,
        phase_pressure=False,
        unmet_cars_w=0.0,
    )
    return replace(base, **changes)


def climb(strategy: ThermalStrategy, steps: int) -> float:
    """Treibt die Strategie mit Einspeisung auf ``steps`` Stufen; gibt die letzte Zeit zurück."""
    now = 0.0
    strategy.step(obs(now=now, export_w=900))
    for _ in range(steps):
        now += INTERVAL
        wish = strategy.step(obs(now=now, export_w=900))
    assert steps == 0 or wish.steps == steps
    return now


@pytest.mark.parametrize(
    ("mode", "steps", "offset"),
    [("kuehlen", 2, -1.0), ("kuehlen", -2, 1.0), ("heizen", 2, 1.0), ("heizen", -2, -1.0)],
)
def test_offset_sign(mode: ThermalMode, steps: int, offset: float) -> None:
    assert offset_for(ThermalWish(steps, "x"), mode, 0.5) == offset


@pytest.mark.parametrize("mode", ["lueften", "aus", "unbekannt"])
def test_offset_is_zero_without_heating_or_cooling(mode: ThermalMode) -> None:
    assert offset_for(ThermalWish(3, "x"), mode, 0.5) == 0.0


def test_offset_zero_steps_is_positive_zero() -> None:
    assert str(offset_for(ThermalWish(0, "x"), "kuehlen", 0.5)) == "0.0"


def test_initial_wish_is_off(strategy: ThermalStrategy) -> None:
    assert strategy.step(obs(mode="aus")) == ThermalWish(0, "thermal_off")


def test_more_after_600s_export(strategy: ThermalStrategy) -> None:
    assert strategy.step(obs(now=0, export_w=900)).steps == 0
    assert strategy.step(obs(now=599, export_w=900)).steps == 0
    assert strategy.step(obs(now=600, export_w=900)) == ThermalWish(1, "thermal_more")


def test_export_exactly_at_threshold_counts(strategy: ThermalStrategy) -> None:
    strategy.step(obs(now=0, export_w=800))
    assert strategy.step(obs(now=600, export_w=800)) == ThermalWish(1, "thermal_more")


def test_export_below_threshold_does_not_count(strategy: ThermalStrategy) -> None:
    for now in (0, 300, 600, 900):
        assert strategy.step(obs(now=now, export_w=799)).steps == 0


def test_export_interrupted_restarts_timer(strategy: ThermalStrategy) -> None:
    strategy.step(obs(now=0, export_w=900))
    strategy.step(obs(now=500, export_w=900))
    strategy.step(obs(now=550, export_w=100))  # unterbrochen
    assert strategy.step(obs(now=600, export_w=900)).steps == 0  # läuft ab 600 neu
    assert strategy.step(obs(now=1199, export_w=900)).steps == 0
    assert strategy.step(obs(now=1200, export_w=900)) == ThermalWish(1, "thermal_more")


def test_unknown_export_interrupts_and_never_fires(strategy: ThermalStrategy) -> None:
    strategy.step(obs(now=0, export_w=900))
    assert strategy.step(obs(now=600, export_w=None)).steps == 0
    assert strategy.step(obs(now=1200, export_w=900)).steps == 0


def test_one_step_per_interval_up_to_max(strategy: ThermalStrategy) -> None:
    now = 0.0
    strategy.step(obs(now=now, export_w=900))
    seen = []
    for _ in range(10):
        now += INTERVAL
        seen.append(strategy.step(obs(now=now, export_w=900)).steps)
    assert seen == [1, 2, 3, 4, 5, 6, 6, 6, 6, 6]
    assert now >= 3600


def test_no_second_step_before_the_interval(strategy: ThermalStrategy) -> None:
    climb(strategy, 1)
    assert strategy.step(obs(now=1199, export_w=900)).steps == 1
    assert strategy.step(obs(now=1200, export_w=900)).steps == 2


# --- Rang 1: sofort 0 -------------------------------------------------------------------------


def test_mode_change_zero_immediately(strategy: ThermalStrategy) -> None:
    climb(strategy, 3)
    assert strategy.step(obs(now=1801, mode="heizen")) == ThermalWish(0, "thermal_mode")


def test_first_cycle_is_not_a_mode_change(strategy: ThermalStrategy) -> None:
    assert strategy.step(obs(mode="heizen")).reason != "thermal_mode"


def test_mode_change_resets_timers(strategy: ThermalStrategy) -> None:
    strategy.step(obs(now=0, export_w=900))
    strategy.step(obs(now=500, mode="heizen", export_w=900))  # Wechsel
    assert strategy.step(obs(now=600, mode="heizen", export_w=900)).steps == 0
    assert strategy.step(obs(now=1199, mode="heizen", export_w=900)).steps == 0
    assert strategy.step(obs(now=1200, mode="heizen", export_w=900)).steps == 1


def test_stale_zero(strategy: ThermalStrategy) -> None:
    climb(strategy, 2)
    assert strategy.step(obs(now=1300, fresh=False)) == ThermalWish(0, "thermal_stale")


def test_not_controllable_zero(strategy: ThermalStrategy) -> None:
    climb(strategy, 2)
    assert strategy.step(obs(now=1300, controllable=False)) == ThermalWish(0, "thermal_no_echo")


def test_reset_reason_without_echo_is_no_echo() -> None:
    # Werte da, Aktor aber nicht steuerbar (kein Echo oder gesperrt): eigener Grund
    assert _reset_reason(obs(fresh=True, controllable=False), None) == "thermal_no_echo"
    # keine frischen Werte: weiter ``thermal_stale``, auch wenn der Aktor nicht steuerbar ist
    assert _reset_reason(obs(fresh=False), None) == "thermal_stale"
    assert _reset_reason(obs(fresh=False, controllable=False), None) == "thermal_stale"
    assert _reset_reason(obs(), None) is None


def test_every_code_of_the_strategy_has_a_text() -> None:
    codes = set(re.findall(r'"(thermal_\w+)"', inspect.getsource(thermal_module)))
    assert CODES <= codes
    for code in sorted(codes):
        assert code in _REASON_TEXT, code


def test_degraded_zero(strategy: ThermalStrategy) -> None:
    climb(strategy, 2)
    assert strategy.step(obs(now=1300, degraded=True)) == ThermalWish(0, "thermal_off")


@pytest.mark.parametrize("mode", ["lueften", "aus", "unbekannt"])
def test_off_modes_zero(strategy: ThermalStrategy, mode: ThermalMode) -> None:
    climb(strategy, 2)
    assert strategy.step(obs(now=1300, mode=mode)) == ThermalWish(0, "thermal_off")


def test_stale_wins_over_everything(strategy: ThermalStrategy) -> None:
    climb(strategy, 2)
    wish = strategy.step(obs(now=1300, fresh=False, degraded=True, mode="aus", peak_pressure=True))
    assert wish == ThermalWish(0, "thermal_stale")


def test_zero_resets_timers(strategy: ThermalStrategy) -> None:
    strategy.step(obs(now=0, export_w=900))
    strategy.step(obs(now=300, fresh=False))
    assert strategy.step(obs(now=600, export_w=900)).steps == 0  # Zeit begann bei 600
    assert strategy.step(obs(now=1200, export_w=900)).steps == 1


# --- Rang 2: Druck ----------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["peak_pressure", "phase_pressure"])
def test_pressure_drops_positive_to_zero(strategy: ThermalStrategy, flag: str) -> None:
    climb(strategy, 3)
    assert strategy.step(obs(now=1801, **{flag: True})) == ThermalWish(0, "thermal_peak")


@pytest.mark.parametrize("flag", ["peak_pressure", "phase_pressure"])
def test_pressure_blocks_new_positive_steps(strategy: ThermalStrategy, flag: str) -> None:
    strategy.step(obs(now=0, export_w=900, **{flag: True}))
    assert strategy.step(obs(now=600, export_w=900, **{flag: True})).steps == 0
    assert strategy.step(obs(now=1200, export_w=900, **{flag: True})).steps == 0
    assert strategy.step(obs(now=1800, export_w=900)) == ThermalWish(1, "thermal_more")


def test_pressure_keeps_negative(strategy: ThermalStrategy) -> None:
    less = dict(hint="weniger", export_w=0.0)
    strategy.step(obs(now=0, **less))
    assert strategy.step(obs(now=600, **less)) == ThermalWish(-1, "thermal_less")
    assert strategy.step(obs(now=700, peak_pressure=True, **less)) == ThermalWish(
        -1, "thermal_less"
    )


def test_pressure_wins_over_cars_and_export(strategy: ThermalStrategy) -> None:
    climb(strategy, 2)
    wish = strategy.step(obs(now=1300, peak_pressure=True, unmet_cars_w=500, export_w=900))
    assert wish.reason == "thermal_peak"


# --- Rang 3: Autos ----------------------------------------------------------------------------


def test_cars_unmet_steps_back(strategy: ThermalStrategy) -> None:
    now = climb(strategy, 3)
    # sofort eine Stufe, dann eine je Intervall
    assert strategy.step(obs(now=now + 1, unmet_cars_w=230)) == ThermalWish(2, "thermal_cars")
    assert strategy.step(obs(now=now + 600, unmet_cars_w=230)).steps == 2
    assert strategy.step(obs(now=now + 601, unmet_cars_w=230)) == ThermalWish(1, "thermal_cars")
    assert strategy.step(obs(now=now + 1201, unmet_cars_w=230)) == ThermalWish(0, "thermal_cars")


def test_cars_below_one_step_do_not_count(strategy: ThermalStrategy) -> None:
    now = climb(strategy, 2)
    assert strategy.step(obs(now=now + 1, unmet_cars_w=229)).steps == 2


def test_cars_interrupted_steps_back_immediately_again(strategy: ThermalStrategy) -> None:
    now = climb(strategy, 3)
    strategy.step(obs(now=now + 1, unmet_cars_w=230))  # 2
    strategy.step(obs(now=now + 2, unmet_cars_w=0))  # unterbrochen
    assert strategy.step(obs(now=now + 3, unmet_cars_w=230)).steps == 1


def test_cars_hold_blocks_further_increase(strategy: ThermalStrategy) -> None:
    now = climb(strategy, 2)
    strategy.step(obs(now=now + 1, unmet_cars_w=230, export_w=900))  # 1
    # die Einspeisung läuft weiter, die Autos wollen noch: keine Stufe mehr
    wish = strategy.step(obs(now=now + 300, unmet_cars_w=230, export_w=900))
    assert wish.steps == 1
    assert strategy.step(obs(now=now + 601, unmet_cars_w=230, export_w=900)).steps == 0


def test_cars_block_new_positive_step_from_zero(strategy: ThermalStrategy) -> None:
    strategy.step(obs(now=0, export_w=900, unmet_cars_w=230))
    assert strategy.step(obs(now=600, export_w=900, unmet_cars_w=230)).steps == 0


def test_cars_do_not_touch_zero_or_negative(strategy: ThermalStrategy) -> None:
    less = dict(hint="weniger", export_w=0.0, unmet_cars_w=230)
    strategy.step(obs(now=0, **less))
    assert strategy.step(obs(now=600, **less)).steps == -1


def test_cars_win_over_battery(strategy: ThermalStrategy) -> None:
    now = climb(strategy, 3)
    wish = strategy.step(obs(now=now + 1, unmet_cars_w=300, soc_pct=50))
    assert wish == ThermalWish(2, "thermal_cars")


# --- Rang 4: Akku -----------------------------------------------------------------------------


def test_battery_gate(strategy: ThermalStrategy) -> None:
    # SOC 80 unter der Schwelle 90: kein neuer Schritt
    strategy.step(obs(now=0, export_w=900, soc_pct=80))
    assert strategy.step(obs(now=600, export_w=900, soc_pct=80)).steps == 0
    # genau die Schwelle genügt
    assert strategy.step(obs(now=1200, export_w=900, soc_pct=90)) == ThermalWish(1, "thermal_more")


def test_battery_below_gate_steps_back(strategy: ThermalStrategy) -> None:
    now = climb(strategy, 3)
    # 85 ist noch nicht unter 85: nichts
    assert strategy.step(obs(now=now + 1, soc_pct=85)).steps == 3
    # 84 < 85: sofort eine Stufe, dann je Intervall
    assert strategy.step(obs(now=now + 2, soc_pct=84)) == ThermalWish(2, "thermal_battery")
    assert strategy.step(obs(now=now + 601, soc_pct=84)).steps == 2
    assert strategy.step(obs(now=now + 602, soc_pct=84)) == ThermalWish(1, "thermal_battery")


def test_battery_between_margin_and_gate_holds(strategy: ThermalStrategy) -> None:
    now = climb(strategy, 2)
    wish = strategy.step(obs(now=now + 700, soc_pct=87, export_w=900))
    assert wish.steps == 2  # kein neuer Schritt, kein Schritt zurück


def test_battery_uses_the_gate_of_the_slot(strategy: ThermalStrategy) -> None:
    now = climb(strategy, 2)
    assert strategy.step(obs(now=now + 1, soc_pct=70, soc_gate_pct=75)).steps == 2
    assert strategy.step(obs(now=now + 2, soc_pct=69, soc_gate_pct=75)).steps == 1


def test_unknown_soc_blocks_new_step_but_no_step_back(strategy: ThermalStrategy) -> None:
    strategy.step(obs(now=0, export_w=900, soc_pct=None))
    assert strategy.step(obs(now=600, export_w=900, soc_pct=None)).steps == 0
    assert strategy.step(obs(now=700, export_w=900, soc_pct=95)).steps == 1
    # mit bestehendem Wunsch kein Schritt zurück ohne SOC
    assert strategy.step(obs(now=800, soc_pct=None)).steps == 1
    assert strategy.step(obs(now=2000, soc_pct=None)).steps == 1


def test_hint_mehr_skips_battery_gate(strategy: ThermalStrategy) -> None:
    strategy.step(obs(now=0, export_w=900, soc_pct=40, hint="mehr"))
    assert strategy.step(obs(now=600, export_w=900, soc_pct=40, hint="mehr")) == ThermalWish(
        1, "thermal_more"
    )
    # und auch kein Schritt zurück wegen des Akkus
    assert strategy.step(obs(now=700, soc_pct=40, hint="mehr")).steps == 1
    assert strategy.step(obs(now=700, soc_pct=40, hint="mehr", export_w=None)).steps == 1


def test_hint_mehr_does_not_skip_cars(strategy: ThermalStrategy) -> None:
    now = climb(strategy, 2)
    wish = strategy.step(obs(now=now + 1, hint="mehr", unmet_cars_w=230))
    assert wish == ThermalWish(1, "thermal_cars")


# --- Rang 5: mehr -----------------------------------------------------------------------------


def test_more_from_negative_is_not_rate_limited_but_waits_for_the_export_timer(
    strategy: ThermalStrategy,
) -> None:
    less = dict(hint="weniger", export_w=0.0)
    strategy.step(obs(now=0, **less))
    strategy.step(obs(now=600, **less))  # -1
    strategy.step(obs(now=1200, **less))  # -2
    strategy.step(obs(now=1300, hint="weniger", export_w=900))
    wish = strategy.step(obs(now=1900, hint="weniger", export_w=900))
    assert wish == ThermalWish(-1, "thermal_more")
    assert strategy.step(obs(now=2500, hint="weniger", export_w=900)) == ThermalWish(
        0, "thermal_more"
    )


def test_end_of_the_hint_goes_to_zero_before_the_export_timer_fires(
    strategy: ThermalStrategy,
) -> None:
    less = dict(hint="weniger", export_w=0.0)
    strategy.step(obs(now=0, **less))
    strategy.step(obs(now=600, **less))  # -1
    assert strategy.step(obs(now=700, export_w=900)) == ThermalWish(0, "thermal_less")
    assert strategy.step(obs(now=1300, export_w=900)) == ThermalWish(1, "thermal_more")


# --- Rang 6: Netzbezug ------------------------------------------------------------------------


def test_import_300w_for_300s_steps_back(strategy: ThermalStrategy) -> None:
    now = climb(strategy, 2)
    assert strategy.step(obs(now=now + 1, import_w=300)).steps == 2
    assert strategy.step(obs(now=now + 300, import_w=300)).steps == 2
    assert strategy.step(obs(now=now + 301, import_w=300)) == ThermalWish(1, "thermal_back")
    # die Zeit beginnt nach jedem Schritt neu
    assert strategy.step(obs(now=now + 600, import_w=300)).steps == 1
    assert strategy.step(obs(now=now + 601, import_w=300)) == ThermalWish(0, "thermal_back")
    assert strategy.step(obs(now=now + 1000, import_w=300)).steps == 0


def test_import_interrupted_restarts(strategy: ThermalStrategy) -> None:
    now = climb(strategy, 2)
    strategy.step(obs(now=now + 1, import_w=300))
    strategy.step(obs(now=now + 200, import_w=299))
    assert strategy.step(obs(now=now + 301, import_w=300)).steps == 2
    assert strategy.step(obs(now=now + 601, import_w=300)) == ThermalWish(1, "thermal_back")


def test_unknown_import_never_fires(strategy: ThermalStrategy) -> None:
    now = climb(strategy, 2)
    strategy.step(obs(now=now + 1, import_w=300))
    assert strategy.step(obs(now=now + 400, import_w=None)).steps == 2


def test_import_does_not_touch_negative(strategy: ThermalStrategy) -> None:
    less = dict(hint="weniger", export_w=0.0, import_w=500)
    strategy.step(obs(now=0, **less))
    strategy.step(obs(now=600, **less))
    assert strategy.step(obs(now=1000, **less)).steps == -1


# --- Rang 7: weniger --------------------------------------------------------------------------


def test_less_with_hint_until_minus_two(strategy: ThermalStrategy) -> None:
    less = dict(hint="weniger", export_w=0.0)
    assert strategy.step(obs(now=0, **less)).steps == 0
    assert strategy.step(obs(now=599, **less)).steps == 0
    assert strategy.step(obs(now=600, **less)) == ThermalWish(-1, "thermal_less")
    assert strategy.step(obs(now=1199, **less)).steps == -1
    assert strategy.step(obs(now=1200, **less)) == ThermalWish(-2, "thermal_less")
    assert strategy.step(obs(now=5000, **less)).steps == -2  # nicht weiter


def test_less_max_steps_zero_never_goes_negative(klima: CoeOffsetConfig) -> None:
    strategy = ThermalStrategy(
        klima.model_copy(
            update={"strategy": klima.strategy.model_copy(update={"less_max_steps": 0})}
        )
    )
    strategy.step(obs(now=0, hint="weniger"))
    assert strategy.step(obs(now=5000, hint="weniger")).steps == 0


def test_less_needs_little_export(strategy: ThermalStrategy) -> None:
    strategy.step(obs(now=0, hint="weniger", export_w=100))
    assert strategy.step(obs(now=700, hint="weniger", export_w=100)).steps == 0
    strategy.step(obs(now=800, hint="weniger", export_w=99))
    assert strategy.step(obs(now=1400, hint="weniger", export_w=99)).steps == -1


def test_less_unknown_export_never_fires(strategy: ThermalStrategy) -> None:
    strategy.step(obs(now=0, hint="weniger", export_w=None))
    assert strategy.step(obs(now=700, hint="weniger", export_w=None)).steps == 0


def test_less_ends_with_hint(strategy: ThermalStrategy) -> None:
    less = dict(hint="weniger", export_w=0.0)
    strategy.step(obs(now=0, **less))
    strategy.step(obs(now=600, **less))
    strategy.step(obs(now=1200, **less))
    assert strategy.step(obs(now=1201, hint="neutral")) == ThermalWish(0, "thermal_less")
    assert strategy.step(obs(now=1202, hint="neutral")).steps == 0


def test_less_steps_down_through_a_positive_wish(strategy: ThermalStrategy) -> None:
    now = climb(strategy, 1)  # +1
    strategy.step(obs(now=now + 1, hint="weniger", export_w=0))
    # Verringerung des Betrags: keine Wartezeit auf die letzte Erhöhung, aber auf das Intervall
    assert strategy.step(obs(now=now + 601, hint="weniger", export_w=0)) == ThermalWish(
        0, "thermal_less"
    )
    assert strategy.step(obs(now=now + 1201, hint="weniger", export_w=0)).steps == -1


def test_increase_of_magnitude_waits_since_last_increase_in_both_directions(
    strategy: ThermalStrategy,
) -> None:
    now = climb(strategy, 1)  # letzte Erhöhung bei ``now``
    strategy.step(obs(now=now + 1, hint="weniger", export_w=0))
    strategy.step(obs(now=now + 601, hint="weniger", export_w=0))  # 0
    # die Zeit seit der letzten Erhöhung (600 s alt) ist abgelaufen: -1 geht
    assert strategy.step(obs(now=now + 1201, hint="weniger", export_w=0)).steps == -1


def test_less_after_a_recent_increase_keeps_the_interval(strategy: ThermalStrategy) -> None:
    # +1 bei 600 (letzte Erhöhung); „weniger“ senkt erst auf 0 und geht dann ins Negative
    strategy.step(obs(now=0, export_w=900))
    strategy.step(obs(now=600, export_w=900))  # +1, letzte Erhöhung 600
    strategy.step(obs(now=601, hint="weniger", export_w=0))
    assert strategy.step(obs(now=1201, hint="weniger", export_w=0)).steps == 0
    wish = strategy.step(obs(now=1801, hint="weniger", export_w=0))
    assert wish == ThermalWish(-1, "thermal_less")


def test_more_after_a_recent_negative_step_keeps_the_interval(strategy: ThermalStrategy) -> None:
    # -1 bei 600 (letzte Erhöhung des Betrags); danach 0 und erst ein Intervall später +1
    strategy.step(obs(now=0, hint="weniger", export_w=0))
    strategy.step(obs(now=600, hint="weniger", export_w=0))  # -1
    strategy.step(obs(now=601, hint="weniger", export_w=900))
    strategy.step(obs(now=1201, hint="weniger", export_w=900))  # 0 (Verringerung)
    wish = strategy.step(obs(now=1801, hint="weniger", export_w=900))
    assert wish == ThermalWish(1, "thermal_more")


def test_end_of_hint_wins_over_a_due_more_step_from_minus_two(
    strategy: ThermalStrategy,
) -> None:
    less = dict(hint="weniger", export_w=0.0)
    for now in (0, 600, 1200):
        strategy.step(obs(now=now, **less))  # -1, -2
    strategy.step(obs(now=1300, hint="weniger", export_w=900))  # der Export-Timer läuft
    # der Hinweis endet im Zyklus, in dem „mehr“ fällig wäre: 0, nicht -1
    assert strategy.step(obs(now=1900, export_w=900)) == ThermalWish(0, "thermal_less")


def test_hint_end_restarts_the_export_timer(strategy: ThermalStrategy) -> None:
    less = dict(hint="weniger", export_w=0.0)
    for now in (0, 600, 1200):
        strategy.step(obs(now=now, **less))  # -1, -2
    strategy.step(obs(now=1300, hint="weniger", export_w=900))  # die Einspeisung steht an
    # Zyklus 1: der Hinweis endet, die Wartezeit der Einspeisung wäre um
    assert strategy.step(obs(now=1900, export_w=900)) == ThermalWish(0, "thermal_less")
    # Zyklus 2, eine Sekunde später: weiter 0, nicht gleich +1
    assert strategy.step(obs(now=1901, export_w=900)).steps == 0
    # die volle Wartezeit der Strategie läuft ab dem Ende des Hinweises
    assert strategy.step(obs(now=1900 + INTERVAL - 1, export_w=900)).steps == 0
    assert strategy.step(obs(now=1900 + INTERVAL, export_w=900)) == ThermalWish(1, "thermal_more")


def import_backoff(strategy: ThermalStrategy, **changes: Any) -> dict[int, float]:
    """Wann (Sekunden nach dem Start) der Wunsch 3 mit Netzbezug 400 W auf 2, 1, 0 fällt."""
    start = climb(strategy, 3)
    reached: dict[int, float] = {}
    for tick in range(1, 2000):
        wish = strategy.step(obs(now=start + 10 * tick, import_w=400, **changes))
        reached.setdefault(wish.steps, 10.0 * tick)
    return reached


@pytest.mark.parametrize("changes", [{"unmet_cars_w": 230}, {"soc_pct": 50}])
def test_holding_priority_rule_does_not_slow_the_import_backoff(
    klima: CoeOffsetConfig, changes: dict[str, Any]
) -> None:
    alone = import_backoff(ThermalStrategy(klima))
    assert alone == {3: 10.0, 2: 310.0, 1: 610.0, 0: 910.0}
    with_priority = import_backoff(ThermalStrategy(klima), **changes)
    assert with_priority[0] <= alone[0]
    assert all(with_priority[steps] <= alone[steps] for steps in (2, 1, 0))


# --- Rest -------------------------------------------------------------------------------------


def test_holding_keeps_wish_and_reason(strategy: ThermalStrategy) -> None:
    now = climb(strategy, 2)
    assert strategy.step(obs(now=now + 5, export_w=500)) == ThermalWish(2, "thermal_more")


def test_reset_returns_to_the_initial_state(strategy: ThermalStrategy) -> None:
    now = climb(strategy, 3)
    strategy.reset()
    # kein Moduswechsel gegenüber dem Zyklus davor, kein Wunsch, keine laufende Zeit
    assert strategy.step(obs(now=now + 1, mode="heizen", export_w=900)) == ThermalWish(
        0, "thermal_off"
    )
    assert strategy.step(obs(now=now + 601, mode="heizen", export_w=900)).steps == 1
    strategy.reset()
    strategy.step(obs(now=0, export_w=900))
    strategy.reset()
    assert strategy.step(obs(now=600, export_w=900)).steps == 0  # die Zeit begann bei 0


@pytest.mark.parametrize(
    ("changes", "reason", "gap"),
    [
        ({}, "thermal_more", 300),
        ({"unmet_cars_w": 230}, "thermal_cars", 300),
        ({"soc_pct": 10}, "thermal_battery", 300),
        ({"import_w": 300}, "thermal_back", 300),
        ({"hint": "weniger"}, "thermal_less", 600),
        ({"peak_pressure": True}, "thermal_peak", 300),
        ({"mode": "heizen"}, "thermal_mode", 300),
        ({"fresh": False}, "thermal_stale", 300),
        ({"controllable": False}, "thermal_no_echo", 300),
        ({"mode": "aus"}, "thermal_off", 300),
    ],
)
def test_reasons_are_codes_of_the_plan(
    strategy: ThermalStrategy, changes: dict[str, Any], reason: str, gap: int
) -> None:
    now = climb(strategy, 3)
    strategy.step(obs(now=now + 1, **changes))
    wish = strategy.step(obs(now=now + 1 + gap, **changes))
    assert wish.reason == reason
    assert reason in CODES


# --- Planwert (Plan 5c, Task 8, Entscheidung 9) -----------------------------------------------


def planned(steps: int | None, **changes: Any) -> ThermalObs:
    """``obs`` mit dem Planwert ``steps`` und einem Akku, der nicht entlädt."""
    return obs(**{"planned_steps": steps, "battery_discharge_w": 0.0, **changes})


# Je Lauf die Zyklen (Zeit, Änderungen gegenüber ``obs``) und der Wunsch am Ende nach 6a
SIXA_RUNS: dict[str, tuple[list[tuple[float, dict[str, Any]]], ThermalWish]] = {
    "mehr": ([(t, {"export_w": 900}) for t in (0, 600, 1200)], ThermalWish(2, "thermal_more")),
    "autos": (
        [(0, {"export_w": 900}), (600, {"export_w": 900}), (601, {"unmet_cars_w": 230})],
        ThermalWish(0, "thermal_cars"),
    ),
    "akku": (
        [(0, {"export_w": 900}), (600, {"export_w": 900}), (601, {"soc_pct": 50})],
        ThermalWish(0, "thermal_battery"),
    ),
    "netzbezug": (
        [
            (0, {"export_w": 900}),
            (600, {"export_w": 900}),
            (610, {"import_w": 400}),
            (910, {"import_w": 400}),
        ],
        ThermalWish(0, "thermal_back"),
    ),
    "weniger": (
        [(t, {"hint": "weniger"}) for t in (0, 600, 1200, 1800)],
        ThermalWish(-2, "thermal_less"),
    ),
    "weniger endet": (
        [(0, {"hint": "weniger"}), (600, {"hint": "weniger"}), (601, {})],
        ThermalWish(0, "thermal_less"),
    ),
    "druck": (
        [(0, {"export_w": 900}), (600, {"export_w": 900}), (601, {"peak_pressure": True})],
        ThermalWish(0, "thermal_peak"),
    ),
    "betriebsart": (
        [(0, {"export_w": 900}), (600, {"export_w": 900}), (601, {"mode": "heizen"})],
        ThermalWish(0, "thermal_mode"),
    ),
    "veraltet": (
        [(0, {"export_w": 900}), (600, {"export_w": 900}), (601, {"fresh": False})],
        ThermalWish(0, "thermal_stale"),
    ),
    "totband": (
        [(0, {"export_w": 900}), (600, {"export_w": 900}), (700, {"export_w": 500})],
        ThermalWish(1, "thermal_more"),
    ),
}


@pytest.mark.parametrize("run", sorted(SIXA_RUNS))
def test_without_plan_slot_strategy_is_6a(klima: CoeOffsetConfig, run: str) -> None:
    # Ohne Planwert (``None``) sind Netzbezug des Plans und Entladung des Akkus ohne Wirkung: Die
    # Strategie wünscht in jedem Zyklus dasselbe wie die aus 6a, die die neuen Felder nicht kennt.
    cycles, expected = SIXA_RUNS[run]
    sixa, now = ThermalStrategy(klima), ThermalStrategy(klima)
    extra = {"planned_steps": None, "planned_grid_w": 1500.0, "battery_discharge_w": 2000.0}
    for at, changes in cycles:
        before = sixa.step(obs(now=at, **changes))
        assert now.step(obs(now=at, **changes, **extra)) == before, (run, at)
    assert before == expected


def test_follows_plan_one_step_per_interval(strategy: ThermalStrategy) -> None:
    # Planwert +3: +1 sofort, +2 nach 600 s, +3 nach 1200 s, Grund thermal_plan
    assert strategy.step(planned(3, now=0)) == ThermalWish(1, "thermal_plan")
    assert strategy.step(planned(3, now=599)).steps == 1
    assert strategy.step(planned(3, now=600)) == ThermalWish(2, "thermal_plan")
    assert strategy.step(planned(3, now=1199)).steps == 2
    assert strategy.step(planned(3, now=1200)) == ThermalWish(3, "thermal_plan")
    assert strategy.step(planned(3, now=5000)) == ThermalWish(3, "thermal_plan")  # nicht weiter


def test_plan_down_is_immediate(strategy: ThermalStrategy) -> None:
    strategy.step(planned(2, now=0))
    strategy.step(planned(2, now=600))  # +2, letzte Erhöhung bei 600
    # +2 → Planwert +1: im selben Zyklus
    assert strategy.step(planned(1, now=700)) == ThermalWish(1, "thermal_plan")
    strategy.step(planned(2, now=1300))  # +2, letzte Erhöhung bei 1300
    # +2 → Planwert −1: im selben Zyklus auf 0 (ein Vorzeichenwechsel geht nur über 0), dann −1,
    # sobald die Schrittregel eine Erhöhung des Betrags zulässt
    assert strategy.step(planned(-1, now=1400)) == ThermalWish(0, "thermal_plan")
    assert strategy.step(planned(-1, now=1899)).steps == 0
    assert strategy.step(planned(-1, now=1900)) == ThermalWish(-1, "thermal_plan")
    # von −1 auf Planwert +1: der Betrag sinkt, also sofort 0, dann +1 nach der Wartezeit
    assert strategy.step(planned(1, now=1901)) == ThermalWish(0, "thermal_plan")
    assert strategy.step(planned(1, now=2499)).steps == 0
    assert strategy.step(planned(1, now=2500)) == ThermalWish(1, "thermal_plan")


def test_plan_negative_steps_one_per_interval(strategy: ThermalStrategy) -> None:
    # Weniger als 0 heißt beim Planwert ebenfalls mehr Betrag: eine Stufe je Intervall
    assert strategy.step(planned(-2, now=0)) == ThermalWish(-1, "thermal_plan")
    assert strategy.step(planned(-2, now=599)).steps == -1
    assert strategy.step(planned(-2, now=600)) == ThermalWish(-2, "thermal_plan")


def test_plan_negative_replaces_hint_less(strategy: ThermalStrategy) -> None:
    less = dict(hint="weniger", export_w=0.0)
    assert strategy.step(planned(-1, now=0, **less)) == ThermalWish(-1, "thermal_plan")
    # „weniger“ führte nach 600 s auf −2; mit dem Planwert bleibt es bei −1
    for now in (600, 1200, 5000):
        assert strategy.step(planned(-1, now=now, **less)) == ThermalWish(-1, "thermal_plan")
    # Planwert 0 mit dem Hinweis: 0, und „weniger“ bleibt aus
    assert strategy.step(planned(0, now=5001, **less)) == ThermalWish(0, "thermal_plan")
    assert strategy.step(planned(0, now=7000, **less)).steps == 0
    # ohne Planwert gilt wieder 6a: Der Hinweis steht seit 7000 s an, „weniger“ handelt sofort
    assert strategy.step(planned(None, now=7001, **less)) == ThermalWish(-1, "thermal_less")


def test_plan_negative_does_not_end_with_the_hint(strategy: ThermalStrategy) -> None:
    # „weniger endet“ gilt nur ohne Planwert: ein negativer Planwert bleibt ohne Hinweis stehen
    strategy.step(planned(-1, now=0))
    assert strategy.step(planned(-1, now=1, hint="neutral")) == ThermalWish(-1, "thermal_plan")


def test_surplus_may_exceed_plan(strategy: ThermalStrategy) -> None:
    # Einspeisung 800 W seit 10 min → Planwert + 1
    assert strategy.step(planned(1, now=0, export_w=800)) == ThermalWish(1, "thermal_plan")
    assert strategy.step(planned(1, now=600, export_w=800)) == ThermalWish(2, "thermal_more")
    # der Planwert holt die Stufe nicht zurück, auch ohne Einspeisung (Totband wie 6a)
    assert strategy.step(planned(1, now=601, export_w=0)) == ThermalWish(2, "thermal_more")
    assert strategy.step(planned(1, now=3000, export_w=0)).steps == 2
    # der Planwert sinkt um eins: die Stufe aus dem Überschuss bleibt darüber
    assert strategy.step(planned(0, now=3001)) == ThermalWish(1, "thermal_plan")
    # der Planwert steigt: die Stufe aus dem Überschuss ist darin aufgegangen
    strategy.step(planned(2, now=3002))
    assert strategy.step(planned(1, now=3003)) == ThermalWish(1, "thermal_plan")


def test_surplus_before_the_plan_stays_above_it(klima: CoeOffsetConfig) -> None:
    # Stufen aus dem Überschuss, bevor es einen positiven Planwert gab, gelten als Stufen über ihm
    strategy = ThermalStrategy(klima)
    climb(strategy, 2)
    assert strategy.step(planned(1, now=1300)) == ThermalWish(2, "thermal_more")
    # bei einem Planwert von höchstens 0 nicht (Fix-Runde 1): Der Plan holt sie zurück
    other = ThermalStrategy(klima)
    climb(other, 2)
    assert other.step(planned(0, now=1300)) == ThermalWish(0, "thermal_plan")


def test_import_threshold_adds_planned_grid(strategy: ThermalStrategy) -> None:
    # 300 W + 1500 W geplant: kein Rückschritt bei 1200 W
    strategy.step(planned(1, now=0, export_w=900, planned_grid_w=1500))
    strategy.step(planned(1, now=600, export_w=900, planned_grid_w=1500))  # +2 (Überschuss)
    for now in (610, 1000, 3000):
        wish = strategy.step(planned(1, now=now, import_w=1200, planned_grid_w=1500))
        assert wish.steps == 2
    # ab 1800 W 300 s lang: eine Stufe zurück, aber nicht unter den Planwert
    strategy.step(planned(1, now=3010, import_w=1800, planned_grid_w=1500))
    assert strategy.step(planned(1, now=3309, import_w=1800, planned_grid_w=1500)).steps == 2
    wish = strategy.step(planned(1, now=3310, import_w=1800, planned_grid_w=1500))
    assert wish == ThermalWish(1, "thermal_back")
    assert strategy.step(planned(1, now=5000, import_w=1800, planned_grid_w=1500)).steps == 1


def test_import_does_not_undo_planned_steps(strategy: ThermalStrategy) -> None:
    # Der Netzbezug des Hauses (Nacht, Akku hält) holt geplante Stufen nicht zurück
    strategy.step(planned(2, now=0))
    strategy.step(planned(2, now=600))
    for now in (610, 1000, 3000):
        assert strategy.step(planned(2, now=now, import_w=3000)) == ThermalWish(2, "thermal_plan")


def test_battery_guard_steps_back_planned_heat(strategy: ThermalStrategy) -> None:
    # Entladung 400 W seit 300 s → eine Stufe zurück. Die SOC-Schwelle gilt für geplante Stufen
    # nicht: SOC 50 % unter der Schwelle 90 % hält den Plan weder auf noch zurück.
    heat = dict(mode="heizen", soc_pct=50.0)
    assert strategy.step(planned(2, now=0, **heat)) == ThermalWish(1, "thermal_plan")
    assert strategy.step(planned(2, now=600, **heat)) == ThermalWish(2, "thermal_plan")
    strategy.step(planned(2, now=700, battery_discharge_w=400, **heat))
    assert strategy.step(planned(2, now=999, battery_discharge_w=400, **heat)).steps == 2
    wish = strategy.step(planned(2, now=1000, battery_discharge_w=400, **heat))
    assert wish == ThermalWish(1, "thermal_battery")
    # entlädt er weiter: keine Stufe hoch, nach weiteren 300 s noch eine zurück
    assert strategy.step(planned(2, now=1299, battery_discharge_w=400, **heat)).steps == 1
    wish = strategy.step(planned(2, now=1300, battery_discharge_w=400, **heat))
    assert wish == ThermalWish(0, "thermal_battery")
    assert strategy.step(planned(2, now=3000, battery_discharge_w=400, **heat)).steps == 0
    # entlädt er nicht mehr: der Plan steigt wieder, eine Stufe je Intervall
    assert strategy.step(planned(2, now=3001, **heat)) == ThermalWish(1, "thermal_plan")
    assert strategy.step(planned(2, now=3601, **heat)) == ThermalWish(2, "thermal_plan")


def test_battery_guard_needs_300w_without_interruption(strategy: ThermalStrategy) -> None:
    strategy.step(planned(1, now=0))
    strategy.step(planned(1, now=10, battery_discharge_w=299))
    assert strategy.step(planned(1, now=400, battery_discharge_w=299)).steps == 1
    strategy.step(planned(1, now=410, battery_discharge_w=300))
    strategy.step(planned(1, now=500, battery_discharge_w=0))  # unterbrochen
    strategy.step(planned(1, now=510, battery_discharge_w=300))
    assert strategy.step(planned(1, now=809, battery_discharge_w=300)).steps == 1
    wish = strategy.step(planned(1, now=810, battery_discharge_w=300))
    assert wish == ThermalWish(0, "thermal_battery")


def test_unknown_battery_power_holds_the_plan(strategy: ThermalStrategy) -> None:
    # Unbekannte Akkuleistung: keine geplante Stufe hoch, aber auch kein Rückschritt
    assert strategy.step(planned(2, now=0, battery_discharge_w=None)).steps == 0
    strategy.step(planned(2, now=10))  # +1
    assert strategy.step(planned(2, now=700, battery_discharge_w=None)).steps == 1
    assert strategy.step(planned(2, now=2000, battery_discharge_w=None)).steps == 1


def test_battery_guard_only_for_a_positive_plan(strategy: ThermalStrategy) -> None:
    # Planwert 0 mit einer Stufe aus dem Überschuss: wie 6a die SOC-Schwelle, keine Entladung
    strategy.step(planned(0, now=0, export_w=900))
    assert strategy.step(planned(0, now=600, export_w=900)) == ThermalWish(1, "thermal_more")
    strategy.step(planned(0, now=700, battery_discharge_w=1000))
    assert strategy.step(planned(0, now=1100, battery_discharge_w=1000)).steps == 1
    assert strategy.step(planned(0, now=1101, soc_pct=50)) == ThermalWish(0, "thermal_battery")


def test_peak_pressure_zeroes_planned_positive(
    strategy: ThermalStrategy, klima: CoeOffsetConfig
) -> None:
    strategy.step(planned(3, now=0))
    strategy.step(planned(3, now=600))  # +2
    assert strategy.step(planned(3, now=700, peak_pressure=True)) == ThermalWish(0, "thermal_peak")
    # solange der Druck anhält, keine geplante Stufe
    assert strategy.step(planned(3, now=2000, phase_pressure=True)).steps == 0
    assert strategy.step(planned(3, now=2001)) == ThermalWish(1, "thermal_plan")
    # ein negativer Planwert bleibt unter Druck stehen
    other = ThermalStrategy(klima)
    other.step(planned(-1, now=0))
    assert other.step(planned(-1, now=1, peak_pressure=True)) == ThermalWish(-1, "thermal_plan")


def test_cars_priority_beats_plan(strategy: ThermalStrategy) -> None:
    strategy.step(planned(2, now=0))
    strategy.step(planned(2, now=600))  # +2
    cars = dict(unmet_cars_w=230)
    assert strategy.step(planned(2, now=601, **cars)) == ThermalWish(1, "thermal_cars")
    assert strategy.step(planned(2, now=1200, **cars)).steps == 1  # der Plan steigt nicht
    assert strategy.step(planned(2, now=1201, **cars)) == ThermalWish(0, "thermal_cars")
    assert strategy.step(planned(2, now=3000, **cars)).steps == 0
    assert strategy.step(planned(2, now=3001)) == ThermalWish(1, "thermal_plan")


def test_plan_steps_ignored_when_mode_changes(strategy: ThermalStrategy) -> None:
    # Ein Wechsel der Betriebsart geht vor dem Planwert: im selben Zyklus 0. Dass ein Plan für
    # Heizen beim Kühlen nie wirkt, sichert die Regelung (``test_regulation.py``).
    strategy.step(planned(2, now=0))
    strategy.step(planned(2, now=600))
    assert strategy.step(planned(2, now=601, mode="heizen")) == ThermalWish(0, "thermal_mode")
    for mode in ("lueften", "aus", "unbekannt"):
        assert strategy.step(planned(2, now=602, mode=mode)) == ThermalWish(0, "thermal_off")


def test_plan_value_beyond_the_band_is_clamped(strategy: ThermalStrategy) -> None:
    now = 0.0
    for _ in range(10):
        wish = strategy.step(planned(6, now=now))
        now += INTERVAL
    assert wish.steps == 6  # max_offset_k 3 K = 6 Stufen
    assert strategy.step(planned(6, now=now, export_w=900)).steps == 6
    assert strategy.step(planned(6, now=now + INTERVAL, export_w=900)).steps == 6


@pytest.mark.parametrize("changes", [{"soc_pct": 50.0}, {"unmet_cars_w": 230}])
def test_plan_down_goes_before_cars_and_battery(
    strategy: ThermalStrategy, changes: dict[str, Any]
) -> None:
    # Fällt der Planwert, geht der Wunsch sofort ganz zurück, nicht nur um die eine Stufe der
    # Autos oder des Akkus (SOC 50 % gilt bei einem Planwert von höchstens 0 wieder)
    strategy.step(planned(3, now=0))
    strategy.step(planned(3, now=600))
    strategy.step(planned(3, now=1200))  # +3
    assert strategy.step(planned(-2, now=1300, **changes)) == ThermalWish(0, "thermal_plan")


# --- Fix-Runde 1 ------------------------------------------------------------------------------


def test_surplus_does_not_lift_a_negative_plan(strategy: ThermalStrategy) -> None:
    # Planwert −2 erreicht, dann 900 W Einspeisung über Stunden: Stufen aus dem Überschuss zählen
    # nur über max(Planwert, 0); unter 0 gehört der Bereich dem Plan, der Wunsch bleibt −2
    strategy.step(planned(-2, now=0))
    strategy.step(planned(-2, now=600))  # −2
    for now in range(700, 9001, 100):
        assert strategy.step(planned(-2, now=now, export_w=900)).steps == -2, now
    # bei Planwert 0 nimmt der Überschuss wieder Stufen über 0
    strategy.step(planned(0, now=9001, export_w=900))  # 0 (sofort, der Betrag sinkt)
    assert strategy.step(planned(0, now=9601, export_w=900)) == ThermalWish(1, "thermal_more")


def test_positive_6a_steps_do_not_hold_a_negative_plan(strategy: ThermalStrategy) -> None:
    # 6a steht bei +2, dann beginnt ein Planwert −2: sofort 0, dann −1 und −2 je Intervall
    climb(strategy, 2)  # +2 bei 1200
    assert strategy.step(planned(-2, now=1300)) == ThermalWish(0, "thermal_plan")
    assert strategy.step(planned(-2, now=1800)) == ThermalWish(-1, "thermal_plan")
    assert strategy.step(planned(-2, now=2400)) == ThermalWish(-2, "thermal_plan")


def test_surplus_steps_clear_when_the_wish_falls_to_the_plan(strategy: ThermalStrategy) -> None:
    # +2 aus Planwert +1 und einer Stufe Überschuss; die Autos nehmen eine Stufe: Der Wunsch
    # steht wieder beim Planwert, und die Stufe aus dem Überschuss ist verbraucht
    strategy.step(planned(1, now=0, export_w=900))
    strategy.step(planned(1, now=600, export_w=900))  # +2 (Überschuss)
    assert strategy.step(planned(1, now=601, unmet_cars_w=230)) == ThermalWish(1, "thermal_cars")
    # der Planwert sinkt auf 0: kein Überschuss mehr darüber, also 0
    assert strategy.step(planned(0, now=602)) == ThermalWish(0, "thermal_plan")


def test_end_of_the_plan_drops_planned_steps_at_once(strategy: ThermalStrategy) -> None:
    # Entscheidung 8: Endet der Planwert (abgelaufen, keine Absicht, andere Betriebsart), gehen die
    # geplanten Stufen sofort auf 0, auch mit SOC über der Schwelle und einem Akku, der die Last
    # deckt; nur Stufen aus dem Überschuss bleiben für 6a
    strategy.step(planned(2, now=0))
    strategy.step(planned(2, now=600))  # +2
    covering = dict(soc_pct=95.0, battery_discharge_w=1000.0)
    assert strategy.step(planned(None, now=601, **covering)) == ThermalWish(0, "thermal_plan")
    assert strategy.step(planned(None, now=5000, **covering)).steps == 0


def test_end_of_the_plan_limits_a_deep_negative_wish_to_6a(strategy: ThermalStrategy) -> None:
    # Planwert −4 mit dem Hinweis „weniger“: Endet der Planwert, gilt 6a, und dort reicht
    # „weniger“ nur bis −less_max_steps (−2). Der Wunsch geht im selben Zyklus auf −2, nicht erst
    # mit dem Ende des Hinweises.
    less = dict(hint="weniger", export_w=0.0)
    for now in (0, 600, 1200, 1800):
        strategy.step(planned(-4, now=now, **less))
    assert strategy.wish == ThermalWish(-4, "thermal_plan")
    assert strategy.step(planned(None, now=1801, **less)) == ThermalWish(-2, "thermal_plan")
    # danach 6a: „weniger“ bleibt bei −2
    for now in (2400, 3000, 9000):
        assert strategy.step(planned(None, now=now, **less)).steps == -2
    # ohne den Hinweis wie 6a sofort 0
    assert strategy.step(planned(None, now=9001)) == ThermalWish(0, "thermal_less")


def test_end_of_the_plan_without_the_hint_drops_a_negative_wish_to_zero(
    strategy: ThermalStrategy,
) -> None:
    for now in (0, 600, 1200, 1800):
        strategy.step(planned(-4, now=now))
    assert strategy.step(planned(None, now=1801)) == ThermalWish(0, "thermal_less")


def test_end_of_the_plan_keeps_a_negative_wish_within_6a(strategy: ThermalStrategy) -> None:
    less = dict(hint="weniger", export_w=0.0)
    strategy.step(planned(-1, now=0, **less))
    assert strategy.step(planned(None, now=1, **less)) == ThermalWish(-1, "thermal_plan")


def test_end_of_the_plan_keeps_the_surplus_steps(strategy: ThermalStrategy) -> None:
    strategy.step(planned(1, now=0, export_w=900))
    strategy.step(planned(1, now=600, export_w=900))  # +2: Planwert +1 und eine Stufe Überschuss
    assert strategy.step(planned(None, now=601)) == ThermalWish(1, "thermal_plan")
    # danach 6a: Totband, die Stufe bleibt
    assert strategy.step(planned(None, now=2000)) == ThermalWish(1, "thermal_plan")


def test_no_surplus_step_while_the_battery_discharges_under_a_positive_plan(
    strategy: ThermalStrategy,
) -> None:
    # Einspeisung 900 W und zugleich 1000 W Entladung: weder Plan noch Überschuss steigen
    both = dict(export_w=900, battery_discharge_w=1000)
    for now in (0, 600, 1200, 1800):
        assert strategy.step(planned(2, now=now, **both)).steps == 0, now
    # ohne Entladung steigen beide wieder
    assert strategy.step(planned(2, now=1801, export_w=900)) == ThermalWish(1, "thermal_plan")


def test_planned_property_is_the_clamped_plan_value(klima: CoeOffsetConfig) -> None:
    narrow = ThermalStrategy(klima.model_copy(update={"max_offset_k": 1.0}))
    narrow.step(planned(6, now=0))
    assert narrow.planned == 2
    narrow.step(planned(-6, now=1))
    assert narrow.planned == -2
    narrow.step(planned(None, now=2))
    assert narrow.planned is None


def test_a_later_send_moves_the_step_timer(strategy: ThermalStrategy) -> None:
    # Ging die Erhöhung erst später hinaus (Schreibvorgang offen), zählt die Wartezeit ab dem
    # Senden: die Safety-Schicht rechnet ab dort
    strategy.step(planned(2, now=0))  # +1, entschieden bei 0
    strategy.note_raised(5.0)  # gesendet bei 5
    assert strategy.step(planned(2, now=600)).steps == 1
    assert strategy.step(planned(2, now=605)) == ThermalWish(2, "thermal_plan")
    strategy.note_raised(1.0)  # früher als die letzte Erhöhung: ändert nichts
    assert strategy.step(planned(3, now=1204)).steps == 2
    assert strategy.step(planned(3, now=1205)).steps == 3
