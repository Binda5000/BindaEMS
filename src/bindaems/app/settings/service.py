"""Einstellungsdienst: jede Änderung ist eine neue Version mit Eintrag im Änderungsprotokoll.

Gleichzeitige Änderungen (UI und HA) werden über die Basisversion erkannt: Wer auf einer
veralteten Version aufsetzt, erhält ``VersionConflictError``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
import yaml
from pydantic import ValidationError
from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import IntegrityError

from bindaems.app.audit import AuditLog, Source
from bindaems.app.db.schema import settings_version
from bindaems.shared.settings import RuntimeSettings, settings_warnings
from bindaems.shared.timeutil import Clock

log = structlog.get_logger(__name__)

MAX_CHANGES = 100


@dataclass(frozen=True)
class SettingsVersion:
    version: int
    created_at: datetime
    actor: str
    source: str
    comment: str | None
    settings: RuntimeSettings


class VersionConflictError(Exception):
    def __init__(self, current: int) -> None:
        self.current = current
        super().__init__(
            f"Die Einstellungen wurden inzwischen geändert (aktuell Version {current})."
        )


class SettingsImportError(Exception):
    """Import abgelehnt; der Text nennt die Gründe (deutsch)."""


def diff_paths(old: Any, new: Any) -> list[dict[str, Any]]:
    """Geänderte Blätter zweier JSON-Strukturen, z. B. ``tariff.components[2].value_ct``."""
    changes: list[dict[str, Any]] = []

    def walk(a: Any, b: Any, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in [*a, *(k for k in b if k not in a)]:
                walk(a.get(key), b.get(key), f"{path}.{key}" if path else str(key))
        elif isinstance(a, list) and isinstance(b, list):
            for index in range(max(len(a), len(b))):
                walk(
                    a[index] if index < len(a) else None,
                    b[index] if index < len(b) else None,
                    f"{path}[{index}]",
                )
        elif a != b:
            changes.append({"path": path, "old": a, "new": b})

    walk(old, new, "")
    return changes


def _validation_lines(error: ValidationError) -> str:
    return "\n".join(
        f"{'.'.join(str(part) for part in item['loc']) or '<wurzel>'}: {item['msg']}"
        for item in error.errors(include_input=False, include_url=False)
    )


class SettingsService:
    def __init__(self, engine: Engine, clock: Clock, audit: AuditLog) -> None:
        self._engine = engine
        self._clock = clock
        self._audit = audit
        self._lock = threading.Lock()
        self._listeners: list[Callable[[SettingsVersion], None]] = []
        self._invalid_stored = False
        self._current = self._load_or_init()

    def current(self) -> SettingsVersion:
        return self._current

    def warnings(self) -> list[str]:
        result = []
        if self._invalid_stored:
            result.append(
                f"Gespeicherte Einstellungen (Version {self._current.version}) passen nicht zum "
                "Schema – Standardwerte aktiv."
            )
        return result + settings_warnings(self._current.settings)

    def on_change(self, callback: Callable[[SettingsVersion], None]) -> None:
        self._listeners.append(callback)

    def update(
        self,
        settings: RuntimeSettings,
        *,
        base_version: int,
        actor: str,
        source: Source,
        comment: str | None = None,
    ) -> SettingsVersion:
        with self._lock:
            current = self._current
            if base_version != current.version:
                raise VersionConflictError(current.version)
            if settings == current.settings and not self._invalid_stored:
                return current
            now = self._clock.now()
            data = settings.model_dump(mode="json")
            changes = diff_paths(current.settings.model_dump(mode="json"), data)
            version = SettingsVersion(current.version + 1, now, actor, source, comment, settings)
            try:
                with self._engine.begin() as conn:
                    conn.execute(
                        insert(settings_version).values(
                            version=version.version,
                            created_at=now,
                            actor=actor,
                            source=source,
                            comment=comment,
                            data=data,
                        )
                    )
                    self._audit.record(
                        actor,
                        source,
                        "settings.update",
                        "einstellungen",
                        {"version": version.version, "changes": changes[:MAX_CHANGES]},
                        conn=conn,
                    )
            except IntegrityError as exc:  # anderer Schreiber war schneller
                raise VersionConflictError(self._latest_version()) from exc
            self._current = version
            self._invalid_stored = False
        for listener in list(self._listeners):
            try:
                listener(version)
            except Exception:  # ein Listener darf die Änderung nicht scheitern lassen
                log.exception("Reaktion auf geänderte Einstellungen fehlgeschlagen")
        return version

    def versions(self, limit: int = 50) -> list[SettingsVersion]:
        """Jüngste Versionen zuerst; nicht mehr gültige Daten erscheinen als Standardwerte."""
        query = select(settings_version).order_by(settings_version.c.version.desc()).limit(limit)
        with self._engine.connect() as conn:
            rows = conn.execute(query).all()
        result = []
        for row in rows:
            try:
                settings = RuntimeSettings.model_validate(row.data)
            except ValidationError:
                settings = RuntimeSettings()
            result.append(
                SettingsVersion(
                    row.version, row.created_at, row.actor, row.source, row.comment, settings
                )
            )
        return result

    def export_yaml(self) -> str:
        current = self._current
        body = yaml.safe_dump(
            current.settings.model_dump(mode="json"), allow_unicode=True, sort_keys=False
        )
        return f"# BindaEMS-Laufzeit-Einstellungen, Version {current.version}\n{body}"

    def import_yaml(self, text: str, *, actor: str, source: Source) -> SettingsVersion:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise SettingsImportError(f"YAML-Fehler: {exc}") from exc
        if not isinstance(data, dict):
            raise SettingsImportError("YAML muss ein Objekt enthalten")
        try:
            settings = RuntimeSettings.model_validate(data)
        except ValidationError as exc:
            raise SettingsImportError(_validation_lines(exc)) from exc
        return self.update(
            settings,
            base_version=self._current.version,
            actor=actor,
            source=source,
            comment="Import",
        )

    def _load_or_init(self) -> SettingsVersion:
        query = select(settings_version).order_by(settings_version.c.version.desc()).limit(1)
        with self._engine.begin() as conn:
            row = conn.execute(query).first()
            if row is None:
                defaults = RuntimeSettings()
                now = self._clock.now()
                conn.execute(
                    insert(settings_version).values(
                        version=1,
                        created_at=now,
                        actor="system",
                        source="system",
                        comment="Standardwerte",
                        data=defaults.model_dump(mode="json"),
                    )
                )
                return SettingsVersion(1, now, "system", "system", "Standardwerte", defaults)
        try:
            settings = RuntimeSettings.model_validate(row.data)
        except ValidationError as exc:
            # nichts überschreiben: der Admin soll die Daten prüfen und neu speichern
            log.error(
                "Gespeicherte Einstellungen ungültig, Standardwerte aktiv",
                version=row.version,
                errors=_validation_lines(exc)[:1000],
            )
            self._invalid_stored = True
            settings = RuntimeSettings()
        return SettingsVersion(
            row.version, row.created_at, row.actor, row.source, row.comment, settings
        )

    def _latest_version(self) -> int:
        query = select(settings_version.c.version).order_by(settings_version.c.version.desc())
        with self._engine.connect() as conn:
            return int(conn.execute(query.limit(1)).scalar_one())
