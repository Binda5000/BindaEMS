"""Szenarien des Simulators in der CI (Design 13.2): die echte Laufzeit gegen das Anlagenmodell.

Jedes Szenario besteht, wenn alle Kennzahlen in ihren Grenzen liegen und seine eigenen Prüfungen
nichts finden; ``failures`` nennt sonst jede Verletzung als Satz. Dazu kommt eine Abwandlung von
„spitze-15min“ für den Boost-Anteil des 15-min-Wächters (Plan 2c-2a, Präzisierung 9).

Bekannte Befunde der Regelung stehen in ``KNOWN_FAULTS``, mit ihren genauen Sätzen: Ist ein Befund
behoben, schlägt der Test fehl, und der Eintrag fällt weg. Zwei Abwandlungen von
„klima-auto-vorrang“ halten fest, wie sich die Klimaanlage neben einem Auto verhält, das nie
starten kann oder sich selbst begrenzt (Ruling R23).
"""

from dataclasses import dataclass, replace
from datetime import timedelta
from functools import partial
from itertools import pairwise

import pytest

from bindaems.core.adapters.coe import CoeAdapter
from bindaems.shared.control import ChargeMode, CommandKind, Intent, VehicleIntent, wallbox_actuator
from bindaems.tools.sim.model import AirConditioner, PlantModel
from bindaems.tools.sim.runner import SimResult, run_scenario
from bindaems.tools.sim.scenarios import (
    CAR_EXPORT_W,
    CAR_PLUG_S,
    EGOLF,
    EVCS_BOX,
    KLIMA_ACTUATOR,
    KLIMA_BASE_W,
    KLIMA_EXPORT_W,
    KLIMA_HEAT_W,
    KLIMA_MORE_W,
    KLIMA_NOISE_W,
    KLIMA_SETTLED_S,
    KLIMA_STEP_K,
    PEAK_LEAD_S,
    QUARTER_S,
    SCENARIOS,
    TESLA,
    WINTER_NOON,
    Checks,
    _changes,
    _klima_pv_w,
    _klima_setup,
    _Plug,
    _receipts,
    _setup,
    _thermal_samples,
    _weather,
)

NAMES = [
    "sonnig",
    "wolkig",
    "abend-sofort",
    "reserve",
    "nur-laden",
    "tesla-5a",
    "tesla-schlaeft",
    "ausfaelle",
    "zuordnung",
    "dry-run",
]
# die Szenarien für Akku und Spitzen (Plan 2c-2a, Design 10.2), mit Victron live
NEW = [
    "akku-wolkig",
    "akku-minpv",
    "akku-sofort",
    "boost-abend",
    "override-stunde",
    "dess-an",
    "spitze-15min",
    "schieflast-pv",
]
# die Szenarien für die Zuordnung (Plan 2c-2b, Design 10.3), mit Victron im Dry-Run
ASSIGNMENT = ["tesla-evcs", "egolf-twc", "gast-evcs"]
# die Szenarien für „Aus“ und Selbststart des Tesla (Plan 2d, Task 6), mit Victron im Dry-Run
TESLA_OFF = ["tesla-aus", "tesla-selbststart"]
# die Szenarien zur Planausführung (Plan 3c, Entscheidung 13), mit Victron live
PLAN = ["plan-halten", "plan-netzladen", "plan-abgelaufen", "plan-budget", "plan-boost"]
# die Szenarien für „Geplant“ und das Ladelimit des Tesla (Plan 4b, Entscheidung 16), Victron live
PLANNED = ["geplant-plan", "geplant-spaet", "geplant-pv", "geplant-unbekannt", "tesla-ladelimit"]
# das Szenario für den Höchststrom in „Sofort“ (Plan Sofort-Limit, Task 3), Victron im Dry-Run
SOFORT = ["sofort-limit"]
# die Szenarien für die Klimaanlage (Plan 6a, Entscheidung 15), am CoE-Adapter und live
KLIMA = [
    "klima-pv-kuehlen",
    "klima-auto-vorrang",
    "klima-moduswechsel",
    "klima-paketverlust",
    "klima-spitze",
    "klima-teuer-heizen",
]
ALL = [*NAMES, *NEW, *ASSIGNMENT, *TESLA_OFF, *PLAN, *PLANNED, *SOFORT, *KLIMA]
# Befunde der Regelung, die ein Szenario aufdeckt und die noch offen sind, mit ihren Sätzen. Der
# Befund aus „klima-auto-vorrang“ (Schritt der Klimaanlage beim Start des e-Golf) ist mit dem
# Anlauf behoben (Ruling R23 a).
KNOWN_FAULTS: dict[str, list[str]] = {}


def test_all_scenarios_are_listed() -> None:
    assert list(SCENARIOS) == ALL


def test_battery_and_peak_scenarios_are_registered() -> None:
    assert list(SCENARIOS)[10:18] == NEW
    assert all(SCENARIOS[name].victron_live for name in NEW)
    assert not any(SCENARIOS[name].victron_live for name in list(SCENARIOS)[:10])


def test_assignment_scenarios_are_registered() -> None:
    assert list(SCENARIOS)[18:21] == ["tesla-evcs", "egolf-twc", "gast-evcs"]
    assert len(SCENARIOS) == 40


def test_tesla_off_scenarios_are_registered() -> None:
    assert list(SCENARIOS)[21:23] == TESLA_OFF
    assert not any(SCENARIOS[name].victron_live for name in TESLA_OFF)


def test_plan_scenarios_are_registered() -> None:
    assert list(SCENARIOS)[23:28] == PLAN
    assert all(SCENARIOS[name].victron_live for name in PLAN)


def test_planned_scenarios_are_registered() -> None:
    assert list(SCENARIOS)[28:33] == PLANNED
    assert all(SCENARIOS[name].victron_live for name in PLANNED)


def test_sofort_scenario_is_registered() -> None:
    assert list(SCENARIOS)[33:34] == SOFORT
    assert SCENARIOS["sofort-limit"].intent.vehicles["egolf"].now_max_a == 8


def test_klima_scenarios_are_registered() -> None:
    assert list(SCENARIOS)[-6:] == KLIMA
    assert all(SCENARIOS[name].thermal and SCENARIOS[name].live for name in KLIMA)
    assert not any(SCENARIOS[name].thermal for name in list(SCENARIOS)[:-6])


@pytest.mark.parametrize("name", ALL)
async def test_scenario(name, cfg, tmp_path) -> None:
    result = await run_scenario(SCENARIOS[name], cfg, tmp_path)
    assert result.failures == KNOWN_FAULTS.get(name, [])


async def test_boost_does_not_hide_the_grid_of_another_car(cfg, tmp_path) -> None:
    # Plan 2c-2a, Präzisierung 9: wie „spitze-15min“ (ohne PV, Akku 60 %, Victron live), aber mit
    # dem Ziel 5 kW; der Tesla lädt am Wall Connector von Anfang an in Boost, der e-Golf an der EVCS
    # in Sofort. Der Akku-Sollwert lässt Boost aus dem Akku laden und den e-Golf aus dem Netz. Der
    # Wächter zieht nur den Netzanteil des Boost ab: Er sieht den Netzbezug des e-Golf, drosselt ihn
    # und hält die Viertelstunde ab 18:00 beim Ziel (5 kW · 0,25 h, 1 % Toleranz wie in
    # „spitze-15min“). Verbuchte er den Netzbezug des e-Golf als Boost, läge sie still darüber.
    base = SCENARIOS["spitze-15min"]
    duration_s = PEAK_LEAD_S + QUARTER_S
    boost = VehicleIntent(boost_until=base.start + timedelta(seconds=duration_s))
    vehicles = {TESLA: boost, EGOLF: VehicleIntent(mode=ChargeMode.NOW)}
    scenario = replace(
        base,
        setup=partial(_setup, soc_pct=60.0),
        intent=Intent(version=1, control=True, vehicles=vehicles),
        events=_weather(base.start, duration_s, base.seed),
        checks=Checks(()),
        control={"peak_target_kw": 5.0},
    )
    result = await run_scenario(scenario, cfg, tmp_path, duration_s=duration_s)
    quarter = [s for s in result.samples if s.t >= PEAK_LEAD_S]
    assert len(quarter) == QUARTER_S
    assert sum(max(0.0, s.grid_w) for s in quarter) / 3_600_000 <= 5.0 * 0.25 * 1.01
    throttled = [d for d in result.decisions if "peak_guard" in d.codes]
    assert {d.actuator for d in throttled} >= {"wallbox.evcs"}  # der e-Golf, nie der Tesla
    assert "vehicle.tesla" not in {d.actuator for d in throttled}


@dataclass(frozen=True)
class _MaxCurrent:
    """Das Auto ``vehicle`` nimmt ab hier höchstens ``amps`` je Phase (es regelt ab), als
    Ereignis."""

    vehicle: str
    amps: float

    def __call__(self, model: PlantModel, rt: object) -> None:
        model.cars[self.vehicle].max_a = self.amps


def _klima_codes(result: SimResult) -> list[tuple[float, str]]:
    return [(d.requested, d.codes[0]) for d in result.decisions if d.actuator == KLIMA_ACTUATOR]


def _export_w(result: SimResult) -> float:
    last = result.samples[-KLIMA_SETTLED_S:]
    return -sum(s.grid_w for s in last) / len(last)


async def test_klima_takes_the_surplus_of_a_car_that_can_never_start(cfg, tmp_path) -> None:
    # Ruling R23 c: wie „klima-auto-vorrang“, aber mit 1,5 kW Einspeisung ohne Versatz. Der e-Golf
    # braucht für den Start 6 A auf zwei Phasen (2760 W); die Regelung hält ihn bei „Laden aus“.
    # Einspeisung und Klimaanlage (600 W ohne Versatz) erreichen das nie: Er zählt nicht, und die
    # Klimaanlage nimmt den Überschuss weiter, ohne zurückzugehen. In „klima-auto-vorrang“ (4 kW)
    # reicht es, und sie weicht dem Auto (dessen Prüfungen).
    base = SCENARIOS["klima-auto-vorrang"]
    pv = _klima_pv_w(KLIMA_EXPORT_W, KLIMA_BASE_W)
    scenario = replace(
        base,
        events=(
            *_weather(
                base.start, base.duration_s, base.seed, pv=lambda t: pv, noise_w=KLIMA_NOISE_W
            ),
            (CAR_PLUG_S, _Plug(EGOLF, EVCS_BOX)),
        ),
        checks=Checks(()),
    )
    result = await run_scenario(scenario, cfg, tmp_path)
    assert result.failures == []
    assert _klima_codes(result) == [
        (0.0, "thermal_off"),  # die 0 zu Beginn geht immer hinaus (Ruling R26)
        (-0.5, "thermal_more"),
        (-1.0, "thermal_more"),
        (-1.5, "thermal_more"),
        (-2.0, "thermal_more"),
    ]
    assert not any(s.cars[EGOLF].charging for s in result.samples)
    assert _export_w(result) < KLIMA_MORE_W


async def test_klima_self_limiting_car_does_not_let_the_offset_flicker(cfg, tmp_path) -> None:
    # Ruling R23 b: wie „klima-auto-vorrang“; nach 65 min nimmt der e-Golf nur noch 6 A (er regelt
    # ab), 60 min lang. Bei 8 A zieht er weniger als 7 A: Nach der Haltezeit begrenzt er sich
    # selbst, und die Klimaanlage nimmt den Überschuss. Senkt die Laderegelung darauf auf 7 A,
    # bleibt er selbst begrenzt, denn er zieht nicht mehr als zuvor. Die Klimaanlage geht nur
    # weiter in Richtung mehr, bis die Einspeisung unter der Schwelle liegt; sie flackert nicht.
    base = SCENARIOS["klima-auto-vorrang"]
    taper_s, duration_s = 65 * 60, 125 * 60
    pv = _klima_pv_w(CAR_EXPORT_W, KLIMA_BASE_W)
    scenario = replace(
        base,
        duration_s=duration_s,
        events=(
            *_weather(base.start, duration_s, base.seed, pv=lambda t: pv, noise_w=KLIMA_NOISE_W),
            (CAR_PLUG_S, _Plug(EGOLF, EVCS_BOX)),
            (taper_s, _MaxCurrent(EGOLF, 6.0)),
        ),
        checks=Checks(()),
    )
    result = await run_scenario(scenario, cfg, tmp_path)
    assert result.failures == []
    after = [(t, old, new) for t, old, new in _changes(result) if t >= taper_s]
    assert len(after) >= 2  # die Klimaanlage nimmt den Überschuss
    assert all(new < old for _, old, new in after)  # nur Richtung mehr, nie zurück
    taper = base.start + timedelta(seconds=taper_s)
    codes = {d.codes[0] for d in result.decisions if d.actuator == KLIMA_ACTUATOR and d.ts >= taper}
    assert codes == {"thermal_more"}
    evcs = wallbox_actuator(EVCS_BOX)
    currents = [
        d.requested
        for d in result.decisions
        if d.actuator == evcs and d.kind is CommandKind.CURRENT and d.ts >= taper
    ]
    assert len(currents) <= 2  # kein Wechsel im Gleichtakt mit der Klimaanlage
    assert _export_w(result) < KLIMA_MORE_W
    assert result.samples[-1].cars[EGOLF].current_a == 6.0


def _unit(model: PlantModel) -> AirConditioner:
    assert model.thermal is not None
    return model.thermal


@dataclass(frozen=True)
class _Deaf:
    """Ab hier erreichen die Pakete des core die UVR nicht (``deaf``) oder wieder, als Ereignis;
    das C.M.I. sendet weiter."""

    deaf: bool

    def __call__(self, model: PlantModel, rt: object) -> None:
        _unit(model).deaf = self.deaf


@dataclass(frozen=True)
class _Held:
    """Merkt den Versatz, den der CoE-Adapter gerade hält, als Ereignis (``held``)."""

    held: list[float]

    def __call__(self, model: PlantModel, rt: object) -> None:
        adapters = getattr(rt, "adapters", [])
        self.held.extend(a.held_k for a in adapters if isinstance(a, CoeAdapter))


DEAF_S, HEARD_S = 32 * 60, 47 * 60  # Pakete des core erreichen die UVR nicht, dann wieder


async def test_klima_one_way_loss_does_not_keep_an_old_offset(cfg, tmp_path) -> None:
    # Abschlussreview I2, Ruling R26: wie „klima-pv-kuehlen“; ab 32 min erreichen die Pakete des
    # core die UVR nicht mehr, die des C.M.I. kommen weiter (die Quelle bleibt verbunden). Nach
    # 5 min fällt die UVR selbst auf 0, ihr Echo zeigt 0, und die Regelung beginnt neu bei 0. Der
    # Adapter darf den alten Versatz (−1,5 K) dann nicht weiter halten: Erreichen die Pakete die
    # UVR nach 47 min wieder, geht ihr Versatz höchstens eine Stufe weg von 0, nie auf einmal auf
    # −1,5 K.
    base = SCENARIOS["klima-pv-kuehlen"]
    held: list[float] = []
    scenario = replace(
        base,
        events=(
            *base.events,
            (DEAF_S, _Deaf(True)),
            (HEARD_S - 60, _Held(held)),
            (HEARD_S, _Deaf(False)),
        ),
        checks=Checks(()),
    )
    result = await run_scenario(scenario, cfg, tmp_path)
    assert result.failures == []
    assert min(k for t, k in _receipts(result) if t < DEAF_S) == -3 * KLIMA_STEP_K
    assert held == [0.0]  # die UVR wendet 0 an; der Adapter hält nichts anderes
    active = [unit.active_k for t, unit in _thermal_samples(result) if t >= HEARD_S - 1]
    assert active[0] == 0.0
    jumps = [(old, new) for old, new in pairwise(active) if new != old]
    assert all(abs(new) - abs(old) <= KLIMA_STEP_K + 1e-9 for old, new in jumps), jumps


async def test_klima_without_echo_stays_at_zero_and_quiet(cfg, tmp_path) -> None:
    # Abschlussreview I3, Ruling R27: wie „klima-pv-kuehlen“, aber die UVR meldet kein Echo des
    # Versatzes; die übrigen Werte kommen, die Quelle ist verbunden. Ohne Echo ist der Aktor nicht
    # steuerbar: Die Strategie bleibt bei 0 (thermal_no_echo), die UVR bekommt 30 min lang nur 0,
    # das Entscheidungslog bleibt ruhig (kein Schreiben je Minute), und die Warnung
    # thermal.echo_mismatch.klima nennt den Befund.
    base = SCENARIOS["klima-pv-kuehlen"]

    def setup(model: PlantModel) -> None:
        base.setup(model)
        _unit(model).echo = False

    scenario = replace(base, setup=setup, checks=Checks(()))
    result = await run_scenario(scenario, cfg, tmp_path, duration_s=30 * 60)
    assert result.failures == []
    assert {k for _, k in _receipts(result)} == {0.0}
    assert len(_klima_codes(result)) <= 1
    last = result.samples[-1].thermal
    assert last is not None and (last.wish, last.reason) == (0, "thermal_no_echo")
    _, health = result.health[-1]
    assert "thermal.echo_mismatch.klima" in {alarm["id"] for alarm in health.alarms}


HEAT_SPARE_W = 1300.0  # Einspeisung ohne Versatz beim Heizen: unter der Startleistung des e-Golf


async def test_klima_base_load_does_not_count_for_a_car_that_can_never_start(cfg, tmp_path) -> None:
    # Abschlussreview I4, Ruling R28: Heizen mit 1,5 kW ohne Versatz, 1,3 kW Einspeisung. Nach
    # 40 min steckt der e-Golf in PV an; für den Start braucht er 2760 W. Einspeisung und die
    # ganze Leistung der Klimaanlage erreichen das, aber ihre Leistung ohne Versatz kann die
    # Strategie nicht freigeben: Zählte sie mit, wiche die Klimaanlage bis 0 zurück, und der
    # e-Golf startete trotzdem nie. Es zählt nur, was sie über ihrer Leistung bei 0 aufnimmt; sie
    # behält den Überschuss.
    base = SCENARIOS["klima-auto-vorrang"]
    pv = _klima_pv_w(HEAT_SPARE_W, KLIMA_HEAT_W)
    scenario = replace(
        base,
        start=WINTER_NOON,
        setup=partial(_klima_setup, mode="heizen", base_w=KLIMA_HEAT_W),
        events=(
            *_weather(
                WINTER_NOON, base.duration_s, base.seed, pv=lambda t: pv, noise_w=KLIMA_NOISE_W
            ),
            (CAR_PLUG_S, _Plug(EGOLF, EVCS_BOX)),
        ),
        checks=Checks(()),
    )
    result = await run_scenario(scenario, cfg, tmp_path)
    assert result.failures == []
    codes = _klima_codes(result)
    assert "thermal_cars" not in {code for _, code in codes}, codes
    assert [k for k, code in codes if code == "thermal_more"][:3] == [0.5, 1.0, 1.5]
    assert not any(s.cars[EGOLF].charging for s in result.samples)
    assert _export_w(result) < KLIMA_MORE_W
