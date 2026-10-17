from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest
from tests.helpers import T0
from tests.unit.core.control.charge_helpers import CONTROLLABLE, evcs, grid, inputs, tesla, twc

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
from bindaems.core.control.plan_exec import plan_health
from bindaems.core.control.regulation import (
    Regulation,
    RegulationInputs,
    battery_cars,
    peak_cars,
    peak_wanted_w,
)
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
    assert peak_wanted_w(need, acting=True) == 18400.0
    assert peak_wanted_w(None, acting=True) is None
    assert peak_wanted_w(need, acting=False) is None


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
    assert list(health) == ["intent_version", "vehicles", "battery", "peak", "wallboxes", "plan"]


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
