from bindaems.core.safety.modes import (
    REASON_CONTROL_OFF_MODE,
    REASON_HANDBACK_MODE,
    REASON_NOT_ALLOWED,
    REASON_WATCHDOG_MODE,
    ModeInfo,
    effective_modes,
    initially_owned,
    live_wanted,
    watchdog_required,
)
from bindaems.shared.control import ActuatorMode, Intent

LIVE, DRY = ActuatorMode.LIVE, ActuatorMode.DRY_RUN
EVCS = "wallbox.evcs"


def test_without_intent_everything_dry_run(limits) -> None:
    modes = effective_modes(None, limits, watchdog_armed=True)
    assert list(modes) == list(limits.actuators)
    assert set(modes.values()) == {ModeInfo(DRY, DRY, ())}


def test_live_needs_config_permission(limits) -> None:
    intent = Intent(version=1, actuators={"wallbox.evcs": LIVE})
    assert effective_modes(intent, limits, True)["wallbox.evcs"] == ModeInfo(
        LIVE, DRY, ("In config.yaml nicht für den Live-Betrieb freigegeben (live_allowed).",)
    )


def test_live_needs_armed_watchdog_for_victron_and_evcs(limits_live) -> None:
    intent = Intent(version=1, control=True, actuators={"wallbox.evcs": LIVE, "victron": LIVE})
    unarmed = effective_modes(intent, limits_live, watchdog_armed=False)
    assert unarmed["victron"] == ModeInfo(LIVE, DRY, ("Watchdog am Cerbo ist nicht scharf.",))
    assert (
        effective_modes(intent, limits_live, watchdog_armed=True)["wallbox.evcs"].effective is LIVE
    )


def test_tesla_live_without_watchdog(limits_live) -> None:
    intent = Intent(version=1, control=True, actuators={"vehicle.tesla": LIVE})
    assert (
        effective_modes(intent, limits_live, watchdog_armed=False)["vehicle.tesla"].effective
        is LIVE
    )


def test_watchdog_required_only_for_victron_and_evcs(limits, limits_live) -> None:
    tesla = Intent(version=1, control=True, actuators={"vehicle.tesla": LIVE})
    evcs = Intent(version=1, control=True, actuators={"wallbox.evcs": LIVE})
    assert watchdog_required(evcs, limits_live)
    assert not watchdog_required(tesla, limits_live)
    assert not watchdog_required(evcs, limits)
    assert not watchdog_required(None, limits_live)


# Weitere Fälle: Zusagen, die die Tests oben nicht berühren.


def test_live_when_requested_allowed_and_watchdog_armed(limits_live) -> None:
    intent = Intent(
        version=1,
        control=True,
        actuators={"victron": LIVE, "wallbox.evcs": LIVE, "vehicle.tesla": LIVE},
    )
    modes = effective_modes(intent, limits_live, watchdog_armed=True)
    assert set(modes.values()) == {ModeInfo(LIVE, LIVE, ())}


def test_explicit_dry_run_request_has_no_reason(limits_live) -> None:
    intent = Intent(version=1, actuators={"victron": DRY})
    assert effective_modes(intent, limits_live, watchdog_armed=False)["victron"] == ModeInfo(
        DRY, DRY, ()
    )


def test_actuator_missing_in_the_intent_requests_dry_run(limits_live) -> None:
    intent = Intent(version=1, control=True, actuators={"wallbox.evcs": LIVE})
    modes = effective_modes(intent, limits_live, watchdog_armed=True)
    assert modes["victron"] == ModeInfo(DRY, DRY, ())
    assert modes["wallbox.evcs"] == ModeInfo(LIVE, LIVE, ())
    assert list(modes) == list(limits_live.actuators)


def test_first_applicable_reason_wins(limits) -> None:
    # Victron ist weder freigegeben noch ist der Watchdog scharf: genannt wird nur der erste Grund.
    intent = Intent(version=1, actuators={"victron": LIVE})
    assert effective_modes(intent, limits, watchdog_armed=False)["victron"] == ModeInfo(
        LIVE, DRY, (REASON_NOT_ALLOWED,)
    )


def test_watchdog_required_only_when_live_is_requested(limits_live) -> None:
    dry = Intent(version=1, control=True, actuators={"victron": DRY, "wallbox.evcs": DRY})
    assert not watchdog_required(dry, limits_live)
    assert not watchdog_required(Intent(version=1, control=True), limits_live)
    live = Intent(version=1, control=True, actuators={"victron": LIVE})
    assert watchdog_required(live, limits_live)


# Übergabe am Ende des Live-Betriebs und Bedarf des Watchdogs nur im Regelbetrieb (Plan 2b).


def test_initially_owned_ignores_control(limits_live) -> None:
    intent = Intent(version=1, control=False, actuators={EVCS: ActuatorMode.LIVE})
    assert initially_owned(intent, limits_live) == {EVCS}
    assert live_wanted(intent, limits_live) == frozenset()


def test_required_needs_control_or_handback(limits_live) -> None:
    off = Intent(version=1, control=False, actuators={EVCS: ActuatorMode.LIVE})
    assert not watchdog_required(off, limits_live)
    assert watchdog_required(off, limits_live, handing_back={EVCS})
    assert watchdog_required(off.model_copy(update={"control": True}), limits_live)


def test_handback_actor_is_live_with_reason(limits_live) -> None:
    modes = effective_modes(Intent(version=1), limits_live, True, handing_back={EVCS})
    assert modes[EVCS] == ModeInfo(
        ActuatorMode.DRY_RUN, ActuatorMode.LIVE, ("Übergabe: Sicherwerte werden gesetzt.",)
    )
    unarmed = effective_modes(Intent(version=1), limits_live, False, handing_back={EVCS})
    assert unarmed[EVCS].effective == ActuatorMode.DRY_RUN


# Weitere Fälle zur Übergabe.

ALL_LIVE = {"victron": LIVE, EVCS: LIVE, "vehicle.tesla": LIVE}


def test_handback_reason_text() -> None:
    assert REASON_HANDBACK_MODE == "Übergabe: Sicherwerte werden gesetzt."


def test_live_wanted_needs_control_request_and_permission(limits, limits_live) -> None:
    on = Intent(version=1, control=True, actuators={EVCS: LIVE, "vehicle.tesla": DRY})
    assert live_wanted(on, limits_live) == {EVCS}
    assert live_wanted(on, limits) == frozenset()  # nicht freigegeben
    assert live_wanted(Intent(version=1, control=True, actuators=ALL_LIVE), limits_live) == {
        "victron",
        EVCS,
        "vehicle.tesla",
    }
    assert live_wanted(None, limits_live) == frozenset()


def test_initially_owned_needs_live_request_and_permission(limits, limits_live) -> None:
    intent = Intent(version=1, actuators={"victron": LIVE, EVCS: DRY, "vehicle.tesla": LIVE})
    assert initially_owned(intent, limits_live) == {"victron", "vehicle.tesla"}
    assert initially_owned(intent, limits) == frozenset()
    assert initially_owned(None, limits_live) == frozenset()


def test_handback_needs_the_watchdog_only_for_victron_and_evcs(limits, limits_live) -> None:
    assert watchdog_required(None, limits_live, handing_back={"victron"})
    assert not watchdog_required(None, limits_live, handing_back={"vehicle.tesla"})
    assert not watchdog_required(None, limits, handing_back={EVCS})  # nicht freigegeben
    assert not watchdog_required(None, limits_live, handing_back={"kein.aktor"})


def test_handback_keeps_a_live_request(limits_live) -> None:
    # Regelbetrieb aus, die EVCS bleibt live angefordert: Sie wird übergeben.
    off = Intent(version=1, actuators={EVCS: LIVE})
    modes = effective_modes(off, limits_live, True, handing_back={EVCS})
    assert modes[EVCS] == ModeInfo(LIVE, LIVE, (REASON_HANDBACK_MODE,))


def test_handback_names_the_objection(limits, limits_live) -> None:
    unarmed = effective_modes(None, limits_live, False, handing_back={EVCS})
    assert unarmed[EVCS] == ModeInfo(DRY, DRY, (REASON_WATCHDOG_MODE,))
    not_allowed = effective_modes(None, limits, True, handing_back={EVCS})
    assert not_allowed[EVCS] == ModeInfo(DRY, DRY, (REASON_NOT_ALLOWED,))


def test_tesla_handback_is_live_without_watchdog(limits_live) -> None:
    modes = effective_modes(None, limits_live, False, handing_back={"vehicle.tesla"})
    assert modes["vehicle.tesla"] == ModeInfo(DRY, LIVE, (REASON_HANDBACK_MODE,))


def test_handback_leaves_the_other_actuators_alone(limits_live) -> None:
    intent = Intent(version=1, control=True, actuators={"victron": LIVE})
    modes = effective_modes(intent, limits_live, True, handing_back={EVCS, "kein.aktor"})
    assert list(modes) == list(limits_live.actuators)
    assert modes["victron"] == ModeInfo(LIVE, LIVE, ())
    assert modes["vehicle.tesla"] == ModeInfo(DRY, DRY, ())
    assert modes == effective_modes(intent, limits_live, True) | {
        EVCS: ModeInfo(DRY, LIVE, (REASON_HANDBACK_MODE,))
    }


# Wirksam live nur im Regelbetrieb (Plan 2b, Präzisierung 3): Ohne ihn ist ein Aktor nur während
# der Übergabe live.


def test_control_off_reason_text() -> None:
    assert REASON_CONTROL_OFF_MODE == "Regelbetrieb ist aus."


def test_live_request_without_control_is_dry_run(limits_live) -> None:
    off = Intent(version=1, control=False, actuators=ALL_LIVE)
    modes = effective_modes(off, limits_live, watchdog_armed=True)
    assert set(modes.values()) == {ModeInfo(LIVE, DRY, (REASON_CONTROL_OFF_MODE,))}
    on = effective_modes(off.model_copy(update={"control": True}), limits_live, True)
    assert set(on.values()) == {ModeInfo(LIVE, LIVE, ())}


def test_order_of_the_reasons(limits, limits_live) -> None:
    # zuerst die Freigabe in config.yaml, dann der Regelbetrieb, dann der Watchdog
    off = Intent(version=1, control=False, actuators={"victron": LIVE})
    assert effective_modes(off, limits, False)["victron"].reasons == (REASON_NOT_ALLOWED,)
    assert effective_modes(off, limits_live, False)["victron"].reasons == (REASON_CONTROL_OFF_MODE,)
    on = off.model_copy(update={"control": True})
    assert effective_modes(on, limits, False)["victron"].reasons == (REASON_NOT_ALLOWED,)
    assert effective_modes(on, limits_live, False)["victron"].reasons == (REASON_WATCHDOG_MODE,)


def test_handback_without_control_stays_live(limits_live) -> None:
    off = Intent(version=1, control=False, actuators=ALL_LIVE)
    modes = effective_modes(off, limits_live, True, handing_back={EVCS})
    assert modes[EVCS] == ModeInfo(LIVE, LIVE, (REASON_HANDBACK_MODE,))
    assert modes["victron"] == ModeInfo(LIVE, DRY, (REASON_CONTROL_OFF_MODE,))
    assert modes["vehicle.tesla"] == ModeInfo(LIVE, DRY, (REASON_CONTROL_OFF_MODE,))
