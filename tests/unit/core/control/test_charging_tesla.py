from dataclasses import replace
from datetime import timedelta

import pytest
from tests.helpers import T0
from tests.unit.core.control.charge_helpers import accept, evcs, grid, inputs, run, tesla, twc

from bindaems.core.control.charging import ChargeController
from bindaems.core.control.executor import WriteResult
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
        62,
        make=tesla_inputs,
        intent=EGOLF_NOW,
        twc_obs=twc(connected=False),
        tesla_obs=tesla(plugged=True),
        evcs_obs=evcs(charging=True, phase_a=dict.fromkeys(PHASES, 10.0)),
        present=at_evcs(10.0, True),
        grid_obs=lambda s: grid(l1=36.0) if s == 61 else grid(),
    )
    # ab 60 unklar: nichts an den Tesla, kein „Laden an/aus“ an die EVCS; bei 61 frei 33 − 26 = 7
    assert [(t, c.command) for t, c in found if t >= 60] == [
        (
            61,
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
    unclear = controller(cfg)
    run(unclear, cfg, 61, make=tesla_inputs, twc_obs=twc(connected=False), evcs_obs=evcs())
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


UNCLEAR_EGOLF = {
    "twc_obs": twc(connected=False),
    "tesla_obs": tesla(plugged=True),
    "evcs_obs": evcs(charging=True, phase_a=dict.fromkeys(PHASES, 10.0)),
    "present": at_evcs(10.0, True),
}


def test_unclear_neither_starts_nor_stops_by_mode(cfg) -> None:  # Design 8
    # PV ohne Überschuss stoppte bei 300; ab 60 ist die Zuordnung unklar. Ab 400 liegt L1 über dem
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
    found = run(
        controller(cfg),
        cfg,
        68,
        make=tesla_inputs,
        intent=EGOLF_NOW,
        grid_obs=lambda s: grid(l1=34.0) if s >= 61 else grid(),
        **UNCLEAR_EGOLF,
    )

    def unclear(kind, value, cause):
        reasons = ("mode_now", "assignment_unclear", cause)
        return Command("wallbox.evcs", kind, value, urgent=True, reasons=reasons)

    assert [(t, c) for t, c in of(found, "wallbox.evcs") if t >= 60] == [
        (61, unclear(CURRENT, 9.0, "phase_limit")),  # frei 33 − (34 − 10)
        (64, unclear(CURRENT, 6.0, "overload")),  # MINIMUM
        (67, unclear(CHARGING, False, "overload")),  # EVCS_STOP
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
        (133, Command("wallbox.evcs", CHARGING, False, reasons=("mode_off",))),
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
