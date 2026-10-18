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

from bindaems.core.control.assignment import AssignmentKind, VehicleState
from bindaems.core.control.charge_modes import ModeDecision
from bindaems.core.control.charging import ChargeController
from bindaems.core.control.charging_tesla import AgainstMode
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.foreign import WATCHED_KINDS
from bindaems.core.control.peak_guard import NO_LIMITS, PeakLimits
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import Outcome, Verdict
from bindaems.shared.config import WakeConfig
from bindaems.shared.control import ChargeMode, Command, CommandKind, Intent, VehicleIntent
from bindaems.shared.decisions import DecisionResult

CURRENT, CHARGING = CommandKind.CURRENT, CommandKind.CHARGING
PHASES = ("L1", "L2", "L3")
TWC_ON = twc(connected=True)
# Der Wall Connector misst den ladenden Tesla (Präzisierung 4): 10 bzw. 16 A je Phase. Bei 10 A
# bleibt seine Leistung bei 0 W; so ist der Überschuss allein die Einspeisung.
TWC_10_A = twc(connected=True, charging=True, phase_a=dict.fromkeys(PHASES, 10.0))
TWC_16_A = twc(connected=True, charging=True, phase_a=dict.fromkeys(PHASES, 16.0), power_w=11040.0)
TESLA_NOW = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW)})
EGOLF_NOW = Intent(version=1, control=True, vehicles={"egolf": VehicleIntent(mode=ChargeMode.NOW)})
ONLY_WAKEABLE = {
    "controllable": {"wallbox.evcs": True, "vehicle.tesla": False},
    "wakeable": {"vehicle.tesla": True},
}


def tesla_inputs(cfg, s, **kw):
    kw.setdefault("controllable", {"wallbox.evcs": True, "vehicle.tesla": True})
    kw.setdefault("tesla_obs", tesla(plugged=True))
    kw.setdefault("twc_obs", TWC_ON)
    kw.setdefault("evcs_obs", evcs(connected=False))
    return inputs(cfg, s=s, **kw)


def currents(found, actuator="vehicle.tesla"):
    return [
        (t, c.command.value)
        for t, c in found
        if c.command.actuator == actuator and c.command.kind == CommandKind.CURRENT
    ]


def controller(cfg) -> ChargeController:
    return ChargeController(cfg, HardLimits.from_config(cfg), started=T0)


def at_tesla(current: float, charging: bool) -> dict:
    """Die Ist-Werte des Tesla; die der EVCS sind unbekannt."""
    return {("vehicle.tesla", CURRENT): current, ("vehicle.tesla", CHARGING): charging}


def at_evcs(current: float, charging: bool) -> dict:
    """Die Ist-Werte der EVCS; die des Tesla sind unbekannt."""
    return {("wallbox.evcs", CURRENT): current, ("wallbox.evcs", CHARGING): charging}


def egolf_in(mode: ChargeMode, version: int = 1) -> Intent:
    """Die Absicht mit dem e-Golf in ``mode``; der Tesla hat keinen Eintrag."""
    return Intent(version=version, control=True, vehicles={"egolf": VehicleIntent(mode=mode)})


def of(found, actuator: str) -> list:
    """``(Sekunde, Befehl)`` für die Befehle an ``actuator``."""
    return [(t, c.command) for t, c in found if c.command.actuator == actuator]


def steps(c: ChargeController, cfg, seconds: range, *, make=inputs, **kw) -> list:
    """Wie ``run``, aber für die Sekunden ``seconds``; zurück kommt ``(Sekunde, ChargeCommand)``."""
    found = []
    for s in seconds:
        resolved = {key: value(s) if callable(value) else value for key, value in kw.items()}
        commands = c.step(make(cfg, s=s, **resolved))
        accept(c, commands)
        found.extend((s, x) for x in commands)
    return found


# Vorgaben des Plans


def test_tesla_current_follows_the_90_s_average(cfg) -> None:
    def grid_at(t: int):
        return grid(grid_w=-7000.0 if t < 60 else -11200.0)

    found = run(
        ChargeController(cfg, HardLimits.from_config(cfg), started=T0),
        cfg,
        90,
        make=tesla_inputs,
        grid_obs=grid_at,
        tesla_obs=tesla(plugged=True, charging=True),
        twc_obs=TWC_10_A,  # das Laden misst der Wall Connector; so übernimmt PV es bei 0
    )
    assert currents(found) == [(0, 10.0), (71, 11.0), (89, 12.0)]


def test_the_evcs_hysteresis_leaves_the_tesla_alone(cfg) -> None:  # Design 6.5
    # Anteil 7038 / 690 = 10,2 A, ab 1 s 7728 / 690 = 11,2 A: Das Mittel erreicht 11 A, sobald der
    # Wert von 0 s älter als 90 s ist. Mit der Hysterese der EVCS bliebe der Wunsch bei 10 A.
    found = run(
        controller(cfg),
        cfg,
        100,
        make=tesla_inputs,
        grid_obs=lambda s: grid(grid_w=-7138.0 if s == 0 else -7828.0),
        tesla_obs=tesla(plugged=True, charging=True),
        twc_obs=TWC_10_A,
    )
    assert currents(found) == [(0, 10.0), (91, 11.0)]


def test_tesla_gets_the_lowest_free_value_of_60_s(cfg) -> None:
    found = run(
        controller(cfg),
        cfg,
        72,
        make=tesla_inputs,
        intent=TESLA_NOW,
        twc_obs=TWC_16_A,
        tesla_obs=tesla(plugged=True, charging=True),
        present=at_tesla(16.0, True),
        grid_obs=lambda s: grid(l1=41.0) if s == 10 else grid(l1=20.0),
    )
    assert currents(found) == [(0, 16.0), (10, 8.0), (71, 16.0)]
    sent = dict(of(found, "vehicle.tesla"))
    # bei 10: frei 33 − (41 − 16) = 8, die Sicherung ist das Ziel: dringend
    assert sent[10] == Command(
        "vehicle.tesla", CURRENT, 8.0, urgent=True, reasons=("mode_now", "phase_limit")
    )
    assert sent[71] == Command("vehicle.tesla", CURRENT, 16.0, reasons=("mode_now",))


def test_a_sleeping_tesla_is_woken_for_a_start_within_its_quota(cfg) -> None:
    c = controller(cfg)
    found = []
    for s in range(1801):
        commands = c.step(
            tesla_inputs(
                cfg,
                s,
                intent=TESLA_NOW,
                tesla_obs=tesla(plugged=True, asleep=True),
                **ONLY_WAKEABLE,
            )
        )
        accept(c, commands)
        found.extend((s, x) for x in commands)
        if s == 5:  # der Start ist gescheitert
            c.note_result(
                WriteResult(
                    "vehicle.tesla", CHARGING, True, DecisionResult.ERROR, "Start scheiterte."
                )
            )
    wake = Command("vehicle.tesla", CHARGING, True, reasons=("mode_now", "wake"), wake=True)
    assert of(found, "vehicle.tesla") == [(0, wake), (1800, wake)]


def test_no_wake_in_pv_without_enough_surplus(cfg) -> None:
    found = run(
        controller(cfg),
        cfg,
        300,
        make=tesla_inputs,
        tesla_obs=tesla(plugged=True, asleep=True),
        grid_obs=grid(grid_w=0.0),
        **ONLY_WAKEABLE,
    )
    assert of(found, "vehicle.tesla") == []


def test_not_taken_is_requested_again_with_its_reason(cfg) -> None:
    c = controller(cfg)
    charging = {
        "twc_obs": TWC_10_A,
        "tesla_obs": tesla(plugged=True, charging=True),
        "present": at_tesla(10.0, True),
        "grid_obs": grid(grid_w=-3700.0),  # 3600 W → 5 A
    }
    first = c.step(tesla_inputs(cfg, 0, **charging))
    accept(c, first)
    assert currents([(0, x) for x in first]) == [(0, 5.0)]
    assert first[0].command == Command(
        "vehicle.tesla", CURRENT, 5.0, reasons=("mode_pv", "pv_surplus")
    )
    c.note_result(
        WriteResult(
            "vehicle.tesla",
            CURRENT,
            5.0,
            DecisionResult.ERROR,
            "Fahrzeug übernahm 5 A nicht (Tessie meldet 10 A).",
            not_taken=True,
        )
    )
    again = c.step(tesla_inputs(cfg, 1, **charging))
    assert [x.command for x in again] == [
        Command("vehicle.tesla", CURRENT, 5.0, reasons=("mode_pv", "pv_surplus", "not_taken_retry"))
    ]


def test_tesla_at_the_evcs_gets_its_maximum_once(cfg) -> None:
    at_the_evcs = Intent(
        version=1,
        control=True,
        vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW, wallbox="evcs")},
    )
    found = run(controller(cfg), cfg, 61, make=tesla_inputs, intent=at_the_evcs, evcs_obs=evcs())
    reasons = ("mode_now",)
    assert [(t, c.command) for t, c in found] == [
        (0, Command("vehicle.tesla", CURRENT, 16.0, reasons=reasons)),
        (0, Command("wallbox.evcs", CURRENT, 16.0, reasons=reasons)),
        (0, Command("wallbox.evcs", CHARGING, True, reasons=reasons)),
    ]


def test_unclear_pauses_the_tesla_and_holds_the_evcs(cfg) -> None:
    found = run(
        controller(cfg),
        cfg,
        UNCLEAR_FROM_S + 2,
        make=tesla_inputs,
        **CONTRADICTION,
        intent=EGOLF_NOW,
        present=at_evcs(10.0, True),
        grid_obs=lambda s: grid(l1=36.0) if s == UNCLEAR_FROM_S + 1 else grid(),
    )
    # ab 120 unklar: nichts an den Tesla, kein „Laden an/aus“ an die EVCS; bei 121 frei
    # 33 − (36 − 10) = 7
    assert [(t, c.command) for t, c in found if t >= UNCLEAR_FROM_S] == [
        (
            UNCLEAR_FROM_S + 1,
            Command(
                "wallbox.evcs",
                CURRENT,
                7.0,
                urgent=True,
                reasons=("mode_now", "assignment_unclear", "phase_limit"),
            ),
        )
    ]


def test_resume_restarts_now_at_once(cfg) -> None:
    c = controller(cfg)
    run(c, cfg, 10, intent=EGOLF_NOW, evcs_obs=evcs(charging=True), present=at_evcs(16.0, True))
    c.idle(T0 + timedelta(seconds=10))
    found = c.step(
        inputs(
            cfg,
            s=130,
            intent=EGOLF_NOW,
            evcs_obs=evcs(charging=False),
            present=at_evcs(6.0, False),
        )
    )
    reasons = ("mode_now", "resume_after_outage")
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 16.0, reasons=reasons),
        Command("wallbox.evcs", CHARGING, True, reasons=reasons),
    ]


def test_resume_in_pv_waits_for_start_after(cfg) -> None:
    c = controller(cfg)
    run(c, cfg, 10)  # PV mit 3900 W Überschuss, bei 0 „Laden aus“
    c.idle(T0 + timedelta(seconds=10))
    found = steps(c, cfg, range(130, 251))
    reasons = ("mode_pv", "pv_surplus", "resume_after_outage")
    assert [(t, x.command) for t, x in found] == [
        (130, Command("wallbox.evcs", CHARGING, False, reasons=("mode_pv",))),  # kein Start
        (250, Command("wallbox.evcs", CURRENT, 8.0, reasons=reasons)),
        (250, Command("wallbox.evcs", CHARGING, True, reasons=reasons)),
    ]


def test_vehicle_wallbox_for_the_safety_layer(cfg) -> None:
    c = controller(cfg)
    c.step(tesla_inputs(cfg, 0))
    assert c.vehicle_wallbox() == {"vehicle.tesla": "twc"}
    override = Intent(version=2, control=True, vehicles={"tesla": VehicleIntent(wallbox="evcs")})
    c.step(tesla_inputs(cfg, 1, intent=override, evcs_obs=evcs()))
    assert c.vehicle_wallbox() == {"vehicle.tesla": "evcs"}
    # „Tesla an der EVCS“: ab 60 s dort erkannt
    detected = controller(cfg)
    run(detected, cfg, 61, make=tesla_inputs, twc_obs=twc(connected=False), evcs_obs=evcs())
    assert detected.vehicle_wallbox() == {"vehicle.tesla": "evcs"}
    unclear = controller(cfg)
    run(unclear, cfg, UNCLEAR_FROM_S + 1, make=tesla_inputs, **CONTRADICTION)
    assert unclear.vehicle_wallbox() == {"vehicle.tesla": None}


def test_tesla_without_tessie_is_load(cfg) -> None:
    found = controller(cfg).step(
        tesla_inputs(
            cfg,
            0,
            controllable={"wallbox.evcs": True, "vehicle.tesla": False},
            twc_obs=twc(
                connected=True,
                charging=True,
                phase_a=dict.fromkeys(PHASES, 10.0),
                power_w=6900.0,
            ),
            evcs_obs=evcs(
                charging=True, phase_a={"L1": 10.0, "L2": 10.0, "L3": 0.0}, power_w=4600.0
            ),
            present=at_evcs(10.0, True),
            grid_obs=grid(l1=22.0, l2=22.0, l3=12.0, grid_w=-8000.0),
        )
    )
    # Der Strom des Tesla bleibt Last: frei 19 − (22 − 10) = 7 auf L1 und L2
    reasons = ("mode_pv", "import_limit")
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 7.0, reasons=reasons),
        Command("wallbox.evcs", CHARGING, True, reasons=reasons),
    ]


def test_unreachable_evcs_reserves_its_safe_current(cfg) -> None:
    found = controller(cfg).step(
        tesla_inputs(
            cfg,
            0,
            intent=TESLA_NOW,
            evcs_obs=evcs(reachable=False, connected=None, phase_a=None),
            twc_obs=TWC_16_A,
            tesla_obs=tesla(plugged=True, charging=True),
            grid_obs=grid(l1=18.0, l2=18.0, l3=18.0, soc=20.0),
        )
    )
    # 2 A Last + 6 A Zuschlag der EVCS, Ziel 19 A
    reasons = ("mode_now", "reserve_guard")
    assert [x.command for x in found] == [
        Command("vehicle.tesla", CURRENT, 11.0, reasons=reasons),
        Command("vehicle.tesla", CHARGING, True, reasons=reasons),
    ]


def test_wall_connector_outage_takes_the_current_from_tessie(cfg) -> None:
    c = controller(cfg)
    found = c.step(
        tesla_inputs(
            cfg,
            0,
            intent=TESLA_NOW,
            twc_obs=twc(reachable=False, connected=None, phase_a=None),
            tesla_obs=tesla(plugged=True, charging=True, actual_current_a=16.0, phases=3),
            grid_obs=grid(l1=30.0, l2=30.0, l3=30.0, soc=20.0),
        )
    )
    # Der Tesla bleibt geregelt: Last 30 − 16 = 14 A, frei 19 − 14 = 5 A
    assert c.health()[0]["limit_a"] == 5.0
    reasons = ("mode_now", "reserve_guard")
    assert [x.command for x in found if x.command.actuator == "vehicle.tesla"] == [
        Command("vehicle.tesla", CURRENT, 5.0, reasons=reasons),
        Command("vehicle.tesla", CHARGING, True, reasons=reasons),
    ]


# Weitere Fälle: Festlegungen der Aufgabe

TESLA_OFF = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(mode=ChargeMode.OFF)})
TESLA_NOW_2 = Intent(
    version=2, control=True, vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW)}
)
AT_THE_EVCS = Intent(
    version=1,
    control=True,
    vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW, wallbox="evcs")},
)
MAXIMUM = Command("vehicle.tesla", CURRENT, 16.0, reasons=("mode_now",))
WAKE = Command("vehicle.tesla", CHARGING, True, reasons=("mode_now", "wake"), wake=True)
TESLA_CHARGING = {
    "intent": TESLA_NOW,
    "twc_obs": TWC_16_A,
    "tesla_obs": tesla(plugged=True, charging=True),
    "present": at_tesla(16.0, True),
}


def with_vehicle(cfg, key: str, **update):
    """Die Konfiguration mit geänderten Werten für das Fahrzeug ``key``."""
    vehicle = cfg.vehicles[key].model_copy(update=update)
    return cfg.model_copy(update={"vehicles": {**cfg.vehicles, key: vehicle}})


def dip_at_5(s: int):
    """L1 bei 41 A in Sekunde 5, sonst 20 A: Der Tesla mit 16 A hat dann frei 8 statt 29 A."""
    return grid(l1=41.0) if s == 5 else grid(l1=20.0)


def failed_start(c: ChargeController) -> None:
    c.note_result(WriteResult("vehicle.tesla", CHARGING, True, DecisionResult.ERROR, "x"))


def test_an_unknown_free_value_counts_as_zero_for_60_s(cfg) -> None:  # Präzisierung 12
    found = run(
        controller(cfg),
        cfg,
        67,
        make=tesla_inputs,
        grid_obs=lambda s: grid(l1=None) if s == 5 else grid(),
        **TESLA_CHARGING,
    )
    assert currents(found) == [(0, 16.0), (5, 5.0), (66, 16.0)]
    assert dict(of(found, "vehicle.tesla"))[5] == Command(
        "vehicle.tesla", CURRENT, 5.0, urgent=True, reasons=("mode_now", "phase_limit")
    )


@pytest.mark.parametrize(
    ("reported_at", "steerable"), [(12, True), (14, False)], ids=["regulated", "begun_unregulated"]
)
def test_a_new_session_starts_the_windows_anew(cfg, reported_at, steerable) -> None:  # Präz. 12
    # Bei 10 und 11 abgesteckt, ab 12 meldet der Wall Connector den Tesla wieder. Im zweiten Fall
    # ist der Tesla bis 13 nicht steuerbar: Der gemerkte Vorgang erreicht ihn bei 14.
    def make(cfg, *, s: int, **kw):
        if 10 <= s < 12:
            kw = {**kw, "twc_obs": twc(), "tesla_obs": tesla()}
        if 10 <= s < 14 and not steerable:
            kw = {**kw, "controllable": {"wallbox.evcs": True, "vehicle.tesla": False}}
        return tesla_inputs(cfg, s, **kw)

    found = run(controller(cfg), cfg, 15, make=make, grid_obs=dip_at_5, **TESLA_CHARGING)
    assert currents(found) == [(0, 16.0), (5, 8.0), (reported_at, 16.0)]


def test_after_a_pause_the_windows_start_anew(cfg) -> None:  # Präzisierung 12
    c = controller(cfg)
    run(c, cfg, 10, make=tesla_inputs, grid_obs=dip_at_5, **TESLA_CHARGING)
    c.idle(T0 + timedelta(seconds=10))
    found = c.step(tesla_inputs(cfg, 20, grid_obs=grid(l1=20.0), **TESLA_CHARGING))
    reasons = ("mode_now", "resume_after_outage")
    assert [x.command for x in found if x.command.actuator == "vehicle.tesla"] == [
        Command("vehicle.tesla", CURRENT, 16.0, reasons=reasons),
        Command("vehicle.tesla", CHARGING, True, reasons=reasons),
    ]


def test_back_under_control_keeps_the_windows(cfg) -> None:
    # Von 6 bis 9 ist der Tesla nicht steuerbar: Die Regelung vergisst ihre letzten Werte, aber
    # der niedrigste freie Wert gilt weiter bis 65.
    found = run(
        controller(cfg),
        cfg,
        67,
        make=tesla_inputs,
        grid_obs=dip_at_5,
        **{
            **TESLA_CHARGING,
            "controllable": lambda s: {"wallbox.evcs": True, "vehicle.tesla": not 6 <= s < 10},
        },
    )
    assert currents(found) == [(0, 16.0), (5, 8.0), (10, 8.0), (66, 16.0)]


def test_the_wish_counts_only_while_the_car_can_charge(cfg) -> None:
    # In Aus wünscht der Tesla nichts: Nach dem Wechsel auf Sofort lädt er gleich mit 16 A.
    found = run(
        controller(cfg),
        cfg,
        61,
        make=tesla_inputs,
        intent=lambda s: TESLA_NOW_2 if s >= 60 else TESLA_OFF,
    )
    assert currents(found) == [(60, 16.0)]


def test_a_wake_start_waits_for_a_known_plug_state(cfg) -> None:  # Präzisierung 4
    # Der Start bei 0 scheitert; von 1 bis 4 schläft der Tesla, und der Wall Connector meldet ihn
    # nicht (angesteckt unbekannt). Ab 5 meldet er ihn wieder: ein neuer Ansteckvorgang.
    def make(cfg, *, s: int, **kw):
        if s == 0:
            return tesla_inputs(cfg, s, **kw)
        seen = TWC_ON if s >= 5 else twc()
        return tesla_inputs(
            cfg, s, twc_obs=seen, tesla_obs=tesla(plugged=True, asleep=True), **ONLY_WAKEABLE, **kw
        )

    c = controller(cfg)
    found = steps(c, cfg, range(1), make=make, intent=TESLA_NOW)
    failed_start(c)
    found += steps(c, cfg, range(1, 7), make=make, intent=TESLA_NOW)
    reasons = ("mode_now",)
    assert of(found, "vehicle.tesla") == [
        (0, Command("vehicle.tesla", CURRENT, 16.0, reasons=reasons)),
        (0, Command("vehicle.tesla", CHARGING, True, reasons=reasons)),
        (5, WAKE),  # nicht von 1 bis 4
    ]


def test_a_wake_start_waits_until_the_limit_carries_the_minimum(cfg) -> None:  # Design 5.3
    # Der schlafende Tesla in Sofort: Bis 4 s ist auf L1 frei 33 − 30 = 3 A, weniger als 5 A. Er
    # bekommt den niedrigsten freien Wert der letzten 60 s: Der Weckstart folgt erst bei 65 s.
    found = run(
        controller(cfg),
        cfg,
        70,
        make=tesla_inputs,
        intent=TESLA_NOW,
        tesla_obs=tesla(plugged=True, asleep=True),
        grid_obs=lambda s: grid(l1=30.0) if s < 5 else grid(),
        **ONLY_WAKEABLE,
    )
    assert of(found, "vehicle.tesla") == [(65, WAKE)]


def test_a_blocked_wake_start_waits(cfg) -> None:
    found = run(
        controller(cfg),
        cfg,
        3,
        make=tesla_inputs,
        intent=TESLA_NOW,
        tesla_obs=tesla(plugged=True, asleep=True),
        blocked=lambda s: frozenset({("vehicle.tesla", CHARGING)}) if s == 0 else frozenset(),
        **ONLY_WAKEABLE,
    )
    assert of(found, "vehicle.tesla") == [(1, WAKE)]


def test_no_start_holds_back_the_wake_start(cfg) -> None:  # Plan 2c-2a, Präzisierung 13
    # Der schlafende Tesla in Sofort lädt nicht: In der Start-Sperre des 15-min-Wächters bleibt
    # der Weckstart aus; fällt sie bei 2 s weg, folgt er.
    blocked = PeakLimits(no_start=frozenset({"tesla"}))
    found = run(
        controller(cfg),
        cfg,
        3,
        make=tesla_inputs,
        intent=TESLA_NOW,
        tesla_obs=tesla(plugged=True, asleep=True),
        peak=lambda s: blocked if s < 2 else NO_LIMITS,
        **ONLY_WAKEABLE,
    )
    assert of(found, "vehicle.tesla") == [(2, WAKE)]


def test_overload_wakes_nothing(cfg) -> None:
    # Überlast ab 3 bis 60 s nach ihrem Ende bei 5; ab 5 will Sofort starten.
    found = run(
        controller(cfg),
        cfg,
        66,
        make=tesla_inputs,
        intent=lambda s: TESLA_NOW_2 if s >= 5 else TESLA_OFF,
        tesla_obs=tesla(plugged=True, asleep=True),
        grid_obs=lambda s: grid(l1=34.0) if s < 5 else grid(),
        **ONLY_WAKEABLE,
    )
    assert of(found, "vehicle.tesla") == [(65, WAKE)]


def test_a_woken_tesla_gets_its_current_once_awake(cfg) -> None:
    found = run(
        controller(cfg),
        cfg,
        3,
        make=tesla_inputs,
        intent=TESLA_NOW,
        tesla_obs=lambda s: tesla(plugged=True, asleep=s == 0),
        controllable=lambda s: {"wallbox.evcs": True, "vehicle.tesla": s > 0},
        wakeable={"vehicle.tesla": True},
    )
    # Der Weckstart hat „Laden an“ schon gegeben: Danach kommt nur der Strom.
    assert of(found, "vehicle.tesla") == [(0, WAKE), (1, MAXIMUM)]


@pytest.mark.parametrize(
    ("judgement", "counted"),
    [
        (Outcome.ACCEPTED, 1),
        (Outcome.CLAMPED, 1),
        (Outcome.UNCHANGED, 0),
        (Outcome.DEFERRED, 0),
        (Outcome.REJECTED, 0),
        ("pending", 0),
        ("not_sent", 1),
    ],
)
def test_the_quota_counts_each_wake_start_with_a_value(cfg, judgement, counted) -> None:
    # Präzisierung 12: jedes Urteil mit Wert, live wie im Dry-Run, auch wenn danach nicht gesendet
    c = controller(cfg)
    found = c.step(
        tesla_inputs(
            cfg, 0, intent=TESLA_NOW, tesla_obs=tesla(plugged=True, asleep=True), **ONLY_WAKEABLE
        )
    )
    wake = found[0].command
    assert wake == WAKE
    if judgement == "pending":
        c.note_verdict(wake, None)
    elif judgement == "not_sent":
        c.note_verdict(wake, Verdict(wake, Outcome.ACCEPTED, True, (), False), not_sent=True)
    else:
        accept(c, found, judgement)
    assert c.health()[0]["wakes_today"] == counted


def test_wakes_per_day_and_today_in_health(cfg) -> None:
    few = with_vehicle(cfg, "tesla", wake=WakeConfig(interval_s=0.0, per_day=2))
    c = ChargeController(few, HardLimits.from_config(few), started=T0)
    asleep = {"intent": TESLA_NOW, "tesla_obs": tesla(plugged=True, asleep=True), **ONLY_WAKEABLE}
    found = []
    for s in [*range(5), 86_400]:  # der letzte Schritt einen Tag später
        commands = c.step(tesla_inputs(few, s, **asleep))
        accept(c, commands)
        found.extend((s, x.command) for x in commands if x.command.wake)
        failed_start(c)
        if s == 4:
            assert c.health()[0]["wakes_today"] == 2
    assert found == [(0, WAKE), (1, WAKE), (86_400, WAKE)]
    assert [v["wakes_today"] for v in c.health()] == [1, 0]


@pytest.mark.parametrize("late", ["vehicle.tesla", "wallbox.evcs", "blocked"])
def test_tesla_at_the_evcs_gets_its_maximum_as_soon_as_it_can(cfg, late) -> None:
    # „sobald steuerbar“, nur, solange die EVCS geregelt wird, und nicht, solange er blockiert ist
    found = run(
        controller(cfg),
        cfg,
        10,
        make=tesla_inputs,
        intent=AT_THE_EVCS,
        evcs_obs=evcs(),
        controllable=lambda s: {"wallbox.evcs": True, "vehicle.tesla": True, late: s >= 5},
        blocked=lambda s: (
            frozenset({("vehicle.tesla", CURRENT)}) if late == "blocked" and s < 5 else frozenset()
        ),
    )
    assert of(found, "vehicle.tesla") == [(5, MAXIMUM)]


def test_the_maximum_at_the_evcs_is_done_with_a_value_or_unchanged(cfg) -> None:
    c = controller(cfg)
    first = c.step(tesla_inputs(cfg, 0, intent=AT_THE_EVCS, evcs_obs=evcs()))
    assert first[0].command == MAXIMUM
    assert first[0].inputs["actual_current_a"] == 16.0  # der Ist-Strom des Tesla
    accept(c, first, Outcome.DEFERRED)
    again = c.step(tesla_inputs(cfg, 1, intent=AT_THE_EVCS, evcs_obs=evcs()))
    assert [x.command for x in again] == [x.command for x in first]
    accept(c, again, Outcome.UNCHANGED)
    assert c.step(tesla_inputs(cfg, 2, intent=AT_THE_EVCS, evcs_obs=evcs())) == []


def test_tesla_at_the_evcs_gets_its_maximum_again_in_a_new_session(cfg) -> None:
    # Bei 5 und 6 meldet die EVCS kein Fahrzeug: Der Vorgang und mit ihm die Übersteuerung enden.
    # Version 2 übersteuert bei 7 erneut, im neuen Vorgang.
    again = AT_THE_EVCS.model_copy(update={"version": 2})
    found = run(
        controller(cfg),
        cfg,
        10,
        make=tesla_inputs,
        intent=lambda s: again if s >= 7 else AT_THE_EVCS,
        twc_obs=twc(),
        evcs_obs=lambda s: evcs(connected=False) if 5 <= s < 7 else evcs(),
    )
    assert of(found, "vehicle.tesla") == [(0, MAXIMUM), (7, MAXIMUM)]


def test_tesla_at_the_evcs_gets_its_maximum_again_after_a_pause(cfg) -> None:
    # Der sichere Zustand setzt den Tesla auf seinen Mindeststrom; danach braucht er den Höchststrom
    c = controller(cfg)
    run(c, cfg, 5, make=tesla_inputs, intent=AT_THE_EVCS, evcs_obs=evcs())
    c.idle(T0 + timedelta(seconds=5))
    found = c.step(tesla_inputs(cfg, 10, intent=AT_THE_EVCS, evcs_obs=evcs()))
    assert found[0].command == MAXIMUM


# der e-Golf an der EVCS, auf zwei Phasen mit 10 A; unklar ab UNCLEAR_FROM_S
UNCLEAR_EGOLF = {**CONTRADICTION, "present": at_evcs(10.0, True)}


def test_unclear_neither_starts_nor_stops_by_mode(cfg) -> None:  # Design 8
    # PV ohne Überschuss stoppte bei 300; ab 120 ist die Zuordnung unklar. Ab 400 liegt L1 über dem
    # Ziel an der Sicherung: Die Überlast setzt den Mindeststrom, ohne „Laden aus“ nach dem Modus.
    found = run(
        controller(cfg),
        cfg,
        404,
        make=tesla_inputs,
        grid_obs=lambda s: grid(l1=34.0, grid_w=2000.0) if s >= 400 else grid(grid_w=2000.0),
        **UNCLEAR_EGOLF,
    )
    assert [(t, c.value) for t, c in of(found, "wallbox.evcs") if c.kind == CHARGING] == [(0, True)]
    assert of(found, "wallbox.evcs")[-1] == (
        403,
        Command(
            "wallbox.evcs",
            CURRENT,
            6.0,
            urgent=True,
            reasons=("mode_pv", "assignment_unclear", "overload"),
        ),
    )


def test_unclear_still_stops_for_overload(cfg) -> None:  # Präzisierung 8
    start = UNCLEAR_FROM_S + 1  # die Überlast beginnt eine Sekunde nach „unklar“
    found = run(
        controller(cfg),
        cfg,
        start + 7,
        make=tesla_inputs,
        intent=EGOLF_NOW,
        grid_obs=lambda s: grid(l1=34.0) if s >= start else grid(),
        **UNCLEAR_EGOLF,
    )

    def unclear(kind, value, cause):
        reasons = ("mode_now", "assignment_unclear", cause)
        return Command("wallbox.evcs", kind, value, urgent=True, reasons=reasons)

    assert [(t, c) for t, c in of(found, "wallbox.evcs") if t >= UNCLEAR_FROM_S] == [
        (start, unclear(CURRENT, 9.0, "phase_limit")),  # frei 33 − (34 − 10)
        (start + 3, unclear(CURRENT, 6.0, "overload")),  # MINIMUM
        (start + 6, unclear(CHARGING, False, "overload")),  # EVCS_STOP
    ]


def test_only_the_first_start_after_a_pause_carries_the_reason(cfg) -> None:
    c = controller(cfg)
    run(c, cfg, 10, intent=EGOLF_NOW, evcs_obs=evcs(charging=True), present=at_evcs(16.0, True))
    c.idle(T0 + timedelta(seconds=10))
    stopped = {"evcs_obs": evcs(charging=False), "present": at_evcs(6.0, False)}
    first = c.step(inputs(cfg, s=130, intent=EGOLF_NOW, **stopped))
    accept(c, first, Outcome.DEFERRED)  # zurückgestellt: Der nächste Versuch trägt den Grund noch
    resumed = ("mode_now", "resume_after_outage")
    found = steps(
        c,
        cfg,
        range(131, 135),
        intent=lambda s: {133: egolf_in(ChargeMode.OFF, 2)}.get(s, egolf_in(ChargeMode.NOW, 3)),
        # bei 131 wartet „Laden an“: Der Strom allein erledigt die Startfolge nicht
        blocked=lambda s: frozenset({("wallbox.evcs", CHARGING)}) if s == 131 else frozenset(),
        **stopped,
    )
    assert [(t, x.command) for t, x in found] == [
        (131, Command("wallbox.evcs", CURRENT, 16.0, reasons=resumed)),
        (132, Command("wallbox.evcs", CHARGING, True, reasons=resumed)),
        (133, Command("wallbox.evcs", CHARGING, False, urgent=True, reasons=("mode_off",))),
        (134, Command("wallbox.evcs", CHARGING, True, reasons=("mode_now",))),
    ]


def test_overload_after_a_pause_starts_nothing_to_resume(cfg) -> None:
    # Die Startfolge nach der Pause wird bis 22 zurückgestellt; ab 23 herrscht Überlast: Der
    # Mindeststrom ist kein Start und trägt den Grund nicht.
    c = controller(cfg)
    run(c, cfg, 10, intent=EGOLF_NOW, evcs_obs=evcs(charging=True), present=at_evcs(16.0, True))
    c.idle(T0 + timedelta(seconds=10))
    over = {
        "intent": EGOLF_NOW,
        "evcs_obs": evcs(charging=False),
        "present": at_evcs(16.0, False),
        "grid_obs": grid(l1=34.0, l2=20.0),
    }
    for s in range(20, 23):
        accept(c, c.step(inputs(cfg, s=s, **over)), Outcome.DEFERRED)
    found = c.step(inputs(cfg, s=23, **over))
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 6.0, urgent=True, reasons=("mode_now", "overload"))
    ]


def test_a_wake_start_after_a_pause_carries_both_reasons(cfg) -> None:
    c = controller(cfg)
    run(c, cfg, 10, make=tesla_inputs, **TESLA_CHARGING)  # wach und steuerbar
    c.idle(T0 + timedelta(seconds=10))
    found = c.step(
        tesla_inputs(
            cfg, 20, intent=TESLA_NOW, tesla_obs=tesla(plugged=True, asleep=True), **ONLY_WAKEABLE
        )
    )
    assert [x.command for x in found if x.command.actuator == "vehicle.tesla"] == [
        Command(
            "vehicle.tesla",
            CHARGING,
            True,
            reasons=("mode_now", "resume_after_outage", "wake"),
            wake=True,
        )
    ]


def test_pauses_before_the_first_step_are_no_outage(cfg) -> None:
    # Die Laufzeit ruft ``idle`` schon in START und OBSERVE: Die erste Regelung nach dem Start des
    # core nimmt nichts wieder auf.
    c = controller(cfg)
    for s in range(3):
        c.idle(T0 + timedelta(seconds=s))
    found = c.step(inputs(cfg, s=3, intent=EGOLF_NOW))
    assert [x.command for x in found] == [
        Command("wallbox.evcs", CURRENT, 16.0, reasons=("mode_now",)),
        Command("wallbox.evcs", CHARGING, True, reasons=("mode_now",)),
    ]


def test_a_judged_wake_start_is_not_repeated(cfg) -> None:
    eager = with_vehicle(cfg, "tesla", wake=WakeConfig(interval_s=0.0, per_day=6))
    asleep = {"intent": TESLA_NOW, "tesla_obs": tesla(plugged=True, asleep=True), **ONLY_WAKEABLE}
    found = run(
        ChargeController(eager, HardLimits.from_config(eager), started=T0),
        eager,
        5,
        make=tesla_inputs,
        **asleep,
    )
    assert of(found, "vehicle.tesla") == [(0, WAKE)]  # „Laden an“ gilt als gegeben


def test_pv_wakes_the_tesla_when_the_surplus_suffices(cfg) -> None:  # Design 7.1
    # 3900 W reichen dem Tesla für 5 A: Nach 120 s will PV starten und weckt ihn.
    found = run(
        controller(cfg),
        cfg,
        121,
        make=tesla_inputs,
        tesla_obs=tesla(plugged=True, asleep=True),
        **ONLY_WAKEABLE,
    )
    reasons = ("mode_pv", "pv_surplus", "wake")
    assert of(found, "vehicle.tesla") == [
        (120, Command("vehicle.tesla", CHARGING, True, reasons=reasons, wake=True))
    ]


def test_tesla_moved_to_another_evcs_gets_its_maximum_there(cfg) -> None:
    # Eine zweite EVCS: Version 2 übersteuert den Tesla dorthin, während beide ein Fahrzeug melden.
    second = cfg.wallboxes["evcs"].model_copy(update={"host": "evcs2.lan"})
    two = cfg.model_copy(update={"wallboxes": {**cfg.wallboxes, "evcs2": second}})
    moved = Intent(
        version=2,
        control=True,
        vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW, wallbox="evcs2")},
    )

    def make(cfg, *, s: int):
        base = tesla_inputs(
            cfg,
            s,
            intent=moved if s >= 5 else AT_THE_EVCS,
            evcs_obs=evcs(),
            controllable={"wallbox.evcs": True, "wallbox.evcs2": True, "vehicle.tesla": True},
        )
        return replace(base, wallboxes={**base.wallboxes, "evcs2": evcs()})

    found = run(ChargeController(two, HardLimits.from_config(two), started=T0), two, 7, make=make)
    assert of(found, "vehicle.tesla") == [(0, MAXIMUM), (5, MAXIMUM)]


def test_only_an_override_puts_the_tesla_at_the_evcs(cfg) -> None:  # Design 8
    # Steht die EVCS als Standard-Wallbox des Tesla in der Konfiguration, regelt sie nur die EVCS.
    at_home = with_vehicle(cfg, "tesla", default_wallbox="evcs")
    found = run(
        ChargeController(at_home, HardLimits.from_config(at_home), started=T0),
        at_home,
        3,
        make=tesla_inputs,
        intent=TESLA_NOW,
        evcs_obs=evcs(),
    )
    assert of(found, "vehicle.tesla") == []
    assert [c.kind for _, c in of(found, "wallbox.evcs")] == [CURRENT, CHARGING]


def test_an_override_to_the_wall_connector_regulates_the_tesla_as_usual(cfg) -> None:
    to_twc = Intent(
        version=1,
        control=True,
        vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW, wallbox="twc")},
    )
    found = controller(cfg).step(tesla_inputs(cfg, 0, intent=to_twc, grid_obs=grid(l1=22.0)))
    reasons = ("mode_now", "phase_limit")  # frei 33 − 22 = 11, unter dem Ist-Strom von 16 A
    assert [x.command for x in found if x.command.actuator == "vehicle.tesla"] == [
        Command("vehicle.tesla", CURRENT, 11.0, urgent=True, reasons=reasons),
        Command("vehicle.tesla", CHARGING, True, reasons=reasons),
    ]


# Nachbesserung: Der erneute Befehl nach „nicht übernommen“ ist normal (Design 7.2)


def not_taken(c: ChargeController, amps: float, reported: float) -> None:
    c.note_result(
        WriteResult(
            "vehicle.tesla",
            CURRENT,
            amps,
            DecisionResult.ERROR,
            f"Fahrzeug übernahm {amps:.0f} A nicht (Tessie meldet {reported:.0f} A).",
            not_taken=True,
        )
    )


def test_a_lowering_at_the_fuse_is_retried_as_a_normal_command(cfg) -> None:
    # Sofort: frei 33 − (41 − 16) = 8; die Senkung unter den Ist-Strom ist dringend. Übernimmt das
    # Fahrzeug sie nicht, wartet der erneute Befehl wie jeder normale auf die 90-s-Regel.
    c = controller(cfg)
    lowered = {**TESLA_CHARGING, "grid_obs": grid(l1=41.0)}
    first = c.step(tesla_inputs(cfg, 0, **lowered))
    accept(c, first)
    assert first[0].command == Command(
        "vehicle.tesla", CURRENT, 8.0, urgent=True, reasons=("mode_now", "phase_limit")
    )
    not_taken(c, 8.0, 16.0)
    again = c.step(tesla_inputs(cfg, 1, **lowered))
    assert [x.command for x in again] == [
        Command(
            "vehicle.tesla", CURRENT, 8.0, reasons=("mode_now", "phase_limit", "not_taken_retry")
        )
    ]


def test_a_not_taken_overload_minimum_is_retried_normally_and_the_stop_stays_urgent(cfg) -> None:
    # L1 liegt über der Sicherung: Mindeststrom bei 3 (MINIMUM), nicht übernommen. Der erneute
    # Befehl bei 4 ist normal; die Überlast eskaliert weiter, bis „Laden aus“ bei 10 (ALL_STOP).
    c = controller(cfg)
    over = {**TESLA_CHARGING, "grid_obs": grid(l1=36.0)}
    found = steps(c, cfg, range(4), make=tesla_inputs, **over)
    not_taken(c, 5.0, 13.0)
    found += steps(c, cfg, range(4, 11), make=tesla_inputs, **over)
    reasons = ("mode_now", "overload")
    assert [(t, x) for t, x in of(found, "vehicle.tesla") if "overload" in x.reasons] == [
        (3, Command("vehicle.tesla", CURRENT, 5.0, urgent=True, reasons=reasons)),
        (4, Command("vehicle.tesla", CURRENT, 5.0, reasons=(*reasons, "not_taken_retry"))),
        (6, Command("vehicle.tesla", CURRENT, 5.0, urgent=True, reasons=reasons)),  # EVCS_STOP
        (10, Command("vehicle.tesla", CHARGING, False, urgent=True, reasons=reasons)),
    ]


def deferred_steps(c: ChargeController, cfg, seconds: range, **kw) -> list:
    """Wie ``steps`` mit ``tesla_inputs``, aber die Safety-Schicht stellt jeden Befehl zurück."""
    found = []
    for s in seconds:
        commands = c.step(tesla_inputs(cfg, s, **kw))
        accept(c, commands, Outcome.DEFERRED)
        found.extend((s, x) for x in commands)
    return found


def test_a_rising_overload_level_sends_its_minimum_fresh_while_a_retry_waits(cfg) -> None:
    # Präzisierung 11: Der Mindeststrom bei 3 (MINIMUM) wird nicht übernommen. Sein Neuversuch ist
    # normal und wartet auf die 90-s-Regel (zurückgestellt bei 4 und 5). Steigt die Stufe bei 6 auf
    # EVCS_STOP, entsteht der Befehl der höheren Stufe frisch und dringend, ohne „not_taken_retry“,
    # und nur einmal. Wird auch er nicht übernommen, ist der Neuversuch auf dieser Stufe wieder
    # normal (8 und 9).
    c = controller(cfg)
    over = {**TESLA_CHARGING, "grid_obs": grid(l1=36.0)}
    found = steps(c, cfg, range(4), make=tesla_inputs, **over)
    not_taken(c, 5.0, 13.0)
    found += deferred_steps(c, cfg, range(4, 6), **over)
    found += steps(c, cfg, range(6, 8), make=tesla_inputs, **over)
    not_taken(c, 5.0, 13.0)
    found += deferred_steps(c, cfg, range(8, 10), **over)
    reasons = ("mode_now", "overload")
    fresh = Command("vehicle.tesla", CURRENT, 5.0, urgent=True, reasons=reasons)
    retry = Command("vehicle.tesla", CURRENT, 5.0, reasons=(*reasons, "not_taken_retry"))
    assert [(t, x) for t, x in of(found, "vehicle.tesla") if "overload" in x.reasons] == [
        (3, fresh),
        (4, retry),
        (5, retry),
        (6, fresh),  # EVCS_STOP: die Reaktion der höheren Stufe, kein Neuversuch
        (8, retry),
        (9, retry),
    ]


# Plan 2c-2b: der erkannte Tesla an der EVCS und der Grund „assignment_detected“ (Präzisierungen 7
# und 8)

DETECTED_AT_THE_EVCS = {  # mit tesla_inputs: zu Hause, angesteckt
    "twc_obs": twc(connected=False),
    "evcs_obs": evcs(),
}
TESLA_NOW_EGOLF_OFF = Intent(
    version=1,
    control=True,
    vehicles={
        "tesla": VehicleIntent(mode=ChargeMode.NOW),
        "egolf": VehicleIntent(mode=ChargeMode.OFF),
    },
)
DETECTED = ("mode_now", "assignment_detected")


def test_a_detected_tesla_at_the_evcs_gets_its_maximum_once(cfg) -> None:  # Design 7.4
    found = run(
        controller(cfg),
        cfg,
        300,
        make=tesla_inputs,
        intent=TESLA_NOW_EGOLF_OFF,
        **DETECTED_AT_THE_EVCS,
    )
    assert [c.command for t, c in found if t == 60] == [
        Command("vehicle.tesla", CURRENT, 16.0, reasons=DETECTED),
        Command("wallbox.evcs", CURRENT, 16.0, reasons=DETECTED),
        Command("wallbox.evcs", CHARGING, True, reasons=DETECTED),
    ]
    assert [t for t, c in found if c.command.actuator == "vehicle.tesla"] == [60]


def test_new_detection_marks_only_the_first_commands(cfg) -> None:  # Review Focus 4
    found = run(
        controller(cfg),
        cfg,
        300,
        make=tesla_inputs,
        intent=TESLA_NOW_EGOLF_OFF,
        **DETECTED_AT_THE_EVCS,
    )
    assert {t for t, c in found if "assignment_detected" in c.command.reasons} == {60}


def test_handover_from_the_assumed_to_the_detected_car_starts_once(cfg) -> None:  # Review Focus 4
    # der e-Golf in Sofort angenommen an der EVCS, ab 60 s der Tesla erkannt (Sofort)
    both_now = Intent(
        version=1,
        control=True,
        vehicles={
            "tesla": VehicleIntent(mode=ChargeMode.NOW),
            "egolf": VehicleIntent(mode=ChargeMode.NOW),
        },
    )
    found = run(
        controller(cfg), cfg, 300, make=tesla_inputs, intent=both_now, **DETECTED_AT_THE_EVCS
    )
    starts = [
        t
        for t, c in found
        if c.command.actuator == "wallbox.evcs"
        and c.command.kind is CHARGING
        and c.command.value is True
    ]
    assert starts == [0, 60]  # der e-Golf bei 0, nach der Übergabe einmal der Tesla
    assert [t for t, c in found if c.command.actuator == "vehicle.tesla"] == [60]


# Ergänzungen: Ankunft an der EVCS und wie lange der Grund gilt


def test_an_override_at_the_same_evcs_is_no_new_arrival(cfg) -> None:  # Präzisierung 7
    # Erkannt bei 60 s, ab 100 s an dieselbe EVCS übersteuert: Nur die Art wechselt, der
    # Höchststrom kommt nicht noch einmal.
    pinned = Intent(
        version=2,
        control=True,
        vehicles={
            "tesla": VehicleIntent(mode=ChargeMode.NOW, wallbox="evcs"),
            "egolf": VehicleIntent(mode=ChargeMode.OFF),
        },
    )
    c = controller(cfg)
    found = run(
        c,
        cfg,
        110,
        make=tesla_inputs,
        intent=lambda s: pinned if s >= 100 else TESLA_NOW_EGOLF_OFF,
        **DETECTED_AT_THE_EVCS,
    )
    assert of(found, "vehicle.tesla") == [
        (60, Command("vehicle.tesla", CURRENT, 16.0, reasons=DETECTED))
    ]
    assert (c.health()[0]["wallbox"], c.health()[0]["assignment"]) == ("evcs", "override")


def test_the_reason_waits_for_the_first_cycle_with_commands(cfg) -> None:  # Präzisierung 8
    # Bei der Erkennung (60 s) ist nichts steuerbar: Erst die Befehle bei 61 s tragen den Grund.
    found = run(
        controller(cfg),
        cfg,
        62,
        make=tesla_inputs,
        intent=TESLA_NOW_EGOLF_OFF,
        controllable=lambda s: {"wallbox.evcs": s != 60, "vehicle.tesla": s != 60},
        **DETECTED_AT_THE_EVCS,
    )
    assert [(t, c.command) for t, c in found if t >= 60] == [
        (61, Command("vehicle.tesla", CURRENT, 16.0, reasons=DETECTED)),
        (61, Command("wallbox.evcs", CURRENT, 16.0, reasons=DETECTED)),
        (61, Command("wallbox.evcs", CHARGING, True, reasons=DETECTED)),
    ]


def test_later_commands_of_a_detected_car_carry_no_reason(cfg) -> None:  # Präzisierung 8
    # Erkannt bei 60 s: Health nennt den Grund als letzten des Tesla. Ab 100 s liegt L1 bei 25 A:
    # Der Strom der EVCS sinkt auf 33 − 25 = 8 A, ohne den Grund.
    c = controller(cfg)
    detected = {"make": tesla_inputs, "intent": TESLA_NOW_EGOLF_OFF, **DETECTED_AT_THE_EVCS}
    run(c, cfg, 61, **detected)
    assert c.health()[0]["reason"] == "assignment_detected"
    loaded = steps(
        c, cfg, range(61, 101), grid_obs=lambda s: grid(l1=25.0) if s >= 100 else grid(), **detected
    )
    assert [(t, x.command) for t, x in loaded] == [
        (100, Command("wallbox.evcs", CURRENT, 8.0, reasons=("mode_now", "phase_limit")))
    ]


def test_a_departing_tesla_gets_no_command_from_a_stale_tessie_value(cfg) -> None:  # Gesamtreview
    # Der e-Golf steckt an der EVCS und lädt nicht (PV-Überschuss, abends ohne PV), der Tesla in
    # Sofort steckt am Wall Connector. Bei 600 s steckt er ab und fährt; Tessie fragt alle 30 s ab
    # und meldet ihn noch 90 s angesteckt. Der Tesla wird an der EVCS nie erkannt: Er bekommt dort
    # kein max_a, und die EVCS lädt nicht in seinem Modus.
    left_s = 600

    def tessie(s):
        polled = s - s % 30
        return tesla(plugged=30 <= polled < left_s + 90, plugged_at=T0 + timedelta(seconds=polled))

    intent = Intent(
        version=1,
        control=True,
        vehicles={
            "tesla": VehicleIntent(mode=ChargeMode.NOW),
            "egolf": VehicleIntent(mode=ChargeMode.PV),
        },
    )
    found = run(
        controller(cfg),
        cfg,
        900,
        make=tesla_inputs,
        intent=intent,
        grid_obs=grid(grid_w=500.0),
        tesla_obs=tessie,
        twc_obs=lambda s: twc(connected=2 <= s < left_s),
        evcs_obs=lambda s: evcs(connected=s >= 1),
        homes={"egolf": True},
    )
    assert [x for t, x in found if "assignment_detected" in x.command.reasons] == []
    # nach der Abfahrt nur „Laden aus“, sobald Tessie ihn abgesteckt meldet (wie in 2c-1)
    assert [
        (t, x.command) for t, x in found if t >= left_s and x.command.actuator == "vehicle.tesla"
    ] == [(690, Command("vehicle.tesla", CHARGING, False, reasons=("mode_now",)))]
    assert [
        (t, x.command)
        for t, x in found
        if x.command.actuator == "wallbox.evcs" and "mode_now" in x.command.reasons
    ] == []


# Selbststart (Plan 2d, Präzisierung 10)


def test_against_mode_needs_120_s_without_interruption() -> None:
    against = AgainstMode(120, 600)
    assert not against.due("tesla", T0, True)
    assert not against.due("tesla", T0 + timedelta(seconds=119), True)
    assert against.due("tesla", T0 + timedelta(seconds=120), True)


def test_against_mode_retries_at_most_every_10_min() -> None:
    against = AgainstMode(120, 600)
    at = lambda s: T0 + timedelta(seconds=s)  # noqa: E731
    due = [s for s in range(0, 1400) if against.due("tesla", at(s), True)]
    assert due == [120, 720, 1320]


def test_interruption_restarts_the_120_s() -> None:
    against = AgainstMode(120, 600)
    at = lambda s: T0 + timedelta(seconds=s)  # noqa: E731
    assert not against.due("tesla", at(0), True)
    assert not against.due("tesla", at(60), False)
    assert [s for s in range(61, 200) if against.due("tesla", at(s), True)] == [181]


def test_against_mode_counts_each_vehicle_alone() -> None:
    against = AgainstMode(120, 600)
    against.due("tesla", T0, True)
    assert not against.due("egolf", T0 + timedelta(seconds=120), True)
    assert against.due("tesla", T0 + timedelta(seconds=120), True)


def test_clear_starts_a_new_process() -> None:
    against = AgainstMode(120, 600)
    at = lambda s: T0 + timedelta(seconds=s)  # noqa: E731
    assert [s for s in range(0, 121) if against.due("tesla", at(s), True)] == [120]
    against.clear("tesla")  # kein Neuversuch-Abstand mehr, die 120 s beginnen neu
    assert not against.due("tesla", at(121), True)
    assert not against.due("tesla", at(240), True)
    assert against.due("tesla", at(241), True)


SELF_STARTING = {
    "twc_obs": TWC_16_A,
    "tesla_obs": tesla(plugged=True, charging=True, actual_current_a=16.0),
    "present": at_tesla(16.0, True),
}
SELF_START_OFF = Command(
    "vehicle.tesla", CHARGING, False, urgent=True, reasons=("mode_off", "self_start")
)


def self_start_run(cfg, intent, seconds, *, quiet_until=60, **kw):
    """Der Tesla ist angesteckt und gestoppt (bestätigt); ab ``quiet_until`` lädt er selbst.

    Tessie meldet ihn bis dahin nicht ladend; der Wall Connector misst dann 0 A.
    """
    quiet = {"tesla_obs": tesla(plugged=True), "twc_obs": TWC_ON, "present": at_tesla(0.0, False)}

    def chosen(key):
        return lambda s: quiet[key] if s < quiet_until else SELF_STARTING[key]

    return run(
        controller(cfg),
        cfg,
        seconds,
        make=tesla_inputs,
        intent=intent,
        **{"grid_obs": grid(grid_w=-3000.0), **{key: chosen(key) for key in quiet}, **kw},
    )


def test_self_starting_tesla_in_off_gets_charging_off_again(cfg) -> None:
    found = self_start_run(cfg, TESLA_OFF, 300)
    stops = [(t, c) for t, c in of(found, "vehicle.tesla") if c.kind == CHARGING]
    assert [t for t, _ in stops if t >= 60] == [180]
    assert stops[-1][1] == SELF_START_OFF


def test_self_start_in_pv_without_surplus(cfg) -> None:
    intent = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PV)})
    # Der Netzbezug deckt die 11 kW des ladenden Tesla: kein Überschuss
    found = self_start_run(cfg, intent, 300, grid_obs=grid(grid_w=12000.0))
    stops = [(t, c) for t, c in of(found, "vehicle.tesla") if c.kind == CHARGING and t >= 60]
    assert stops == [
        (180, Command("vehicle.tesla", CHARGING, False, reasons=("mode_pv", "self_start")))
    ]


def test_self_start_is_retried_at_most_every_10_min(cfg) -> None:
    found = self_start_run(cfg, TESLA_OFF, 1000)
    stops = [t for t, c in of(found, "vehicle.tesla") if c.kind == CHARGING and t >= 60]
    assert stops == [180, 780]


def test_no_self_start_below_one_ampere(cfg) -> None:
    found = self_start_run(
        cfg,
        TESLA_OFF,
        400,
        tesla_obs=tesla(plugged=True, charging=True, actual_current_a=0.5),
    )
    assert [c for _, c in of(found, "vehicle.tesla") if "self_start" in c.reasons] == []


def test_no_correction_while_the_assignment_is_unclear(cfg) -> None:
    intent = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PV)})
    found = run(
        controller(cfg),
        cfg,
        UNCLEAR_FROM_S + 400,
        make=tesla_inputs,
        intent=intent,
        **{**CONTRADICTION, "tesla_obs": tesla(plugged=True, charging=True, actual_current_a=16.0)},
    )
    assert [c for _, c in of(found, "vehicle.tesla") if "self_start" in c.reasons] == []


def test_a_tesla_charging_as_its_mode_wants_gets_no_self_start(cfg) -> None:
    found = self_start_run(cfg, TESLA_NOW, 400)
    assert [c for _, c in of(found, "vehicle.tesla") if "self_start" in c.reasons] == []


def test_a_pause_starts_the_self_start_times_anew(cfg) -> None:
    # Stopp bei 180; die Pause bei 190 löscht die Zeiten samt dem Abstand zum Neuversuch: Der
    # nächste Stopp mit Grund folgt 120 s nach der Pause, nicht erst nach 600 s.
    c = controller(cfg)
    kw = {
        "make": tesla_inputs,
        "intent": TESLA_OFF,
        "grid_obs": grid(grid_w=-3000.0),
        **SELF_STARTING,
    }
    before = steps(c, cfg, range(60, 191), **kw)
    c.idle(T0 + timedelta(seconds=191))
    after = steps(c, cfg, range(200, 400), **kw)
    tagged = [t for t, x in before + after if "self_start" in x.command.reasons]
    assert tagged == [180, 320]


def test_an_unregulated_stretch_interrupts_the_self_start_time(cfg) -> None:
    # Von 150 bis 159 ist der Tesla nicht steuerbar, also nicht geregelt: Die 120 s beginnen bei 160
    found = self_start_run(
        cfg,
        TESLA_OFF,
        400,
        controllable=lambda s: {"wallbox.evcs": True, "vehicle.tesla": not 150 <= s < 160},
    )
    tagged = [t for t, c in of(found, "vehicle.tesla") if "self_start" in c.reasons]
    assert tagged == [280]


def test_a_self_start_correction_keeps_the_current_and_its_retry_marker(cfg) -> None:
    # Bei 0 sinkt die Sicherung den Tesla in Sofort auf 8 A; er übernimmt das nicht. Ab 1 ist der
    # Modus Aus, der Tesla lädt weiter. Die Korrektur bei 121 vergisst nur „Laden an/aus“: Der
    # Merker des Stroms bleibt, sein nächster Befehl bleibt ein normaler (Design 7.2).
    c = controller(cfg)
    accept(c, c.step(tesla_inputs(cfg, 0, **TESLA_CHARGING, grid_obs=grid(l1=41.0))))
    not_taken(c, 8.0, 16.0)
    off = {**TESLA_CHARGING, **SELF_STARTING, "intent": TESLA_OFF}
    found = steps(c, cfg, range(1, 200), make=tesla_inputs, **off)
    tagged = [(t, x.command) for t, x in found if "self_start" in x.command.reasons]
    assert tagged == [(121, SELF_START_OFF)]
    assert c._last.retry(("vehicle.tesla", CURRENT))


@pytest.mark.parametrize("pending", ["blocked", "wake_only"])
def test_the_self_start_waits_for_a_cycle_that_can_send_it(cfg, pending) -> None:
    # Von 170 bis 199 kann der Stopp nicht hinaus: „Laden an/aus“ ist blockiert, oder der Tesla ist
    # nur weckbar. Die Zeit läuft weiter, der Neuversuch bleibt unverbraucht: Der Stopp mit Grund
    # geht im ersten Zyklus hinaus, der ihn senden kann (200), nicht ohne Grund später.
    held = lambda s: 170 <= s < 200  # noqa: E731
    kw = (
        {"blocked": lambda s: frozenset({("vehicle.tesla", CHARGING)}) if held(s) else frozenset()}
        if pending == "blocked"
        else {
            "controllable": lambda s: {"wallbox.evcs": True, "vehicle.tesla": not held(s)},
            "wakeable": {"vehicle.tesla": True},
        }
    )
    found = self_start_run(cfg, TESLA_OFF, 400, **kw)
    stops = [(t, c) for t, c in of(found, "vehicle.tesla") if c.kind == CHARGING and t >= 60]
    assert stops == [(200, SELF_START_OFF)]


def test_the_self_start_ignores_an_unclear_assignment(cfg) -> None:
    # Bei unklarer Zuordnung zählt das Laden gegen den Modus nicht und unterbricht die Zeit: Die
    # 120 s beginnen erst mit der klaren Zuordnung (241), der Neuversuch ist nicht verbraucht.
    c = controller(cfg)
    decision = ModeDecision(charge=False, reason=None)

    def due(s: int, kind: AssignmentKind) -> bool:
        state = VehicleState("twc", kind, True, True, False, False, None, False)
        inp = tesla_inputs(cfg, s, **SELF_STARTING)
        return c._self_start(inp.now, "tesla", "vehicle.tesla", state, decision, inp)

    unclear, clear = AssignmentKind.UNCLEAR, AssignmentKind.DEFAULT
    assert [due(s, unclear) for s in (0, 119, 120, 240)] == [False] * 4
    assert [due(s, clear) for s in (241, 360, 361)] == [False, False, True]


EVCS_OFF = Intent(
    version=1,
    control=True,
    vehicles={"tesla": VehicleIntent(mode=ChargeMode.OFF, wallbox="evcs")},
)


def test_a_self_starting_tesla_at_the_evcs_gets_the_stop_at_the_evcs(cfg) -> None:
    # Geregelt wird die EVCS: Das erneute „Laden aus“ geht an den Aktor des Ladepunkts
    evcs_charging = evcs(charging=True, phase_a=dict.fromkeys(PHASES, 16.0), power_w=11040.0)
    started = lambda s: s >= 60  # noqa: E731
    found = run(
        controller(cfg),
        cfg,
        300,
        make=tesla_inputs,
        intent=EVCS_OFF,
        twc_obs=twc(),
        evcs_obs=lambda s: evcs_charging if started(s) else evcs(),
        tesla_obs=lambda s: (
            tesla(plugged=True, charging=True, actual_current_a=16.0)
            if started(s)
            else tesla(plugged=True)
        ),
        present=lambda s: at_evcs(16.0, True) if started(s) else at_evcs(0.0, False),
    )
    tagged = [(t, x.command) for t, x in found if "self_start" in x.command.reasons]
    assert tagged == [
        (
            180,
            Command(
                "wallbox.evcs", CHARGING, False, urgent=True, reasons=("mode_off", "self_start")
            ),
        )
    ]


# --- Ladelimit (Plan 4b, Entscheidung 11, Präzisierung 9) -----------------------------------------

LIMIT = CommandKind.CHARGE_LIMIT


def tesla_planned(target: int = 80) -> Intent:
    """Der Tesla in „Geplant“ mit dem Ziel ``target``; Abfahrt 18:00 (T0 ist 12:00)."""
    entry = VehicleIntent(mode=ChargeMode.PLANNED, target_soc_pct=target, departure="18:00")
    return Intent(version=1, control=True, vehicles={"tesla": entry})


def synced(cfg):
    return with_vehicle(cfg, "tesla", charge_limit_sync=True)


def limit_at(pct: float | None, **kw):
    """Der Tesla angesteckt, wach und zu Hause, mit dem Ladelimit ``pct`` im Auto."""
    return tesla(plugged=True, charge_limit_pct=pct, **kw)


def limits_of(found) -> list:
    """Die Ladelimit-Befehle unter ``found`` (Befehle der Regelung)."""
    return [x.command for x in found if x.command.kind == LIMIT]


def set_limit(value: float) -> Command:
    return Command("vehicle.tesla", LIMIT, value, reasons=("mode_planned", "charge_limit"))


def limit_step(c, cfg, s: int, *, target: int = 80, pct: float | None = 70.0, judge=True, **kw):
    """Ein Schritt mit dem Tesla in „Geplant“; die Befehle bekommen ein Dry-Run-Urteil."""
    kw.setdefault("tesla_obs", limit_at(pct))
    found = c.step(tesla_inputs(cfg, s, intent=tesla_planned(target), **kw))
    if judge:
        accept(c, found)
    return limits_of(found)


def test_charge_limit_once_per_session_and_target(cfg) -> None:  # Review Focus 3
    sync = synced(cfg)
    c = controller(sync)
    assert limit_step(c, sync, 0) == [set_limit(80.0)]
    # gleicher Ansteckvorgang und gleiches Ziel: kein zweiter, auch wenn der Cache noch 70 zeigt
    assert limit_step(c, sync, 10) == []
    assert limit_step(c, sync, 20, pct=60.0) == []  # in der Tesla-App geändert: bleibt
    assert limit_step(c, sync, 30, target=90) == [set_limit(90.0)]
    assert limit_step(c, sync, 40, target=40) == [set_limit(50.0)]  # nie unter 50 %
    assert limit_step(c, sync, 50, target=40) == []
    # Abstecken und wieder anstecken: ein neuer Vorgang, das Limit geht noch einmal hinaus
    unplugged = tesla(charge_limit_pct=70.0)
    assert (
        limit_step(c, sync, 60, target=40, twc_obs=twc(connected=False), tesla_obs=unplugged) == []
    )
    assert limit_step(c, sync, 70, target=40) == [set_limit(50.0)]
    # Sync aus: nie
    plain = controller(cfg)
    assert [limit_step(plain, cfg, s, target=t) for s, t in ((0, 80), (10, 90), (20, 40))] == [
        [],
        [],
        [],
    ]


def test_limit_change_in_the_app_is_not_foreign() -> None:
    assert CommandKind.CHARGE_LIMIT not in WATCHED_KINDS


def test_charge_limit_goes_first_with_the_facts_of_the_car(cfg) -> None:
    sync = synced(cfg)
    found = controller(sync).step(
        tesla_inputs(sync, 0, intent=tesla_planned(), tesla_obs=limit_at(70.0))
    )
    assert found[0].command == set_limit(80.0)
    assert set(found[0].inputs) == {"surplus_w", "limit_a", "actual_current_a", "actual_charging"}


def test_charge_limit_needs_an_awake_planned_plugged_car_with_a_known_limit(cfg) -> None:
    sync = synced(cfg)
    # schläft (nur weckbar): kein Limit, und es wird dafür nicht geweckt
    assert limit_step(controller(sync), sync, 0, **ONLY_WAKEABLE) == []
    # das Limit im Auto ist unbekannt
    assert limit_step(controller(sync), sync, 0, pct=None) == []
    # Tessie sieht das Auto nicht angesteckt
    assert limit_step(controller(sync), sync, 0, tesla_obs=tesla(charge_limit_pct=70.0)) == []
    # nicht in „Geplant“: Sofort, und Boost geht vor
    now = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW)})
    found = controller(sync).step(tesla_inputs(sync, 0, intent=now, tesla_obs=limit_at(70.0)))
    assert limits_of(found) == []
    boost = VehicleIntent(
        mode=ChargeMode.PLANNED, boost_until=T0 + timedelta(hours=1), target_soc_pct=80
    )
    boosted = Intent(version=1, control=True, vehicles={"tesla": boost})
    c = controller(sync)
    assert limits_of(c.step(tesla_inputs(sync, 0, intent=boosted, tesla_obs=limit_at(70.0)))) == []
    assert c.health()[0]["mode"] == "boost"
    # blockiert (offener Schreibvorgang): wartet, danach geht es hinaus
    c = controller(sync)
    assert limit_step(c, sync, 0, blocked=frozenset({("vehicle.tesla", LIMIT)})) == []
    assert limit_step(c, sync, 10) == [set_limit(80.0)]


def test_a_limit_the_car_already_has_is_done(cfg) -> None:
    sync = synced(cfg)
    c = controller(sync)
    assert limit_step(c, sync, 0, pct=80.0) == []
    assert limit_step(c, sync, 10, pct=60.0) == []  # danach in der App geändert: bleibt so
    assert limit_step(c, sync, 20, target=90, pct=60.0) == [set_limit(90.0)]


def test_a_limit_without_a_value_is_offered_again(cfg) -> None:
    sync = synced(cfg)
    c = controller(sync)
    for outcome in (Outcome.DEFERRED, Outcome.REJECTED):
        found = c.step(tesla_inputs(sync, 0, intent=tesla_planned(), tesla_obs=limit_at(70.0)))
        (command,) = limits_of(found)
        accept(c, found, outcome)
        assert command == set_limit(80.0)
    assert limit_step(c, sync, 10) == [set_limit(80.0)]
    assert limit_step(c, sync, 20) == []


def test_a_failed_or_not_taken_limit_is_requested_again(cfg) -> None:
    sync = synced(cfg)
    c = controller(sync)
    assert limit_step(c, sync, 0) == [set_limit(80.0)]
    c.note_result(WriteResult("vehicle.tesla", LIMIT, 80.0, DecisionResult.TIMEOUT, "x"))
    assert limit_step(c, sync, 10) == [set_limit(80.0)]
    text = "Fahrzeug übernahm das Ladelimit 80 % nicht (Tessie meldet 70 %)."
    c.note_result(
        WriteResult("vehicle.tesla", LIMIT, 80.0, DecisionResult.ERROR, text, not_taken=True)
    )
    retry = Command(
        "vehicle.tesla", LIMIT, 80.0, reasons=("mode_planned", "charge_limit", "not_taken_retry")
    )
    assert limit_step(c, sync, 20) == [retry]
    # Ein bestätigtes Ergebnis und eines mit anderem Wert ändern nichts
    c.note_result(WriteResult("vehicle.tesla", LIMIT, 80.0, DecisionResult.OK, ""))
    c.note_result(WriteResult("vehicle.tesla", LIMIT, 90.0, DecisionResult.ERROR, "x"))
    assert limit_step(c, sync, 30) == []


def test_a_pause_keeps_the_limit_but_a_change_of_mode_sends_it_again(cfg) -> None:
    sync = synced(cfg)
    c = controller(sync)
    assert limit_step(c, sync, 0) == [set_limit(80.0)]
    c.idle(T0 + timedelta(seconds=5))  # eine Pause der Regelung: Das Limit gilt weiter
    assert limit_step(c, sync, 10) == []
    c.forget({"wallbox.evcs"})  # andere Aktoren ändern nichts
    assert limit_step(c, sync, 20) == []
    c.forget({"vehicle.tesla"})  # etwa Dry-Run → live: Das Dry-Run-Urteil zählte nicht als gesendet
    assert limit_step(c, sync, 30) == [set_limit(80.0)]


def test_a_confirmed_higher_limit_lets_a_complete_car_charge_again(cfg) -> None:
    """Tessie meldete „geladen“ am alten Limit; das bestätigte höhere Limit hebt das auf.

    Sonst hielte die Zuordnung „geladen“ bis zum Abstecken, und das Ziel über dem alten Limit
    würde nie erreicht (Szenario ``tesla-ladelimit``).
    """
    sync = synced(cfg)
    c = controller(sync)
    # Abfahrt 13:00: Die späteste Startzeit ist schon vorbei, das Laden ist fällig
    entry = VehicleIntent(mode=ChargeMode.PLANNED, target_soc_pct=75, departure="13:00")
    soon = Intent(version=1, control=True, vehicles={"tesla": entry})

    def full(s: int, *, complete: bool, pct: float):
        return tesla(
            plugged=True,
            complete=complete,
            charge_limit_pct=pct,
            soc_pct=65.0,
            soc_at=T0 + timedelta(seconds=s),
        )

    found = c.step(tesla_inputs(sync, 0, intent=soon, tesla_obs=full(0, complete=True, pct=60.0)))
    accept(c, found)
    assert limits_of(found) == [set_limit(75.0)]
    assert not [x for x in found if x.command.kind == CommandKind.CHARGING and x.command.value]
    c.note_result(WriteResult("vehicle.tesla", LIMIT, 75.0, DecisionResult.OK, ""))
    found = c.step(
        tesla_inputs(sync, 10, intent=soon, tesla_obs=full(10, complete=False, pct=75.0))
    )
    started = [
        x.command
        for x in found
        if x.command.kind == CommandKind.CHARGING and x.command.value is True
    ]
    assert [x.actuator for x in started] == ["vehicle.tesla"]
    # Ein abgelehntes Ergebnis hebt „geladen“ nicht auf
    other = controller(sync)
    accept(
        other,
        other.step(tesla_inputs(sync, 0, intent=soon, tesla_obs=full(0, complete=True, pct=60.0))),
    )
    other.note_result(WriteResult("vehicle.tesla", LIMIT, 75.0, DecisionResult.ERROR, "x"))
    later = other.step(
        tesla_inputs(sync, 10, intent=soon, tesla_obs=full(10, complete=False, pct=60.0))
    )
    assert not [
        x for x in later if x.command.kind == CommandKind.CHARGING and x.command.value is True
    ]


def test_the_limit_respects_the_reserve_of_the_hourly_budget(cfg) -> None:
    sync = synced(cfg)
    reserve = 20 - sync.control.tesla_reserve_commands
    c = controller(sync)
    assert limit_step(c, sync, 0, commands_hour={"vehicle.tesla": reserve}) == []
    assert limit_step(c, sync, 10, commands_hour={"vehicle.tesla": reserve - 1}) == [
        set_limit(80.0)
    ]
