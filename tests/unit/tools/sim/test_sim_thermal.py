"""Tests der Klimaanlage im Simulator (Plan 6a, Task 12): Modell an der UVR, Port und Uhr des
CoE-Adapters im Prozess und die Prüfungen der Szenarien „klima-*“ an Hand gebauter Ergebnisse.
Die ganzen Läufe prüft ``tests/integration/test_sim_scenarios.py``.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Final

import pytest
from tests.unit.tools.sim.conftest import T0, at

from bindaems.shared.coe import decode, offset_packet
from bindaems.shared.config import CoeOffsetConfig, Config
from bindaems.shared.control import CommandKind
from bindaems.shared.decisions import Decision, DecisionResult
from bindaems.shared.timeutil import ManualClock
from bindaems.tools.sim.metrics import CarSample, Sample, ThermalSample, measure
from bindaems.tools.sim.model import (
    AC_GAIN_W_PER_K,
    CMI_EVERY_S,
    UVR_TIMEOUT_S,
    AirConditioner,
    PlantModel,
)
from bindaems.tools.sim.runner import SimResult, SimTimer, _open_coe
from bindaems.tools.sim.scenarios import (
    KLIMA,
    KLIMA_ACTUATOR,
    SCENARIOS,
    Check,
    Checks,
)

QUIET: Final = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
CAR: Final = CarSample(False, 0.0, 0.0, "pv", 6)


def device(cfg: Config) -> CoeOffsetConfig:
    found = cfg.thermal.devices[KLIMA]
    assert isinstance(found, CoeOffsetConfig)
    return found


def unit_of(cfg: Config, mode: str = "kuehlen", base_w: float = 600.0) -> AirConditioner:
    unit = AirConditioner(KLIMA, device(cfg))
    unit.mode, unit.base_w, unit.power_w = mode, base_w, base_w  # type: ignore[assignment]
    unit.step(T0, 0.0)
    return unit


def offset(cfg: Config, value_k: float) -> bytes:
    found = device(cfg)
    return offset_packet(value_k, node=found.own_node, output=found.own_output)


def cmi(cfg: Config) -> tuple[str, int]:
    found = device(cfg)
    return (str(found.cmi_host), found.port)


# --- das Modell ------------------------------------------------------------------------------


def test_the_model_takes_the_air_conditioner_from_the_config(model: PlantModel) -> None:
    assert model.thermal is not None
    assert model.thermal.name == KLIMA
    assert model.thermal.mode == "aus"
    assert model.thermal_w == 0.0


def test_an_offset_takes_effect_at_once(cfg: Config) -> None:
    unit = unit_of(cfg)
    unit.receive(offset(cfg, -1.0), cmi(cfg))
    assert unit.active_k == -1.0
    assert unit.received == [(T0, -1.0)]


def test_packets_from_elsewhere_or_to_another_output_change_nothing(cfg: Config) -> None:
    unit = unit_of(cfg)
    unit.receive(offset(cfg, -1.0), ("192.168.81.1", cmi(cfg)[1]))
    found = device(cfg)
    unit.receive(offset_packet(-1.0, node=found.own_node, output=found.own_output + 1), cmi(cfg))
    assert unit.active_k == 0.0
    assert unit.received == []


def test_receiving_before_the_first_step_is_an_error(cfg: Config) -> None:
    unit = AirConditioner(KLIMA, device(cfg))
    with pytest.raises(RuntimeError, match=r"PlantModel\.step fehlt"):
        unit.receive(offset(cfg, -0.5), cmi(cfg))


@pytest.mark.parametrize(
    ("mode", "offset_k", "target_w"),
    [
        ("kuehlen", -1.0, 600.0 + AC_GAIN_W_PER_K),  # mehr Kühlen
        ("kuehlen", 1.0, 600.0),  # weniger Kühlen ändert die Leistung nicht
        ("heizen", 1.5, 600.0 + 1.5 * AC_GAIN_W_PER_K),
        ("lueften", -1.0, 600.0),
        ("aus", -1.0, 0.0),
    ],
)
def test_the_power_follows_the_offset_towards_more(
    cfg: Config, mode: str, offset_k: float, target_w: float
) -> None:
    unit = unit_of(cfg, mode)
    for second in range(1, 3601):
        if second % 60 == 1:  # der core sendet wieder, vor dem Timeout der UVR
            unit.receive(offset(cfg, offset_k), cmi(cfg))
        unit.step(at(second), 1.0)
    assert unit.power_w == pytest.approx(target_w, abs=1.0)


def test_the_uvr_falls_back_to_zero_after_its_timeout(cfg: Config) -> None:
    unit = unit_of(cfg)
    unit.receive(offset(cfg, -0.5), cmi(cfg))
    unit.step(at(UVR_TIMEOUT_S - 1), 1.0)
    assert unit.active_k == -0.5
    unit.step(at(UVR_TIMEOUT_S), 1.0)
    assert unit.active_k == 0.0
    assert unit.received == [(T0, -0.5)]  # das Zurückfallen ist kein empfangener Versatz


def test_the_cmi_sends_every_ten_seconds_and_at_once_after_a_change(cfg: Config) -> None:
    unit = unit_of(cfg)
    got: list[tuple[float, bytes]] = []
    unit.connect(lambda data, addr: got.append(((unit._now - T0).total_seconds(), data)))
    for second in range(0, 25):
        if second == 13:
            unit.receive(offset(cfg, -0.5), cmi(cfg))
        if second == 17:
            unit.mode = "heizen"
        unit.step(at(second), 1.0)
        unit.publish(at(second))
    packets = len(unit.packets())
    times = sorted({t for t, _ in got})
    assert times == [0.0, CMI_EVERY_S, 13.0, 17.0]  # danach erst wieder bei 27 s
    assert len(got) == len(times) * packets


def test_the_cmi_sends_the_receive_table(cfg: Config) -> None:
    unit = unit_of(cfg)
    unit.receive(offset(cfg, -0.5), cmi(cfg))
    values = [value for packet in unit.packets() for value in decode(packet)]
    channels = device(cfg).receive.channels()
    assert len(values) == len(channels)
    raw = {(v.node, v.output, v.analog): v.raw for v in values}
    active = channels["offset_active_k"]
    assert raw[(active.node, active.analog, True)] == -5  # Zehntel K


def test_a_silent_or_disconnected_cmi_sends_nothing(cfg: Config) -> None:
    unit = unit_of(cfg)
    got: list[bytes] = []
    unit.publish(T0)  # nicht verbunden
    unit.connect(lambda data, addr: got.append(data))
    unit.silent = True
    unit.publish(T0)
    assert got == []
    unit.silent = False
    unit.publish(T0)
    assert got
    unit.disconnect()
    got.clear()
    unit.publish(at(CMI_EVERY_S))
    assert got == []


def test_the_house_carries_the_air_conditioner(model: PlantModel) -> None:
    assert model.thermal is not None
    model.thermal.mode, model.thermal.power_w = "kuehlen", 900.0
    assert model.thermal_w == 900.0


# --- Port und Uhr des Adapters im Prozess ------------------------------------------------------


async def test_the_port_sends_to_the_uvr_and_closing_disconnects(cfg: Config) -> None:
    unit = unit_of(cfg)
    got: list[bytes] = []
    port = await _open_coe(unit, lambda data, addr: got.append(data), device(cfg).port)
    port.sendto(offset(cfg, -1.5), cmi(cfg))
    assert unit.active_k == -1.5
    unit.publish(T0)
    assert got
    port.close()
    got.clear()
    unit.publish(at(CMI_EVERY_S))
    assert got == []


async def test_the_timer_sleeps_in_simulated_time() -> None:
    clock = ManualClock(T0)
    timer = SimTimer(clock, T0)
    woke: list[float] = []

    async def nap(seconds: float) -> None:
        await timer.sleep(seconds)
        woke.append(timer.monotonic())

    tasks = [asyncio.create_task(nap(s)) for s in (10.0, 5.0)]
    await asyncio.sleep(0)
    clock.advance(timedelta(seconds=4))
    timer.wake()
    await asyncio.sleep(0)
    assert woke == []
    clock.advance(timedelta(seconds=6))
    timer.wake()
    await asyncio.gather(*tasks)
    assert woke == [10.0, 10.0]


async def test_a_cancelled_sleep_is_forgotten() -> None:
    clock = ManualClock(T0)
    timer = SimTimer(clock, T0)
    task = asyncio.create_task(timer.sleep(1.0))
    await asyncio.sleep(0)
    task.cancel()
    clock.advance(timedelta(seconds=2))
    timer.wake()  # setzt kein Ergebnis auf die abgebrochene Pause
    with pytest.raises(asyncio.CancelledError):
        await task


# --- die Prüfungen der Szenarien ---------------------------------------------------------------


def thermal(
    mode: str = "kuehlen",
    received: tuple[float, ...] = (),
    *,
    active_k: float = 0.0,
    wish: int | None = None,
    reason: str | None = None,
    pressure: bool = False,
) -> ThermalSample:
    return ThermalSample(mode, 600.0, active_k, received, False, wish, reason, pressure)


def sample(t: int, unit: ThermalSample | None, *, grid_w: float = 0.0) -> Sample:
    return Sample(t, QUIET, QUIET, 0.0, 100.0, {"egolf": CAR}, grid_w=grid_w, thermal=unit)


def run(
    cfg: Config,
    name: str,
    found: dict[int, ThermalSample],
    duration_s: int,
    *,
    decisions: list[Decision] | None = None,
    grid_w: float = 0.0,
) -> SimResult:
    """Ein Ergebnis über ``duration_s`` s: ``found`` je Sekunde die Klimaanlage, dazwischen die
    der Sekunde davor ohne Pakete."""
    samples, last = [], thermal()
    for t in range(duration_s):
        unit = found.get(t)
        if unit is None:
            unit = ThermalSample(last.mode, 600.0, last.active_k, (), False, last.wish, last.reason)
        last = unit
        samples.append(sample(t, unit, grid_w=grid_w))
    decisions = decisions or []
    return SimResult(
        SCENARIOS[name],
        measure(samples, decisions, cfg),
        decisions,
        [],
        samples,
        [],
        usable_kwh=cfg.battery.usable_kwh,
    )


def decision(name: str, seconds: int, value: float, *codes: str) -> Decision:
    return Decision(
        id=f"{seconds}",
        ts=SCENARIOS[name].start + timedelta(seconds=seconds),
        actuator=KLIMA_ACTUATOR,
        kind=CommandKind.SETPOINT_OFFSET,
        requested=value,
        applied=value,
        dry_run=False,
        outcome="accepted",
        result=DecisionResult.OK,
        codes=codes,
        text="",
        inputs={},
    )


def check(name: str, text: str) -> Check:
    checks = SCENARIOS[name].checks
    assert isinstance(checks, Checks)
    return next(c for c in checks.items if text in c.text)


def steps(*changes: tuple[int, float], mode: str = "kuehlen") -> dict[int, ThermalSample]:
    return {t: thermal(mode, (k,), active_k=k) for t, k in changes}


def test_every_klima_check_has_a_german_sentence() -> None:
    for name in SCENARIOS:
        if not name.startswith("klima-"):
            continue
        checks = SCENARIOS[name].checks
        assert isinstance(checks, Checks)
        assert checks.items
        assert all(c.text.endswith(".") for c in checks.items)


def test_steps_pass_at_600_s_and_fail_closer_or_larger(cfg: Config) -> None:
    name = "klima-pv-kuehlen"
    rule = check(name, "Schritten von −0,5 K")
    good = run(cfg, name, steps((660, -0.5), (1260, -1.0), (1860, -1.5)), 2000)
    assert rule.find(good) == []
    close = run(cfg, name, steps((660, -0.5), (1200, -1.0), (1860, -1.5)), 2000)
    assert rule.find(close) == [
        "Die UVR bekam bei 1200 s einen Schritt nur 540 s nach dem vorigen, verlangt sind "
        "mindestens 600 s."
    ]
    jump = run(cfg, name, steps((660, -1.0), (1260, -1.5), (1860, -2.0)), 2000)
    assert rule.find(jump) == [
        "Die UVR bekam bei 660 s −1 K nach 0 K, verlangt ist ein Schritt von −0,5 K."
    ]
    few = run(cfg, name, steps((660, -0.5)), 2000)
    assert rule.find(few) == ["Die UVR bekam 1 Schritte, verlangt sind mindestens 3."]


def test_teuer_heizen_allows_exactly_two_steps(cfg: Config) -> None:
    name = "klima-teuer-heizen"
    rule = check(name, "2 Schritte")
    three = run(cfg, name, steps((660, -0.5), (1260, -1.0), (1860, -1.5), mode="heizen"), 2000)
    assert rule.find(three) == ["Die UVR bekam 3 Schritte, verlangt sind 2."]
    beyond = check(name, "zwischen −1 K und 0 K")
    assert beyond.find(three) == [
        "Die UVR bekam bei 1860 s −1,5 K (1 Pakete außerhalb, bis −1,5 K)."
    ]
    reason = check(name, "thermal_less")
    wrong = run(cfg, name, {}, 10, decisions=[decision(name, 5, -0.5, "thermal_more")])
    assert reason.find(wrong) == [
        "Die Entscheidung bei 5 s nennt thermal_more, verlangt thermal_less."
    ]


def test_a_wrong_sign_reaches_the_uvr(cfg: Config) -> None:
    name = "klima-moduswechsel"
    rule = check(name, "Richtung „mehr“")
    found = {10: thermal("heizen", (-1.5,), active_k=-1.5)}
    assert rule.find(run(cfg, name, found, 20)) == [
        "Die UVR bekam bei 10 s −1,5 K beim Heizen (1 solche Pakete)."
    ]
    assert rule.find(run(cfg, name, {10: thermal("aus", (0.5,), active_k=0.5)}, 20)) != []


def test_moduswechsel_wants_zero_in_the_switch_second(cfg: Config) -> None:
    name = "klima-moduswechsel"
    rule = check(name, "Wechsel auf Heizen")
    good = {2115: thermal("heizen", (-1.5, 0.0), wish=0, reason="thermal_mode")}
    assert rule.find(run(cfg, name, good, 2200)) == []
    late = {2116: thermal("heizen", (0.0,), wish=0, reason="thermal_mode")}
    assert rule.find(run(cfg, name, late, 2200)) == [
        "Die UVR bekam bei 2115 s (Wechsel auf Heizen) nichts, verlangt 0 K.",
        "Die Regelung hatte bei 2115 s keinen Wunsch, verlangt 0 mit thermal_mode.",
    ]
    unwished = {2115: thermal("heizen", (0.0,), wish=3, reason="thermal_more")}
    assert rule.find(run(cfg, name, unwished, 2200)) == [
        "Die Regelung hatte bei 2115 s 3 Stufen mit thermal_more, verlangt 0 mit thermal_mode."
    ]
    signed = check(name, "keinen Versatz über 0")
    decisions = [decision(name, 2000, 0.5, "thermal_more"), decision(name, 2200, -0.5, "x")]
    assert signed.find(run(cfg, name, {}, 10, decisions=decisions)) == [
        "Die Regelung verlangte bei 2000 s +0,5 K (thermal_more).",
        "Die Regelung verlangte bei 2200 s −0,5 K (x).",
    ]


def test_paketverlust_wants_zero_180_s_after_the_last_packet(cfg: Config) -> None:
    name = "klima-paketverlust"
    rule = check(name, "nach dem letzten Paket")
    stale = {1670: thermal(received=(0.0,), wish=0, reason="thermal_stale")}
    assert (
        rule.find(run(cfg, name, {1400: thermal(received=(-1.0,), active_k=-1.0), **stale}, 2100))
        == []
    )
    early = {1600: thermal(received=(0.0,))}
    assert rule.find(run(cfg, name, early, 2100)) == [
        "Die UVR bekam die erste 0 nach dem Schweigen bei 1600 s, verlangt zwischen 1670 und "
        "1690 s."
    ]
    assert rule.find(run(cfg, name, {}, 2100)) == [
        "Die UVR bekam nach dem Schweigen keine 0, verlangt zwischen 1670 und 1690 s."
    ]
    only = check(name, "nur 0")
    again = {**stale, 1800: thermal(received=(-0.5,))}
    assert only.find(run(cfg, name, again, 2100)) == [
        "Die UVR bekam nach erste 0 nach dem Schweigen bei 1800 s −0,5 K."
    ]
    assert only.find(run(cfg, name, {}, 2100)) == [
        "Den Zeitpunkt „erste 0 nach dem Schweigen“ gab es nicht."
    ]
    zero = check(name, "im selben Zyklus")
    assert zero.find(run(cfg, name, {}, 2100)) == [
        "Den Zeitpunkt „erste 0 nach dem Schweigen“ gab es nicht."
    ]
    reached = check(name, "Vor 25 min")
    assert reached.find(run(cfg, name, {}, 2100)) == ["Die UVR bekam vor 1500 s nie −1 K."]


def test_spitze_wants_zero_when_the_guard_presses(cfg: Config) -> None:
    name = "klima-spitze"
    rule = check(name, "Druck des 15-min-Wächters“ bekommt")
    good = {1821: thermal("heizen", (0.0,), wish=0, reason="thermal_peak", pressure=True)}
    assert rule.find(run(cfg, name, good, 2400)) == []
    slow = {
        1821: thermal("heizen", (1.0,), active_k=1.0, wish=2, reason="thermal_more", pressure=True)
    }
    assert rule.find(run(cfg, name, slow, 2400)) == [
        "Die UVR bekam bei 1821 s (Druck des 15-min-Wächters) +1 K, verlangt 0 K.",
        "Die Regelung hatte bei 1821 s 2 Stufen mit thermal_more, verlangt 0 mit thermal_peak.",
    ]


def test_auto_vorrang_backs_off_only_for_the_cars(cfg: Config) -> None:
    name = "klima-auto-vorrang"
    rule = check(name, "nur wegen der Autos")
    found = steps((1860, -1.5), (2401, -1.0), (2521, -1.5), (2522, -1.0), (3122, 0.0))
    decisions = [
        decision(name, 1861, -1.5, "thermal_more"),
        decision(name, 2402, -1.0, "thermal_cars"),
        decision(name, 2522, -1.5, "thermal_more"),
        decision(name, 2523, -1.0, "thermal_off"),
    ]
    assert rule.find(run(cfg, name, found, 3200, decisions=decisions)) == [
        "Die UVR bekam bei 2521 s −1,5 K nach −1 K: Der Versatz wurde größer, obwohl ein Auto "
        "Vorrang hat.",
        "Die UVR bekam bei 3122 s 0 K nach −1 K, verlangt ein Schritt von +0,5 K zurück.",
        "Die Entscheidung bei 2523 s auf −1 K nennt thermal_off, verlangt thermal_cars.",
    ]
    end = check(name, "am Ende 0 K")
    assert end.find(run(cfg, name, found, 3200)) == []
    assert end.find(run(cfg, name, steps((10, -0.5)), 20)) == [
        "Die UVR wendet am Ende −0,5 K an, verlangt 0 K."
    ]


def test_the_export_is_averaged_over_the_last_ten_minutes(cfg: Config) -> None:
    name = "klima-pv-kuehlen"
    rule = check(name, "Einspeisung")
    assert rule.find(run(cfg, name, {}, 700, grid_w=-700.0)) == []
    assert rule.find(run(cfg, name, {}, 700, grid_w=-900.0)) == [
        "Die Einspeisung lag in den letzten 10 min im Mittel bei 900 W, verlangt unter 800 W."
    ]


def test_checks_without_the_air_conditioner_find_it_missing(cfg: Config) -> None:
    name = "klima-auto-vorrang"
    samples = [sample(t, None) for t in range(5)]
    result = SimResult(
        SCENARIOS[name],
        measure(samples, [], cfg),
        [],
        [],
        samples,
        [],
        usable_kwh=cfg.battery.usable_kwh,
    )
    assert check(name, "am Ende 0 K").find(result) == [
        "Die UVR wendet am Ende keinen Versatz an, verlangt 0 K."
    ]
