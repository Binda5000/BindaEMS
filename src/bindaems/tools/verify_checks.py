"""Prüffunktionen für das Prüfprotokoll Teil 1 (Spec 17.1) – reine Funktionen ohne E/A."""

from __future__ import annotations

import re
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from bindaems.core.adapters.evcs import (
    KNOWN_PRODUCT_IDS,
    REG_FW_HIGH,
    REG_FW_LOW,
    REG_PRODUCT_ID,
)
from bindaems.core.checks.selfcheck import CheckResult
from bindaems.core.checks.values import fmt, number
from bindaems.shared.config import Config
from bindaems.shared.domain import Quality, Snapshot

Status = Literal["ok", "warn", "fail", "info"]

MAX_UPDATE_INTERVAL_S = 5.0
MAX_CLOCK_OFFSET_S = 2.0
EXPORT_THRESHOLD_W = -100.0


@dataclass(frozen=True)
class Finding:
    item: str  # Spec-Punkt, z. B. "17.1-4"
    title: str
    status: Status
    detail: str


_SELFCHECK_ITEMS: dict[str, tuple[str, str]] = {
    "phases": ("17.1-1", "vebus-Phasen"),
    "ess_mode": ("17.1-2", "ESS-Modus"),
    "batterylife": ("17.1-2", "BatteryLife-Zustand"),
    "dess": ("17.1-2", "Dynamic ESS"),
    "schedules": ("17.1-2", "Victron-Ladefenster"),
    "min_soc": ("17.1-2", "Min-SOC und USV-Reserve"),
    "fresh_data": ("17.1-4", "Datenfrische"),
}
_PV_POSITIONS = {0: "AC-In 1", 1: "AC-Out", 2: "AC-In 2"}


def _values(snap: Snapshot, signals: Sequence[str]) -> tuple[list[str], list[str]]:
    """``(vorhanden als „signal = wert“, fehlend)`` für OK-Werte."""
    present, missing = [], []
    for signal in signals:
        value = snap.ok(signal)
        if value is None:
            missing.append(signal)
        else:
            present.append(f"{signal} = {fmt(value)}")
    return present, missing


def check_selfcheck(results: Sequence[CheckResult]) -> list[Finding]:
    findings = []
    for result in results:
        if result.id.startswith("evcs_mode."):
            item, title = "17.1-8", f"EVCS-Modus {result.id.split('.', 1)[1]}"
        else:
            item, title = _SELFCHECK_ITEMS.get(result.id, ("17.1-2", result.id))
        status: Status = "warn" if result.status == "unknown" else result.status
        findings.append(Finding(item, title, status, result.message))
    return findings


def check_mqtt_inventory(services: Mapping[str, set[int]], cfg: Config) -> Finding:
    instances = cfg.victron.instances
    expected: list[tuple[str, int]] = [
        (service, instance)
        for service, instance in (
            ("grid", instances.grid),
            ("vebus", instances.vebus),
            ("battery", instances.battery),
        )
        if instance is not None
    ]
    expected += [("pvinverter", i) for i in instances.pv]
    expected += [("acload", i) for i in instances.acload]
    expected += [("evcharger", i) for i in instances.evcharger]
    missing = [f"{service} {i}" for service, i in expected if i not in services.get(service, set())]
    found = "; ".join(
        f"{service} {', '.join(str(i) for i in sorted(ids))}"
        for service, ids in sorted(services.items())
        if ids
    )
    detail = f"Gefunden: {found or 'keine Dienste'}"
    if missing:
        detail += f". Fehlt: {', '.join(missing)}"
    return Finding("17.1-3", "MQTT-Dienste und Instanzen", "warn" if missing else "ok", detail)


def check_grid_meter(samples: Sequence[Snapshot]) -> list[Finding]:
    if not samples:
        return [Finding("17.1-4", "Netzzähler", "fail", "Keine Proben vom Cerbo erhalten.")]
    last = samples[-1]
    required = [
        f"grid.l{n}.{quantity}"
        for n in (1, 2, 3)
        for quantity in ("power_w", "current_a", "voltage_v")
    ]
    present, missing = _values(last, required)
    findings = [
        Finding(
            "17.1-4",
            "Netzzähler P/I/U je Phase",
            "fail" if missing else "ok",
            f"Fehlt: {', '.join(missing)}" if missing else "; ".join(present),
        )
    ]

    exporting = signed = False
    for sample in samples:
        for n in (1, 2, 3):
            power = number(sample, f"grid.l{n}.power_w")
            current = number(sample, f"grid.l{n}.current_a")
            if power is not None and current is not None and power < EXPORT_THRESHOLD_W:
                exporting = True
                signed = signed or current < 0
    if exporting:
        sign = Finding(
            "17.1-4",
            "Vorzeichen des Netzstroms",
            "info",
            "vorzeichenbehaftet" if signed else "vorzeichenlos",
        )
    else:
        sign = Finding(
            "17.1-4",
            "Vorzeichen des Netzstroms",
            "warn",
            "vorzeichenlos angenommen – keine Probe mit Einspeisung über 100 W, bitte "
            "bei Einspeisung wiederholen",
        )
    findings.append(sign)

    ages = []
    for sample in samples:
        reading = sample.get("grid.l1.power_w")
        if reading is not None and reading.quality is Quality.OK:
            ages.append((sample.ts - reading.ts).total_seconds())
    if ages:
        median_age = statistics.median(ages)
        interval = 2 * median_age  # Alter zum Probenzeitpunkt ist gleichverteilt
        findings.append(
            Finding(
                "17.1-4",
                "Netzzähler Aktualisierung",
                "ok" if interval <= MAX_UPDATE_INTERVAL_S else "warn",
                f"Median-Alter von grid.l1.power_w {median_age:.1f} s, Aktualisierung etwa alle "
                f"{interval:.1f} s ({len(ages)} Proben)",
            )
        )
    else:
        findings.append(
            Finding("17.1-4", "Netzzähler Aktualisierung", "fail", "grid.l1.power_w nie gültig.")
        )

    counters, missing_counters = _values(last, ["grid.energy_import_kwh", "grid.energy_export_kwh"])
    findings.append(
        Finding(
            "17.1-4",
            "Netzzähler Zählerstände",
            "warn" if missing_counters else "ok",
            "; ".join(counters + [f"fehlt: {c}" for c in missing_counters]),
        )
    )
    return findings


def check_pv_and_submeter(snap: Snapshot) -> list[Finding]:
    findings = []
    positions = sorted(name for name in snap.readings if re.fullmatch(r"pv\.[^.]+\.position", name))
    if not positions:
        findings.append(Finding("17.1-5", "PV-Position", "fail", "Keine PV-Position gemeldet."))
    for signal in positions:
        pv_id = signal.split(".")[1]
        value = number(snap, signal)
        if value == 1:
            findings.append(Finding("17.1-5", f"PV-Position {pv_id}", "ok", f"{pv_id}: AC-Out"))
        else:
            meaning = _PV_POSITIONS.get(int(value), "unbekannt") if value is not None else "–"
            findings.append(
                Finding(
                    "17.1-5",
                    f"PV-Position {pv_id}",
                    "warn",
                    f"{pv_id}: Position {fmt(value)} ({meaning}), erwartet AC-Out (1)",
                )
            )
    counters = sorted(
        name for name in snap.readings if re.fullmatch(r"(pv|load)\.[^.]+\.energy_kwh", name)
    )
    present, _ = _values(snap, counters)
    findings.append(
        Finding(
            "17.1-5",
            "Zählerstände PV und Subzähler",
            "ok" if present else "warn",
            "; ".join(present) or "Keine Zählerstände gemeldet.",
        )
    )
    return findings


def check_battery(snap: Snapshot) -> Finding:
    signals = [
        "battery.soc_pct",
        "battery.voltage_v",
        "battery.current_a",
        "battery.power_w",
        "battery.ccl_a",
        "battery.dcl_a",
        "battery.installed_capacity_ah",
    ]
    present, missing = _values(snap, signals)
    detail = "; ".join(present + ([f"Fehlt: {', '.join(missing)}"] if missing else []))
    return Finding("17.1-6", "Akku", "warn" if missing else "ok", detail)


def check_hub4_overrides(snap: Snapshot) -> Finding:
    signals = sorted(name for name in snap.readings if name.startswith("ess.override."))
    present, _ = _values(snap, signals)
    return Finding(
        "17.1-7", "hub4-Overrides", "info", "; ".join(present) or "Keine Overrides gemeldet."
    )


def check_peak_shaving(snap: Snapshot) -> Finding:
    signals = sorted(
        name
        for name in snap.readings
        if name.startswith("settings.cgwacs.")
        and any(
            word in name.removeprefix("settings.cgwacs.") for word in ("peak", "limit", "import")
        )
    )
    present, _ = _values(snap, signals)
    return Finding(
        "17.1-2",
        "Peak-Shaving-Einstellungen",
        "info",
        "; ".join(present) or "Keine Peak-Shaving-Einstellungen gemeldet.",
    )


def check_evcs_dump(regs: Mapping[int, int]) -> list[Finding]:
    if REG_PRODUCT_ID not in regs:
        return [Finding("17.1-8", "EVCS-Produkt-ID", "fail", "Register 5000 nicht lesbar.")]
    product_id = regs[REG_PRODUCT_ID]
    known = product_id in KNOWN_PRODUCT_IDS
    findings = [
        Finding(
            "17.1-8",
            "EVCS-Produkt-ID",
            "ok" if known else "warn",
            f"Produkt-ID 0x{product_id:04X}" + ("" if known else " – unbekannt"),
        ),
        Finding(
            "17.1-8",
            "EVCS-Firmware",
            "info",
            f"Firmware {regs.get(REG_FW_HIGH, 0):04x}.{regs.get(REG_FW_LOW, 0):04x}",
        ),
    ]
    nonzero = [
        f"{address} = {value} (0x{value:04X})" for address, value in sorted(regs.items()) if value
    ]
    findings.append(
        Finding("17.1-8", "EVCS-Register ≠ 0", "info", "; ".join(nonzero) or "Alle Register 0.")
    )
    return findings


_TWC_FIELDS = (
    "contactor_closed",
    "vehicle_connected",
    "evse_state",
    "currentA_a",
    "currentB_a",
    "currentC_a",
    "voltageA_v",
    "voltageB_v",
    "voltageC_v",
    "session_energy_wh",
)


def check_twc(vitals: Mapping[str, Any]) -> Finding:
    missing = [field for field in _TWC_FIELDS if field not in vitals]
    if missing:
        return Finding("17.1-9", "Wall-Connector-Vitals", "warn", f"Fehlt: {', '.join(missing)}")
    values = ", ".join(f"{field} = {vitals[field]}" for field in _TWC_FIELDS)
    return Finding("17.1-9", "Wall-Connector-Vitals", "ok", values)


_TESSIE_FIELDS = (
    ("state",),
    ("charge_state", "battery_level"),
    ("charge_state", "charge_limit_soc"),
    ("charge_state", "charging_state"),
    ("charge_state", "charge_amps"),
    ("charge_state", "charge_current_request"),
    ("charge_state", "charger_actual_current"),
    ("charge_state", "charger_phases"),
    ("charge_state", "charger_power"),
    ("charge_state", "scheduled_charging_mode"),
    ("drive_state", "latitude"),
    ("drive_state", "longitude"),
)


def _has(data: Mapping[str, Any], path: tuple[str, ...]) -> bool:
    node: Any = data
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            return False
        node = node[key]
    return True


def check_tessie(state: Mapping[str, Any]) -> Finding:
    problems = [
        "Fehlt: " + ", ".join(".".join(path) for path in _TESSIE_FIELDS if not _has(state, path))
    ]
    if problems[0] == "Fehlt: ":
        problems = []
    charge = state.get("charge_state")
    mode = charge.get("scheduled_charging_mode") if isinstance(charge, Mapping) else None
    if mode not in (None, "Off"):
        problems.append(f"Ladeplan im Fahrzeug aktiv (scheduled_charging_mode = {mode})")
    if problems:
        return Finding("17.1-10", "Tessie-Felder", "warn", "; ".join(problems))
    return Finding("17.1-10", "Tessie-Felder", "ok", "Alle benötigten Felder vorhanden.")


def check_ha(entities: Mapping[str, Mapping[str, Any] | None]) -> list[Finding]:
    findings = []
    for entity_id, data in entities.items():
        if data is None:
            findings.append(
                Finding(
                    "17.1-11", f"HA {entity_id}", "fail", f"Entität {entity_id} nicht gefunden."
                )
            )
            continue
        state = data.get("state")
        status: Status = "warn" if state in ("unavailable", "unknown", None, "") else "ok"
        findings.append(Finding("17.1-11", f"HA {entity_id}", status, f"{entity_id} = {state}"))
    return findings


def check_influx(show: Mapping[str, Any]) -> list[Finding]:
    databases = show.get("databases") or []
    findings = [
        Finding(
            "17.1-11",
            "InfluxDB-Datenbank",
            "ok" if "bindaems" in databases else "fail",
            f"Datenbanken: {', '.join(databases) or 'keine'}",
        )
    ]
    policies = {p.get("name"): p for p in show.get("retention_policies") or []}
    problems = []
    raw, long = policies.get("raw"), policies.get("long")
    if raw is None:
        problems.append("raw fehlt")
    elif raw.get("duration") != "2160h0m0s" or not raw.get("default"):
        problems.append(f"raw: Dauer {raw.get('duration')}, Standard {raw.get('default')}")
    if long is None:
        problems.append("long fehlt")
    elif long.get("duration") != "0s":
        problems.append(f"long: Dauer {long.get('duration')}")
    findings.append(
        Finding(
            "17.1-11",
            "InfluxDB-Retention-Policies",
            "fail" if problems else "ok",
            "; ".join(problems) or "raw (90 Tage, Standard) und long (unbegrenzt) vorhanden.",
        )
    )
    ha_db, measurements = show.get("ha_database"), show.get("ha_measurements") or []
    findings.append(
        Finding(
            "17.1-11",
            "HA-Messungen in InfluxDB",
            "info" if ha_db and measurements else "warn",
            f"{ha_db}: {len(measurements)} Messungen ({', '.join(measurements[:30])})"
            if ha_db and measurements
            else "Keine HA-Datenbank oder keine Messungen gefunden.",
        )
    )
    return findings


def check_clock_offsets(offsets_s: Mapping[str, float]) -> Finding:
    if not offsets_s:
        return Finding("17.1-12", "Uhrzeit", "warn", "Keine Zeitquelle erreichbar.")
    detail = "; ".join(f"{name}: {offset:+.1f} s" for name, offset in offsets_s.items())
    too_far = any(abs(offset) > MAX_CLOCK_OFFSET_S for offset in offsets_s.values())
    return Finding("17.1-12", "Uhrzeit gegenüber der VM", "warn" if too_far else "ok", detail)


_LABELS: dict[Status, str] = {"ok": "OK", "warn": "WARNUNG", "fail": "FEHLER", "info": "INFO"}


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", "<br>")


def render_report(findings: Sequence[Finding], meta: Mapping[str, str]) -> str:
    lines = [
        f"# Prüfprotokoll Teil 1 – {meta.get('Datum', '')}",
        "",
        "| Angabe | Wert |",
        "|---|---|",
        *(f"| {_cell(key)} | {_cell(value)} |" for key, value in meta.items()),
        "",
        "| Punkt | Prüfung | Ergebnis | Details |",
        "|---|---|---|---|",
        *(
            f"| {f.item} | {_cell(f.title)} | {_LABELS[f.status]} | {_cell(f.detail)} |"
            for f in findings
        ),
        "",
    ]
    return "\n".join(lines)
