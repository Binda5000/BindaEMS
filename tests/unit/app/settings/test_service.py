import pytest
import yaml
from sqlalchemy import insert
from tests.app_helpers import T_APP, with_grid_usage

from bindaems.app.db.schema import settings_version
from bindaems.app.settings.service import (
    SettingsImportError,
    SettingsService,
    VersionConflictError,
)
from bindaems.shared.settings import RuntimeSettings


def test_first_start_creates_version_1_with_defaults(service) -> None:
    current = service.current()
    assert (current.version, current.actor, current.settings) == (1, "system", RuntimeSettings())


def test_update_creates_version_and_audits_diff(service, audit) -> None:
    new = with_grid_usage(RuntimeSettings(), 7.12)
    updated = service.update(
        new, base_version=1, actor="chris", source="ui", comment="Netz NÖ 2026"
    )
    assert (updated.version, updated.comment) == (2, "Netz NÖ 2026")
    assert service.current().settings == new
    entry = audit.recent()[0]
    assert (entry.action, entry.target, entry.actor) == (
        "settings.update",
        "einstellungen",
        "chris",
    )
    assert entry.details["changes"] == [
        {"path": "tariff.components[2].value_ct", "old": None, "new": 7.12}
    ]


def test_unchanged_settings_create_no_version(service, audit) -> None:
    assert (
        service.update(RuntimeSettings(), base_version=1, actor="chris", source="ui").version == 1
    )
    assert audit.recent() == []


def test_concurrent_update_conflicts(service) -> None:
    service.update(with_grid_usage(RuntimeSettings(), 7.0), base_version=1, actor="a", source="ui")
    with pytest.raises(VersionConflictError) as conflict:
        service.update(
            with_grid_usage(RuntimeSettings(), 8.0), base_version=1, actor="b", source="ha"
        )
    assert conflict.value.current == 2
    assert (
        str(conflict.value) == "Die Einstellungen wurden inzwischen geändert (aktuell Version 2)."
    )


def test_listeners_notified_and_their_errors_contained(service) -> None:
    seen: list[int] = []
    service.on_change(lambda v: 1 / 0)
    service.on_change(lambda v: seen.append(v.version))
    service.update(with_grid_usage(RuntimeSettings(), 7.0), base_version=1, actor="a", source="ui")
    assert seen == [2]


def test_export_import_round_trip(service) -> None:
    text = service.export_yaml()
    assert text.startswith("# BindaEMS-Laufzeit-Einstellungen, Version 1\n")
    data = yaml.safe_load(text)
    data["tariff"]["fixed_price_gross_ct"] = 28.5
    imported = service.import_yaml(yaml.safe_dump(data), actor="chris", source="ui")
    assert (imported.version, imported.comment) == (2, "Import")
    assert imported.settings.tariff.fixed_price_gross_ct == 28.5
    assert imported.settings.tariff.components[2].windows[0].start.hour == 10


def test_import_rejects_invalid_yaml(service) -> None:
    with pytest.raises(SettingsImportError, match="YAML muss ein Objekt enthalten"):
        service.import_yaml("- liste\n", actor="chris", source="ui")
    with pytest.raises(SettingsImportError, match=r"tariff\.vat_pct"):
        service.import_yaml("tariff: {vat_pct: 150}\n", actor="chris", source="ui")
    assert service.current().version == 1


def test_invalid_stored_settings_fall_back_to_defaults_with_warning(engine, clock, audit) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(settings_version).values(
                version=1, created_at=T_APP, actor="system", source="system", data={"veraltet": 1}
            )
        )
    service = SettingsService(engine, clock, audit)
    assert (service.current().version, service.current().settings) == (1, RuntimeSettings())
    assert service.warnings()[0] == (
        "Gespeicherte Einstellungen (Version 1) passen nicht zum Schema – Standardwerte aktiv."
    )
    service.update(RuntimeSettings(), base_version=1, actor="chris", source="ui")
    assert service.current().version == 2
    assert not service.warnings()[0].startswith("Gespeicherte")
