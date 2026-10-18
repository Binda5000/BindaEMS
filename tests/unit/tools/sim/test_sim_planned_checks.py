"""Tests der Szenarien für „Geplant“ und das Ladelimit des Tesla (Plan 4b, Task 8): Aufbau,
Ereignisse und Prüfungen an Hand gebauter Ergebnisse. Die ganzen Läufe prüft
``tests/integration/test_sim_scenarios.py``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

import pytest

from bindaems.core.api.app import HealthReport
from bindaems.core.control.intent import IntentReply
from bindaems.shared.config import Config
from bindaems.shared.control import ChargeMode, CommandKind, Intent, VehicleIntent
from bindaems.shared.decisions import Decision, DecisionResult
from bindaems.shared.plan import BatteryIntent
from bindaems.shared.timeutil import LOCAL_TZ
from bindaems.tools.sim.metrics import CarSample, Sample, measure
from bindaems.tools.sim.model import PlantModel
from bindaems.tools.sim.runner import SimResult
from bindaems.tools.sim.scenarios import SCENARIOS, Checks, _Plan, _Target

PLANNED: Final = ["geplant-plan", "geplant-spaet", "geplant-pv", "geplant-unbekannt"]
NAMES: Final = [*PLANNED, "tesla-ladelimit"]
OK, DEFERRED = DecisionResult.OK, DecisionResult.DEFERRED
CHARGING, LIMIT = CommandKind.CHARGING, CommandKind.CHARGE_LIMIT
TESLA, EVCS = "vehicle.tesla", "wallbox.evcs"
QUIET: Final = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
SELF_USE: Final = BatteryIntent()  # EIGENVERBRAUCH


def vienna(month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=LOCAL_TZ)


def set_up(cfg: Config, name: str) -> PlantModel:
    model = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    SCENARIOS[name].setup(model)
    return model


def decision(
    name: str,
    seconds: float,
    actuator: str,
    kind: CommandKind,
    value: Any,
    *codes: str,
    **more: Any,
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
        result=more.get("result", OK),
        codes=codes,
        text="",
        inputs={},
    )


def car(amps: float = 0.0, soc: float = 60.0, *, complete: bool = False) -> CarSample:
    return CarSample(amps > 0, amps, amps * 3 * 230.0, "planned", 5, complete, soc_pct=soc)


def sample(t: int, *, tesla: CarSample | None = None, egolf: CarSample | None = None) -> Sample:
    cars = {"tesla": tesla or car(), "egolf": egolf or car()}
    return Sample(t, QUIET, QUIET, 0.0, 60.0, cars)


def health(**vehicle: Any) -> HealthReport:
    return HealthReport(
        mode="CONTROL",
        version="0",
        adapters=[],
        selfcheck=[],
        alarms=[],
        cycle_ms_p95=None,
        vehicles=[vehicle] if vehicle else [],
    )


def result_of(
    cfg: Config,
    name: str,
    *,
    samples: list[Sample] | None = None,
    decisions: list[Decision] | None = None,
    states: list[tuple[int, HealthReport]] | None = None,
    sent: dict[str, float] | None = None,
) -> SimResult:
    """Ein Ergebnis; ``sent`` nennt je Kennung einer Entscheidung die Sekunde, zu der ihr Befehl
    Tessie erreichte."""
    samples, decisions = samples or [], decisions or []
    metrics = measure(samples, decisions, cfg)
    start = SCENARIOS[name].start
    at = {key: start + timedelta(seconds=s) for key, s in (sent or {}).items()}
    return SimResult(SCENARIOS[name], metrics, decisions, states or [], samples, [], sent=at)


def check(name: str, index: int) -> Any:
    checks = SCENARIOS[name].checks
    assert isinstance(checks, Checks)
    return checks.items[index]


def at_latest_start(name: str, seconds: int) -> str:
    """Die späteste Startzeit ``seconds`` nach dem Beginn, wie die Gesundheit sie nennt (ISO)."""
    return (SCENARIOS[name].start + timedelta(seconds=seconds)).isoformat()


def targets(name: str) -> list[tuple[int, _Target]]:
    return [(t, a) for t, a in SCENARIOS[name].events if isinstance(a, _Target)]


def plans(name: str) -> list[tuple[int, _Plan]]:
    return [(t, a) for t, a in SCENARIOS[name].events if isinstance(a, _Plan)]


@dataclass
class FakeRuntime:
    """Was ``_Target`` von der Laufzeit braucht: die gültige Absicht und ``submit_intent``."""

    current: Intent | None
    reply: IntentReply = field(default_factory=lambda: IntentReply("accepted", 2))
    sent: list[Intent] = field(default_factory=list)

    def intent(self) -> Intent | None:
        return self.current

    def submit_intent(self, intent: Intent) -> IntentReply:
        self.sent.append(intent)
        return self.reply


# --- Aufbau und Ereignisse -----------------------------------------------------------------------


def test_the_planned_scenarios_follow_the_table_of_the_plan(cfg) -> None:
    evening, summer = vienna(10, 12, 18), vienna(6, 21, 10)
    table = {
        "geplant-plan": ("Geplant mit Plan, der Plan läuft ab", evening, 3600, 29),
        "geplant-spaet": ("Geplant ohne Plan, Start zur spätesten Startzeit", evening, 7200, 30),
        "geplant-pv": ("Geplant mit PV-Überschuss", summer, 7200, 31),
        "geplant-unbekannt": ("Geplant ohne Ladestand", evening, 7200, 32),
        "tesla-ladelimit": ("Ladelimit des Tesla an das Ziel angleichen", evening, 3600, 33),
    }
    assert list(SCENARIOS)[-5:] == NAMES == list(table)
    for name, (title, start, duration_s, seed) in table.items():
        scenario = SCENARIOS[name]
        found = (scenario.title, scenario.start, scenario.duration_s, scenario.seed)
        assert found == (title, start, duration_s, seed), name
        assert scenario.live and scenario.victron_live and scenario.intent.control, name
        assert scenario.intent.plan is None and dict(scenario.control) == {}, name
        expected = {"tesla": {"charge_limit_sync": True}} if name == "tesla-ladelimit" else {}
        assert {key: dict(values) for key, values in scenario.vehicles.items()} == expected
        key = "egolf" if name in ("geplant-spaet", "geplant-unbekannt") else "tesla"
        assert {k: entry.mode for k, entry in scenario.intent.vehicles.items()} == {
            key: ChargeMode.PLANNED
        }, name


def test_the_setups_place_one_car_with_its_soc(cfg) -> None:
    table = {
        # Name: Wallbox von Tesla und e-Golf, ihr SOC, Limit des Tesla, e-Golf in HA
        "geplant-plan": ("twc", None, 60.0, 50.0, 80.0, True),
        "geplant-spaet": (None, "evcs", 50.0, 50.0, 80.0, True),
        "geplant-pv": ("twc", None, 60.0, 50.0, 80.0, True),
        "geplant-unbekannt": (None, "evcs", 50.0, 50.0, 80.0, False),
        "tesla-ladelimit": ("twc", None, 65.0, 50.0, 60.0, True),
    }
    for name, expected in table.items():
        model = set_up(cfg, name)
        tesla, egolf = model.teslas["tesla"], model.cars["egolf"]
        found = (tesla.wallbox, egolf.wallbox, tesla.soc_pct, egolf.soc_pct, tesla.limit_pct)
        assert (*found, model.egolf_soc_known) == expected, name
        assert model.soc_pct == 60.0 and not tesla.asleep and not tesla.charge_enabled, name


def test_target_and_departure_come_as_version_2(cfg) -> None:
    table = {
        "geplant-plan": ("tesla", 70, "19:05"),
        "geplant-spaet": ("egolf", 70, "20:00"),
        "geplant-pv": ("tesla", 70, "07:00"),
        "geplant-unbekannt": ("egolf", 70, "23:15"),
        "tesla-ladelimit": ("tesla", 75, "19:00"),
    }
    for name, (key, target, departure) in table.items():
        ((at, event),) = targets(name)
        assert (at, event.version, event.vehicle, event.target, event.departure) == (
            0,
            2,
            key,
            target,
            departure,
        ), name
        # vor dem ersten Plan, der die nächste Version nimmt
        first = [a for t, a in SCENARIOS[name].events if t == 0 and isinstance(a, _Target | _Plan)]
        assert first[0] is event, name


def test_a_target_is_a_new_version_of_the_valid_intent(cfg) -> None:
    model = set_up(cfg, "geplant-spaet")  # das Ereignis braucht vom Modell nichts
    ((_, event),) = targets("geplant-spaet")
    current = Intent(
        version=1, control=True, vehicles={"egolf": VehicleIntent(mode=ChargeMode.PLANNED)}
    )
    rt = FakeRuntime(current)
    event(model, rt)  # type: ignore[arg-type]
    wanted = VehicleIntent(mode=ChargeMode.PLANNED, target_soc_pct=70, departure="20:00")
    assert rt.sent == [current.model_copy(update={"version": 2, "vehicles": {"egolf": wanted}})]
    rt.reply = IntentReply("rejected", 1, ("Unbekanntes Fahrzeug „egolf“.",))
    with pytest.raises(ValueError, match="Ziel abgelehnt: Unbekanntes Fahrzeug"):
        event(model, rt)  # type: ignore[arg-type]
    rt.current = None
    with pytest.raises(ValueError, match="Ziel ohne gültige Absicht"):
        event(model, rt)  # type: ignore[arg-type]


def test_the_plans_name_the_power_of_the_tesla() -> None:
    # „geplant-plan“: ein Plan mit 6900 W über vier Slots, gültig 30 min, danach keiner
    start = SCENARIOS["geplant-plan"].start
    slots = tuple((k, SELF_USE, None) for k in range(4))
    expected = _Plan(start, 0, slots, valid_s=1800, vehicles={"tesla": 6900})
    assert plans("geplant-plan") == [(0, expected)]
    plan = expected.plan()
    assert [slot.vehicles for slot in plan.slots] == [{"tesla": 6900}] * 4
    # „geplant-pv“: alle 15 min ein Plan wie von der app, der den Tesla mit 0 W nennt
    start = SCENARIOS["geplant-pv"].start
    found = plans("geplant-pv")
    assert [t for t, _ in found] == list(range(0, 7200, 900))
    for t, event in found:
        assert event == _Plan(
            start,
            t,
            tuple((t // 900 + k, SELF_USE, None) for k in range(4)),
            valid_s=2700,
            vehicles={"tesla": 0},
        )
    assert [slot.vehicles for slot in found[0][1].plan().slots] == [{"tesla": 0}] * 4
    for name in ("geplant-spaet", "geplant-unbekannt", "tesla-ladelimit"):
        assert plans(name) == [], name


def test_a_plan_without_cars_names_none(cfg) -> None:
    start = SCENARIOS["plan-halten"].start
    ((_, event), *_) = [(t, a) for t, a in SCENARIOS["plan-halten"].events if isinstance(a, _Plan)]
    assert dict(event.vehicles) == {}
    assert all(slot.vehicles == {} for slot in event.plan().slots)
    assert event.start == start


# --- Prüfungen -----------------------------------------------------------------------------------


def test_the_tesla_must_charge_with_at_least_9_a_until_the_target(cfg) -> None:
    keep = check("geplant-plan", 0)
    assert keep.text == "Vom ersten Laden bis zum Stopp am Ziel lädt „tesla“ mit mindestens 9 A."
    stop = decision("geplant-plan", 4, TESLA, CHARGING, False, "mode_planned", "target_reached")

    def found(
        *amps: float, decisions: list[Decision] | None = None, sent: dict[str, float] | None = None
    ) -> list[str]:
        samples = [sample(t, tesla=car(a)) for t, a in enumerate(amps)]
        result = result_of(cfg, "geplant-plan", samples=samples, decisions=decisions, sent=sent)
        return keep.find(result)

    assert found(0.0, 10.0, 16.0, 9.0, 0.0, 0.0, decisions=[stop]) == []
    assert found(0.0, 10.0, 16.0, 9.0, 4.0, decisions=[stop]) == []  # ab dem Stopp zählt nichts
    assert found(0.0, 10.0, 0.0, 10.0, decisions=[stop]) == [
        "„tesla“ lädt bei 2 s mit 0 A, verlangt mindestens 9 A (1 s darunter)."
    ]
    assert found(0.0, 10.0, 8.0, 8.0) == [  # ohne Stopp bis zum Ende
        "„tesla“ lädt bei 2 s mit 8 A, verlangt mindestens 9 A (2 s darunter)."
    ]
    assert found(0.0, 0.0) == ["„tesla“ lädt nie."]
    # Der Stopp erreichte Tessie bei 3 s, sein Ergebnis kam bei 4 s: Ab 3 s zählt nichts mehr
    assert found(0.0, 10.0, 16.0, 0.0, 0.0, decisions=[stop], sent={stop.id: 3.0}) == []
    assert found(0.0, 10.0, 0.0, 0.0, 0.0, decisions=[stop], sent={stop.id: 3.0}) == [
        "„tesla“ lädt bei 2 s mit 0 A, verlangt mindestens 9 A (1 s darunter)."
    ]
    assert found(0.0, 10.0, 16.0, 0.0, 0.0, decisions=[stop]) == [  # ohne Sendezeit: das Ergebnis
        "„tesla“ lädt bei 3 s mit 0 A, verlangt mindestens 9 A (1 s darunter)."
    ]


def test_the_tesla_charges_with_the_power_of_the_plan_while_it_holds(cfg) -> None:
    keep = check("geplant-plan", 2)
    assert keep.text == (
        "Vom ersten Strombefehl an „tesla“ bis 1800 s lädt es mit der Leistung des Plans, "
        "10 A ± 0,5 A, ab 3 s nach Strombefehl und erstem Laden (Antwortzeit von Tessie)."
    )
    current = decision("geplant-plan", 3, TESLA, CommandKind.CURRENT, 10.0, "mode_planned")
    held = decision(
        "geplant-plan", 1, TESLA, CommandKind.CURRENT, 16.0, "mode_planned", result=DEFERRED
    )

    def found(
        amps: dict[int, float],
        decisions: list[Decision],
        sent: dict[str, float] | None = None,
    ) -> list[str]:
        # bis 1800 s lädt der Tesla mit 10 A, außer in den Sekunden von ``amps``
        samples = [sample(t, tesla=car(amps.get(t, 10.0))) for t in range(1900)]
        result = result_of(cfg, "geplant-plan", samples=samples, decisions=decisions, sent=sent)
        return keep.find(result)

    assert found({0: 16.0, 1: 16.0, 1800: 16.0}, [current], {current.id: 2.0}) == []
    assert found({2: 16.0}, [current]) == []  # ohne Sendezeit ab dem Ergebnis, 3 s
    assert found({}, [held]) == ["Kein Strombefehl an „vehicle.tesla“."]
    assert found(dict.fromkeys(range(1900), 0.0), [current]) == ["„tesla“ lädt nie."]
    assert found({900: 16.0, 901: 16.0, 1500: 0.0}, [held, current]) == [
        "„tesla“ lädt bei 900 s mit 16 A, verlangt 10 A ± 0,5 A (3 s daneben)."
    ]
    assert found({10: 10.4, 11: 9.6}, [current]) == []
    # Der Strombefehl geht vor dem Start hinaus (2 s); der Tesla startet bei 4 s mit dem alten
    # Strom, Tessie übernimmt den neuen binnen ihrer Antwortzeit: ab 7 s zählt jede Sekunde
    starting = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0, 4: 16.0, 5: 16.0, 6: 16.0}
    assert found(starting, [current], {current.id: 2.0}) == []
    assert found({**starting, 7: 16.0}, [current], {current.id: 2.0}) == [
        "„tesla“ lädt bei 7 s mit 16 A, verlangt 10 A ± 0,5 A (1 s daneben)."
    ]


def test_the_car_must_stop_at_its_target_and_stay_off(cfg) -> None:
    stops = check("geplant-plan", 1)
    assert stops.text == (
        "„tesla“ bekommt „Laden aus“ mit „target_reached“ bei mindestens 70 % und lädt danach "
        "nicht mehr."
    )
    reached = ("mode_planned", "target_reached")
    stop = decision("geplant-plan", 2, TESLA, CHARGING, False, *reached)
    start = decision("geplant-plan", 3, TESLA, CHARGING, True, "mode_planned")

    def found(
        socs: list[float],
        amps: list[float],
        decisions: list[Decision],
        sent: dict[str, float] | None = None,
    ) -> list[str]:
        samples = [
            sample(t, tesla=car(a, s)) for t, (s, a) in enumerate(zip(socs, amps, strict=True))
        ]
        result = result_of(cfg, "geplant-plan", samples=samples, decisions=decisions, sent=sent)
        return stops.find(result)

    assert found([69.9, 70.0, 70.01, 70.01], [16, 16, 0, 0], [stop]) == []
    # Mit Sendezeit zählt der Ladestand, als der Stopp Tessie erreichte (1 s)
    assert found([69.9, 70.0, 70.01, 70.01], [16, 0, 0, 0], [stop], {stop.id: 1.0}) == []
    assert found([69.9, 69.98, 70.01, 70.01], [16, 0, 0, 0], [stop], {stop.id: 1.0}) == [
        "„tesla“ hat beim Stopp am Ziel (1 s) 69,98 %, verlangt mindestens 70 %."
    ]
    assert found([69.9, 69.95, 69.99, 69.99], [16, 16, 0, 0], [stop]) == [
        "„tesla“ hat beim Stopp am Ziel (2 s) 69,99 %, verlangt mindestens 70 %."
    ]
    assert found([69.9, 70.0, 70.01, 70.02], [16, 16, 0, 6], [stop, start]) == [
        "„tesla“ lädt nach dem Stopp am Ziel wieder, bei 3 s.",
        "1 Befehle „Laden an“ an „vehicle.tesla“ nach dem Stopp am Ziel, der erste bei 3 s.",
    ]
    deferred = decision("geplant-plan", 2, TESLA, CHARGING, False, *reached, result=DEFERRED)
    other = decision("geplant-plan", 2, TESLA, CHARGING, False, "mode_planned", "pv_surplus")
    assert found([70.0] * 3, [16, 0, 0], [deferred, other]) == [
        "Kein „Laden aus“ mit „target_reached“ an „vehicle.tesla“."
    ]
    # beim e-Golf an der EVCS geht der Stopp an die EVCS
    egolf = check("geplant-spaet", 1)
    assert egolf.text.startswith("„egolf“ bekommt „Laden aus“ an „wallbox.evcs“")


def test_the_egolf_waits_for_its_latest_start(cfg) -> None:
    waits = check("geplant-spaet", 0)
    assert waits.text == (
        "„egolf“ lädt erst ab der spätesten Startzeit, die die Gesundheit vor seinem ersten Laden "
        "nennt."
    )
    planned = {"state": "surplus", "latest_start": at_latest_start("geplant-spaet", 25)}
    states = [(t, health(id="egolf", planned=planned)) for t in (0, 10, 20)]

    def found(first: int | None, reports: list[tuple[int, HealthReport]] = states) -> list[str]:
        samples = [
            sample(t, egolf=car(16.0 if first is not None and t >= first else 0.0))
            for t in range(40)
        ]
        return waits.find(result_of(cfg, "geplant-spaet", samples=samples, states=reports))

    assert found(25) == [] and found(30) == []
    assert found(24) == [
        "„egolf“ lädt bei 24 s, vor der spätesten Startzeit (25 s laut Gesundheit bei 20 s)."
    ]
    assert found(None) == ["„egolf“ lädt nie."]
    assert found(25, [(0, health(id="egolf", planned=None))]) == [
        "Die Gesundheit nennt vor dem ersten Laden von „egolf“ (25 s) keine späteste Startzeit."
    ]


def test_the_egolf_must_reach_its_target_before_the_departure(cfg) -> None:
    reaches = check("geplant-spaet", 2)
    assert reaches.text == "„egolf“ erreicht 70 % vor der Abfahrt (120 min)."

    def found(*socs: float) -> list[str]:
        samples = [sample(7197 + t, egolf=car(16.0, soc)) for t, soc in enumerate(socs)]
        return reaches.find(result_of(cfg, "geplant-spaet", samples=samples))

    assert found(69.9, 70.0, 70.1) == []
    assert found(69.9, 69.98, 69.99, 70.2) == ["„egolf“ hat vor der Abfahrt höchstens 69,99 %."]


def test_the_unknown_soc_is_assumed_at_0_pct(cfg) -> None:
    assumed = check("geplant-unbekannt", 0)
    assert assumed.text == (
        "Jede Gesundheit, die einen Ladestand für „egolf“ nennt, nennt ihn „angenommen“ mit 0 %."
    )

    def found(*socs: tuple[float | None, str | None]) -> list[str]:
        states = [
            (t * 10, health(id="egolf", soc_pct=pct, soc_source=source))
            for t, (pct, source) in enumerate(socs)
        ]
        return assumed.find(result_of(cfg, "geplant-unbekannt", states=states))

    assert found((None, None), (0.0, "assumed"), (0.0, "assumed")) == []
    assert found((None, None)) == ["Die Gesundheit nennt für „egolf“ nie einen Ladestand."]
    assert found((0.0, "assumed"), (52.0, "measured")) == [
        "Bei 10 s nennt die Gesundheit für „egolf“ 52 % (measured), verlangt 0 % (assumed)."
    ]


def test_the_unknown_soc_starts_at_the_latest_start(cfg) -> None:
    starts = check("geplant-unbekannt", 1)
    assert starts.text == (
        "„egolf“ lädt erst ab der spätesten Startzeit, die die Gesundheit vor seinem ersten Laden "
        "nennt, und beginnt binnen 30 s danach."
    )
    planned = {"state": "surplus", "latest_start": at_latest_start("geplant-unbekannt", 5)}
    states = [(0, health(id="egolf", planned=planned))]

    def found(first: int) -> list[str]:
        samples = [sample(t, egolf=car(16.0 if t >= first else 0.0)) for t in range(60)]
        return starts.find(result_of(cfg, "geplant-unbekannt", samples=samples, states=states))

    assert found(5) == [] and found(35) == []
    assert found(36) == [
        "„egolf“ beginnt bei 36 s, 31 s nach der spätesten Startzeit, verlangt binnen 30 s."
    ]


def test_without_a_known_soc_the_target_is_never_reached(cfg) -> None:
    never = check("geplant-unbekannt", 2)
    assert never.text == "Keine Entscheidung nennt „target_reached“."
    stop = decision("geplant-unbekannt", 9, EVCS, CHARGING, False, "target_reached")
    assert never.find(result_of(cfg, "geplant-unbekannt")) == []
    assert never.find(result_of(cfg, "geplant-unbekannt", decisions=[stop])) == [
        "1 Entscheidungen nennen „target_reached“, die erste bei 9 s."
    ]


def test_the_pv_car_takes_little_from_the_battery_and_stops_at_its_target(cfg) -> None:
    battery, stops = check("geplant-pv", 0), check("geplant-pv", 1)
    assert battery.text == (
        "Höchstens 5 % der Energie von „tesla“ kommen aus dem Akku (ohne Übergänge)."
    )
    assert stops.text.startswith("„tesla“ bekommt „Laden aus“ mit „target_reached“ bei mindestens")


def test_exactly_one_charge_limit_of_75_pct(cfg) -> None:
    one = check("tesla-ladelimit", 0)
    assert (
        one.text
        == "Genau eine Entscheidung „Ladelimit 75 %“ an „tesla“, keine andere über das Ladelimit."
    )
    limit = decision("tesla-ladelimit", 70, TESLA, LIMIT, 75, "mode_planned", "charge_limit")
    other = decision("tesla-ladelimit", 700, TESLA, LIMIT, 80, "mode_planned", "charge_limit")
    deferred = decision("tesla-ladelimit", 80, TESLA, LIMIT, 75, "charge_limit", result=DEFERRED)

    def found(*decisions: Decision) -> list[str]:
        return one.find(result_of(cfg, "tesla-ladelimit", decisions=list(decisions)))

    assert found(limit) == [] and found(limit, deferred) == []
    assert found() == ["0 Entscheidungen „Ladelimit 75 %“ an „tesla“, verlangt genau eine."]
    assert found(limit, limit) == [
        "2 Entscheidungen „Ladelimit 75 %“ an „tesla“, verlangt genau eine."
    ]
    assert found(limit, other) == ["Eine andere Entscheidung über das Ladelimit: 80 % bei 700 s."]


def test_the_health_shows_the_new_limit_at_the_end(cfg) -> None:
    shown = check("tesla-ladelimit", 1)
    assert shown.text == "Am Ende zeigt die Gesundheit für „tesla“ das Ladelimit 75 %."

    def found(*limits: float | None) -> list[str]:
        states = [
            (t * 10, health(id="tesla", charge_limit_pct=pct)) for t, pct in enumerate(limits)
        ]
        return shown.find(result_of(cfg, "tesla-ladelimit", states=states))

    assert found(60.0, 75.0) == []
    assert found(75.0, 60.0) == ["Am Ende zeigt die Gesundheit für „tesla“ das Ladelimit 60 %."]
    assert found(75.0, None) == ["Am Ende zeigt die Gesundheit für „tesla“ kein Ladelimit."]
    assert found() == ["Die Gesundheit nennt „tesla“ nie."]


def test_the_tesla_must_complete_at_75_pct(cfg) -> None:
    complete = check("tesla-ladelimit", 2)
    assert complete.text == "Nach dem Laden meldet „tesla“ „Complete“ bei 75 %."

    def found(*cars: CarSample) -> list[str]:
        samples = [sample(t, tesla=c) for t, c in enumerate(cars)]
        return complete.find(result_of(cfg, "tesla-ladelimit", samples=samples))

    before = car(0.0, 65.0, complete=True)  # über dem alten Limit von 60 %
    assert found(before, car(16.0, 74.9), car(0.0, 75.0, complete=True)) == []
    assert found(before, car(16.0, 74.9), car(0.0, 80.0, complete=True)) == [
        "„tesla“ meldet „Complete“ bei 80 %, verlangt 75 %."
    ]
    assert found(before, car(16.0, 74.9), car(0.0, 74.9)) == [
        "„tesla“ meldet nach dem Laden nie „Complete“."
    ]
    assert found(before, before) == ["„tesla“ lädt nie."]
