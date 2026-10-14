"""Tests der Szenarien für Akku und Spitzen (Plan 2c-2a, Design 10.2): Aufbau, Ereignisse und
Prüfungen an Hand gebauter Ergebnisse. Die ganzen Läufe prüft
``tests/integration/test_sim_scenarios.py``.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Final

import pytest

from bindaems.core.api.app import HealthReport
from bindaems.shared.config import Config
from bindaems.shared.control import VICTRON, ChargeMode, CommandKind, VehicleIntent
from bindaems.shared.decisions import Decision, DecisionResult
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
        if t == second and not isinstance(action, _Boost):
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
    return SimResult(SCENARIOS[name], metrics, decisions, states or [], samples, [])


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
        "spitze-15min": (60.0, "twc", "evcs", 5, False, {"tesla": min_pv, "egolf": now}),
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
