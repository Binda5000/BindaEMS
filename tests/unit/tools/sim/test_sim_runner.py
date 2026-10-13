"""Tests des Laufs und der Szenarien des Simulators: Ablauf je Sekunde, Sendezeiten, Wetter.

Die Läufe sind kurz; die ganzen Szenarien prüft ``tests/integration/test_sim_scenarios.py``.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Final

import pytest

from bindaems.core.api.app import HealthReport
from bindaems.shared.config import Config
from bindaems.shared.control import ActuatorMode, ChargeMode, CommandKind, Intent, VehicleIntent
from bindaems.shared.decisions import Decision, DecisionResult
from bindaems.shared.timeutil import LOCAL_TZ
from bindaems.tools.sim import runner
from bindaems.tools.sim.metrics import CarSample, OwnValue, Sample, measure
from bindaems.tools.sim.model import PlantModel
from bindaems.tools.sim.runner import SimResult, run_scenario, sent_times
from bindaems.tools.sim.scenarios import (
    CLOUD_FACTORS,
    HOUSE_NOISE_W,
    HOUSE_W,
    SCENARIOS,
    SUN_PEAK_W,
    Scenario,
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
    assert len({d.ts for d in result.decisions}) == len(result.decisions)  # Gruppen je Zyklus


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

    assert await steered(crossed, live=True) == {"tesla": True, "egolf": False}
    assert await steered(usual, live=True) == {"tesla": True, "egolf": False}
    assert await steered(crossed, live=False) == {"tesla": False, "egolf": False}
    assert await steered(usual, live=False) == {"tesla": False, "egolf": False}
    # nur die EVCS live: Der Tesla an ihr ist gesteuert, am Wall Connector nicht
    assert await steered(crossed, live=False, evcs_only=True) == {"tesla": True, "egolf": False}
    assert await steered(usual, live=False, evcs_only=True) == {"tesla": False, "egolf": False}


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
    for live, dry_run in ((True, False), (False, True)):
        scenario = replace(SCENARIOS["reserve"], setup=at_13_a, live=live)
        data_dir = tmp_path / str(live)
        data_dir.mkdir()
        result = await run_scenario(scenario, cfg, data_dir, duration_s=70)
        assert seen.pop() == [
            OwnValue(*current, dry_run=False),
            OwnValue(*charging, dry_run=dry_run),
        ]
        assert [(d.kind, d.requested) for d in result.decisions] == [charging[1:]]


# --- Prüfungen der Szenarien ----------------------------------------------------------------------


def result_of(
    cfg: Config,
    samples: list[Sample],
    decisions: list[Decision] | None = None,
    health: list[tuple[int, HealthReport]] | None = None,
) -> SimResult:
    metrics = measure([], [], cfg)
    return SimResult(SCENARIOS["reserve"], metrics, decisions or [], health or [], samples, [])


def egolf_at(t: int, current_a: float, *, soc_pct: float = 60.0) -> Sample:
    """Der e-Golf mit ``current_a`` nach Sekunde ``t``, sonst ruhig."""
    car = CarSample(current_a > 0, current_a, current_a * 460.0, "now", 6)
    quiet = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
    return Sample(t, quiet, quiet, 0.0, soc_pct, {"egolf": car})


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
