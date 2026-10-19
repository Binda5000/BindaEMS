import math
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest
from structlog.testing import capture_logs
from tests.helpers import T0, snap
from tests.unit.core.control.charge_helpers import evcs, inputs, tesla, twc
from tests.unit.core.control.fakes import FakeActuator, FakeWakeActuator
from tests.unit.core.state.conftest import BASE

from bindaems.core.accounting.flows import SlotProgress, allocate, wb_key
from bindaems.core.control.actuator import Capabilities
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.last_values import ControlCommand
from bindaems.core.control.peak_guard import PEAK_EXCEEDED, QuarterObs
from bindaems.core.control.regulation import Regulation, RegulationInputs, ThermalInputs
from bindaems.core.regulation_link import CONTROL_EXCEPTION, THERMAL_EXCEPTION, RegulationLink
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import Outcome, Verdict
from bindaems.core.state.derived import PHASES, derive
from bindaems.shared.control import (
    ChargeMode,
    Command,
    CommandKind,
    Intent,
    OperatingState,
    VehicleIntent,
)
from bindaems.shared.decisions import DecisionResult
from bindaems.shared.domain import Alarm, Quality, Severity, Snapshot

NOW = T0
INTENT = Intent(version=1, control=True)
MESSAGE = "Regelung ausgesetzt: Ausnahme RuntimeError im Regelzyklus."
TEN = Command("wallbox.evcs", CommandKind.CURRENT, 10.0)
RESULT = WriteResult("wallbox.evcs", CommandKind.CURRENT, 10.0, DecisionResult.OK, "")
OK = DecisionResult.OK


def broken(*args: object, **kwargs: object) -> Any:
    raise RuntimeError("kaputt")


def link_for(cfg) -> RegulationLink:
    return RegulationLink(cfg, HardLimits.from_config(cfg), started=T0)


def step(link: RegulationLink, cfg, when: datetime = NOW, **kw: Any) -> list[ControlCommand]:
    """Ein Schritt mit leerem Snapshot (alles unbekannt), ``when`` auf beiden Uhren, ohne Flüsse
    und ohne Slot."""
    empty = snap({})
    args: dict[str, Any] = {
        "snap": empty,
        "derived": derive(empty, cfg),
        "now": when,
        "snow": when,
        "present": {},
        "capabilities": {},
        "actuators": {},
        "intent": INTENT,
        "accepted_at": None,
        "blocked": frozenset(),
        "connected": lambda source: None,
        "locks": {},
        "flows": None,
        "slot": None,
        "hourly_count": lambda actuator: 0,
        "operating": OperatingState.CONTROL,
    }
    return link.step(**(args | kw))


def cycle(link: RegulationLink, cfg, when: datetime, **kw: Any) -> None:
    """Ein Zyklus mit Regelung, wie ihn die Laufzeit führt (ohne Befehle auszuführen); ``kw`` geht
    an ``step``."""
    link.start_cycle()
    step(link, cfg, when, **kw)
    link.finish(when)


def idle_cycle(link: RegulationLink, when: datetime, *calls: Callable[[], object]) -> None:
    """Ein Zyklus ohne Regelung (START, OBSERVE, SAFE); ``calls`` laufen vor ``idle``."""
    link.start_cycle()
    for call in calls:
        call()
    link.idle(when)
    link.finish(when)


def kernel_idle(link: RegulationLink, monkeypatch) -> list[datetime]:
    """Die Zeiten, zu denen der Kern ``idle`` bekommt; er ruht dabei wirklich."""
    idled: list[datetime] = []
    original = link.regulation.idle

    def idle(now: datetime) -> None:
        idled.append(now)
        original(now)

    monkeypatch.setattr(link.regulation, "idle", idle)
    return idled


# Vorgaben des Plans


def test_a_throwing_kernel_fails_the_cycle_and_rests(cfg, monkeypatch) -> None:
    link = link_for(cfg)
    idled = kernel_idle(link, monkeypatch)
    monkeypatch.setattr(link.regulation, "step", broken)
    link.start_cycle()
    assert step(link, cfg) == []
    assert link.failed
    assert idled == [NOW]
    link.finish(NOW)
    assert link.alarms() == [Alarm(CONTROL_EXCEPTION, Severity.ERROR, MESSAGE, NOW)]


def test_the_alarm_keeps_the_start_of_the_streak_and_ends_with_a_good_cycle(
    cfg, monkeypatch
) -> None:
    link = link_for(cfg)
    with monkeypatch.context() as patch:
        patch.setattr(link.regulation, "step", broken)
        cycle(link, cfg, NOW)
        cycle(link, cfg, NOW + timedelta(seconds=1))
        assert link.alarms() == [Alarm(CONTROL_EXCEPTION, Severity.ERROR, MESSAGE, NOW)]
    cycle(link, cfg, NOW + timedelta(seconds=2))
    assert not link.failed
    assert link.alarms() == []


def test_the_alarm_outlasts_cycles_without_regulation(cfg, monkeypatch) -> None:
    # Präzisierung 15: Der Alarm gilt, solange der letzte Zyklus mit Regelung scheiterte; etwa in
    # SAFE, nachdem der Watchdog ohne Heartbeat auslöste.
    link = link_for(cfg)
    with monkeypatch.context() as patch:
        patch.setattr(link.regulation, "step", broken)
        cycle(link, cfg, NOW)
        cycle(link, cfg, NOW + timedelta(seconds=1))
    for s in (2, 3):  # ohne Regelung und ohne Ausnahme
        idle_cycle(link, NOW + timedelta(seconds=s))
        assert not link.failed
        assert link.alarms() == [Alarm(CONTROL_EXCEPTION, Severity.ERROR, MESSAGE, NOW)]
    cycle(link, cfg, NOW + timedelta(seconds=4))  # Regelung ohne Ausnahme: Die Folge endet
    assert link.alarms() == []


def test_an_exception_without_regulation_starts_or_continues_the_streak(cfg, monkeypatch) -> None:
    link = link_for(cfg)
    with monkeypatch.context() as patch:
        patch.setattr(link.regulation, "core_forget", broken)
        idle_cycle(link, NOW, lambda: link.forget({"wallbox.evcs"}))  # beginnt die Folge
    assert link.failed
    assert link.alarms() == [Alarm(CONTROL_EXCEPTION, Severity.ERROR, MESSAGE, NOW)]
    idle_cycle(link, NOW + timedelta(seconds=1))
    with monkeypatch.context() as patch:
        patch.setattr(link.regulation, "note_result", broken)
        idle_cycle(link, NOW + timedelta(seconds=2), lambda: link.note_result(RESULT))
    assert link.failed
    assert link.alarms() == [Alarm(CONTROL_EXCEPTION, Severity.ERROR, MESSAGE, NOW)]
    cycle(link, cfg, NOW + timedelta(seconds=3))
    assert link.alarms() == []


def test_a_throwing_note_verdict_fails_the_cycle(cfg, monkeypatch) -> None:
    link = link_for(cfg)
    idled = kernel_idle(link, monkeypatch)
    monkeypatch.setattr(link.regulation, "note_verdict", broken)
    link.start_cycle()
    step(link, cfg)
    link.note_verdict(TEN, Verdict(TEN, Outcome.ACCEPTED, 10.0, (), True))
    assert link.failed
    assert idled == [NOW]
    link.start_cycle()
    assert not link.failed


# Weitere Fälle


def test_step_builds_the_charge_inputs(cfg, monkeypatch) -> None:
    link = link_for(cfg)
    seen: list[RegulationInputs] = []
    monkeypatch.setattr(link.regulation, "step", lambda inputs: seen.append(inputs) or [])
    present = {("wallbox.evcs", CommandKind.CURRENT): 6.0}
    blocked = frozenset({("wallbox.evcs", CommandKind.CHARGING)})
    accepted = NOW - timedelta(hours=1)
    sources = {"evcs": False, "tessie:tesla": True}  # die EVCS getrennt, der Wall Connector nie
    link.start_cycle()
    step(
        link,
        cfg,
        now=NOW + timedelta(seconds=2),  # Wanduhr
        snow=NOW,  # Uhr der Safety-Schicht
        present=present,
        capabilities={"wallbox.evcs": Capabilities(True), "vehicle.tesla": Capabilities(False)},
        actuators={
            "wallbox.evcs": FakeActuator("wallbox.evcs"),
            "vehicle.tesla": FakeWakeActuator("vehicle.tesla", controllable=False),
        },
        accepted_at=accepted,
        blocked=blocked,
        connected=sources.get,
    )
    [charge] = [inputs.charge for inputs in seen]
    assert (charge.now, charge.wall_now) == (NOW, NOW + timedelta(seconds=2))
    assert charge.present == present
    assert charge.controllable == {"wallbox.evcs": True, "vehicle.tesla": False}
    assert charge.wakeable == {"wallbox.evcs": False, "vehicle.tesla": True}
    assert (charge.blocked, charge.intent, charge.accepted_at) == (blocked, INTENT, accepted)
    reachable = {key: obs.reachable for key, obs in charge.wallboxes.items()}
    assert reachable == {"evcs": False, "twc": True}
    assert charge.vehicles["tesla"].reachable is True


def test_the_commands_of_the_hour_reach_the_regulation(cfg, monkeypatch) -> None:  # Plan 2d
    link = link_for(cfg)
    seen: list[RegulationInputs] = []
    monkeypatch.setattr(link.regulation, "step", lambda inputs: seen.append(inputs) or [])
    asked: list[str] = []

    def count(actuator: str) -> int:
        asked.append(actuator)
        return 7

    link.start_cycle()
    step(link, cfg, hourly_count=count)
    assert seen[-1].charge.commands_hour == {"vehicle.tesla": 7}  # nur das Fahrzeug mit Tessie
    assert asked == ["vehicle.tesla"]


def test_the_home_state_reaches_the_regulation(cfg, monkeypatch) -> None:  # Plan 2c-2b
    link = link_for(cfg)
    seen: list[RegulationInputs] = []
    monkeypatch.setattr(link.regulation, "step", lambda inputs: seen.append(inputs) or [])
    current = snap({"vehicle.egolf.home_state": "home"})
    link.start_cycle()
    step(link, cfg, snap=current, derived=derive(current, cfg))
    assert seen[-1].charge.homes == {"egolf": True}
    link.start_cycle()
    step(link, cfg)  # leerer Snapshot: Das Fahrzeug fehlt nicht, sein Zustand ist unbekannt
    assert seen[-1].charge.homes == {"egolf": None}


def test_step_builds_the_inputs_of_the_battery(cfg, monkeypatch) -> None:  # Plan 2c-2a
    link = link_for(cfg)
    seen: list[RegulationInputs] = []
    monkeypatch.setattr(link.regulation, "step", lambda inputs: seen.append(inputs) or [])

    def built(current: Snapshot, **kw: Any) -> tuple[Any, ...]:
        link.start_cycle()
        step(link, cfg, snap=current, derived=derive(current, cfg), **kw)
        found = seen[-1]
        return found.pv_w, found.house_w, found.base_setpoint_w, found.locks

    # PV 3000 W; die Hauslast ist der Verbrauch (3000 − 1500) + (900 + 3000) W ohne die Wallboxen
    walls = {f"wallbox.{name}.power_w": 0.0 for name in cfg.wallboxes}
    locks = {"victron": ("Dynamic ESS ist aktiv.",)}
    current = snap(BASE | walls | {"ess.grid_setpoint_w": 50.0})
    assert built(current, locks=locks) == (3000.0, 5400.0, 50.0, locks)
    # Ohne Leistung der Wallboxen ist die Hauslast unbekannt; der Basis-Sollwert zählt nur als
    # endliche Zahl mit Qualität OK.
    assert built(snap(BASE | {"ess.grid_setpoint_w": math.inf})) == (3000.0, None, None, {})
    stale = snap(BASE | walls | {"ess.grid_setpoint_w": 50.0}, quality=Quality.STALE)
    assert built(stale) == (None, None, None, {})


def test_step_builds_the_quarter_of_the_guard(cfg, monkeypatch) -> None:  # Präzisierung 9
    link = link_for(cfg)
    seen: list[RegulationInputs] = []
    monkeypatch.setattr(link.regulation, "step", lambda inputs: seen.append(inputs) or [])

    def built(**kw: Any) -> QuarterObs | None:
        link.start_cycle()
        step(link, cfg, **kw)
        return seen[-1].quarter

    energy = {"grid>house": 2.0, "grid>wb:evcs": 1.0, "pv>house": 5.0, "battery>wb:evcs": 0.5}
    slot = SlotProgress(T0, energy, 2.0)
    flows = {"grid>house": 3600.0, "grid>wb:evcs": 1800.0, "grid>battery": 600.0, "pv>house": 900.0}
    # Energie bisher und Leistung des Zyklus zählen die Flüsse aus dem Netz, auch ins Auto und in
    # den Akku; je Wallbox in der Reihenfolge von config.yaml ihr Netzanteil des Zyklus, auch 0 W.
    expected = QuarterObs(T0, 3.0, 6000.0, {"evcs": 1800.0, "twc": 0.0})
    assert built(flows=flows, slot=slot) == expected
    # Ohne Flüsse (Bilanz unbekannt) ist die Leistung unbekannt, ohne Slot fehlt die Viertelstunde.
    assert built(flows=None, slot=slot) == QuarterObs(T0, 3.0, None, {})
    assert built(flows=flows, slot=None) is None


def test_the_boost_share_comes_after_pv_and_battery(cfg, monkeypatch) -> None:  # Präzisierung 9
    # Ohne PV lädt der Tesla am Wall Connector in Boost mit 11040 W, der e-Golf an der EVCS in
    # Sofort mit 7360 W; das Haus braucht 900 W. Der Akku gibt 11940 W ab, 7360 W kommen aus dem
    # Netz: Der Akku-Sollwert lässt Boost aus dem Akku laden und schickt den e-Golf ans Netz. Die
    # Flüsse der Abrechnung geben den Akku dagegen zuerst der EVCS (``wallbox_order``) und buchen
    # den Netzbezug auf den Wall Connector. Der Wächter zöge ihn so als Boost ab und sähe vom
    # e-Golf nichts.
    link = link_for(cfg)
    boost = VehicleIntent(boost_until=NOW + timedelta(hours=1))
    now = VehicleIntent(mode=ChargeMode.NOW)
    both = Intent(version=1, control=True, vehicles={"tesla": boost, "egolf": now})
    charge = inputs(
        cfg,
        intent=both,
        evcs_obs=evcs(charging=True, power_w=7360.0),
        twc_obs=twc(
            connected=True, charging=True, phase_a=dict.fromkeys(PHASES, 16.0), power_w=11040.0
        ),
        tesla_obs=tesla(plugged=True, charging=True),
    )
    link.regulation.step(RegulationInputs(charge))  # der Kern sieht den Tesla in Boost
    seen: list[RegulationInputs] = []
    monkeypatch.setattr(link.regulation, "step", lambda found: seen.append(found) or [])
    wallbox_w = {"evcs": 7360.0, "twc": 11040.0}
    derived = replace(
        derive(snap({}), cfg),
        pv_total_w=0.0,
        grid_w=7360.0,
        battery_w=-11940.0,
        house_load_w=900.0,
        wallbox_w=wallbox_w,
    )
    flows = allocate(0.0, 7360.0, -11940.0, 900.0, wallbox_w)  # die Flüsse der Abrechnung
    assert (flows.get(wb_key("grid", "evcs")), flows[wb_key("grid", "twc")]) == (None, 7360.0)
    link.start_cycle()
    slot = SlotProgress(T0, {}, 0.0)
    step(link, cfg, NOW + timedelta(seconds=1), derived=derived, flows=flows, slot=slot)
    # Die Summe bleibt die der Abrechnung; den Netzanteil je Wallbox rechnet der Link mit PV und
    # Akku zuerst für die Wallboxen in Boost.
    assert seen[-1].quarter == QuarterObs(T0, 0.0, 7360.0, {"evcs": 7360.0, "twc": 0.0})


def test_the_alarm_of_the_guard_ends_with_its_quarter_without_regulation(cfg) -> None:
    # Präzisierung 14: ``finish`` fragt die Alarme des Kerns in jedem Zyklus, auch ohne Regelung. So
    # endet „überschritten“ mit seiner Viertelstunde, obwohl der Wächter keinen Schritt mehr macht.
    control = cfg.control.model_copy(update={"peak_target_kw": 8.0})
    link = link_for(cfg.model_copy(update={"control": control}))
    for s in range(61):  # 9080 W aus dem Netz ab dem Beginn der Viertelstunde
        slot = SlotProgress(T0, {"grid>house": 9080.0 * s / 3600}, float(s))
        cycle(link, cfg, NOW + timedelta(seconds=s), flows={"grid>house": 9080.0}, slot=slot)
    [alarm] = link.alarms()
    assert (alarm.id, alarm.since) == (PEAK_EXCEEDED, NOW + timedelta(seconds=60))
    end = NOW + timedelta(minutes=15)
    idle_cycle(link, end - timedelta(seconds=1))  # die Regelung ruht, etwa in OBSERVE
    assert link.alarms() == [alarm]
    idle_cycle(link, end)
    assert link.alarms() == []


def test_health_shows_the_last_cycle_without_exception(cfg, monkeypatch) -> None:
    link = link_for(cfg)
    fresh = Regulation(cfg, HardLimits.from_config(cfg), started=T0).health()
    assert link.health() == fresh  # vor dem ersten ``finish``
    cycle(link, cfg, NOW)
    good = link.health()
    assert good != fresh
    monkeypatch.setattr(link.regulation, "step", broken)
    cycle(link, cfg, NOW + timedelta(seconds=1))
    assert link.regulation.health() == fresh  # der Kern ruht
    assert link.health() == good


@pytest.mark.parametrize(
    ("name", "call", "fallback"),
    [
        ("note_result", lambda link: link.note_result(RESULT), None),
        ("core_forget", lambda link: link.forget({"wallbox.evcs"}), None),
        ("vehicle_wallbox", lambda link: link.vehicle_wallbox(), {}),
        ("grid_budget_w", lambda link: link.grid_budget_w(), None),
        ("plan_inputs", lambda link: link.plan_inputs(), {}),
    ],
)
def test_every_call_of_the_kernel_is_guarded(cfg, monkeypatch, name, call, fallback) -> None:
    link = link_for(cfg)
    idled = kernel_idle(link, monkeypatch)
    monkeypatch.setattr(link.regulation, name, broken)
    link.start_cycle()
    assert call(link) == fallback
    assert link.failed
    assert idled == [T0]


def test_a_throwing_boost_wallboxes_fails_the_cycle_before_the_step(cfg, monkeypatch) -> None:
    # Präzisierung 15: Auch die Frage nach den Wallboxen in Boost (für die Viertelstunde des
    # Wächters) ist ein Aufruf des Kerns. Wirft sie, ruht der Kern, und ``step`` erreicht ihn nicht.
    link = link_for(cfg)
    idled = kernel_idle(link, monkeypatch)
    steps: list[RegulationInputs] = []
    monkeypatch.setattr(link.regulation, "boost_wallboxes", broken)
    monkeypatch.setattr(link.regulation, "step", lambda found: steps.append(found) or [])
    link.start_cycle()
    assert step(link, cfg) == []
    assert link.failed
    assert (idled, steps) == ([NOW], [])


@pytest.mark.parametrize("name", ["core_health", "alarms"])
def test_a_throwing_health_or_alarms_fails_the_cycle(cfg, monkeypatch, name) -> None:
    link = link_for(cfg)
    before = link.health()
    monkeypatch.setattr(link.regulation, name, broken)
    cycle(link, cfg, NOW)
    assert link.failed
    assert link.health() == before
    assert link.alarms() == [Alarm(CONTROL_EXCEPTION, Severity.ERROR, MESSAGE, NOW)]


def test_after_an_exception_the_kernel_rests_until_the_next_cycle(cfg, monkeypatch) -> None:
    link = link_for(cfg)
    steps: list[RegulationInputs] = []
    monkeypatch.setattr(link.regulation, "core_forget", broken)
    monkeypatch.setattr(link.regulation, "step", lambda inputs: steps.append(inputs) or [])
    link.start_cycle()
    link.forget({"wallbox.evcs"})
    assert step(link, cfg) == []
    assert steps == []
    link.start_cycle()
    step(link, cfg)
    assert len(steps) == 1


def test_a_throwing_idle_rebuilds_the_kernel(cfg, monkeypatch) -> None:
    link = link_for(cfg)
    old = link.regulation
    monkeypatch.setattr(old, "step", broken)
    monkeypatch.setattr(old, "idle", broken)
    link.start_cycle()
    assert step(link, cfg) == []
    assert link.failed
    assert link.regulation is not old
    rebuilt = link.regulation
    monkeypatch.setattr(rebuilt, "idle", broken)  # auch ohne Ausnahme davor
    link.start_cycle()
    link.idle(NOW)
    assert link.failed
    assert link.regulation is not rebuilt
    cycle(link, cfg, NOW + timedelta(seconds=1))  # der neue Kern regelt
    assert not link.failed


def test_the_log_shows_the_first_exception_of_a_streak_with_traceback(cfg, monkeypatch) -> None:
    link = link_for(cfg)
    with capture_logs() as logs, monkeypatch.context() as patch:
        patch.setattr(link.regulation, "step", broken)
        cycle(link, cfg, NOW)
        cycle(link, cfg, NOW + timedelta(seconds=1))
    with capture_logs() as later:
        cycle(link, cfg, NOW + timedelta(seconds=2))  # ohne Ausnahme: Die Folge endet
        monkeypatch.setattr(link.regulation, "step", broken)
        cycle(link, cfg, NOW + timedelta(seconds=3))
    assert [(e["log_level"], e.get("exc_info")) for e in logs] == [("error", True), ("error", None)]
    assert "kaputt" in logs[1]["error"]
    assert [e.get("exc_info") for e in later] == [True]


def test_step_builds_the_thermal_inputs(cfg, monkeypatch) -> None:  # Plan 6a, Task 8
    link = link_for(cfg)
    seen: list[RegulationInputs] = []
    stepped: list[RegulationInputs] = []
    monkeypatch.setattr(link.regulation, "step", lambda inputs: stepped.append(inputs) or [])
    monkeypatch.setattr(link.regulation, "thermal_step", lambda inputs: seen.append(inputs) or [])
    bits = {"running": True, "heating": False, "venting": False, "cooling": True}
    values = snap({f"thermal.klima.{name}": value for name, value in bits.items()})
    sources = {"thermal.klima": True}  # die Quelle des CoE-Adapters (``coe_source``)
    able = {"thermal.klima": Capabilities(True)}
    link.start_cycle()
    step(link, cfg, snap=values, connected=sources.get, capabilities=able)
    step(link, cfg, connected=sources.get, capabilities=able, locks={"thermal.klima": ("x",)})
    step(link, cfg, operating=OperatingState.DEGRADED)
    when = NOW.timestamp()
    assert [(inputs.thermal, inputs.degraded) for inputs in seen] == [
        ({"klima": ThermalInputs(True, "kuehlen", True, False, when)}, False),
        ({"klima": ThermalInputs(True, "unbekannt", True, True, when)}, False),
        ({"klima": ThermalInputs(False, "unbekannt", False, False, when)}, True),
    ]
    # Der Kern bekommt dieselben Eingaben ohne die der Klimaanlagen.
    assert [(inputs.thermal, inputs.degraded) for inputs in stepped] == [({}, False)] * 3
    assert [inputs.charge for inputs in stepped] == [inputs.charge for inputs in seen]


# Klimaanlage für sich (Plan 6a, Ruling R19)

OFFSET = Command("thermal.klima", CommandKind.SETPOINT_OFFSET, -0.5, reasons=("thermal_more",))
THERMAL_MESSAGE = "Regelung der Klimaanlage ausgesetzt: Ausnahme RuntimeError im Regelzyklus."


def test_thermal_commands_come_after_the_kernel(cfg, monkeypatch) -> None:
    link = link_for(cfg)
    monkeypatch.setattr(link.regulation, "step", lambda inputs: [ControlCommand(TEN, {})])
    monkeypatch.setattr(
        link.regulation, "thermal_step", lambda inputs: [ControlCommand(OFFSET, {})]
    )
    link.start_cycle()
    assert [x.command for x in step(link, cfg)] == [TEN, OFFSET]


def test_a_thermal_exception_leaves_cars_and_battery(cfg, monkeypatch) -> None:
    link = link_for(cfg)
    rested: list[bool] = []
    original = link.regulation.thermal_idle
    monkeypatch.setattr(link.regulation, "step", lambda inputs: [ControlCommand(TEN, {})])
    monkeypatch.setattr(link.regulation, "thermal_step", broken)
    monkeypatch.setattr(link.regulation, "thermal_idle", lambda: rested.append(True) or original())
    link.start_cycle()
    with capture_logs() as logs:
        found = step(link, cfg)
    # Die Befehle der Ladepunkte und des Akkus bleiben; nur die Klimaanlage ruht.
    assert [x.command for x in found] == [TEN]
    assert not link.failed and link.thermal_failed
    assert rested == [True]
    assert [entry["event"] for entry in logs] == [
        "Ausnahme in der Regelung der Klimaanlage, Klimaanlage ausgesetzt"
    ]
    link.finish(NOW)
    alarm = Alarm(THERMAL_EXCEPTION, Severity.WARNING, THERMAL_MESSAGE, NOW)
    assert link.alarms() == [alarm]
    # Die Folge geht weiter, mit einer Zeile im Log je weiterer Ausnahme ...
    link.start_cycle()
    with capture_logs() as logs:
        step(link, cfg, when=NOW + timedelta(seconds=1))
    link.finish(NOW + timedelta(seconds=1))
    events = [entry["event"] for entry in logs]
    assert events == ["Weitere Ausnahme in der Regelung der Klimaanlage"]
    assert link.alarms() == [alarm]
    # ... ein Zyklus ohne Regelung lässt den Alarm stehen, einer mit fehlerfreier Klimaanlage
    # beendet ihn.
    link.start_cycle()
    link.idle(NOW + timedelta(seconds=2))
    link.finish(NOW + timedelta(seconds=2))
    assert link.alarms() == [alarm]
    monkeypatch.setattr(link.regulation, "thermal_step", lambda inputs: [])
    link.start_cycle()
    step(link, cfg, when=NOW + timedelta(seconds=3))
    assert not link.thermal_failed
    link.finish(NOW + timedelta(seconds=3))
    assert link.alarms() == []


def test_a_thermal_exception_in_the_routing_leaves_the_kernel(cfg, monkeypatch) -> None:
    link = link_for(cfg)
    original = link.regulation.note_verdict

    def note_verdict(command: Command, verdict: Any, *, not_sent: bool = False) -> None:
        if command.actuator.startswith("thermal."):
            raise RuntimeError("kaputt")
        original(command, verdict, not_sent=not_sent)

    monkeypatch.setattr(link.regulation, "note_verdict", note_verdict)
    monkeypatch.setattr(link.regulation, "note_result", broken)
    link.start_cycle()
    step(link, cfg)
    link.note_verdict(OFFSET, None)
    assert link.thermal_failed and not link.failed
    link.note_verdict(TEN, None)  # die Laderegelung bekommt ihr Urteil weiter
    assert not link.failed
    link.start_cycle()
    link.note_result(WriteResult("thermal.klima", CommandKind.SETPOINT_OFFSET, -0.5, OK, ""))
    assert link.thermal_failed and not link.failed
    link.note_result(RESULT)
    assert link.failed


def test_a_failing_thermal_rest_rebuilds_nothing_else(cfg, monkeypatch) -> None:
    # Wirft auch ``thermal_idle``, bleibt der Kern, wie er ist; die Klimaanlage ruht für den Zyklus.
    link = link_for(cfg)
    kernel = link.regulation
    monkeypatch.setattr(kernel, "thermal_step", broken)
    monkeypatch.setattr(kernel, "thermal_idle", broken)
    link.start_cycle()
    step(link, cfg)
    assert link.thermal_failed and not link.failed
    assert link.regulation is kernel


def test_a_thermal_exception_in_forget_leaves_the_kernel(cfg, monkeypatch) -> None:
    # Die Klimaanlagen vergessen im eigenen Aufruf: Wirft er, vergisst der Kern trotzdem.
    link = link_for(cfg)
    forgotten: list[set[str]] = []
    monkeypatch.setattr(
        link.regulation, "core_forget", lambda actuators: forgotten.append(set(actuators))
    )
    monkeypatch.setattr(link.regulation, "thermal_forget", broken)
    link.start_cycle()
    link.forget({"wallbox.evcs", "thermal.klima"})
    assert forgotten == [{"wallbox.evcs", "thermal.klima"}]
    assert link.thermal_failed and not link.failed


def test_a_thermal_exception_in_health_leaves_the_kernel(cfg, monkeypatch) -> None:
    # Health der Klimaanlagen kommt im eigenen Aufruf: Wirft er, gilt der Stand des Kerns, und
    # ``thermal`` ist leer.
    link = link_for(cfg)
    monkeypatch.setattr(link.regulation, "thermal_health", broken)
    cycle(link, cfg, NOW)
    assert link.thermal_failed and not link.failed
    health = link.health()
    assert health["thermal"] == {}
    assert health == {**link.regulation.core_health(), "thermal": {}}
    assert link.alarms() == [Alarm(THERMAL_EXCEPTION, Severity.WARNING, THERMAL_MESSAGE, NOW)]
