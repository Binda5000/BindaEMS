"""Selbstprüfung der Victron- und Wallbox-Einstellungen sowie der Datenfrische."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from bindaems.core.checks.values import evcs_mode, fmt, number, schedule_days
from bindaems.shared.config import Config, EvcsConfig
from bindaems.shared.domain import Snapshot
from bindaems.shared.numbers import de

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

_MULTI_ON = 3  # VE.Bus-Modus „Ein“ (1 „Nur Laden“, 2 „Nur Wechselrichter“, 4 „Aus“)
_IMPORT_LIMIT_OFF = -1  # AcInputLimit des Cerbo: Importlimit aus
_IMPORT_LIMIT_TOLERANCE_A = 0.5  # erst eine größere Abweichung vom konfigurierten Wert warnt


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
            "min_soc",
            "fail",
            f"Victron-Min-SOC {de(v)} % liegt unter der USV-Reserve {de(reserve)} %.",
        )
    if v > reserve:
        return CheckResult(
            "min_soc",
            "warn",
            f"Victron-Min-SOC {de(v)} % liegt über der USV-Reserve {de(reserve)} %.",
        )
    return CheckResult("min_soc", "ok", "Victron-Min-SOC entspricht der USV-Reserve.")


def _multi_mode(snap: Snapshot) -> CheckResult:
    """Ohne „Ein“ wirkt das Importlimit des Cerbo nicht.

    Der Prüfpunkt warnt nur und sperrt nichts (``lock_causes`` kennt ihn nicht).
    """
    v = number(snap, "vebus.mode")
    if v is None:
        return CheckResult("multi_mode", "unknown", "Multi-Modus nicht verfügbar.")
    if v == _MULTI_ON:
        return CheckResult("multi_mode", "ok", "Multi auf „Ein“.")
    return CheckResult(
        "multi_mode",
        "warn",
        f"Multi nicht auf „Ein“ (Modus {fmt(v)}); das Importlimit wirkt nicht.",
    )


def _import_limit(snap: Snapshot, cfg: Config) -> CheckResult:
    """Das Importlimit am Cerbo soll dem konfigurierten entsprechen; −1 heißt „aus“.

    Der Prüfpunkt warnt nur und sperrt nichts (``lock_causes`` kennt ihn nicht). Eine Abweichung
    bis ``_IMPORT_LIMIT_TOLERANCE_A`` zählt noch als konfiguriert.
    """
    v, expected = number(snap, "settings.cgwacs.acinputlimit"), cfg.grid.import_limit_a
    if v is None:
        return CheckResult("import_limit", "warn", "Importlimit am Cerbo nicht verfügbar.")
    if v == _IMPORT_LIMIT_OFF:
        return CheckResult("import_limit", "warn", "Importlimit am Cerbo aus.")
    if abs(v - expected) > _IMPORT_LIMIT_TOLERANCE_A:
        return CheckResult(
            "import_limit",
            "warn",
            f"Importlimit am Cerbo {fmt(v)} A, konfiguriert {fmt(expected)} A.",
        )
    return CheckResult("import_limit", "ok", f"Importlimit am Cerbo {fmt(v)} A wie konfiguriert.")


def _evcs_modes(snap: Snapshot, cfg: Config) -> list[CheckResult]:
    results = []
    for name, wallbox in cfg.wallboxes.items():
        if not isinstance(wallbox, EvcsConfig):
            continue
        check_id = f"evcs_mode.{name}"
        v = evcs_mode(snap, name)
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
        _multi_mode(snap),
        _import_limit(snap, cfg),
        *_evcs_modes(snap, cfg),
        _fresh_data(ok_since, now),
    ]
