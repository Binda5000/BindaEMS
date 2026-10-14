from dataclasses import replace
from datetime import datetime
from typing import Any

from tests.helpers import T0
from tests.unit.core.control.charge_helpers import CONTROLLABLE, evcs, inputs, twc

from bindaems.core.control.battery import BatteryCar
from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.charging import ChargeController
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.regulation import Regulation, RegulationInputs, battery_cars
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import Outcome, Verdict
from bindaems.shared.control import (
    VICTRON,
    ChargeMode,
    Command,
    CommandKind,
    GuestIntent,
    Intent,
    VehicleIntent,
)
from bindaems.shared.decisions import DecisionResult

EGOLF_NOW = Intent(version=1, control=True, vehicles={"egolf": VehicleIntent(mode=ChargeMode.NOW)})


def test_regulation_passes_the_charging_through(cfg) -> None:
    limits = HardLimits.from_config(cfg)
    regulation = Regulation(cfg, limits, started=T0)
    charger = ChargeController(cfg, limits, started=T0)
    found = regulation.step(RegulationInputs(inputs(cfg)))
    assert found  # „Laden aus“ für den e-Golf in PV-Überschuss
    assert found == charger.step(inputs(cfg))
    assert regulation.health()["vehicles"] == charger.health()
    assert regulation.vehicle_wallbox() == charger.vehicle_wallbox()


class SpyCharger:
    """Merkt sich jeden Aufruf, den der Kern an die Laderegelung weitergibt."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def note_verdict(self, command: Command, verdict: Verdict | None, *, not_sent: bool) -> None:
        self.calls.append(("note_verdict", command, verdict, not_sent))

    def note_result(self, result: WriteResult) -> None:
        self.calls.append(("note_result", result))

    def forget(self, actuators: object) -> None:
        self.calls.append(("forget", actuators))

    def idle(self, now: datetime) -> None:
        self.calls.append(("idle", now))


def test_regulation_hands_every_call_to_the_charging(cfg) -> None:
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    spy = SpyCharger()
    regulation.charger = spy  # type: ignore[assignment]
    command = Command("wallbox.evcs", CommandKind.CURRENT, 10.0)
    verdict = Verdict(command, Outcome.ACCEPTED, 10.0, (), True)
    result = WriteResult("wallbox.evcs", CommandKind.CURRENT, 10.0, DecisionResult.TIMEOUT, "")
    regulation.note_verdict(command, verdict, not_sent=True)
    regulation.note_verdict(command, None)
    regulation.note_result(result)
    regulation.forget({"wallbox.evcs"})
    regulation.idle(T0)
    assert spy.calls == [
        ("note_verdict", command, verdict, True),
        ("note_verdict", command, None, False),
        ("note_result", result),
        ("forget", {"wallbox.evcs"}),
        ("idle", T0),
    ]


class SpyBattery:
    """Merkt sich jeden Aufruf, den der Kern an den Akku-Sollwert weitergibt."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def note_verdict(self, command: Command, verdict: Verdict | None, *, not_sent: bool) -> None:
        self.calls.append(("note_verdict", command, verdict, not_sent))

    def note_result(self, result: WriteResult) -> None:
        self.calls.append(("note_result", result))

    def forget(self, actuators: object) -> None:
        self.calls.append(("forget", actuators))

    def idle(self) -> None:
        self.calls.append(("idle",))


def test_regulation_routes_the_override_to_the_battery(cfg) -> None:
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    charger, battery = SpyCharger(), SpyBattery()
    regulation.charger = charger  # type: ignore[assignment]
    regulation.battery = battery  # type: ignore[assignment]
    override = Command(VICTRON, CommandKind.GRID_SETPOINT, 2000.0)
    current = Command("wallbox.evcs", CommandKind.CURRENT, 10.0)
    accepted = Verdict(override, Outcome.ACCEPTED, 2000.0, (), True)
    judged = Verdict(current, Outcome.ACCEPTED, 10.0, (), True)
    ok = DecisionResult.OK
    own = WriteResult(VICTRON, CommandKind.GRID_SETPOINT, 2000.0, ok, "")
    # Ein anderer Wert an Victron, etwa die Entladegrenze des Keepers: nicht der Override
    limit = WriteResult(VICTRON, CommandKind.MAX_DISCHARGE, 0.0, ok, "")
    regulation.note_verdict(override, accepted)
    regulation.note_verdict(current, judged, not_sent=True)
    regulation.note_result(own)
    regulation.note_result(limit)
    regulation.forget({VICTRON})
    regulation.idle(T0)
    assert battery.calls == [
        ("note_verdict", override, accepted, False),
        ("note_result", own),
        ("forget", {VICTRON}),
        ("idle",),
    ]
    assert charger.calls == [
        ("note_verdict", current, judged, True),
        ("note_result", limit),
        ("forget", {VICTRON}),
        ("idle", T0),
    ]


def test_wallbox_without_vehicle_counts_with_the_guest_mode(cfg) -> None:  # Präzisierung 4
    # Der Tesla ist auf die EVCS übersteuert, der e-Golf hat damit keine Wallbox: Am Wall Connector
    # lädt ein Gast mit 2300 W.
    override = Intent(
        version=1,
        control=True,
        vehicles={"tesla": VehicleIntent(wallbox="evcs")},
        guest=GuestIntent(mode=ChargeMode.NOW),
    )
    charge = inputs(
        cfg, twc_obs=twc(connected=True, charging=True, power_w=2300.0), intent=override
    )
    charger = ChargeController(cfg, HardLimits.from_config(cfg), started=T0)
    charger.step(charge)
    found = battery_cars(cfg, charger.cars(), charge.wallboxes, ChargeMode.NOW)
    assert found == [
        BatteryCar("evcs", Effective.PV, 0.0, False),
        BatteryCar("twc", Effective.NOW, 2300.0, True),
    ]
    # Ohne Ladepunkte zählt jede Wallbox in der Reihenfolge von config.yaml mit dem Modus des
    # Gastes; eine, die die Beobachtungen nicht nennen, ist unbekannt.
    assert battery_cars(cfg, (), {"twc": charge.wallboxes["twc"]}, ChargeMode.OFF) == [
        BatteryCar("evcs", Effective.OFF, None, None),
        BatteryCar("twc", Effective.OFF, 2300.0, True),
    ]


def test_battery_commands_follow_the_charging_commands(cfg) -> None:  # Präzisierung 3
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    charge = inputs(
        cfg,
        evcs_obs=evcs(charging=True, power_w=2760.0),
        controllable={**CONTROLLABLE, VICTRON: True},
        intent=EGOLF_NOW,
    )
    found = regulation.step(RegulationInputs(charge, pv_w=0.0, house_w=500.0, base_setpoint_w=0.0))
    # Der e-Golf in Sofort ohne PV: 16 A und „Laden an“ an die EVCS, dahinter der Override, damit
    # der Akku seine 2760 W nicht liefert
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CommandKind.CURRENT, 16.0, reasons=("mode_now",)),
        Command("wallbox.evcs", CommandKind.CHARGING, True, reasons=("mode_now",)),
        Command(VICTRON, CommandKind.GRID_SETPOINT, 2760.0, reasons=("battery_comp",)),
    ]
    assert found[-1].inputs["cars_w"] == 2760.0
    health = regulation.health()["battery"]
    assert (health["state"], health["target_w"], health["support"]) == ("ok", 2760.0, "soc")


def test_no_override_without_a_target_or_while_victron_is_held(cfg) -> None:  # Präzisierung 8
    charge = inputs(
        cfg,
        evcs_obs=evcs(charging=True, power_w=2760.0),
        controllable={**CONTROLLABLE, VICTRON: True},
        intent=EGOLF_NOW,
    )
    usual = {"pv_w": 0.0, "house_w": 500.0, "base_setpoint_w": 0.0}
    writing = frozenset({(VICTRON, CommandKind.GRID_SETPOINT)})
    cases = [
        (RegulationInputs(charge, pv_w=0.0, house_w=500.0), "unknown"),  # ohne Basis
        (RegulationInputs(charge, **{**usual, "pv_w": None}), "unknown"),  # ohne PV
        (RegulationInputs(charge, **usual, locks={VICTRON: ("Dynamic ESS",)}), "locked"),
        (RegulationInputs(replace(charge, controllable=CONTROLLABLE), **usual), "locked"),
        (RegulationInputs(replace(charge, blocked=writing), **usual), "ok"),  # Schreiben offen
    ]
    for regulation_inputs, state in cases:
        regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
        found = regulation.step(regulation_inputs)
        assert VICTRON not in {x.command.actuator for x in found}
        assert regulation.health()["battery"]["state"] == state
    # Die Sperre eines anderen Aktors hält den Override nicht auf.
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    found = regulation.step(RegulationInputs(charge, **usual, locks={"wallbox.evcs": ("x",)}))
    assert found[-1].command.actuator == VICTRON


def test_regulation_has_no_alarms_and_no_budget_yet(cfg) -> None:
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    assert regulation.alarms(T0) == []
    assert regulation.grid_budget_w() is None
