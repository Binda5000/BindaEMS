"""Selbstprüfung der Victron- und Wallbox-Einstellungen sowie der Datenfrische."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from bindaems.core.checks.values import fmt, number, schedule_days
from bindaems.shared.config import Config, EvcsConfig
from bindaems.shared.domain import Snapshot

Status = Literal["ok", "warn", "fail", "unknown"]

CRITICAL_SIGNALS = (
    "grid.l1.power_w",
    "grid.l2.power_w",
    "grid.l3.power_w",
    "battery.soc_pct",
    "vebus.l1.ac_in_power_w",
    "vebus.l2.ac_in_power_w",
    "vebus.l3.ac_in_power_w",
)
FRESH_FOR = timedelta(seconds=30)


@dataclass(frozen=True)
class CheckResult:
    id: str
    status: Status
    message: str


def _phases(snap: Snapshot, cfg: Config) -> CheckResult:
    v, expected = number(snap, "vebus.phases"), cfg.grid.phases
    if v is None:
        return CheckResult("phases", "unknown", "Phasenzahl nicht verfügbar.")
    if v == expected:
        return CheckResult("phases", "ok", f"{expected} Phasen erkannt.")
    return CheckResult("phases", "fail", f"Erwartet {expected} Phasen, gemeldet {fmt(v)}.")


def _ess_mode(snap: Snapshot, cfg: Config) -> CheckResult:
    v, expected = number(snap, "ess.hub4_mode"), cfg.victron.expected.hub4_mode
    if v is None:
        return CheckResult("ess_mode", "unknown", "ESS-Modus nicht verfügbar.")
    if v == expected:
        return CheckResult("ess_mode", "ok", f"ESS-Modus {fmt(v)} wie erwartet.")
    return CheckResult("ess_mode", "fail", f"ESS-Modus {fmt(v)}, erwartet {expected}.")


def _batterylife(snap: Snapshot, cfg: Config) -> CheckResult:
    v, expected = number(snap, "ess.batterylife_state"), cfg.victron.expected.batterylife_states
    if v is None:
        return CheckResult("batterylife", "unknown", "BatteryLife-Zustand nicht verfügbar.")
    if v in expected:
        return CheckResult("batterylife", "ok", f"BatteryLife-Zustand {fmt(v)} wie erwartet.")
    return CheckResult(
        "batterylife", "fail", f"BatteryLife-Zustand {fmt(v)}, erwartet {sorted(expected)}."
    )


def _dess(snap: Snapshot) -> CheckResult:
    v = number(snap, "dess.mode")
    if v is None:
        return CheckResult("dess", "unknown", "DESS-Modus nicht verfügbar.")
    if v == 0:
        return CheckResult("dess", "ok", "Dynamic ESS ist aus.")
    return CheckResult("dess", "fail", f"Dynamic ESS ist aktiv (Modus {fmt(v)}).")


def _schedules(snap: Snapshot) -> CheckResult:
    days = schedule_days(snap)
    if not days:
        return CheckResult("schedules", "unknown", "Ladefenster nicht verfügbar.")
    active = [str(k) for k, day in days.items() if day >= 0]
    if active:
        return CheckResult("schedules", "fail", f"Aktive Victron-Ladefenster: {', '.join(active)}.")
    return CheckResult("schedules", "ok", "Keine Victron-Ladefenster aktiv.")


def _min_soc(snap: Snapshot, cfg: Config) -> CheckResult:
    v, reserve = number(snap, "ess.min_soc_pct"), cfg.battery.reserve_soc_pct
    if v is None:
        return CheckResult("min_soc", "unknown", "Victron-Min-SOC nicht verfügbar.")
    if v < reserve:
        return CheckResult(
            "min_soc", "fail", f"Victron-Min-SOC {v:g} % liegt unter der USV-Reserve {reserve:g} %."
        )
    if v > reserve:
        return CheckResult(
            "min_soc", "warn", f"Victron-Min-SOC {v:g} % liegt über der USV-Reserve {reserve:g} %."
        )
    return CheckResult("min_soc", "ok", "Victron-Min-SOC entspricht der USV-Reserve.")


def _evcs_modes(snap: Snapshot, cfg: Config) -> list[CheckResult]:
    results = []
    for name, wallbox in cfg.wallboxes.items():
        if not isinstance(wallbox, EvcsConfig):
            continue
        check_id = f"evcs_mode.{name}"
        v = number(snap, f"wallbox.{name}.mode")
        if v is None:
            results.append(CheckResult(check_id, "unknown", "EVCS-Modus nicht verfügbar."))
        elif v == 0:
            results.append(CheckResult(check_id, "ok", "EVCS im manuellen Modus."))
        else:
            results.append(CheckResult(check_id, "fail", f"EVCS im Modus {fmt(v)} statt manuell."))
    return results


def _fresh_data(ok_since: Callable[[str], datetime | None], now: datetime) -> CheckResult:
    stale = [
        signal
        for signal in CRITICAL_SIGNALS
        if (since := ok_since(signal)) is None or now - since < FRESH_FOR
    ]
    if stale:
        return CheckResult("fresh_data", "fail", f"Nicht aktuell: {', '.join(stale)}.")
    return CheckResult("fresh_data", "ok", "Kritische Messwerte seit mindestens 30 s aktuell.")


def run_selfcheck(
    snap: Snapshot,
    cfg: Config,
    ok_since: Callable[[str], datetime | None],
    now: datetime,
) -> list[CheckResult]:
    return [
        _phases(snap, cfg),
        _ess_mode(snap, cfg),
        _batterylife(snap, cfg),
        _dess(snap),
        _schedules(snap),
        _min_soc(snap, cfg),
        *_evcs_modes(snap, cfg),
        _fresh_data(ok_since, now),
    ]
