"""Tests der Szenarien 6 bis 10: Aufbau, Ereignisse und Prüfungen an Hand gebauten Ergebnissen.

Dazu das Ereignis ``_Boost`` und die Option ``dess`` von ``_setup``, die die Szenarien von Akku und
Spitzen brauchen, die Optionen von ``_setup`` und ``_intent`` für Gast, Home Assistant und den
Tesla woanders und die Szenarien für die Zuordnung (Plan 2c-2b, Design 10.3): ``zuordnung``,
``tesla-evcs``, ``egolf-twc`` und ``gast-evcs``, dazu die Szenarien für „Aus“ und Selbststart des
Tesla (Plan 2d, Task 6): ``tesla-aus`` und ``tesla-selbststart``. Die ganzen Läufe prüft
``tests/integration/test_sim_scenarios.py``.
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
from bindaems.shared.control import (
    ActuatorMode,
    ChargeMode,
    CommandKind,
    GuestIntent,
    Intent,
    VehicleIntent,
)
from bindaems.shared.decisions import Decision, DecisionResult
from bindaems.shared.timeutil import LOCAL_TZ
from bindaems.tools.sim.metrics import CarSample, Sample, measure
from bindaems.tools.sim.model import GUEST_CAR, PlantModel
from bindaems.tools.sim.runner import SimResult
from bindaems.tools.sim.scenarios import CLOUD_FACTORS, SCENARIOS, Checks, sun_w

T0: Final = datetime(2026, 10, 12, 16, 0, tzinfo=UTC)  # 18:00 Wiener Zeit
OK, ERROR, DRY_RUN, REJECTED = (
    DecisionResult.OK,
    DecisionResult.ERROR,
    DecisionResult.DRY_RUN,
    DecisionResult.REJECTED,
)
CURRENT, CHARGING = CommandKind.CURRENT, CommandKind.CHARGING
TESLA, EVCS = "vehicle.tesla", "wallbox.evcs"


def vienna(month: int, day: int, hour: int) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=LOCAL_TZ)


def set_up(cfg: Config, name: str) -> PlantModel:
    model = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    SCENARIOS[name].setup(model)
    return model


def actions(name: str, kind: type) -> list[tuple[int, Any]]:
    return [(t, action) for t, action in SCENARIOS[name].events if isinstance(action, kind)]


def decision(
    seconds: float, actuator: str, kind: CommandKind, value: object, result: DecisionResult
) -> Decision:
    codes: tuple[str, ...] = ("mode_pv",)
    return Decision(
        id=f"{actuator}/{kind}/{seconds}",
        ts=T0 + timedelta(seconds=seconds),
        actuator=actuator,
        kind=kind,
        requested=value,  # type: ignore[arg-type]
        applied=None if result is REJECTED else value,  # type: ignore[arg-type]
        dry_run=result is DRY_RUN,
        outcome="accepted",
        result=result,
        codes=codes,
        text="",
        inputs={},
    )


def with_codes(d: Decision, *codes: str) -> Decision:
    return replace(d, codes=codes)


def cars_at(t: int, tesla_a: float = 0.0, egolf_a: float = 0.0) -> Sample:
    cars = {
        "tesla": CarSample(tesla_a > 0, tesla_a, tesla_a * 690.0, "pv", 5),
        "egolf": CarSample(egolf_a > 0, egolf_a, egolf_a * 460.0, "pv", 6),
    }
    quiet = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
    return Sample(t, quiet, quiet, 0.0, 60.0, cars)


def health(
    mode: str = "CONTROL", *, vehicles: Any = (), alarms: Any = (), wallboxes: Any = ()
) -> HealthReport:
    return HealthReport(
        mode=mode,
        version="0",
        adapters=[],
        selfcheck=[],
        alarms=list(alarms),
        cycle_ms_p95=None,
        vehicles=list(vehicles),
        wallboxes=list(wallboxes),
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
    scenario = replace(SCENARIOS[name], start=T0)
    return SimResult(
        scenario, metrics, decisions, states or [], samples, [], usable_kwh=cfg.battery.usable_kwh
    )


def check(name: str, index: int) -> Any:
    checks = SCENARIOS[name].checks
    assert isinstance(checks, Checks)
    return checks.items[index]


def shown_once(cfg: Config, name: str, at: int, who: str, kind: str) -> SimResult:
    """Bis 20 min alle 10 s eine Gesundheit; nur die bei ``at`` zeigt ``who`` mit ``kind``."""
    vehicles = [{"id": who, "assignment": kind}]
    reports = [(t, health(vehicles=vehicles if t == at else [])) for t in range(0, 1200, 10)]
    return result_of(cfg, name, states=reports)


# --- Aufbau und Ereignisse -----------------------------------------------------------------------


def test_scenarios_6_to_10_follow_the_table_of_the_plan() -> None:
    table = {
        "tesla-5a": ("Tesla auf 5 A mit verlorenem erstem Befehl", vienna(6, 21, 10), 3600),
        "tesla-schlaeft": ("Schlafender Tesla im PV-Modus", vienna(6, 21, 9), 3600),
        "ausfaelle": ("Ausfälle einzeln, Netzausfall mit Wiederaufnahme", vienna(10, 12, 18), 5400),
        "zuordnung": ("Zuordnung unklar und Übersteuerung", vienna(10, 12, 18), 3600),
        "dry-run": ("Dry-Run neben einem fremden Regler", vienna(6, 21, 10), 3600),
    }
    for name, (title, start, duration_s) in table.items():
        scenario = SCENARIOS[name]
        assert (scenario.title, scenario.start, scenario.duration_s) == (title, start, duration_s)
        assert scenario.intent.control
    seeds = [scenario.seed for scenario in SCENARIOS.values()]
    assert len(set(seeds)) == len(seeds)


def test_the_tesla_waits_at_10_a_for_its_5_a(cfg) -> None:
    model = set_up(cfg, "tesla-5a")
    tesla = model.teslas["tesla"]
    assert (tesla.wallbox, tesla.charge_amps, model.cars["egolf"].wallbox) == ("twc", 10, None)
    assert model.soc_pct == 90.0
    scenario = SCENARIOS["tesla-5a"]
    assert scenario.live and scenario.intent.vehicles == {"tesla": VehicleIntent()}
    probe = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    for t, action in scenario.events:
        if t in (0, 3599):
            action(probe, None)
            assert probe.pv_w == 4600.0


def test_the_tesla_sleeps_while_the_sun_rises(cfg) -> None:
    model = set_up(cfg, "tesla-schlaeft")
    tesla = model.teslas["tesla"]
    assert (tesla.wallbox, tesla.asleep, tesla.charge_enabled) == ("twc", True, False)
    assert model.cars["egolf"].wallbox is None
    probe = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    pv = {}
    for t, action in SCENARIOS["tesla-schlaeft"].events:
        if t in (0, 900, 1800, 3599):
            action(probe, None)
            pv[t] = probe.pv_w
    assert pv == {0: 0.0, 900: 3000.0, 1800: 6000.0, 3599: 6000.0}


def test_the_outages_come_one_after_the_other(cfg) -> None:
    from bindaems.tools.sim.scenarios import _Outage

    found = [(t, action.name, action.begins) for t, action in actions("ausfaelle", _Outage)]
    assert found == [
        (600, "evcs", True),
        (900, "evcs", False),
        (1200, "twc", True),
        (1500, "twc", False),
        (1800, "tessie", True),
        (2100, "tessie", False),
        (2400, "cerbo", True),
        (2460, "cerbo", False),
        (3000, "grid", True),
        (3180, "grid", False),
    ]
    model = set_up(cfg, "ausfaelle")
    begins, ends = (action for t, action in actions("ausfaelle", _Outage) if t in (600, 900))
    begins(model, None)
    assert model.outages == {"evcs"}
    ends(model, None)
    assert model.outages == set()
    assert SCENARIOS["ausfaelle"].intent.vehicles == {
        "tesla": VehicleIntent(mode=ChargeMode.NOW),
        "egolf": VehicleIntent(mode=ChargeMode.NOW),
    }


@dataclass
class FakeRuntime:
    """Was ``_Override`` von der Laufzeit braucht: die gültige Absicht und ``submit_intent``."""

    current: Intent | None
    reply: IntentReply = field(default_factory=lambda: IntentReply("accepted", 2))
    sent: list[Intent] = field(default_factory=list)

    def intent(self) -> Intent | None:
        return self.current

    def submit_intent(self, intent: Intent) -> IntentReply:
        self.sent.append(intent)
        return self.reply


def test_the_override_is_a_new_version_of_the_valid_intent(cfg) -> None:
    from bindaems.tools.sim.scenarios import _Override, _Unplug

    model = set_up(cfg, "zuordnung")  # der e-Golf an der EVCS, der Tesla woanders
    egolf = model.cars["egolf"]
    ((at, override),) = actions("zuordnung", _Override)
    live = {TESLA: ActuatorMode.LIVE, EVCS: ActuatorMode.LIVE}
    current = SCENARIOS["zuordnung"].intent.model_copy(update={"actuators": live})
    rt = FakeRuntime(current)
    override(model, rt)  # type: ignore[arg-type]
    assert at == 600 and rt.sent == [
        current.model_copy(
            update={
                "version": 2,
                "vehicles": {"egolf": VehicleIntent(mode=ChargeMode.NOW, wallbox="evcs")},
            }
        )
    ]
    rt.reply = IntentReply("rejected", 1, ("Unbekannte Wallbox „evcs“ für „egolf“.",))
    with pytest.raises(ValueError, match="Übersteuerung abgelehnt: Unbekannte Wallbox"):
        override(model, rt)  # type: ignore[arg-type]
    rt.current = None
    with pytest.raises(ValueError, match="Übersteuerung ohne gültige Absicht"):
        override(model, rt)  # type: ignore[arg-type]
    ((at, unplug),) = actions("zuordnung", _Unplug)
    unplug(model, None)
    assert at == 2400 and egolf.wallbox is None and model.wallboxes["evcs"].car is None


def test_boost_is_a_new_version_of_the_valid_intent_with_boost_until(cfg) -> None:
    from bindaems.tools.sim.scenarios import _Boost

    model = set_up(cfg, "zuordnung")  # das Ereignis braucht vom Modell nichts
    until = T0 + timedelta(minutes=45)
    boost = _Boost(2, "egolf", until)
    assert (boost.version, boost.vehicle, boost.until) == (2, "egolf", until)
    # Sonst bleibt die gültige Absicht, auch Modus und Wallbox des Fahrzeugs.
    current = Intent(
        version=1,
        control=True,
        actuators={EVCS: ActuatorMode.LIVE},
        vehicles={
            "tesla": VehicleIntent(mode=ChargeMode.PV),
            "egolf": VehicleIntent(mode=ChargeMode.NOW, wallbox="evcs"),
        },
    )
    rt = FakeRuntime(current)
    boost(model, rt)  # type: ignore[arg-type]
    assert rt.sent == [
        current.model_copy(
            update={
                "version": 2,
                "vehicles": {
                    "tesla": VehicleIntent(mode=ChargeMode.PV),
                    "egolf": VehicleIntent(mode=ChargeMode.NOW, wallbox="evcs", boost_until=until),
                },
            }
        )
    ]
    # Ein Fahrzeug ohne Eintrag bekommt den Standard und den Boost.
    rt = FakeRuntime(Intent(version=1, control=True))
    boost(model, rt)  # type: ignore[arg-type]
    assert [i.vehicles for i in rt.sent] == [{"egolf": VehicleIntent(boost_until=until)}]
    assert rt.sent[0].version == 2 and rt.sent[0].control


def test_a_rejected_boost_stops_the_run(cfg) -> None:
    from bindaems.tools.sim.scenarios import _Boost

    model = set_up(cfg, "zuordnung")
    boost = _Boost(2, "egolf", T0 + timedelta(minutes=45))
    rt = FakeRuntime(Intent(version=1, control=True))
    rt.reply = IntentReply("rejected", 1, ("Unbekanntes Fahrzeug „egolf“.",))
    with pytest.raises(ValueError, match="Boost abgelehnt: Unbekanntes Fahrzeug"):
        boost(model, rt)  # type: ignore[arg-type]
    rt.reply = IntentReply("clamped", 2, ("Boost auf 12 h begrenzt, bis 06:00.",))
    boost(model, rt)  # type: ignore[arg-type]  # geklemmt ist angenommen
    rt.current = None
    with pytest.raises(ValueError, match="Boost ohne gültige Absicht"):
        boost(model, rt)  # type: ignore[arg-type]


def test_setup_switches_dynamic_ess_on_request(cfg) -> None:
    from bindaems.tools.sim.scenarios import _setup

    without = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    _setup(without, soc_pct=60.0)
    assert without.dess is False
    with_dess = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    _setup(with_dess, soc_pct=60.0, dess=True)
    assert with_dess.dess is True
    assert with_dess.soc_pct == 60.0  # der Rest des Aufbaus wie sonst
    assert all(not set_up(cfg, name).dess for name in list(SCENARIOS)[:10])


def test_setup_puts_the_guest_and_the_tesla_elsewhere(cfg) -> None:
    from bindaems.tools.sim.scenarios import _setup

    model = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    _setup(
        model,
        soc_pct=60.0,
        tesla=None,
        egolf=None,  # sonst steckt der e-Golf schon an der EVCS, und der Gast fände sie belegt
        tesla_elsewhere=True,
        guest="evcs",
        guest_phases=2,
        away=frozenset({"egolf"}),
        evcs_on=True,
    )
    guest, tesla = model.cars[GUEST_CAR], model.teslas["tesla"]
    assert (guest.wallbox, guest.phases) == ("evcs", 2)
    assert model.wallboxes["evcs"].car is guest
    assert tesla.elsewhere and tesla.wallbox is None
    assert model.away == {"egolf"}
    assert model.wallboxes["evcs"].start_stop == 1


def test_setup_keeps_the_guest_unplugged_and_everybody_home_unless_asked(cfg) -> None:
    from bindaems.tools.sim.scenarios import _setup

    model = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    _setup(model, soc_pct=60.0)
    guest = model.cars[GUEST_CAR]
    assert (guest.wallbox, guest.phases) == (None, 3)
    assert not model.teslas["tesla"].elsewhere and model.away == set()
    assert model.wallboxes["evcs"].start_stop == 0
    assert model.wallboxes["evcs"].car is model.cars["egolf"]  # wie bisher an der Standard-Wallbox
    # die bestehenden 18 Szenarien stecken den Gast nie an und schicken niemanden weg
    for name in list(SCENARIOS)[:18]:
        existing = set_up(cfg, name)
        assert existing.cars[GUEST_CAR].wallbox is None and existing.away == set(), name


def test_evcs_on_needs_an_evcs_under_that_name(cfg) -> None:
    from bindaems.tools.sim.scenarios import _setup

    odd = cfg.model_copy(update={"wallboxes": {"evcs": cfg.wallboxes["twc"]}})
    model = PlantModel(odd, random.Random(1))  # noqa: S311 - Saat der Simulation
    with pytest.raises(ValueError, match="„evcs“ ist keine EVCS"):
        _setup(model, soc_pct=60.0, tesla=None, egolf=None, evcs_on=True)


def test_the_intent_takes_the_guest_mode_beside_the_vehicle_modes() -> None:
    from bindaems.tools.sim.scenarios import _intent

    plain = _intent(tesla=ChargeMode.NOW)
    assert plain.guest == GuestIntent(mode=ChargeMode.PV)  # wie die Absicht ohne Angabe
    assert plain.vehicles == {"tesla": VehicleIntent(mode=ChargeMode.NOW)}
    sooner = _intent(egolf=ChargeMode.PV, guest_mode=ChargeMode.NOW)
    assert sooner.guest == GuestIntent(mode=ChargeMode.NOW)
    assert sooner.vehicles == {"egolf": VehicleIntent(mode=ChargeMode.PV)}  # kein Fahrzeug „guest“
    assert (sooner.version, sooner.control) == (1, True)


def test_the_dry_run_runs_beside_a_foreign_controller(cfg) -> None:
    model = set_up(cfg, "dry-run")
    assert model.foreign and model.teslas["tesla"].charge_enabled
    assert (model.cars["tesla"].wallbox, model.cars["egolf"].wallbox) == ("twc", "evcs")
    scenario = SCENARIOS["dry-run"]
    assert not scenario.live and scenario.intent.actuators == {}
    assert scenario.intent.vehicles == {
        "tesla": VehicleIntent(mode=ChargeMode.PV),
        "egolf": VehicleIntent(mode=ChargeMode.PV),
    }
    factors = {
        round(action.pv_w / sun_w(scenario.start + timedelta(seconds=t)), 6)
        for t, action in scenario.events
    }
    assert factors == set(CLOUD_FACTORS)  # wie „wolkig“: Sonne und Wolken im Wechsel


def test_the_assignment_scenarios_follow_the_table_of_the_plan(cfg) -> None:
    # Plan 2c-2b, Task 8: alle abends, live und mit Victron im Dry-Run, ohne PV
    from bindaems.tools.sim.scenarios import HOUSE_NOISE_W, HOUSE_W, _Weather

    table = {
        "zuordnung": ("Zuordnung unklar und Übersteuerung", 3600, 9),
        "tesla-evcs": ("Tesla an der EVCS ohne Übersteuerung", 1800, 19),
        "egolf-twc": ("e-Golf am Wall Connector", 1800, 20),
        "gast-evcs": ("Gast an der EVCS, e-Golf weg", 1800, 21),
    }
    for name, (title, duration_s, seed) in table.items():
        scenario = SCENARIOS[name]
        found = (scenario.title, scenario.start, scenario.duration_s, scenario.seed)
        assert found == (title, vienna(10, 12, 18), duration_s, seed), name
        assert scenario.live and not scenario.victron_live and scenario.intent.control, name
        assert dict(scenario.control) == {}, name
        weather = [(t, action) for t, action in scenario.events if isinstance(action, _Weather)]
        assert [t for t, _ in weather] == list(range(duration_s)), name
        probe = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
        weather[-1][1](probe, None)  # type: ignore[arg-type]  # das Wetter braucht keine Laufzeit
        assert probe.pv_w == 0.0, name
        assert all(abs(watts - HOUSE_W) <= HOUSE_NOISE_W for watts in probe.house_w), name
    others = {
        name: [t for t, action in SCENARIOS[name].events if not isinstance(action, _Weather)]
        for name in table
    }  # nur „zuordnung“ hat Übersteuerung und Abstecken (oben)
    assert others == {"zuordnung": [600, 2400], "tesla-evcs": [], "egolf-twc": [], "gast-evcs": []}


def test_the_assignment_setups_place_the_cars(cfg) -> None:
    now, pv = ChargeMode.NOW, ChargeMode.PV
    table = {
        # Name: Wallbox von Tesla, e-Golf und Gast; weg laut Home Assistant; Tesla woanders
        # angesteckt; EVCS schon auf „Laden an“; Modi laut Absicht; Modus des Gastes
        "zuordnung": (None, "evcs", None, set(), True, 1, {"egolf": now}, pv),
        "tesla-evcs": ("evcs", None, None, set(), False, 0, {"tesla": now}, pv),
        "egolf-twc": (None, "twc", None, set(), False, 0, {"egolf": pv}, pv),
        "gast-evcs": (None, None, "evcs", {"egolf"}, False, 0, {}, now),
    }
    for name, (tesla, egolf, guest, away, elsewhere, evcs_on, modes, guest_mode) in table.items():
        model = set_up(cfg, name)
        cars = model.cars
        found = (cars["tesla"].wallbox, cars["egolf"].wallbox, cars[GUEST_CAR].wallbox)
        assert found == (tesla, egolf, guest), name
        assert (model.away, model.teslas["tesla"].elsewhere) == (away, elsewhere), name
        assert model.wallboxes["evcs"].start_stop == evcs_on, name
        assert (model.soc_pct, cars[GUEST_CAR].phases) == (60.0, 3), name
        intent = SCENARIOS[name].intent
        assert {key: entry.mode for key, entry in intent.vehicles.items()} == modes, name
        assert intent.guest == GuestIntent(mode=guest_mode), name
    tesla = set_up(cfg, "tesla-evcs").teslas["tesla"]
    assert (tesla.charge_amps, tesla.charge_enabled) == (10, True)  # lädt selbst, 10 A im Auto


# --- Prüfungen -----------------------------------------------------------------------------------


def test_a_decision_must_name_not_taken_retry(cfg) -> None:
    named = check("tesla-5a", 0)
    retry = with_codes(decision(180, TESLA, CURRENT, 5.0, OK), "mode_pv", "not_taken_retry")
    assert named.text == "Eine Entscheidung nennt „not_taken_retry“."
    assert named.find(result_of(cfg, "tesla-5a", decisions=[retry])) == []
    plain = decision(90, TESLA, CURRENT, 5.0, ERROR)
    assert named.find(result_of(cfg, "tesla-5a", decisions=[plain])) == [
        "Keine Entscheidung mit „not_taken_retry“."
    ]


def test_the_tesla_must_charge_with_5_a_at_the_end(cfg) -> None:
    at_5 = check("tesla-5a", 1)
    assert at_5.text == "„tesla“ lädt am Ende mit 5 A."

    def ending(*amps: float) -> SimResult:
        return result_of(cfg, "tesla-5a", samples=[cars_at(t, a) for t, a in enumerate(amps)])

    assert at_5.find(ending(10.0, 5.0)) == []
    assert at_5.find(ending(5.0, 10.0)) == ["„tesla“ lädt am Ende mit 10 A, verlangt 5 A."]
    assert at_5.find(ending(5.0, 0.0)) == ["„tesla“ lädt am Ende nicht, verlangt 5 A."]
    assert at_5.find(ending(5.0, 4.0)) == ["„tesla“ lädt am Ende mit 4 A, verlangt 5 A."]
    assert at_5.find(ending()) == ["„tesla“ lädt am Ende nicht, verlangt 5 A."]


def test_exactly_one_wake(cfg) -> None:
    one_wake = check("tesla-schlaeft", 0)
    assert one_wake.text == "Genau ein Weckvorgang an „tesla“."
    wake = with_codes(decision(1506, TESLA, CHARGING, True, OK), "mode_pv", "wake")
    refused = with_codes(decision(1500, TESLA, CHARGING, True, REJECTED), "mode_pv", "wake")
    other = with_codes(decision(1500, EVCS, CHARGING, True, OK), "mode_pv", "wake")
    later = replace(wake, id="zweiter", ts=wake.ts + timedelta(hours=1))

    def found(*decisions: Decision) -> list[str]:
        return one_wake.find(result_of(cfg, "tesla-schlaeft", decisions=list(decisions)))

    assert found(refused, wake, other) == []
    assert found(refused, other) == ["0 Weckvorgänge an „tesla“, verlangt genau einer."]
    assert found(wake, later) == ["2 Weckvorgänge an „tesla“, verlangt genau einer."]


def test_a_deferred_wake_or_current_is_no_command(cfg) -> None:
    # Zurückgestellt (Stundenlimit) ging nichts hinaus: Das ist weder ein Weckvorgang noch eine
    # weitere Entscheidung „Strom 16 A“.
    deferred = DecisionResult.DEFERRED
    one_wake, sixteen = check("tesla-schlaeft", 0), check("tesla-evcs", 1)
    held = with_codes(decision(1400, TESLA, CHARGING, True, deferred), "wake", "rate.start_stop")
    wake = with_codes(decision(1506, TESLA, CHARGING, True, OK), "mode_pv", "wake")
    assert one_wake.find(result_of(cfg, "tesla-schlaeft", decisions=[held, wake])) == []
    later = with_codes(decision(600, TESLA, CURRENT, 16.0, deferred), "mode_now", "rate.per_hour")
    sent = decision(654, TESLA, CURRENT, 16.0, OK)
    assert sixteen.find(result_of(cfg, "tesla-evcs", decisions=[later, sent])) == []


def test_the_tesla_must_charge_at_the_end(cfg) -> None:
    charging = check("tesla-schlaeft", 1)
    assert charging.text == "„tesla“ lädt am Ende."
    samples = [cars_at(0), cars_at(1, 7.0)]
    assert charging.find(result_of(cfg, "tesla-schlaeft", samples=samples)) == []
    stopped = [cars_at(0, 7.0), cars_at(1)]
    assert charging.find(result_of(cfg, "tesla-schlaeft", samples=stopped)) == [
        "„tesla“ lädt am Ende nicht."
    ]
    assert charging.find(result_of(cfg, "tesla-schlaeft")) == ["„tesla“ lädt am Ende nicht."]


def test_safe_through_the_last_two_outages(cfg) -> None:
    cerbo, grid = check("ausfaelle", 0), check("ausfaelle", 1)
    assert cerbo.text == "SAFE während des Ausfalls „cerbo“ (40–41 min)."
    assert grid.text == "SAFE während des Ausfalls „grid“ (50–53 min)."

    def states(mode_at: Any) -> SimResult:
        reports = [(t, health(mode_at(t))) for t in range(0, 5400, 10)]
        return result_of(cfg, "ausfaelle", states=reports)

    def safe_in_windows(t: int) -> str:
        return "SAFE" if 2400 <= t < 2490 or 3000 <= t < 3180 else "CONTROL"

    assert cerbo.find(states(safe_in_windows)) == []
    assert grid.find(states(safe_in_windows)) == []

    def late(t: int) -> str:
        return "SAFE" if 2410 <= t < 2490 or 3000 <= t < 3170 else "CONTROL"

    assert cerbo.find(states(late)) == [
        "Während des Ausfalls „cerbo“ bei 2400 s CONTROL, verlangt SAFE."
    ]
    assert grid.find(states(late)) == [
        "Während des Ausfalls „grid“ bei 3170 s CONTROL, verlangt SAFE."
    ]
    assert grid.find(result_of(cfg, "ausfaelle")) == ["Der Ausfall „grid“ liegt nicht im Lauf."]


def test_both_cars_charge_from_60_min(cfg) -> None:
    both = check("ausfaelle", 2)
    assert both.text == "Ab 60 min laden beide Autos."

    def run(gap: int | None) -> SimResult:
        samples = [cars_at(t, 16.0, 0.0 if t == gap else 15.0) for t in range(3590, 3700)]
        return result_of(cfg, "ausfaelle", samples=samples)

    assert both.find(run(3599)) == []  # vor 60 min zählt nicht
    assert both.find(run(3600)) == ["„egolf“ lädt bei 3600 s nicht."]
    assert both.find(run(3650)) == ["„egolf“ lädt bei 3650 s nicht."]
    short = result_of(cfg, "ausfaelle", samples=[cars_at(0, 16.0, 15.0)])
    assert both.find(short) == ["Der Lauf endet vor 3600 s."]


def test_the_unplugged_guest_does_not_count_for_both_cars(cfg) -> None:
    # Der Gast steht in jeder Probe, abgesteckt und ohne Strom: „beide Autos“ sind Tesla und e-Golf.
    both = check("ausfaelle", 2)
    idle = CarSample(False, 0.0, 0.0, "pv", 6)

    def sample_at(t: int, *, egolf_idle: bool = False) -> Sample:
        sample = cars_at(t, 16.0, 15.0)
        cars = {**sample.cars, GUEST_CAR: idle}
        return replace(sample, cars={**cars, "egolf": idle} if egolf_idle else cars)

    whole = [sample_at(t) for t in range(3590, 3700)]
    assert both.find(result_of(cfg, "ausfaelle", samples=whole)) == []
    gap = [sample_at(t, egolf_idle=t == 3650) for t in range(3590, 3700)]
    assert both.find(result_of(cfg, "ausfaelle", samples=gap)) == ["„egolf“ lädt bei 3650 s nicht."]


def test_a_decision_must_name_resume_after_outage(cfg) -> None:
    resumed = check("ausfaelle", 3)
    assert resumed.text == "Eine Entscheidung nennt „resume_after_outage“."
    start = with_codes(decision(3181, EVCS, CHARGING, True, OK), "mode_now", "resume_after_outage")
    assert resumed.find(result_of(cfg, "ausfaelle", decisions=[start])) == []


def test_the_health_must_show_unclear_before_the_override(cfg) -> None:
    unclear = check("zuordnung", 0)
    assert unclear.text == "Die Gesundheit zeigt „egolf“ vor 10 min „unklar“."

    def found(at: int, who: str = "egolf", kind: str = "unclear") -> list[str]:
        return unclear.find(shown_once(cfg, "zuordnung", at, who, kind))

    assert found(590) == []
    message = ["Die Gesundheit zeigt „egolf“ vor 600 s nie „unklar“."]
    assert found(600) == message
    assert found(590, "tesla") == message
    assert found(590, kind="detected") == message


def test_after_the_override_nothing_may_be_unclear(cfg) -> None:
    clear = check("zuordnung", 1)
    assert clear.text == "Ab 11 min zeigt die Gesundheit kein Fahrzeug „unklar“."

    def found(unclear: dict[int, str], *, until: int = 1200) -> list[str]:
        """Alle 10 s bis ``until`` eine Gesundheit: ``unclear`` nennt je Sekunde das Fahrzeug, das
        sie „unklar“ zeigt; sonst zeigt sie den Tesla angenommen und den e-Golf übersteuert."""

        def vehicles(t: int) -> list[dict[str, str]]:
            return [
                {"id": key, "assignment": "unclear" if unclear.get(t) == key else usual}
                for key, usual in (("tesla", "default"), ("egolf", "override"))
            ]

        reports = [(t, health(vehicles=vehicles(t))) for t in range(0, until, 10)]
        return clear.find(result_of(cfg, "zuordnung", states=reports))

    assert found({}) == []
    assert found({650: "egolf"}) == []  # vor 11 min zählt nicht
    assert found({700: "egolf"}) == ["Bei 700 s zeigt die Gesundheit „egolf“ „unklar“."]
    assert found({660: "tesla", 700: "egolf"}) == [  # nur die erste
        "Bei 660 s zeigt die Gesundheit „tesla“ „unklar“."
    ]
    assert found({}, until=660) == ["Der Lauf endet vor 660 s."]


def test_the_tesla_must_be_detected_within_3_min(cfg) -> None:
    # Design 10.3: binnen 2 min ab dem Beginn der Regelung; sie beginnt im Simulator nach 60 s
    detected = check("tesla-evcs", 0)
    assert detected.text == "Die Gesundheit zeigt „tesla“ vor 3 min „erkannt“."

    def found(at: int, who: str = "tesla", kind: str = "detected") -> list[str]:
        return detected.find(shown_once(cfg, "tesla-evcs", at, who, kind))

    assert found(170) == []
    message = ["Die Gesundheit zeigt „tesla“ vor 180 s nie „erkannt“."]
    assert found(180) == message
    assert found(170, "egolf") == message
    assert found(170, kind="default") == message


def test_exactly_one_decision_tesla_current_16_a(cfg) -> None:
    sixteen = check("tesla-evcs", 1)
    assert sixteen.text == "Genau eine Entscheidung „Strom 16 A“ an „tesla“."
    one = decision(654, TESLA, CURRENT, 16.0, OK)
    others = [
        decision(700, TESLA, CURRENT, 10.0, OK),
        decision(700, EVCS, CURRENT, 16.0, OK),
        decision(700, TESLA, CHARGING, True, OK),
    ]

    def found(*decisions: Decision) -> list[str]:
        return sixteen.find(result_of(cfg, "tesla-evcs", decisions=list(decisions)))

    assert found(one, *others) == []
    assert found(*others) == ["0 Entscheidungen „Strom 16 A“ an „tesla“, verlangt genau eine."]
    again = replace(one, id="noch einmal", result=ERROR)
    assert found(one, again) == ["2 Entscheidungen „Strom 16 A“ an „tesla“, verlangt genau eine."]


def test_the_evcs_must_not_charge_from_41_min(cfg) -> None:
    off = check("zuordnung", 2)
    assert off.text == "Ab 41 min lädt die EVCS „evcs“ nicht."
    on = decision(602, EVCS, CHARGING, True, OK)
    stopped = decision(2402, EVCS, CHARGING, False, OK)
    idle = [cars_at(t) for t in range(2455, 2470)]

    def found(samples: list[Sample], *decisions: Decision) -> list[str]:
        result = result_of(cfg, "zuordnung", samples=samples, decisions=list(decisions))
        return off.find(result)

    assert found(idle, on, stopped) == []
    assert found(idle, on, decision(2460, EVCS, CHARGING, False, OK)) == []  # bis 41 min genau
    assert found(idle, on, decision(2400, EVCS, CHARGING, False, REJECTED)) == [
        "Die EVCS „evcs“ ist bei 2460 s nicht auf „Laden aus“."
    ]
    assert found(idle, on, decision(2461, EVCS, CHARGING, False, OK)) == [
        "Die EVCS „evcs“ ist bei 2460 s nicht auf „Laden aus“."
    ]
    assert found(idle, on, stopped, decision(3000, EVCS, CHARGING, True, DRY_RUN)) == [
        "Die EVCS „evcs“ bekommt nach 2460 s „Laden an“."
    ]
    charging = [cars_at(2459), cars_at(2460, 0.0, 6.0)]
    assert found(charging, on, stopped) == ["Bei 2460 s lädt ein Auto."]


def test_the_dry_run_must_decide_without_repeats_and_alarms(cfg) -> None:
    repeats, decided, alarms = (check("dry-run", i) for i in range(3))
    assert repeats.text == "Keine wiederholte Dry-Run-Entscheidung."
    assert (
        decided.text
        == "Mindestens eine Dry-Run-Entscheidung an „wallbox.evcs“ und „vehicle.tesla“."
    )
    assert alarms.text == "Kein Alarm „competitor.foreign.*“."
    both = [decision(30, EVCS, CURRENT, 6.0, DRY_RUN), decision(30, TESLA, CURRENT, 9.0, DRY_RUN)]
    assert repeats.find(result_of(cfg, "dry-run", decisions=both)) == []
    assert decided.find(result_of(cfg, "dry-run", decisions=both)) == []
    twice = [*both, decision(60, TESLA, CURRENT, 9.0, DRY_RUN)]
    assert repeats.find(result_of(cfg, "dry-run", decisions=twice)) == [
        "Wiederholte Dry-Run-Entscheidungen: 1, verlangt keine."
    ]
    live = [decision(30, TESLA, CURRENT, 9.0, OK)]
    assert decided.find(result_of(cfg, "dry-run", decisions=live)) == [
        "Keine Dry-Run-Entscheidung an „wallbox.evcs“.",
        "Keine Dry-Run-Entscheidung an „vehicle.tesla“.",
    ]
    foreign = {"id": "competitor.foreign.wallbox.evcs", "severity": "error"}
    other = {"id": "competitor.dess", "severity": "error"}
    reports = [(0, health(alarms=[other])), (10, health(alarms=[other, foreign]))]
    assert alarms.find(result_of(cfg, "dry-run", states=reports[:1])) == []
    assert alarms.find(result_of(cfg, "dry-run", states=reports)) == [
        "Alarm „competitor.foreign.wallbox.evcs“ bei 10 s."
    ]


def test_the_wall_connector_must_show_the_egolf(cfg) -> None:
    occupied = check("egolf-twc", 0)
    assert occupied.text == "Ab 4 min steht an „twc“ laut Gesundheit „egolf“, erkannt."

    def found(changes: dict[int, dict[str, Any]], *, until: int = 600) -> list[str]:
        """Alle 10 s bis ``until`` eine Gesundheit: an der EVCS niemand, am Wall Connector der
        e-Golf, erkannt; ``changes`` ändert den Eintrag des Wall Connectors je Sekunde."""

        def wallboxes(t: int) -> list[dict[str, Any]]:
            twc = {"id": "twc", "occupant": "egolf", "assignment": "detected"} | changes.get(t, {})
            return [{"id": "evcs", "occupant": None, "assignment": None}, twc]

        reports = [(t, health(wallboxes=wallboxes(t))) for t in range(0, until, 10)]
        return occupied.find(result_of(cfg, "egolf-twc", states=reports))

    assert found({}) == []
    assert found({230: {"occupant": "guest", "assignment": "default"}}) == []  # vor 4 min
    message = ["Bei 300 s steht an „twc“ nicht „egolf“, erkannt."]
    assert found({300: {"occupant": "guest"}}) == message
    assert found({300: {"assignment": "default"}}) == message
    assert found({300: {"id": "evcs"}}) == message  # erkannt, aber an der EVCS
    assert found({300: {"occupant": "guest"}, 400: {"occupant": None}}) == message  # die erste
    assert found({}, until=240) == ["Der Lauf endet vor 240 s."]


def test_no_decision_may_reach_the_tesla(cfg) -> None:
    none = check("egolf-twc", 1)
    assert none.text == "Keine Entscheidung an „vehicle.tesla“."
    evcs = decision(60, EVCS, CURRENT, 16.0, OK)
    assert none.find(result_of(cfg, "egolf-twc", decisions=[evcs])) == []
    tesla = decision(90, TESLA, CURRENT, 16.0, OK)
    assert none.find(result_of(cfg, "egolf-twc", decisions=[evcs, tesla])) == [
        "1 Entscheidungen an „vehicle.tesla“, die erste bei 90 s."
    ]
    # Jede zählt, auch eine abgelehnte und eine im Dry-Run.
    refused = decision(120.5, TESLA, CHARGING, True, REJECTED)
    dry = decision(150, TESLA, CURRENT, 5.0, DRY_RUN)
    assert none.find(result_of(cfg, "egolf-twc", decisions=[refused, dry])) == [
        "2 Entscheidungen an „vehicle.tesla“, die erste bei 120 s."
    ]


def test_the_evcs_must_show_the_guest(cfg) -> None:
    occupied = check("gast-evcs", 0)
    assert occupied.text == "Ab 3 min steht an „evcs“ laut Gesundheit „guest“, erkannt."

    def found(detected_from: int) -> list[str]:
        """Alle 10 s bis 5 min eine Gesundheit: an der EVCS der Gast, angenommen, ab
        ``detected_from`` erkannt; am Wall Connector niemand."""

        def wallboxes(t: int) -> list[dict[str, Any]]:
            kind = "detected" if t >= detected_from else "default"
            evcs = {"id": "evcs", "occupant": "guest", "assignment": kind}
            return [evcs, {"id": "twc", "occupant": None, "assignment": None}]

        reports = [(t, health(wallboxes=wallboxes(t))) for t in range(0, 300, 10)]
        return occupied.find(result_of(cfg, "gast-evcs", states=reports))

    assert found(120) == [] and found(180) == []
    assert found(190) == ["Bei 180 s steht an „evcs“ nicht „guest“, erkannt."]


def test_the_cars_must_charge_with_16_a_at_the_end(cfg) -> None:
    # Abends ohne PV laden der Tesla und der Gast so nur in Sofort, der e-Golf als Last am Wall
    # Connector, den die Regelung nicht steuert.
    texts = {
        ("tesla-evcs", 2): "„tesla“ lädt am Ende mit 16 A.",
        ("egolf-twc", 2): "„egolf“ lädt am Ende mit 16 A.",
        ("gast-evcs", 1): "„gast“ lädt am Ende mit 16 A.",
    }
    assert {(name, index): check(name, index).text for name, index in texts} == texts
    full = check("gast-evcs", 1)

    def ending(amps: float) -> SimResult:
        sample = cars_at(0)
        guest = CarSample(amps > 0, amps, amps * 3 * 230.0, "now", 6)
        samples = [replace(sample, cars={**sample.cars, GUEST_CAR: guest})]
        return result_of(cfg, "gast-evcs", samples=samples)

    assert full.find(ending(16.0)) == []
    assert full.find(ending(6.0)) == ["„gast“ lädt am Ende mit 6 A, verlangt 16 A."]


def stop(seconds: float, result: DecisionResult = OK, value: object = False) -> Decision:
    return decision(seconds, TESLA, CHARGING, value, result)


def test_stops_within_finds_a_late_stop(cfg) -> None:
    within = check("tesla-aus", 0)
    assert within.text == "„vehicle.tesla“ bekommt „Laden aus“ binnen 10 s nach 45 min."

    def found(*decisions: Decision) -> list[str]:
        return within.find(result_of(cfg, "tesla-aus", decisions=list(decisions)))

    assert found(stop(2700)) == [] and found(stop(2710)) == []
    late = ["„vehicle.tesla“ bekam nach 2700 s kein „Laden aus“ binnen 10 s."]
    assert found(stop(2715)) == late
    assert found() == late
    # Davor zählt nicht, ebenso wenig ein Start, ein anderer Aktor oder ein zurückgehaltener Stopp.
    elsewhere = decision(2705, EVCS, CHARGING, False, OK)
    assert found(stop(2699), stop(2705, value=True), elsewhere) == late
    held = [stop(2705, REJECTED), stop(2705, DecisionResult.DEFERRED)]
    assert found(*held, stop(2705, DecisionResult.FOREIGN)) == late
    # Auch ein Stopp im Dry-Run oder ein fehlgeschlagener zählt: Der Befehl ging hinaus.
    assert found(stop(2705, DRY_RUN)) == [] and found(stop(2705, ERROR)) == []


def test_the_selfstart_stop_has_210_s(cfg) -> None:
    within = check("tesla-selbststart", 0)
    assert within.text == "„vehicle.tesla“ bekommt „Laden aus“ binnen 210 s nach 5 min."
    result = result_of(cfg, "tesla-selbststart", decisions=[stop(300 + 210)])
    assert within.find(result) == []
    result = result_of(cfg, "tesla-selbststart", decisions=[stop(300 + 211)])
    late = ["„vehicle.tesla“ bekam nach 300 s kein „Laden aus“ binnen 210 s."]
    assert within.find(result) == late


def test_no_deferred_counts_deferred_commands(cfg) -> None:
    none = check("tesla-aus", 1)
    assert none.text == "Kein zurückgestellter Befehl „charging“ an „vehicle.tesla“."

    def found(*decisions: Decision) -> list[str]:
        return none.find(result_of(cfg, "tesla-aus", decisions=list(decisions)))

    deferred = DecisionResult.DEFERRED
    assert found() == [] and found(stop(2700)) == []
    # Andere Art, anderer Aktor und andere Ergebnisse gehören nicht dazu.
    other_kind = decision(100, TESLA, CURRENT, 8.0, deferred)
    other_actuator = decision(100, EVCS, CHARGING, False, deferred)
    assert found(other_kind, other_actuator, stop(100, REJECTED)) == []
    assert found(stop(2800, deferred), stop(2701, deferred)) == [
        "2 zurückgestellte Befehle „charging“ an „vehicle.tesla“, der erste bei 2701 s."
    ]
    assert found(stop(2000, deferred)) == [
        "1 zurückgestellte Befehle „charging“ an „vehicle.tesla“, der erste bei 2000 s."
    ]


def test_the_tesla_must_not_charge_from_9_min(cfg) -> None:
    idle = check("tesla-selbststart", 1)
    assert idle.text == "Ab 9 min lädt „tesla“ nicht."

    def found(*amps: float) -> list[str]:
        samples = [cars_at(540 - 2 + t, a) for t, a in enumerate(amps)]
        return idle.find(result_of(cfg, "tesla-selbststart", samples=samples))

    assert found(7.0, 7.0, 0.0, 0.0) == []  # vor 540 s darf er laden
    assert found(0.0, 0.0, 0.0) == [] and found() == []
    assert found(0.0, 0.0, 0.0, 6.0) == ["„tesla“ lädt bei 541 s."]


def test_a_mode_change_is_a_new_version_of_the_valid_intent(cfg) -> None:
    from bindaems.tools.sim.scenarios import _Mode

    model = set_up(cfg, "tesla-aus")  # das Ereignis braucht vom Modell nichts
    ((at, event),) = actions("tesla-aus", _Mode)
    assert (at, event.version, event.vehicle, event.mode) == (2700, 2, "tesla", ChargeMode.OFF)
    current = Intent(
        version=1,
        control=True,
        vehicles={
            "tesla": VehicleIntent(mode=ChargeMode.PV, wallbox="twc"),
            "egolf": VehicleIntent(mode=ChargeMode.PV),
        },
    )
    rt = FakeRuntime(current)
    event(model, rt)  # type: ignore[arg-type]
    assert rt.sent == [
        current.model_copy(
            update={
                "version": 2,
                "vehicles": {
                    "tesla": VehicleIntent(mode=ChargeMode.OFF, wallbox="twc"),
                    "egolf": VehicleIntent(mode=ChargeMode.PV),
                },
            }
        )
    ]
    rt.reply = IntentReply("rejected", 1, ("Unbekanntes Fahrzeug „tesla“.",))
    with pytest.raises(ValueError, match="Moduswechsel abgelehnt: Unbekanntes Fahrzeug"):
        event(model, rt)  # type: ignore[arg-type]
    rt.current = None
    with pytest.raises(ValueError, match="Moduswechsel ohne gültige Absicht"):
        event(model, rt)  # type: ignore[arg-type]


def test_the_new_tesla_scenarios_follow_the_table_of_the_plan(cfg) -> None:
    from bindaems.tools.sim.scenarios import _SelfStart, _Weather

    aus, selbst = SCENARIOS["tesla-aus"], SCENARIOS["tesla-selbststart"]
    assert (aus.title, aus.duration_s, aus.live, aus.victron_live) == (
        "Tesla wird unter Wolken auf Aus gestellt",
        3600,
        True,
        False,
    )
    assert (selbst.title, selbst.duration_s, selbst.live, selbst.victron_live) == (
        "Tesla startet selbst im Modus Aus",
        1800,
        True,
        False,
    )
    assert aus.intent.vehicles["tesla"].mode is ChargeMode.PV
    assert selbst.intent.vehicles["tesla"].mode is ChargeMode.OFF
    assert [t for t, a in selbst.events if not isinstance(a, _Weather)] == [300]
    ((_, start),) = actions("tesla-selbststart", _SelfStart)
    assert start.vehicle == "tesla"
    model = set_up(cfg, "tesla-selbststart")
    car = model.teslas["tesla"]
    assert (car.wallbox, car.charge_enabled) == ("twc", False)
    assert model.cars["egolf"].wallbox is None


def test_every_scenario_has_described_checks() -> None:
    for scenario in SCENARIOS.values():
        assert isinstance(scenario.checks, Checks), scenario.name
        assert all(item.text.endswith(".") for item in scenario.checks.items)


def test_checks_report_all_findings_in_order(cfg) -> None:
    result = result_of(cfg, "tesla-5a")
    assert SCENARIOS["tesla-5a"].checks(result) == [
        "Keine Entscheidung mit „not_taken_retry“.",
        "„tesla“ lädt am Ende nicht, verlangt 5 A.",
    ]
