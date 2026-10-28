"""Tests der Szenarien für Akku ins Auto (Plan Akku ins Auto, Task 8): Aufbau, Plan und Prüfungen
an Hand gebauter Ergebnisse. Die ganzen Läufe prüft ``tests/integration/test_sim_scenarios.py``.
"""

from __future__ import annotations

import random
from datetime import timedelta
from typing import Any, Final

import pytest

from bindaems.core.api.app import HealthReport
from bindaems.shared.config import Config
from bindaems.shared.control import VICTRON, ChargeMode, CommandKind
from bindaems.shared.decisions import Decision, DecisionResult
from bindaems.shared.plan import BatteryIntent
from bindaems.tools.sim.metrics import CarSample, Sample, measure
from bindaems.tools.sim.model import PlantModel
from bindaems.tools.sim.runner import SimResult
from bindaems.tools.sim.scenarios import SCENARIOS, Checks, _Plan, _Target

SOFORT: Final = "akku-uebrig-sofort"
GEPLANT: Final = "akku-uebrig-geplant"
QUIET: Final = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
SELF_USE: Final = BatteryIntent()  # EIGENVERBRAUCH


def plan_of(name: str) -> _Plan:
    plans = [action for _, action in SCENARIOS[name].events if isinstance(action, _Plan)]
    assert len(plans) == 1
    return plans[0]


def check(name: str, index: int) -> Any:
    checks = SCENARIOS[name].checks
    assert isinstance(checks, Checks)
    return checks.items[index]


def car(watts: float, *, phases: int = 3) -> CarSample:
    amps = watts / phases / 230.0
    return CarSample(watts > 0, amps, watts, "now", 5)


def sample(
    t: int, *, grid_w: float = 0.0, soc_pct: float = 72.0, cars_w: float = 0.0, house_w: float = 900
) -> Sample:
    cars = {"tesla": car(cars_w), "egolf": car(0.0, phases=2)}
    return Sample(t, QUIET, QUIET, 0.0, soc_pct, cars, grid_w=grid_w, house_w=house_w)


def health(spare: str | None, spare_w: float | None) -> HealthReport:
    return HealthReport(
        mode="CONTROL",
        version="0",
        adapters=[],
        selfcheck=[],
        alarms=[],
        cycle_ms_p95=None,
        battery={"state": "ok", "spare": spare, "spare_w": spare_w},
    )


def setpoint(name: str, seconds: int, watts: float, code: str) -> Decision:
    return Decision(
        id=f"victron/{seconds}",
        ts=SCENARIOS[name].start + timedelta(seconds=seconds),
        actuator=VICTRON,
        kind=CommandKind.GRID_SETPOINT,
        requested=watts,
        applied=watts,
        dry_run=False,
        outcome="accepted",
        result=DecisionResult.OK,
        codes=(code,),
        text="",
        inputs={},
    )


def result_of(
    cfg: Config,
    name: str,
    samples: list[Sample],
    *,
    decisions: list[Decision] | None = None,
    states: list[tuple[int, HealthReport]] | None = None,
) -> SimResult:
    decisions = decisions or []
    metrics = measure(samples, decisions, cfg)
    return SimResult(
        SCENARIOS[name],
        metrics,
        decisions,
        states or [],
        samples,
        [],
        usable_kwh=cfg.battery.usable_kwh,
    )


# --- Aufbau und Plan ------------------------------------------------------------------------------


def test_the_spare_scenarios_follow_the_brief(cfg) -> None:
    sofort, geplant = SCENARIOS[SOFORT], SCENARIOS[GEPLANT]
    assert (sofort.title, sofort.duration_s) == ("Sofort am Abend mit übrigem Akku", 1800)
    assert (geplant.title, geplant.duration_s) == ("Geplant in der Nacht aus dem Akku", 2700)
    assert sofort.victron_live and geplant.victron_live
    assert sofort.intent.vehicles["tesla"].mode is ChargeMode.NOW
    assert sofort.intent.vehicles["tesla"].now_max_a == 10
    assert geplant.intent.vehicles["egolf"].mode is ChargeMode.PLANNED
    model = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    sofort.setup(model)
    assert model.soc_pct == 72.0
    assert model.cars["tesla"].wallbox is not None and model.cars["egolf"].wallbox is None
    model = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    geplant.setup(model)
    assert model.soc_pct == 60.0
    assert model.cars["egolf"].wallbox is not None and model.cars["tesla"].wallbox is None
    targets = [a for _, a in geplant.events if isinstance(a, _Target)]
    assert targets == [_Target(2, "egolf", 80, "07:00")]


def test_the_plans_carry_the_battery_fields() -> None:
    sofort = plan_of(SOFORT).plan()
    assert sofort.valid_until - sofort.created_at == timedelta(minutes=30)
    assert [(s.intent, s.now_battery, s.support_soc_pct) for s in sofort.slots] == [
        (SELF_USE, True, 70.0)
    ] * 2
    assert all(s.ev_battery_w == {} and s.soc_end_pct is None for s in sofort.slots)
    geplant = plan_of(GEPLANT).plan()
    assert geplant.valid_until - geplant.created_at == timedelta(minutes=45)
    assert [s.start - geplant.slots[0].start for s in geplant.slots] == [
        timedelta(minutes=15 * k) for k in range(3)
    ]
    assert [(s.vehicles, s.ev_battery_w, s.soc_end_pct) for s in geplant.slots] == [
        ({"egolf": 2760}, {"egolf": 2760}, 56.0),
        ({"egolf": 2760}, {"egolf": 2760}, 53.0),
        ({"egolf": 2760}, {"egolf": 2760}, 52.0),
    ]
    assert not any(s.now_battery for s in geplant.slots)


def test_a_plan_needs_one_soc_at_the_end_per_slot() -> None:
    plan = plan_of(GEPLANT)
    broken = _Plan(plan.start, 0, plan.slots, valid_s=2700, soc_end_pct=(56.0, 53.0))
    with pytest.raises(ValueError, match="2 SOC am Ende für 3 Slots"):
        broken.plan()


# --- „akku-uebrig-sofort“ ------------------------------------------------------------------------


def test_above_the_threshold_the_battery_carries_house_and_car(cfg) -> None:
    carried = check(SOFORT, 0)
    assert carried.text == (
        "Solange der SOC über 70 % liegt, ab 2 min kein Override des core: Netzbezug höchstens "
        "Haus und Autos über der Entladegrenze des Modells (12000 W) plus 300 W."
    )

    def run(grid_w: dict[int, float], *, cars_w: float = 6900.0, house_w: float = 900.0) -> Any:
        samples = [
            sample(
                t,
                grid_w=grid_w.get(t, 0.0),
                soc_pct=71.0 if t < 130 else 70.0,
                cars_w=cars_w,
                house_w=house_w,
            )
            for t in range(110, 140)
        ]
        return carried.find(result_of(cfg, SOFORT, samples))

    assert run({119: 7800.0, 135: 7800.0}) == []  # vor 2 min und ab 70 %
    assert run({125: 300.0}) == []
    assert run({125: 301.0, 126: 400.0}) == [
        "Netzbezug bei 125 s 301 W, verlangt höchstens 300 W (2 s darüber)."
    ]
    # Haus und Auto über der Entladegrenze: der Rest kommt ohne Override aus dem Netz
    assert run({125: 500.0}, cars_w=11040.0, house_w=1260.0) == []
    assert run({125: 601.0}, cars_w=11040.0, house_w=1260.0) == [
        "Netzbezug bei 125 s 601 W, verlangt höchstens 600 W (1 s darüber)."
    ]
    assert run({}, cars_w=0.0) == ["Ab 120 s lädt kein Auto, solange der SOC über 70 % liegt."]


def test_after_the_threshold_the_grid_carries_the_car(cfg) -> None:
    grid = check(SOFORT, 1)
    assert grid.text == (
        "Ab 2 min nach dem SOC 70 % (Akku nicht ins Auto) liegt der Netzbezug in jeder Sekunde "
        "mindestens bei der Leistung der Autos minus 300 W."
    )

    def run(grid_w: dict[int, float], *, reached: bool = True, cars_w: float = 6900.0) -> Any:
        samples = [
            sample(
                t,
                grid_w=grid_w.get(t, 6900.0),
                soc_pct=70.0 if reached and t >= 100 else 71.0,
                cars_w=cars_w,
            )
            for t in range(90, 240)
        ]
        return grid.find(result_of(cfg, SOFORT, samples))

    assert run({219: 0.0}) == []  # vor 100 s + 2 min
    assert run({220: 6600.0}) == []
    assert run({220: 6599.0, 221: 0.0}) == [
        "Netzbezug bei 220 s 6599 W, verlangt mindestens 6600 W (Autos 6900 W, 2 s darunter)."
    ]
    assert run({}, reached=False) == ["Es gibt keine Sekunde „dem SOC 70 % (Akku nicht ins Auto)“."]
    assert run({}, cars_w=0.0) == [
        "Ab 220 s (dem SOC 70 % (Akku nicht ins Auto) plus 120 s) lädt kein Auto."
    ]


def test_health_shows_the_spare_battery_before_battery_comp(cfg) -> None:
    shown = check(SOFORT, 2)
    assert shown.text == (
        "Vor der ersten Entscheidung mit „battery_comp“ zeigt die Gesundheit den übrigen Akku "
        "(„battery.spare“ „on“, Anteil über 0 W; Grund „battery_spare“)."
    )
    comp = setpoint(SOFORT, 300, 6900.0, "battery_comp")

    def run(states: list[tuple[int, HealthReport]], decisions: list[Decision]) -> Any:
        return shown.find(result_of(cfg, SOFORT, [sample(0)], decisions=decisions, states=states))

    assert run([(60, health("on", 0.0)), (120, health("on", 6900.0))], [comp]) == []
    assert run([(60, health("on", 0.0)), (300, health("on", 6900.0))], [comp]) == [
        "Vor der ersten Entscheidung mit „battery_comp“ bei 300 s zeigt keine Gesundheit "
        "„battery.spare“ „on“ mit einem Anteil über 0 W."
    ]
    assert run([(120, health("soc", 6900.0))], [comp]) != []
    assert run([(120, health("on", 6900.0))], []) == ["Keine Entscheidung nennt „battery_comp“."]


# --- „akku-uebrig-geplant“ -----------------------------------------------------------------------


def test_until_the_share_ends_no_grid_for_the_car(cfg) -> None:
    no_grid = check(GEPLANT, 0)
    assert no_grid.text == (
        "Ab 2 min, bis der SOC das geplante Ende seines Slots erreicht (im Slot 3), höchstens "
        "300 W Netzbezug für das Auto."
    )

    def run(grid_w: dict[int, float], *, end_s: int | None = 2000, cars_w: float = 2760.0) -> Any:
        def soc(t: int) -> float:
            return 51.9 if end_s is not None and t >= end_s else 57.0 if t < 900 else 54.0

        samples = [
            sample(t, grid_w=grid_w.get(t, 0.0), soc_pct=soc(t), cars_w=cars_w)
            for t in range(0, 2100, 10)
        ]
        return no_grid.find(result_of(cfg, GEPLANT, samples))

    assert run({110: 3000.0, 2050: 3000.0}) == []  # vor 2 min und nach dem Ende
    assert run({300: 310.0}) == [
        "Netzbezug bei 300 s 310 W, verlangt höchstens 300 W (1 s darüber, bis 310 W)."
    ]
    assert run({}, end_s=1000) == ["Der Akkuanteil endet bei 1000 s, verlangt im Slot 3."]
    assert run({}, end_s=None) == ["Der Akkuanteil endet nie, verlangt im Slot 3."]
    assert run({}, cars_w=0.0) == ["Kein Auto lädt, solange der Akkuanteil gilt."]


def test_the_soc_stays_above_the_plan(cfg) -> None:
    kept = check(GEPLANT, 2)
    assert kept.text == "Der SOC fällt nie unter 51 % (kleinstes SOC am Ende minus 1 Punkt)."
    assert check(GEPLANT, 1).text == (
        "Ab 2 min nach dem Ende des Akkuanteils liegt der Netzbezug in jeder Sekunde mindestens "
        "bei der Leistung der Autos minus 300 W."
    )

    def run(low: float) -> Any:
        samples = [sample(0, soc_pct=60.0), sample(1, soc_pct=low)]
        return kept.find(result_of(cfg, GEPLANT, samples))

    assert run(51.0) == []
    assert run(50.99) == ["Der SOC fällt bei 1 s auf 50,99 %, verlangt mindestens 51 %."]
