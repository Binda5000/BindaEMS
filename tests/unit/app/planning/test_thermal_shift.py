"""Tests des zweiten Planungsschritts (Plan 5c, Task 6, Präzisierung 6): Vorheizen und Vorkühlen
als kleines LP auf den fertigen Slots des Plans für Akku und Autos.

Alle Werte sind erfunden. ``S`` ist der Speicher in kWh am Slotende; die Tests rechnen ihn aus
``thermal_w`` mit derselben Rekursion nach, die das LP verwendet
(``S_t = a · S_{t−1} + thermal_w · Δ / 1000``, ``S_{−1} = start_k · C``).
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any, NoReturn

import pulp
import pytest

from bindaems.app.planning import thermal_shift
from bindaems.app.planning.thermal_shift import (
    MIN_SAVING_CT,
    ShiftInput,
    ShiftResult,
    ShiftSlot,
    shift,
)
from bindaems.shared.timeutil import SLOT

DT_H = 0.25
T0 = datetime(2026, 1, 12, 22, 0, tzinfo=UTC)
TOL_W = 1e-3
TOL_KWH = 1e-6


def slot(
    i: int,
    *,
    import_ct: float = 20.0,
    base_w: float = 800.0,
    on_share: float = 1.0,
    export_w: float = 0.0,
    import_w: float | None = None,
    discharge_w: float = 0.0,
    feed_in_ct: float | None = None,
    budget_w: float | None = None,
) -> ShiftSlot:
    """Ein Slot des fertigen Plans; ohne Angabe bezieht das Haus die Grundleistung aus dem Netz."""
    return ShiftSlot(
        start=T0 + i * SLOT,
        base_w=base_w,
        on_share=on_share,
        export_w=export_w,
        import_w=base_w if import_w is None else import_w,
        discharge_w=discharge_w,
        import_ct=import_ct,
        feed_in_ct=feed_in_ct,
        budget_w=budget_w,
    )


def shift_input(slots: list[ShiftSlot], **overrides: Any) -> ShiftInput:
    """Heizen mit C 2 kWh/K, τ 20 h, höchstens 2000 W, Band −0,5 … +1,0 K."""
    values: dict[str, Any] = {
        "mode": "heizen",
        "c_kwh_per_k": 2.0,
        "tau_h": 20.0,
        "max_w": 2000.0,
        "up_k": 1.0,
        "down_k": 0.5,
        "margin_ct": 2.0,
        "end_value_ct": 12.0,
        "start_k": 0.0,
        "slots": slots,
    }
    values.update(overrides)
    return ShiftInput(**values)


def price_of(i: int) -> float:
    """Nacht billig (0–15), Übergang (16–23), Morgen teuer (24–31), danach wieder billig."""
    if i < 16:
        return 10.0
    if i < 24:
        return 20.0
    if i < 32:
        return 40.0
    return 10.0


def night_cheap_morning_expensive(**overrides: Any) -> ShiftInput:
    return shift_input([slot(i, import_ct=price_of(i)) for i in range(48)], **overrides)


def store(inp: ShiftInput, result: ShiftResult) -> list[float]:
    """Speicher am Ende jedes Slots, ``[S_{−1}, S_0, …]``, nachgerechnet aus ``thermal_w``."""
    a = math.exp(-DT_H / inp.tau_h)
    start = min(max(inp.start_k, -inp.down_k), inp.up_k) * inp.c_kwh_per_k
    values = [start]
    for item in result.slots:
        values.append(a * values[-1] + item.thermal_w * DT_H / 1000)
    return values


def all_zero(result: ShiftResult) -> bool:
    return all(s.thermal_w == 0 and s.grid_w == 0 and s.steps == 0 for s in result.slots)


def test_preheats_before_expensive_morning() -> None:
    r = shift(night_cheap_morning_expensive())
    assert r.reason == "ok" and r.saving_ct > 2
    assert max(s.steps for s in r.slots[:16]) > 0 and min(s.steps for s in r.slots[24:32]) < 0
    assert [s.start for s in r.slots] == [T0 + i * SLOT for i in range(48)]
    # Mehrverbrauch nur in billigen Slots, Minderverbrauch im teuren Morgen
    assert all(s.thermal_w <= TOL_W for s in r.slots[16:32])
    assert sum(s.thermal_w for s in r.slots[24:32]) < 0


def test_steps_follow_the_store() -> None:
    inp = night_cheap_morning_expensive()
    r = shift(inp)
    for s_end, item in zip(store(inp, r)[1:], r.slots, strict=True):
        assert item.steps == thermal_shift._steps(s_end / inp.c_kwh_per_k / 0.5, -1, 2)


def test_uses_planned_export_before_grid() -> None:
    sunny = [
        slot(i, import_ct=30.0, export_w=600.0, import_w=0.0, feed_in_ct=5.0) for i in range(8)
    ]
    evening = [slot(i, import_ct=40.0) for i in range(8, 16)]
    r = shift(shift_input(sunny + evening))
    assert r.reason == "ok"
    from_export = [s.thermal_w - s.grid_w for s in r.slots[:8]]
    assert sum(from_export) > 0
    for s in r.slots[:8]:
        if s.grid_w > TOL_W:  # Netz erst, wenn die Einspeisung des Slots ganz genutzt ist
            assert s.thermal_w - s.grid_w == pytest.approx(600.0, abs=TOL_W)
        assert s.thermal_w - s.grid_w <= 600.0 + TOL_W


def test_no_grid_up_while_battery_discharges() -> None:
    inp = night_cheap_morning_expensive()
    slots = [
        replace(s, discharge_w=800.0, import_w=0.0) if i < 8 else s for i, s in enumerate(inp.slots)
    ]
    r = shift(replace(inp, slots=slots))
    assert r.reason == "ok"
    assert all(s.grid_w == 0 and s.thermal_w <= 0 for s in r.slots[:8])
    assert max(s.grid_w for s in r.slots[8:16]) > 0


def test_small_discharge_still_allows_grid() -> None:
    inp = night_cheap_morning_expensive()
    slots = [replace(s, discharge_w=50.0) if i < 16 else s for i, s in enumerate(inp.slots)]
    r = shift(replace(inp, slots=slots))
    assert max(s.grid_w for s in r.slots[:16]) > 0


def test_respects_budget() -> None:
    inp = night_cheap_morning_expensive()
    slots = [replace(s, budget_w=700.0 if i < 4 else 1000.0) for i, s in enumerate(inp.slots)]
    r = shift(replace(inp, slots=slots))
    assert r.reason == "ok"
    assert max(s.grid_w for s in r.slots) > 0
    for planned, shifted in zip(slots, r.slots, strict=True):
        assert planned.budget_w is not None
        if planned.import_w >= planned.budget_w:
            assert shifted.grid_w == 0
        else:
            assert planned.import_w + shifted.grid_w <= planned.budget_w + TOL_W


def test_steps_stay_in_band() -> None:
    r = shift(night_cheap_morning_expensive(up_k=1.0, down_k=0.5))
    steps = [s.steps for s in r.slots]
    assert all(-1 <= s <= 2 for s in steps)
    assert min(steps) == -1 and max(steps) == 2


def test_start_outside_band_is_clamped() -> None:
    inp = night_cheap_morning_expensive(start_k=5.0)
    r = shift(inp)
    assert r.reason == "ok"
    assert all(-1 <= s.steps <= 2 for s in r.slots)
    assert all(s <= inp.up_k * inp.c_kwh_per_k + TOL_KWH for s in store(inp, r))


def test_at_most_one_step_up_per_slot() -> None:
    # großer Speicher je Slot möglich (5200 W · 0,25 h = 1,3 kWh), die Schrittregel erlaubt 0,5 C
    inp = night_cheap_morning_expensive(c_kwh_per_k=1.0, max_w=6000.0)
    r = shift(inp)
    values = store(inp, r)
    rises = [b - a for a, b in pairwise(values)]
    assert max(rises) == pytest.approx(0.5 * inp.c_kwh_per_k, abs=TOL_KWH)  # die Regel greift
    assert all(rise <= 0.5 * inp.c_kwh_per_k + TOL_KWH for rise in rises)
    steps = [s.steps for s in r.slots]
    assert all(b - a <= 1 for a, b in pairwise(steps))


def test_storage_ends_non_negative() -> None:
    # teuer am Ende: ohne S_end ≥ 0 würde das LP bis zum Horizontende absenken
    slots = [slot(i, import_ct=10.0 if i < 16 else 40.0) for i in range(32)]
    inp = shift_input(slots)
    r = shift(inp)
    assert r.reason == "ok"
    assert store(inp, r)[-1] >= -TOL_KWH
    assert r.slots[-1].steps >= 0
    assert min(s.thermal_w for s in r.slots[16:]) < 0


def test_flat_price_is_not_worth() -> None:
    r = shift(shift_input([slot(i) for i in range(96)]))
    assert r.reason == "not_worth" and r.saving_ct < MIN_SAVING_CT
    assert len(r.slots) == 96 and all_zero(r)


def test_small_spread_is_not_worth() -> None:
    slots = [slot(i, import_ct=20.0 if i < 8 else 23.0) for i in range(16)]
    r = shift(shift_input(slots))
    assert r.reason == "not_worth" and 0 < r.saving_ct < MIN_SAVING_CT
    assert all_zero(r)


def test_off_quarters_are_not_shifted() -> None:
    inp = night_cheap_morning_expensive()
    slots = [
        replace(s, on_share=0.5) if 24 <= i < 28 else replace(s, on_share=0.8)
        for i, s in enumerate(inp.slots)
    ]
    r = shift(replace(inp, slots=slots))
    assert r.reason == "ok"
    assert all(s.thermal_w == 0 and s.grid_w == 0 for s in r.slots[24:28])
    assert min(s.thermal_w for s in r.slots[28:32]) < 0  # Laufanteil 0,8 zählt als laufend


def test_shift_energy_balance_within_losses() -> None:
    """Mehrenergie ``E = Σ thermal_w · Δ / 1000`` gegen die Verluste des Speichers.

    Aus ``S_t = a · S_{t−1} + thermal_w_t · Δ / 1000`` folgt durch Summieren über den Horizont
    ``E = Σ (S_t − S_{t−1}) + (1 − a) · Σ S_{t−1} = S_end − S_{−1} + (1 − a) · Σ S_{t−1}``.
    Mit ``S_{−1} = 0`` und ``S_end ≥ 0`` ist ``E`` also der Verlust ``(1 − a) · Σ S_{t−1}`` plus
    die Wärme, die am Ende noch im Haus ist. Weil Absenken (``S < 0``) Verluste spart, gilt die
    obere Grenze ``E ≤ (1 − a) · Σ max(S_{t−1}, 0) + S_end``. Die untere Grenze ``E ≥ 0`` gibt die
    Energiebedingung des LP: Die Verschiebung spart nie Energie, nur Geld.
    """
    inp = night_cheap_morning_expensive()
    r = shift(inp)
    a = math.exp(-DT_H / inp.tau_h)
    values = store(inp, r)
    extra_kwh = sum(s.thermal_w for s in r.slots) * DT_H / 1000
    losses = (1 - a) * sum(values[:-1])
    assert extra_kwh == pytest.approx(values[-1] - values[0] + losses, abs=TOL_KWH)
    assert extra_kwh >= -TOL_KWH
    assert extra_kwh <= (1 - a) * sum(max(v, 0.0) for v in values[:-1]) + values[-1] + TOL_KWH
    assert values[-1] == pytest.approx(0.0, abs=TOL_KWH)  # keine Wärme bleibt ungenutzt
    assert 0 < extra_kwh < 0.2


def test_cooling_mirrors_heating() -> None:
    heating = night_cheap_morning_expensive(start_k=0.25)
    cooling = replace(heating, mode="kuehlen")
    assert shift(cooling) == shift(heating)


def test_stored_heat_at_start_is_used() -> None:
    inp = shift_input([slot(i, import_ct=40.0) for i in range(16)], start_k=1.0)
    r = shift(inp)
    assert r.reason == "ok" and r.saving_ct > MIN_SAVING_CT
    assert r.slots[0].steps == 2 and sum(s.thermal_w for s in r.slots) < 0


def test_empty_slots_are_not_worth() -> None:
    assert shift(shift_input([])) == ShiftResult(slots=[], saving_ct=0.0, reason="not_worth")


@pytest.mark.parametrize(
    "overrides",
    [
        {"c_kwh_per_k": 0.0},
        {"c_kwh_per_k": math.nan},
        {"tau_h": -1.0},
        {"tau_h": math.inf},
        {"max_w": 0.0},
        {"up_k": -0.5},
        {"down_k": math.nan},
        {"margin_ct": math.inf},
        {"end_value_ct": math.nan},
        {"start_k": math.nan},
        {"mode": "lueften"},
    ],
)
def test_unusable_model_is_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        shift(night_cheap_morning_expensive(**overrides))


def test_base_above_max_allows_no_up() -> None:
    r = shift(night_cheap_morning_expensive(max_w=500.0))
    assert all(s.thermal_w <= 0 and s.grid_w == 0 for s in r.slots)


def test_infeasible_store_is_a_solver_result() -> None:
    # unter dem Soll gestartet, nie in Betrieb: S_end ≥ 0 ist nicht zu erreichen
    slots = [slot(i, on_share=0.0) for i in range(8)]
    r = shift(shift_input(slots, start_k=-0.5))
    assert r.reason == "solver" and r.saving_ct == 0 and len(r.slots) == 8 and all_zero(r)


def boom(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise RuntimeError("Solver kaputt")


def test_solver_error_is_no_shift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pulp.LpProblem, "solve", boom)
    r = shift(night_cheap_morning_expensive())
    assert r.reason == "solver" and r.saving_ct == 0 and all_zero(r)


def test_unsolved_lp_is_no_shift(monkeypatch: pytest.MonkeyPatch) -> None:
    def stopped(self: pulp.LpProblem, *_args: Any, **_kwargs: Any) -> int:
        self.status = pulp.LpStatusNotSolved  # so meldet PuLP ein Zeitlimit ohne Lösung
        self.sol_status = pulp.LpSolutionNoSolutionFound
        return self.status

    monkeypatch.setattr(pulp.LpProblem, "solve", stopped)
    r = shift(night_cheap_morning_expensive())
    assert r.reason == "solver" and all_zero(r)


def test_time_limit_reaches_the_solver(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[float] = []
    original = pulp.HiGHS

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["timeLimit"])
        return original(*args, **kwargs)

    monkeypatch.setattr(pulp, "HiGHS", spy)
    shift(night_cheap_morning_expensive(), time_limit_s=3.0)
    shift(night_cheap_morning_expensive())
    assert seen == [3.0, 5.0]


@pytest.mark.parametrize(
    ("x", "expected"),
    [(0.0, 0), (0.5, 0), (-0.5, 0), (0.51, 1), (1.5, 1), (-1.5, -1), (-1.6, -1), (2.7, 2)],
)
def test_steps_round_half_toward_zero_within_band(x: float, expected: int) -> None:
    assert thermal_shift._steps(x, -1, 2) == expected
