import math
from dataclasses import replace
from datetime import timedelta

import pytest
from tests.helpers import T0
from tests.unit.core.control.charge_helpers import (
    CONTRADICTION,
    UNCLEAR_FROM_S,
    accept,
    evcs,
    grid,
    inputs,
    run,
    tesla,
    twc,
)

from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.charging import CarView, ChargeController
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.peak_guard import NO_LIMITS, PeakLimits
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
    assert as_commands(found) == [cmd(0, CommandKind.CHARGING, False, ("mode_off",))]


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
        cmd(131, CommandKind.CHARGING, False, ("mode_off",))
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
        "reason": "mode_pv",
    }


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
        cmd(0, CHARGING, False, ("mode_off",)),
        cmd(3, CHARGING, False, ("mode_off",)),
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
    off = Command("vehicle.tesla", CHARGING, False, reasons=(code,))
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
    off = Command("vehicle.tesla", CHARGING, False, reasons=(code,))
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
    off = Command("vehicle.tesla", CHARGING, False, reasons=("mode_off",))
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
