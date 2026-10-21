from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest
from tests.helpers import T0
from tests.unit.core.control.charge_helpers import (
    CONTROLLABLE,
    PRESENT,
    evcs,
    grid,
    inputs,
    tesla,
    twc,
)

from bindaems.core.control.battery import SUPPORT_MODES, BatteryCar, BatteryController, BatteryNeed
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
from bindaems.core.control.plan_exec import ActiveSlot, plan_health
from bindaems.core.control.regulation import (
    THERMAL_BASE_SETTLE_S,
    THERMAL_BASE_TAU_S,
    THERMAL_RESYNC_S,
    Regulation,
    RegulationInputs,
    ThermalInputs,
    _Thermal,
    battery_cars,
    peak_cars,
    peak_wanted_w,
)
from bindaems.core.control.thermal import ThermalMode
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.ranges import check_range
from bindaems.core.safety.types import Outcome, Verdict
from bindaems.shared.config import CoeOffsetConfig, Config
from bindaems.shared.control import (
    VICTRON,
    ChargeMode,
    Command,
    CommandKind,
    GuestIntent,
    Intent,
    SupportIntent,
    VehicleIntent,
)
from bindaems.shared.decisions import DecisionResult
from bindaems.shared.domain import Alarm, Severity
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind, IntentPlan, IntentPlanSlot

EGOLF_NOW = Intent(version=1, control=True, vehicles={"egolf": VehicleIntent(mode=ChargeMode.NOW)})
# Abends ohne PV ab 10:00 UTC (``T0``, der Beginn einer Viertelstunde): Der e-Golf lädt in Sofort
# mit 7360 W (16 A auf zwei Phasen), das Haus braucht 1720 W; beides kommt aus dem Netz.
EGOLF_W = 7360.0
DRAWN_W = 9080.0
QUARTER_END = T0 + timedelta(minutes=15)
# Der Tesla lädt am Wall Connector mit 16 A auf drei Phasen (11040 W); beide Autos in Sofort.
TESLA_16_A = twc(
    connected=True,
    charging=True,
    phase_a=dict.fromkeys(("L1", "L2", "L3"), 16.0),
    power_w=11040.0,
)
BOTH_NOW = Intent(
    version=1,
    control=True,
    vehicles={
        "egolf": VehicleIntent(mode=ChargeMode.NOW),
        "tesla": VehicleIntent(mode=ChargeMode.NOW),
    },
)


def with_target(cfg: Config, target_kw: float = 8.0) -> Config:
    """Die Konfiguration mit dem Ziel ``target_kw`` für den 15-min-Wächter (``config.example`` hat
    keins)."""
    control = cfg.control.model_copy(update={"peak_target_kw": target_kw})
    return cfg.model_copy(update={"control": control})


def quarter(s: int, drawn_w: float = DRAWN_W) -> QuarterObs:
    """Die Viertelstunde ab ``T0`` nach ``s`` Sekunden mit ``drawn_w`` aus dem Netz; an der EVCS
    davon 7360 W (zählt nur in Boost)."""
    return QuarterObs(T0, drawn_w * s / 3600, drawn_w, {"evcs": EGOLF_W})


def peak_cycle(
    regulation: Regulation,
    cfg: Config,
    s: int,
    *,
    base_w: float = 0.0,
    pv_w: float = 0.0,
    drawn_w: float = DRAWN_W,
    lag: timedelta = timedelta(0),
    locks: Mapping[str, tuple[str, ...]] | None = None,
    **kw: Any,
) -> list[ControlCommand]:
    """Ein Zyklus ``s`` Sekunden nach ``T0`` mit der Viertelstunde (``quarter``); jeder Befehl wird
    angenommen.

    ``base_w`` ist der Basis-Sollwert, ``pv_w`` die PV, ``drawn_w`` der Netzbezug, ``locks`` die
    Sperren (ohne Angabe keine); die Uhr der Safety-Schicht geht um ``lag`` nach. ``kw`` ändert die
    Eingaben der Laderegelung (``inputs``), ohne Angabe der e-Golf in Sofort mit 7360 W an der EVCS.
    """
    given = {"evcs_obs": evcs(charging=True, power_w=EGOLF_W), "intent": EGOLF_NOW} | kw
    charge = inputs(cfg, s=s, **given)
    charge = replace(charge, now=charge.now - lag)
    found = regulation.step(
        RegulationInputs(
            charge,
            pv_w=pv_w,
            house_w=1720.0,
            base_setpoint_w=base_w,
            locks={} if locks is None else locks,
            quarter=quarter(s, drawn_w),
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

    def spy(
        charge: ChargeInputs, peak: PeakLimits = NO_LIMITS, active: ActiveSlot | None = None
    ) -> list[ControlCommand]:
        seen.append(peak)
        return step(charge, peak, active)

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
    assert regulation.health()["wallboxes"] == charger.wallbox_health()
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


def test_battery_cars_give_planned_no_support(cfg) -> None:  # Plan 4b, Präzisierung 8
    found = battery_cars(cfg, (), {}, ChargeMode.PLANNED)
    assert found
    assert {car.mode for car in found} == {Effective.PLANNED}
    assert Effective.PLANNED not in SUPPORT_MODES


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


def test_the_wanted_grid_counts_when_it_is_above_the_mean(cfg) -> None:  # Präzisierung 10
    # Der Akku gibt 2400 W ab, aus dem Netz kommen nur 6680 W. Mit dem Basis-Sollwert 600 W will
    # der Akku-Sollwert aber 600 + 7360 = 7960 W, unter der festen Grenze von Regel 2 (8000 W), und
    # danach rechnet der Wächter: 7960 − 7700 = 260 W Senkung. Im nächsten Zyklus geht der e-Golf
    # auf 7100 W, das sind 15 A.
    peak = with_target(cfg)
    regulation = Regulation(peak, HardLimits.from_config(peak), started=T0)
    victron = {**CONTROLLABLE, VICTRON: True}
    peak_cycle(regulation, peak, 0, base_w=600.0, drawn_w=6680.0, controllable=victron)
    assert regulation.health()["peak"]["reduction_kw"] == 0.26
    found = peak_cycle(regulation, peak, 1, base_w=600.0, drawn_w=6680.0, controllable=victron)
    assert currents(found) == [
        Command("wallbox.evcs", CommandKind.CURRENT, 15.0, reasons=("mode_now", "peak_guard"))
    ]


# Beide Autos laden in Sofort ohne PV: der e-Golf mit 7360 W an der EVCS, der Tesla mit 11040 W am
# Wall Connector. Sie wollen zusammen 18400 W aus dem Netz, weit über der festen Grenze von Regel 2.
BOTH_CHARGING = {
    "intent": BOTH_NOW,
    "twc_obs": TESLA_16_A,
    "tesla_obs": tesla(plugged=True, charging=True),
}
# Victron und beide Ladepunkte sind steuerbar: Der Wächter kann beide Autos drosseln.
ALL_STEERABLE = {**CONTROLLABLE, "vehicle.tesla": True, VICTRON: True}


@pytest.mark.parametrize("soc", [60.0, 22.0, None], ids=["budget", "no_budget", "unknown_soc"])
def test_with_p_zul_above_the_fixed_ceiling_the_guard_does_not_throttle(
    cfg, monkeypatch, soc
) -> None:
    # Präzisierung 10 und Design 2c-2, Entscheidung 3: Regel 2 begrenzt den Override auf
    # ``max_grid_charge_setpoint_w`` (8000 W), mit und ohne Budget für den Akku; was die Autos
    # darüber wollen, liefert der Akku. Der Netzbezug bleibt so bei 8000 W, unter P_zul eines Ziels
    # von 10 kW (9700 W): Der Wächter drosselt nicht, obwohl die Autos 18400 W aus dem Netz wollen,
    # auch ohne Budget (SOC 22 % oder unbekannt), wo er bei P_zul unter der Grenze die ganze Senkung
    # verlangte.
    peak = with_target(cfg, 10.0)
    regulation = Regulation(peak, HardLimits.from_config(peak), started=T0)
    seen = charger_limits(regulation, monkeypatch)
    kw = {"controllable": ALL_STEERABLE, "grid_obs": grid(soc=soc), **BOTH_CHARGING}
    for s in range(3):
        peak_cycle(regulation, peak, s, drawn_w=8000.0, **kw)
    assert [limits.caps_w for limits in seen] == [{}, {}, {}]
    assert regulation.health()["peak"]["reduction_kw"] == 0.0


@pytest.mark.parametrize(
    ("held", "caps"),
    [(None, {"egolf": 7060.0}), ("locked", {}), ("not controllable", {})],
)
def test_while_victron_cannot_act_only_the_mean_counts(cfg, monkeypatch, held, caps) -> None:
    # Präzisierung 10: Ziel 8 kW (P_zul 7700 W), beide Autos in Sofort wie oben, SOC 60 % (ein
    # Budget für den Akku), aus dem Netz kommen 6000 W. Kann Victron handeln, zählt der gewollte
    # Netzbezug bis zur festen Grenze von Regel 2 (8000 W), 300 W über P_zul: Der Wächter senkt den
    # e-Golf um 300 W auf 7060 W. Ist Victron gesperrt (etwa durch Dynamic ESS) oder nicht
    # steuerbar, setzt kein Override diesen Netzbezug durch; dann zählt nur das Mittel, und es liegt
    # unter P_zul.
    peak = with_target(cfg)
    regulation = Regulation(peak, HardLimits.from_config(peak), started=T0)
    seen = charger_limits(regulation, monkeypatch)
    victron = {**CONTROLLABLE, VICTRON: held != "not controllable"}
    locks = {VICTRON: ("Dynamic ESS ist aktiv.",)} if held == "locked" else {}
    for s in (0, 1):
        given = {"controllable": victron, **BOTH_CHARGING}
        peak_cycle(regulation, peak, s, drawn_w=6000.0, locks=locks, **given)
    assert seen[1].caps_w == caps


# Der Tesla lädt in Min+PV ohne PV mit seinem Mindeststrom von 5 A (3450 W), der e-Golf in Sofort.
TESLA_5_A = twc(
    connected=True,
    charging=True,
    phase_a=dict.fromkeys(("L1", "L2", "L3"), 5.0),
    power_w=3450.0,
)
MIN_PV_AND_NOW = Intent(
    version=1,
    control=True,
    vehicles={
        "egolf": VehicleIntent(mode=ChargeMode.NOW),
        "tesla": VehicleIntent(mode=ChargeMode.MIN_PV),
    },
)


@pytest.mark.parametrize(
    ("intent", "twc_obs"),
    [(MIN_PV_AND_NOW, TESLA_5_A), (BOTH_NOW, TESLA_16_A)],
    ids=["tesla_min_pv", "both_now"],
)
def test_with_a_budget_the_guard_cuts_once_by_the_gap_below_the_ceiling(
    cfg, monkeypatch, intent, twc_obs
) -> None:
    # Präzisierung 10 und Design 2c-2, Entscheidung 3: Ziel 8 kW, P_zul 7700 W liegt 300 W unter der
    # festen Grenze von Regel 2 (8000 W); der SOC 60 % gibt dem Akku ein Budget, und er hält den
    # Netzbezug bei 7700 W. Die Autos wollen 10810 W oder 18400 W aus dem Netz. Der Wächter senkt
    # einmal um 300 W, den e-Golf in Sofort auf 7060 W (der Tesla in Min+PV ist an seinem Boden, in
    # Sofort hat er die höhere Priorität). Ist der e-Golf mit 15 A (6900 W) gefolgt, senkt er über
    # fünf Wartezeiten nicht weiter: Was die Autos über 8000 W wollen, liefert der Akku wie ohne
    # Ziel.
    peak = with_target(cfg)
    regulation = Regulation(peak, HardLimits.from_config(peak), started=T0)
    seen = charger_limits(regulation, monkeypatch)
    given = {"intent": intent, "twc_obs": twc_obs, "tesla_obs": tesla(plugged=True, charging=True)}
    for s in range(151):
        egolf = evcs(charging=True, power_w=EGOLF_W if s < 2 else 6900.0)
        kw = {"controllable": ALL_STEERABLE, "evcs_obs": egolf, **given}
        peak_cycle(regulation, peak, s, drawn_w=7700.0, **kw)
    assert seen[0] == NO_LIMITS
    assert [limits.caps_w for limits in seen[1:]] == [{"egolf": 7060.0}] * 150


@pytest.mark.parametrize(
    ("target_kw", "reduction_kw", "caps"),
    [
        (8.0, 10.7, {"egolf": 2760.0, "tesla": 4940.0}),
        (6.0, 12.7, {"egolf": 2760.0, "tesla": 3450.0}),
    ],
)
def test_without_a_budget_the_guard_cuts_the_whole_reduction_at_once(
    cfg, monkeypatch, target_kw, reduction_kw, caps
) -> None:
    # Präzisierung 10: Der SOC 25 % liegt nicht über USV-Reserve + reserve_guard_pct (20 % + 5 %),
    # der Akku hat kein Budget. Regel 2 hält den Override dann bei der festen Grenze (8000 W), aus
    # dem Netz kommen 8000 W, und beide Autos wollen in Sofort 18400 W. Weil P_zul unter der Grenze
    # liegt, bringt den Netzbezug nur die ganze Senkung Basis + W − P_zul unter P_zul, in einem
    # Schritt und in der Reihenfolge: zuerst der e-Golf (die niedrigere Priorität) bis zu seinem
    # Boden, dann der Tesla. Bei 8 kW (P_zul 7700 W) sind das 10700 W; bei 6 kW (P_zul 5700 W)
    # 12700 W, mehr, als beide Autos bis zu ihrem Boden hergeben (4600 W + 7590 W).
    peak = with_target(cfg, target_kw)
    regulation = Regulation(peak, HardLimits.from_config(peak), started=T0)
    seen = charger_limits(regulation, monkeypatch)
    kw = {"controllable": ALL_STEERABLE, "grid_obs": grid(soc=25.0), **BOTH_CHARGING}
    peak_cycle(regulation, peak, 0, drawn_w=8000.0, **kw)
    assert regulation.health()["peak"]["reduction_kw"] == pytest.approx(reduction_kw)
    peak_cycle(regulation, peak, 1, drawn_w=8000.0, **kw)
    assert seen[1] == PeakLimits(budget_w=None, caps_w=caps)


@pytest.mark.parametrize(("steerable", "caps"), [(True, {"egolf": 5980.0}), (False, {})])
def test_only_a_regulated_car_gets_a_cap(cfg, monkeypatch, steerable, caps) -> None:
    # Ist die EVCS nicht steuerbar, regelt die Laderegelung den Ladepunkt des e-Golf nicht: Er fehlt
    # bei den Autos des Wächters (``peak_cars``), denn eine Grenze wirkte dort nie. Das Budget gilt
    # in beiden Fällen.
    peak = with_target(cfg)
    regulation = Regulation(peak, HardLimits.from_config(peak), started=T0)
    seen = charger_limits(regulation, monkeypatch)
    for s in (0, 1):
        peak_cycle(regulation, peak, s, controllable={**CONTROLLABLE, "wallbox.evcs": steerable})
    assert seen == [NO_LIMITS, PeakLimits(budget_w=7700.0, caps_w=caps)]


def test_a_car_whose_point_is_no_longer_regulated_loses_its_cap(cfg, monkeypatch) -> None:
    # Präzisierung 11: Beide Autos laden in Sofort. Die PV (9720 W) deckt das Haus und 8000 W des
    # Tesla, 10400 W kommen aus dem Netz. Der Wächter senkt zuerst den e-Golf (die niedrigere
    # Priorität) um 2700 W auf 4660 W. Ab 1 s ist die EVCS nicht steuerbar: Der e-Golf fehlt bei
    # den Autos des Wächters und verliert still seine Grenze. Nach 30 s bekommt der Tesla die ganze
    # verlangte Senkung; was der e-Golf seiner Grenze nicht folgte, zählt nicht mehr.
    peak = with_target(cfg)
    regulation = Regulation(peak, HardLimits.from_config(peak), started=T0)
    seen = charger_limits(regulation, monkeypatch)
    tesla_charging = {"twc_obs": TESLA_16_A, "tesla_obs": tesla(plugged=True, charging=True)}
    reduction_w: list[float] = []
    for s in range(32):
        steerable = {"wallbox.evcs": s == 0, "vehicle.tesla": True}
        kw = {"controllable": steerable, "intent": BOTH_NOW, **tesla_charging}
        peak_cycle(regulation, peak, s, pv_w=9720.0, drawn_w=10400.0, **kw)
        reduction_w.append(regulation.health()["peak"]["reduction_kw"] * 1000)
    assert seen[1].caps_w == {"egolf": 4660.0}
    assert [limits.caps_w for limits in seen[2:31]] == [{}] * 29  # still, ohne Änderung
    assert seen[31].caps_w == pytest.approx({"tesla": 11040.0 - reduction_w[30]})


def test_an_unregulated_car_in_boost_counts_for_the_boost_share(cfg) -> None:  # Präzisierung 9
    # Der e-Golf lädt in Boost an der EVCS, die nicht steuerbar ist: Seine 7360 W aus dem Netz
    # zählen trotzdem außerhalb des Budgets. Für die Hochrechnung bleibt das Haus mit 1720 W.
    peak = with_target(cfg)
    regulation = Regulation(peak, HardLimits.from_config(peak), started=T0)
    until = T0 + timedelta(hours=1)
    boost = Intent(version=1, control=True, vehicles={"egolf": VehicleIntent(boost_until=until)})
    unsteerable = {**CONTROLLABLE, "wallbox.evcs": False}
    peak_cycle(regulation, peak, 0, intent=boost, controllable=unsteerable)
    assert regulation.health()["peak"]["projected_kw"] == 1.72


def test_boost_wallboxes_name_the_wallboxes_of_the_cars_in_boost(cfg) -> None:  # Präzisierung 9
    # Der Tesla lädt am Wall Connector in Boost, der e-Golf an der EVCS in Sofort: Nur der Wall
    # Connector ist eine Wallbox in Boost, bis zum nächsten ``step``; vorher und nach ``idle``
    # keine. Ohne Boost (beide in Sofort) gibt es auch nach einem ``step`` keine.
    until = T0 + timedelta(hours=1)
    boost = Intent(
        version=1,
        control=True,
        vehicles={
            "tesla": VehicleIntent(boost_until=until),
            "egolf": VehicleIntent(mode=ChargeMode.NOW),
        },
    )
    charging = {"twc_obs": TESLA_16_A, "tesla_obs": tesla(plugged=True, charging=True)}
    egolf = evcs(charging=True, power_w=EGOLF_W)
    found: dict[str, frozenset[str]] = {}
    for name, intent in (("boost", boost), ("now", BOTH_NOW)):
        regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
        assert regulation.boost_wallboxes() == frozenset()
        regulation.step(RegulationInputs(inputs(cfg, intent=intent, evcs_obs=egolf, **charging)))
        found[name] = regulation.boost_wallboxes()
        regulation.idle(T0 + timedelta(seconds=1))
        assert regulation.boost_wallboxes() == frozenset()
    assert found == {"boost": frozenset({"twc"}), "now": frozenset()}


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
    assert [car.grid_w for car in peak_cars(views, None)] == [0.0, 0.0]  # ohne Bedarf
    # Ein Ladepunkt, den die Laderegelung nicht regelt, fehlt, außer in Boost; der Rang der
    # anderen bleibt ihre Stelle in ``cars``.
    unregulated = replace(views[0], regulated=False)
    found = peak_cars((unregulated, views[1]), need)
    assert [(car.vehicle, car.rank) for car in found] == [("egolf", 1)]
    boost = replace(unregulated, mode=Effective.BOOST)
    assert peak_cars((boost, views[1]), need)[0] == PeakCar(
        "tesla", "twc", Effective.BOOST, 0, 0.0, 0.0, 3450.0
    )


def test_peak_wanted_w_is_w_while_victron_can_act() -> None:  # Präzisierung 10
    # Beide Autos in Sofort mit 18400 W Fehlbetrag: Der Wächter bekommt W ungekappt, auch über der
    # festen Grenze von Regel 2 (die Regel mit und ohne Budget kennt er selbst); ohne Bedarf oder
    # solange Victron nicht handeln kann, nichts.
    need = BatteryNeed(0.0, 0.0, 18400.0, {"evcs": 7360.0, "twc": 11040.0}, {}, "soc", None)
    assert peak_wanted_w(need, acting=True, base_w=0.0, active=None) == 18400.0
    assert peak_wanted_w(None, acting=True, base_w=0.0, active=None) is None
    assert peak_wanted_w(need, acting=False, base_w=0.0, active=None) is None
    assert peak_wanted_w(need, acting=True, base_w=None, active=None) == 18400.0


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


def test_the_guard_runs_on_the_wall_clock(cfg) -> None:  # Global Constraints: Zeit
    # Die Uhr der Safety-Schicht geht einen Tag nach. Viertelstunde, Alarm und Health des Wächters
    # folgen der Wanduhr (``ChargeInputs.wall_now``), wie die Viertelstunden der Abrechnung.
    peak = with_target(cfg)
    regulation = Regulation(peak, HardLimits.from_config(peak), started=T0)
    for s in range(61):
        peak_cycle(regulation, peak, s, lag=timedelta(days=1))
    assert regulation.grid_budget_w() is not None  # die Viertelstunde der Abrechnung passt
    health = regulation.health()["peak"]
    assert (health["state"], health["quarter_end"]) == ("exceeded", QUARTER_END.isoformat())
    [alarm] = regulation.alarms(T0 + timedelta(seconds=60))
    assert alarm.since == T0 + timedelta(seconds=60)


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


def test_health_has_the_wallboxes(cfg) -> None:  # Plan 2c-2b, Präzisierung 11
    health = Regulation(cfg, HardLimits.from_config(cfg), started=T0).health()
    assert list(health) == [
        "intent_version",
        "vehicles",
        "battery",
        "peak",
        "wallboxes",
        "plan",
        "thermal",  # Plan 6a, vor dem ersten ``step`` leer
    ]
    assert health["thermal"] == {}


PLAN = IntentPlan(
    id=1001,
    created_at=T0 + timedelta(minutes=1),
    valid_until=T0 + timedelta(minutes=46),
    grid_charge_allowed=True,
    slots=[
        IntentPlanSlot(
            start=T0 + timedelta(minutes=15 * n),
            intent=BatteryIntent(
                kind=BatteryIntentKind.HALTEN if n == 1 else BatteryIntentKind.EIGENVERBRAUCH
            ),
        )
        for n in range(4)
    ],
)


def plan_cycle(regulation: Regulation, cfg: Config, s: int, plan: IntentPlan | None) -> None:
    intent = Intent(version=1, control=True, plan=plan)
    regulation.step(RegulationInputs(inputs(cfg, s=s, intent=intent)))


def test_regulation_health_has_the_plan(cfg) -> None:  # Plan 3c, Präzisierung 5
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    assert regulation.health()["plan"]["state"] == "none"  # vor dem ersten Schritt
    assert regulation.active() is None
    plan_cycle(regulation, cfg, 20 * 60, PLAN)  # 10:20
    active = regulation.active()
    assert active is not None and active.start == T0 + timedelta(minutes=15)
    assert regulation.health()["plan"] == plan_health(PLAN, active, T0 + timedelta(minutes=20))
    assert regulation.health()["plan"]["state"] == "valid"
    assert regulation.health()["plan"]["intent"] == "HALTEN"


def test_regulation_takes_the_slot_from_the_wall_clock_of_each_cycle(cfg) -> None:
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    plan_cycle(regulation, cfg, 30, PLAN)  # 10:00:30, vor created_at
    assert regulation.active() is None
    assert regulation.health()["plan"]["state"] == "expired"
    plan_cycle(regulation, cfg, 20 * 60, PLAN)
    assert regulation.active() is not None
    plan_cycle(regulation, cfg, 46 * 60, PLAN)  # valid_until ist offen
    assert regulation.active() is None
    assert regulation.health()["plan"]["state"] == "expired"
    plan_cycle(regulation, cfg, 47 * 60, None)  # Absicht ohne Plan
    assert regulation.health()["plan"]["state"] == "none"


def test_a_step_without_an_intent_has_no_plan(cfg) -> None:
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    plan_cycle(regulation, cfg, 20 * 60, PLAN)
    regulation.step(RegulationInputs(replace(inputs(cfg, s=21 * 60), intent=None)))
    assert regulation.active() is None
    assert regulation.health()["plan"]["state"] == "none"


def test_a_resting_regulation_reports_the_plan_by_its_validity(cfg) -> None:
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    plan_cycle(regulation, cfg, 20 * 60, PLAN)
    regulation.idle(T0 + timedelta(minutes=21))
    assert regulation.active() is None  # in Ruhe bindet kein Slot
    assert regulation.health()["plan"]["state"] == "none"  # ohne Angabe des Plans
    regulation.rest_plan(PLAN, T0 + timedelta(minutes=21))
    assert regulation.active() is None
    health = regulation.health()["plan"]
    assert health == plan_health(PLAN, None, T0 + timedelta(minutes=21))
    assert (health["state"], health["slot"]) == ("valid", None)  # gültig, aber nichts bindet
    regulation.rest_plan(PLAN, T0 + timedelta(minutes=50))
    assert regulation.health()["plan"]["state"] == "expired"
    regulation.rest_plan(None, T0 + timedelta(minutes=22))
    assert regulation.health()["plan"]["state"] == "none"


# --- Ausführung des Plans (Plan 3c, Präzisierung 7) ----------------------------------------------

TESLA_NOW = Intent(
    version=1,
    control=True,
    vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW)},
    plan=PLAN,
)


def test_hold_above_rule_2_is_clamped_downstream(cfg) -> None:  # Review Focus 4
    # Um 10:20 gilt HALTEN. Der Tesla lädt in Sofort mit 11040 W, das Haus braucht 960 W, keine PV:
    # Der Akku-Sollwert verlangt 12000 W aus dem Netz. Regel 2 der Safety-Schicht klemmt den
    # Override auf victron.max_grid_charge_setpoint_w (8000 W); den Rest liefert der Akku.
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    charge = inputs(
        cfg,
        s=20 * 60,
        intent=TESLA_NOW,
        twc_obs=TESLA_16_A,
        tesla_obs=tesla(plugged=True, charging=True),
        controllable={**CONTROLLABLE, VICTRON: True},
    )
    found = regulation.step(RegulationInputs(charge, pv_w=0.0, house_w=960.0, base_setpoint_w=0.0))
    override = found[-1]
    assert override.command == Command(
        VICTRON, CommandKind.GRID_SETPOINT, 12000.0, reasons=("plan_hold",)
    )
    assert override.inputs["plan_grid_charge_w"] == 0.0
    step = check_range(override.command, HardLimits.from_config(cfg), regulation.grid_budget_w())
    assert (step.value, [x.code for x in step.findings], step.reject) == (
        8000.0,
        ["range.ceiling"],
        False,
    )


def grid_charge(watts: float) -> ActiveSlot:
    intent = BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=watts)
    return ActiveSlot(1, T0, intent, None, None, None)


def budget_plan(budget_kw: float | None) -> IntentPlan:
    """Ein Plan ab ``T0`` mit dem Budget ``budget_kw`` im ersten Slot (10:00), im zweiten keins."""
    return IntentPlan(
        id=1002,
        created_at=T0,
        valid_until=T0 + timedelta(minutes=30),
        grid_charge_allowed=True,
        slots=[
            IntentPlanSlot(
                start=T0 + timedelta(minutes=15 * n),
                intent=BatteryIntent(kind=BatteryIntentKind.EIGENVERBRAUCH),
                budget_kw=budget_kw if n == 0 else None,
            )
            for n in range(2)
        ],
    )


def budget_run(cfg: Config, plan: IntentPlan | None) -> Regulation:
    """Eine Regelung nach dem Zyklus um 10:00:00 mit dem e-Golf in Sofort und dem Plan."""
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    intent = Intent(version=1, control=True, vehicles=EGOLF_NOW.vehicles, plan=plan)
    peak_cycle(regulation, cfg, 0, intent=intent)
    return regulation


def test_the_slot_power_reaches_the_charge_controller(cfg) -> None:  # Plan 4b, Task 6
    # Der gültige Slot nennt 4600 W für den e-Golf in „Geplant“: 10 A auf zwei Phasen.
    plan = IntentPlan(
        id=1003,
        created_at=T0,
        valid_until=T0 + timedelta(minutes=15),
        grid_charge_allowed=True,
        slots=[IntentPlanSlot(start=T0, intent=BatteryIntent(), vehicles={"egolf": 4600})],
    )
    planned = {"egolf": VehicleIntent(mode=ChargeMode.PLANNED)}
    intent = Intent(version=1, control=True, vehicles=planned, plan=plan)
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    calm = {"intent": intent, "evcs_obs": evcs(), "grid_obs": grid(grid_w=0.0)}  # ohne Überschuss
    found = peak_cycle(regulation, cfg, 0, **calm)
    reasons = ("mode_planned", "plan_power")
    assert [x.command for x in found if x.command.actuator == "wallbox.evcs"] == [
        Command("wallbox.evcs", CommandKind.CURRENT, 10.0, reasons=reasons),
        Command("wallbox.evcs", CommandKind.CHARGING, True, reasons=reasons),
    ]
    assert regulation.active().vehicles == {"egolf": 4600}  # type: ignore[union-attr]
    health = regulation.health()["vehicles"][1]["planned"]
    assert (health["state"], health["plan_w"]) == ("plan", 4600.0)
    # Nach dem Plan gibt es keinen Slot mehr: kein fester Bedarf aus dem Plan
    peak_cycle(regulation, cfg, 900, **calm)
    assert regulation.active() is None
    assert regulation.health()["vehicles"][1]["planned"]["plan_w"] is None


def test_regulation_uses_the_plan_budget(cfg) -> None:  # Plan 3c, Präzisierung 9
    # Ohne Ziel in der Konfiguration schaltet das Budget des Slots den Wächter ein:
    # P_zul = 5,5 kW · 0,25 h in 900 s, abzüglich der Marge von 300 W.
    regulation = budget_run(cfg, budget_plan(5.5))
    health = regulation.health()["peak"]
    assert (health["state"], health["target_kw"]) == ("active", 5.5)
    assert regulation.grid_budget_w() == 5200.0
    # Das Budget ersetzt das Ziel der Konfiguration.
    with_config = with_target(cfg, 8.0)
    regulation = budget_run(with_config, budget_plan(5.5))
    assert regulation.health()["peak"]["target_kw"] == 5.5
    # Ein Slot ohne Budget und ein Zyklus ohne Plan lassen das Ziel der Konfiguration gelten.
    for plan in (budget_plan(None), None):
        regulation = budget_run(with_config, plan)
        assert regulation.health()["peak"]["target_kw"] == 8.0
        assert regulation.health()["peak"]["state"] == "active"
    # Ohne Budget und ohne Ziel ruht der Wächter.
    for plan in (budget_plan(None), None):
        regulation = budget_run(cfg, plan)
        assert regulation.health()["peak"]["state"] == "off"
        assert regulation.grid_budget_w() is None


def test_the_plan_budget_ends_with_its_slot(cfg) -> None:
    # Im zweiten Slot (ab 10:15) hat der Plan kein Budget mehr: Ohne Ziel ruht der Wächter.
    regulation = budget_run(cfg, budget_plan(5.5))
    assert regulation.health()["peak"]["target_kw"] == 5.5
    intent = Intent(version=1, control=True, vehicles=EGOLF_NOW.vehicles, plan=budget_plan(5.5))
    peak_cycle(regulation, cfg, 900, intent=intent)
    assert regulation.health()["peak"]["state"] == "off"
    assert regulation.health()["peak"]["target_kw"] is None
    assert regulation.grid_budget_w() is None


def test_guard_sees_the_plan_target(cfg, monkeypatch) -> None:
    # Bei NETZLADEN bekommt der Wächter P plus den Fehlbetrag von Haus und Autos über der Basis;
    # im Eigenverbrauch wie bisher W, ohne Basis bei HALTEN und NETZLADEN nichts.
    need = BatteryNeed(0.0, 800.0, 0.0, {}, {}, "soc", None)
    assert peak_wanted_w(need, acting=True, base_w=0.0, active=grid_charge(3000.0)) == 3800.0
    assert peak_wanted_w(need, acting=True, base_w=300.0, active=grid_charge(3000.0)) == 3500.0
    assert peak_wanted_w(need, acting=True, base_w=None, active=grid_charge(3000.0)) is None
    assert peak_wanted_w(need, acting=False, base_w=0.0, active=grid_charge(3000.0)) is None
    # Im Regelzyklus: Um 10:20 gilt NETZLADEN mit 3000 W, das Haus braucht 1720 W.
    plan = PLAN.model_copy(
        update={
            "slots": [
                slot.model_copy(
                    update={
                        "intent": BatteryIntent(
                            kind=BatteryIntentKind.NETZLADEN, grid_charge_w=3000.0
                        )
                    }
                )
                if slot.intent.kind is BatteryIntentKind.HALTEN
                else slot
                for slot in PLAN.slots
            ]
        }
    )
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    seen: list[float | None] = []
    guard_step = regulation.peak_guard.step

    def spy(*args: Any, **kw: Any) -> PeakLimits:
        seen.append(kw["wanted_w"])
        return guard_step(*args, **kw)

    monkeypatch.setattr(regulation.peak_guard, "step", spy)
    intent = Intent(version=1, control=True, plan=plan)
    charge = inputs(cfg, s=20 * 60, intent=intent, controllable={**CONTROLLABLE, VICTRON: True})
    found = regulation.step(RegulationInputs(charge, pv_w=0.0, house_w=1720.0, base_setpoint_w=0.0))
    assert seen == [4720.0]
    assert found[-1].command == Command(
        VICTRON, CommandKind.GRID_SETPOINT, 4720.0, reasons=("plan_grid_charge",)
    )
    assert found[-1].inputs["plan_grid_charge_w"] == 3000.0


# --- Preis und Plan-Slot in den Entscheidungen (Plan 3c, Präzisierung 10) ------------------------


def priced_plan(budget_kw: float | None = 6.0, import_ct: float | None = 31.5) -> IntentPlan:
    """``PLAN`` (HALTEN im zweiten Slot, ab 10:15) mit Budget und Preis in jedem Slot."""
    return PLAN.model_copy(
        update={
            "slots": [
                slot.model_copy(update={"budget_kw": budget_kw, "import_ct": import_ct})
                for slot in PLAN.slots
            ]
        }
    )


def regulation_after_step(cfg: Config, plan: IntentPlan | None, s: int = 20 * 60) -> Regulation:
    """Eine Regelung nach einem Zyklus ``s`` Sekunden nach ``T0`` (Standard 10:20) mit ``plan``."""
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    plan_cycle(regulation, cfg, s, plan)
    return regulation


def test_plan_inputs_without_plan_are_empty(cfg) -> None:
    assert regulation_after_step(cfg, None).plan_inputs() == {}
    assert Regulation(cfg, HardLimits.from_config(cfg), started=T0).plan_inputs() == {}
    # Ein Plan ohne Slot dieser Viertelstunde (abgelaufen) bindet nicht.
    assert regulation_after_step(cfg, priced_plan(), s=46 * 60).plan_inputs() == {}


def test_plan_inputs_with_slot(cfg) -> None:
    found = regulation_after_step(cfg, priced_plan()).plan_inputs()
    assert found == {
        "plan_slot_ts": (T0 + timedelta(minutes=15)).timestamp(),
        "price_ct": 31.5,
        "budget_kw": 6.0,
    }
    assert all(isinstance(value, float) for value in found.values())


def test_plan_inputs_omit_what_is_unknown(cfg) -> None:
    slot_ts = (T0 + timedelta(minutes=15)).timestamp()
    # Ohne Preis und ohne Ziel (``config.example`` hat keins) bleibt der Beginn des Slots.
    found = regulation_after_step(cfg, priced_plan(budget_kw=None, import_ct=None)).plan_inputs()
    assert found == {"plan_slot_ts": slot_ts}
    # Ohne Budget im Slot ist das Ziel des Wächters das der Konfiguration.
    with_config = with_target(cfg, 8.0)
    found = regulation_after_step(with_config, priced_plan(budget_kw=None)).plan_inputs()
    assert found == {"plan_slot_ts": slot_ts, "price_ct": 31.5, "budget_kw": 8.0}
    # Das Budget des Slots ersetzt es.
    found = regulation_after_step(with_config, priced_plan(budget_kw=6.0)).plan_inputs()
    assert found["budget_kw"] == 6.0


def test_plan_inputs_end_with_the_rest_of_the_regulation(cfg) -> None:
    regulation = regulation_after_step(with_target(cfg, 8.0), priced_plan())
    assert regulation.plan_inputs() != {}
    regulation.idle(T0 + timedelta(minutes=21))
    assert regulation.plan_inputs() == {}  # ohne Slot auch kein Ziel des Wächters


# --- Unterstützung und Wächter unter dem Plan (Plan 3c, Präzisierung 8, Entscheidung 3) -----------

HOLD = ActiveSlot(1, T0, BatteryIntent(kind=BatteryIntentKind.HALTEN), None, None, None)


def guard_shares(cfg: Config, view: CarView, active: ActiveSlot | None) -> list[float]:
    """Der Netzanteil, den der Wächter für ``view`` bekommt, mit dem Bedarf bei SOC 80 %, ohne PV
    und mit 500 W Hauslast."""
    cars = battery_cars(cfg, (view,), {"twc": twc()}, ChargeMode.PV)
    need = BatteryController(cfg).assess(T0, 80.0, 0.0, 500.0, cars, active=active)
    return [car.grid_w for car in peak_cars((view,), need)]


def test_under_hold_the_guard_counts_a_support_car_fully_from_the_grid(cfg) -> None:
    # Der e-Golf in Min+PV mit 2760 W: Im Eigenverbrauch liefert der Akku alles (SOC 80 %), bei
    # HALTEN ist die Unterstützung gesperrt, und der ganze Fehlbetrag kommt aus dem Netz.
    view = CarView("egolf", "evcs", Effective.MIN_PV, 2760.0, True, 2760.0, True)
    assert guard_shares(cfg, view, None) == [0.0]
    assert guard_shares(cfg, view, HOLD) == [2760.0]
    assert guard_shares(cfg, view, grid_charge(3000.0)) == [2760.0]


def test_under_hold_the_guard_counts_a_boost_car_fully_from_the_grid(cfg) -> None:
    # Boost darf im Eigenverbrauch aus dem Akku laden (Netzanteil 0), bei HALTEN und NETZLADEN
    # lädt er aus dem Netz (Entscheidung 3).
    view = CarView("egolf", "evcs", Effective.BOOST, 7360.0, True, 2760.0, False)
    assert guard_shares(cfg, view, None) == [0.0]
    assert guard_shares(cfg, view, HOLD) == [7360.0]
    assert guard_shares(cfg, view, grid_charge(3000.0)) == [7360.0]


def test_regulation_hands_the_support_and_the_slot_to_the_battery(cfg) -> None:
    # Um 10:05 gilt Eigenverbrauch, um 10:20 HALTEN; an der EVCS lädt nichts in PV-Überschuss,
    # der SOC ist 60 %.
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    intent = Intent(version=1, control=True, plan=PLAN, support=SupportIntent(soc_pct=50.0))

    def cycle(s: int, given: Intent) -> dict[str, Any]:
        charge = inputs(cfg, s=s, intent=given)
        regulation.step(RegulationInputs(charge, pv_w=0.0, house_w=500.0, base_setpoint_w=0.0))
        battery: dict[str, Any] = regulation.health()["battery"]
        return battery

    found = cycle(5 * 60, intent)
    assert (found["support"], found["support_soc_pct"], found["support_source"]) == (
        "on",
        50.0,
        "intent",
    )
    assert cycle(20 * 60, intent)["support"] == "plan"
    off = intent.model_copy(update={"support": SupportIntent(allowed=False)})
    assert cycle(21 * 60, off)["support"] == "off"


# --- Klimaanlage (Plan 6a, Task 8) ----------------------------------------------------------------

KLIMA = "thermal.klima"
OFFSET = CommandKind.SETPOINT_OFFSET
OFFSET_KEY = (KLIMA, OFFSET)
INTENT = Intent(version=1, control=True)


@pytest.fixture
def reg_with_klima(cfg) -> Regulation:
    """Der Kern mit der Klimaanlage ``klima`` aus ``config.example`` (Stufe 0,5 K, 600 s)."""
    return Regulation(cfg, HardLimits.from_config(cfg), started=T0)


def accepted(command: Command) -> Verdict:
    return Verdict(command, Outcome.ACCEPTED, command.value, (), True)


def rejected(command: Command) -> Verdict:
    return Verdict(command, Outcome.REJECTED, None, (), False)


def klima(
    regulation: Regulation,
    cfg: Config,
    s: int,
    *,
    grid_w: float | None = -900.0,
    soc: float | None = 95.0,
    mode: ThermalMode = "kuehlen",
    fresh: bool = True,
    controllable: bool = True,
    locked: bool = False,
    degraded: bool = False,
    echo: float | None = None,
    blocked: frozenset[tuple[str, CommandKind]] = frozenset(),
    intent: Intent = INTENT,
    accept: bool = True,
    power_w: float | None = None,
    **kw: Any,
) -> list[ControlCommand]:
    """Ein Zyklus ``s`` Sekunden nach ``T0`` mit der Klimaanlage; zurück kommen nur ihre Befehle.

    Ohne Angabe: 900 W Einspeisung, SOC 95 %, Kühlen, frisch, steuerbar, kein Auto an der EVCS,
    kein Echo und unbekannte Leistung der Klimaanlage (``power_w``). ``accept`` nimmt jeden ihrer
    Befehle an; ``kw`` geht an ``inputs``.
    """
    base = kw.pop("present", PRESENT)
    present = dict(base) if echo is None else {**base, OFFSET_KEY: echo}
    given = {"evcs_obs": evcs(connected=False), "grid_obs": grid(grid_w=grid_w, soc=soc)} | kw
    charge = inputs(
        cfg,
        s=s,
        present=present,
        blocked=blocked,
        intent=intent,
        **given,
    )
    seen = ThermalInputs(fresh, mode, controllable, locked, charge.now.timestamp(), power_w)
    given_inputs = RegulationInputs(charge, thermal={"klima": seen}, degraded=degraded)
    found = regulation.step(given_inputs)
    assert all(x.command.actuator != KLIMA for x in found)  # die Klimaanlage nur in thermal_step
    ours = regulation.thermal_step(given_inputs)
    if accept:
        for x in ours:
            regulation.note_verdict(x.command, accepted(x.command))
    return ours


def klima_run(regulation: Regulation, cfg: Config, first: int, last: int, **kw: Any) -> list:
    """Zyklen alle 10 s von ``first`` bis ``last``; zurück kommen die Befehle mit ihrer Sekunde."""
    found = []
    for s in range(first, last + 1, 10):
        found += [(s, x) for x in klima(regulation, cfg, s, **kw)]
    return found


def offsets(found: list) -> list[tuple[int, float, tuple[str, ...]]]:
    return [(s, x.command.value, x.command.reasons) for s, x in found]


def klima_health(regulation: Regulation) -> dict[str, Any]:
    entry: dict[str, Any] = regulation.health()["thermal"]["klima"]
    return entry


def wish(regulation: Regulation) -> dict[str, Any]:
    """Wunsch, Grund und Hinweis aus dem Eintrag in Health."""
    entry = klima_health(regulation)
    return {key: entry[key] for key in ("wish", "reason", "hint")}


def test_regulation_emits_offset_after_override(reg_with_klima, cfg) -> None:
    # Der erste Zyklus bietet den Versatz 0 an (noch kein eigener Wert), danach nach 600 s mit
    # 900 W Einspeisung eine Stufe mehr: Kühlen mehr heißt −0,5 K.
    found = klima_run(reg_with_klima, cfg, 0, 600)
    assert offsets(found) == [(0, 0.0, ("thermal_off",)), (600, -0.5, ("thermal_more",))]
    assert found[-1][1] == ControlCommand(
        Command(KLIMA, OFFSET, -0.5, reasons=("thermal_more",)),
        {"thermal_steps": 1.0, "export_w": 900.0, "soc_pct": 95.0},
    )
    assert wish(reg_with_klima) == {"wish": 1, "reason": "thermal_more", "hint": "neutral"}
    # Heizen mehr heißt +0,5 K, nach dem Wechsel der Betriebsart erst 0.
    assert offsets(klima_run(reg_with_klima, cfg, 610, 1220, mode="heizen")) == [
        (610, 0.0, ("thermal_mode",)),
        (1220, 0.5, ("thermal_more",)),
    ]


def test_regulation_thermal_step_follows_the_step(reg_with_klima, cfg) -> None:
    # Der e-Golf in Sofort ohne PV: Ströme, „Laden an“ und der Override kommen aus ``step``; die
    # Klimaanlage rechnet danach in ``thermal_step`` (``RegulationLink`` hängt sie hinten an).
    charge = inputs(
        cfg,
        evcs_obs=evcs(charging=True, power_w=2760.0),
        controllable={**CONTROLLABLE, VICTRON: True},
        intent=EGOLF_NOW,
    )
    seen = {"klima": ThermalInputs(True, "kuehlen", True, False, T0.timestamp())}
    given = RegulationInputs(charge, pv_w=0.0, house_w=500.0, base_setpoint_w=0.0, thermal=seen)
    found = reg_with_klima.step(given)
    assert [(x.command.actuator, x.command.kind) for x in found] == [
        ("wallbox.evcs", CommandKind.CURRENT),
        ("wallbox.evcs", CommandKind.CHARGING),
        (VICTRON, CommandKind.GRID_SETPOINT),
    ]
    assert [(x.command.actuator, x.command.kind) for x in reg_with_klima.thermal_step(given)] == [
        (KLIMA, OFFSET)
    ]


def test_regulation_no_repeat_same_offset(reg_with_klima, cfg) -> None:
    klima_run(reg_with_klima, cfg, 0, 600)
    # Der Wunsch bleibt bei einer Stufe (Totband, 500 W Einspeisung): kein weiterer Befehl.
    assert klima_run(reg_with_klima, cfg, 610, 1300, grid_w=-500.0) == []
    # Ohne Urteil mit Wert (``None``, etwa zurückgestellt) kommt derselbe Wert im nächsten Zyklus
    # wieder.
    found = klima(reg_with_klima, cfg, 1310, grid_w=-500.0, mode="heizen", accept=False)
    assert [x.command.value for x in found] == [0.0]
    reg_with_klima.note_verdict(found[0].command, None)
    again = klima(reg_with_klima, cfg, 1311, grid_w=-500.0, mode="heizen")
    assert [x.command.value for x in again] == [0.0]
    assert klima(reg_with_klima, cfg, 1312, grid_w=-500.0, mode="heizen") == []


def test_regulation_idle_resets(reg_with_klima, cfg) -> None:
    klima_run(reg_with_klima, cfg, 0, 600)
    assert wish(reg_with_klima)["wish"] == 1
    reg_with_klima.idle(T0 + timedelta(seconds=601))
    assert reg_with_klima.health()["thermal"] == {}
    # Die Strategie beginnt neu bei 0 und vergisst den letzten Versatz: 0 kommt wieder, −0,5 K erst
    # nach weiteren 600 s Einspeisung.
    found = klima_run(reg_with_klima, cfg, 610, 1210)
    assert offsets(found) == [(610, 0.0, ("thermal_off",)), (1210, -0.5, ("thermal_more",))]


def test_regulation_forget_resets_the_strategy(reg_with_klima, cfg) -> None:
    klima_run(reg_with_klima, cfg, 0, 600)
    reg_with_klima.forget({"wallbox.evcs"})  # eine andere Aktor: Die Klimaanlage bleibt
    assert klima(reg_with_klima, cfg, 610) == []
    reg_with_klima.forget({KLIMA})  # etwa Dry-Run → live: Der Adapter hält jetzt 0
    assert offsets([(620, x) for x in klima(reg_with_klima, cfg, 620)]) == [
        (620, 0.0, ("thermal_off",))
    ]
    assert wish(reg_with_klima)["wish"] == 0


def test_regulation_resyncs_with_the_echo_after_a_gap(reg_with_klima, cfg) -> None:
    # Live: −0,5 K bestätigt (Echo −0,5). Nach einer Lücke der Freigabe hält der Adapter 0, das
    # Echo zeigt 0, die Regelung hielte −0,5 als letzten Wert und bliebe still.
    klima_run(reg_with_klima, cfg, 0, 600)
    reg_with_klima.note_result(WriteResult(KLIMA, OFFSET, -0.5, DecisionResult.OK, ""))
    assert klima(reg_with_klima, cfg, 610, echo=-0.5) == []
    # Während ein Schreibvorgang offen ist, zählt die Abweichung nicht.
    assert klima(reg_with_klima, cfg, 620, echo=0.0, blocked=frozenset({OFFSET_KEY})) == []
    start = 630
    assert klima(reg_with_klima, cfg, start, echo=0.0) == []
    assert klima(reg_with_klima, cfg, start + int(THERMAL_RESYNC_S) - 1, echo=0.0) == []
    assert wish(reg_with_klima)["wish"] == 1
    # Nach ``THERMAL_RESYNC_S`` beginnt die Strategie neu: Versatz 0 wie am Adapter, und die
    # nächste Stufe kommt wieder über die Schrittregel.
    found = klima(reg_with_klima, cfg, start + int(THERMAL_RESYNC_S), echo=0.0)
    assert [x.command.value for x in found] == [0.0]
    assert wish(reg_with_klima)["wish"] == 0
    later = klima_run(reg_with_klima, cfg, 700, 1300, echo=0.0)
    assert offsets(later) == [(1290, -0.5, ("thermal_more",))]  # 600 s nach dem Neubeginn


def test_regulation_resync_needs_a_lasting_mismatch(reg_with_klima, cfg) -> None:
    klima_run(reg_with_klima, cfg, 0, 600)
    reg_with_klima.note_result(WriteResult(KLIMA, OFFSET, -0.5, DecisionResult.OK, ""))
    klima(reg_with_klima, cfg, 610, echo=0.0)
    klima(reg_with_klima, cfg, 650, echo=-0.5)  # wieder gleich: Die Zeit beginnt neu
    assert klima(reg_with_klima, cfg, 680, echo=0.0) == []
    assert klima(reg_with_klima, cfg, 700, echo=None) == []  # ohne Echo kein Abgleich
    assert klima(reg_with_klima, cfg, 739, echo=0.0) == []
    assert wish(reg_with_klima)["wish"] == 1


def test_regulation_dry_run_has_no_resync(reg_with_klima, cfg) -> None:
    # Im Dry-Run bleibt das Echo 0, und es gibt kein Ergebnis ``OK``: kein Abgleich.
    klima_run(reg_with_klima, cfg, 0, 600)
    assert klima_run(reg_with_klima, cfg, 610, 1000, grid_w=-500.0, echo=0.0) == []
    assert wish(reg_with_klima)["wish"] == 1
    # Ein gescheitertes Ergebnis löscht die Bestätigung und den letzten Wert: −0,5 kommt wieder.
    reg_with_klima.note_result(WriteResult(KLIMA, OFFSET, -0.5, DecisionResult.OK, ""))
    reg_with_klima.note_result(WriteResult(KLIMA, OFFSET, -0.5, DecisionResult.TIMEOUT, ""))
    found = klima(reg_with_klima, cfg, 1010, grid_w=-500.0, echo=0.0)
    assert [x.command.value for x in found] == [-0.5]
    assert klima_run(reg_with_klima, cfg, 1020, 1200, grid_w=-500.0, echo=0.0) == []


def test_regulation_rejected_offset_restarts_the_strategy(reg_with_klima, cfg) -> None:
    klima_run(reg_with_klima, cfg, 0, 590)
    found = klima(reg_with_klima, cfg, 600, accept=False)
    assert [x.command.value for x in found] == [-0.5]
    reg_with_klima.note_verdict(found[0].command, rejected(found[0].command))
    # Die Schrittregel ließ −0,5 nicht zu: Der Wunsch geht auf 0, und 0 kommt als Nächstes.
    again = klima(reg_with_klima, cfg, 601)
    assert [x.command.value for x in again] == [0.0]
    assert wish(reg_with_klima)["wish"] == 0


def test_regulation_decides_the_offset_for_the_permit(reg_with_klima, cfg) -> None:
    # Ruling R26: Der Versatz, den die Regelung in diesem Zyklus entschieden hat, auch ohne Befehl
    # (offener Schreibvorgang, gleicher Wert); nach ihm gibt die Laufzeit frei.
    assert reg_with_klima.thermal_targets() == {}  # vor dem ersten Zyklus
    klima_run(reg_with_klima, cfg, 0, 600)
    assert reg_with_klima.thermal_targets() == {"klima": -0.5}
    assert klima(reg_with_klima, cfg, 610, blocked=frozenset({OFFSET_KEY})) == []
    assert reg_with_klima.thermal_targets() == {"klima": -0.5}
    # Wechsel auf Heizen, während der Schreibvorgang offen ist: kein Befehl, aber entschieden ist 0
    assert klima(reg_with_klima, cfg, 620, mode="heizen", blocked=frozenset({OFFSET_KEY})) == []
    assert reg_with_klima.thermal_targets() == {"klima": 0.0}
    reg_with_klima.idle(T0 + timedelta(seconds=630))
    assert reg_with_klima.thermal_targets() == {}


def test_regulation_rejected_offset_decides_zero(reg_with_klima, cfg) -> None:
    # Lehnt die Safety-Schicht einen Versatz außer 0 ab, beginnt die Strategie neu bei 0; der
    # entschiedene Versatz ist dann sofort 0, nicht erst im nächsten Zyklus.
    klima_run(reg_with_klima, cfg, 0, 590)
    found = klima(reg_with_klima, cfg, 600, accept=False)
    assert reg_with_klima.thermal_targets() == {"klima": -0.5}
    reg_with_klima.note_verdict(found[0].command, rejected(found[0].command))
    assert reg_with_klima.thermal_targets() == {"klima": 0.0}


def test_regulation_rejected_zero_keeps_the_strategy(reg_with_klima, cfg) -> None:
    klima_run(reg_with_klima, cfg, 0, 600)
    zero = Command(KLIMA, OFFSET, 0.0, reasons=("thermal_off",))
    reg_with_klima.note_verdict(zero, rejected(zero))
    assert klima(reg_with_klima, cfg, 610) == []  # −0,5 bleibt der letzte eigene Wert
    assert wish(reg_with_klima)["wish"] == 1


@pytest.mark.parametrize("kw", [{"controllable": False}, {"locked": True}])
def test_regulation_drops_the_wish_while_not_controllable(cfg, kw) -> None:
    # Nicht steuerbar (Quelle getrennt) oder gesperrt: kein Befehl, der Wunsch geht auf 0
    # (Entscheidung 5.1). Den gesendeten Versatz nimmt die Laufzeit mit der Freigabe zurück.
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    klima_run(regulation, cfg, 0, 600)
    assert klima_run(regulation, cfg, 610, 700, **kw) == []
    assert wish(regulation) == {"wish": 0, "reason": "thermal_stale", "hint": "neutral"}
    # Wieder steuerbar: 0 geht hinaus (wie am Adapter), die nächste Stufe erst nach 600 s.
    assert offsets(klima_run(regulation, cfg, 710, 1310)) == [
        (710, 0.0, ("thermal_stale",)),  # der Grund des letzten Rücksetzens bleibt stehen
        (1310, -0.5, ("thermal_more",)),
    ]


def test_regulation_keeps_stepping_while_the_offset_is_written(reg_with_klima, cfg) -> None:
    # Ein offener Schreibvorgang hält nur den Befehl zurück; die Strategie rechnet weiter.
    klima(reg_with_klima, cfg, 0)
    assert klima_run(reg_with_klima, cfg, 10, 700, blocked=frozenset({OFFSET_KEY})) == []
    assert wish(reg_with_klima) == {"wish": 1, "reason": "thermal_more", "hint": "neutral"}
    assert [x.command.value for x in klima(reg_with_klima, cfg, 710)] == [-0.5]


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"degraded": True}, "thermal_off"),
        ({"fresh": False}, "thermal_stale"),
        ({"mode": "lueften"}, "thermal_off"),
        ({"mode": "unbekannt"}, "thermal_off"),
    ],
)
def test_regulation_resets_to_zero(reg_with_klima, cfg, kw, reason) -> None:
    klima_run(reg_with_klima, cfg, 0, 600)
    found = klima(reg_with_klima, cfg, 610, **kw)
    assert [(x.command.value, x.command.reasons) for x in found] == [(0.0, (reason,))]


def test_regulation_peak_and_overload_press_the_wish(reg_with_klima, cfg, monkeypatch) -> None:
    klima_run(reg_with_klima, cfg, 0, 600)
    assert wish(reg_with_klima)["wish"] == 1
    # Der 15-min-Wächter macht Druck (``pressure``): Der positive Wunsch fällt auf 0.
    monkeypatch.setattr(reg_with_klima.peak_guard, "pressure", lambda: True)
    found = klima(reg_with_klima, cfg, 610)
    assert [(x.command.value, x.command.reasons) for x in found] == [(0.0, ("thermal_peak",))]

    # Überlast (eine Phase über der Sicherung seit 3 s) drückt ebenso.
    other = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    klima_run(other, cfg, 0, 600)
    over = grid(40.0, 2.0, 2.0, grid_w=-900.0, soc=95.0)
    found = []
    for s in range(610, 614):
        found += klima(other, cfg, s, grid_obs=over)
    assert other.charger.overload_level() > 0
    assert [(x.command.value, x.command.reasons) for x in found] == [(0.0, ("thermal_peak",))]


def klima_plan(thermal: str = "neutral", soc_end_pct: float | None = None) -> Intent:
    """Die Absicht mit einem Plan von ``T0`` an für zwei Stunden, jeder Slot Eigenverbrauch mit
    ``thermal`` und ``soc_end_pct``."""
    plan = IntentPlan(
        id=7,
        created_at=T0,
        valid_until=T0 + timedelta(hours=2),
        grid_charge_allowed=False,
        slots=[
            IntentPlanSlot(
                start=T0 + timedelta(minutes=15 * n),
                intent=BatteryIntent(kind=BatteryIntentKind.EIGENVERBRAUCH),
                thermal=thermal,
                soc_end_pct=soc_end_pct,
            )
            for n in range(8)
        ],
    )
    return Intent(version=1, control=True, plan=plan)


@pytest.mark.parametrize(
    ("intent", "soc", "more"),
    [
        (INTENT, 89.0, False),  # ohne Plan gilt die feste Schwelle 90 %
        (INTENT, 90.0, True),
        (klima_plan(soc_end_pct=60.0), 70.0, True),  # die Schwelle des Slots
        (klima_plan(soc_end_pct=80.0), 70.0, False),
        (klima_plan(), 89.0, False),  # der Slot nennt keine: 90 %
        (klima_plan("mehr"), 50.0, True),  # „mehr“: keine Akku-Schwelle
    ],
)
def test_regulation_takes_gate_and_hint_from_the_slot(
    reg_with_klima, cfg, intent: Intent, soc: float, more: bool
) -> None:
    found = klima_run(reg_with_klima, cfg, 0, 600, soc=soc, intent=intent)
    assert (found[-1][1].command.value == -0.5) is more
    hint = "neutral" if intent.plan is None else intent.plan.slots[0].thermal
    assert wish(reg_with_klima)["hint"] == hint


def test_regulation_less_with_the_hint_of_the_slot(reg_with_klima, cfg) -> None:
    # „weniger“ ohne Einspeisung: nach 600 s eine Stufe weniger, beim Kühlen +0,5 K
    found = klima_run(reg_with_klima, cfg, 0, 600, grid_w=200.0, intent=klima_plan("weniger"))
    assert offsets(found)[-1] == (600, 0.5, ("thermal_less",))
    assert found[-1][1].inputs == {"thermal_steps": -1.0, "export_w": 0.0, "soc_pct": 95.0}


def test_regulation_cars_go_first(reg_with_klima, cfg) -> None:
    klima_run(reg_with_klima, cfg, 0, 600)
    # Der e-Golf in PV folgt 6 A (2760 W) unter seinem Höchststrom: eine Stufe zurück, wieder 0.
    egolf = evcs(charging=True, power_w=2760.0)
    present = {**PRESENT, ("wallbox.evcs", CommandKind.CHARGING): True}
    found = klima(reg_with_klima, cfg, 610, evcs_obs=egolf, present=present)
    assert reg_with_klima.charger.unmet_surplus_w() >= 230.0
    assert [(x.command.value, x.command.reasons) for x in found] == [(0.0, ("thermal_cars",))]


def held_car_offsets(cfg: Config, power_w: float | None, base_w: float | None = None) -> list:
    """Die Klimaanlage steht bei einer Stufe mehr (−0,5 K), davor meldete sie ``base_w`` (ohne:
    unbekannt); dann steckt der e-Golf in PV an, bei 1500 W Einspeisung. Die Regelung hält ihn bei
    „Laden aus“ (jedes Urteil nimmt an); die Klimaanlage meldet ``power_w``. Zurück kommen ihre
    Befehle ab dann mit ihrer Sekunde."""
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    klima_run(regulation, cfg, 0, 600, power_w=base_w)
    found = []
    for s in range(610, 1300, 10):
        charge = inputs(
            cfg,
            s=s,
            evcs_obs=evcs(charging=False, power_w=0.0),
            grid_obs=grid(grid_w=-1500.0, soc=95.0),
            intent=INTENT,
        )
        seen = ThermalInputs(True, "kuehlen", True, False, charge.now.timestamp(), power_w)
        given = RegulationInputs(charge, thermal={"klima": seen})
        for x in regulation.step(given):
            regulation.note_verdict(x.command, accepted(x.command))
        for x in regulation.thermal_step(given):
            regulation.note_verdict(x.command, accepted(x.command))
            found.append((s, x.command.value, x.command.reasons))
    return found


def test_regulation_a_held_car_goes_first_only_with_its_start_power(cfg) -> None:  # R23 c
    # Der e-Golf braucht für den Start 6 A auf zwei Phasen, 2760 W. Erreichen Einspeisung (1500 W)
    # und die Leistung der Klimaanlage (1300 W) das, könnte er starten, sobald sie weicht: eine
    # Stufe zurück (thermal_cars).
    assert held_car_offsets(cfg, 1300.0)[0] == (620, 0.0, ("thermal_cars",))
    # Mit 1200 W oder unbekannter Leistung (0) startet er nie: Die Klimaanlage nimmt den
    # Überschuss und geht nach 600 s eine Stufe weiter.
    assert held_car_offsets(cfg, 1200.0) == [(1200, -1.0, ("thermal_more",))]
    assert held_car_offsets(cfg, None) == [(1200, -1.0, ("thermal_more",))]


def test_regulation_counts_only_the_power_the_klima_can_free(cfg) -> None:  # Ruling R28
    # Ohne Versatz nahm die Klimaanlage 600 W (gelernt, solange der Versatz 0 war); bei −0,5 K
    # nimmt sie 1300 W. Freigeben kann die Strategie nur die 700 W darüber: mit 1500 W
    # Einspeisung 2200 W, unter der Startleistung des e-Golf (2760 W). Er zählt nicht, und sie
    # geht nach 600 s eine Stufe weiter.
    assert held_car_offsets(cfg, 1300.0, base_w=600.0) == [(1200, -1.0, ("thermal_more",))]
    # Kann sie 1300 W freigeben (1900 W bei −0,5 K), reicht es für den Start: Sie weicht.
    assert held_car_offsets(cfg, 1900.0, base_w=600.0)[0] == (620, 0.0, ("thermal_cars",))


def test_klima_power_without_offset_is_learned_after_it_settled(cfg) -> None:  # Ruling R28
    # Gelernt wird die Leistung bei Versatz 0 erst, wenn er THERMAL_BASE_SETTLE_S lang 0 war
    # (die Leistung folgt dem Versatz träge), dann gleitend mit THERMAL_BASE_TAU_S. Bis dahin
    # zählt die ganze Leistung (R23 c). Beim Wechsel der Betriebsart beginnt es neu; bei einem
    # Wunsch von höchstens 0 gibt die Strategie nichts frei, in Lüften und Aus ebenso.
    device = cfg.thermal.devices["klima"]
    assert isinstance(device, CoeOffsetConfig)
    unit = _Thermal(device)

    def seen(s: float, power_w: float | None, mode: ThermalMode = "kuehlen") -> ThermalInputs:
        return ThermalInputs(True, mode, True, False, s, power_w)

    assert (THERMAL_BASE_SETTLE_S, THERMAL_BASE_TAU_S) == (300.0, 300.0)
    unit.learn(seen(0, 1300.0), 0.0)  # eben zurück auf 0: Die Leistung ist noch hoch
    unit.learn(seen(299, 1300.0), 0.0)
    assert unit.freeable_w(seen(299, 1300.0), wish_steps=1) == 1300.0  # nichts gelernt
    unit.learn(seen(300, 600.0), 0.0)
    assert unit.freeable_w(seen(300, 1300.0), wish_steps=1) == 700.0
    unit.learn(seen(450, 900.0), 0.0)  # halbe Zeitkonstante: halber Weg
    assert unit.freeable_w(seen(450, 1300.0), wish_steps=1) == 550.0
    unit.learn(seen(460, 2000.0), -0.5)  # mit Versatz lernt sie nicht
    assert unit.freeable_w(seen(460, 1300.0), wish_steps=1) == 550.0
    assert unit.freeable_w(seen(460, 600.0), wish_steps=1) == 0.0
    assert unit.freeable_w(seen(460, 1300.0), wish_steps=0) == 0.0
    assert unit.freeable_w(seen(460, 1300.0), wish_steps=-1) == 0.0
    assert unit.freeable_w(seen(460, None), wish_steps=1) == 0.0
    assert unit.freeable_w(seen(460, 1300.0, "lueften"), wish_steps=1) == 0.0
    unit.learn(seen(470, 1500.0, "heizen"), 0.0)  # neue Betriebsart: neu lernen
    assert unit.freeable_w(seen(470, 1500.0, "heizen"), wish_steps=1) == 1500.0


def test_thermal_health_shows_base_and_spare(reg_with_klima, cfg) -> None:
    # Die Grundlast (gelernt bei Versatz 0) und die zuletzt freigebbare Leistung stehen in Health:
    # 400 W gelernt, 900 W bei einer Stufe mehr → 500 W freigebbar.
    klima_run(reg_with_klima, cfg, 0, 600, power_w=400.0)
    assert klima_health(reg_with_klima)["base_w"] == 400
    klima(reg_with_klima, cfg, 610, power_w=900.0)
    entry = klima_health(reg_with_klima)
    assert (entry["wish"], entry["base_w"], entry["spare_w"]) == (1, 400, 500)


def test_thermal_health_without_learned_base_has_none(reg_with_klima, cfg) -> None:
    # Nichts gelernt und die Leistung unbekannt: keine Grundlast, nichts freigebbar.
    klima(reg_with_klima, cfg, 0)
    entry = klima_health(reg_with_klima)
    assert (entry["base_w"], entry["spare_w"]) == (None, 0)


def test_regulation_without_grid_power_knows_no_export(reg_with_klima, cfg) -> None:
    found = klima_run(reg_with_klima, cfg, 0, 700, grid_w=None)
    assert [x.inputs for _, x in found] == [
        {"thermal_steps": 0.0, "export_w": None, "soc_pct": 95.0}
    ]
    assert wish(reg_with_klima)["wish"] == 0


def test_regulation_skips_a_device_without_inputs(reg_with_klima, cfg) -> None:
    given = RegulationInputs(inputs(cfg))
    reg_with_klima.step(given)
    assert reg_with_klima.thermal_step(given) == []
    assert reg_with_klima.health()["thermal"] == {}


def test_regulation_routes_thermal_verdicts_and_results(reg_with_klima, cfg, monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(reg_with_klima.charger, "note_verdict", lambda *a, **k: seen.append("v"))
    monkeypatch.setattr(reg_with_klima.charger, "note_result", lambda *a, **k: seen.append("r"))
    command = Command(KLIMA, OFFSET, -0.5)
    reg_with_klima.note_verdict(command, accepted(command))
    reg_with_klima.note_result(WriteResult(KLIMA, OFFSET, -0.5, DecisionResult.OK, ""))
    assert seen == []
    # Ein unbekanntes Gerät mit dem Präfix ``thermal.`` geht an die Laderegelung wie bisher.
    other = Command("thermal.wp", OFFSET, 1.0)
    reg_with_klima.note_verdict(other, accepted(other))
    reg_with_klima.note_result(WriteResult("thermal.wp", OFFSET, 1.0, DecisionResult.OK, ""))
    assert seen == ["v", "r"]
    # ``not_sent`` löscht den letzten Wert: −0,5 kommt wieder.
    klima_run(reg_with_klima, cfg, 0, 600)
    found = klima(reg_with_klima, cfg, 610, accept=False)
    assert found == []
    last = Command(KLIMA, OFFSET, -0.5, reasons=("thermal_more",))
    reg_with_klima.note_verdict(last, accepted(last), not_sent=True)
    assert [x.command.value for x in klima(reg_with_klima, cfg, 620)] == [-0.5]


def without_thermal(cfg: Config) -> Config:
    return cfg.model_copy(update={"thermal": cfg.thermal.model_copy(update={"devices": {}})})


def test_regulation_without_thermal_devices(cfg) -> None:  # Ruling R20
    # Ohne ``thermal.devices`` regeln Autos und Akku wie mit, und es gibt keinen Versatz.
    bare = without_thermal(cfg)
    charge = inputs(
        bare,
        evcs_obs=evcs(charging=True, power_w=2760.0),
        controllable={**CONTROLLABLE, VICTRON: True},
        intent=EGOLF_NOW,
    )
    given = RegulationInputs(charge, pv_w=0.0, house_w=500.0, base_setpoint_w=0.0)
    regulation = Regulation(bare, HardLimits.from_config(bare), started=T0)
    with_klima = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    found = regulation.step(given)
    assert found == with_klima.step(given)
    assert [x.command.actuator for x in found] == ["wallbox.evcs", "wallbox.evcs", VICTRON]
    # Auch Eingaben für ein unbekanntes Gerät ergeben nichts.
    seen = {"klima": ThermalInputs(True, "kuehlen", True, False, T0.timestamp())}
    assert regulation.thermal_step(replace(given, thermal=seen)) == []
    assert regulation.health()["thermal"] == {}
    command = Command(KLIMA, OFFSET, -0.5)
    regulation.note_verdict(command, accepted(command))  # geht an die Laderegelung, ohne Wirkung
    regulation.forget({KLIMA})
