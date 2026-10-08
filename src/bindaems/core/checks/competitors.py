"""Erkennung von Systemen, die parallel zum EMS regeln würden."""

from __future__ import annotations

from datetime import datetime

from bindaems.core.checks.values import fmt, number, schedule_days
from bindaems.shared.config import Config, EvcsConfig
from bindaems.shared.domain import Alarm, Severity, Snapshot


def detect_competitors(snap: Snapshot, cfg: Config, now: datetime) -> list[Alarm]:
    """Alarme für aktive Fremdregelungen; geprüft werden nur ``OK``-Werte."""
    alarms: list[Alarm] = []
    dess = number(snap, "dess.mode")
    if dess is not None and dess != 0:
        alarms.append(
            Alarm(
                "competitor.dess",
                Severity.ERROR,
                f"Dynamic ESS ist aktiv (Modus {fmt(dess)}). Das EMS darf Victron nicht steuern.",
                now,
            )
        )
    for k, day in schedule_days(snap).items():
        if day >= 0:
            alarms.append(
                Alarm(
                    f"competitor.schedule.{k}",
                    Severity.ERROR,
                    f"Victron-Ladefenster {k} ist aktiv.",
                    now,
                )
            )
    for name, wallbox in cfg.wallboxes.items():
        mode = number(snap, f"wallbox.{name}.mode")
        if isinstance(wallbox, EvcsConfig) and mode is not None and mode != 0:
            alarms.append(
                Alarm(
                    f"competitor.evcs_mode.{name}",
                    Severity.ERROR,
                    f"EVCS „{name}“ ist nicht im manuellen Modus (Modus {fmt(mode)}).",
                    now,
                )
            )
    for vehicle_id, vehicle in cfg.vehicles.items():
        if snap.ok(f"vehicle.{vehicle_id}.scheduled_charging") is True:
            alarms.append(
                Alarm(
                    f"competitor.vehicle_schedule.{vehicle_id}",
                    Severity.WARNING,
                    f"Ladeplan im Fahrzeug „{vehicle.name}“ ist aktiv.",
                    now,
                )
            )
    return alarms
