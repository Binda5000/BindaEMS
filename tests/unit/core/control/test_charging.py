from dataclasses import replace
from datetime import timedelta

import pytest
from tests.helpers import T0
from tests.unit.core.control.charge_helpers import accept, evcs, grid, inputs, run, tesla, twc

from bindaems.core.control.charging import ChargeController
from bindaems.core.control.executor import WriteResult
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import Outcome, Verdict
from bindaems.shared.control import (
    ChargeMode,
    Command,
    CommandKind,
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
    assert [(400, x.command) for x in resumed] == [
        cmd(400, CURRENT, 6.0, ("mode_pv",)),
        cmd(400, CHARGING, True, ("mode_pv",)),
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
    assert found == [
        cmd(4, CURRENT, 15.0, ("mode_now", "phase_limit"), urgent=True),
        cmd(4, CHARGING, True, ("mode_now", "phase_limit")),
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
    tesla_at_the_evcs = {
        "intent": egolf_in(ChargeMode.NOW),
        "twc_obs": twc(connected=False),
        "tesla_obs": tesla(plugged=True),
        "grid_obs": grid(l3=30.0),
        "controllable": BOTH,
    }
    run(c, cfg, 60, **tesla_at_the_evcs)
    before = c.health()
    accept(c, c.step(inputs(cfg, s=60, **tesla_at_the_evcs)))
    after = c.health()
    assert (before[1]["assignment"], before[1]["limit_a"]) == ("default", 31.0)  # L1, L2
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
    shared = cfg.vehicles["tesla"].model_copy(update={"default_wallbox": "evcs"})
    shared_cfg = cfg.model_copy(update={"vehicles": {**cfg.vehicles, "tesla": shared}})
    c = ChargeController(shared_cfg, HardLimits.from_config(shared_cfg), started=T0)
    both_now = Intent(version=1, control=True, vehicles={"tesla": NOW, "egolf": NOW})
    at_10_a = evcs(charging=True, phase_a=dict.fromkeys(("L1", "L2", "L3"), 10.0), power_w=6900.0)
    found = c.step(
        inputs(
            shared_cfg,
            intent=both_now,
            evcs_obs=at_10_a,
            present=at_evcs(10.0, True),
            grid_obs=grid(l1=12.0, l2=12.0, l3=12.0),
        )
    )
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
    unclear = controller(cfg)
    run(unclear, cfg, 61, twc_obs=twc(connected=False), tesla_obs=tesla(plugged=True))
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
        61,
        intent=egolf_in(ChargeMode.NOW),
        evcs_obs=evcs(charging=True),
        twc_obs=twc(connected=False),
        tesla_obs=tesla(plugged=True),
        present=at_evcs(12.0, True),
        grid_obs=grid(l1=30.0),
    )
    # frei 33 − 30 = 3: mit dem e-Golf sein Mindeststrom 8 A, ab „unklar“ der der EVCS
    assert [(t, c.command.value) for t, c in found if c.command.kind == CURRENT] == [
        (0, 8.0),
        (60, 6.0),
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
    c = controller(cfg)
    found = c.step(inputs(cfg, intent=egolf_in(ChargeMode.NOW), grid_obs=grid(l1=None)))
    reasons = ("mode_now", "phase_limit")
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 6.0, reasons=reasons),
        Command("wallbox.evcs", CHARGING, True, reasons=reasons),
    ]
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
    reasons = ("mode_pv", "pv_surplus")
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
