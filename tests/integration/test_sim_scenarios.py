"""Szenarien des Simulators in der CI (Design 13.2): die echte Laufzeit gegen das Anlagenmodell.

Jedes Szenario besteht, wenn alle Kennzahlen in ihren Grenzen liegen und seine eigenen Prüfungen
nichts finden; ``failures`` nennt sonst jede Verletzung als Satz. Dazu kommt eine Abwandlung von
„spitze-15min“ für den Boost-Anteil des 15-min-Wächters (Plan 2c-2a, Präzisierung 9).

Bekannte Befunde der Regelung stehen in ``KNOWN_FAULTS``, mit ihren genauen Sätzen: Ist ein Befund
behoben, schlägt der Test fehl, und der Eintrag fällt weg. Zwei Abwandlungen von
„klima-auto-vorrang“ halten fest, wie sich die Klimaanlage neben einem Auto verhält, das nicht
starten kann (Ruling R21) oder sich selbst begrenzt (Plan 6a, Task 12).
"""

from dataclasses import dataclass, replace
from datetime import timedelta
from functools import partial

import pytest

from bindaems.shared.control import ChargeMode, CommandKind, Intent, VehicleIntent, wallbox_actuator
from bindaems.tools.sim.model import PlantModel
from bindaems.tools.sim.runner import SimResult, run_scenario
from bindaems.tools.sim.scenarios import (
    CAR_EXPORT_W,
    CAR_PLUG_S,
    EGOLF,
    EVCS_BOX,
    KLIMA_ACTUATOR,
    KLIMA_BASE_W,
    KLIMA_EXPORT_W,
    KLIMA_NOISE_W,
    KLIMA_SETTLED_S,
    PEAK_LEAD_S,
    QUARTER_S,
    SCENARIOS,
    TESLA,
    Checks,
    _changes,
    _klima_pv_w,
    _Plug,
    _setup,
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
# die Szenarien für die Klimaanlage (Plan 6a, Entscheidung 15), am CoE-Adapter und live
KLIMA = [
    "klima-pv-kuehlen",
    "klima-auto-vorrang",
    "klima-moduswechsel",
    "klima-paketverlust",
    "klima-spitze",
    "klima-teuer-heizen",
]
ALL = [*NAMES, *NEW, *ASSIGNMENT, *TESLA_OFF, *PLAN, *PLANNED, *KLIMA]
# Befunde der Regelung, die ein Szenario aufdeckt und die noch offen sind, mit ihren Sätzen
KNOWN_FAULTS = {
    # Startet die Regelung den e-Golf (2521 s 7 A, 2522 s „Laden an“), zieht er im ersten Zyklus
    # noch nichts: Er gilt als selbst begrenzt (Ruling R18), der offene Wunsch der Autos ist einen
    # Zyklus lang 0, und die Klimaanlage geht eine Stufe hoch (thermal_more), im nächsten Zyklus
    # wieder zurück (thermal_cars). Bericht zu Task 12.
    "klima-auto-vorrang": [
        "Die UVR bekam bei 2521 s −1,5 K nach −1 K: Der Versatz wurde größer, obwohl ein Auto "
        "Vorrang hat."
    ],
}


def test_all_scenarios_are_listed() -> None:
    assert list(SCENARIOS) == ALL


def test_battery_and_peak_scenarios_are_registered() -> None:
    assert list(SCENARIOS)[10:18] == NEW
    assert all(SCENARIOS[name].victron_live for name in NEW)
    assert not any(SCENARIOS[name].victron_live for name in list(SCENARIOS)[:10])


def test_assignment_scenarios_are_registered() -> None:
    assert list(SCENARIOS)[18:21] == ["tesla-evcs", "egolf-twc", "gast-evcs"]
    assert len(SCENARIOS) == 39


def test_tesla_off_scenarios_are_registered() -> None:
    assert list(SCENARIOS)[21:23] == TESLA_OFF
    assert not any(SCENARIOS[name].victron_live for name in TESLA_OFF)


def test_plan_scenarios_are_registered() -> None:
    assert list(SCENARIOS)[23:28] == PLAN
    assert all(SCENARIOS[name].victron_live for name in PLAN)


def test_planned_scenarios_are_registered() -> None:
    assert list(SCENARIOS)[28:33] == PLANNED
    assert all(SCENARIOS[name].victron_live for name in PLANNED)


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


async def test_klima_car_held_at_zero_amps_takes_the_surplus(cfg, tmp_path) -> None:
    # Ruling R21: wie „klima-auto-vorrang“, aber mit 1,5 kW Einspeisung ohne Versatz. Der e-Golf
    # braucht für den Start 6 A auf zwei Phasen (2760 W) und startet nie; die Regelung hält ihn
    # bei „Laden aus“, und so zählt er mit seiner ganzen Höchstleistung als offener Wunsch. Die
    # Klimaanlage geht darum stufenweise auf 0 zurück, und die 1,5 kW gehen ins Netz.
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
        (-0.5, "thermal_more"),
        (-1.0, "thermal_more"),
        (-1.5, "thermal_more"),
        (-1.0, "thermal_cars"),
        (-0.5, "thermal_cars"),
        (0.0, "thermal_cars"),
    ]
    assert not any(s.cars[EGOLF].charging for s in result.samples)
    assert _export_w(result) > KLIMA_EXPORT_W - 100


async def test_klima_self_limiting_car_lets_the_offset_flicker(cfg, tmp_path) -> None:
    # Wie „klima-auto-vorrang“; nach 65 min nimmt der e-Golf nur noch 6 A (er regelt ab), 60 min
    # lang. Die Laderegelung wechselt zwischen 8 A (er zieht weniger als 7 A: selbst begrenzt,
    # kein Vorrang, die Klimaanlage geht hoch) und 7 A (er folgt: Vorrang, sie geht zurück). Die
    # Klimaanlage flackert so zwischen 0 und −1 K, rund alle 10 min ein Schritt; die Einspeisung
    # bleibt bei rund 1,1 kW. Bericht zu Task 12, offener Befund.
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
    after = [(t, new) for t, _, new in _changes(result) if t >= taper_s]
    assert len(after) >= 4  # hoch und zurück, mehr als einmal
    assert {new for _, new in after} <= {0.0, -0.5, -1.0}
    assert sum(1 for _, new in after if new == 0.0) >= 2
    evcs = wallbox_actuator(EVCS_BOX)
    currents = [
        d.requested
        for d in result.decisions
        if d.actuator == evcs
        and d.kind is CommandKind.CURRENT
        and d.ts >= base.start + timedelta(seconds=taper_s)
    ]
    assert {7.0, 8.0} <= set(currents)
    assert result.samples[-1].cars[EGOLF].current_a == 6.0
