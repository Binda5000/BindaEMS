"""Schalter-Übersicht (Betrieb im Blick): Stand, Wert ab Werk und letzte Änderung.

Rein: Einstellungen, Absicht, Health und Änderungsprotokoll hinein, die Liste der Schalter in der
Reihenfolge des Katalogs heraus. Sie zeigt nur; geschaltet wird an den verlinkten Seiten.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from bindaems.app.audit import AuditEntry
from bindaems.app.charging.sessions import wallbox_name
from bindaems.shared.config import Config
from bindaems.shared.control import ActuatorMode, ChargeMode, Intent
from bindaems.shared.numbers import de
from bindaems.shared.settings import RuntimeSettings

UNKNOWN: Final = "unbekannt"
UNCHANGED: Final = "Unverändert"
DRY_RUN: Final = "Dry-Run"
CONFIG_LOCK: Final = "in config.yaml gesperrt"
REPORT_DEFAULT: Final = "täglich 07:30"
REPORT_TEXT: Final = {"findings": "nur bei Auffälligkeiten", "off": "aus"}
MODE_TEXT: Final = {
    ChargeMode.PV: "PV-Überschuss",
    ChargeMode.MIN_PV: "Min + PV",
    ChargeMode.NOW: "Sofort",
    ChargeMode.OFF: "Aus",
    ChargeMode.PLANNED: "Geplant",
}
ACTUATOR_TEXT: Final = {ActuatorMode.LIVE.value: "Live", ActuatorMode.DRY_RUN.value: DRY_RUN}

# Gruppen
REGELUNG: Final = "Regelung"
AKKU: Final = "Akku"
PROGNOSEN: Final = "Prognosen"
KLIMA: Final = "Klima"
AUTOS: Final = "Autos"
MELDUNGEN: Final = "Benachrichtigungen"


@dataclass(frozen=True)
class Switch:
    """Ein Schalter; ``since``, ``actor`` und ``source`` sind ``None``, wenn das Protokoll keine
    Änderung kennt (seit Einrichtung)."""

    id: str
    label: str
    group: str
    value: str
    factory: str
    changed: bool
    since: datetime | None
    actor: str | None
    source: str | None
    link: str


Match = Callable[[AuditEntry], bool]


def _on_off(on: bool) -> str:
    return "an" if on else "aus"


def _paths(entry: AuditEntry) -> list[str]:
    changes = entry.details.get("changes") if entry.details is not None else None
    if not isinstance(changes, list):
        return []
    return [c["path"] for c in changes if isinstance(c, Mapping) and isinstance(c.get("path"), str)]


def _by_path(*paths: str) -> Match:
    """Ein ``settings.update``, das einen der Pfade oder ein Blatt darunter ändert."""

    def match(entry: AuditEntry) -> bool:
        if entry.action != "settings.update":
            return False
        return any(
            changed == path or changed.startswith((f"{path}.", f"{path}["))
            for changed in _paths(entry)
            for path in paths
        )

    return match


def _by_action(action: str, target: str | None = None) -> Match:
    """Ein Eintrag der Aktion; mit ``target`` muss es unter den Zielen stehen (durch Komma
    getrennt, so schreibt ``intent.actuators`` mehrere)."""

    def match(entry: AuditEntry) -> bool:
        if entry.action != action:
            return False
        return target is None or target in (entry.target or "").split(",")

    return match


def _either(*matches: Match) -> Match:
    return lambda entry: any(match(entry) for match in matches)


def _health_actuators(health: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    raw = health.get("actuators") if health is not None else None
    if not isinstance(raw, list):
        return []
    return [a for a in raw if isinstance(a, Mapping) and isinstance(a.get("id"), str)]


def _actuator_value(actuator: Mapping[str, Any], intent_known: bool) -> tuple[str, bool]:
    if not intent_known:
        return UNKNOWN, False
    requested = actuator.get("requested")
    text = ACTUATOR_TEXT.get(requested, str(requested)) if isinstance(requested, str) else UNKNOWN
    if actuator.get("live_allowed") is False:
        text = f"{text}, {CONFIG_LOCK}"
    return text, requested not in (None, ActuatorMode.DRY_RUN.value)


def _support(intent: Intent | None, cfg: Config) -> tuple[str, str, bool]:
    default = f"an ab {de(cfg.control.support_soc_pct)} %"
    if intent is None:
        return UNKNOWN, default, False
    support = intent.support
    if not support.allowed:
        return "aus", default, True
    threshold = support.soc_pct if support.soc_pct is not None else cfg.control.support_soc_pct
    value = f"an ab {de(threshold)} %"
    return value, default, value != default


def _report(settings: RuntimeSettings) -> str:
    ops = settings.ops
    return f"täglich {ops.report_at}" if ops.report == "daily" else REPORT_TEXT[ops.report]


def switches(
    *,
    settings: RuntimeSettings,
    intent: Intent | None,
    health: Mapping[str, Any] | None,
    cfg: Config,
    audit: Sequence[AuditEntry],
    wallbox_names: Mapping[str, str],
) -> list[Switch]:
    """Alle Schalter in der Reihenfolge des Katalogs; ``audit`` neueste zuerst."""
    result: list[Switch] = []

    def add(
        id_: str,
        label: str,
        group: str,
        value: str,
        factory: str,
        link: str,
        match: Match,
        changed: bool | None = None,
    ) -> None:
        last = next((e for e in audit if match(e)), None)
        result.append(
            Switch(
                id=id_,
                label=label,
                group=group,
                value=value,
                factory=factory,
                changed=value != factory if changed is None else changed,
                since=last.ts if last is not None else None,
                actor=last.actor if last is not None else None,
                source=last.source if last is not None else None,
                link=link,
            )
        )

    known = intent is not None
    control = _on_off(intent.control) if intent is not None else UNKNOWN
    add(
        "control",
        "Regelbetrieb",
        REGELUNG,
        control,
        "aus",
        "/system",
        _by_action("intent.control"),
        changed=known and control != "aus",
    )
    for actuator in _health_actuators(health):
        id_ = actuator["id"]
        label = actuator.get("label")
        value, changed = _actuator_value(actuator, known)
        add(
            f"actuator.{id_}",
            label if isinstance(label, str) and label else id_,
            REGELUNG,
            value,
            DRY_RUN,
            "/system",
            _by_action("intent.actuators", id_),
            changed=changed,
        )
    planning = settings.planning
    add(
        "grid_charge",
        "Netzladen des Akkus",
        AKKU,
        _on_off(planning.grid_charge_allowed),
        "aus",
        "/zeitplan",
        _by_path("planning.grid_charge_allowed"),
    )
    add(
        "car_battery",
        "Akku ins Auto",
        AKKU,
        _on_off(planning.car_battery_allowed),
        "aus",
        "/einstellungen",
        _by_path("planning.car_battery_allowed"),
    )
    value, factory, changed = _support(intent, cfg)
    add(
        "support",
        "Akku-Unterstützung fürs Auto",
        AKKU,
        value,
        factory,
        "/laden",
        _by_action("control.support"),
        changed=changed,
    )
    add(
        "load_model",
        "Lernen der Hauslast",
        PROGNOSEN,
        _on_off(settings.learning.load_model_enabled),
        "an",
        "/prognosen",
        _by_path("learning.load_model_enabled"),
    )
    add(
        "thermal_plan",
        "Vorheizen und Vorkühlen",
        KLIMA,
        _on_off(settings.learning.thermal.enabled),
        "aus",
        "/zeitplan",
        _by_path("learning.thermal.enabled"),
    )
    for key, vehicle in cfg.vehicles.items():
        add(
            f"automation.{key}",
            f"Automatik Ziel und Abfahrt: {vehicle.name}",
            AUTOS,
            _on_off(settings.learning.driving.automation.get(key, False)),
            "aus",
            "/laden",
            _either(
                _by_action("control.automation", key),
                _by_path(f"learning.driving.automation.{key}"),
            ),
        )
    for key in cfg.wallboxes:
        mode = settings.wallbox_default_modes.get(key)
        add(
            f"default_mode.{key}",
            f"Lademodus nach dem Abstecken: {wallbox_name(cfg, wallbox_names, key)}",
            AUTOS,
            MODE_TEXT[mode] if mode is not None else UNCHANGED,
            UNCHANGED,
            "/einstellungen",
            _by_path(f"wallbox_default_modes.{key}"),
        )
    report = _report(settings)
    add(
        "report",
        "Tagesbericht",
        MELDUNGEN,
        report,
        REPORT_DEFAULT,
        "/einstellungen",
        _by_path("ops.report", "ops.report_at"),
    )
    return result
