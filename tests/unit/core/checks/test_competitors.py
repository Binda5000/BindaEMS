from tests.helpers import T0, snap

from bindaems.core.checks.competitors import detect_competitors
from bindaems.shared.domain import Alarm, Quality, Severity, SignalKind

STATE = SignalKind.STATE


def test_dess_active_raises_error(cfg) -> None:
    alarms = detect_competitors(snap({"dess.mode": 1}, kind=STATE), cfg, T0)
    assert alarms == [
        Alarm(
            "competitor.dess",
            Severity.ERROR,
            "Dynamic ESS ist aktiv (Modus 1). Das EMS darf Victron nicht steuern.",
            T0,
        )
    ]


def test_float_mode_is_shown_without_decimals(cfg) -> None:
    alarms = detect_competitors(snap({"dess.mode": 4.0}, kind=STATE), cfg, T0)
    assert "(Modus 4)" in alarms[0].message


def test_active_schedule_detected(cfg) -> None:
    alarms = detect_competitors(
        snap({"ess.schedule.0.day": 7, "ess.schedule.1.day": -7}, kind=STATE), cfg, T0
    )
    assert [a.id for a in alarms] == ["competitor.schedule.0"]
    assert alarms[0].message == "Victron-Ladefenster 0 ist aktiv."


def test_evcs_not_manual(cfg) -> None:
    alarms = detect_competitors(snap({"wallbox.evcs.mode": 1}, kind=STATE), cfg, T0)
    assert alarms[0].message == "EVCS „evcs“ ist nicht im manuellen Modus (Modus 1)."


def test_vehicle_schedule_is_warning(cfg) -> None:
    alarms = detect_competitors(
        snap({"vehicle.tesla.scheduled_charging": True}, kind=STATE), cfg, T0
    )
    assert alarms[0].severity is Severity.WARNING
    assert alarms[0].id == "competitor.vehicle_schedule.tesla"
    assert alarms[0].message == "Ladeplan im Fahrzeug „Tesla Model 3“ ist aktiv."


def test_only_ok_values_are_checked(cfg) -> None:
    stale = snap({"dess.mode": 1, "wallbox.evcs.mode": 2}, kind=STATE, quality=Quality.STALE)
    assert detect_competitors(stale, cfg, T0) == []


def test_alarm_order(cfg) -> None:
    values = {
        "vehicle.tesla.scheduled_charging": True,
        "wallbox.evcs.mode": 1,
        "ess.schedule.10.day": 0,
        "ess.schedule.2.day": 8,
        "dess.mode": 1,
    }
    ids = [a.id for a in detect_competitors(snap(values, kind=STATE), cfg, T0)]
    assert ids == [
        "competitor.dess",
        "competitor.schedule.2",
        "competitor.schedule.10",
        "competitor.evcs_mode.evcs",
        "competitor.vehicle_schedule.tesla",
    ]


def test_quiet_when_all_clear(cfg) -> None:
    values = {"dess.mode": 0, "ess.schedule.0.day": -7, "wallbox.evcs.mode": 0}
    assert detect_competitors(snap(values, kind=STATE), cfg, T0) == []
