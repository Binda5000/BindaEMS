from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest
from tests.helpers import T0
from tests.unit.core.control.charge_helpers import CONTROLLABLE, evcs, inputs, twc

from bindaems.core.control.battery import BatteryCar, BatteryNeed
from bindaems.core.control.charge_inputs import ChargeInputs
from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.charging import CarView, ChargeController
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.last_values import ControlCommand
from bindaems.core.control.peak_guard import (
    NO_LIMITS,
    PEAK_EXCEEDED,
    PeakCar,
    PeakLimits,
    QuarterObs,
)
from bindaems.core.control.regulation import Regulation, RegulationInputs, battery_cars, peak_cars
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import Outcome, Verdict
from bindaems.shared.config import Config
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
from bindaems.shared.domain import Alarm, Severity

EGOLF_NOW = Intent(version=1, control=True, vehicles={"egolf": VehicleIntent(mode=ChargeMode.NOW)})
# Abends ohne PV ab 10:00 UTC (``T0``, der Beginn einer Viertelstunde): Der e-Golf lädt in Sofort
# mit 7360 W (16 A auf zwei Phasen), das Haus braucht 1720 W; beides kommt aus dem Netz.
EGOLF_W = 7360.0
DRAWN_W = 9080.0
QUARTER_END = T0 + timedelta(minutes=15)


def with_target(cfg: Config) -> Config:
    """Die Konfiguration mit dem Ziel 8 kW für den 15-min-Wächter (``config.example`` hat keins)."""
    control = cfg.control.model_copy(update={"peak_target_kw": 8.0})
    return cfg.model_copy(update={"control": control})


def quarter(s: int) -> QuarterObs:
    """Die Viertelstunde ab ``T0`` nach ``s`` Sekunden mit ``DRAWN_W`` aus dem Netz."""
    return QuarterObs(T0, DRAWN_W * s / 3600, DRAWN_W, {"evcs": EGOLF_W})


def peak_cycle(
    regulation: Regulation, cfg: Config, s: int, *, base_w: float = 0.0, **kw: Any
) -> list[ControlCommand]:
    """Ein Zyklus ``s`` Sekunden nach ``T0`` mit der Viertelstunde (``quarter``); jeder Befehl wird
    angenommen. ``base_w`` ist der Basis-Sollwert, ``kw`` geht an ``inputs``."""
    charge = inputs(cfg, s=s, evcs_obs=evcs(charging=True, power_w=EGOLF_W), intent=EGOLF_NOW, **kw)
    found = regulation.step(
        RegulationInputs(
            charge, pv_w=0.0, house_w=1720.0, base_setpoint_w=base_w, quarter=quarter(s)
        )
    )
    for command in (x.command for x in found):
        regulation.note_verdict(
            command, Verdict(command, Outcome.ACCEPTED, command.value, (), True)
        )
    return found


def currents(found: list[ControlCommand]) -> list[Command]:
    """Die Befehle für den Strom."""
    return [x.command for x in found if x.command.kind is CommandKind.CURRENT]


def charger_limits(regulation: Regulation, monkeypatch) -> list[PeakLimits]:
    """Die Grenzen des 15-min-Wächters, die die Laderegelung je ``step`` bekommt."""
    seen: list[PeakLimits] = []
    step = regulation.charger.step

    def spy(charge: ChargeInputs, peak: PeakLimits = NO_LIMITS) -> list[ControlCommand]:
        seen.append(peak)
        return step(charge, peak)

    monkeypatch.setattr(regulation.charger, "step", spy)
    return seen


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


def test_without_a_target_the_guard_rests(cfg) -> None:
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)  # ohne peak_target_kw
    for s in range(61):  # auch weit über 8 kW
        peak_cycle(regulation, cfg, s)
    assert regulation.alarms(T0 + timedelta(seconds=60)) == []
    assert regulation.grid_budget_w() is None
    assert regulation.health()["peak"]["state"] == "off"


# --- 15-min-Wächter (Plan 2c-2a, Präzisierungen 3, 9 bis 14 und 16) ------------------------------


def test_guard_limits_reach_the_charging_one_cycle_later(cfg) -> None:  # Präzisierung 3
    # Ziel 8 kW, ab dem Beginn der Viertelstunde 9080 W aus dem Netz: P_zul ist 7700 W. Der Wächter
    # drosselt den e-Golf sofort um 1380 W auf 5980 W, das sind 13 A auf zwei Phasen.
    peak = with_target(cfg)
    regulation = Regulation(peak, HardLimits.from_config(peak), started=T0)
    first = currents(peak_cycle(regulation, peak, 0))
    assert first == [Command("wallbox.evcs", CommandKind.CURRENT, 16.0, reasons=("mode_now",))]
    assert regulation.grid_budget_w() == 7700.0
    second = currents(peak_cycle(regulation, peak, 1))
    assert second == [
        Command("wallbox.evcs", CommandKind.CURRENT, 13.0, reasons=("mode_now", "peak_guard"))
    ]


def test_the_budget_counts_in_the_same_cycle(cfg) -> None:  # Präzisierungen 3 und 12
    # Mit dem Basis-Sollwert 1000 W wollte der Akku-Sollwert 8360 W. Das Budget P_zul (7700 W) gilt
    # schon in dem Zyklus, in dem der Wächter es bestimmt: W' = 7700 − 1000 = 6700 W.
    peak = with_target(cfg)
    regulation = Regulation(peak, HardLimits.from_config(peak), started=T0)
    victron = {**CONTROLLABLE, VICTRON: True}
    found = peak_cycle(regulation, peak, 0, base_w=1000.0, controllable=victron)
    assert found[-1].command == Command(
        VICTRON, CommandKind.GRID_SETPOINT, 7700.0, reasons=("battery_comp", "peak_guard")
    )
    assert found[-1].inputs["target_w"] == 6700.0


@pytest.mark.parametrize(("steerable", "caps"), [(True, {"egolf": 5980.0}), (False, {})])
def test_only_a_regulated_car_gets_a_cap(cfg, monkeypatch, steerable, caps) -> None:
    # Ist die EVCS nicht steuerbar, regelt die Laderegelung den Ladepunkt des e-Golf nicht: Eine
    # Grenze wirkte dort nie, und was er ihr nicht folgte, schmälerte die Drosselung der anderen.
    # Das Budget gilt in beiden Fällen.
    peak = with_target(cfg)
    regulation = Regulation(peak, HardLimits.from_config(peak), started=T0)
    seen = charger_limits(regulation, monkeypatch)
    for s in (0, 1):
        peak_cycle(regulation, peak, s, controllable={**CONTROLLABLE, "wallbox.evcs": steerable})
    assert seen == [NO_LIMITS, PeakLimits(budget_w=7700.0, caps_w=caps)]


def test_peak_cars_follow_the_charge_points() -> None:  # Präzisierungen 4 und 11
    views = (
        CarView("tesla", "twc", Effective.PV, 0.0, False, 3450.0, True),
        CarView("egolf", "evcs", Effective.MIN_PV, 4140.0, True, 2760.0, True),
    )
    # Der e-Golf hat 4140 W Fehlbetrag, 1000 W davon darf der Akku liefern: 3140 W Netzanteil.
    need = BatteryNeed(0.0, 0.0, 4140.0, {"twc": 0.0, "evcs": 4140.0}, {"evcs": 1000.0}, "on", 5.0)
    assert peak_cars(views, need) == [
        PeakCar("tesla", "twc", Effective.PV, 0, 0.0, 0.0, 3450.0),
        PeakCar("egolf", "evcs", Effective.MIN_PV, 1, 4140.0, 3140.0, 2760.0),
    ]
    # Ohne Bedarf hat kein Auto einen Netzanteil, ebenso ein Ladepunkt, der nicht geregelt wird.
    assert [car.grid_w for car in peak_cars(views, None)] == [0.0, 0.0]
    unregulated = (views[0], replace(views[1], regulated=False))
    assert [car.grid_w for car in peak_cars(unregulated, need)] == [0.0, 0.0]


def test_the_alarm_of_the_guard_ends_with_its_quarter(cfg) -> None:  # Präzisierung 14
    # 9080 W ab dem Beginn der Viertelstunde: Nach 60 s über dem Ziel ist sie überschritten.
    peak = with_target(cfg)
    regulation = Regulation(peak, HardLimits.from_config(peak), started=T0)
    for s in range(61):
        peak_cycle(regulation, peak, s)
    text = "15-min-Ziel überschritten: 9,1 kW in der Viertelstunde bis 12:15."
    alarm = Alarm(PEAK_EXCEEDED, Severity.WARNING, text, T0 + timedelta(seconds=60))
    assert regulation.alarms(T0 + timedelta(seconds=60)) == [alarm]
    # Die Regelung ruht, etwa in OBSERVE: Der Wächter macht keinen Schritt mehr, und doch endet der
    # Alarm mit seiner Viertelstunde.
    regulation.idle(T0 + timedelta(seconds=61))
    assert regulation.alarms(QUARTER_END - timedelta(microseconds=1)) == [alarm]
    assert regulation.alarms(QUARTER_END) == []


def test_idle_rests_the_guard_and_drops_its_limits(cfg, monkeypatch) -> None:  # Review Focus 4
    peak = with_target(cfg)
    regulation = Regulation(peak, HardLimits.from_config(peak), started=T0)
    seen = charger_limits(regulation, monkeypatch)
    peak_cycle(regulation, peak, 0)
    assert regulation.grid_budget_w() == 7700.0
    regulation.idle(T0 + timedelta(seconds=1))
    assert regulation.grid_budget_w() is None  # keins, solange die Regelung ruht
    assert regulation.health()["peak"]["state"] == "gap"
    # Nach der Pause ist die Viertelstunde lückenhaft: keine Grenze, kein Budget bis zur nächsten.
    peak_cycle(regulation, peak, 2)
    assert seen == [NO_LIMITS, NO_LIMITS]
    assert regulation.grid_budget_w() is None


def test_health_shows_the_guard(cfg) -> None:  # Präzisierung 16
    peak = with_target(cfg)
    regulation = Regulation(peak, HardLimits.from_config(peak), started=T0)
    assert regulation.health()["peak"]["state"] == "gap"  # vor dem ersten Schritt
    peak_cycle(regulation, peak, 0)
    assert regulation.health()["peak"] == {
        "state": "active",
        "target_kw": 8.0,
        "energy_kwh": 0.0,
        "projected_kw": 9.08,
        "budget_kw": 7.7,
        "reduction_kw": 1.38,
        "quarter_end": QUARTER_END.isoformat(),
    }
