from bindaems.core.safety.modes import (
    REASON_NOT_ALLOWED,
    ModeInfo,
    effective_modes,
    watchdog_required,
)
from bindaems.shared.control import ActuatorMode, Intent

LIVE, DRY = ActuatorMode.LIVE, ActuatorMode.DRY_RUN


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
    intent = Intent(version=1, actuators={"wallbox.evcs": LIVE, "victron": LIVE})
    unarmed = effective_modes(intent, limits_live, watchdog_armed=False)
    assert unarmed["victron"] == ModeInfo(LIVE, DRY, ("Watchdog am Cerbo ist nicht scharf.",))
    assert (
        effective_modes(intent, limits_live, watchdog_armed=True)["wallbox.evcs"].effective is LIVE
    )


def test_tesla_live_without_watchdog(limits_live) -> None:
    intent = Intent(version=1, actuators={"vehicle.tesla": LIVE})
    assert (
        effective_modes(intent, limits_live, watchdog_armed=False)["vehicle.tesla"].effective
        is LIVE
    )


def test_watchdog_required_only_for_victron_and_evcs(limits, limits_live) -> None:
    tesla = Intent(version=1, actuators={"vehicle.tesla": LIVE})
    evcs = Intent(version=1, actuators={"wallbox.evcs": LIVE})
    assert watchdog_required(evcs, limits_live)
    assert not watchdog_required(tesla, limits_live)
    assert not watchdog_required(evcs, limits)
    assert not watchdog_required(None, limits_live)


# Weitere Fälle: Zusagen, die die Tests oben nicht berühren.


def test_live_when_requested_allowed_and_watchdog_armed(limits_live) -> None:
    intent = Intent(
        version=1, actuators={"victron": LIVE, "wallbox.evcs": LIVE, "vehicle.tesla": LIVE}
    )
    modes = effective_modes(intent, limits_live, watchdog_armed=True)
    assert set(modes.values()) == {ModeInfo(LIVE, LIVE, ())}


def test_explicit_dry_run_request_has_no_reason(limits_live) -> None:
    intent = Intent(version=1, actuators={"victron": DRY})
    assert effective_modes(intent, limits_live, watchdog_armed=False)["victron"] == ModeInfo(
        DRY, DRY, ()
    )


def test_actuator_missing_in_the_intent_requests_dry_run(limits_live) -> None:
    intent = Intent(version=1, actuators={"wallbox.evcs": LIVE})
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
    dry = Intent(version=1, actuators={"victron": DRY, "wallbox.evcs": DRY})
    assert not watchdog_required(dry, limits_live)
    assert not watchdog_required(Intent(version=1), limits_live)
    assert watchdog_required(Intent(version=1, actuators={"victron": LIVE}), limits_live)
