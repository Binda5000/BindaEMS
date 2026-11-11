"""Tests der Schalter-Übersicht (Betrieb im Blick, Task 6)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.unit.app.ops.test_check import _actuator, _health

from bindaems.app.audit import AuditEntry
from bindaems.app.ops.switches import Switch, switches
from bindaems.shared.config import Config
from bindaems.shared.control import ActuatorMode, Intent, SupportIntent
from bindaems.shared.settings import RuntimeSettings

T0 = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)


def entry(
    action: str,
    target: str | None = None,
    details: dict[str, Any] | None = None,
    *,
    id: int = 1,
    actor: str = "chris",
    source: str = "ui",
    ts: datetime = T0,
) -> AuditEntry:
    return AuditEntry(id, ts, actor, source, action, target, details)


def changed_paths(*paths: str, **kwargs: Any) -> AuditEntry:
    changes = [{"path": p, "old": None, "new": None} for p in paths]
    return entry("settings.update", "einstellungen", {"version": 2, "changes": changes}, **kwargs)


def health4() -> dict[str, Any]:
    health = copy.deepcopy(_health())
    health["actuators"].append(_actuator("vehicle.egolf", "e-Golf", "dry_run", "dry_run"))
    return health


def build(
    cfg: Config,
    *,
    settings: RuntimeSettings | None = None,
    intent: Intent | str | None = "default",
    health: dict[str, Any] | None = None,
    audit: list[AuditEntry] | None = None,
) -> dict[str, Switch]:
    result = switches(
        settings=settings or RuntimeSettings(),
        intent=Intent(version=3) if isinstance(intent, str) else intent,
        health=health if health is not None else health4(),
        cfg=cfg,
        audit=audit or [],
        wallbox_names={"evcs": "Garage"},
    )
    return {item.id: item for item in result}


def test_all_switches_listed_in_catalog_order(cfg: Config) -> None:
    ids = [
        s.id
        for s in switches(
            settings=RuntimeSettings(),
            intent=Intent(version=3),
            health=health4(),
            cfg=cfg,
            audit=[],
            wallbox_names={},
        )
    ]
    assert ids == [
        "control",
        "actuator.victron",
        "actuator.wallbox.evcs",
        "actuator.vehicle.tesla",
        "actuator.thermal.klima",
        "actuator.vehicle.egolf",
        "grid_charge",
        "car_battery",
        "support",
        "load_model",
        "thermal_plan",
        "automation.tesla",
        "automation.egolf",
        "default_mode.evcs",
        "default_mode.twc",
        "report",
    ]


def test_factory_values_not_changed(cfg: Config) -> None:
    health = health4()
    for item in health["actuators"]:
        item["requested"] = "dry_run"
    found = build(cfg, health=health)
    assert not any(s.changed for s in found.values())
    assert all(s.since is None and s.actor is None and s.source is None for s in found.values())
    assert found["control"].value == "aus"
    assert found["support"].value == "an ab 70 %"
    assert found["support"].factory == "an ab 70 %"
    assert found["report"].value == "täglich 07:30"
    assert found["default_mode.evcs"].value == "Unverändert"
    assert found["default_mode.evcs"].label.endswith("Garage")
    assert found["actuator.victron"].factory == "Dry-Run"
    assert found["grid_charge"].link == "/zeitplan"


def test_changed_values(cfg: Config) -> None:
    settings = RuntimeSettings.model_validate(
        {
            "planning": {"grid_charge_allowed": True},
            "ops": {"report": "findings"},
            "wallbox_default_modes": {"evcs": "pv"},
            "learning": {"driving": {"automation": {"tesla": True}}},
        }
    )
    intent = Intent(version=3, control=True, support=SupportIntent(allowed=True, soc_pct=60.0))
    found = build(cfg, settings=settings, intent=intent)
    assert found["grid_charge"].value == "an"
    assert found["grid_charge"].changed
    assert found["report"].value == "nur bei Auffälligkeiten"
    assert found["default_mode.evcs"].changed
    assert found["automation.tesla"].changed
    assert not found["automation.egolf"].changed
    assert found["control"].value == "an" and found["control"].changed
    assert found["support"].value == "an ab 60 %" and found["support"].changed
    assert found["actuator.victron"].value == "Live" and found["actuator.victron"].changed


def test_grid_charge_since_and_actor_from_settings_update(cfg: Config) -> None:
    newer = changed_paths(
        "planning.grid_charge_allowed",
        id=3,
        actor="anna",
        source="ha",
        ts=T0 + timedelta(hours=2),
    )
    other = changed_paths("planning.margin_ct", id=4, ts=T0 + timedelta(hours=3))
    older = changed_paths("planning.grid_charge_allowed", id=1, actor="chris")
    found = build(cfg, audit=[other, newer, older])
    assert found["grid_charge"].since == T0 + timedelta(hours=2)
    assert found["grid_charge"].actor == "anna"
    assert found["grid_charge"].source == "ha"
    assert found["car_battery"].since is None


def test_leaf_below_path_and_report_paths_match(cfg: Config) -> None:
    audit = [
        changed_paths("ops.report_at", id=2, actor="anna", ts=T0 + timedelta(hours=1)),
        changed_paths("learning.thermal.enabled", id=1),
    ]
    found = build(cfg, audit=audit)
    assert found["report"].actor == "anna"
    assert found["thermal_plan"].actor == "chris"
    assert found["load_model"].since is None


def test_automation_from_control_automation_entry(cfg: Config) -> None:
    audit = [
        entry("control.automation", "tesla", id=5, actor="Automatik", source="system"),
        changed_paths("learning.driving.automation.egolf", id=4, actor="anna"),
        changed_paths("learning.driving.automation.tesla", id=1, actor="chris"),
    ]
    found = build(cfg, audit=audit)
    assert found["automation.tesla"].actor == "Automatik"
    assert found["automation.tesla"].source == "system"
    assert found["automation.egolf"].actor == "anna"


def test_actuator_entry_matches_target_list(cfg: Config) -> None:
    audit = [
        entry("intent.actuators", "victron,wallbox.evcs", id=2, actor="anna"),
        entry("intent.actuators", "vehicle.tesla", id=1, actor="chris"),
    ]
    found = build(cfg, audit=audit)
    assert found["actuator.victron"].actor == "anna"
    assert found["actuator.wallbox.evcs"].actor == "anna"
    assert found["actuator.vehicle.tesla"].actor == "chris"
    assert found["actuator.thermal.klima"].since is None


def test_control_and_support_from_intent_entries(cfg: Config) -> None:
    audit = [
        entry("control.support", "Akku-Unterstützung", id=2, actor="anna"),
        entry("intent.control", "regelbetrieb", id=1, actor="chris"),
    ]
    found = build(cfg, audit=audit)
    assert found["control"].actor == "chris"
    assert found["support"].actor == "anna"


def test_actuator_live_not_allowed_shows_config_lock(cfg: Config) -> None:
    health = health4()
    health["actuators"][0]["live_allowed"] = False
    found = build(cfg, health=health)
    assert "in config.yaml gesperrt" in found["actuator.victron"].value
    assert "gesperrt" not in found["actuator.wallbox.evcs"].value


def test_intent_unknown_without_core(cfg: Config) -> None:
    found = build(cfg, intent=None, health=health4())
    assert found["control"].value == "unbekannt"
    assert found["support"].value == "unbekannt"
    assert found["actuator.victron"].value == "unbekannt"
    assert not any(found[k].changed for k in ("control", "support", "actuator.victron"))
    assert found["grid_charge"].value == "aus"


def test_actuator_modes_in_intent_do_not_matter_without_health(cfg: Config) -> None:
    intent = Intent(version=3, actuators={"victron": ActuatorMode.LIVE})
    found = build(cfg, intent=intent, health={})
    assert not [k for k in found if k.startswith("actuator.")]
