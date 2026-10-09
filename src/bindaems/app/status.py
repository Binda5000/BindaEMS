"""Zustand der Bausteine der app für die System-Seite und HA."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ComponentStatus:
    name: str
    ok: bool
    message: str  # kurzer deutscher Text für die System-Seite
    since: datetime | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


StatusSource = Callable[[], ComponentStatus]


def status_json(status: ComponentStatus) -> dict[str, Any]:
    return {
        "name": status.name,
        "ok": status.ok,
        "message": status.message,
        "since": status.since.isoformat() if status.since is not None else None,
        "details": dict(status.details),
    }
