"""Tests des Laufs und der Szenarien des Simulators: Ablauf je Sekunde, Sendezeiten, Wetter.

Die Läufe sind kurz; die ganzen Szenarien prüft ``tests/integration/test_sim_scenarios.py``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Final

import pytest
from pydantic import ValidationError

from bindaems.core.adapters.victron_mqtt import SETPOINT_OVERRIDE_PATH
from bindaems.core.api.app import HealthReport
from bindaems.core.regulation_link import RegulationLink
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import Outcome, Verdict
from bindaems.shared.config import Config
from bindaems.shared.control import (
    VICTRON,
    ActuatorMode,
    ChargeMode,
    Command,
    CommandKind,
    GuestIntent,
    Intent,
    VehicleIntent,
)
from bindaems.shared.decisions import Decision, DecisionResult
from bindaems.shared.timeutil import LOCAL_TZ
from bindaems.tools.sim import runner
from bindaems.tools.sim.metrics import CarSample, Forgotten, OwnValue, Sample, measure
from bindaems.tools.sim.model import GUEST_CAR, PlantModel
from bindaems.tools.sim.runner import SimResult, run_scenario, sent_times
from bindaems.tools.sim.scenarios import (
    CLOUD_FACTORS,
    HOUSE_NOISE_W,
    HOUSE_W,
    SCENARIOS,
    SUN_PEAK_W,
    Action,
    Checks,
    Scenario,
    _Boost,
    sun_w,
)

VIN: Final = "5YJ3E7EB0MF000000"  # der Tesla der Beispielkonfiguration
T0: Final = datetime(2026, 10, 12, 16, 0, tzinfo=UTC)  # 18:00 Wiener Zeit


def vienna(month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=LOCAL_TZ)


def weather(scenario: Scenario, cfg: Config, second: int) -> tuple[float, tuple[float, ...]]:
    """PV und Hauslast, die die Ereignisse der Sekunde ``second`` im Modell einstellen."""
    model = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    for t, action in scenario.events:
        if t == second:
            action(model, None)  # type: ignore[arg-type]  # das Wetter braucht die Laufzeit nicht
    return model.pv_w, model.house_w


def test_the_first_five_scenarios_in_the_order_of_the_design() -> None:
    assert list(SCENARIOS)[:5] == ["sonnig", "wolkig", "abend-sofort", "reserve", "nur-laden"]
    sunny = SCENARIOS["sonnig"]
    assert (sunny.title, sunny.start, sunny.duration_s) == (
        "Sonniger Tag, beide Autos im PV-Modus",
        datetime(2026, 6, 21, 8, 0, tzinfo=UTC),  # 10:00 Wiener Zeit
        7200,
    )
    assert SCENARIOS["nur-laden"].start == T0
    first_five = list(SCENARIOS.values())[:5]
    assert all(scenario.live and scenario.intent.control for scenario in first_five)


# --- Wetter --------------------------------------------------------------------------------------


def test_the_sun_peaks_at_13_00_vienna_time() -> None:
    assert sun_w(vienna(6, 21, 13)) == pytest.approx(SUN_PEAK_W)
    assert sun_w(vienna(6, 21, 9)) == pytest.approx(SUN_PEAK_W * 2**-0.5)  # 4 von 16 h
    assert sun_w(vienna(6, 21, 17)) == pytest.approx(sun_w(vienna(6, 21, 9)))
    assert sun_w(vienna(6, 21, 5)) == pytest.approx(0.0)
    assert sun_w(vienna(6, 21, 4)) == sun_w(vienna(6, 21, 22)) == 0.0


def test_sunny_day_with_a_noisy_house(cfg) -> None:
    sunny = SCENARIOS["sonnig"]
    pv, _ = weather(sunny, cfg, 3600)  # 11:00
    assert pv == pytest.approx(sun_w(vienna(6, 21, 11)))
    loads = [weather(sunny, cfg, t)[1] for t in range(0, 7200, 7)]
    flat = [w for load in loads for w in load]
    assert all(HOUSE_W - HOUSE_NOISE_W <= w <= HOUSE_W + HOUSE_NOISE_W for w in flat)
    assert sum(flat) / len(flat) == pytest.approx(HOUSE_W, abs=5.0)
    assert len(set(flat)) > len(flat) // 2  # Rauschen, kein fester Wert


def test_clouds_switch_between_full_sun_and_a_quarter(cfg) -> None:
    cloudy = SCENARIOS["wolkig"]
    factors = []
    for t in range(cloudy.duration_s):
        pv, _ = weather(cloudy, cfg, t)
        factors.append(round(pv / sun_w(cloudy.start + timedelta(seconds=t)), 6))
    assert set(factors) == set(CLOUD_FACTORS)
    assert factors[0] == 1.0
    runs = [1]
    for before, after in pairwise(factors):
        if after == before:
            runs[-1] += 1
        else:
            runs.append(1)
    assert all(30 <= run <= 180 for run in runs[:-1])  # das letzte schneidet das Ende ab
    assert len(set(runs)) > 5


def test_the_tesla_starts_close_to_its_limit(cfg) -> None:
    for name, soc in (("sonnig", 72.0), ("wolkig", 75.0)):
        model = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
        SCENARIOS[name].setup(model)
        assert (model.cars["tesla"].soc_pct, model.cars["tesla"].wallbox) == (soc, "twc")
        assert model.cars["egolf"].wallbox == "evcs"


def test_reserve_has_more_load_on_l1(cfg) -> None:
    _, house = weather(SCENARIOS["reserve"], cfg, 600)
    assert house[0] == pytest.approx(HOUSE_W + 1000.0, abs=HOUSE_NOISE_W)
    assert house[1] == pytest.approx(HOUSE_W, abs=HOUSE_NOISE_W)


def test_evening_peak_on_l1(cfg) -> None:
    evening = SCENARIOS["abend-sofort"]
    assert weather(evening, cfg, 1199)[1][0] < 400.0
    for t in (1200, 1499):
        pv, house = weather(evening, cfg, t)
        assert pv == 0.0 and HOUSE_W + 2500.0 - HOUSE_NOISE_W <= house[0]
        assert house[1] <= HOUSE_W + HOUSE_NOISE_W
    assert weather(evening, cfg, 1500)[1][0] < 400.0


def test_charger_only_ramp_on_l1(cfg) -> None:
    ramp = SCENARIOS["nur-laden"]
    noise = HOUSE_NOISE_W
    assert weather(ramp, cfg, 1199)[1][0] <= HOUSE_W + noise
    assert weather(ramp, cfg, 1800)[1][0] == pytest.approx(HOUSE_W + 1500.0, abs=noise)
    for t in (2400, 3599):
        assert weather(ramp, cfg, t)[1][0] == pytest.approx(HOUSE_W + 3000.0, abs=noise)


# --- Lauf ----------------------------------------------------------------------------------------


async def test_a_run_samples_every_second_and_reads_health_every_10_s(cfg, tmp_path) -> None:
    result = await run_scenario(SCENARIOS["reserve"], cfg, tmp_path, duration_s=150)
    assert [sample.t for sample in result.samples] == list(range(150))
    assert [t for t, _ in result.health] == list(range(0, 150, 10))
    assert result.health[0][1].mode == "OBSERVE"
    assert result.health[-1][1].mode == "CONTROL"  # Daten nach 30 s, Watchdog 30 s später scharf
    started = [
        (d.kind, d.requested, d.result)
        for d in result.decisions
        if d.actuator == "wallbox.evcs" and d.result is DecisionResult.OK
    ]
    assert (CommandKind.CHARGING, True, DecisionResult.OK) in started  # live gesendet
    egolf, tesla = result.samples[-1].cars["egolf"], result.samples[-1].cars["tesla"]
    assert (egolf.charging, egolf.mode, egolf.min_a) == (True, "now", 6)
    assert egolf.power_w == pytest.approx(egolf.current_a * 2 * 230.0)
    assert (tesla.charging, tesla.current_a, tesla.mode, tesla.min_a) == (False, 0.0, "pv", 5)


async def test_events_act_before_the_second_and_checks_see_the_result(cfg, tmp_path) -> None:
    seen: list[SimResult] = []

    def check(result: SimResult) -> list[str]:
        seen.append(result)
        return ["Prüfung des Szenarios."]

    def full(model: PlantModel) -> None:
        model.soc_pct = 100.0  # der Akku nimmt nichts: Die PV geht ins Netz

    def sunny(model: PlantModel, rt: object) -> None:
        model.pv_w = 6900.0

    scenario = Scenario(
        name="test",
        title="Test",
        start=T0,
        duration_s=6,
        seed=1,
        setup=full,
        intent=Intent(version=1, control=True),
        live=False,
        events=((3, sunny),),
        checks=check,
    )
    result = await run_scenario(scenario, cfg, tmp_path)
    assert [sample.grid_a["L1"] for sample in result.samples] == [
        0.0,
        0.0,
        0.0,
        pytest.approx(-10.0),  # 2300 W je Phase aus der PV
        pytest.approx(-10.0),
        pytest.approx(-10.0),
    ]
    assert result.failures == ["Prüfung des Szenarios."]
    assert seen[0].samples == result.samples and seen[0].failures == []


def tessie_decision(
    seconds: int, kind: CommandKind, value: object, result: DecisionResult
) -> Decision:
    return Decision(
        id=f"{kind}/{seconds}",
        ts=T0 + timedelta(seconds=seconds),
        actuator="vehicle.tesla",
        kind=kind,
        requested=value,  # type: ignore[arg-type]
        applied=value,  # type: ignore[arg-type]
        dry_run=result is DecisionResult.DRY_RUN,
        outcome="accepted",
        result=result,
        codes=("mode_pv",),
        text="",
        inputs={},
    )


def test_sent_times_come_from_the_requests_at_tessie(cfg) -> None:
    def request(seconds: int, name: str) -> tuple[datetime, str]:
        return T0 + timedelta(seconds=seconds), f"/{VIN}/command/{name}"

    requests = [
        request(0, "set_charging_amps"),
        request(1, "start_charging"),
        request(40, "set_charging_amps"),  # Ergebnis im selben Zyklus wie der nächste Befehl
        request(95, "stop_charging"),
    ]
    current = tessie_decision(40, CommandKind.CURRENT, 10.0, DecisionResult.OK)
    start = tessie_decision(3, CommandKind.CHARGING, True, DecisionResult.OK)
    second = tessie_decision(70, CommandKind.CURRENT, 12.0, DecisionResult.ERROR)
    stop = tessie_decision(97, CommandKind.CHARGING, False, DecisionResult.TIMEOUT)
    dry = tessie_decision(98, CommandKind.CURRENT, 8.0, DecisionResult.DRY_RUN)
    not_sent = tessie_decision(99, CommandKind.CURRENT, 8.0, DecisionResult.NOT_SENT)
    decisions = [start, current, second, stop, dry, not_sent]
    assert sent_times(decisions, requests, cfg) == {
        current.id: T0,
        start.id: T0 + timedelta(seconds=1),
        second.id: T0 + timedelta(seconds=40),
        stop.id: T0 + timedelta(seconds=95),
    }


def test_sent_times_know_the_charge_limit(cfg) -> None:
    sent = T0 + timedelta(seconds=5)
    requests = [(T0, f"/{VIN}/command/stop_charging"), (sent, f"/{VIN}/command/set_charge_limit")]
    limit = tessie_decision(30, CommandKind.CHARGE_LIMIT, 75, DecisionResult.OK)
    assert sent_times([limit], requests, cfg) == {limit.id: sent}


async def test_the_result_knows_when_tessie_got_each_command(cfg, tmp_path) -> None:
    result = await run_scenario(SCENARIOS["tesla-ladelimit"], cfg, tmp_path, duration_s=180)
    (limit,) = [d for d in result.decisions if d.kind is CommandKind.CHARGE_LIMIT]
    assert set(result.sent) >= {limit.id}
    assert result.sent[limit.id] < limit.ts


def test_the_example_intent_names_both_cars() -> None:
    sunny = SCENARIOS["sonnig"].intent
    assert sunny.vehicles == {
        "tesla": VehicleIntent(mode=ChargeMode.PV),
        "egolf": VehicleIntent(mode=ChargeMode.PV),
    }
    assert SCENARIOS["reserve"].intent.vehicles["egolf"].mode is ChargeMode.NOW


async def test_a_rejected_intent_stops_the_run(cfg, tmp_path) -> None:
    unknown = Intent(version=1, control=True, vehicles={"bus": VehicleIntent()})
    scenario = replace(SCENARIOS["reserve"], intent=unknown)
    with pytest.raises(ValueError, match="Absicht des Szenarios abgelehnt: Unbekanntes Fahrzeug"):
        await run_scenario(scenario, cfg, tmp_path, duration_s=1)


async def test_decisions_are_read_page_by_page(cfg, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runner, "READ_LIMIT", 1)  # je Lesen eine Gruppe gleicher Zeit
    result = await run_scenario(SCENARIOS["reserve"], cfg, tmp_path, duration_s=70)
    started = [(d.kind, d.requested) for d in result.decisions if d.actuator == "wallbox.evcs"]
    assert started == [(CommandKind.CURRENT, 13.0), (CommandKind.CHARGING, True)]  # Importziel
    # Mit „Laden an“ entscheidet im selben Zyklus der Akku-Sollwert (Dry-Run): Diese Gruppe reicht
    # über das Limit hinaus und kommt doch ganz und nur einmal.
    assert [d.actuator for d in result.decisions] == ["wallbox.evcs", "wallbox.evcs", VICTRON]
    assert result.decisions[1].ts == result.decisions[2].ts
    assert len({d.id for d in result.decisions}) == len(result.decisions)


async def test_samples_name_the_cars_the_regulation_steers_live(cfg, tmp_path) -> None:
    def crossed(model: PlantModel) -> None:
        model.plug("tesla", "evcs")  # an der EVCS steuert die Regelung die EVCS
        model.plug("egolf", "twc")  # ohne Tessie am Wall Connector: nur Last

    def usual(model: PlantModel) -> None:
        model.plug("tesla", "twc")  # das Fahrzeug selbst; der e-Golf ist abgesteckt

    async def steered(setup: object, *, live: bool, evcs_only: bool = False) -> dict[str, bool]:
        actuators = {"wallbox.evcs": ActuatorMode.LIVE} if evcs_only else {}
        scenario = Scenario(
            name="test",
            title="Test",
            start=T0,
            duration_s=2,
            seed=1,
            setup=setup,  # type: ignore[arg-type]
            intent=Intent(version=1, control=True, actuators=actuators),
            live=live,
            events=(),
            checks=lambda result: [],
        )
        data_dir = tmp_path / f"{getattr(setup, '__name__', 'x')}-{live}-{evcs_only}"
        data_dir.mkdir()
        result = await run_scenario(scenario, cfg, data_dir)
        return {key: car.steered for key, car in result.samples[-1].cars.items()}

    def expected(tesla: bool) -> dict[str, bool]:
        # der e-Golf ist ohne Tessie nie gesteuert, der abgesteckte Gast hinter den Fahrzeugen
        # der Konfiguration ebenso
        return {"tesla": tesla, "egolf": False, GUEST_CAR: False}

    assert await steered(crossed, live=True) == expected(True)
    assert await steered(usual, live=True) == expected(True)
    assert await steered(crossed, live=False) == expected(False)
    assert await steered(usual, live=False) == expected(False)
    # nur die EVCS live: Der Tesla an ihr ist gesteuert, am Wall Connector nicht
    assert await steered(crossed, live=False, evcs_only=True) == expected(True)
    assert await steered(usual, live=False, evcs_only=True) == expected(False)


def guest_intent(mode: ChargeMode, *, evcs_live: bool) -> Intent:
    """Eine Absicht, die dem Gast den Modus ``mode`` gibt, mit der EVCS live oder im Dry-Run."""
    actuators = {"wallbox.evcs": ActuatorMode.LIVE} if evcs_live else {}
    return Intent(version=1, control=True, actuators=actuators, guest=GuestIntent(mode=mode))


def test_samples_carry_the_guest_in_the_guest_mode(cfg) -> None:
    # Der Gast steckt an der EVCS, die Absicht gibt ihm Sofort und schaltet die EVCS live; die EVCS
    # lädt mit ihrem Sollstrom von 6 A. Die Probe führt ihn hinter den Fahrzeugen der Konfiguration
    # mit dem Modus „now“, dem Mindeststrom der EVCS und als gesteuert.
    model = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    intent = guest_intent(ChargeMode.NOW, evcs_live=True)
    model.wallboxes["evcs"].start_stop = 1
    model.plug(GUEST_CAR, "evcs")
    model.step(T0)
    sample = runner._sample(0, model, cfg, intent, T0)
    assert list(sample.cars) == ["tesla", "egolf", GUEST_CAR]
    assert sample.cars[GUEST_CAR] == CarSample(
        charging=True,
        current_a=6.0,
        power_w=6.0 * 3 * 230.0,
        mode="now",
        min_a=6,
        complete=False,
        steered=True,
        soc_pct=pytest.approx(50.0, abs=0.01),  # eine Sekunde mit 6 A
    )
    model.unplug(GUEST_CAR)
    model.step(T0 + timedelta(seconds=1))
    gone = runner._sample(1, model, cfg, intent, T0).cars[GUEST_CAR]
    assert gone == CarSample(
        False,
        0.0,
        0.0,
        "now",
        6,
        complete=False,
        steered=False,
        soc_pct=pytest.approx(50.0, abs=0.01),
    )


def test_samples_carry_the_soc_of_every_car(cfg) -> None:
    # Plan 4b, Task 8: der Ladestand des Modells, für die Prüfungen von „Geplant“
    model = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    model.cars["tesla"].soc_pct, model.cars["egolf"].soc_pct = 61.5, 48.25
    model.step(T0)
    cars = runner._sample(0, model, cfg, None, T0).cars
    assert {key: car.soc_pct for key, car in cars.items()} == {
        "tesla": 61.5,
        "egolf": 48.25,
        GUEST_CAR: 50.0,
    }


def test_the_guest_without_an_intent_is_in_pv(cfg) -> None:
    model = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    model.plug(GUEST_CAR, "evcs")
    guest = runner._sample(0, model, cfg, None, T0).cars[GUEST_CAR]
    assert (guest.mode, guest.min_a, guest.steered) == ("pv", 6, False)
    # eine Absicht ohne Angabe zum Gast: auch PV (``GuestIntent()``)
    plain = Intent(version=1, control=True)
    assert runner._sample(0, model, cfg, plain, T0).cars[GUEST_CAR].mode == "pv"


def test_a_full_guest_is_complete_in_the_sample(cfg) -> None:
    model = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    assert runner._sample(0, model, cfg, None, T0).cars[GUEST_CAR].complete is False
    model.cars[GUEST_CAR].soc_pct = 100.0  # sein Ladelimit
    assert runner._sample(1, model, cfg, None, T0).cars[GUEST_CAR].complete is True


def test_the_guest_at_the_wall_connector_is_not_steered(cfg) -> None:
    # Ein Auto ohne Tessie hat am Wall Connector keinen Aktor, den die Absicht live schalten kann.
    model = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    model.plug(GUEST_CAR, "twc")
    model.step(T0)
    intent = guest_intent(ChargeMode.MIN_PV, evcs_live=True)
    guest = runner._sample(0, model, cfg, intent, T0).cars[GUEST_CAR]
    assert (guest.charging, guest.current_a, guest.power_w) == (True, 16.0, 16.0 * 3 * 230.0)
    assert (guest.mode, guest.min_a, guest.steered) == ("min_pv", 6, False)


def test_the_guest_has_the_minimum_current_of_the_evcs_it_is_plugged_to(cfg) -> None:
    evcs = cfg.wallboxes["evcs"].model_copy(update={"min_a": 8, "safe_a": 8})
    config = cfg.model_copy(update={"wallboxes": cfg.wallboxes | {"evcs": evcs}})
    model = PlantModel(config, random.Random(1))  # noqa: S311 - Saat der Simulation
    intent = guest_intent(ChargeMode.PV, evcs_live=False)
    assert runner._sample(0, model, config, intent, T0).cars[GUEST_CAR].min_a == 6  # abgesteckt
    model.plug(GUEST_CAR, "evcs")
    assert runner._sample(0, model, config, intent, T0).cars[GUEST_CAR].min_a == 8
    model.unplug(GUEST_CAR)
    model.plug(GUEST_CAR, "twc")  # ein Wall Connector hat keinen eigenen Mindeststrom
    assert runner._sample(0, model, config, intent, T0).cars[GUEST_CAR].min_a == 6


async def test_the_unplugged_guest_changes_no_metric(cfg, tmp_path) -> None:
    # Der abgesteckte Gast zählt überall als „lädt nicht“: Die Kennzahlen eines Laufs sind die der
    # Proben ohne ihn.
    result = await run_scenario(SCENARIOS["reserve"], cfg, tmp_path, duration_s=150)
    assert list(result.samples[-1].cars) == ["tesla", "egolf", GUEST_CAR]
    assert not any(s.cars[GUEST_CAR].charging for s in result.samples)
    without = [
        replace(s, cars={key: car for key, car in s.cars.items() if key != GUEST_CAR})
        for s in result.samples
    ]
    assert measure(without, result.decisions, cfg) == measure(result.samples, result.decisions, cfg)


async def test_the_metrics_get_every_own_value_of_the_regulation(
    cfg, tmp_path, monkeypatch
) -> None:
    # Präzisierung 10: Eigene Werte setzt jedes Urteil mit Wert und UNCHANGED; UNCHANGED steht in
    # keiner Entscheidung, darum gibt der Lauf sie der Kennzahl selbst.
    seen: list[object] = []
    real = runner.measure

    def measure_spy(*args: object, **kwargs: object) -> object:
        seen.append(kwargs.get("own"))
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(runner, "measure", measure_spy)

    def at_13_a(model: PlantModel) -> None:
        SCENARIOS["reserve"].setup(model)
        model.wallboxes["evcs"].set_current_a = 13.0  # schon der Strom, den die Regelung will

    current = ("wallbox.evcs", CommandKind.CURRENT, 13.0)
    charging = ("wallbox.evcs", CommandKind.CHARGING, True)
    override = (VICTRON, CommandKind.GRID_SETPOINT, 5980.0)  # e-Golf in Sofort: 13 A × 2 × 230 V
    for live, dry_run in ((True, False), (False, True)):
        scenario = replace(SCENARIOS["reserve"], setup=at_13_a, live=live)
        data_dir = tmp_path / str(live)
        data_dir.mkdir()
        result = await run_scenario(scenario, cfg, data_dir, duration_s=70)
        seen_own = seen.pop()
        assert isinstance(seen_own, list)
        # Lädt der e-Golf (im Modell nur, wenn die EVCS live ist), entscheidet der Akku-Sollwert im
        # Dry-Run mit; auch seine eigenen Werte bekommt die Kennzahl (Präzisierung 17).
        battery = [e for e in seen_own if isinstance(e, OwnValue) and e.actuator == VICTRON]
        assert battery == ([OwnValue(*override, dry_run=True)] if live else [])
        own = [event for event in seen_own if event not in battery]
        before = own[:-2]
        assert before and all(isinstance(event, Forgotten) for event in before)
        assert Forgotten() in before  # bis zum Regelbetrieb ruht die Regelung (idle)
        # live: Beim Scharfschalten wechselt die EVCS vom Dry-Run nach live (forget)
        assert (Forgotten("wallbox.evcs") in before) is live
        assert own[-2:] == [OwnValue(*current, dry_run=False), OwnValue(*charging, dry_run=dry_run)]
        evcs = [(d.kind, d.requested) for d in result.decisions if d.actuator == "wallbox.evcs"]
        assert evcs == [charging[1:]]


# --- Victron live, Steuerwerte, Boost und die Felder der Probe ------------------------------------


def bare_scenario(
    *, events: tuple[tuple[int, Action], ...] = (), duration_s: int = 5, **changes: object
) -> Scenario:
    """Ein Szenario ohne Aufbau, mit der Absicht „Regelbetrieb“ und ohne Prüfungen."""
    scenario = Scenario(
        name="test",
        title="Test",
        start=T0,
        duration_s=duration_s,
        seed=1,
        setup=lambda model: None,
        intent=Intent(version=1, control=True),
        live=False,
        events=events,
        checks=lambda result: [],
    )
    return replace(scenario, **changes)


def test_victron_live_scenarios_allow_and_switch_victron(cfg, tmp_path) -> None:
    assert not cfg.victron.live_allowed
    assert not runner.sim_config(cfg, tmp_path).victron.live_allowed  # wie bisher
    allowed = runner.sim_config(cfg, tmp_path, victron=True)
    assert allowed.victron.live_allowed and allowed.wallboxes["evcs"].live_allowed
    assert allowed.vehicles["tesla"].live_allowed and allowed.core.data_dir == tmp_path
    assert not cfg.victron.live_allowed  # die übergebene Konfiguration bleibt, wie sie ist
    # ``live`` schaltet EVCS und Tesla, ``victron_live`` dazu Victron
    scenario = SCENARIOS["reserve"]
    live = {"wallbox.evcs": ActuatorMode.LIVE, "vehicle.tesla": ActuatorMode.LIVE}
    assert runner._intent(scenario, cfg).actuators == live
    with_victron = runner._intent(replace(scenario, victron_live=True), cfg)
    assert with_victron.actuators == live | {VICTRON: ActuatorMode.LIVE}
    assert with_victron.model_copy(update={"actuators": live}) == runner._intent(scenario, cfg)
    # ``victron_live`` allein schaltet nur Victron
    only = runner._intent(replace(scenario, live=False, victron_live=True), cfg)
    assert only.actuators == {VICTRON: ActuatorMode.LIVE}
    # ohne beides bleibt es bei der Absicht des Szenarios
    dry = replace(scenario, live=False)
    assert runner._intent(dry, cfg) == dry.intent and VICTRON not in dry.intent.actuators


def test_scenario_control_values_reach_the_config(cfg, tmp_path) -> None:
    assert cfg.control.peak_target_kw is None and cfg.control.support_max_w == 3000.0
    values = {"peak_target_kw": 8.0, "support_max_w": 2500.0}
    config = runner.sim_config(cfg, tmp_path, control=values)
    assert (config.control.peak_target_kw, config.control.support_max_w) == (8.0, 2500.0)
    assert config.control == cfg.control.model_copy(update=values)  # sonst unverändert
    assert cfg.control.peak_target_kw is None  # die übergebene Konfiguration bleibt
    assert runner.sim_config(cfg, tmp_path).control == cfg.control
    for invalid in ({"peak_target_kw": -1.0}, {"peak_target_kw": float("nan")}, {"peak_target": 8}):
        with pytest.raises(ValidationError):
            runner.sim_config(cfg, tmp_path, control=invalid)


def test_scenario_vehicle_values_reach_the_config(cfg, tmp_path) -> None:
    # Plan 4b, Task 8: etwa ``charge_limit_sync`` für „tesla-ladelimit“
    assert not cfg.vehicles["tesla"].charge_limit_sync
    config = runner.sim_config(cfg, tmp_path, vehicles={"tesla": {"charge_limit_sync": True}})
    tesla = config.vehicles["tesla"]
    assert tesla.charge_limit_sync and tesla.live_allowed  # die Freigabe des Laufs bleibt
    assert (
        tesla.model_copy(update={"charge_limit_sync": False, "live_allowed": False})
        == (cfg.vehicles["tesla"])
    )
    assert config.vehicles["egolf"] == cfg.vehicles["egolf"]
    assert not cfg.vehicles["tesla"].charge_limit_sync  # die übergebene Konfiguration bleibt
    for invalid in ({"egolf": {"charge_limit_sync": True}}, {"tesla": {"limit": 1}}):
        with pytest.raises(ValidationError):  # ohne tessie, unbekannter Name
            runner.sim_config(cfg, tmp_path, vehicles=invalid)
    with pytest.raises(KeyError):
        runner.sim_config(cfg, tmp_path, vehicles={"bus": {}})


def test_a_scenario_keeps_victron_in_the_dry_run_unless_it_says_otherwise() -> None:
    plain = bare_scenario()
    assert plain.victron_live is False and plain.control == {}
    with pytest.raises(TypeError):
        plain.control["peak_target_kw"] = 8.0  # type: ignore[index]
    existing = list(SCENARIOS.values())[:10]  # die zehn bestehenden Szenarien
    assert all(not s.victron_live and not s.control for s in existing)


async def test_a_victron_live_run_follows_the_override_of_the_core(cfg, tmp_path) -> None:
    # „reserve“: Der e-Golf lädt in Sofort mit 13 A × 2 Phasen × 230 V = 5980 W, und der
    # Akku-Sollwert verlangt dafür 5980 W Netzbezug. Mit Victron live schreibt der core den Override
    # ans Modell, und der ESS folgt ihm: Der Netzbezug ist der Override, der Akku trägt nur das
    # Haus. Im Dry-Run (wie bisher) bleibt es bei der Entscheidung.
    results = {}
    for name, scenario in (
        ("live", replace(SCENARIOS["reserve"], victron_live=True)),
        ("dry", SCENARIOS["reserve"]),
    ):
        data_dir = tmp_path / name
        data_dir.mkdir()
        results[name] = await run_scenario(scenario, cfg, data_dir, duration_s=100)
    live, dry = results["live"], results["dry"]
    first, last = live.samples[0], live.samples[-1]
    assert (first.override_w, first.override_age_s) == (None, None)
    assert last.override_w == 5980.0 and last.override_age_s is not None
    assert 0.0 <= last.override_age_s < 100.0
    assert last.grid_w == pytest.approx(5980.0)
    cars_w = sum(car.power_w for car in last.cars.values())
    assert last.battery_w == pytest.approx(5980.0 - last.house_w - cars_w)  # das Haus aus dem Akku
    battery = (CommandKind.GRID_SETPOINT, 5980.0)
    assert [(d.kind, d.requested, d.result) for d in live.decisions if d.actuator == VICTRON] == [
        (*battery, DecisionResult.OK)
    ]
    assert [(d.kind, d.requested, d.result) for d in dry.decisions if d.actuator == VICTRON] == [
        (*battery, DecisionResult.DRY_RUN)
    ]
    assert dry.samples[-1].override_w is None
    assert dry.samples[-1].grid_w == pytest.approx(0.0, abs=1.0)  # AcPowerSetPoint 0


async def test_scenario_control_values_switch_the_peak_guard_on(cfg, tmp_path) -> None:
    # Ohne Ziel ruht der Wächter (``peak.state`` „off“); mit ``peak_target_kw`` kennt der core das
    # Ziel und arbeitet (hier, nach dem Start des core mitten im Lauf, mit einer Lücke).
    peaks = {}
    for name, control in (("off", {}), ("on", {"peak_target_kw": 8.0})):
        data_dir = tmp_path / name
        data_dir.mkdir()
        scenario = replace(SCENARIOS["reserve"], control=control)
        result = await run_scenario(scenario, cfg, data_dir, duration_s=100)
        peaks[name] = result.health[-1][1].peak
    assert (peaks["off"]["state"], peaks["off"]["target_kw"]) == ("off", None)
    assert peaks["on"]["state"] != "off" and peaks["on"]["target_kw"] == 8.0


async def test_samples_carry_grid_pv_house_and_override(cfg, tmp_path) -> None:
    # SOC 50 %: PV 2000 W, Haus 300 + 400 + 500 = 1200 W; der Akku nimmt die 800 W, am Netz bleibt
    # nichts. Der Override 1500 W, den ein Ereignis vor der Physik der Sekunde 2 schreibt (zur Zeit
    # des Schritts von Sekunde 1), hebt den Netzbezug auf 1500 W. In Sekunde 4 fällt das Netz aus.
    def weather(model: PlantModel, rt: object) -> None:
        model.pv_w = 2000.0
        model.house_w = (300.0, 400.0, 500.0)

    def override(model: PlantModel, rt: object) -> None:
        assert model.mqtt_write(SETPOINT_OVERRIDE_PATH, 1500.0)

    def island(model: PlantModel, rt: object) -> None:
        model.outages.add("grid")

    scenario = bare_scenario(events=((0, weather), (2, override), (4, island)), duration_s=6)
    samples = (await run_scenario(scenario, cfg, tmp_path)).samples
    for sample in samples[:2]:
        assert (sample.pv_w, sample.house_w) == (2000.0, 1200.0)
        assert sample.grid_w == pytest.approx(0.0)
        assert (sample.override_w, sample.override_age_s) == (None, None)
    for t, sample in enumerate(samples[2:4], 2):
        assert (sample.override_w, sample.override_age_s) == (1500.0, t - 1.0)
        assert (sample.pv_w, sample.house_w) == (2000.0, 1200.0)
        assert sample.grid_w == pytest.approx(1500.0)
    for t, sample in enumerate(samples[4:], 4):  # Inselbetrieb: keine PV am AC-Eingang, kein Netz
        assert (sample.pv_w, sample.house_w, sample.grid_w) == (0.0, 1200.0, 0.0)
        assert (sample.override_w, sample.override_age_s) == (1500.0, t - 1.0)


async def test_a_boost_event_shows_in_the_samples(cfg, tmp_path) -> None:
    # Ab Sekunde 3 lädt der e-Golf in Boost bis zum absoluten Ende T0 + 8 s (``now < boost_until``).
    boost = _Boost(2, "egolf", T0 + timedelta(seconds=8))
    scenario = bare_scenario(events=((3, boost),), duration_s=12)
    result = await run_scenario(scenario, cfg, tmp_path)
    assert [s.cars["egolf"].mode for s in result.samples] == ["pv"] * 3 + ["boost"] * 5 + ["pv"] * 4
    assert {s.cars["tesla"].mode for s in result.samples} == {"pv"}


async def test_the_run_counts_the_roundtrip_of_the_scenario(cfg, tmp_path) -> None:
    # 3000 W aus dem Akku 10 s lang: 8,33 Wh am Netz, also 0,0278 % von 30 kWh; mit 88 %
    # Roundtrip zieht das 1/√0,88 mal so viel aus dem Akku (Präzisierung 15).
    def setup(model: PlantModel) -> None:
        model.soc_pct = 60.0
        model.house_w = (1000.0, 1000.0, 1000.0)

    drop = 3000.0 * 10 / 3600 / 30000.0 * 100
    assert bare_scenario().roundtrip_pct == 88.0
    for pct, expected in ((100.0, 60.0 - drop), (88.0, 60.0 - drop / (0.88**0.5))):
        scenario = bare_scenario(setup=setup, duration_s=10, roundtrip_pct=pct)
        result = await run_scenario(scenario, cfg, tmp_path / str(pct))
        assert result.samples[-1].soc_pct == pytest.approx(expected)


class FakeRegulation:
    """Was der Lauf an der Regelung (``rt.regulation``) einhüllt; merkt sich die Aufrufe."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def note_verdict(self, command: object, verdict: object, *, not_sent: bool = False) -> None:
        self.calls.append(("note_verdict", not_sent))

    def forget(self, actuators: object) -> None:
        self.calls.append(("forget", actuators))

    def idle(self, now: object) -> None:
        self.calls.append(("idle", now))


@dataclass
class FakeRuntime:
    regulation: object = field(default_factory=FakeRegulation)


def verdict_for(command: Command, outcome: Outcome, *, dry_run: bool) -> Verdict:
    value = None if outcome is Outcome.REJECTED else command.value
    return Verdict(command, outcome, value, (), dry_run)


SIX: Final = Command("vehicle.tesla", CommandKind.CURRENT, 6.0)
ON: Final = Command("wallbox.evcs", CommandKind.CHARGING, True)
OVERRIDE: Final = Command(VICTRON, CommandKind.GRID_SETPOINT, 2340.0, reasons=("battery_comp",))
NO_OVERRIDE: Final = Command(VICTRON, CommandKind.GRID_SETPOINT, None, reasons=("battery_comp",))


def wrapped() -> tuple[FakeRegulation, list[OwnValue | Forgotten]]:
    """Eine Regelung, eingehüllt vom Lauf, und die Liste ihrer eigenen Werte."""
    regulation = FakeRegulation()
    return regulation, runner._own_values(FakeRuntime(regulation))  # type: ignore[arg-type]


def dry_run_six(regulation: FakeRegulation) -> None:
    """Ein Dry-Run-Urteil „Strom 6 A“ an den Tesla."""
    regulation.note_verdict(SIX, verdict_for(SIX, Outcome.ACCEPTED, dry_run=True))


def repeats(cfg: Config, own: list[OwnValue | Forgotten]) -> int:
    return measure([], [], cfg, own=own).repeated_dry_runs


def test_verdicts_with_a_value_or_unchanged_are_own_values(cfg) -> None:
    regulation, own = wrapped()
    dry_run_six(regulation)
    regulation.note_verdict(SIX, verdict_for(SIX, Outcome.UNCHANGED, dry_run=True))
    regulation.note_verdict(SIX, verdict_for(SIX, Outcome.DEFERRED, dry_run=True))  # setzt nichts
    regulation.note_verdict(SIX, None)  # nicht betrachtet: setzt nichts
    dry_run_six(regulation)
    assert own == [
        OwnValue("vehicle.tesla", CommandKind.CURRENT, 6.0, dry_run=True),
        OwnValue("vehicle.tesla", CommandKind.CURRENT, 6.0, dry_run=False),
        OwnValue("vehicle.tesla", CommandKind.CURRENT, 6.0, dry_run=True),
    ]
    assert repeats(cfg, own) == 1  # das letzte Urteil wiederholt den Wert von UNCHANGED
    assert regulation.calls == [("note_verdict", False)] * 5  # die Regelung bekommt jeden Aufruf


def test_a_verdict_that_was_not_sent_is_no_own_value(cfg) -> None:
    # Ein Wert stand fest, ging aber nicht hinaus: Die Regelung löscht ihren Wert dieser Art,
    # und das nächste Urteil mit demselben Wert ist keine Wiederholung.
    regulation, own = wrapped()
    dry_run_six(regulation)
    not_sent = verdict_for(SIX, Outcome.ACCEPTED, dry_run=False)
    regulation.note_verdict(SIX, not_sent, not_sent=True)
    dry_run_six(regulation)
    assert own[1] == Forgotten("vehicle.tesla", CommandKind.CURRENT)
    assert repeats(cfg, own) == 0
    assert regulation.calls == [
        ("note_verdict", False),
        ("note_verdict", True),
        ("note_verdict", False),
    ]


def test_forget_drops_the_own_values_of_its_actuators(cfg) -> None:
    regulation, own = wrapped()
    dry_run_six(regulation)
    regulation.forget({"wallbox.evcs", "vehicle.tesla"})
    regulation.forget(set())
    dry_run_six(regulation)
    assert own[1:3] == [Forgotten("vehicle.tesla"), Forgotten("wallbox.evcs")]
    assert repeats(cfg, own) == 0
    forgotten = [("forget", {"wallbox.evcs", "vehicle.tesla"}), ("forget", set())]
    assert regulation.calls[1:3] == forgotten
    regulation.forget({"wallbox.evcs"})  # ein anderer Aktor: Der Wert des Tesla gilt weiter
    dry_run_six(regulation)
    assert repeats(cfg, own) == 1


def test_idle_drops_all_own_values(cfg) -> None:
    regulation, own = wrapped()
    dry_run_six(regulation)
    regulation.note_verdict(ON, verdict_for(ON, Outcome.ACCEPTED, dry_run=True))
    regulation.idle(T0)
    dry_run_six(regulation)
    regulation.note_verdict(ON, verdict_for(ON, Outcome.ACCEPTED, dry_run=True))
    assert own[2] == Forgotten()
    assert repeats(cfg, own) == 0
    assert ("idle", T0) in regulation.calls


def test_own_values_of_the_battery_are_recorded(cfg) -> None:  # Präzisierung 17
    # Der Lauf hüllt die ganze Regelung ein, also auch den Akku-Sollwert. Kommt ein Override nach
    # einem UNCHANGED für „kein Override“ wieder, ist er keine Wiederholung.
    regulation, own = wrapped()
    regulation.note_verdict(OVERRIDE, verdict_for(OVERRIDE, Outcome.ACCEPTED, dry_run=True))
    regulation.note_verdict(NO_OVERRIDE, verdict_for(NO_OVERRIDE, Outcome.UNCHANGED, dry_run=True))
    regulation.note_verdict(OVERRIDE, verdict_for(OVERRIDE, Outcome.ACCEPTED, dry_run=True))
    assert own == [
        OwnValue(VICTRON, CommandKind.GRID_SETPOINT, 2340.0, dry_run=True),
        OwnValue(VICTRON, CommandKind.GRID_SETPOINT, None, dry_run=False),
        OwnValue(VICTRON, CommandKind.GRID_SETPOINT, 2340.0, dry_run=True),
    ]
    assert repeats(cfg, own) == 0
    dry_runs = [value for value in own if isinstance(value, OwnValue) and value.dry_run]
    assert repeats(cfg, dry_runs) == 1  # ohne das UNCHANGED dazwischen eine Wiederholung


def test_a_refresh_is_an_own_value_without_a_decision(cfg) -> None:
    # Im Dry-Run entsteht für das Auffrischen keine Entscheidung: Es setzt den eigenen Wert erneut,
    # ist aber keine wiederholte Dry-Run-Entscheidung.
    regulation, own = wrapped()
    regulation.note_verdict(OVERRIDE, verdict_for(OVERRIDE, Outcome.ACCEPTED, dry_run=True))
    refresh = replace(OVERRIDE, refresh=True)
    regulation.note_verdict(refresh, verdict_for(refresh, Outcome.ACCEPTED, dry_run=True))
    assert own[-1] == OwnValue(VICTRON, CommandKind.GRID_SETPOINT, 2340.0, dry_run=False)
    assert repeats(cfg, own) == 0
    # Bei einer anderen Art hat ``refresh`` keine Wirkung: Die Entscheidung entsteht.
    six = replace(SIX, refresh=True)
    dry_run_six(regulation)
    regulation.note_verdict(six, verdict_for(six, Outcome.ACCEPTED, dry_run=True))
    assert own[-1] == OwnValue("vehicle.tesla", CommandKind.CURRENT, 6.0, dry_run=True)
    assert repeats(cfg, own) == 1


def test_a_pause_after_an_exception_drops_all_own_values(cfg, monkeypatch) -> None:
    # Wirft der Kern, ruht er über ``RegulationLink.idle``: Das Einhüllen sieht auch diese Pause.
    link = RegulationLink(cfg, HardLimits.from_config(cfg), started=T0)
    own = runner._own_values(FakeRuntime(link))  # type: ignore[arg-type]

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Testfehler in der Regelung")

    monkeypatch.setattr(link.regulation, "note_verdict", broken)
    link.start_cycle()
    link.note_verdict(OVERRIDE, verdict_for(OVERRIDE, Outcome.ACCEPTED, dry_run=True))
    assert link.failed
    assert own == [OwnValue(VICTRON, CommandKind.GRID_SETPOINT, 2340.0, dry_run=True), Forgotten()]


# --- Prüfungen der Szenarien ----------------------------------------------------------------------


def result_of(
    cfg: Config,
    samples: list[Sample],
    decisions: list[Decision] | None = None,
    health: list[tuple[int, HealthReport]] | None = None,
) -> SimResult:
    metrics = measure([], [], cfg)
    return SimResult(
        SCENARIOS["reserve"],
        metrics,
        decisions or [],
        health or [],
        samples,
        [],
        usable_kwh=cfg.battery.usable_kwh,
    )


def egolf_at(t: int, current_a: float, *, soc_pct: float = 60.0, l1: float = 0.0) -> Sample:
    """Der e-Golf mit ``current_a`` nach Sekunde ``t``, ``l1`` A auf L1 am Netz, sonst ruhig."""
    car = CarSample(current_a > 0, current_a, current_a * 460.0, "now", 6)
    quiet = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
    return Sample(t, quiet | {"L1": l1}, quiet, 0.0, soc_pct, {"egolf": car})


def one_after_the_other(
    *, tesla_until: int, complete: bool = True, egolf_from: int, end: int = 6000
) -> list[Sample]:
    """Der Tesla lädt mit 9 A bis ``tesla_until`` und ist danach „Complete“ (wenn ``complete``),
    der e-Golf lädt mit 8 A ab ``egolf_from``; ``end`` Sekunden."""
    quiet = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
    samples = []
    for t in range(end):
        tesla_a = 9.0 if t < tesla_until else 0.0
        egolf_a = 8.0 if t >= egolf_from else 0.0
        done = complete and t >= tesla_until
        cars = {
            "tesla": CarSample(tesla_a > 0, tesla_a, tesla_a * 690.0, "pv", 5, done),
            "egolf": CarSample(egolf_a > 0, egolf_a, egolf_a * 460.0, "pv", 6),
        }
        samples.append(Sample(t, quiet, quiet, 0.0, 60.0, cars))
    return samples


def test_the_cars_charge_one_after_the_other(cfg) -> None:
    sunny, cloudy = SCENARIOS["sonnig"].checks, SCENARIOS["wolkig"].checks
    # Tesla 50 min bis „Complete“ bei 3000 s, e-Golf ab 3150 s: 47,5 min
    ok = result_of(cfg, one_after_the_other(tesla_until=3000, egolf_from=3150))
    assert sunny(ok) == [] and cloudy(ok) == []
    early = result_of(cfg, one_after_the_other(tesla_until=3000, egolf_from=2900))
    assert sunny(early) == ["„egolf“ startet bei 2900 s, bevor „tesla“ „Complete“ erreicht."]
    short = result_of(cfg, one_after_the_other(tesla_until=2400, egolf_from=3400, end=5000))
    assert sunny(short) == [
        "„tesla“ lädt 40 min, verlangt 45 min.",
        "„egolf“ lädt danach 26,7 min, verlangt 45 min.",
    ]
    assert cloudy(short) == []  # je 20 min reichen
    never = result_of(cfg, one_after_the_other(tesla_until=1500, complete=False, egolf_from=6000))
    assert cloudy(never) == [
        "„tesla“ erreicht „Complete“ nicht.",
        "„egolf“ lädt danach 0 min, verlangt 20 min.",
    ]


def evcs_decision(seconds: int, kind: CommandKind, result: DecisionResult) -> Decision:
    value = 9.0 if kind is CommandKind.CURRENT else True
    return replace(tessie_decision(seconds, kind, value, result), actuator="wallbox.evcs")


def test_the_evcs_follows_the_sun_with_few_current_commands(cfg) -> None:  # Design 6.5
    sunny = SCENARIOS["sonnig"].checks
    # Der e-Golf lädt von 3150 bis 6000 s, 47,5 min: 7 Strombefehle sind 8,8 je Stunde.
    samples = one_after_the_other(tesla_until=3000, egolf_from=3150)
    seven = [evcs_decision(3150 + 60 * i, CommandKind.CURRENT, DecisionResult.OK) for i in range(7)]
    eighth = evcs_decision(5000, CommandKind.CURRENT, DecisionResult.OK)
    assert sunny(result_of(cfg, samples, seven)) == []
    assert sunny(result_of(cfg, samples, [*seven, eighth])) == [
        "8 Strombefehle an die EVCS in 47,5 min Laden von „egolf“: 10,1 je Stunde, verlangt "
        "höchstens 10."
    ]
    # Es zählen nur Strombefehle mit Wert an die EVCS.
    others = [
        evcs_decision(5000, CommandKind.CURRENT, DecisionResult.REJECTED),
        evcs_decision(5001, CommandKind.CURRENT, DecisionResult.DEFERRED),
        evcs_decision(5002, CommandKind.CHARGING, DecisionResult.OK),
        tessie_decision(5003, CommandKind.CURRENT, 9.0, DecisionResult.OK),
        # eine fremde Änderung ist kein Befehl der Regelung, auch wenn sie einen Wert nennt
        evcs_decision(5004, CommandKind.CURRENT, DecisionResult.FOREIGN),
    ]
    assert sunny(result_of(cfg, samples, [*seven, *others])) == []


def test_the_evcs_must_drop_during_the_peak(cfg) -> None:
    check = SCENARIOS["abend-sofort"].checks
    before = [egolf_at(1199, 15.0)]
    # die ersten 10 s darf sie noch 15 A ziehen
    dropped = before + [egolf_at(t, 15.0 if t < 1210 else 6.0) for t in range(1200, 1500)]
    assert check(result_of(cfg, dropped)) == []
    late = before + [egolf_at(t, 15.0 if t < 1211 else 6.0) for t in range(1200, 1500)]
    assert check(result_of(cfg, late)) == ["EVCS-Strom während der Spitze bis 15 A, davor 15 A."]
    assert check(result_of(cfg, before)) == ["Die Lastspitze liegt nicht im Lauf."]


def test_the_reserve_is_kept_and_named(cfg) -> None:
    check = SCENARIOS["reserve"].checks
    guard = replace(
        tessie_decision(0, CommandKind.CURRENT, 13.0, DecisionResult.OK),
        actuator="wallbox.evcs",
        codes=("mode_now", "reserve_guard"),
    )
    kept = [egolf_at(t, 13.0, soc_pct=23.0 - t / 100) for t in range(300)]
    assert check(result_of(cfg, kept, [guard])) == []
    low = [egolf_at(0, 13.0, soc_pct=20.5), egolf_at(1, 13.0, soc_pct=19.99876)]
    assert check(result_of(cfg, low)) == [
        "Keine Entscheidung mit „reserve_guard“.",
        "SOC bis 19,9988 %, unter der USV-Reserve von 20 %.",
    ]


def test_the_reserve_checks_name_the_regulation_and_the_model() -> None:
    checks = SCENARIOS["reserve"].checks
    assert isinstance(checks, Checks)
    assert [check.text for check in checks.items] == [
        "Eine Entscheidung nennt „reserve_guard“.",
        "Unter der Reserve-Absicherung liegt keine Phase länger als 10 s über dem Importziel "
        "(Last ohne Akku-Hilfe).",
        "Der SOC sinkt nie unter 20 % (Invariante des Modells).",
    ]


def test_the_reserve_guard_must_hold_the_import_target(cfg) -> None:
    # SOC 23 % liegt unter Reserve + Absicherung (25 %): Sofort richtet sich am Importziel aus
    # (19 A, gezählt ab 19,5 A). Mehr als 10 s am Stück darüber ist ein Fehler der Regelung.
    checks = SCENARIOS["reserve"].checks
    assert isinstance(checks, Checks)
    target = checks.items[1]

    def found(seconds_over: int) -> list[str]:
        samples = [
            egolf_at(t, 13.0, soc_pct=23.0, l1=20.0 if t < seconds_over else 18.0)
            for t in range(60)
        ]
        result = replace(result_of(cfg, samples), metrics=measure(samples, [], cfg))
        return target.find(result)

    assert found(10) == []
    assert found(11) == [
        "Unter der Reserve-Absicherung 11 s am Stück über dem Importziel, verlangt höchstens 10 s."
    ]


def test_the_multi_mode_must_warn(cfg) -> None:
    check = SCENARIOS["nur-laden"].checks

    def health(status: str) -> list[tuple[int, HealthReport]]:
        found = {"id": "multi_mode", "status": status, "message": "…"}
        report = HealthReport(
            mode="CONTROL",
            version="0",
            adapters=[],
            selfcheck=[found],
            alarms=[],
            cycle_ms_p95=None,
        )
        return [(0, report)]

    assert check(result_of(cfg, [], health=health("warn"))) == []
    assert check(result_of(cfg, [], health=health("ok"))) == [
        "Die Selbstprüfung „multi_mode“ warnt nicht."
    ]
