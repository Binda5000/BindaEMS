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
