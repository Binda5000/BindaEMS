"""Tests der Szenarien für Akku und Spitzen (Plan 2c-2a, Design 10.2) und zur Planausführung
(Plan 3c, Entscheidung 13): Aufbau, Ereignisse und Prüfungen an Hand gebauter Ergebnisse. Die
ganzen Läufe prüft ``tests/integration/test_sim_scenarios.py``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest

from bindaems.core.api.app import HealthReport
from bindaems.core.control.intent import IntentReply
from bindaems.shared.config import Config
from bindaems.shared.control import VICTRON, ChargeMode, CommandKind, Intent, VehicleIntent
from bindaems.shared.decisions import Decision, DecisionResult
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind, IntentPlan, IntentPlanSlot
from bindaems.shared.timeutil import LOCAL_TZ
from bindaems.tools.sim.metrics import CarSample, Sample, measure
from bindaems.tools.sim.model import PlantModel
from bindaems.tools.sim.runner import SimResult
from bindaems.tools.sim.scenarios import (
    CLOUD_FACTORS,
    HOUSE_NOISE_W,
    HOUSE_W,
    SCENARIOS,
    Checks,
    _Boost,
    _Plan,
    sun_w,
)

OK: Final = DecisionResult.OK
SETPOINT, CURRENT = CommandKind.GRID_SETPOINT, CommandKind.CURRENT
EVCS: Final = "wallbox.evcs"
QUIET: Final = {"L1": 0.0, "L2": 0.0, "L3": 0.0}


def vienna(month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=LOCAL_TZ)


def set_up(cfg: Config, name: str) -> PlantModel:
    model = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    SCENARIOS[name].setup(model)
    return model


def weather(cfg: Config, name: str, second: int) -> tuple[float, tuple[float, float, float]]:
    """PV und Hauslast, die die Ereignisse der Sekunde ``second`` im Modell einstellen."""
    model = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    for t, action in SCENARIOS[name].events:
        if t == second and not isinstance(action, _Boost | _Plan):
            action(model, None)  # type: ignore[arg-type]  # das Wetter braucht die Laufzeit nicht
    return model.pv_w, model.house_w


def decision(
    name: str, seconds: float, actuator: str, kind: CommandKind, value: Any, *codes: str
) -> Decision:
    """Eine Entscheidung ``seconds`` nach dem Beginn des Szenarios ``name``, mit Wert."""
    return Decision(
        id=f"{actuator}/{kind}/{seconds}",
        ts=SCENARIOS[name].start + timedelta(seconds=seconds),
        actuator=actuator,
        kind=kind,
        requested=value,
        applied=value,
        dry_run=False,
        outcome="accepted",
        result=OK,
        codes=codes,
        text="",
        inputs={},
    )


def car(amps: float = 0.0, mode: str = "pv", *, min_a: int = 6, phases: int = 2) -> CarSample:
    return CarSample(amps > 0, amps, amps * phases * 230.0, mode, min_a)


def sample(
    t: int,
    *,
    grid_w: float = 0.0,
    battery_w: float = 0.0,
    house_w: float = 0.0,
    soc_pct: float = 60.0,
    tesla: CarSample | None = None,
    egolf: CarSample | None = None,
    override_w: float | None = None,
    override_age_s: float | None = None,
) -> Sample:
    cars = {
        "tesla": tesla or car(min_a=5, phases=3),
        "egolf": egolf or car(),
    }
    return Sample(
        t,
        QUIET,
        QUIET,
        battery_w,
        soc_pct,
        cars,
        grid_w=grid_w,
        house_w=house_w,
        override_w=override_w,
        override_age_s=override_age_s,
    )


def health(battery_state: str | None = "ok") -> HealthReport:
    return HealthReport(
        mode="CONTROL",
        version="0",
        adapters=[],
        selfcheck=[],
        alarms=[],
        cycle_ms_p95=None,
        battery={"state": battery_state},
    )


def result_of(
    cfg: Config,
    name: str,
    *,
    samples: list[Sample] | None = None,
    decisions: list[Decision] | None = None,
    states: list[tuple[int, HealthReport]] | None = None,
) -> SimResult:
    samples, decisions = samples or [], decisions or []
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


def check(name: str, index: int) -> Any:
    checks = SCENARIOS[name].checks
    assert isinstance(checks, Checks)
    return checks.items[index]


# --- Aufbau und Ereignisse -----------------------------------------------------------------------


def test_the_battery_and_peak_scenarios_follow_the_table_of_the_plan() -> None:
    summer, evening = vienna(6, 21, 10), vienna(10, 12, 18)
    table = {
        "akku-wolkig": ("PV unter Wolken, SOC 60 %", summer, 3600, 11),
        "akku-minpv": ("Min+PV ohne Sonne, SOC 80 %", evening, 1800, 12),
        "akku-sofort": ("Sofort am Abend, beide Autos", evening, 1800, 13),
        "boost-abend": ("Boost am Abend", evening, 2700, 14),
        "override-stunde": ("Eine Stunde mit aktivem Override", evening, 3600, 15),
        "dess-an": ("Dynamic ESS an", evening, 1800, 16),
        "spitze-15min": ("15-min-Ziel 8 kW", vienna(10, 12, 17, 59), 4500, 17),
        "schieflast-pv": ("PV mit schwerer Last auf L1", summer, 3600, 18),
    }
    for name, (title, start, duration_s, seed) in table.items():
        scenario = SCENARIOS[name]
        assert (scenario.title, scenario.start, scenario.duration_s, scenario.seed) == (
            title,
            start,
            duration_s,
            seed,
        )
        assert scenario.live and scenario.victron_live and scenario.intent.control
        assert dict(scenario.control) == ({"peak_target_kw": 8.0} if name == "spitze-15min" else {})


def test_the_cars_modes_and_battery_of_the_setups(cfg) -> None:
    now, pv, min_pv = ChargeMode.NOW, ChargeMode.PV, ChargeMode.MIN_PV
    table = {
        # Name: SOC, Wallbox von Tesla und e-Golf, Strom im Tesla, DESS, Modi laut Absicht
        "akku-wolkig": (60.0, None, "evcs", 16, False, {"egolf": pv}),
        "akku-minpv": (80.0, "twc", "evcs", 5, False, {"tesla": min_pv, "egolf": min_pv}),
        "akku-sofort": (60.0, "twc", "evcs", 16, False, {"tesla": now, "egolf": now}),
        "override-stunde": (50.0, None, "evcs", 16, False, {"egolf": now}),
        "dess-an": (60.0, None, "evcs", 16, True, {"egolf": now}),
        "spitze-15min": (60.0, "twc", "evcs", 16, False, {"tesla": min_pv, "egolf": now}),
        "schieflast-pv": (60.0, None, "evcs", 16, False, {"egolf": pv}),
    }
    for name, (soc, tesla, egolf, amps, dess, modes) in table.items():
        model = set_up(cfg, name)
        found = (model.soc_pct, model.cars["tesla"].wallbox, model.cars["egolf"].wallbox)
        assert found == (soc, tesla, egolf), name
        assert (model.teslas["tesla"].charge_amps, model.dess) == (amps, dess), name
        vehicles = SCENARIOS[name].intent.vehicles
        assert {key: entry.mode for key, entry in vehicles.items()} == modes, name
        assert all(entry.boost_until is None for entry in vehicles.values()), name


def test_boost_until_the_end_of_the_evening(cfg) -> None:
    scenario = SCENARIOS["boost-abend"]
    model = set_up(cfg, "boost-abend")
    assert (model.soc_pct, model.cars["tesla"].wallbox, model.cars["egolf"].wallbox) == (
        35.0,
        None,
        "evcs",
    )
    end = scenario.start + timedelta(seconds=scenario.duration_s)
    assert scenario.intent.vehicles == {"egolf": VehicleIntent(boost_until=end)}
    assert all(not isinstance(action, _Boost) for _, action in scenario.events)


def test_the_peak_scenario_boosts_the_egolf_from_18_45(cfg) -> None:
    scenario = SCENARIOS["spitze-15min"]
    ((at, boost),) = [(t, a) for t, a in scenario.events if isinstance(a, _Boost)]
    end = scenario.start + timedelta(seconds=scenario.duration_s)
    assert (at, boost) == (2760, _Boost(2, "egolf", end))
    assert scenario.start + timedelta(seconds=at) == vienna(10, 12, 18, 45)
    assert end == vienna(10, 12, 19, 14)


def test_weather_of_the_battery_and_peak_scenarios(cfg) -> None:
    cloudy = SCENARIOS["akku-wolkig"]
    factors = {
        round(weather(cfg, "akku-wolkig", t)[0] / sun_w(cloudy.start + timedelta(seconds=t)), 6)
        for t in range(0, cloudy.duration_s, 5)
    }
    assert factors == set(CLOUD_FACTORS)  # wie „wolkig“: Sonne und Wolken im Wechsel
    for name in ("akku-minpv", "akku-sofort", "boost-abend", "override-stunde", "dess-an"):
        pv, house = weather(cfg, name, 600)
        assert pv == 0.0 and all(abs(w - HOUSE_W) <= HOUSE_NOISE_W for w in house), name
    assert weather(cfg, "spitze-15min", 4499)[0] == 0.0
    # „schieflast-pv“: konstante PV, die schwere Last auf L1 erst ab 10 min
    for t, heavy in ((0, False), (599, False), (600, True), (3599, True)):
        pv, (l1, l2, l3) = weather(cfg, "schieflast-pv", t)
        assert pv == 12000.0
        assert l1 == pytest.approx(HOUSE_W + (5500.0 if heavy else 0.0), abs=HOUSE_NOISE_W)
        assert abs(l2 - HOUSE_W) <= HOUSE_NOISE_W and abs(l3 - HOUSE_W) <= HOUSE_NOISE_W


# --- Prüfungen -----------------------------------------------------------------------------------


def test_the_egolf_takes_at_most_5_pct_from_the_battery(cfg) -> None:
    share = check("akku-wolkig", 0)
    assert share.text == (
        "Höchstens 5 % der Energie von „egolf“ kommen aus dem Akku (ohne Übergänge)."
    )

    def run(battery_w: float, amps: float = 6.0) -> list[str]:
        samples = [
            sample(t, battery_w=battery_w, house_w=900.0, egolf=car(amps)) for t in range(30)
        ]
        return share.find(result_of(cfg, "akku-wolkig", samples=samples))

    assert run(-900.0) == []  # der Akku deckt nur das Haus
    # 30 s: Die ersten 10 s sind ein Übergang, dann deckt der Akku das ganze Auto: 20 von 30 s
    assert run(-900.0 - 2760.0) == ["„egolf“ lädt zu 66,67 % aus dem Akku, verlangt höchstens 5 %."]
    assert run(-5000.0, amps=0.0) == ["„egolf“ lädt nie."]

    def covered(seconds: int) -> list[str]:
        """200 s mit 6 A; nach dem Übergang deckt der Akku das Auto ``seconds`` s lang ganz."""
        samples = [
            sample(
                t,
                battery_w=-900.0 - (2760.0 if 10 <= t < 10 + seconds else 0.0),
                house_w=900.0,
                egolf=car(6.0),
            )
            for t in range(200)
        ]
        return share.find(result_of(cfg, "akku-wolkig", samples=samples))

    assert covered(10) == []  # genau 5 %
    assert covered(11) == ["„egolf“ lädt zu 5,5 % aus dem Akku, verlangt höchstens 5 %."]


def test_a_decision_at_victron_must_name_battery_comp(cfg) -> None:
    named = check("akku-wolkig", 1)
    assert named.text == "Eine Entscheidung an „victron“ nennt „battery_comp“."
    at_victron = decision("akku-wolkig", 400, VICTRON, SETPOINT, 1740.0, "battery_comp")
    elsewhere = decision("akku-wolkig", 400, EVCS, CURRENT, 6.0, "mode_pv", "battery_comp")
    assert named.find(result_of(cfg, "akku-wolkig", decisions=[at_victron])) == []
    assert named.find(result_of(cfg, "akku-wolkig", decisions=[elsewhere])) == [
        "Keine Entscheidung an „victron“ mit „battery_comp“."
    ]


def test_the_battery_supports_min_pv_up_to_3200_w_then_only_the_grid(cfg) -> None:
    supported = check("akku-minpv", 0)
    assert supported.text == (
        "Akku in die Autos (Entladung − Haus): ab 10 s nach dem ersten Laden bis 890 s danach "
        "höchstens 3200 W, ab 930 s danach höchstens 200 W."
    )

    def run(into_cars: dict[int, float], *, seconds: int = 1000) -> list[str]:
        """Das erste Laden bei 5 s; ``into_cars`` nennt den Akku in die Autos je Sekunde."""
        samples = [
            sample(
                t,
                battery_w=-(900.0 + into_cars.get(t, 0.0)),
                house_w=900.0,
                egolf=car(6.0 if t >= 5 else 0.0, "min_pv"),
            )
            for t in range(seconds)
        ]
        return supported.find(result_of(cfg, "akku-minpv", samples=samples))

    support = {t: 3200.0 for t in range(5, 935)}  # bis zum Ende der Unterstützung und danach
    assert run(support) == []
    assert run({14: 5800.0, 900: 3000.0}) == []  # vor dem Fenster und im Übergang danach
    assert run({15: 5800.0, 16: 3300.0}) == [
        "Akku in die Autos bei 15 s 5800 W, verlangt höchstens 3200 W (2 s darüber, bis 5800 W)."
    ]
    assert run({935: 300.0}) == [
        "Akku in die Autos bei 935 s 300 W, verlangt höchstens 200 W (1 s darüber, bis 300 W)."
    ]
    assert run({20: 3200.4}) == [  # ungerundet, wenn die Rundung den Verstoß verdeckte
        "Akku in die Autos bei 20 s 3200,4 W, verlangt höchstens 3200 W (1 s darüber, bis "
        "3200,4 W)."
    ]
    assert run({}, seconds=900) == ["Der Lauf endet vor 935 s."]
    idle = [sample(t, battery_w=-900.0, house_w=900.0) for t in range(100)]
    assert supported.find(result_of(cfg, "akku-minpv", samples=idle)) == ["Kein Auto lädt."]


def test_in_now_the_grid_stays_at_8_kw(cfg) -> None:
    capped = check("akku-sofort", 0)
    assert capped.text == (
        "Ab 2 min liegt der Netzbezug in jeder Sekunde, in der Autos und Haus mehr als 8300 W "
        "ziehen, zwischen 7800 und 8200 W."
    )

    def run(grid_w: dict[int, float], *, tesla_a: float = 16.0) -> list[str]:
        tesla = car(tesla_a, "now", min_a=5, phases=3)
        samples = [
            sample(t, grid_w=grid_w.get(t, 8000.0), house_w=900.0, tesla=tesla, egolf=car(6.0))
            for t in range(110, 130)
        ]
        return capped.find(result_of(cfg, "akku-sofort", samples=samples))

    assert run({}) == []
    assert run({119: 7000.0}) == []  # vor 2 min
    assert run({125: 7700.0, 126: 8300.0}) == [
        "Netzbezug bei 125 s 7700 W, verlangt 7800 bis 8200 W (2 s außerhalb)."
    ]
    assert run({125: 8200.3}) == [
        "Netzbezug bei 125 s 8200,3 W, verlangt 7800 bis 8200 W (1 s außerhalb)."
    ]
    # nur der e-Golf mit 6 A und das Haus: 2760 + 900 W, nicht mehr als 8300 W
    assert run({125: 3660.0}, tesla_a=0.0) == [
        "Autos und Haus ziehen ab 120 s nie mehr als 8300 W."
    ]


def test_boost_sends_no_override(cfg) -> None:
    no_number = check("boost-abend", 0)
    assert no_number.text == "Keine Entscheidung an „victron“ mit einer Zahl."
    lifted = decision("boost-abend", 30, VICTRON, SETPOINT, None, "battery_support")
    current = decision("boost-abend", 30, EVCS, CURRENT, 16.0, "boost")
    override = decision("boost-abend", 40, VICTRON, SETPOINT, 1790.0, "battery_comp")
    assert no_number.find(result_of(cfg, "boost-abend", decisions=[lifted, current])) == []
    assert no_number.find(result_of(cfg, "boost-abend", decisions=[lifted, override])) == [
        "Entscheidung an „victron“ bei 40 s: 1790 W."
    ]


def test_in_boost_the_battery_carries_the_house_above_25_pct(cfg) -> None:
    house = check("boost-abend", 1)
    assert house.text == "Solange der SOC über 25 % liegt, ab 2 min Netzbezug höchstens 300 W."

    def run(grid_w: dict[int, float], soc_pct: float = 30.0) -> list[str]:
        samples = [
            sample(t, grid_w=grid_w.get(t, 0.0), soc_pct=soc_pct if t < 125 else 25.0)
            for t in range(110, 130)
        ]
        return house.find(result_of(cfg, "boost-abend", samples=samples))

    assert run({119: 8000.0, 125: 8000.0}) == []  # vor 2 min und bei 25 %
    assert run({121: 300.0}) == []  # genau an der Grenze
    assert run({121: 300.1}) == [
        "Netzbezug bei 121 s 300,1 W, verlangt höchstens 300 W (1 s darüber, bis 300,1 W)."
    ]
    assert run({121: 400.0}) == [
        "Netzbezug bei 121 s 400 W, verlangt höchstens 300 W (1 s darüber, bis 400 W)."
    ]
    assert run({}, soc_pct=25.0) == ["Der SOC liegt ab 120 s nie über 25 %."]
    assert check("boost-abend", 2).text == "Der SOC sinkt nie unter 20 % (Invariante des Modells)."


def test_the_override_lasts_the_hour(cfg) -> None:
    kept = check("override-stunde", 0)
    assert kept.text == (
        "Ab 2 min gilt in jeder Sekunde ein Override, zuletzt vor höchstens 245 s geschrieben."
    )

    def run(changes: dict[int, tuple[float | None, float | None]]) -> list[str]:
        samples = []
        for t in range(110, 400):
            watts, age = changes.get(t, (7360.0, float(t % 240)))
            samples.append(sample(t, override_w=watts, override_age_s=age))
        return kept.find(result_of(cfg, "override-stunde", samples=samples))

    assert run({}) == []
    assert run({119: (None, None)}) == []  # vor 2 min
    assert run({130: (7360.0, 245.0)}) == []  # genau an der Grenze
    assert run({130: (None, None)}) == ["Bei 130 s gilt kein Override."]
    assert run({130: (7360.0, 246.0)}) == [
        "Bei 130 s ist der Override vor 246 s geschrieben, verlangt höchstens 245 s."
    ]
    short = [sample(t) for t in range(100)]
    assert kept.find(result_of(cfg, "override-stunde", samples=short)) == [
        "Der Lauf endet vor 120 s."
    ]
    assert check("override-stunde", 1).text == "Kein Alarm „competitor.foreign.*“."


def test_at_most_5_decisions_at_victron(cfg) -> None:
    few = check("override-stunde", 2)
    assert few.text == "Höchstens 5 Entscheidungen an „victron“."
    five = [decision("override-stunde", t, VICTRON, SETPOINT, 7360.0) for t in range(5)]
    current = decision("override-stunde", 9, EVCS, CURRENT, 16.0, "mode_now")
    assert few.find(result_of(cfg, "override-stunde", decisions=[*five, current])) == []
    six = [*five, decision("override-stunde", 5, VICTRON, SETPOINT, None)]
    assert few.find(result_of(cfg, "override-stunde", decisions=six)) == [
        "6 Entscheidungen an „victron“, verlangt höchstens 5."
    ]


def test_with_dess_victron_gets_no_decision_and_is_locked(cfg) -> None:
    no_decision, locked = check("dess-an", 0), check("dess-an", 1)
    assert no_decision.text == "Keine Entscheidung an „victron“."
    assert locked.text == "Ab 1 min zeigt jede Gesundheit „battery.state“ „locked“."
    assert check("dess-an", 2).text == "„egolf“ lädt am Ende."
    current = decision("dess-an", 70, EVCS, CURRENT, 16.0, "mode_now")
    assert no_decision.find(result_of(cfg, "dess-an", decisions=[current])) == []
    lifted = decision("dess-an", 30, VICTRON, SETPOINT, None, "battery_comp")
    override = decision("dess-an", 50, VICTRON, SETPOINT, 7360.0, "battery_comp")
    assert no_decision.find(result_of(cfg, "dess-an", decisions=[lifted, override])) == [
        "Entscheidungen an „victron“: 2, die erste bei 30 s (kein Override)."
    ]

    def states(state: str | None, at: int = 70) -> list[str]:
        """Bis 50 s „idle“, ab 60 s „locked“, nur bei ``at`` der Stand ``state``."""
        reports = [(t, health("idle" if t < 60 else "locked")) for t in range(0, 100, 10)]
        reports[at // 10] = (at, health(state))
        return locked.find(result_of(cfg, "dess-an", states=reports))

    assert states("locked") == [] and states("ok", at=50) == []
    assert states("ok") == ["„battery.state“ bei 70 s „ok“, verlangt „locked“."]
    assert states("idle", at=60) == ["„battery.state“ bei 60 s „idle“, verlangt „locked“."]
    assert states(None) == ["„battery.state“ bei 70 s ohne Wert, verlangt „locked“."]
    early = [(t, health("idle")) for t in range(0, 60, 10)]
    assert locked.find(result_of(cfg, "dess-an", states=early)) == ["Der Lauf endet vor 60 s."]


def test_the_grid_energy_of_the_first_three_quarters(cfg) -> None:
    quarters = check("spitze-15min", 0)
    assert quarters.text == (
        "Netzbezug der Viertelstunden ab 18:00, 18:15 und 18:30 je höchstens 2,02 kWh."
    )

    def run(grid_w: dict[int, float], *, seconds: int = 2760) -> list[str]:
        samples = [sample(t, grid_w=grid_w.get(t, 7000.0)) for t in range(seconds)]
        return quarters.find(result_of(cfg, "spitze-15min", samples=samples))

    assert run({}) == []  # 1,75 kWh je Viertelstunde
    assert run(dict.fromkeys(range(60), 20000.0)) == []  # vor 18:00 zählt nicht
    # ab 18:15 mit 8200 W: 2,05 kWh; ein negativer Netzbezug zählt 0
    high = dict.fromkeys(range(960, 1860), 8200.0) | {1000: -50000.0}
    assert run(high) == [
        "Netzbezug der Viertelstunde ab 18:15: 2,048 kWh, verlangt höchstens 2,02 kWh."
    ]
    assert run({}, seconds=2700) == ["Die Viertelstunde ab 18:30 liegt nicht ganz im Lauf."]


def test_peak_guard_before_but_not_at_the_evcs_after_18_45(cfg) -> None:
    before, after = check("spitze-15min", 1), check("spitze-15min", 2)
    assert before.text == "Eine Entscheidung vor 18:45 nennt „peak_guard“."
    assert after.text == "Ab 18:45 nennt keine Entscheidung an „wallbox.evcs“ „peak_guard“."
    early = decision("spitze-15min", 2759, EVCS, CURRENT, 9.0, "mode_now", "peak_guard")
    late = decision("spitze-15min", 2760, EVCS, CURRENT, 9.0, "mode_now", "peak_guard")
    budget = decision("spitze-15min", 2800, VICTRON, SETPOINT, 7700.0, "battery_comp", "peak_guard")
    assert before.find(result_of(cfg, "spitze-15min", decisions=[early])) == []
    assert after.find(result_of(cfg, "spitze-15min", decisions=[early, budget])) == []
    assert before.find(result_of(cfg, "spitze-15min", decisions=[late])) == [
        "Keine Entscheidung mit „peak_guard“ vor 2760 s."
    ]
    assert after.find(result_of(cfg, "spitze-15min", decisions=[late, replace(late, id="2")])) == [
        "Entscheidungen an „wallbox.evcs“ mit „peak_guard“ ab 2760 s: 2, die erste bei 2760 s."
    ]


def test_now_is_throttled_only_with_min_pv_at_its_minimum(cfg) -> None:
    order = check("spitze-15min", 3)
    assert order.text == (
        "Bei der ersten Drosselung von „egolf“ in Sofort lädt kein Auto in PV-Überschuss oder "
        "Min+PV über Mindeststrom + 0,5 A."
    )
    throttled = decision("spitze-15min", 70.4, EVCS, CURRENT, 9.0, "mode_now", "peak_guard")

    def run(tesla_a: float, *, egolf_mode: str = "now", codes: tuple[str, ...] = ()) -> list[str]:
        tesla = car(tesla_a, "min_pv", min_a=5, phases=3)
        samples = [sample(t, tesla=tesla, egolf=car(16.0, egolf_mode)) for t in range(60, 80)]
        found = replace(throttled, codes=codes) if codes else throttled
        return order.find(result_of(cfg, "spitze-15min", samples=samples, decisions=[found]))

    assert run(5.0) == [] and run(5.5) == []
    assert run(5.6) == [
        "Bei der ersten Drosselung von „egolf“ (70 s) lädt „tesla“ (min_pv) mit 5,6 A, "
        "Mindeststrom 5 A."
    ]
    assert run(16.0) == [
        "Bei der ersten Drosselung von „egolf“ (70 s) lädt „tesla“ (min_pv) mit 16 A, "
        "Mindeststrom 5 A."
    ]
    missing = ["Keine Drosselung von „egolf“ in Sofort."]
    assert run(16.0, egolf_mode="boost") == missing  # in Boost drosselt der Wächter nie
    assert run(16.0, codes=("mode_now", "phase_limit")) == missing


def test_a_decision_at_the_evcs_must_name_import_limit(cfg) -> None:
    named = check("schieflast-pv", 0)
    assert named.text == "Eine Entscheidung an „wallbox.evcs“ nennt „import_limit“."
    limited = decision("schieflast-pv", 606, EVCS, CURRENT, 10.0, "mode_pv", "import_limit")
    assert named.find(result_of(cfg, "schieflast-pv", decisions=[limited])) == []
    other = decision("schieflast-pv", 606, VICTRON, SETPOINT, 1790.0, "import_limit")
    assert named.find(result_of(cfg, "schieflast-pv", decisions=[other])) == [
        "Keine Entscheidung an „wallbox.evcs“ mit „import_limit“."
    ]


def test_the_checks_report_all_findings_in_order(cfg) -> None:
    assert SCENARIOS["dess-an"].checks(result_of(cfg, "dess-an")) == [
        "Der Lauf endet vor 60 s.",
        "„egolf“ lädt am Ende nicht.",
    ]


# --- Szenarien zur Planausführung (Plan 3c, Entscheidung 13) -------------------------------------

HOLD: Final = BatteryIntent(kind=BatteryIntentKind.HALTEN)


def grid_charge(watts: float) -> BatteryIntent:
    return BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=watts)


def plans(name: str) -> list[tuple[int, _Plan]]:
    return [(t, a) for t, a in SCENARIOS[name].events if isinstance(a, _Plan)]


@dataclass
class FakeRuntime:
    """Was ``_Plan`` von der Laufzeit braucht: die gültige Absicht und ``submit_intent``."""

    current: Intent | None
    reply: IntentReply = field(default_factory=lambda: IntentReply("accepted", 2))
    sent: list[Intent] = field(default_factory=list)

    def intent(self) -> Intent | None:
        return self.current

    def submit_intent(self, intent: Intent) -> IntentReply:
        self.sent.append(intent)
        return self.reply


def test_the_plan_scenarios_follow_the_table_of_the_plan(cfg) -> None:
    evening, night = vienna(10, 12, 18), vienna(10, 13, 2)
    table = {
        "plan-halten": ("Halten am Abend", evening, 3600, 24),
        "plan-netzladen": ("Netzladen in der Nacht", night, 1800, 25),
        "plan-abgelaufen": ("Plan läuft ab", evening, 1800, 26),
        "plan-budget": ("Netzladen unter dem Budget", vienna(10, 12, 17, 59), 2820, 27),
        "plan-boost": ("Boost während Halten", evening, 3600, 28),
    }
    assert list(SCENARIOS)[23:28] == list(table)
    for name, (title, start, duration_s, seed) in table.items():
        scenario = SCENARIOS[name]
        found = (scenario.title, scenario.start, scenario.duration_s, scenario.seed)
        assert found == (title, start, duration_s, seed), name
        assert scenario.live and scenario.victron_live and scenario.intent.control, name
        assert scenario.intent.plan is None and dict(scenario.control) == {}, name
        model = set_up(cfg, name)
        found_cars = (model.cars["tesla"].wallbox, model.cars["egolf"].wallbox)
        assert (model.soc_pct, *found_cars) == (
            30.0 if name == "plan-netzladen" else 60.0,
            None,
            "evcs",
        )
        assert {key: entry.mode for key, entry in scenario.intent.vehicles.items()} == {
            "egolf": ChargeMode.PV
        }, name
    # alle Szenarien rechnen mit 88 % Roundtrip
    assert {scenario.roundtrip_pct for scenario in SCENARIOS.values()} == {88.0}
    assert cfg.battery.usable_kwh == 30.0  # damit rechnet „plan-netzladen“


def test_hold_plans_come_every_quarter_for_four_slots() -> None:
    for name in ("plan-halten", "plan-boost"):
        start = SCENARIOS[name].start
        expected = [
            (t, _Plan(start, t, tuple((t // 900 + k, HOLD, None) for k in range(4)), valid_s=2700))
            for t in (0, 900, 1800, 2700)
        ]
        assert plans(name) == expected, name


def test_the_other_plans_of_the_plan_scenarios() -> None:
    charge, start = grid_charge(3000.0), SCENARIOS["plan-netzladen"].start
    assert plans("plan-netzladen") == [
        (0, _Plan(start, 0, ((0, charge, None), (1, charge, None)), valid_s=1800))
    ]
    start = SCENARIOS["plan-abgelaufen"].start
    assert plans("plan-abgelaufen") == [
        (0, _Plan(start, 0, ((0, HOLD, None), (1, HOLD, None)), valid_s=1200))
    ]
    # „plan-budget“ beginnt um 17:59: ein Plan sofort, dann je einer zur Viertelstunde
    budget, start = grid_charge(6000.0), SCENARIOS["plan-budget"].start
    assert plans("plan-budget") == [
        (t, _Plan(start, t, tuple((q + k, budget, 4.0) for k in range(4)), valid_s=2700))
        for t, q in ((0, 0), (60, 1), (960, 2), (1860, 3), (2760, 4))
    ]


def test_the_egolf_boosts_from_minute_1_between_the_plans() -> None:
    scenario = SCENARIOS["plan-boost"]
    end = scenario.start + timedelta(seconds=scenario.duration_s)
    boosts = [(t, a) for t, a in scenario.events if isinstance(a, _Boost)]
    assert boosts == [(60, _Boost(3, "egolf", end))]  # Version 2 ist der erste Plan


def test_a_plan_is_the_next_version_of_the_valid_intent(cfg) -> None:
    start = vienna(10, 12, 18).astimezone(UTC)
    event = _Plan(start, 900, ((1, HOLD, None), (2, grid_charge(2000.0), 4.0)), valid_s=1200)
    boost = VehicleIntent(boost_until=start + timedelta(hours=1))
    current = Intent(version=3, control=True, vehicles={"egolf": boost})
    rt = FakeRuntime(current)
    event(set_up(cfg, "plan-halten"), rt)  # type: ignore[arg-type]
    plan = IntentPlan(
        id=900,
        created_at=start + timedelta(minutes=15),
        valid_until=start + timedelta(minutes=35),
        grid_charge_allowed=True,
        slots=[
            IntentPlanSlot(start=start + timedelta(minutes=15), intent=HOLD),
            IntentPlanSlot(
                start=start + timedelta(minutes=30), intent=grid_charge(2000.0), budget_kw=4.0
            ),
        ],
    )
    # Boost und alles andere bleiben (Review Focus 1)
    assert rt.sent == [current.model_copy(update={"version": 4, "plan": plan})]
    off = _Plan(start, 0, ((0, HOLD, None),), valid_s=600, grid_charge_allowed=False)
    off(None, rt)  # type: ignore[arg-type]
    assert rt.sent[-1].plan is not None and rt.sent[-1].plan.grid_charge_allowed is False


def test_plan_slots_count_from_the_quarter_of_the_start() -> None:
    # Beginnt ein Szenario um 17:59, ist der Slot 0 die Viertelstunde ab 17:45.
    event = _Plan(vienna(10, 12, 17, 59).astimezone(UTC), 60, ((1, HOLD, None),), valid_s=600)
    rt = FakeRuntime(Intent(version=1, control=True))
    event(None, rt)  # type: ignore[arg-type]
    plan = rt.sent[0].plan
    assert plan is not None
    assert [slot.start for slot in plan.slots] == [vienna(10, 12, 18)]
    assert (plan.created_at, plan.valid_until) == (vienna(10, 12, 18), vienna(10, 12, 18, 10))


def test_a_rejected_plan_stops_the_run() -> None:
    event = _Plan(vienna(10, 12, 18), 0, ((0, HOLD, None),), valid_s=600)
    rt = FakeRuntime(Intent(version=1, control=True))
    rt.reply = IntentReply("rejected", 1, ("Plan: Gültigkeit passt nicht zu den Slots.",))
    with pytest.raises(ValueError, match="Plan abgelehnt: Plan: Gültigkeit passt nicht"):
        event(None, rt)  # type: ignore[arg-type]
    rt.reply = IntentReply("clamped", 2, ("Plan bis 18:45 begrenzt.",))
    event(None, rt)  # type: ignore[arg-type]  # geklemmt ist angenommen
    rt.current = None
    with pytest.raises(ValueError, match="Plan ohne gültige Absicht"):
        event(None, rt)  # type: ignore[arg-type]


def test_weather_of_the_plan_scenarios(cfg) -> None:
    for name in ("plan-halten", "plan-netzladen", "plan-abgelaufen", "plan-budget", "plan-boost"):
        pv, house = weather(cfg, name, 600)
        assert pv == 0.0 and all(abs(w - HOUSE_W) <= HOUSE_NOISE_W for w in house), name
    # „plan-halten“: 2 kW mehr auf L1 von 20 bis 25 min
    for t, jump in ((1199, False), (1200, True), (1499, True), (1500, False)):
        pv, (l1, *others) = weather(cfg, "plan-halten", t)
        assert l1 == pytest.approx(HOUSE_W + (2000.0 if jump else 0.0), abs=HOUSE_NOISE_W), t
        assert all(abs(w - HOUSE_W) <= HOUSE_NOISE_W for w in others), t


def test_while_holding_the_battery_gives_at_most_300_w_per_minute(cfg) -> None:
    held = check("plan-halten", 0)
    assert held.text == "Ab 2 min entlädt der Akku im Mittel jeder Minute höchstens 300 W."

    def run(battery_w: dict[int, float], *, seconds: int = 300) -> list[str]:
        samples = [sample(t, battery_w=battery_w.get(t, -100.0)) for t in range(seconds)]
        return held.find(result_of(cfg, "plan-halten", samples=samples))

    assert run({}) == []
    assert run(dict.fromkeys(range(60, 120), -2000.0)) == []  # vor 2 min
    # 30 s mit −500 W und 30 s mit −100 W: im Mittel −300 W, genau an der Grenze
    assert run(dict.fromkeys(range(120, 150), -500.0)) == []
    assert run(dict.fromkeys(range(180, 190), -2000.0)) == [
        "Mittlere Akkuleistung der Minute ab 180 s −416,7 W, verlangt mindestens −300 W "
        "(1 von 3 Minuten darunter)."
    ]
    # eine angebrochene Minute am Ende zählt nicht
    assert run(dict.fromkeys(range(300, 310), -9000.0), seconds=310) == []
    assert run({}, seconds=170) == ["Der Lauf hat ab 120 s keine ganze Minute."]
    until = check("plan-abgelaufen", 0)
    assert until.text == "Von 2 bis 20 min entlädt der Akku im Mittel jeder Minute höchstens 300 W."
    samples = [sample(t, battery_w=-900.0 if t >= 1200 else 0.0) for t in range(1500)]
    assert until.find(result_of(cfg, "plan-abgelaufen", samples=samples)) == []


def test_while_holding_the_soc_drops_at_most_1_5_points(cfg) -> None:
    dropped = check("plan-halten", 1)
    assert dropped.text == "Der SOC fällt höchstens um 1,5 Prozentpunkte."

    def run(socs: list[float]) -> list[str]:
        samples = [sample(t, soc_pct=soc) for t, soc in enumerate(socs)]
        return dropped.find(result_of(cfg, "plan-halten", samples=samples))

    assert run([60.0, 59.5, 58.5, 59.0]) == []
    assert run([60.0, 58.4, 59.0]) == [
        "Der SOC fällt um 1,6 Prozentpunkte, verlangt höchstens 1,5."
    ]
    assert run([]) == ["Der Lauf hat keine Probe."]


def test_while_holding_at_most_40_victron_decisions_per_hour(cfg) -> None:
    few = check("plan-halten", 2)
    assert few.text == "Höchstens 40 Entscheidungen an „victron“ je Stunde."

    def run(count: int, *, seconds: int = 3600) -> list[str]:
        decisions = [
            decision("plan-halten", t, VICTRON, SETPOINT, 900.0, "plan_hold") for t in range(count)
        ]
        current = decision("plan-halten", 9, EVCS, CURRENT, 16.0, "mode_now")
        samples = [sample(t) for t in range(seconds)]
        result = result_of(cfg, "plan-halten", samples=samples, decisions=[*decisions, current])
        return few.find(result)

    assert run(40) == [] and run(20, seconds=1800) == []
    assert run(41) == [
        "41 Entscheidungen an „victron“ in 60 min: 41 je Stunde, verlangt höchstens 40."
    ]
    assert run(21, seconds=1800) == [
        "21 Entscheidungen an „victron“ in 30 min: 42 je Stunde, verlangt höchstens 40."
    ]
    assert run(0, seconds=0) == ["Der Lauf hat keine Probe."]


def test_grid_charging_follows_the_house_plus_3000_w(cfg) -> None:
    charged = check("plan-netzladen", 0)
    assert charged.text == (
        "Ab 2 min liegt der Netzbezug in jeder Sekunde bei Haus + 3000 W, auf ± 300 W."
    )

    def run(grid_w: dict[int, float], *, house_w: float = 900.0) -> list[str]:
        samples = [
            sample(t, grid_w=grid_w.get(t, house_w + 3000.0), house_w=house_w)
            for t in range(110, 130)
        ]
        return charged.find(result_of(cfg, "plan-netzladen", samples=samples))

    assert run({}) == [] and run({119: 0.0}) == []  # vor 2 min
    assert run({120: 3600.0, 121: 4200.0}) == []  # genau an den Grenzen
    assert run({125: 3599.6, 126: 4300.0}) == [
        "Netzbezug bei 125 s 3599,6 W bei 900 W Haus, verlangt 3600 bis 4200 W (2 s außerhalb)."
    ]
    assert charged.find(result_of(cfg, "plan-netzladen", samples=[sample(10)])) == [
        "Der Lauf endet vor 120 s."
    ]


def test_grid_charging_raises_the_soc_by_90_pct_of_the_energy(cfg) -> None:
    raised = check("plan-netzladen", 1)
    # 0,9 · 3 kW · 0,5 h · √0,88 = 1,266 kWh, von 30 kWh 4,22 Prozentpunkte
    assert raised.text == (
        "Der SOC steigt um mindestens 90 % von 3 kW · 0,5 h · √0,88, geteilt durch die nutzbare "
        "Kapazität des Akkus."
    )

    def run(last: float) -> list[str]:
        samples = [sample(0, soc_pct=30.0), sample(1, soc_pct=31.0), sample(2, soc_pct=last)]
        return raised.find(result_of(cfg, "plan-netzladen", samples=samples))

    assert run(34.23) == []
    assert run(34.2) == ["Der SOC steigt um 4,2 Prozentpunkte, verlangt mindestens 4,22."]
    assert raised.find(result_of(cfg, "plan-netzladen")) == ["Der Lauf hat keine Probe."]


def test_plan_netzladen_scales_with_capacity(cfg) -> None:
    raised = check("plan-netzladen", 1)
    big = cfg.model_copy(update={"battery": cfg.battery.model_copy(update={"usable_kwh": 60.0})})
    # 0,9 · 3 kW · 0,5 h · √0,88 = 1,266 kWh, von 60 kWh 2,11 Prozentpunkte
    samples = [sample(0, soc_pct=30.0), sample(1, soc_pct=31.0), sample(2, soc_pct=32.12)]
    assert raised.find(result_of(big, "plan-netzladen", samples=samples)) == []
    too_little = [sample(0, soc_pct=30.0), sample(2, soc_pct=32.0)]
    assert raised.find(result_of(big, "plan-netzladen", samples=too_little)) == [
        "Der SOC steigt um 2 Prozentpunkte, verlangt mindestens 2,11."
    ]
    # dieselbe Probe wäre bei 30 kWh zu wenig
    assert raised.find(result_of(cfg, "plan-netzladen", samples=samples)) == [
        "Der SOC steigt um 2,12 Prozentpunkte, verlangt mindestens 4,22."
    ]


def test_violation_text_never_reads_like_the_limit(cfg, monkeypatch) -> None:
    """Ein Verstoß, den die Rundung auf die Grenze legte, zeigt eine Stelle mehr: sonst stünde da
    „1,02 kWh, verlangt höchstens 1,02 kWh“."""
    # Netzbezug einer Viertelstunde: 4081,6 W · 900 s = 1,0204 kWh gegen 1,02; 3598,4 W = 0,8996
    quarters = check("plan-budget", 0)

    def quarter(grid_w: float, begin: int) -> list[str]:
        samples = [
            sample(t, grid_w=grid_w if begin <= t < begin + 900 else 4000.0) for t in range(2820)
        ]
        return quarters.find(result_of(cfg, "plan-budget", samples=samples))

    assert quarter(4081.6, 960) == [
        "Netzbezug der Viertelstunde ab 18:15: 1,0204 kWh, verlangt höchstens 1,02 kWh."
    ]
    assert quarter(3598.4, 1860) == [
        "Netzbezug der Viertelstunde ab 18:30: 0,8996 kWh, verlangt mindestens 0,9 kWh."
    ]
    # 40 Entscheidungen in 3599 s sind 40,01 je Stunde, gerundet 40,0
    few = check("plan-halten", 2)
    decisions = [
        decision("plan-halten", t, VICTRON, SETPOINT, 900.0, "plan_hold") for t in range(40)
    ]
    samples = [sample(t) for t in range(3599)]
    [text] = few.find(result_of(cfg, "plan-halten", samples=samples, decisions=decisions))
    assert "40,0111142 je Stunde, verlangt höchstens 40." in text
    # 8999 von 10000 s in Boost sind 89,99 %, gerundet 90,0
    boosted = check("plan-boost", 0)
    samples = [
        sample(t, egolf=car(0.0 if t < 60 + 1001 else 16.0, "boost" if t >= 60 else "pv"))
        for t in range(10060)
    ]
    assert boosted.find(result_of(cfg, "plan-boost", samples=samples)) == [
        "„egolf“ lädt ab 60 s in 89,99 % der Sekunden in Boost, verlangt mindestens 90 %."
    ]
    # 5,004 % aus dem Akku gegen 5, gerundet 5,0
    monkeypatch.setattr("bindaems.tools.sim.scenarios.battery_into_car_pct", lambda *_: 5.004)
    assert check("akku-wolkig", 0).find(result_of(cfg, "akku-wolkig")) == [
        "„egolf“ lädt zu 5,004 % aus dem Akku, verlangt höchstens 5 %."
    ]


def test_after_the_plan_expires_the_battery_carries_the_house(cfg) -> None:
    free = check("plan-abgelaufen", 1)
    assert free.text == (
        "Ab 26 min gilt kein Override, und der Akku trägt das Haus: Akkuleistung höchstens "
        "−Haus + 300 W."
    )

    def run(changes: dict[int, tuple[float, float | None]]) -> list[str]:
        samples = []
        for t in range(1550, 1600):
            battery_w, override_w = changes.get(t, (-900.0, None))
            samples.append(sample(t, battery_w=battery_w, house_w=900.0, override_w=override_w))
        return free.find(result_of(cfg, "plan-abgelaufen", samples=samples))

    assert run({}) == [] and run({1559: (0.0, 900.0)}) == []  # vor 26 min
    assert run({1560: (-600.0, None)}) == []  # genau an der Grenze
    assert run({1570: (-900.0, 900.0)}) == ["Bei 1570 s gilt ein Override (900 W)."]
    assert run({1570: (-500.0, None), 1571: (0.0, None)}) == [
        "Akkuleistung bei 1570 s −500 W bei 900 W Haus, verlangt höchstens −600 W (2 s darüber)."
    ]
    short = [sample(t) for t in range(100)]
    assert free.find(result_of(cfg, "plan-abgelaufen", samples=short)) == [
        "Der Lauf endet vor 1560 s."
    ]


def test_the_budget_holds_each_quarter_between_0_9_and_1_02_kwh(cfg) -> None:
    quarters = check("plan-budget", 0)
    assert quarters.text == (
        "Netzbezug der Viertelstunden ab 18:00, 18:15 und 18:30 je zwischen 0,9 und 1,02 kWh."
    )

    def run(grid_w: dict[int, float]) -> list[str]:
        samples = [sample(t, grid_w=grid_w.get(t, 4000.0)) for t in range(2820)]
        return quarters.find(result_of(cfg, "plan-budget", samples=samples))

    assert run({}) == []  # 1 kWh je Viertelstunde
    assert run(dict.fromkeys(range(60), 9000.0)) == []  # vor 18:00 zählt nicht
    assert run(dict.fromkeys(range(960, 1860), 4100.0)) == [
        "Netzbezug der Viertelstunde ab 18:15: 1,025 kWh, verlangt höchstens 1,02 kWh."
    ]
    # ohne Plan trüge der Akku das Haus: kaum Netzbezug
    assert run(dict.fromkeys(range(1860, 2760), 900.0)) == [
        "Netzbezug der Viertelstunde ab 18:30: 0,225 kWh, verlangt mindestens 0,9 kWh."
    ]
    assert run(dict.fromkeys(range(960, 1860), 3600.0)) == []  # genau 0,9 kWh


def test_the_budget_scenario_names_the_plan_and_the_guard(cfg) -> None:
    grid, guard = check("plan-budget", 1), check("plan-budget", 2)
    assert grid.text == "Eine Entscheidung an „victron“ nennt „plan_grid_charge“."
    assert guard.text == "Eine Entscheidung an „victron“ nennt „peak_guard“."
    both = decision("plan-budget", 61, VICTRON, SETPOINT, 3700.0, "plan_grid_charge", "peak_guard")
    plain = decision("plan-budget", 61, VICTRON, SETPOINT, 6900.0, "plan_grid_charge")
    assert grid.find(result_of(cfg, "plan-budget", decisions=[both])) == []
    assert guard.find(result_of(cfg, "plan-budget", decisions=[both])) == []
    assert guard.find(result_of(cfg, "plan-budget", decisions=[plain])) == [
        "Keine Entscheidung an „victron“ mit „peak_guard“."
    ]
    assert grid.find(result_of(cfg, "plan-budget")) == [
        "Keine Entscheidung an „victron“ mit „plan_grid_charge“."
    ]


def test_the_boost_runs_through_all_new_plans(cfg) -> None:
    boosted = check("plan-boost", 0)
    assert boosted.text == "Ab 1 min lädt „egolf“ in mindestens 90 % der Sekunden in Boost."

    def run(idle: int, *, mode: str = "boost") -> list[str]:
        samples = [
            sample(t, egolf=car(0.0 if t < 60 + idle else 16.0, mode if t >= 60 else "pv"))
            for t in range(160)
        ]
        return boosted.find(result_of(cfg, "plan-boost", samples=samples))

    assert run(0) == [] and run(10) == []  # 90 von 100 s
    assert run(11) == [
        "„egolf“ lädt ab 60 s in 89 % der Sekunden in Boost, verlangt mindestens 90 %."
    ]
    assert run(0, mode="pv") == [
        "„egolf“ lädt ab 60 s in 0 % der Sekunden in Boost, verlangt mindestens 90 %."
    ]
    short = [sample(t) for t in range(60)]
    assert boosted.find(result_of(cfg, "plan-boost", samples=short)) == ["Der Lauf endet vor 60 s."]
    assert check("plan-boost", 1).text == (
        "Höchstens 5 % der Energie von „egolf“ kommen aus dem Akku (ohne Übergänge)."
    )
