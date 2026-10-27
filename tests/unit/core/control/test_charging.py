import math
from dataclasses import replace
from datetime import timedelta

import pytest
from tests.helpers import T0
from tests.unit.core.control.charge_helpers import (
    CONTRADICTION,
    NONE,
    UNCLEAR_FROM_S,
    accept,
    evcs,
    grid,
    inputs,
    run,
    tesla,
    twc,
)
from tests.unit.core.safety.conftest import make_ctx

from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.charging import FOLLOW_HOLD_S, RAMP_UP_S, CarView, ChargeController
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.peak_guard import NO_LIMITS, PeakLimits
from bindaems.core.control.phase_guard import OverloadLevel
from bindaems.core.control.plan_exec import ActiveSlot
from bindaems.core.safety.budget import PersistentWriteBudget
from bindaems.core.safety.layer import SafetyLayer
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import Outcome, Verdict
from bindaems.shared.control import (
    ChargeMode,
    Command,
    CommandKind,
    GuestIntent,
    Intent,
    SurplusOrder,
    SurplusPriority,
    VehicleIntent,
)
from bindaems.shared.decisions import DecisionResult
from bindaems.shared.plan import BatteryIntent

CURRENT, CHARGING = CommandKind.CURRENT, CommandKind.CHARGING
NOW = VehicleIntent(mode=ChargeMode.NOW)
AT_16_A = evcs(charging=True, phase_a={"L1": 16.0, "L2": 16.0, "L3": 0.0}, power_w=7360.0)
TWC_OUT = twc(reachable=False, connected=None, charging=None, phase_a=None, power_w=None)
BOTH = {"wallbox.evcs": True, "vehicle.tesla": True}
TESLA_PLUGGED = {
    "twc_obs": twc(connected=True),
    "tesla_obs": tesla(plugged=True),
    "controllable": BOTH,
}


def egolf_in(mode: ChargeMode, version: int = 1) -> Intent:
    """Die Absicht mit dem e-Golf in ``mode``; der Tesla hat keinen Eintrag."""
    return Intent(version=version, control=True, vehicles={"egolf": VehicleIntent(mode=mode)})


def at_evcs(current: float, charging: bool) -> dict:
    """Die Ist-Werte der EVCS; die des Tesla sind unbekannt."""
    return {("wallbox.evcs", CURRENT): current, ("wallbox.evcs", CHARGING): charging}


def controller(cfg) -> ChargeController:
    return ChargeController(cfg, HardLimits.from_config(cfg), started=T0)


def with_vehicle(cfg, key: str, **update):
    """Die Konfiguration mit geänderten Werten für das Fahrzeug ``key``."""
    vehicle = cfg.vehicles[key].model_copy(update=update)
    return cfg.model_copy(update={"vehicles": {**cfg.vehicles, key: vehicle}})


# Vorgaben des Plans


def cmd(t: int, kind, value, reasons, *, urgent=False):
    return (t, Command("wallbox.evcs", kind, value, urgent=urgent, reasons=reasons))


def as_commands(found):
    return [(t, c.command) for t, c in found]


def test_pv_start_after_120_s_sets_current_then_charging(cfg) -> None:
    found = run(ChargeController(cfg, HardLimits.from_config(cfg), started=T0), cfg, 121)
    assert as_commands(found) == [
        cmd(0, CommandKind.CHARGING, False, ("mode_pv",)),
        cmd(120, CommandKind.CURRENT, 8.0, ("mode_pv", "pv_surplus")),
        cmd(120, CommandKind.CHARGING, True, ("mode_pv", "pv_surplus")),
    ]
    assert found[1][1].inputs == {
        "surplus_w": 3900.0,
        "limit_a": 17.0,
        "actual_current_a": 6.0,
        "actual_charging": 0.0,
    }


def test_pv_follows_the_surplus(cfg) -> None:
    started = evcs(charging=True, phase_a={"L1": 8.0, "L2": 8.0, "L3": 0.0}, power_w=3680.0)
    found = run(
        ChargeController(cfg, HardLimits.from_config(cfg), started=T0),
        cfg,
        122,
        evcs_obs=lambda s: started if s > 120 else evcs(),
        grid_obs=lambda s: grid(grid_w=-1380.0) if s > 120 else grid(),
    )
    # Überschuss 3680 + 1380 − 100 = 4960 W → 10 A
    assert [c for c in as_commands(found) if c[0] > 120] == [
        cmd(121, CommandKind.CURRENT, 10.0, ("mode_pv", "pv_surplus"))
    ]


AT_8_A = evcs(charging=True, phase_a={"L1": 8.0, "L2": 8.0, "L3": 0.0}, power_w=3680.0)


def with_control(cfg, **update):
    """Die Konfiguration mit geänderten Werten im Abschnitt ``control``."""
    return cfg.model_copy(update={"control": cfg.control.model_copy(update=update)})


@pytest.mark.parametrize(
    ("hysteresis", "expected"),
    [
        (0.5, [(131, 9.0), (141, 8.0)]),  # 9,4 A halten 8 A, erst 9,5 A erhöhen
        (0.0, [(121, 9.0), (141, 8.0)]),  # ohne Hysterese abgerundet wie bisher
    ],
)
def test_pv_raises_the_evcs_current_only_from_one_ampere_plus_the_hysteresis(
    cfg, hysteresis, expected
) -> None:  # Design 6.5
    def grid_at(s: int):
        if s <= 120:
            return grid()  # Anteil 3900 / 460 = 8,48 A: Start mit 8 A
        if s <= 130:
            return grid(grid_w=-744.0)  # Anteil (3680 + 744 − 100) / 460 = 9,4 A
        if s <= 140:
            return grid(grid_w=-790.0)  # 9,5 A
        return grid(grid_w=-514.0)  # 8,9 A: unter dem Strom, er sinkt sofort

    cfg = with_control(cfg, evcs_hysteresis_a=hysteresis)
    found = run(
        controller(cfg),
        cfg,
        142,
        evcs_obs=lambda s: AT_8_A if s > 120 else evcs(),
        grid_obs=grid_at,
    )
    reasons = ("mode_pv", "pv_surplus")
    assert [c for c in as_commands(found) if c[0] > 120] == [
        cmd(t, CommandKind.CURRENT, value, reasons) for t, value in expected
    ]


def test_without_an_own_value_the_evcs_takes_the_rounded_share(cfg) -> None:  # Design 6.5
    c = controller(cfg)
    run(c, cfg, 121)  # Start mit 8 A bei 120 s
    c.forget({"wallbox.evcs"})  # etwa nach einem Wert des Keepers
    found = c.step(inputs(cfg, s=121, evcs_obs=AT_8_A, grid_obs=grid(grid_w=-744.0)))
    # Anteil 9,4 A: Ohne eigenen Wert hält die Regelung nichts, sie rundet ab.
    assert [x.command.value for x in found if x.command.kind is CURRENT] == [9.0]


def test_import_limit_caps_the_current(cfg) -> None:
    charging = evcs(charging=True, phase_a={"L1": 10.0, "L2": 10.0, "L3": 0.0}, power_w=4600.0)
    found = run(
        ChargeController(cfg, HardLimits.from_config(cfg), started=T0),
        cfg,
        2,
        evcs_obs=charging,
        present=at_evcs(10.0, True),
        grid_obs=lambda s: grid(l1=18.0 if s == 0 else 25.0, l2=12.0, grid_w=-6000.0),
    )
    reasons = ("mode_pv", "import_limit")
    assert as_commands(found) == [
        cmd(0, CommandKind.CURRENT, 11.0, reasons),  # Anteil 16, frei min(19 − 8, 19 − 2) = 11
        cmd(0, CommandKind.CHARGING, True, reasons),
        cmd(1, CommandKind.CURRENT, 6.0, reasons),  # frei 4 < 6: der Mindeststrom
    ]


def test_now_is_lowered_urgently_at_the_fuse(cfg) -> None:
    found = run(
        ChargeController(cfg, HardLimits.from_config(cfg), started=T0),
        cfg,
        1,
        intent=egolf_in(ChargeMode.NOW),
        evcs_obs=AT_16_A,
        present=at_evcs(16.0, True),
        grid_obs=grid(l1=36.0, l2=20.0),
    )
    reasons = ("mode_now", "phase_limit")
    assert as_commands(found) == [
        cmd(0, CommandKind.CURRENT, 13.0, reasons, urgent=True),  # frei min(33 − 20, 33 − 4)
        cmd(0, CommandKind.CHARGING, True, reasons),
    ]


def test_now_starts_only_when_the_limit_carries_the_minimum(cfg) -> None:  # Design 5.3
    # Der e-Golf in Sofort, angesteckt, lädt nicht. Bis 4 s ist auf L1 frei 33 − 30,3 = 2,7 A,
    # weniger als sein Mindeststrom: Der Strom geht auf 6 A, „Laden an“ wartet. Ab 5 s ist frei
    # 33 − 27 = 6 A: Jetzt startet er.
    reasons = ("mode_now", "phase_limit")
    found = run(
        controller(cfg),
        cfg,
        10,
        intent=egolf_in(ChargeMode.NOW),
        grid_obs=lambda s: grid(l1=30.3 if s < 5 else 27.0),
    )
    assert as_commands(found) == [cmd(0, CURRENT, 6.0, reasons), cmd(5, CHARGING, True, reasons)]


def test_min_pv_starts_when_the_fuse_carries_the_minimum(cfg) -> None:  # Design 5.3, 6.3
    # Min+PV: Der Mindeststrom geht dem Importlimit vor, nicht der Sicherung. Bis 4 s ist auf L1 an
    # der Sicherung frei 33 − 30,3 = 2,7 A: kein Start. Ab 5 s 33 − 25 = 8 A: Start mit 6 A, obwohl
    # am Importlimit 19 − 25 = −6 A frei sind.
    reasons = ("mode_min_pv", "import_limit")
    found = run(
        controller(cfg),
        cfg,
        10,
        intent=egolf_in(ChargeMode.MIN_PV),
        grid_obs=lambda s: grid(l1=30.3 if s < 5 else 25.0),
    )
    assert as_commands(found) == [cmd(0, CURRENT, 6.0, reasons), cmd(5, CHARGING, True, reasons)]


def test_a_charging_car_keeps_charging_below_the_minimum(cfg) -> None:  # Design 5.3
    # Lädt es schon, geht es auf den Mindeststrom; „Laden an“ beim Übernehmen bleibt.
    charging = evcs(charging=True, phase_a={"L1": 10.0, "L2": 10.0, "L3": 0.0}, power_w=4600.0)
    found = run(
        controller(cfg),
        cfg,
        1,
        intent=egolf_in(ChargeMode.NOW),
        evcs_obs=charging,
        present=at_evcs(10.0, True),
        grid_obs=grid(l1=40.3),  # frei 33 − (40,3 − 10) = 2,7 A
    )
    reasons = ("mode_now", "phase_limit")
    assert as_commands(found) == [
        cmd(0, CURRENT, 6.0, reasons, urgent=True),
        cmd(0, CHARGING, True, reasons),
    ]


def test_reserve_guard_limits_now_to_the_import_target(cfg) -> None:
    found = run(
        ChargeController(cfg, HardLimits.from_config(cfg), started=T0),
        cfg,
        1,
        intent=egolf_in(ChargeMode.NOW),
        evcs_obs=AT_16_A,
        present=at_evcs(16.0, True),
        grid_obs=grid(l1=36.0, l2=20.0, soc=24.0),
    )
    reasons = ("mode_now", "reserve_guard")
    assert as_commands(found) == [
        cmd(0, CommandKind.CURRENT, 6.0, reasons),  # frei min(19 − 20, 19 − 4) < 6
        cmd(0, CommandKind.CHARGING, True, reasons),
    ]


def test_off_stops_at_once(cfg) -> None:
    found = run(
        ChargeController(cfg, HardLimits.from_config(cfg), started=T0),
        cfg,
        10,
        intent=egolf_in(ChargeMode.OFF),
        evcs_obs=evcs(charging=True),
        present=at_evcs(6.0, True),
    )
    # Der Stopp in „Aus“ ist dringend (Plan 2d, Präzisierung 9)
    assert as_commands(found) == [cmd(0, CommandKind.CHARGING, False, ("mode_off",), urgent=True)]


def test_min_pv_keeps_its_minimum_without_surplus(cfg) -> None:
    found = run(
        ChargeController(cfg, HardLimits.from_config(cfg), started=T0),
        cfg,
        400,
        intent=egolf_in(ChargeMode.MIN_PV),
        grid_obs=grid(grid_w=2000.0),
    )
    reasons = ("mode_min_pv", "pv_surplus")
    assert as_commands(found) == [
        cmd(0, CommandKind.CURRENT, 6.0, reasons),
        cmd(0, CommandKind.CHARGING, True, reasons),
    ]


def test_unknown_surplus_holds_the_minimum_then_stops(cfg) -> None:
    found = run(
        ChargeController(cfg, HardLimits.from_config(cfg), started=T0),
        cfg,
        301,
        evcs_obs=evcs(charging=True),
        present=at_evcs(6.0, True),
        grid_obs=grid(grid_w=None),
    )
    assert as_commands(found) == [
        cmd(0, CommandKind.CURRENT, 6.0, ("mode_pv",)),
        cmd(0, CommandKind.CHARGING, True, ("mode_pv",)),
        cmd(300, CommandKind.CHARGING, False, ("mode_pv", "pv_surplus")),
    ]


def test_unknown_evcs_status_holds_still(cfg) -> None:  # Review Focus 3
    found = run(
        ChargeController(cfg, HardLimits.from_config(cfg), started=T0),
        cfg,
        200,
        evcs_obs=evcs(connected=None, charging=None, complete=None),
    )
    assert found == []


def test_only_changes_are_sent(cfg) -> None:
    found = run(ChargeController(cfg, HardLimits.from_config(cfg), started=T0), cfg, 100)
    assert [t for t, _ in found] == [0]


def test_deferred_is_offered_again_and_unchanged_counts_as_sent(cfg) -> None:
    controller = ChargeController(cfg, HardLimits.from_config(cfg), started=T0)
    first = controller.step(inputs(cfg))
    accept(controller, first, Outcome.DEFERRED)
    again = controller.step(inputs(cfg, s=1))
    assert [c.command for c in again] == [c.command for c in first]
    accept(controller, again, Outcome.UNCHANGED)
    assert controller.step(inputs(cfg, s=2)) == []


def test_forget_offers_the_same_wish_again(cfg) -> None:
    ctrl = controller(cfg)
    run(ctrl, cfg, 121)  # Start bei 120 s mit 8 A
    ctrl.forget({"vehicle.tesla"})  # ein anderer Aktor: Die EVCS behält ihre Werte
    assert ctrl.step(inputs(cfg, s=121)) == []
    ctrl.forget({"wallbox.evcs"})
    again = [(122, found.command) for found in ctrl.step(inputs(cfg, s=122))]
    assert again == [
        cmd(122, CURRENT, 8.0, ("mode_pv", "pv_surplus")),
        cmd(122, CHARGING, True, ("mode_pv", "pv_surplus")),
    ]


def test_a_failure_offers_the_value_again(cfg) -> None:
    controller = ChargeController(cfg, HardLimits.from_config(cfg), started=T0)
    run(controller, cfg, 121)  # Start bei 120 mit 8 A
    controller.note_result(
        WriteResult("wallbox.evcs", CommandKind.CURRENT, 8.0, DecisionResult.ERROR, "x")
    )
    again = controller.step(inputs(cfg, s=121))
    assert [(121, c.command) for c in again] == [
        cmd(121, CommandKind.CURRENT, 8.0, ("mode_pv", "pv_surplus"))
    ]


def test_blocked_kind_waits_and_follows_after_result(cfg) -> None:  # Review Focus 2
    off = egolf_in(ChargeMode.OFF, version=2)
    found = run(
        ChargeController(cfg, HardLimits.from_config(cfg), started=T0),
        cfg,
        132,
        intent=lambda s: off if s >= 130 else Intent(version=1, control=True),
        blocked=lambda s: (
            frozenset({("wallbox.evcs", CommandKind.CHARGING)}) if s == 130 else frozenset()
        ),
    )
    assert [c for c in as_commands(found) if c[0] >= 130] == [
        cmd(131, CommandKind.CHARGING, False, ("mode_off",), urgent=True)
    ]


def test_not_controllable_gets_nothing(cfg) -> None:
    found = run(
        ChargeController(cfg, HardLimits.from_config(cfg), started=T0),
        cfg,
        200,
        controllable={"wallbox.evcs": False, "vehicle.tesla": False},
    )
    assert found == []


def test_overload_sets_the_minimum_then_stops_the_evcs(cfg) -> None:
    found = run(
        ChargeController(cfg, HardLimits.from_config(cfg), started=T0),
        cfg,
        7,
        intent=egolf_in(ChargeMode.NOW),
        evcs_obs=AT_16_A,
        present=at_evcs(16.0, True),
        grid_obs=grid(l1=34.0, l2=20.0),
    )
    assert as_commands(found) == [
        cmd(0, CommandKind.CURRENT, 15.0, ("mode_now", "phase_limit"), urgent=True),
        cmd(0, CommandKind.CHARGING, True, ("mode_now", "phase_limit")),
        cmd(3, CommandKind.CURRENT, 6.0, ("mode_now", "overload"), urgent=True),
        cmd(6, CommandKind.CHARGING, False, ("mode_now", "overload"), urgent=True),
    ]


def test_vehicle_without_entry_charges_in_pv(cfg) -> None:  # Review Focus 4
    intent = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW)})
    found = run(
        ChargeController(cfg, HardLimits.from_config(cfg), started=T0), cfg, 121, intent=intent
    )
    assert [(t, c.command.kind) for t, c in found if c.command.actuator == "wallbox.evcs"] == [
        (0, CommandKind.CHARGING),
        (120, CommandKind.CURRENT),
        (120, CommandKind.CHARGING),
    ]


def test_unplugged_car_gets_charging_off_once(cfg) -> None:
    found = run(
        ChargeController(cfg, HardLimits.from_config(cfg), started=T0),
        cfg,
        200,
        evcs_obs=evcs(connected=False),
        present=at_evcs(6.0, True),
    )
    assert as_commands(found) == [cmd(0, CommandKind.CHARGING, False, ("mode_pv",))]


def test_health_per_vehicle(cfg) -> None:
    controller = ChargeController(cfg, HardLimits.from_config(cfg), started=T0)
    accept(controller, controller.step(inputs(cfg)))
    assert controller.health()[1] == {
        "id": "egolf",
        "name": "e-Golf",
        "mode": "pv",
        "boost_until": None,
        "wallbox": "evcs",
        "assignment": "default",
        "limit_a": 17.0,
        "plugged": True,
        "charging": False,
        "wakes_today": 0,
        "commands_hour": None,
        "boost_pending": False,
        "override_pending": False,
        "reason": "mode_pv",
        "soc_pct": 0.0,  # ohne Ladestand angenommen
        "soc_source": "assumed",
        "charge_limit_pct": None,
        "planned": None,
    }


def test_intent_version_is_the_one_of_the_last_step_and_unknown_after_idle(cfg) -> None:
    c = controller(cfg)
    assert c.intent_version is None
    intent = Intent(version=42, control=True)
    c.step(inputs(cfg, intent=intent))
    assert c.intent_version == 42
    c.step(inputs(cfg, s=1, intent=Intent(version=43, control=True)))
    assert c.intent_version == 43
    c.idle(T0 + timedelta(seconds=3))
    assert c.intent_version is None


def test_idle_drops_the_version_before_the_views(cfg) -> None:
    """Ein Health zwischen den Zuweisungen in ``idle`` zeigt nie die alte Version mit leeren
    Ansichten (die app hielte Boost und Übersteuerung sonst für beendet)."""
    seen: list[object] = []

    class Watched(ChargeController):
        def __setattr__(self, name: str, value: object) -> None:
            if name == "_views":
                seen.append(self._intent_version)
            super().__setattr__(name, value)

    c = controller(cfg)
    c.step(inputs(cfg, intent=Intent(version=42, control=True)))
    c.__class__ = Watched
    c.idle(T0 + timedelta(seconds=1))
    assert seen == [None]


def test_intent_version_changes_together_with_the_views(cfg, monkeypatch) -> None:
    """Scheitert ein Schritt, bevor er seine Ansichten ablegt, nennt Health die alte Version."""
    until = T0 + timedelta(hours=2)
    c = controller(cfg)
    c.step(inputs(cfg, intent=Intent(version=1, control=True)))
    assert c.intent_version == 1

    def broken(*args, **kwargs):
        raise RuntimeError("Schritt scheitert")

    monkeypatch.setattr("bindaems.core.control.charging.charge_points", broken)
    boost = Intent(version=2, control=True, vehicles={"egolf": VehicleIntent(boost_until=until)})
    with pytest.raises(RuntimeError):
        c.step(inputs(cfg, s=1, intent=boost))
    assert c.intent_version == 1  # die Ansichten sind die des vorigen Schritts
    assert c.health()[1]["boost_pending"] is False
    monkeypatch.undo()
    c.step(inputs(cfg, s=2, intent=boost))
    assert c.intent_version == 2


def test_health_shows_a_boost_waiting_for_the_plug_as_pending(cfg) -> None:
    until = T0 + timedelta(hours=2)
    intent = Intent(version=1, control=True, vehicles={"egolf": VehicleIntent(boost_until=until)})
    c = controller(cfg)
    c.step(inputs(cfg, intent=intent, evcs_obs=evcs(connected=False)))
    egolf = c.health()[1]
    assert (egolf["boost_until"], egolf["boost_pending"], egolf["override_pending"]) == (
        None,
        True,
        False,
    )
    c.step(inputs(cfg, s=1, intent=intent))
    egolf = c.health()[1]
    assert egolf["boost_until"] == until.isoformat()
    assert (egolf["boost_pending"], egolf["override_pending"]) == (False, False)
    c.step(inputs(cfg, s=2, intent=intent, evcs_obs=evcs(connected=False)))
    c.step(inputs(cfg, s=3, intent=intent, evcs_obs=evcs(connected=False)))
    egolf = c.health()[1]
    assert (egolf["boost_until"], egolf["boost_pending"]) == (None, False)


# Weitere Fälle: Festlegungen der Aufgabe


@pytest.mark.parametrize(
    ("priority", "order", "evcs_a"),
    [
        (["egolf", "tesla"], ["wallbox.evcs", "vehicle.tesla"], 16.0),
        (["egolf"], ["vehicle.tesla", "wallbox.evcs"], 15.0),  # nennt nicht jedes Fahrzeug
        (["egolf", "tesla", "ghost"], ["vehicle.tesla", "wallbox.evcs"], 15.0),
    ],
    ids=["from_intent", "incomplete", "unknown_vehicle"],
)
def test_priority_of_the_intent_counts_only_with_each_vehicle_once(
    cfg, priority, order, evcs_a
) -> None:
    intent = Intent(
        version=1,
        control=True,
        vehicles={"tesla": NOW, "egolf": NOW, "ghost": NOW},
        priority=priority,
    )
    found = controller(cfg).step(inputs(cfg, intent=intent, **TESLA_PLUGGED))
    currents = [(c.command.actuator, c.command.value) for c in found if c.command.kind == CURRENT]
    assert [actuator for actuator, _ in currents] == order
    # Ziel 33 A, Last 2 A: das erste Auto bekommt 16 A, dem zweiten bleiben 15 A
    assert dict(currents)["wallbox.evcs"] == evcs_a


def test_boost_charges_to_the_fuse_until_it_ends(cfg) -> None:
    until = T0 + timedelta(hours=2)
    intent = Intent(version=1, control=True, vehicles={"egolf": VehicleIntent(boost_until=until)})
    c = controller(cfg)
    loaded = grid(l1=20.0, l2=20.0)
    found = c.step(inputs(cfg, intent=intent, grid_obs=loaded))
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 13.0, reasons=("boost", "phase_limit")),  # 33 − 20
        Command("wallbox.evcs", CHARGING, True, reasons=("boost", "phase_limit")),
    ]
    assert (c.health()[1]["mode"], c.health()[1]["boost_until"]) == (
        "boost",
        "2026-10-08T12:00:00+00:00",
    )
    accept(c, found)
    # danach PV-Überschuss: Moduswechsel, frei 19 − 20 < 6 reicht nicht
    after = c.step(inputs(cfg, s=7201, intent=intent, grid_obs=loaded))
    assert [x.command for x in after] == [
        Command("wallbox.evcs", CHARGING, False, reasons=("mode_pv",))
    ]


def test_not_taken_is_offered_again_with_its_reason_until_judged(cfg) -> None:
    c = controller(cfg)
    run(c, cfg, 121)  # Start bei 120 mit 8 A
    c.note_result(
        WriteResult("wallbox.evcs", CURRENT, 8.0, DecisionResult.ERROR, "x", not_taken=True)
    )
    retry = Command(
        "wallbox.evcs", CURRENT, 8.0, reasons=("mode_pv", "pv_surplus", "not_taken_retry")
    )
    first = c.step(inputs(cfg, s=121))
    assert [x.command for x in first] == [retry]
    accept(c, first, Outcome.DEFERRED)  # zurückgestellt: gilt nicht als gesendet
    second = c.step(inputs(cfg, s=122))
    assert [x.command for x in second] == [retry]
    accept(c, second)
    assert c.step(inputs(cfg, s=123)) == []
    # beurteilt: Der nächste Strom (4900 W → 10 A) trägt den Grund nicht mehr
    later = c.step(inputs(cfg, s=124, grid_obs=grid(grid_w=-5000.0)))
    assert [x.command for x in later] == [
        Command("wallbox.evcs", CURRENT, 10.0, reasons=("mode_pv", "pv_surplus"))
    ]


def test_only_a_failure_of_its_own_value_is_offered_again(cfg) -> None:
    c = controller(cfg)
    run(c, cfg, 121)  # Start bei 120: Strom 8 A, Laden an
    # ein anderer Wert, etwa der Sicherstrom des Keepers, und eine Bestätigung ändern nichts
    c.note_result(WriteResult("wallbox.evcs", CURRENT, 6.0, DecisionResult.TIMEOUT, "x"))
    c.note_result(WriteResult("wallbox.evcs", CURRENT, 8.0, DecisionResult.OK, "x"))
    assert c.step(inputs(cfg, s=121)) == []
    c.note_result(WriteResult("wallbox.evcs", CHARGING, True, DecisionResult.TIMEOUT, "x"))
    again = c.step(inputs(cfg, s=122))
    assert [x.command for x in again] == [
        Command("wallbox.evcs", CHARGING, True, reasons=("mode_pv", "pv_surplus"))
    ]


@pytest.mark.parametrize("judgement", ["not_sent", "rejected", "pending"])
def test_a_command_without_effect_is_offered_again(cfg, judgement) -> None:
    c = controller(cfg)
    first = c.step(inputs(cfg))
    command = first[0].command  # Laden aus
    if judgement == "not_sent":  # ein Wert stand fest, ging aber nicht hinaus
        c.note_verdict(command, Verdict(command, Outcome.ACCEPTED, False, (), False), not_sent=True)
    elif judgement == "rejected":
        accept(c, first, Outcome.REJECTED)
    else:  # Schreibvorgang offen: nicht betrachtet
        c.note_verdict(command, None)
    assert [x.command for x in c.step(inputs(cfg, s=1))] == [command]


def test_after_a_pause_pv_takes_over_a_charging_car_again(cfg) -> None:  # Präzisierung 9
    c = controller(cfg)
    charging = {
        "evcs_obs": evcs(charging=True),
        "present": at_evcs(6.0, True),
        "grid_obs": grid(grid_w=None),
    }
    found = run(c, cfg, 301, **charging)  # ohne Überschuss: Stopp bei 300
    assert as_commands(found)[-1] == cmd(300, CHARGING, False, ("mode_pv", "pv_surplus"))
    c.idle(T0 + timedelta(seconds=301))
    resumed = c.step(inputs(cfg, s=400, **charging))
    reasons = ("mode_pv", "resume_after_outage")  # die erste Startfolge nach der Pause
    assert [(400, x.command) for x in resumed] == [
        cmd(400, CURRENT, 6.0, reasons),
        cmd(400, CHARGING, True, reasons),
    ]


def test_after_a_pause_the_overload_counts_anew(cfg) -> None:
    c = controller(cfg)
    over = {
        "intent": egolf_in(ChargeMode.NOW),
        "evcs_obs": AT_16_A,
        "present": at_evcs(16.0, True),
        "grid_obs": grid(l1=34.0, l2=20.0),
    }
    run(c, cfg, 3, **over)  # 0 bis 2 über dem Ziel, noch keine Überlast
    c.idle(T0 + timedelta(seconds=3))
    found = []
    for s in range(4, 8):
        commands = c.step(inputs(cfg, s=s, **over))
        accept(c, commands)
        found.extend((s, x.command) for x in commands)
    resumed = ("mode_now", "phase_limit", "resume_after_outage")
    assert found == [
        cmd(4, CURRENT, 15.0, resumed, urgent=True),
        cmd(4, CHARGING, True, resumed),
        cmd(7, CURRENT, 6.0, ("mode_now", "overload"), urgent=True),
    ]


@pytest.mark.parametrize(
    ("mode", "l1", "lowered"),
    [
        (ChargeMode.NOW, 36.0, cmd(1, CURRENT, 13.0, ("mode_now", "phase_limit"), urgent=True)),
        (ChargeMode.NOW, 46.0, cmd(1, CURRENT, 6.0, ("mode_now", "phase_limit"), urgent=True)),
        (ChargeMode.PV, 20.0, cmd(1, CURRENT, 15.0, ("mode_pv", "import_limit"))),
    ],
    ids=["fuse", "never_below_minimum", "import_limit_not_urgent"],
)
def test_unknown_plug_state_only_lowers_the_current_for_the_guard(  # Präzisierung 4
    cfg, mode, l1, lowered
) -> None:
    unknown = replace(AT_16_A, connected=None, charging=None, complete=None)
    found = run(
        controller(cfg),
        cfg,
        3,
        intent=egolf_in(mode),
        evcs_obs=unknown,
        present=at_evcs(16.0, True),
        grid_obs=lambda s: grid(l1=18.0, l2=18.0) if s == 0 else grid(l1=l1, l2=20.0),
    )
    # bei 0 lässt der Wächter mindestens 17 A frei: nichts; ab 1 weniger als die 16 A
    assert as_commands(found) == [lowered]


def test_overload_stops_even_with_an_unknown_plug_state(cfg) -> None:
    unknown = replace(AT_16_A, connected=None, charging=None, complete=None)
    found = run(
        controller(cfg),
        cfg,
        7,
        intent=egolf_in(ChargeMode.NOW),
        evcs_obs=unknown,
        present=at_evcs(16.0, True),
        grid_obs=grid(l1=34.0, l2=20.0),
    )
    assert as_commands(found) == [
        cmd(0, CURRENT, 15.0, ("mode_now", "phase_limit"), urgent=True),
        cmd(3, CURRENT, 6.0, ("mode_now", "overload"), urgent=True),
        cmd(6, CHARGING, False, ("mode_now", "overload"), urgent=True),
    ]


def test_evcs_stop_keeps_the_tesla_at_its_minimum_until_all_stop(cfg) -> None:
    both_now = Intent(version=1, control=True, vehicles={"tesla": NOW, "egolf": NOW})
    tesla_16 = twc(
        connected=True,
        charging=True,
        phase_a=dict.fromkeys(("L1", "L2", "L3"), 16.0),
        power_w=11040.0,
    )
    present = {
        **at_evcs(16.0, True),
        ("vehicle.tesla", CURRENT): 16.0,
        ("vehicle.tesla", CHARGING): True,
    }
    found = run(
        controller(cfg),
        cfg,
        11,
        intent=both_now,
        evcs_obs=AT_16_A,
        twc_obs=tesla_16,
        tesla_obs=tesla(plugged=True, charging=True),
        present=present,
        controllable=BOTH,
        grid_obs=grid(l1=36.0, l2=33.0, l3=20.0),  # L1 über der Sicherung
    )
    overload = [
        (t, c.command.actuator, c.command.kind, c.command.value, c.command.urgent)
        for t, c in found
        if "overload" in c.command.reasons
    ]
    # Jede höhere Stufe bietet ihre Reaktion allen geregelten Aktoren einmal an.
    assert overload == [
        (3, "vehicle.tesla", CURRENT, 5.0, True),
        (3, "wallbox.evcs", CURRENT, 6.0, True),
        (6, "vehicle.tesla", CURRENT, 5.0, True),
        (6, "wallbox.evcs", CHARGING, False, True),
        (10, "vehicle.tesla", CHARGING, False, True),
        (10, "wallbox.evcs", CHARGING, False, True),
    ]


def test_overload_starts_nothing(cfg) -> None:
    found = run(
        controller(cfg),
        cfg,
        66,
        intent=lambda s: egolf_in(ChargeMode.OFF) if s < 4 else egolf_in(ChargeMode.NOW, 2),
        grid_obs=lambda s: grid(l1=34.0) if s < 5 else grid(),
    )
    # Überlast ab 3 bis 60 s nach dem Ende bei 5: Sofort setzt nur den Mindeststrom. Mit der
    # Überlast vergisst die Regelung ihre letzten Werte und bietet „Laden aus“ einmal neu an.
    assert as_commands(found) == [
        cmd(0, CHARGING, False, ("mode_off",), urgent=True),
        cmd(3, CHARGING, False, ("mode_off",), urgent=True),
        cmd(4, CURRENT, 6.0, ("mode_now", "overload"), urgent=True),
        cmd(65, CURRENT, 16.0, ("mode_now",)),
        cmd(65, CHARGING, True, ("mode_now",)),
    ]


@pytest.mark.parametrize(
    ("kind", "current_at"),
    [(CURRENT, 121), (CHARGING, 120)],
    ids=["current_blocked", "charging_blocked"],
)
def test_a_start_waits_for_a_blocked_kind(cfg, kind, current_at) -> None:  # erst der Strom
    found = run(
        controller(cfg),
        cfg,
        122,
        blocked=lambda s: frozenset({("wallbox.evcs", kind)}) if s == 120 else frozenset(),
    )
    assert as_commands(found) == [
        cmd(0, CHARGING, False, ("mode_pv",)),
        cmd(current_at, CURRENT, 8.0, ("mode_pv", "pv_surplus")),
        cmd(121, CHARGING, True, ("mode_pv", "pv_surplus")),
    ]


def test_power_of_an_unregulated_car_is_no_surplus(cfg) -> None:  # Design 9
    # Der Tesla lädt ungeregelt mit 10 A je Phase; das Netz bezieht 2,9 kW.
    found = run(
        controller(cfg),
        cfg,
        130,
        twc_obs=twc(
            connected=True,
            charging=True,
            phase_a=dict.fromkeys(("L1", "L2", "L3"), 10.0),
            power_w=6900.0,
        ),
        tesla_obs=tesla(plugged=True, charging=True),
        grid_obs=grid(l1=12.0, l2=12.0, l3=12.0, grid_w=2900.0),
    )
    assert as_commands(found) == [cmd(0, CHARGING, False, ("mode_pv",))]


@pytest.mark.parametrize(
    ("mode", "evcs_obs", "priority"),
    [
        (ChargeMode.NOW, evcs(connected=False), None),
        (ChargeMode.NOW, evcs(complete=True), None),
        (ChargeMode.OFF, evcs(), ["egolf", "tesla"]),
    ],
    ids=["unplugged", "complete", "off_first"],
)
def test_a_car_that_does_not_charge_takes_no_surplus(cfg, mode, evcs_obs, priority) -> None:
    intent = Intent(
        version=1,
        control=True,
        vehicles={"egolf": VehicleIntent(mode=mode)},
        priority=priority,
    )
    found = run(controller(cfg), cfg, 121, intent=intent, evcs_obs=evcs_obs, **TESLA_PLUGGED)
    starts = [
        (t, c.command.value)
        for t, c in found
        if c.command.actuator == "vehicle.tesla" and c.command.kind == CHARGING
    ]
    assert starts == [(0, False), (120, True)]  # 3900 W reichen dem Tesla für 5 A


def test_a_pv_car_in_its_stop_minutes_keeps_its_minimum_from_the_surplus(cfg) -> None:
    # Der Tesla lädt mit 5 A, sein Anteil an 2900 W wäre 4 A: Er verbraucht 3450 W weiter.
    found = run(
        controller(cfg),
        cfg,
        130,
        twc_obs=twc(
            connected=True,
            charging=True,
            phase_a=dict.fromkeys(("L1", "L2", "L3"), 5.0),
            power_w=3450.0,
        ),
        tesla_obs=tesla(plugged=True, charging=True),
        controllable=BOTH,
        grid_obs=grid(l1=7.0, l2=7.0, l3=7.0, grid_w=450.0),
    )
    assert [x for x in as_commands(found) if x[1].actuator == "wallbox.evcs"] == [
        cmd(0, CHARGING, False, ("mode_pv",))
    ]


def test_wall_connector_outage_reserves_the_maximum_of_the_tesla(cfg) -> None:  # Präz. 6
    c = controller(cfg)
    tesla_without_current = tesla(plugged=True, actual_current_a=None)
    c.step(
        inputs(
            cfg, intent=egolf_in(ChargeMode.NOW), twc_obs=TWC_OUT, tesla_obs=tesla_without_current
        )
    )
    assert c.health()[1]["limit_a"] == 15.0  # 33 − 2 − 16
    c = controller(cfg)
    c.step(
        inputs(cfg, intent=egolf_in(ChargeMode.NOW), twc_obs=TWC_OUT, tesla_obs=tesla(plugged=True))
    )
    assert c.health()[1]["limit_a"] == 31.0  # mit Strom aus Tessie kein Zuschlag


def test_evcs_outage_reserves_its_safe_current(cfg) -> None:  # Präzisierung 6
    c = controller(cfg)
    outage = evcs(
        reachable=False, connected=None, charging=None, complete=None, phase_a=None, power_w=None
    )
    tesla_now = Intent(version=1, control=True, vehicles={"tesla": NOW})
    c.step(inputs(cfg, intent=tesla_now, evcs_obs=outage, grid_obs=grid(soc=20.0), **TESLA_PLUGGED))
    assert c.health()[0]["limit_a"] == 11.0  # Ziel 19 − 2 A Last − 6 A Zuschlag


def test_wall_connector_outage_takes_the_current_from_tessie(cfg) -> None:  # Präzisierung 6
    c = controller(cfg)
    tesla_now = Intent(version=1, control=True, vehicles={"tesla": NOW})
    c.step(
        inputs(
            cfg,
            intent=tesla_now,
            twc_obs=TWC_OUT,
            tesla_obs=tesla(plugged=True, charging=True, actual_current_a=16.0),
            controllable=BOTH,
            grid_obs=grid(l1=30.0, l2=30.0, l3=30.0, soc=20.0),
        )
    )
    assert c.health()[0]["limit_a"] == 5.0  # geregelt: Ziel 19 − (30 − 16)


def test_a_vehicle_without_tessie_at_the_wall_connector_is_only_load(cfg) -> None:
    plain_cfg = with_vehicle(cfg, "tesla", tessie=None, live_allowed=False)
    c = ChargeController(plain_cfg, HardLimits.from_config(plain_cfg), started=T0)
    found = run(
        c,
        plain_cfg,
        10,
        intent=Intent(version=1, control=True, vehicles={"tesla": NOW}),
        twc_obs=twc(connected=True),
        controllable=BOTH,
    )
    assert [x for x in as_commands(found) if x[1].actuator != "wallbox.evcs"] == []
    assert (c.vehicle_wallbox(), c.health()[0]["wallbox"], c.health()[0]["limit_a"]) == (
        {},
        "twc",
        None,
    )


def test_unclear_assignment_counts_all_phases_of_the_evcs(cfg) -> None:  # Präzisierung 5
    c = controller(cfg)
    contradiction = {
        **CONTRADICTION,
        "intent": egolf_in(ChargeMode.NOW),
        "grid_obs": grid(l3=30.0),
        "controllable": BOTH,
    }
    run(c, cfg, UNCLEAR_FROM_S, **contradiction)
    before = c.health()
    accept(c, c.step(inputs(cfg, s=UNCLEAR_FROM_S, **contradiction)))
    after = c.health()
    # Die EVCS zieht 10 A auf L1 und L2: Ohne sie liegt die Last dort bei 2 − 10 = −8 A
    assert (before[1]["assignment"], before[1]["limit_a"]) == ("default", 41.0)  # L1, L2
    assert (after[1]["assignment"], after[1]["limit_a"]) == ("unclear", 3.0)  # mit L3
    # der Tesla pausiert: Er wird nicht mehr geregelt (Präzisierung 6)
    assert (before[0]["limit_a"], after[0]["limit_a"]) == (-11.0, None)  # 19 − 30 auf L3


def test_a_tesla_that_can_only_be_woken_is_regulated(cfg) -> None:  # Präzisierung 6
    asleep = {"twc_obs": twc(connected=True), "tesla_obs": tesla(plugged=True, asleep=True)}
    c = controller(cfg)
    found = c.step(inputs(cfg, wakeable={"vehicle.tesla": True}, **asleep))
    # geregelt: Er hat eine Grenze und nimmt mit 5 A Anteil der EVCS Platz weg (19 − 2 − 5)
    assert [v["limit_a"] for v in c.health()] == [17.0, 12.0]
    assert [x.command.actuator for x in found] == ["wallbox.evcs"]  # nicht steuerbar: nichts
    c = controller(cfg)
    c.step(inputs(cfg, **asleep))
    assert [v["limit_a"] for v in c.health()] == [None, 17.0]


def test_two_vehicles_at_one_wallbox_count_it_once(cfg) -> None:
    # Beide Fahrzeuge laut Übersteuerung an der EVCS: Ohne sie stünde dort nur eins, das erkannte
    # oder angenommene (Plan 2c-2b, Präzisierung 5).
    both_at_the_evcs = Intent(
        version=1,
        control=True,
        vehicles={
            "tesla": VehicleIntent(mode=ChargeMode.NOW, wallbox="evcs"),
            "egolf": VehicleIntent(mode=ChargeMode.NOW, wallbox="evcs"),
        },
    )
    c = controller(cfg)
    at_10_a = evcs(charging=True, phase_a=dict.fromkeys(("L1", "L2", "L3"), 10.0), power_w=6900.0)
    found = c.step(
        inputs(
            cfg,
            intent=both_at_the_evcs,
            evcs_obs=at_10_a,
            present=at_evcs(10.0, True),
            grid_obs=grid(l1=12.0, l2=12.0, l3=12.0),
        )
    )
    assert [(v["wallbox"], v["assignment"]) for v in c.health()] == [("evcs", "override")] * 2
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 16.0, reasons=("mode_now",)),
        Command("wallbox.evcs", CHARGING, True, reasons=("mode_now",)),
    ]
    assert [v["limit_a"] for v in c.health()] == [31.0, None]  # 33 − (12 − 10), einmal


def test_vehicle_wallbox_for_the_safety_layer(cfg) -> None:
    c = controller(cfg)
    assert c.vehicle_wallbox() == {}
    c.step(inputs(cfg))
    assert c.vehicle_wallbox() == {"vehicle.tesla": "twc"}
    override = Intent(version=2, control=True, vehicles={"tesla": VehicleIntent(wallbox="evcs")})
    c.step(inputs(cfg, s=1, intent=override))
    assert c.vehicle_wallbox() == {"vehicle.tesla": "evcs"}
    c.idle(T0 + timedelta(seconds=2))
    assert c.vehicle_wallbox() == {}
    # „Tesla an der EVCS“: angenommen am Wall Connector, ab 60 s an der EVCS erkannt
    picture = {"twc_obs": twc(connected=False), "tesla_obs": tesla(plugged=True)}
    detected = controller(cfg)
    run(detected, cfg, 60, **picture)
    assert detected.vehicle_wallbox() == {"vehicle.tesla": "twc"}
    detected.step(inputs(cfg, s=60, **picture))
    assert detected.vehicle_wallbox() == {"vehicle.tesla": "evcs"}
    unclear = controller(cfg)
    run(unclear, cfg, UNCLEAR_FROM_S + 1, **CONTRADICTION)
    assert unclear.vehicle_wallbox() == {"vehicle.tesla": None}


def test_health_before_the_first_step_and_after_a_pause(cfg) -> None:
    c = controller(cfg)
    unknown = {
        "mode": None,
        "boost_until": None,
        "wallbox": None,
        "assignment": None,
        "limit_a": None,
        "plugged": None,
        "charging": None,
        "wakes_today": 0,
        "commands_hour": None,
        "boost_pending": False,
        "override_pending": False,
        "soc_pct": None,
        "soc_source": None,
        "charge_limit_pct": None,
        "planned": None,
    }
    assert c.health() == [
        {"id": "tesla", "name": "Tesla Model 3", **unknown, "reason": None},
        {"id": "egolf", "name": "e-Golf", **unknown, "reason": None},
    ]
    accept(c, c.step(inputs(cfg)))
    c.idle(T0 + timedelta(seconds=1))
    assert c.health()[1] == {"id": "egolf", "name": "e-Golf", **unknown, "reason": "mode_pv"}


def test_cars_show_the_points_of_the_last_step(cfg) -> None:  # Plan 2c-2a, Präzisierung 4
    c = controller(cfg)
    assert c.cars() == ()
    charging = evcs(charging=True, power_w=2760.0)
    c.step(inputs(cfg, evcs_obs=charging, intent=egolf_in(ChargeMode.NOW)))
    # Je Ladepunkt nach Priorität: Der Tesla hat seinen am Wall Connector auch abgesteckt, er lädt
    # nicht (0 W). ``min_w`` ist min_a × 230 V × Phasen: 5 A × 3 beim Tesla, 6 A × 2 beim e-Golf.
    # Geregelt wird nur der Ladepunkt des e-Golf: Der Tesla ist weder steuerbar noch weckbar.
    assert c.cars() == (
        CarView("tesla", "twc", Effective.PV, 0.0, False, 3450.0, False),
        CarView("egolf", "evcs", Effective.NOW, 2760.0, True, 2760.0, True),
    )
    first = Intent(
        version=2,
        control=True,
        vehicles={"egolf": VehicleIntent(mode=ChargeMode.NOW)},
        priority=["egolf", "tesla"],
    )
    c.step(inputs(cfg, s=1, evcs_obs=charging, intent=first))
    assert [car.vehicle for car in c.cars()] == ["egolf", "tesla"]
    c.idle(T0 + timedelta(seconds=2))
    assert c.cars() == ()


def test_cars_tell_whether_the_charge_point_is_regulated(cfg) -> None:  # Präzisierung 11
    # Nur an einem Ladepunkt, den die Laderegelung regelt, wirkt eine Grenze des 15-min-Wächters.
    c = controller(cfg)
    c.step(inputs(cfg))  # die EVCS steuerbar, der Tesla weder steuerbar noch weckbar
    assert [(car.vehicle, car.regulated) for car in c.cars()] == [("tesla", False), ("egolf", True)]
    c.step(inputs(cfg, s=1, controllable={"wallbox.evcs": False, "vehicle.tesla": True}))
    assert [(car.vehicle, car.regulated) for car in c.cars()] == [("tesla", True), ("egolf", False)]


def test_cars_take_the_power_from_tessie_without_the_wallbox(cfg) -> None:
    # Präzisierung 4 (Plan 2c-2a): Fehlt die Leistung der Wallbox, gilt beim Fahrzeug mit Tessie
    # actual_current_a × phases × 230 V, wenn Tessie erreichbar ist und beides kennt.
    c = controller(cfg)
    plugged = tesla(plugged=True, charging=True, actual_current_a=10.0, phases=3)
    c.step(inputs(cfg, twc_obs=TWC_OUT, tesla_obs=plugged))
    assert c.cars()[0] == CarView("tesla", "twc", Effective.PV, 6900.0, True, 3450.0, False)
    unknown = [
        replace(plugged, reachable=False),
        replace(plugged, actual_current_a=None),
        replace(plugged, phases=None),
    ]
    for s, seen in enumerate(unknown, 1):
        c.step(inputs(cfg, s=s, twc_obs=TWC_OUT, tesla_obs=seen))
        assert c.cars()[0].power_w is None
    # Eine Leistung der Wallbox, die keine endliche Zahl ist, fehlt ebenso.
    odd = twc(connected=True, charging=True, power_w=math.nan)
    c.step(inputs(cfg, s=4, twc_obs=odd, tesla_obs=plugged))
    assert c.cars()[0] == CarView("tesla", "twc", Effective.PV, 6900.0, True, 3450.0, False)


def test_without_an_intent_every_vehicle_charges_in_pv(cfg) -> None:
    found = controller(cfg).step(replace(inputs(cfg), intent=None))
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CHARGING, False, reasons=("mode_pv",))
    ]


@pytest.mark.parametrize(
    ("present", "grid_w", "expected"),
    [
        ({}, None, {"surplus_w": None, "actual_current_a": None, "actual_charging": None}),
        (
            at_evcs(10.0, True),
            -1000.0,
            {"surplus_w": 900.0, "actual_current_a": 10.0, "actual_charging": 1.0},
        ),
    ],
    ids=["unknown", "known"],
)
def test_inputs_carry_the_actual_values(cfg, present, grid_w, expected) -> None:
    found = controller(cfg).step(inputs(cfg, present=present, grid_obs=grid(grid_w=grid_w)))
    assert [x.inputs for x in found] == [{**expected, "limit_a": 17.0}]


def test_the_current_range_of_a_charge_point(cfg) -> None:  # Ladepunkt eines Fahrzeugs
    small = with_vehicle(cfg, "egolf", max_a=12)  # an der EVCS bis 16 A
    found = controller(small).step(inputs(small, intent=egolf_in(ChargeMode.NOW)))
    assert found[0].command == Command("wallbox.evcs", CURRENT, 12.0, reasons=("mode_now",))
    twc_10 = cfg.wallboxes["twc"].model_copy(update={"max_a": 10})  # der Tesla bis 16 A
    weak = cfg.model_copy(update={"wallboxes": {**cfg.wallboxes, "twc": twc_10}})
    tesla_now = Intent(version=1, control=True, vehicles={"tesla": NOW})
    found = controller(weak).step(inputs(weak, intent=tesla_now, **TESLA_PLUGGED))
    tesla_currents = [
        x.command.value
        for x in found
        if x.command.actuator == "vehicle.tesla" and x.command.kind == CURRENT
    ]
    assert tesla_currents == [10.0]


def test_unclear_uses_the_range_of_the_evcs(cfg) -> None:  # Präzisierung 5
    strict = with_vehicle(cfg, "egolf", min_a=8, max_a=12)
    found = run(
        controller(strict),
        strict,
        UNCLEAR_FROM_S + 1,
        **CONTRADICTION,
        intent=egolf_in(ChargeMode.NOW),
        present=at_evcs(12.0, True),
        grid_obs=grid(l1=30.0, feed=(10.0, 0.0, 0.0)),
    )
    # Last auf L1 ohne die EVCS: 30 A Netz + 10 A aus dem Akku − 10 A der EVCS. Frei 33 − 30 = 3:
    # mit dem e-Golf sein Mindeststrom 8 A, ab „unklar“ der der EVCS
    assert [(t, c.command.value) for t, c in found if c.command.kind == CURRENT] == [
        (0, 8.0),
        (UNCLEAR_FROM_S, 6.0),
    ]


def test_unknown_power_at_a_wallbox_makes_the_surplus_unknown(cfg) -> None:
    found = run(controller(cfg), cfg, 121, evcs_obs=evcs(power_w=None))
    assert as_commands(found) == [cmd(0, CHARGING, False, ("mode_pv",))]
    assert found[0][1].inputs["surplus_w"] is None


def test_a_reduction_that_does_not_act_yet_stays_reserved(cfg) -> None:  # Präzisierung 7
    # Der Tesla zieht noch 16 A, sein Anteil an 7000 W sind 10 A.
    c = controller(cfg)
    c.step(
        inputs(
            cfg,
            twc_obs=twc(
                connected=True,
                charging=True,
                phase_a=dict.fromkeys(("L1", "L2", "L3"), 16.0),
                power_w=11040.0,
            ),
            tesla_obs=tesla(plugged=True, charging=True),
            controllable=BOTH,
            grid_obs=grid(l1=18.0, l2=18.0, l3=18.0, grid_w=3940.0),
        )
    )
    assert [v["limit_a"] for v in c.health()] == [17.0, 1.0]  # 19 − 2 − 16, nicht − 10


def test_a_share_at_the_limit_comes_from_the_surplus(cfg) -> None:
    charging = evcs(charging=True, phase_a={"L1": 10.0, "L2": 10.0, "L3": 0.0}, power_w=4600.0)
    found = controller(cfg).step(
        inputs(
            cfg,
            evcs_obs=charging,
            present=at_evcs(10.0, True),
            grid_obs=grid(l1=18.0, l2=12.0, grid_w=-560.0),
        )
    )
    # Anteil (4600 + 560 − 100) / 460 = 11, frei 19 − 8 = 11: Der Wächter kappt nicht.
    reasons = ("mode_pv", "pv_surplus")
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 11.0, reasons=reasons),
        Command("wallbox.evcs", CHARGING, True, reasons=reasons),
    ]


def test_a_pv_start_names_the_surplus_even_when_capped(cfg) -> None:
    found = run(
        controller(cfg),
        cfg,
        122,
        grid_obs=lambda s: grid(l1=10.0 if s <= 120 else 11.0, grid_w=-6000.0),
    )
    assert as_commands(found) == [
        cmd(0, CHARGING, False, ("mode_pv",)),
        cmd(120, CURRENT, 9.0, ("mode_pv", "pv_surplus")),  # Anteil 12, frei 19 − 10
        cmd(120, CHARGING, True, ("mode_pv", "pv_surplus")),
        cmd(121, CURRENT, 8.0, ("mode_pv", "import_limit")),
    ]


def test_after_an_overload_stop_the_minimum_pause_holds(cfg) -> None:  # Präzisierung 8
    found = run(
        controller(cfg),
        cfg,
        607,
        intent=egolf_in(ChargeMode.NOW),
        evcs_obs=AT_16_A,
        present=at_evcs(16.0, True),
        grid_obs=lambda s: grid(l1=34.0, l2=20.0) if s < 7 else grid(l1=20.0, l2=18.0),
    )
    # Stopp bei 6, die Überlast endet bei 67; Sofort startet erst nach min_off_s
    assert [(t, c.command.value) for t, c in found if c.command.kind == CHARGING] == [
        (0, True),
        (6, False),
        (606, True),
    ]


def test_the_guard_rounds_the_free_current_down(cfg) -> None:
    found = controller(cfg).step(inputs(cfg, intent=egolf_in(ChargeMode.NOW), grid_obs=grid(21.4)))
    reasons = ("mode_now", "phase_limit")  # frei 33 − 21,4 = 11,6
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 11.0, reasons=reasons),
        Command("wallbox.evcs", CHARGING, True, reasons=reasons),
    ]


def test_an_unknown_load_allows_only_the_minimum(cfg) -> None:  # Design 5.5
    # Die Phase gilt als voll: höchstens der Mindeststrom, und ein Start unterbleibt.
    c = controller(cfg)
    found = c.step(inputs(cfg, intent=egolf_in(ChargeMode.NOW), grid_obs=grid(l1=None)))
    reasons = ("mode_now", "phase_limit")
    assert [x.command for x in found] == [Command("wallbox.evcs", CURRENT, 6.0, reasons=reasons)]
    assert c.health()[1]["limit_a"] is None


def test_a_current_that_does_not_drop_is_not_urgent(cfg) -> None:  # Präzisierung 11
    found = controller(cfg).step(
        inputs(
            cfg,
            intent=egolf_in(ChargeMode.NOW),
            evcs_obs=AT_16_A,
            present=at_evcs(16.0, True),
            grid_obs=grid(l1=20.0, l2=18.0),  # frei 29 A
        )
    )
    assert found[0].command == Command("wallbox.evcs", CURRENT, 16.0, reasons=("mode_now",))


def test_min_pv_takes_its_minimum_from_the_surplus_first(cfg) -> None:  # Präzisierung 7
    intent = Intent(
        version=1, control=True, vehicles={"tesla": VehicleIntent(mode=ChargeMode.MIN_PV)}
    )
    found = run(
        controller(cfg), cfg, 121, intent=intent, grid_obs=grid(grid_w=-3100.0), **TESLA_PLUGGED
    )
    # 3000 W: Der Tesla nimmt seine 5 A (3450 W) zuerst; für 6 A am e-Golf bleibt nichts.
    assert [x for x in as_commands(found) if x[1].actuator == "wallbox.evcs"] == [
        cmd(0, CHARGING, False, ("mode_pv",))
    ]


def test_the_surplus_priority_comes_from_the_intent(cfg) -> None:
    charging_battery = grid(grid_w=0.0, battery_w=3000.0)
    battery_first = Intent(
        version=1,
        control=True,
        surplus_priority=SurplusPriority(kind=SurplusOrder.BATTERY_FIRST),
    )
    found = run(controller(cfg), cfg, 121, intent=battery_first, grid_obs=charging_battery)
    assert as_commands(found) == [cmd(0, CHARGING, False, ("mode_pv",))]
    # Vorgabe „Akku bis 30 %“ bei SOC 60 %: Die 3000 W des Akkus zählen, Start bei 120
    found = run(controller(cfg), cfg, 121, grid_obs=charging_battery)
    assert [t for t, _ in found] == [0, 120, 120]


@pytest.mark.parametrize(
    "override",
    [{"present": {}}, {"blocked": frozenset({("wallbox.evcs", CURRENT)})}],
    ids=["actual_unknown", "current_blocked"],
)
def test_unknown_plug_state_lowers_nothing_it_cannot_compare_or_send(cfg, override) -> None:
    unknown = replace(AT_16_A, connected=None, charging=None, complete=None)
    situation = {
        "intent": egolf_in(ChargeMode.NOW),
        "evcs_obs": unknown,
        "present": at_evcs(16.0, True),
        "grid_obs": grid(l1=36.0, l2=20.0),  # frei 13 A
    }
    assert controller(cfg).step(inputs(cfg, **{**situation, **override})) == []


def test_a_pause_forgets_not_taken_too(cfg) -> None:
    c = controller(cfg)
    run(c, cfg, 121)  # Start bei 120 mit 8 A
    c.note_result(
        WriteResult("wallbox.evcs", CURRENT, 8.0, DecisionResult.ERROR, "x", not_taken=True)
    )
    c.idle(T0 + timedelta(seconds=121))
    resumed = c.step(inputs(cfg, s=200, evcs_obs=evcs(charging=True)))
    reasons = ("mode_pv", "pv_surplus", "resume_after_outage")
    assert [x.command for x in resumed] == [
        Command("wallbox.evcs", CURRENT, 8.0, reasons=reasons),
        Command("wallbox.evcs", CHARGING, True, reasons=reasons),
    ]


def test_a_vehicle_without_tessie_gets_no_current_from_tessie(cfg) -> None:  # Präzisierung 6
    plain = with_vehicle(cfg, "tesla", tessie=None, live_allowed=False)
    c = ChargeController(plain, HardLimits.from_config(plain), started=T0)
    c.step(
        inputs(
            plain, intent=egolf_in(ChargeMode.NOW), twc_obs=TWC_OUT, tesla_obs=tesla(plugged=True)
        )
    )
    assert c.health()[1]["limit_a"] == 15.0  # Zuschlag 16 A trotz Werten von Tessie: 33 − 18


def test_a_charge_point_that_is_not_controllable_is_load(cfg) -> None:  # Präzisierung 6
    c = controller(cfg)
    c.step(inputs(cfg, controllable={"wallbox.evcs": False, "vehicle.tesla": False}))
    assert c.health()[1]["limit_a"] is None


# Nachbesserung: Das Gerät ändert sich ohne die Regelung (neuer Ansteckvorgang, Rückkehr in die
# Regelung, Überlast)

TESLA_16_A = twc(
    connected=True,
    charging=True,
    phase_a=dict.fromkeys(("L1", "L2", "L3"), 16.0),
    power_w=11040.0,
)
BOTH_MODES = pytest.mark.parametrize(
    ("mode", "code"), [(ChargeMode.PV, "mode_pv"), (ChargeMode.OFF, "mode_off")], ids=["pv", "off"]
)


def plugged_again(s: int) -> dict:
    """Der Tesla steht bis 10 s am Wall Connector, ist bis 60 s weg und lädt danach von selbst."""
    if s < 10:
        return {"twc_obs": twc(connected=True), "tesla_obs": tesla(plugged=True)}
    if s < 60:
        return {"twc_obs": twc(), "tesla_obs": tesla()}
    return {"twc_obs": TESLA_16_A, "tesla_obs": tesla(plugged=True, charging=True)}


def tesla_inputs(cfg, *, s: int, l1: float = 18.0, **kw):
    """Die Eingaben zu ``plugged_again``; ab 60 s zieht der Tesla 16 A je Phase aus dem Netz."""
    loaded = grid(l1=l1, l2=18.0, l3=18.0, grid_w=12420.0)
    return inputs(cfg, s=s, grid_obs=grid() if s < 60 else loaded, **plugged_again(s), **kw)


@BOTH_MODES
def test_a_car_that_starts_by_itself_in_a_new_session_is_stopped(cfg, mode, code) -> None:
    intent = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(mode=mode)})
    found = run(controller(cfg), cfg, 70, make=tesla_inputs, intent=intent, controllable=BOTH)
    off = Command("vehicle.tesla", CHARGING, False, urgent=code == "mode_off", reasons=(code,))
    assert [(t, c.command) for t, c in found if c.command.actuator == "vehicle.tesla"] == [
        (0, off),
        (60, off),  # neuer Ansteckvorgang: Der letzte Wert „aus“ ist vergessen
    ]


@BOTH_MODES
def test_overload_stops_a_car_that_charges_against_its_mode(cfg, mode, code) -> None:
    intent = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(mode=mode)})
    found = run(
        controller(cfg),
        cfg,
        120,
        make=tesla_inputs,
        intent=intent,
        controllable=BOTH,
        l1=lambda s: 36.0 if s >= 90 else 18.0,  # ab 90 s über der Sicherung
    )
    off = Command("vehicle.tesla", CHARGING, False, urgent=code == "mode_off", reasons=(code,))
    urgent = Command("vehicle.tesla", CHARGING, False, urgent=True, reasons=(code, "overload"))
    # Der Tesla lädt weiter (Dry-Run): Jede Stufe der Überlast stoppt ihn dringend.
    assert [(t, c.command) for t, c in found if c.command.actuator == "vehicle.tesla"] == [
        (0, off),
        (60, off),
        (93, urgent),  # MINIMUM
        (96, urgent),  # EVCS_STOP
        (100, urgent),  # ALL_STOP
    ]


def test_a_charge_point_back_under_control_is_taken_over(cfg) -> None:  # Präzisierung 9
    # Der Tesla lädt bei 12,8 kW Überschuss; von 10 bis 409 s ist er nicht steuerbar.
    found = run(
        controller(cfg),
        cfg,
        420,
        twc_obs=TESLA_16_A,
        tesla_obs=tesla(plugged=True, charging=True),
        controllable=lambda s: {"wallbox.evcs": True, "vehicle.tesla": not 10 <= s < 410},
        present={("vehicle.tesla", CURRENT): 16.0, ("vehicle.tesla", CHARGING): True},
        grid_obs=grid(l1=18.0, l2=18.0, l3=18.0, grid_w=-1860.0),
    )
    charging = [
        (t, c.command.value)
        for t, c in found
        if c.command.actuator == "vehicle.tesla" and c.command.kind == CHARGING
    ]
    assert charging == [(0, True), (410, True)]  # Übernahme statt Stopp, wie nach einer Pause


def test_after_a_pause_each_overload_stage_reacts_again(cfg) -> None:
    # Der Tesla lädt in Aus von selbst, L1 liegt durchgehend über der Sicherung.
    c = controller(cfg)
    against = {
        "intent": Intent(
            version=1, control=True, vehicles={"tesla": VehicleIntent(mode=ChargeMode.OFF)}
        ),
        "controllable": BOTH,
        "twc_obs": TESLA_16_A,
        "tesla_obs": tesla(plugged=True, charging=True),
        "grid_obs": grid(l1=36.0, l2=18.0, l3=18.0, grid_w=12420.0),
    }
    run(c, cfg, 11, **against)  # Stufen bei 3, 6 und 10
    c.idle(T0 + timedelta(seconds=11))
    found = []
    for s in range(20, 31):
        commands = c.step(inputs(cfg, s=s, **against))
        accept(c, commands)
        found.extend((s, x.command) for x in commands if x.command.actuator == "vehicle.tesla")
    off = Command("vehicle.tesla", CHARGING, False, urgent=True, reasons=("mode_off",))
    urgent = Command(
        "vehicle.tesla", CHARGING, False, urgent=True, reasons=("mode_off", "overload")
    )
    assert found == [(20, off), (23, urgent), (26, urgent), (30, urgent)]


def test_a_new_session_forgets_not_taken(cfg) -> None:
    c = controller(cfg)
    now = egolf_in(ChargeMode.NOW)
    accept(c, c.step(inputs(cfg, intent=now)))  # Strom 16 A, Laden an
    c.note_result(
        WriteResult("wallbox.evcs", CURRENT, 16.0, DecisionResult.ERROR, "x", not_taken=True)
    )
    accept(c, c.step(inputs(cfg, s=1, intent=now, evcs_obs=evcs(connected=False))))
    again = c.step(inputs(cfg, s=2, intent=now))  # neuer Ansteckvorgang
    assert [x.command for x in again] == [
        Command("wallbox.evcs", CURRENT, 16.0, reasons=("mode_now",)),
        Command("wallbox.evcs", CHARGING, True, reasons=("mode_now",)),
    ]


def late_tessie(s: int, *, away_until: int, reported_at: int) -> dict:
    """Der Tesla ist bis ``away_until`` weg, dann meldet der Wall Connector ihn, und er lädt von
    selbst; Tessie meldet „angesteckt“ erst ab ``reported_at``. Bis dahin ist der Aktor weder
    steuerbar noch weckbar. Vor ``away_until - 50`` stand er angesteckt und steuerbar da.
    """
    if s >= reported_at:
        tesla_obs, controllable = tesla(plugged=True, charging=True), BOTH
    else:
        tesla_obs, controllable = tesla(), None
    if s >= away_until:
        return {
            "twc_obs": TESLA_16_A,
            "tesla_obs": tesla_obs,
            "controllable": controllable,
            "grid_obs": grid(l1=18.0, l2=18.0, l3=18.0, grid_w=12420.0),
        }
    if s < away_until - 50:
        return {
            "twc_obs": twc(connected=True),
            "tesla_obs": tesla(plugged=True),
            "controllable": BOTH,
        }
    return {"twc_obs": twc(), "tesla_obs": tesla()}


@pytest.mark.parametrize(
    ("away_until", "reported_at", "expected"),
    [(60, 65, [0, 65]), (10, 15, [15])],
    ids=["regulated_before", "never_regulated_since_start"],
)
def test_a_session_that_begins_unregulated_starts_without_charging(  # Präzisierung 9
    cfg, away_until, reported_at, expected
) -> None:
    def make(cfg, *, s: int):
        return inputs(cfg, s=s, **late_tessie(s, away_until=away_until, reported_at=reported_at))

    found = run(controller(cfg), cfg, reported_at + 5, make=make)
    off = Command("vehicle.tesla", CHARGING, False, reasons=("mode_pv",))
    assert [(t, c.command) for t, c in found if c.command.actuator == "vehicle.tesla"] == [
        (t, off) for t in expected
    ]


def test_a_remembered_session_waits_for_a_known_plug_state(cfg) -> None:
    # PV lädt seit 120; von 125 bis 129 ist die EVCS nicht steuerbar, das Auto wird ab- und wieder
    # angesteckt und lädt von selbst; bei 130 ist der Status unbekannt, ab 131 wieder bekannt.
    def make(cfg, *, s: int):
        if s < 125:
            return inputs(cfg, s=s)
        if s < 130:
            seen = evcs(connected=False) if s < 127 else evcs(charging=True)
            return inputs(cfg, s=s, evcs_obs=seen, controllable={"wallbox.evcs": False})
        if s == 130:
            return inputs(cfg, s=s, evcs_obs=evcs(connected=None, charging=None, complete=None))
        return inputs(cfg, s=s, evcs_obs=evcs(charging=True))

    found = run(controller(cfg), cfg, 132, make=make)
    assert [c for c in as_commands(found) if c[0] >= 125] == [
        cmd(131, CHARGING, False, ("mode_pv",))
    ]


def test_a_remembered_session_forgets_the_last_values_once(cfg) -> None:
    # PV lädt seit 0 mit 16 A. Von 10 bis 14 ist die EVCS nicht steuerbar; der e-Golf wird ab- und
    # wieder angesteckt. Ab 15 ist der Status unbekannt, und das Importlimit verlangt 11 A: Die
    # Senkung geht einmal hinaus, nicht jede Sekunde.
    unknown = replace(AT_16_A, connected=None, charging=None, complete=None)

    def make(cfg, *, s: int):
        if s < 10:
            return inputs(cfg, s=s, evcs_obs=AT_16_A, present=at_evcs(16.0, True))
        if s < 15:
            seen = evcs(connected=False) if s < 12 else AT_16_A
            not_controllable = {"wallbox.evcs": False}
            return inputs(cfg, s=s, evcs_obs=seen, controllable=not_controllable)
        return inputs(
            cfg,
            s=s,
            evcs_obs=unknown,
            present=at_evcs(16.0, True),
            grid_obs=grid(l1=24.0, l2=18.0),  # frei 19 − (24 − 16) = 11
        )

    found = run(controller(cfg), cfg, 25, make=make)
    assert [c for c in as_commands(found) if c[0] >= 10] == [
        cmd(15, CURRENT, 11.0, ("mode_pv", "import_limit"))
    ]


def test_a_remembered_session_reaching_the_machine_forgets_once_more(cfg) -> None:
    # Sofort lädt mit 16 A. Wie oben wird der e-Golf neu angesteckt, während die EVCS nicht
    # steuerbar ist; bei 15 ist der Status unbekannt, und die Sicherung verlangt 9 A. Bei 16 ist er
    # bekannt: Der gemerkte Vorgang erreicht die Maschine, und die Regelung vergisst ihre Werte
    # noch einmal, denn das Gerät kann sich mit dem neuen Vorgang geändert haben.
    unknown = replace(AT_16_A, connected=None, charging=None, complete=None)
    now = egolf_in(ChargeMode.NOW)

    def make(cfg, *, s: int):
        if s < 10:
            return inputs(cfg, s=s, intent=now, evcs_obs=AT_16_A, present=at_evcs(16.0, True))
        if s < 15:
            seen = evcs(connected=False) if s < 12 else AT_16_A
            not_controllable = {"wallbox.evcs": False}
            return inputs(cfg, s=s, intent=now, evcs_obs=seen, controllable=not_controllable)
        return inputs(
            cfg,
            s=s,
            intent=now,
            evcs_obs=unknown if s == 15 else AT_16_A,
            present=at_evcs(16.0, True),
            grid_obs=grid(l1=40.0, l2=18.0),  # frei 33 − (40 − 16) = 9
        )

    found = run(controller(cfg), cfg, 17, make=make)
    reasons = ("mode_now", "phase_limit")
    assert [c for c in as_commands(found) if c[0] >= 10] == [
        cmd(15, CURRENT, 9.0, reasons, urgent=True),
        cmd(16, CURRENT, 9.0, reasons, urgent=True),
        cmd(16, CHARGING, True, reasons),
    ]


# Plan 2c-2a: Grenzen des 15-min-Wächters (Präzisierungen 11 und 13)

NOW_AT_16_A = {
    "intent": egolf_in(ChargeMode.NOW),
    "evcs_obs": AT_16_A,
    "present": at_evcs(16.0, True),
}


def test_peak_cap_limits_the_current_with_its_reason(cfg) -> None:
    # Der e-Golf lädt an der EVCS in Sofort mit 16 A; frei sind 33 − (18 − 16) = 31 A. Die Grenze
    # des 15-min-Wächters 4600 W ist 4600 / (230 V · 2 Phasen) = 10 A.
    c = controller(cfg)
    found = c.step(
        inputs(cfg, grid_obs=grid(l1=18.0, l2=18.0), **NOW_AT_16_A),
        PeakLimits(caps_w={"egolf": 4600.0}),
    )
    reasons = ("mode_now", "peak_guard")
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 10.0, reasons=reasons),  # nicht dringend
        Command("wallbox.evcs", CHARGING, True, reasons=reasons),
    ]
    # Entscheidungslog und Health nennen weiter den freien Strom des Phasenwächters.
    assert found[0].inputs["limit_a"] == 31.0
    assert c.health()[1]["limit_a"] == 31.0


def test_peak_cap_below_the_minimum_stops_a_pv_car_by_its_rules(cfg) -> None:
    # Der e-Golf lädt in PV mit 8 A, sein Anteil an 3680 + 1380 − 100 = 4960 W sind 10 A. Die Grenze
    # 0 W lässt keinen Strom frei: Der Strom geht auf den Mindeststrom, und „Laden aus“ folgt nach
    # den Regeln von PV-Überschuss, erst nach stop_after_s (300 s).
    charging = {
        "evcs_obs": AT_8_A,
        "present": at_evcs(8.0, True),
        "grid_obs": grid(l1=10.0, l2=10.0, grid_w=-1380.0),
    }
    found = run(controller(cfg), cfg, 302, peak=PeakLimits(caps_w={"egolf": 0.0}), **charging)
    assert as_commands(found) == [
        cmd(0, CURRENT, 6.0, ("mode_pv", "peak_guard")),
        cmd(0, CHARGING, True, ("mode_pv", "peak_guard")),
        cmd(300, CHARGING, False, ("mode_pv", "pv_surplus")),
    ]
    # Ohne Grenze lädt er mit seinem Anteil weiter.
    reasons = ("mode_pv", "pv_surplus")
    assert as_commands(run(controller(cfg), cfg, 302, **charging)) == [
        cmd(0, CURRENT, 10.0, reasons),
        cmd(0, CHARGING, True, reasons),
    ]


def test_no_start_waits(cfg) -> None:  # Präzisierung 13
    # Der e-Golf in Min+PV ist angesteckt und lädt nicht: In der Start-Sperre bleibt „Laden an“ aus;
    # fällt sie weg, startet er nach den Regeln seines Modus.
    reasons = ("mode_min_pv", "pv_surplus")
    min_pv = {"intent": egolf_in(ChargeMode.MIN_PV), "grid_obs": grid(grid_w=2000.0)}
    blocked = PeakLimits(no_start=frozenset({"egolf"}))
    found = run(controller(cfg), cfg, 10, peak=blocked, **min_pv)
    assert as_commands(found) == [cmd(0, CURRENT, 6.0, reasons)]
    found = run(controller(cfg), cfg, 10, **min_pv)
    assert as_commands(found) == [cmd(0, CURRENT, 6.0, reasons), cmd(0, CHARGING, True, reasons)]
    found = run(controller(cfg), cfg, 10, peak=lambda s: blocked if s < 5 else NO_LIMITS, **min_pv)
    assert as_commands(found) == [cmd(0, CURRENT, 6.0, reasons), cmd(5, CHARGING, True, reasons)]


def test_no_start_leaves_a_charging_car_alone(cfg) -> None:  # Präzisierung 13
    situation = inputs(cfg, grid_obs=grid(l1=18.0, l2=18.0), **NOW_AT_16_A)
    blocked = PeakLimits(no_start=frozenset({"egolf"}))
    found = controller(cfg).step(situation, blocked)
    assert found == controller(cfg).step(situation)
    assert [x.command.kind for x in found] == [CURRENT, CHARGING]


def test_boost_ignores_peak_limits(cfg) -> None:
    until = T0 + timedelta(hours=2)
    intent = Intent(version=1, control=True, vehicles={"egolf": VehicleIntent(boost_until=until)})
    situation = inputs(cfg, intent=intent, grid_obs=grid(l1=20.0, l2=20.0))
    limits = PeakLimits(caps_w={"egolf": 0.0}, no_start=frozenset({"egolf"}))
    found = controller(cfg).step(situation, limits)
    assert found == controller(cfg).step(situation)
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 13.0, reasons=("boost", "phase_limit")),
        Command("wallbox.evcs", CHARGING, True, reasons=("boost", "phase_limit")),
    ]


def test_a_peak_cap_that_does_not_bind_changes_nothing(cfg) -> None:
    # Frei sind 33 − (40 − 16) = 9 A; die Grenze 4600 W sind 10 A: Es bleibt beim Phasenwächter.
    capped = PeakLimits(caps_w={"egolf": 4600.0})
    situation = inputs(cfg, grid_obs=grid(l1=40.0, l2=18.0), **NOW_AT_16_A)
    without = controller(cfg).step(situation)
    assert controller(cfg).step(situation, capped) == without
    reasons = ("mode_now", "phase_limit")
    assert without[0].command == Command("wallbox.evcs", CURRENT, 9.0, urgent=True, reasons=reasons)
    # Ein unbekannter freier Strom bleibt unbekannt: höchstens der Mindeststrom, kein Start.
    unknown = inputs(cfg, intent=egolf_in(ChargeMode.NOW), grid_obs=grid(l1=None))
    without = controller(cfg).step(unknown)
    assert controller(cfg).step(unknown, capped) == without
    assert [x.command for x in without] == [Command("wallbox.evcs", CURRENT, 6.0, reasons=reasons)]


def test_a_lowering_the_fuse_demands_stays_urgent_below_a_peak_cap(cfg) -> None:
    # Wieder 9 A frei an der Sicherung, die Grenze 3680 W ist aber 3680 / 460 = 8 A: Der Strom geht
    # auf 8 A mit dem Grund des 15-min-Wächters. Dringend bleibt die Senkung, weil die Sicherung
    # allein sie schon verlangt; mit 31 A frei ist dieselbe Senkung normal.
    capped = PeakLimits(caps_w={"egolf": 3680.0})
    reasons = ("mode_now", "peak_guard")
    fuse = controller(cfg).step(inputs(cfg, grid_obs=grid(l1=40.0, l2=18.0), **NOW_AT_16_A), capped)
    assert fuse[0].command == Command("wallbox.evcs", CURRENT, 8.0, urgent=True, reasons=reasons)
    free = controller(cfg).step(inputs(cfg, grid_obs=grid(l1=18.0, l2=18.0), **NOW_AT_16_A), capped)
    assert free[0].command == Command("wallbox.evcs", CURRENT, 8.0, reasons=reasons)


@pytest.mark.parametrize(("l1", "urgent"), [(18.0, False), (40.0, True)], ids=["cap", "fuse_too"])
def test_unknown_plug_state_lowers_to_the_peak_cap(cfg, l1, urgent) -> None:  # Präzisierung 4
    # Bei unbekanntem Ansteckzustand sinkt der Strom nur für eine Grenze, auch für die des
    # 15-min-Wächters (8 A); dringend nur, wenn die Sicherung es verlangt (bei 40 A auf L1 9 A).
    unknown = replace(AT_16_A, connected=None, charging=None, complete=None)
    situation = inputs(
        cfg,
        intent=egolf_in(ChargeMode.NOW),
        evcs_obs=unknown,
        present=at_evcs(16.0, True),
        grid_obs=grid(l1=l1, l2=18.0),
    )
    found = controller(cfg).step(situation, PeakLimits(caps_w={"egolf": 3680.0}))
    reasons = ("mode_now", "peak_guard")
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 8.0, urgent=urgent, reasons=reasons)
    ]


def test_the_tesla_window_sees_the_capped_free_current(cfg) -> None:  # Präzisierung 12
    # Der Tesla in Sofort am Wall Connector; die Grenze 6900 W ist 6900 / (230 V · 3 Phasen) = 10 A
    # und gilt nur in der ersten Sekunde. Das vorsichtige Fenster (tesla_caution_s, 60 s) hält die
    # 10 A, bis sie herausfallen.
    tesla_now = Intent(version=1, control=True, vehicles={"tesla": NOW})
    capped = PeakLimits(caps_w={"tesla": 6900.0})
    found = run(
        controller(cfg),
        cfg,
        62,
        intent=tesla_now,
        peak=lambda s: capped if s == 0 else NO_LIMITS,
        **TESLA_PLUGGED,
    )
    reasons = ("mode_now", "peak_guard")
    assert [(t, c.command) for t, c in found if c.command.actuator == "vehicle.tesla"] == [
        (0, Command("vehicle.tesla", CURRENT, 10.0, reasons=reasons)),
        (0, Command("vehicle.tesla", CHARGING, True, reasons=reasons)),
        (61, Command("vehicle.tesla", CURRENT, 16.0, reasons=("mode_now",))),
    ]


def test_a_cap_at_the_minimum_power_carries_the_minimum_current(cfg) -> None:
    # Mit 229,7 V ist die Mindestleistung des e-Golf 6 A · 229,7 V · 2 = 2756,4 W; zurückgerechnet
    # landet sie knapp unter 6 A. Die Grenze genau am Boden trägt trotzdem den Mindeststrom: Der
    # e-Golf in Sofort startet mit 6 A.
    grid_229 = cfg.grid.model_copy(update={"voltage_nominal_v": 229.7})
    odd = cfg.model_copy(update={"grid": grid_229})
    limits = PeakLimits(caps_w={"egolf": 6 * 229.7 * 2})
    found = controller(odd).step(inputs(odd, intent=egolf_in(ChargeMode.NOW)), limits)
    reasons = ("mode_now", "peak_guard")
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 6.0, reasons=reasons),
        Command("wallbox.evcs", CHARGING, True, reasons=reasons),
    ]


# Plan 2c-2b: der Gast und der e-Golf am Wall Connector (Präzisierungen 6 und 8)

GUEST_NOW = Intent(version=1, control=True, guest=GuestIntent(mode=ChargeMode.NOW))


def test_a_guest_at_the_evcs_charges_in_the_guest_mode(cfg) -> None:  # Design 7.4
    c = controller(cfg)
    found = run(c, cfg, 61, intent=GUEST_NOW, homes={"egolf": False})
    assert [(t, x.command) for t, x in found] == [
        (0, Command("wallbox.evcs", CURRENT, 16.0, reasons=("mode_now",))),
        (0, Command("wallbox.evcs", CHARGING, True, reasons=("mode_now",))),
    ]
    assert c.health()[1]["wallbox"] is None  # der e-Golf steht nirgends


def test_a_guest_at_the_wall_connector_is_load(cfg) -> None:  # Design 7.4
    c = controller(cfg)
    twc_16 = twc(
        connected=True,
        charging=True,
        phase_a=dict.fromkeys(("L1", "L2", "L3"), 16.0),
        power_w=11040.0,
    )
    found = run(c, cfg, 61, twc_obs=twc_16, homes={"egolf": False}, controllable=BOTH)
    assert not any(x.command.actuator == "vehicle.tesla" for _, x in found)
    guest = next(car for car in c.cars() if car.wallbox == "twc")
    assert (guest.vehicle, guest.mode, guest.regulated) == ("guest@twc", Effective.PV, False)


def test_the_egolf_at_the_wall_connector_is_load_in_its_mode(cfg) -> None:  # Design 7.4
    c = controller(cfg)
    egolf = twc(
        connected=True, charging=True, phase_a={"L1": 16.0, "L2": 16.0, "L3": 0.0}, power_w=7360.0
    )
    intent = Intent(
        version=1, control=True, vehicles={"egolf": VehicleIntent(mode=ChargeMode.MIN_PV)}
    )
    found = run(
        c,
        cfg,
        121,
        twc_obs=egolf,
        evcs_obs=evcs(connected=False),
        homes={"egolf": True},
        intent=intent,
        controllable=BOTH,
    )
    assert not any(x.command.actuator == "vehicle.tesla" for _, x in found)
    car = next(car for car in c.cars() if car.wallbox == "twc")  # erkannt bei 120 s: Phasen ab 60 s
    assert (car.vehicle, car.mode, car.regulated) == ("egolf", Effective.MIN_PV, False)
    assert c.health()[1]["assignment"] == "detected"


# Ergänzungen: Rang, neuer Vorgang, Pause und Ausfall beim Gast; Ende des Grundes


def test_a_guest_comes_after_every_vehicle(cfg) -> None:  # Präzisierung 6
    # Der Tesla am Wall Connector und ein Gast an der EVCS, beide in Sofort; die Absicht nennt den
    # e-Golf vor dem Tesla. Ziel 33 A, Last 2 A: Der Tesla bekommt 16 A, dem Gast bleiben 15 A.
    intent = Intent(
        version=1,
        control=True,
        vehicles={"tesla": NOW},
        priority=["egolf", "tesla"],
        guest=GuestIntent(mode=ChargeMode.NOW),
    )
    c = controller(cfg)
    found = c.step(inputs(cfg, intent=intent, homes={"egolf": False}, **TESLA_PLUGGED))
    currents = [(x.command.actuator, x.command.value) for x in found if x.command.kind is CURRENT]
    assert currents == [("vehicle.tesla", 16.0), ("wallbox.evcs", 15.0)]
    assert [car.vehicle for car in c.cars()] == ["tesla", "guest@evcs"]


def test_a_guest_in_a_new_session_starts_in_its_mode(cfg) -> None:  # Präzisierung 6
    # Bis 4 s meldet die EVCS kein Fahrzeug, der e-Golf steht dort abgesteckt. Ab 5 s steht dort ein
    # Gast, denn der e-Golf ist nicht zu Hause: Sein neuer Vorgang beginnt in Sofort.
    found = run(
        controller(cfg),
        cfg,
        7,
        intent=GUEST_NOW,
        homes={"egolf": False},
        evcs_obs=lambda s: evcs() if s >= 5 else evcs(connected=False),
    )
    assert as_commands(found) == [
        cmd(0, CHARGING, False, ("mode_pv",)),
        cmd(5, CURRENT, 16.0, ("mode_now",)),
        cmd(5, CHARGING, True, ("mode_now",)),
    ]


def test_after_a_pause_the_first_start_of_a_guest_resumes(cfg) -> None:  # Präzisierung 6
    # wie bei einem Fahrzeug mit resume_after_outage (Plan 2c-1, Präzisierung 9)
    c = controller(cfg)
    guest = {"intent": GUEST_NOW, "homes": {"egolf": False}}
    run(c, cfg, 2, **guest)  # der Gast an der EVCS startet bei 0
    c.idle(T0 + timedelta(seconds=2))
    resumed = ("mode_now", "resume_after_outage")
    assert [x.command for x in c.step(inputs(cfg, s=10, **guest))] == [
        Command("wallbox.evcs", CURRENT, 16.0, reasons=resumed),
        Command("wallbox.evcs", CHARGING, True, reasons=resumed),
    ]


def test_the_peak_guard_can_throttle_a_guest_at_the_evcs(cfg) -> None:  # Präzisierung 6
    # Der Gast an der EVCS in Sofort lädt auf allen drei Phasen: Die Grenze 4140 W des
    # 15-min-Wächters ist 4140 / (230 V · 3 Phasen) = 6 A.
    found = controller(cfg).step(
        inputs(cfg, intent=GUEST_NOW, homes={"egolf": False}),
        PeakLimits(caps_w={"guest@evcs": 4140.0}),
    )
    reasons = ("mode_now", "peak_guard")
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 6.0, reasons=reasons),
        Command("wallbox.evcs", CHARGING, True, reasons=reasons),
    ]


def test_a_guest_at_a_wall_connector_that_is_out_reserves_its_maximum(cfg) -> None:
    # Präzisierung 6: Am Wall Connector steht ein Gast, denn Tessie sieht den Tesla abgesteckt; ab
    # 1 s fällt der Wall Connector aus. Sein Höchststrom zählt als Zuschlag, und die Leistung des
    # Gastes ist unbekannt: Tessie misst ihn nicht.
    c = controller(cfg)
    c.step(inputs(cfg, intent=egolf_in(ChargeMode.NOW), twc_obs=twc(connected=True)))
    c.step(inputs(cfg, s=1, intent=egolf_in(ChargeMode.NOW), twc_obs=TWC_OUT))
    assert c.health()[1]["limit_a"] == 15.0  # 33 − 2 − 16
    assert c.cars()[-1] == CarView("guest@twc", "twc", Effective.PV, None, None, 4140.0, False)


def test_the_reason_lapses_when_the_car_is_no_longer_detected(cfg) -> None:  # Präzisierung 8
    # Der e-Golf lädt am Wall Connector, ab 120 s dort erkannt; Befehle bekommt er dort keine, er
    # ist Last. Bei 130 s steckt er ab und steht wieder angenommen an der EVCS: Ihr „Laden aus“
    # trägt den Grund nicht.
    egolf = twc(
        connected=True, charging=True, phase_a={"L1": 16.0, "L2": 16.0, "L3": 0.0}, power_w=7360.0
    )
    found = run(
        controller(cfg),
        cfg,
        131,
        twc_obs=lambda s: egolf if s < 130 else twc(),
        evcs_obs=evcs(connected=False),
        homes={"egolf": True},
    )
    off = ("mode_pv",)
    assert as_commands(found) == [cmd(0, CHARGING, False, off), cmd(130, CHARGING, False, off)]


# Plan 2c-2b: Health je Wallbox (Präzisierung 11)

NOBODY = [
    {"id": wallbox, "occupant": None, "name": None, "assignment": None, "indicators": []}
    for wallbox in ("evcs", "twc")
]


def test_wallbox_health_shows_who_stands_where(cfg) -> None:
    c = controller(cfg)
    run(c, cfg, 61, twc_obs=twc(connected=False), tesla_obs=tesla(plugged=True))
    assert c.wallbox_health() == [
        {
            "id": "evcs",
            "occupant": "tesla",
            "name": "Tesla Model 3",
            "assignment": "detected",
            "indicators": [
                {"vehicle": "tesla", "clue": "tessie", "supports": True},
                {"vehicle": "tesla", "clue": "elsewhere", "supports": True},
            ],
        },
        NOBODY[1],
    ]


def test_wallbox_health_names_a_guest(cfg) -> None:
    c = controller(cfg)
    run(c, cfg, 1, homes={"egolf": False})
    evcs_ = c.wallbox_health()[0]
    assert (evcs_["occupant"], evcs_["name"], evcs_["assignment"]) == ("guest", "Gast", "default")


def test_wallbox_health_names_a_detected_guest(cfg) -> None:
    # Ab 60 s ist jedes Fahrzeug an der EVCS ausgeschlossen: Der Gast ist erkannt, nicht mehr nur
    # angenommen. Die Indizien sind die, die die Fahrzeuge dort ausschließen.
    c = controller(cfg)
    run(c, cfg, 61, homes={"egolf": False})
    assert c.wallbox_health()[0] == {
        "id": "evcs",
        "occupant": "guest",
        "name": "Gast",
        "assignment": "detected",
        "indicators": [
            {"vehicle": "tesla", "clue": "tessie", "supports": False},
            {"vehicle": "egolf", "clue": "home", "supports": False},
        ],
    }


def test_wallbox_health_is_empty_before_the_first_step_and_after_idle(cfg) -> None:
    c = controller(cfg)
    assert c.wallbox_health() == NOBODY
    run(c, cfg, 1)
    c.idle(T0 + timedelta(seconds=1))
    assert c.wallbox_health() == NOBODY


def test_wallbox_health_shows_an_assumed_car_and_its_clues(cfg) -> None:
    # Der e-Golf steht angenommen an der EVCS; die Indizien des Zyklus nennt die Gesundheit auch,
    # wenn sie nichts entscheiden: Tessie sieht den Tesla dort nicht (er ist nicht angesteckt).
    c = controller(cfg)
    run(c, cfg, 1)
    assert c.wallbox_health() == [
        {
            "id": "evcs",
            "occupant": "egolf",
            "name": "e-Golf",
            "assignment": "default",
            "indicators": [{"vehicle": "tesla", "clue": "tessie", "supports": False}],
        },
        NOBODY[1],
    ]


def test_wallbox_health_shows_an_override(cfg) -> None:
    override = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(wallbox="evcs")})
    c = controller(cfg)
    run(c, cfg, 1, intent=override)
    evcs_, twc_ = c.wallbox_health()
    assert (evcs_["occupant"], evcs_["name"], evcs_["assignment"]) == (
        "tesla",
        "Tesla Model 3",
        "override",
    )
    assert twc_ == NOBODY[1]


def test_wallbox_health_shows_an_unclear_assignment_with_its_contradiction(cfg) -> None:
    # ``CONTRADICTION``: Tessie und der Ausschluss stützen den Tesla an der EVCS, die
    # Phasen-Signatur schließt ihn dort aus. Ab 120 s ist die Zuordnung unklar: An der EVCS steht
    # dann wieder ihr Standardfahrzeug, mit der Art „unklar“, und die Indizien zeigen den
    # Widerspruch.
    c = controller(cfg)
    run(c, cfg, UNCLEAR_FROM_S + 1, **CONTRADICTION)
    assert c.wallbox_health() == [
        {
            "id": "evcs",
            "occupant": "egolf",
            "name": "e-Golf",
            "assignment": "unclear",
            "indicators": [
                {"vehicle": "tesla", "clue": "tessie", "supports": True},
                {"vehicle": "tesla", "clue": "phases", "supports": False},
                {"vehicle": "tesla", "clue": "elsewhere", "supports": True},
                {"vehicle": "egolf", "clue": "phases", "supports": True},
            ],
        },
        NOBODY[1],
    ]


def test_wallbox_health_names_nobody_at_a_wallbox_with_a_session_while_unclear(cfg) -> None:
    # Haben beide Fahrzeuge den Wall Connector als Standard-Wallbox, steht an der EVCS, an der ein
    # Vorgang läuft, niemand, solange die Zuordnung unklar ist: die Art bleibt „unklar“.
    cfg = with_vehicle(cfg, "egolf", default_wallbox="twc")
    c = controller(cfg)
    run(c, cfg, UNCLEAR_FROM_S + 1, **CONTRADICTION)
    evcs_ = c.wallbox_health()[0]
    assert (evcs_["occupant"], evcs_["name"], evcs_["assignment"]) == (None, None, "unclear")
    assert {"vehicle": "tesla", "clue": "phases", "supports": False} in evcs_["indicators"]


# Plan 2d, Präzisierung 9: „Aus“ sofort und Reserve im Stundenbudget

TESLA_OFF = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(mode=ChargeMode.OFF)})
TESLA_CHARGING = {
    "twc_obs": twc(
        connected=True,
        charging=True,
        phase_a=dict.fromkeys(("L1", "L2", "L3"), 16.0),
        power_w=11040.0,
    ),
    "tesla_obs": tesla(plugged=True, charging=True, actual_current_a=16.0),
    "evcs_obs": evcs(connected=False),
    "controllable": BOTH,
}
AT_TESLA = {
    ("vehicle.tesla", CURRENT): 16.0,
    ("vehicle.tesla", CHARGING): True,
    ("wallbox.evcs", CURRENT): 6.0,
    ("wallbox.evcs", CHARGING): False,
}
NO_SURPLUS = grid(grid_w=None)  # unbekannter Überschuss: nach 300 s stoppt der Tesla in PV
FULL_HOUR = {"vehicle.tesla": 16}  # per_hour 20 − Reserve 4


def tesla_commands(found, kind) -> list:
    return [
        c.command
        for _, c in found
        if c.command.actuator == "vehicle.tesla" and c.command.kind == kind
    ]


def test_off_stops_urgently(cfg) -> None:
    c = controller(cfg)
    run(c, cfg, 5, present=AT_TESLA, **TESLA_CHARGING)  # PV: Der Tesla lädt weiter
    found = run(c, cfg, 5, intent=TESLA_OFF, present=AT_TESLA, **TESLA_CHARGING)
    [stop] = tesla_commands(found, CHARGING)
    assert (stop.value, stop.urgent, stop.reasons) == (False, True, ("mode_off",))


def test_a_stop_in_pv_is_not_urgent(cfg) -> None:
    found = run(controller(cfg), cfg, 301, present=AT_TESLA, grid_obs=NO_SURPLUS, **TESLA_CHARGING)
    stop = tesla_commands(found, CHARGING)[-1]  # davor der erste Befehl nach dem Start
    assert (stop.value, stop.urgent, stop.reasons) == (False, False, ("mode_pv", "pv_surplus"))


def test_off_stop_passes_an_exhausted_hourly_budget(cfg, tmp_path) -> None:
    limits = HardLimits.from_config(cfg)
    layer = SafetyLayer(limits, PersistentWriteBudget(12, 100, tmp_path / "budget.json", T0))
    ctx = make_ctx(now=T0 + timedelta(seconds=3000), present=AT_TESLA)
    for i in range(20):  # per_hour = 20
        layer.authorize(
            Command("vehicle.tesla", CURRENT, 6.0 + i % 2 * 2),
            make_ctx(now=T0 + timedelta(seconds=100 * i)),
        )
    assert layer.hourly_count("vehicle.tesla", ctx.now) == 20
    c = controller(cfg)
    run(c, cfg, 5, present=AT_TESLA, **TESLA_CHARGING)
    found = run(c, cfg, 5, intent=TESLA_OFF, present=AT_TESLA, **TESLA_CHARGING)
    [stop] = tesla_commands(found, CHARGING)
    verdict = layer.authorize(stop, ctx)
    assert verdict.outcome is Outcome.ACCEPTED
    assert [f.code for f in verdict.findings][-1] == "urgent.bypass"
    normal = layer.authorize(replace(stop, urgent=False), ctx)
    assert normal.outcome is Outcome.DEFERRED


def test_reserve_holds_back_pv_current_changes(cfg) -> None:
    present = {**AT_TESLA, ("vehicle.tesla", CURRENT): 6.0}
    kw = {
        "tesla_obs": tesla(plugged=True, charging=True, actual_current_a=6.0),
        "grid_obs": grid(l1=-4.0, l2=-4.0, l3=-4.0, grid_w=-8000.0),
    }
    settle = {"grid_obs": grid(grid_w=3000.0)}  # erst wenig Überschuss, dann viel
    _, held = charging_tesla(cfg, present, settle=settle, commands_hour=FULL_HOUR, **kw)
    assert tesla_commands(held, CURRENT) == []
    _, free = charging_tesla(cfg, present, settle=settle, commands_hour={"vehicle.tesla": 15}, **kw)
    assert tesla_commands(free, CURRENT) != []  # bei 15 Befehlen steigt der Strom


def test_reserve_lets_guard_reductions_through(cfg) -> None:
    c = controller(cfg)
    crowded = grid(l1=25.0, l2=25.0, l3=25.0)  # der Wächter lässt 10 A
    found = run(
        c, cfg, 5, present=AT_TESLA, commands_hour=FULL_HOUR, grid_obs=crowded, **TESLA_CHARGING
    )
    assert c.health()[0]["limit_a"] == 10.0
    assert [c.value for c in tesla_commands(found, CURRENT)] == [10.0]


def test_reserve_does_not_hold_starts_and_stops(cfg) -> None:
    waiting = dict(
        TESLA_CHARGING, tesla_obs=tesla(plugged=True, charging=False, actual_current_a=0.0)
    )
    start = run(controller(cfg), cfg, 5, present=AT_TESLA, commands_hour=FULL_HOUR, **waiting)
    assert [c.value for c in tesla_commands(start, CHARGING)] == [True]
    assert [c.value for c in tesla_commands(start, CURRENT)] == [
        16.0
    ]  # der Strom geht vor dem Start
    stop = run(
        controller(cfg),
        cfg,
        301,
        present=AT_TESLA,
        grid_obs=NO_SURPLUS,
        commands_hour=FULL_HOUR,
        **TESLA_CHARGING,
    )
    assert [c.value for c in tesla_commands(stop, CHARGING)] == [True, False]  # Start, dann Stopp


def charging_tesla(cfg, present, *, settle=None, **kw):
    """Ein Tesla, der lädt und seine Werte kennt (``step`` 0 mit ``settle``); danach ``step`` 1 bis
    199 mit ``kw``."""
    c = controller(cfg)
    accept(c, c.step(inputs(cfg, present=present, **TESLA_CHARGING | (settle or {}))))
    found = []
    for s in range(1, 200):
        commands = c.step(inputs(cfg, s=s, present=present, **TESLA_CHARGING | kw))
        accept(c, commands)
        found.extend((s, x) for x in commands)
    return c, found


def test_reserve_holds_back_a_reduction_that_no_guard_asks_for(cfg) -> None:
    less = {
        "grid_obs": grid(grid_w=3000.0)
    }  # weniger Überschuss: der Wunsch sinkt, die Grenze nicht
    c, free = charging_tesla(cfg, AT_TESLA, commands_hour={"vehicle.tesla": 0}, **less)
    assert c.health()[0]["limit_a"] == 33.0
    assert [x.value for x in tesla_commands(free, CURRENT)][:1] == [13.0]
    _, held = charging_tesla(cfg, AT_TESLA, commands_hour=FULL_HOUR, **less)
    assert tesla_commands(held, CURRENT) == []


def test_reserve_lets_a_phase_limit_reduction_through(cfg) -> None:
    now = Intent(version=1, control=True, vehicles={"tesla": NOW})
    crowded = grid(l1=36.0, l2=36.0, l3=36.0)  # über der Sicherung: dringend, Grund phase_limit
    found = run(
        controller(cfg),
        cfg,
        3,
        intent=now,
        present=AT_TESLA,
        grid_obs=crowded,
        commands_hour=FULL_HOUR,
        **TESLA_CHARGING,
    )
    [current] = tesla_commands(found, CURRENT)
    assert (current.value, current.urgent, current.reasons) == (
        13.0,
        True,
        ("mode_now", "phase_limit"),
    )


UNKNOWN_CURRENT = {k: v for k, v in AT_TESLA.items() if k != ("vehicle.tesla", CURRENT)}


def test_reserve_does_not_hold_the_current_of_a_start(cfg) -> None:
    now = Intent(version=1, control=True, vehicles={"tesla": NOW})
    waiting = dict(
        TESLA_CHARGING, tesla_obs=tesla(plugged=True, charging=False, actual_current_a=0.0)
    )
    found = run(
        controller(cfg),
        cfg,
        3,
        intent=now,
        present={**UNKNOWN_CURRENT, ("vehicle.tesla", CHARGING): False},
        grid_obs=grid(l1=41.0, l2=41.0, l3=41.0),  # der Wächter lässt 8 A
        commands_hour=FULL_HOUR,
        **waiting,
    )
    sent = [
        (c.kind, c.value) for c in tesla_commands(found, CURRENT) + tesla_commands(found, CHARGING)
    ]
    assert sent == [(CURRENT, 8.0), (CHARGING, True)]  # der Strom vor dem Start


def test_reserve_holds_nothing_back_while_the_current_is_unknown(cfg) -> None:
    now = Intent(version=1, control=True, vehicles={"tesla": NOW})
    crowded = grid(l1=41.0, l2=41.0, l3=41.0)
    _, found = charging_tesla(
        cfg, UNKNOWN_CURRENT, intent=now, grid_obs=crowded, commands_hour=FULL_HOUR
    )
    assert [x.value for x in tesla_commands(found, CURRENT)][:1] == [8.0]
    _, free = charging_tesla(
        cfg, UNKNOWN_CURRENT, intent=now, grid_obs=crowded, commands_hour={"vehicle.tesla": 0}
    )
    assert tesla_commands(found, CURRENT) == tesla_commands(free, CURRENT)


def test_reserve_does_not_touch_the_evcs(cfg) -> None:
    kw = {"present": at_evcs(6.0, True), "evcs_obs": evcs(charging=True)}
    free = run(controller(cfg), cfg, 5, **kw)
    crowded = run(controller(cfg), cfg, 5, commands_hour={"wallbox.evcs": 20}, **kw)
    assert free != [] and as_commands(crowded) == as_commands(free)


def test_health_names_commands_of_the_last_hour(cfg) -> None:
    c = controller(cfg)
    c.step(inputs(cfg, commands_hour={"vehicle.tesla": 7}))
    health = {entry["id"]: entry["commands_hour"] for entry in c.health()}
    assert health == {"tesla": 7, "egolf": None}
    c.idle(T0 + timedelta(seconds=1))
    assert [entry["commands_hour"] for entry in c.health()] == [None, None]


# --- Geplant (Plan 4b, Präzisierung 7) ------------------------------------------------------------

NO_SHARE = grid(grid_w=0.0)  # Überschuss −100 W: kein Anteil
EGOLF_FULL_KW = 16 * 230.0 * 2 / 1000.0  # volle Leistung des e-Golf an der EVCS


def planned_for(key: str = "egolf", *, target: int = 80, departure: str = "18:00") -> Intent:
    """Die Absicht mit ``key`` in „Geplant“; T0 ist 12:00 Wiener Zeit."""
    entry = VehicleIntent(mode=ChargeMode.PLANNED, target_soc_pct=target, departure=departure)
    return Intent(version=1, control=True, vehicles={key: entry})


def egolf_soc(pct: float, at=T0):
    """Der e-Golf mit einem Ladestand aus Home Assistant."""
    return replace(NONE, soc_pct=pct, soc_at=at)


def egolf_need(soc: float, target: float = 80.0) -> float:
    return (target - soc) / 100.0 * 38.6 / 0.9


def egolf_latest(departure, soc: float):
    hours = egolf_need(soc) / EGOLF_FULL_KW * 1.1
    return departure - timedelta(hours=hours) - timedelta(minutes=15)


def evcs_commands(found) -> list:
    return [(t, c.command) for t, c in found if c.command.actuator == "wallbox.evcs"]


def test_planned_without_plan_charges_surplus_before_latest_start(cfg) -> None:
    c = controller(cfg)
    intent = planned_for(departure="18:00")
    found = run(c, cfg, 200, intent=intent, egolf_obs=egolf_soc(60.0), grid_obs=NO_SHARE)
    assert evcs_commands(found) == [cmd(0, CHARGING, False, ("mode_planned",))]
    departure = T0 + timedelta(hours=6)
    health = c.health()[1]
    assert (health["mode"], health["soc_pct"], health["soc_source"]) == (
        "planned",
        60.0,
        "measured",
    )
    assert health["charge_limit_pct"] is None
    assert health["planned"] == {
        "state": "surplus",
        "target_soc_pct": 80.0,
        "departure_at": departure.isoformat(),
        "need_kwh": pytest.approx(egolf_need(60.0)),
        "latest_start": egolf_latest(departure, 60.0).isoformat(),
        "plan_w": None,
    }
    # Mit Überschuss lädt es davor wie PV, mit den Zeiten von PV
    pv = run(controller(cfg), cfg, 121, intent=intent, egolf_obs=egolf_soc(60.0))
    assert evcs_commands(pv) == [
        cmd(0, CHARGING, False, ("mode_planned",)),
        cmd(120, CURRENT, 8.0, ("mode_planned", "pv_surplus")),
        cmd(120, CHARGING, True, ("mode_planned", "pv_surplus")),
    ]


def test_planned_starts_full_at_latest_start(cfg) -> None:
    c = controller(cfg)
    kw = {"intent": planned_for(departure="18:00"), "grid_obs": NO_SHARE}
    latest_s = (egolf_latest(T0 + timedelta(hours=6), 60.0) - T0).total_seconds()
    before = math.floor(latest_s)
    assert before < latest_s  # die Sekunde davor liegt echt vor der spätesten Startzeit
    accept(c, c.step(inputs(cfg, egolf_obs=egolf_soc(60.0), **kw)))
    early = egolf_soc(60.0, T0 + timedelta(seconds=before))
    assert c.step(inputs(cfg, s=before, egolf_obs=early, **kw)) == []
    at = before + 1
    found = c.step(inputs(cfg, s=at, egolf_obs=egolf_soc(60.0, T0 + timedelta(seconds=at)), **kw))
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 16.0, reasons=("mode_planned", "latest_start")),
        Command("wallbox.evcs", CHARGING, True, reasons=("mode_planned", "latest_start")),
    ]
    assert c.health()[1]["planned"]["state"] == "due"


def test_planned_due_at_once_charges_with_max_a_and_the_fuse_target(cfg) -> None:
    c = controller(cfg)
    loaded = replace(NO_SHARE, phase_import_a={"L1": 20.0, "L2": 20.0, "L3": 2.0})
    found = c.step(
        inputs(
            cfg, intent=planned_for(departure="14:00"), egolf_obs=egolf_soc(30.0), grid_obs=loaded
        )
    )
    # Ziel des Phasenwächters wie bei voller Leistung: die Sicherung, 33 − 20 = 13 A
    assert [x.command for x in found] == [
        Command(
            "wallbox.evcs", CURRENT, 13.0, reasons=("mode_planned", "latest_start", "phase_limit")
        ),
        Command(
            "wallbox.evcs", CHARGING, True, reasons=("mode_planned", "latest_start", "phase_limit")
        ),
    ]


def test_planned_stops_at_target(cfg) -> None:
    c = controller(cfg)
    fixed = {**TESLA_PLUGGED, "intent": planned_for("tesla", target=80)}
    del fixed["tesla_obs"]

    def at_80(s: int):  # Tessie meldet den Ladestand frisch
        return tesla(plugged=True, soc_pct=80.0, soc_at=T0 + timedelta(seconds=s))

    found = run(c, cfg, 300, tesla_obs=at_80, **fixed)  # mit Überschuss
    assert [(t, x.command) for t, x in found if x.command.actuator == "vehicle.tesla"] == [
        (0, Command("vehicle.tesla", CHARGING, False, reasons=("mode_planned", "target_reached")))
    ]
    assert c.health()[0]["planned"]["state"] == "reached"


def test_planned_stops_once_at_target_and_restarts_only_three_points_below(cfg) -> None:
    c = controller(cfg)
    kw = {"intent": planned_for(departure="14:00"), "grid_obs": NO_SHARE}

    def step(s: int, soc: float) -> list:
        found = c.step(inputs(cfg, s=s, egolf_obs=egolf_soc(soc, T0 + timedelta(seconds=s)), **kw))
        accept(c, found)
        return [x.command for x in found]

    assert [x.kind for x in step(0, 30.0)] == [CURRENT, CHARGING]
    assert step(10, 80.0) == [
        Command("wallbox.evcs", CHARGING, False, reasons=("mode_planned", "target_reached"))
    ]
    assert step(20, 78.0) == []  # noch erreicht
    assert c.health()[1]["planned"]["state"] == "reached"
    assert step(30, 76.9) == []  # wieder unter dem Ziel, aber die späteste Startzeit ist weit
    assert c.health()[1]["planned"]["state"] == "surplus"
    # 13:50, Abfahrt 14:00: Die späteste Startzeit für 76,9 % ist vorbei, das Auto startet wieder
    assert step(6600, 76.9) == [
        Command("wallbox.evcs", CHARGING, True, reasons=("mode_planned", "latest_start"))
    ]
    assert c.health()[1]["planned"]["state"] == "due"


def test_planned_due_names_the_surplus_when_it_carries_the_wish(cfg) -> None:
    c = controller(cfg)
    sunny = grid(grid_w=-10000.0)  # Anteil 9900 / 460 = 21 A ≥ 16 A
    found = c.step(
        inputs(
            cfg, intent=planned_for(departure="14:00"), egolf_obs=egolf_soc(30.0), grid_obs=sunny
        )
    )
    reasons = ("mode_planned", "latest_start", "pv_surplus")
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 16.0, reasons=reasons),
        Command("wallbox.evcs", CHARGING, True, reasons=reasons),
    ]


def test_assumed_soc_never_reports_reached(cfg) -> None:  # Review Focus 2
    c = controller(cfg)
    kw = {"intent": planned_for(departure="14:00"), "grid_obs": NO_SHARE}
    first = c.step(inputs(cfg, evcs_obs=evcs(total_kwh=100.0), **kw))
    assert first[-1].command.reasons == ("mode_planned", "latest_start")
    accept(c, first)
    for s, counted in ((3600, 30.0), (7000, 60.0)):
        charging = evcs(charging=True, power_w=7360.0, total_kwh=100.0 + counted)
        found = c.step(inputs(cfg, s=s, evcs_obs=charging, **kw))
        accept(c, found)
        assert all("target_reached" not in x.command.reasons for x in found)
        health = c.health()[1]
        assert (health["soc_pct"], health["soc_source"]) == (0.0, "assumed")
        assert health["planned"]["state"] == "due"


def test_due_latch_resets_with_new_session(cfg) -> None:  # Review Focus 3
    c = controller(cfg)
    kw = {"intent": planned_for(departure="18:00"), "grid_obs": NO_SHARE}
    due_s = math.ceil((egolf_latest(T0 + timedelta(hours=6), 60.0) - T0).total_seconds())

    def step(s: int, soc: float, **more) -> list:
        at = T0 + timedelta(seconds=s)
        found = c.step(inputs(cfg, s=s, egolf_obs=egolf_soc(soc, at), **kw, **more))
        accept(c, found)
        return found

    step(0, 60.0)
    assert [x.command.kind for x in step(due_s, 60.0)] == [CURRENT, CHARGING]
    # Der Ladestand steigt, die späteste Startzeit rückt hinter jetzt: Es bleibt bei voller Leistung
    assert step(due_s + 10, 75.0) == []
    assert c.health()[1]["planned"]["state"] == "due"
    # Abstecken und wieder anstecken: ein neuer Ansteckvorgang, der Merker gilt nicht mehr
    step(due_s + 20, 75.0, evcs_obs=evcs(connected=False))
    replugged = step(due_s + 30, 75.0)
    assert [x.command for x in replugged] == [
        Command("wallbox.evcs", CHARGING, False, reasons=("mode_planned",))
    ]
    assert c.health()[1]["planned"]["state"] == "surplus"


def test_departure_passed_while_plugged_uses_next_day(cfg) -> None:  # Review Focus 3
    c = controller(cfg)
    kw = {**TESLA_PLUGGED, "intent": planned_for("tesla", departure="12:01")}

    def health_at(s: int) -> dict:
        obs = tesla(plugged=True, soc_pct=80.0, soc_at=T0 + timedelta(seconds=s))
        accept(c, c.step(inputs(cfg, s=s, **{**kw, "tesla_obs": obs})))
        return c.health()[0]["planned"]

    assert health_at(0)["departure_at"] == "2026-10-08T10:01:00+00:00"
    after = health_at(120)
    assert after["departure_at"] == "2026-10-09T10:01:00+00:00"
    assert after["state"] == "reached"  # ein erreichtes Ziel bleibt erreicht


def test_soc_session_starts_with_each_plug_in(cfg) -> None:  # Plan 4b, Präzisierung 5
    c = controller(cfg)
    measured = egolf_soc(50.0, T0 - timedelta(minutes=30))  # vor dem Start des core gemessen

    def health_at(s: int, counter: float | None, **more) -> dict:
        wallbox = more.pop("evcs_obs", evcs(total_kwh=counter))
        accept(c, c.step(inputs(cfg, s=s, evcs_obs=wallbox, egolf_obs=measured, **more)))
        return c.health()[1]

    # Der Vorgang beim Start des core: Er beginnt für die Schätzung jetzt
    assert health_at(0, 100.0)["soc_source"] == "measured"
    estimated = health_at(60, 104.3)
    assert estimated["soc_source"] == "estimated"
    assert estimated["soc_pct"] == pytest.approx(50.0 + 4.3 * 0.9 / 38.6 * 100.0)
    # Abstecken, nach 3 h wieder anstecken: Die Messung liegt mehr als 60 min davor
    health_at(120, None, evcs_obs=evcs(connected=False))
    later = health_at(120 + 3 * 3600, 104.3)
    assert (later["soc_pct"], later["soc_source"]) == (0.0, "assumed")


def test_boost_overrides_planned(cfg) -> None:
    until = T0 + timedelta(hours=2)
    entry = VehicleIntent(mode=ChargeMode.PLANNED, boost_until=until)
    intent = Intent(version=1, control=True, vehicles={"egolf": entry})
    c = controller(cfg)
    found = c.step(inputs(cfg, intent=intent, egolf_obs=egolf_soc(90.0)))
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 16.0, reasons=("boost",)),
        Command("wallbox.evcs", CHARGING, True, reasons=("boost",)),
    ]
    assert (c.health()[1]["mode"], c.health()[1]["planned"]) == ("boost", None)


def test_other_modes_unchanged(cfg) -> None:
    c = controller(cfg)
    obs = tesla(plugged=True, soc_pct=55.0, soc_at=T0, charge_limit_pct=90.0)
    accept(c, c.step(inputs(cfg, **{**TESLA_PLUGGED, "tesla_obs": obs})))
    tesla_health, egolf_health = c.health()
    assert (tesla_health["soc_pct"], tesla_health["soc_source"]) == (55.0, "measured")
    assert tesla_health["charge_limit_pct"] == 90.0
    assert tesla_health["planned"] is None
    assert (egolf_health["mode"], egolf_health["planned"]) == ("pv", None)


def test_a_planned_guest_cannot_happen_but_charges_like_pv(cfg) -> None:  # Präzisierung 2
    # Der core lehnt „Geplant“ für den Gast ab; käme es doch an, lädt er wie PV-Überschuss.
    guest = Intent(version=1, control=True, guest=GuestIntent(mode=ChargeMode.PLANNED))
    pv = Intent(version=1, control=True, guest=GuestIntent(mode=ChargeMode.PV))
    obs = {"evcs_obs": evcs(), "homes": {"egolf": False}}
    planned_run = run(controller(cfg), cfg, 121, intent=guest, **obs)
    pv_run = run(controller(cfg), cfg, 121, intent=pv, **obs)
    assert [(t, c.kind, c.value) for t, c in as_commands(planned_run)] == [
        (t, c.kind, c.value) for t, c in as_commands(pv_run)
    ]


# --- Geplant mit Plan-Slot (Plan 4b, Präzisierung 7, Task 6) --------------------------------------


def slot(**vehicles: int) -> ActiveSlot:
    """Ein gültiger Slot ab ``T0`` im Eigenverbrauch mit der Ladeleistung je Fahrzeug in W."""
    return ActiveSlot(1, T0, BatteryIntent(), None, None, None, vehicles=vehicles)


def tesla_planned(s: int):
    """Der Tesla am Wall Connector mit 50 %, frisch von Tessie."""
    return tesla(plugged=True, soc_pct=50.0, soc_at=T0 + timedelta(seconds=s))


def tesla_step(c: ChargeController, cfg, s: int, active: ActiveSlot | None, **more) -> list:
    kw = {**TESLA_PLUGGED, "intent": planned_for("tesla", departure="18:00"), "grid_obs": NO_SHARE}
    kw |= {"tesla_obs": tesla_planned(s)} | more
    found = c.step(inputs(cfg, s=s, **kw), NO_LIMITS, active)
    accept(c, found)
    return [x.command for x in found if x.command.actuator == "vehicle.tesla"]


def test_slot_power_is_a_firm_demand(cfg) -> None:
    c = controller(cfg)
    # 6900 W auf drei Phasen: ceil(6900 / 690) = 10 A, ohne Überschuss
    reasons = ("mode_planned", "plan_power")
    assert tesla_step(c, cfg, 0, slot(tesla=6900)) == [
        Command("vehicle.tesla", CURRENT, 10.0, reasons=reasons),
        Command("vehicle.tesla", CHARGING, True, reasons=reasons),
    ]
    health = c.health()[0]["planned"]
    assert (health["state"], health["plan_w"]) == ("plan", 6900.0)


def test_surplus_on_top_of_slot_power(cfg) -> None:
    c = controller(cfg)
    sunny = grid(grid_w=-7100.0)  # Überschuss 7000 W: Anteil floor(7000 / 690) = 10 A
    # Der Slot will 4140 W (6 A); der Anteil am Überschuss trägt mehr und kommt obendrauf.
    reasons = ("mode_planned", "plan_power", "pv_surplus")
    assert tesla_step(c, cfg, 0, slot(tesla=4140), grid_obs=sunny) == [
        Command("vehicle.tesla", CURRENT, 10.0, reasons=reasons),
        Command("vehicle.tesla", CHARGING, True, reasons=reasons),
    ]


@pytest.mark.parametrize("active", [slot(egolf=0), slot(egolf=0, tesla=6900)])
def test_zero_slot_power_behaves_like_pv(cfg, active) -> None:
    # 30 % und Abfahrt 14:00: Ohne Slot wäre die späteste Startzeit schon vorbei. Ein gültiger Slot,
    # der das Auto mit 0 W nennt, lädt nur PV-Überschuss, mit den Zeiten von PV.
    kw = {"intent": planned_for(departure="14:00"), "egolf_obs": egolf_soc(30.0)}
    c = controller(cfg)
    assert evcs_commands(run(c, cfg, 200, active=active, grid_obs=NO_SHARE, **kw)) == [
        cmd(0, CHARGING, False, ("mode_planned",))
    ]
    health = c.health()[1]["planned"]
    assert (health["state"], health["plan_w"]) == ("surplus", 0.0)
    pv = run(controller(cfg), cfg, 121, active=active, **kw)
    assert evcs_commands(pv) == [
        cmd(0, CHARGING, False, ("mode_planned",)),
        cmd(120, CURRENT, 8.0, ("mode_planned", "pv_surplus")),
        cmd(120, CHARGING, True, ("mode_planned", "pv_surplus")),
    ]


def test_valid_slot_without_the_car_uses_the_latest_start(cfg) -> None:
    # 30 % und Abfahrt 14:00, die späteste Startzeit ist vorbei. Der gültige Slot nennt nur den
    # Tesla (etwa ein Plan von vor dem Anstecken des e-Golf): Für den e-Golf gilt kein Slot, er lädt
    # ab der spätesten Startzeit mit voller Leistung (Entscheidung 4, Ruling R22).
    kw = {"intent": planned_for(departure="14:00"), "egolf_obs": egolf_soc(30.0)}
    c = controller(cfg)
    due = ("mode_planned", "latest_start")
    found = run(c, cfg, 1, active=slot(tesla=6900), grid_obs=NO_SHARE, **kw)
    assert evcs_commands(found) == [cmd(0, CURRENT, 16.0, due), cmd(0, CHARGING, True, due)]
    health = c.health()[1]["planned"]
    assert (health["state"], health["plan_w"]) == ("due", None)


AT_10_A = evcs(charging=True, phase_a={"L1": 10.0, "L2": 10.0, "L3": 0.0}, power_w=4600.0)


def test_plan_expiry_falls_back_to_latest_start_without_stop(cfg) -> None:  # Review Focus 4
    # 30 % und Abfahrt 14:00: Die späteste Startzeit ist vorbei. Der Plan lädt mit 4600 W (10 A)
    # bis 10:15; danach gibt es keinen gültigen Slot (app aus, core weiter).
    c = controller(cfg)
    kw = {"intent": planned_for(departure="14:00"), "grid_obs": NO_SHARE}

    def active(s: int) -> ActiveSlot | None:
        return slot(egolf=4600) if s < 900 else None

    def soc(s: int):
        return egolf_soc(30.0, T0 + timedelta(seconds=s))

    found = run(c, cfg, 1000, active=active, egolf_obs=soc, evcs_obs=AT_10_A, **kw)
    plan, due = ("mode_planned", "plan_power"), ("mode_planned", "latest_start")
    assert evcs_commands(found) == [
        cmd(0, CURRENT, 10.0, plan),
        cmd(0, CHARGING, True, plan),
        cmd(900, CURRENT, 16.0, due),  # kein „Laden aus“ dazwischen
    ]
    health = c.health()[1]["planned"]
    assert (health["state"], health["plan_w"]) == ("due", None)


def test_plan_expiry_before_latest_start_stops_with_the_times_of_pv(cfg) -> None:
    # 60 % und Abfahrt 18:00: Die späteste Startzeit ist weit. Der Plan startet das Auto um 10:00:10
    # und läuft um 10:01 ab; danach gilt PV-Überschuss mit seinen Zeiten: Es hält den Mindeststrom
    # und stoppt erst nach der Mindestlaufzeit seit dem Start des Plans (Review Focus 4).
    c = controller(cfg)
    importing = grid(grid_w=4600.0)  # die Ladeleistung kommt ganz aus dem Netz: kein Überschuss
    kw = {"intent": planned_for(departure="18:00"), "grid_obs": importing}

    def active(s: int) -> ActiveSlot | None:
        return slot(egolf=4600) if 10 <= s < 60 else None

    def soc(s: int):
        return egolf_soc(60.0, T0 + timedelta(seconds=s))

    def wallbox(s: int):
        return AT_10_A if s > 10 else evcs()

    found = run(c, cfg, 700, active=active, egolf_obs=soc, evcs_obs=wallbox, **kw)
    plan, pv = ("mode_planned", "plan_power"), ("mode_planned", "pv_surplus")
    stop_s = 10 + int(cfg.control.min_on_s)
    assert stop_s > int(cfg.control.stop_after_s)  # die Mindestlaufzeit bindet
    assert evcs_commands(found) == [
        cmd(0, CHARGING, False, ("mode_planned",)),
        cmd(10, CURRENT, 10.0, plan),
        cmd(10, CHARGING, True, plan),
        cmd(60, CURRENT, 6.0, pv),  # wie PV: der Mindeststrom bis zum Stopp
        cmd(stop_s, CHARGING, False, pv),
    ]
    assert c.health()[1]["planned"]["state"] == "surplus"


@pytest.mark.parametrize(("l1", "urgent"), [(18.0, False), (40.0, True)], ids=["cap", "fuse"])
def test_peak_cap_lowering_with_firm_demand_is_urgent_only_when_the_fuse_demands_it(
    cfg, l1, urgent
) -> None:
    # Der e-Golf lädt „Geplant“ mit 16 A, der Plan will 4600 W (10 A), der 15-min-Wächter kappt
    # auf 3680 W (8 A). Dringend ist die Senkung nur, wenn schon die Sicherung allein mit dem
    # Höchststrom unter den Ist-Strom senkte (bei 40 A auf L1 sind 9 A frei); die Plan-Leistung
    # zählt dafür nicht.
    situation = inputs(
        cfg,
        intent=planned_for(departure="18:00"),
        egolf_obs=egolf_soc(60.0),
        evcs_obs=AT_16_A,
        present=at_evcs(16.0, True),
        grid_obs=grid(l1=l1, l2=18.0, grid_w=7360.0),
    )
    found = controller(cfg).step(situation, PeakLimits(caps_w={"egolf": 3680.0}), slot(egolf=4600))
    current = next(x.command for x in found if x.command.kind is CURRENT)
    assert (current.actuator, current.value, current.urgent) == ("wallbox.evcs", 8.0, urgent)
    assert "peak_guard" in current.reasons


@pytest.mark.parametrize(
    ("l1", "urgent"),
    [(2.0, False), (36.0, True)],
    ids=["plan_only", "fuse"],
)
def test_lowering_to_the_slot_power_is_urgent_only_for_the_fuse(cfg, l1, urgent) -> None:
    # Die EVCS lädt mit 16 A ohne Überschuss; der Plan will 4600 W (10 A). Die Senkung auf den Plan
    # ist nicht dringend; dringend ist sie nur, wenn schon die Sicherung allein senkt (33 − 20 A).
    kw = {"intent": planned_for(departure="18:00"), "egolf_obs": egolf_soc(60.0)}
    found = run(
        controller(cfg),
        cfg,
        1,
        active=slot(egolf=4600),
        evcs_obs=AT_16_A,
        present=at_evcs(16.0, True),
        grid_obs=grid(l1=l1, l2=20.0 if urgent else 2.0, grid_w=7360.0),
        **kw,
    )
    assert evcs_commands(found) == [
        cmd(0, CURRENT, 10.0, ("mode_planned", "plan_power"), urgent=urgent),
        cmd(0, CHARGING, True, ("mode_planned", "plan_power")),
    ]


# --- Lesende Zugriffe für die Klimaanlage (Plan 6a, Task 8) ---------------------------------------


def unmet_after(cfg, mode: ChargeMode, *, current: float | None, on: bool | None, **kw) -> float:
    """``unmet_surplus_w`` nach einem Schritt mit dem e-Golf in ``mode`` an der EVCS; ``current``
    und ``on`` sind Ist-Strom und Ist-„Laden an“ der EVCS (``None``: unbekannt)."""
    c = controller(cfg)
    present = {
        key: value
        for key, value in (
            (("wallbox.evcs", CURRENT), current),
            (("wallbox.evcs", CHARGING), on),
        )
        if value is not None
    }
    c.step(inputs(cfg, intent=egolf_in(mode), present=present, **kw))
    return c.unmet_surplus_w()


def test_unmet_surplus_counts_cars_that_follow_their_command(cfg) -> None:  # Ruling R18
    # Der e-Golf (zwei Phasen, 6 … 16 A, 7360 W) folgt 8 A mit 3680 W: offen bis zum Höchststrom
    # sind 3680 W, egal wie viel Überschuss gerade da ist.
    at_8_a = evcs(charging=True, power_w=3680.0)
    assert unmet_after(cfg, ChargeMode.PV, current=8.0, on=True, evcs_obs=at_8_a) == 3680.0
    assert unmet_after(cfg, ChargeMode.MIN_PV, current=8.0, on=True, evcs_obs=at_8_a) == 3680.0
    # Eine Stufe von 1 A darunter folgt er noch (3220 W bei 8 A).
    at_7_a = evcs(charging=True, power_w=3220.0)
    assert unmet_after(cfg, ChargeMode.PV, current=8.0, on=True, evcs_obs=at_7_a) == 4140.0


def held_off_unmet(cfg, steps: int, spare_w: float, **kw) -> list[float]:
    """``unmet_surplus_w(spare_w)`` je Schritt, ohne Überschuss; jedes Urteil der Safety-Schicht
    nimmt an."""
    c = controller(cfg)
    found: list[float] = []
    for s in range(steps):
        accept(c, c.step(inputs(cfg, s=s, grid_obs=grid(grid_w=0.0), **kw)))
        found.append(c.unmet_surplus_w(spare_w))
    return found


def test_unmet_surplus_counts_a_car_the_regulation_holds_off(cfg) -> None:  # Rulings R18, R23 c
    # Ohne Überschuss hält die Regelung den e-Golf aus („Laden aus“): Ab ihrem Urteil folgt er
    # mit 0 W, er wartet auf Überschuss, und offen ist sein Höchststrom, sobald Einspeisung und
    # Klimaanlage (``spare_w``) seine Startleistung erreichen: 6 A auf zwei Phasen, 2760 W. Vorher
    # ist der Befehl unbekannt.
    off = evcs(charging=False, power_w=0.0)
    kw = {"intent": egolf_in(ChargeMode.PV), "evcs_obs": off}
    assert held_off_unmet(cfg, 2, 2760.0, **kw) == [0.0, 7360.0]
    # Ebenso der Tesla am Wall Connector (drei Phasen, 5 … 16 A, 3450 … 11040 W).
    present = {("vehicle.tesla", CURRENT): 16.0, ("vehicle.tesla", CHARGING): False}
    alone = TESLA_PLUGGED | {"evcs_obs": evcs(connected=False)}
    assert held_off_unmet(cfg, 2, 3450.0, present=present, **alone) == [0.0, 11040.0]


def test_unmet_surplus_leaves_out_a_held_car_that_could_never_start(cfg) -> None:  # Ruling R23 c
    # Reichen Einspeisung und Klimaanlage nicht für die Startleistung, könnte das Auto mit dem
    # Überschuss nie starten: Es zählt nicht, und die Klimaanlage darf den Überschuss nehmen.
    off = evcs(charging=False, power_w=0.0)
    kw = {"intent": egolf_in(ChargeMode.PV), "evcs_obs": off}
    assert held_off_unmet(cfg, 2, 2759.0, **kw) == [0.0, 0.0]
    assert held_off_unmet(cfg, 2, 0.0, **kw) == [0.0, 0.0]
    present = {("vehicle.tesla", CURRENT): 16.0, ("vehicle.tesla", CHARGING): False}
    alone = TESLA_PLUGGED | {"evcs_obs": evcs(connected=False)}
    assert held_off_unmet(cfg, 2, 3449.0, present=present, **alone) == [0.0, 0.0]
    # Ein Auto, das lädt, zählt ohne Rücksicht darauf.
    at_8_a = evcs(charging=True, power_w=3680.0)
    assert unmet_after(cfg, ChargeMode.PV, current=8.0, on=True, evcs_obs=at_8_a) == 3680.0


def test_unmet_surplus_start_power_has_a_hysteresis(cfg) -> None:  # Ruling R28 (T12b #6)
    # Der e-Golf bei „Laden aus“ zählt ab seiner Startleistung (2760 W) und bleibt dabei, bis
    # ``spare_w`` eine Stufe von 1 A auf seinen Phasen (460 W) darunter fällt; erst dann zählt er
    # nicht mehr, und wieder erst ab der Startleistung. Rauschen um die Startleistung lässt die
    # Klimaanlage so nicht zwischen „darf“ und „weicht“ springen.
    off = evcs(charging=False, power_w=0.0)
    c = controller(cfg)
    found: list[float] = []
    spares = [2760.0, 2760.0, 2400.0, 2300.0, 2299.0, 2700.0, 2759.0, 2760.0, 2301.0]
    for s, spare_w in enumerate(spares):
        given = {"intent": egolf_in(ChargeMode.PV), "evcs_obs": off, "grid_obs": grid(grid_w=0.0)}
        accept(c, c.step(inputs(cfg, s=s, **given)))
        found.append(c.unmet_surplus_w(spare_w))
    assert found == [0.0, 7360.0, 7360.0, 7360.0, 0.0, 0.0, 0.0, 7360.0, 7360.0]


def test_unmet_surplus_leaves_out_a_tesla_that_stopped_by_itself(cfg) -> None:  # Ruling R18
    # Beim Tesla ist „Laden an“ des Aktors der Ladezustand des Autos, kein Befehl: Hört er von
    # selbst auf (in der App, ohne „Complete“), begrenzt er sich, auch wenn die Regelung ihn
    # startete. Bis RAMP_UP_S nach dem Start ist das vom Anlauf nicht zu unterscheiden: Dann folgt
    # er (Ruling R23 a).
    c = controller(cfg)
    surplus = grid(grid_w=-12000.0)
    started = c.step(inputs(cfg, present=AT_TESLA, grid_obs=surplus, **TESLA_CHARGING))
    accept(c, started)
    sent = [((x.command.actuator, x.command.kind), x.command.value) for x in started]
    assert (("vehicle.tesla", CHARGING), True) in sent
    assert c.unmet_surplus_w() == 0.0  # 16 A: nichts offen
    stopped = {**AT_TESLA, ("vehicle.tesla", CHARGING): False}
    by_itself = TESLA_CHARGING | {
        "twc_obs": twc(connected=True, power_w=0.0),
        "tesla_obs": tesla(plugged=True),
    }
    c.step(inputs(cfg, s=1, present=stopped, grid_obs=surplus, **by_itself))
    assert c.unmet_surplus_w() == 11040.0
    c.step(inputs(cfg, s=int(RAMP_UP_S), present=stopped, grid_obs=surplus, **by_itself))
    assert c.unmet_surplus_w() == 0.0
    # Ohne eigenen Befehl ebenso: Der Ladezustand allein ist kein „Laden aus“.
    fresh = controller(cfg)
    fresh.step(inputs(cfg, present=stopped, grid_obs=surplus, **by_itself))
    assert fresh.unmet_surplus_w() == 0.0


@pytest.mark.parametrize(
    ("mode", "current", "on", "obs"),
    [
        # zieht deutlich weniger als befohlen (Abregeln, Ladelimit im Auto, Timer): er begrenzt sich
        (ChargeMode.PV, 16.0, True, evcs(charging=True, power_w=1380.0)),
        (ChargeMode.PV, 8.0, True, evcs(charging=False, power_w=0.0)),
        # „Laden aus“ am Aktor ohne eigenen Befehl: Das Auto hört von selbst auf
        (ChargeMode.PV, 6.0, False, evcs(charging=False, power_w=0.0)),
        # am Höchststrom ist nichts offen
        (ChargeMode.PV, 16.0, True, evcs(charging=True, power_w=7360.0)),
        # Leistung unbekannt
        (ChargeMode.PV, 8.0, True, evcs(charging=True, power_w=None)),
        # Befehl unbekannt
        (ChargeMode.PV, None, True, evcs(charging=True, power_w=3680.0)),
        (ChargeMode.PV, 8.0, None, evcs(charging=True, power_w=3680.0)),
        # Sofort, abgesteckt, geladen
        (ChargeMode.NOW, 8.0, True, evcs(charging=True, power_w=3680.0)),
        (ChargeMode.PV, 8.0, True, evcs(connected=False)),
        (ChargeMode.PV, 8.0, True, evcs(charging=False, complete=True, power_w=0.0)),
    ],
)
def test_unmet_surplus_leaves_out_cars_that_limit_themselves(cfg, mode, current, on, obs) -> None:
    assert unmet_after(cfg, mode, current=current, on=on, evcs_obs=obs) == 0.0


def starting_unmet(cfg, seconds: int) -> list[float]:
    """``unmet_surplus_w`` je Sekunde: Der e-Golf in PV wartet 5 s ohne Überschuss bei „Laden
    aus“, dann kommen 4 kW Einspeisung. Die Regelung startet ihn nach ihrer Wartezeit; die EVCS
    meldet weiter „aus“, und er zieht nichts. Jedes Urteil der Safety-Schicht nimmt an."""
    c = controller(cfg)
    found: list[float] = []
    for s in range(seconds):
        surplus = grid(grid_w=0.0 if s < 5 else -4000.0)
        given = {"intent": egolf_in(ChargeMode.PV), "evcs_obs": evcs(charging=False, power_w=0.0)}
        accept(c, c.step(inputs(cfg, s=s, grid_obs=surplus, present=at_evcs(6.0, False), **given)))
        found.append(c.unmet_surplus_w(4000.0))
    return found


def test_ramp_up_covers_the_confirmation_of_the_slowest_charger() -> None:  # Ruling R25
    # Der Anlauf muss mindestens so lang sein wie die Frist der Bestätigung beim Tesla.
    from bindaems.core.actuators.tesla import TeslaActuator

    assert RAMP_UP_S >= TeslaActuator.confirm_timeout_s


def test_unmet_surplus_counts_a_starting_car_during_its_ramp_up(cfg) -> None:  # Ruling R23 a
    # Die Regelung startet den e-Golf bei 125 s (8 A, „Laden an“). Bis RAMP_UP_S danach folgt er
    # seinem Befehl, obwohl die EVCS noch „aus“ meldet und er nichts zieht: Der offene Wunsch
    # bleibt bei seinem Höchststrom, die Klimaanlage bekommt keine Lücke für einen Schritt.
    found = starting_unmet(cfg, 260)
    start = 125
    assert set(found[1:start]) == {7360.0}  # „Laden aus“, und 4 kW reichen für den Start
    assert set(found[start : start + int(RAMP_UP_S)]) == {7360.0}
    # Danach ist „Laden an“ des Aktors weiter unbekannt: Der Befehl gilt als unbekannt.
    assert set(found[start + int(RAMP_UP_S) + 1 :]) == {0.0}


def following_unmet(cfg, power_w, current_a, seconds: int, *, raise_at: int | None = None):
    """``unmet_surplus_w`` je Sekunde für den e-Golf in PV bei „Laden an“ an der EVCS.

    ``power_w`` und ``current_a`` (Ist-Strom des Aktors) sind Funktionen der Sekunde. Die Regelung
    hat keinen eigenen Wert, außer ``raise_at``: Dann nimmt die Safety-Schicht nach dem Schritt
    dieser Sekunde ihre Erhöhung von 8 auf 12 A an.
    """
    c = controller(cfg)
    found: list[float] = []
    for s in range(seconds):
        obs = evcs(charging=True, power_w=power_w(s))
        c.step(
            inputs(
                cfg,
                s=s,
                intent=egolf_in(ChargeMode.PV),
                evcs_obs=obs,
                present=at_evcs(current_a(s), True),
            )
        )
        found.append(c.unmet_surplus_w())
        if s == raise_at:
            for amps in (8.0, 12.0):
                command = Command("wallbox.evcs", CURRENT, amps)
                c.note_verdict(command, Verdict(command, Outcome.ACCEPTED, amps, (), True))
    return found


def test_unmet_surplus_judges_a_car_with_a_hold_time(cfg) -> None:  # Ruling R23 b
    # Der e-Golf folgt 8 A mit 3680 W; ab 10 s zieht er nur 6 A (2760 W, er regelt ab). Das Urteil
    # „begrenzt sich selbst“ gilt erst nach FOLLOW_HOLD_S; bis dahin zählt er mit seiner Leistung.
    hold = int(FOLLOW_HOLD_S)
    back = 10 + hold + 50  # ab hier zieht er wieder 8 A
    found = following_unmet(
        cfg, lambda s: 2760.0 if 10 <= s < back else 3680.0, lambda s: 8.0, back + hold + 10
    )
    assert set(found[:10]) == {3680.0}
    assert set(found[10 : 10 + hold]) == {4600.0}
    assert set(found[10 + hold : back + hold]) == {0.0}
    assert set(found[back + hold :]) == {3680.0}  # und zurück, wieder nach der Haltezeit
    # Ein einzelner Ausreißer ändert das Urteil nicht: Er zählt mit seiner Leistung weiter.
    blip = following_unmet(cfg, lambda s: 2760.0 if s == 10 else 3680.0, lambda s: 8.0, hold + 20)
    assert blip[10] == 4600.0
    assert set(blip[:10] + blip[11:]) == {3680.0}


def test_unmet_surplus_keeps_a_self_limited_car_at_its_limit(cfg) -> None:  # Ruling R23 b
    # Der e-Golf begrenzt sich bei 8 A auf 6 A (2760 W). Senkt die Regelung danach auf 7 A, weil
    # die Klimaanlage den Überschuss nimmt, folgt er scheinbar wieder (eine Stufe darunter); er
    # zieht aber nicht mehr als zuvor und bleibt selbst begrenzt. Erst wenn er wieder bis auf eine
    # Stufe zieht, wo er sich begrenzte (7 A bei 8 A), folgt er, nach der Haltezeit.
    hold = int(FOLLOW_HOLD_S)
    lowered, more = 10 + hold + 10, 4 * hold
    found = following_unmet(
        cfg,
        lambda s: 3680.0 if s < 10 else 3220.0 if s >= more else 2760.0,
        lambda s: 8.0 if s < lowered else 7.0,
        5 * hold + 10,
    )
    assert set(found[:10]) == {3680.0}
    assert set(found[10 : 10 + hold]) == {4600.0}
    assert set(found[10 + hold : more + hold]) == {0.0}  # auch bei 7 A mit 2760 W
    assert set(found[more + hold :]) == {4140.0}


def test_unmet_surplus_starts_the_hold_after_the_ramp_up_of_a_raise(cfg) -> None:  # R23 a, b
    # Der e-Golf folgt 8 A mit 3680 W; nach dem Schritt bei 10 s nimmt die Safety-Schicht die
    # Erhöhung auf 12 A an, er bleibt bei 3680 W. Während des Anlaufs folgt er, danach läuft die
    # Haltezeit: selbst begrenzt erst RAMP_UP_S + FOLLOW_HOLD_S nach der Erhöhung.
    ramp, hold = int(RAMP_UP_S), int(FOLLOW_HOLD_S)
    found = following_unmet(
        cfg, lambda s: 3680.0, lambda s: 8.0 if s < 10 else 12.0, ramp + hold + 30, raise_at=10
    )
    assert set(found[: 10 + ramp + hold]) == {3680.0}
    assert set(found[10 + ramp + hold + 1 :]) == {0.0}


def test_unmet_surplus_needs_a_regulated_point_and_a_step(cfg) -> None:
    c = controller(cfg)
    assert c.unmet_surplus_w() == 0.0  # vor dem ersten ``step``
    present = {("wallbox.evcs", CURRENT): 8.0, ("wallbox.evcs", CHARGING): True}
    at_8_a = evcs(charging=True, power_w=3680.0)
    unregulated = {"wallbox.evcs": False, "vehicle.tesla": False}
    given = {"intent": egolf_in(ChargeMode.PV), "present": present, "evcs_obs": at_8_a}
    c.step(inputs(cfg, controllable=unregulated, **given))
    assert c.unmet_surplus_w() == 0.0
    c.step(inputs(cfg, s=1, **given))
    assert c.unmet_surplus_w() == 3680.0
    c.idle(T0 + timedelta(seconds=2))
    assert c.unmet_surplus_w() == 0.0


def test_overload_level_follows_the_last_step(cfg) -> None:
    c = controller(cfg)
    assert c.overload_level() is OverloadLevel.NONE
    # Eine Phase über der Sicherung: nach ``MIN_AFTER_S`` (3 s) mindestens ``MINIMUM``
    over = grid(40.0, 2.0, 2.0)
    for s in range(4):
        c.step(inputs(cfg, s=s, grid_obs=over))
    assert c.overload_level() >= OverloadLevel.MINIMUM
    c.idle(T0 + timedelta(seconds=4))
    assert c.overload_level() is OverloadLevel.NONE


# --- Plan Sofort-Limit: Höchststrom in „Sofort“ je Fahrzeug und für Gäste ------------------------


def egolf_now(limit: int | None, version: int = 1, **update) -> Intent:
    """Die Absicht mit dem e-Golf in Sofort und dem Limit ``limit``; ``update`` ändert die
    Absicht (etwa den Gast)."""
    entry = VehicleIntent(mode=ChargeMode.NOW, now_max_a=limit)
    return Intent(version=version, control=True, vehicles={"egolf": entry}, **update)


NOW_LIMIT_REASONS = ("mode_now", "now_limit")


def test_now_limit_caps_the_current_and_names_the_reason(cfg) -> None:
    # frei 33 − 2 = 31 A: Nicht der Wächter, das Limit 10 A begrenzt den e-Golf.
    found = controller(cfg).step(inputs(cfg, intent=egolf_now(10)))
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 10.0, reasons=NOW_LIMIT_REASONS),
        Command("wallbox.evcs", CHARGING, True, reasons=NOW_LIMIT_REASONS),
    ]


def test_now_limit_never_below_the_point_minimum(cfg) -> None:
    # Der Tesla an der EVCS (Mindeststrom dort max(6, 5) = 6 A) mit dem Limit 5 A
    tesla_now = VehicleIntent(mode=ChargeMode.NOW, wallbox="evcs", now_max_a=5)
    intent = Intent(version=1, control=True, vehicles={"tesla": tesla_now})
    found = controller(cfg).step(inputs(cfg, intent=intent))
    currents = [x.command for x in found if x.command.actuator == "wallbox.evcs"]
    assert currents == [
        Command("wallbox.evcs", CURRENT, 6.0, reasons=NOW_LIMIT_REASONS),
        Command("wallbox.evcs", CHARGING, True, reasons=NOW_LIMIT_REASONS),
    ]


def test_boost_ignores_the_now_limit(cfg) -> None:
    # Boost lädt mit voller Leistung (Entscheidung 3); danach gilt in Sofort das Limit, und die
    # Senkung von 16 auf 8 A ist nicht dringend: Die Sicherung allein verlangt sie nicht.
    entry = VehicleIntent(mode=ChargeMode.NOW, now_max_a=8, boost_until=T0 + timedelta(hours=1))
    intent = Intent(version=1, control=True, vehicles={"egolf": entry})
    c = controller(cfg)
    found = c.step(inputs(cfg, intent=intent))
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 16.0, reasons=("boost",)),
        Command("wallbox.evcs", CHARGING, True, reasons=("boost",)),
    ]
    accept(c, found)
    after = c.step(
        inputs(cfg, s=3601, intent=intent, evcs_obs=AT_16_A, present=at_evcs(16.0, True))
    )
    assert [x.command for x in after] == [
        Command("wallbox.evcs", CURRENT, 8.0, reasons=NOW_LIMIT_REASONS)
    ]


def test_now_limit_leaves_the_rest_of_the_surplus_to_a_pv_car(cfg) -> None:
    # 9000 W Überschuss (9100 − 100); der e-Golf in Sofort mit 8 A nimmt vorab 8 · 230 · 2 = 3680 W,
    # dem Tesla in PV am Wall Connector bleiben floor(5320 / 690) = 7 A. Ohne Limit nähme der
    # e-Golf 7360 W, und die 2 A Rest trügen den Tesla nicht.
    found = run(
        controller(cfg),
        cfg,
        121,
        intent=egolf_now(8),
        grid_obs=grid(grid_w=-9100.0),
        **TESLA_PLUGGED,
    )
    currents = [
        (t, x.command.actuator, x.command.value) for t, x in found if x.command.kind == CURRENT
    ]
    assert currents == [(0, "wallbox.evcs", 8.0), (120, "vehicle.tesla", 7.0)]
    assert found[0][1].inputs["surplus_w"] == 9000.0


def test_now_car_never_counts_toward_unmet_surplus_even_when_capped(cfg) -> None:
    # Ein Auto in Sofort zählt nie zum offenen Wunsch (nur PV und Min+PV), auch nicht bis zum
    # ungekappten Höchststrom: Was über dem Limit frei ist, darf die Klimaanlage nehmen.
    at_8_a = evcs(charging=True, phase_a={"L1": 8.0, "L2": 8.0, "L3": 0.0}, power_w=3680.0)
    c = controller(cfg)
    c.step(inputs(cfg, intent=egolf_now(8), evcs_obs=at_8_a, present=at_evcs(8.0, True)))
    assert c.unmet_surplus_w() == 0.0


def test_now_limit_change_while_charging_lowers_the_current_next_cycle(cfg) -> None:
    c = controller(cfg)
    first = c.step(inputs(cfg, **NOW_AT_16_A))
    assert first[0].command == Command("wallbox.evcs", CURRENT, 16.0, reasons=("mode_now",))
    accept(c, first)
    lowered = c.step(
        inputs(cfg, s=1, intent=egolf_now(10, 2), evcs_obs=AT_16_A, present=at_evcs(16.0, True))
    )
    # nicht dringend: Die Sicherung allein verlangt keine Senkung (Entscheidung 6)
    assert [x.command for x in lowered] == [
        Command("wallbox.evcs", CURRENT, 10.0, reasons=NOW_LIMIT_REASONS)
    ]
    accept(c, lowered)
    raised = c.step(
        inputs(cfg, s=2, intent=egolf_now(12, 3), evcs_obs=AT_16_A, present=at_evcs(10.0, True))
    )
    assert [x.command for x in raised] == [
        Command("wallbox.evcs", CURRENT, 12.0, reasons=NOW_LIMIT_REASONS)
    ]


def test_guard_below_the_now_limit_keeps_its_own_reason(cfg) -> None:
    # frei 33 − (40 − 16) = 9 A unter dem Limit 12 A: Grund und Dringlichkeit des Wächters
    situation = inputs(
        cfg,
        intent=egolf_now(12),
        evcs_obs=AT_16_A,
        present=at_evcs(16.0, True),
        grid_obs=grid(l1=40.0, l2=18.0),
    )
    found = controller(cfg).step(situation)
    reasons = ("mode_now", "phase_limit")
    assert found[0].command == Command("wallbox.evcs", CURRENT, 9.0, urgent=True, reasons=reasons)


def test_a_lowering_the_fuse_demands_stays_urgent_below_the_now_limit(cfg) -> None:
    # frei 33 − (49 − 16) = 0 A: Der Strom geht auf den Mindeststrom 6 A, dringend wie ohne Limit.
    situation = inputs(
        cfg,
        intent=egolf_now(10),
        evcs_obs=AT_16_A,
        present=at_evcs(16.0, True),
        grid_obs=grid(l1=49.0, l2=18.0),
    )
    found = controller(cfg).step(situation)
    reasons = ("mode_now", "phase_limit")
    assert found[0].command == Command("wallbox.evcs", CURRENT, 6.0, urgent=True, reasons=reasons)


# Ein Wächter zwischen Limit und ungekapptem Wunsch: frei 33 − (37 − 16) = 12 A über dem Limit 10 A
# und unter den 16 A. Das Limit begrenzt den Wert, der Wächter allein senkte aber schon unter den
# Ist-Strom: beide Gründe, dringend wie ohne Limit (Plan Sofort-Limit, Entscheidung 8).
LIMIT_AND_GUARD = ("mode_now", "now_limit", "phase_limit")


def test_a_guard_between_now_limit_and_wish_names_both_at_the_evcs(cfg) -> None:
    situation = inputs(
        cfg,
        intent=egolf_now(10),
        evcs_obs=AT_16_A,
        present=at_evcs(16.0, True),
        grid_obs=grid(l1=37.0, l2=18.0),
    )
    found = controller(cfg).step(situation)
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 10.0, urgent=True, reasons=LIMIT_AND_GUARD),
        Command("wallbox.evcs", CHARGING, True, reasons=LIMIT_AND_GUARD),
    ]


def test_a_guard_between_now_limit_and_wish_names_both_while_holding(cfg) -> None:
    # bei unbekanntem Ansteckzustand (``_hold``) ebenso
    unknown = replace(AT_16_A, connected=None, charging=None, complete=None)
    situation = inputs(
        cfg,
        intent=egolf_now(10),
        evcs_obs=unknown,
        present=at_evcs(16.0, True),
        grid_obs=grid(l1=37.0, l2=18.0),
    )
    found = controller(cfg).step(situation)
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 10.0, urgent=True, reasons=LIMIT_AND_GUARD)
    ]


def test_a_guard_between_now_limit_and_wish_names_both_at_the_wall_connector(cfg) -> None:
    # Der Tesla lädt in Sofort mit 16 A, dann gilt das Limit 10 A: Das Fenster wünscht im Mittel
    # (16 + 10) / 2 = 13 A, die Grenze ist 12 A; der Strom bleibt 10 A, der Grund wie an der EVCS.
    tesla_obs = {
        "twc_obs": twc(
            connected=True, charging=True, phase_a=dict.fromkeys(("L1", "L2", "L3"), 16.0)
        ),
        "tesla_obs": tesla(plugged=True, charging=True, actual_current_a=16.0),
        "controllable": BOTH,
        "evcs_obs": evcs(connected=False),
        "present": {("vehicle.tesla", CURRENT): 16.0, ("vehicle.tesla", CHARGING): True},
    }
    full = Intent(version=1, control=True, vehicles={"tesla": NOW})
    capped = Intent(
        version=2,
        control=True,
        vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW, now_max_a=10)},
    )
    c = controller(cfg)
    accept(c, c.step(inputs(cfg, intent=full, grid_obs=grid(18.0, 18.0, 18.0), **tesla_obs)))
    found = c.step(inputs(cfg, s=1, intent=capped, grid_obs=grid(37.0, 18.0, 18.0), **tesla_obs))
    assert [x.command for x in found if x.command.actuator == "vehicle.tesla"] == [
        Command("vehicle.tesla", CURRENT, 10.0, urgent=True, reasons=LIMIT_AND_GUARD)
    ]


def test_now_limit_from_a_stale_intent_above_config_max_is_clamped(cfg) -> None:
    found = controller(cfg).step(inputs(cfg, intent=egolf_now(32)))
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 16.0, reasons=("mode_now",)),
        Command("wallbox.evcs", CHARGING, True, reasons=("mode_now",)),
    ]


@pytest.mark.parametrize(("l1", "urgent"), [(18.0, False), (40.0, True)], ids=["limit", "fuse"])
def test_unknown_plug_state_lowers_to_the_now_limit(cfg, l1, urgent) -> None:  # Präzisierung 4
    # Bei unbekanntem Ansteckzustand sinkt der Strom auch auf das Limit (10 A), nicht dringend;
    # kappt der Wächter darunter (bei 40 A auf L1 frei 9 A), mit seinem Grund und dringend.
    unknown = replace(AT_16_A, connected=None, charging=None, complete=None)
    situation = inputs(
        cfg,
        intent=egolf_now(10),
        evcs_obs=unknown,
        present=at_evcs(16.0, True),
        grid_obs=grid(l1=l1, l2=18.0),
    )
    found = controller(cfg).step(situation)
    expected = (
        Command("wallbox.evcs", CURRENT, 9.0, urgent=True, reasons=("mode_now", "phase_limit"))
        if urgent
        else Command("wallbox.evcs", CURRENT, 10.0, reasons=NOW_LIMIT_REASONS)
    )
    assert [x.command for x in found] == [expected]


def test_now_limit_caps_the_tesla_at_the_wall_connector(cfg) -> None:
    tesla_now = VehicleIntent(mode=ChargeMode.NOW, now_max_a=10)
    intent = Intent(version=1, control=True, vehicles={"tesla": tesla_now})
    found = controller(cfg).step(
        inputs(cfg, intent=intent, evcs_obs=evcs(connected=False), **TESLA_PLUGGED)
    )
    assert [x.command for x in found if x.command.actuator == "vehicle.tesla"] == [
        Command("vehicle.tesla", CURRENT, 10.0, reasons=NOW_LIMIT_REASONS),
        Command("vehicle.tesla", CHARGING, True, reasons=NOW_LIMIT_REASONS),
    ]


def test_guest_now_limit_caps_the_guest_at_the_evcs(cfg) -> None:
    guest = GuestIntent(mode=ChargeMode.NOW, now_max_a=8)
    intent = Intent(version=1, control=True, guest=guest)
    found = controller(cfg).step(inputs(cfg, intent=intent, homes={"egolf": False}))
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 8.0, reasons=NOW_LIMIT_REASONS),
        Command("wallbox.evcs", CHARGING, True, reasons=NOW_LIMIT_REASONS),
    ]


def test_guest_now_limit_does_not_touch_a_vehicle(cfg) -> None:
    intent = egolf_now(None, guest=GuestIntent(mode=ChargeMode.NOW, now_max_a=8))
    found = controller(cfg).step(inputs(cfg, intent=intent))
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 16.0, reasons=("mode_now",)),
        Command("wallbox.evcs", CHARGING, True, reasons=("mode_now",)),
    ]


def test_guest_now_limit_at_the_wall_connector_sends_nothing(cfg) -> None:
    # Am Wall Connector hat ein Gast keinen Aktor (Entscheidung 5): Er bleibt Last, und fällt der
    # Wall Connector aus, zählt weiter sein voller Höchststrom als Zuschlag (33 − 2 − 16 = 15 A).
    intent = egolf_now(None, guest=GuestIntent(mode=ChargeMode.NOW, now_max_a=8))
    c = controller(cfg)
    first = c.step(inputs(cfg, intent=intent, twc_obs=twc(connected=True), controllable=BOTH))
    out = c.step(inputs(cfg, s=1, intent=intent, twc_obs=TWC_OUT, controllable=BOTH))
    assert {x.command.actuator for x in [*first, *out]} == {"wallbox.evcs"}
    assert c.health()[1]["limit_a"] == 15.0
    guest = next(car for car in c.cars() if car.wallbox == "twc")
    assert (guest.vehicle, guest.mode, guest.regulated) == ("guest@twc", Effective.NOW, False)
