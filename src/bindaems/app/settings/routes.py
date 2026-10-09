"""Endpunkte der Laufzeit-Einstellungen und der harten Grenzen (nur Ansicht).

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.settings.service import (
    SettingsImportError,
    SettingsService,
    SettingsVersion,
    VersionConflictError,
)
from bindaems.shared.config import Config, EvcsConfig
from bindaems.shared.settings import RuntimeSettings


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_version: int
    settings: RuntimeSettings
    comment: Annotated[str | None, Field(max_length=200)] = None


class ImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    yaml: Annotated[str, Field(max_length=1_000_000)]


def _version_meta(version: SettingsVersion) -> dict[str, Any]:
    return {
        "version": version.version,
        "created_at": version.created_at.isoformat(),
        "actor": version.actor,
        "source": version.source,
        "comment": version.comment,
    }


def limits_json(cfg: Config) -> dict[str, Any]:
    """Harte Grenzen aus ``config.yaml`` – ohne Hosts, Zugangsdaten und Fahrzeug-IDs."""
    wallboxes: dict[str, Any] = {}
    for name, wallbox in cfg.wallboxes.items():
        if isinstance(wallbox, EvcsConfig):
            wallboxes[name] = {
                "type": wallbox.type,
                "min_a": wallbox.min_a,
                "max_a": wallbox.max_a,
                "safe_a": wallbox.safe_a,
                "phase_map": list(wallbox.phase_map),
                "live_allowed": wallbox.live_allowed,
            }
        else:
            wallboxes[name] = {
                "type": wallbox.type,
                "max_a": wallbox.max_a,
                "phase_map": list(wallbox.phase_map),
            }
    return {
        "grid": cfg.grid.model_dump(mode="json"),
        "battery": cfg.battery.model_dump(mode="json"),
        "victron": {
            "max_grid_charge_setpoint_w": cfg.victron.max_grid_charge_setpoint_w,
            "persistent_writes": cfg.victron.persistent_writes.model_dump(mode="json"),
            "watchdog": cfg.victron.watchdog.model_dump(mode="json"),
        },
        "wallboxes": wallboxes,
        "vehicles": {
            key: {
                "name": vehicle.name,
                "usable_kwh": vehicle.usable_kwh,
                "phases": vehicle.phases,
                "min_a": vehicle.min_a,
                "max_a": vehicle.max_a,
                "default_wallbox": vehicle.default_wallbox,
                "live_allowed": vehicle.live_allowed,
            }
            for key, vehicle in cfg.vehicles.items()
        },
    }


def settings_router(service: SettingsService, cfg: Config, guard: Guard) -> APIRouter:
    router = APIRouter()
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]
    Admin = Annotated[SessionInfo, Depends(guard.require("admin"))]

    def current_json() -> dict[str, Any]:
        current = service.current()
        return {
            **_version_meta(current),
            "settings": current.settings.model_dump(mode="json"),
            "warnings": service.warnings(),
        }

    @router.get("/api/settings")
    def read(session: Viewer) -> dict[str, Any]:
        return current_json()

    @router.get("/api/settings/schema")
    def schema(session: Viewer) -> dict[str, Any]:
        return RuntimeSettings.model_json_schema()

    @router.put("/api/settings")
    def write(body: SettingsBody, session: Admin) -> dict[str, Any]:
        try:
            service.update(
                body.settings,
                base_version=body.base_version,
                actor=session.user.username,
                source="ui",
                comment=body.comment,
            )
        except VersionConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return current_json()

    @router.get("/api/settings/versions")
    def versions(session: Admin) -> list[dict[str, Any]]:
        return [_version_meta(version) for version in service.versions()]

    @router.get("/api/settings/export")
    def export(session: Admin) -> Response:
        version = service.current().version
        return Response(
            service.export_yaml(),
            media_type="text/yaml",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="bindaems-einstellungen-v{version}.yaml"'
                )
            },
        )

    @router.post("/api/settings/import")
    def import_(body: ImportBody, session: Admin) -> dict[str, Any]:
        try:
            service.import_yaml(body.yaml, actor=session.user.username, source="ui")
        except SettingsImportError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        except VersionConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return current_json()

    @router.get("/api/limits")
    def limits(session: Viewer) -> dict[str, Any]:
        return limits_json(cfg)

    return router
