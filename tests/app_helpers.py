"""Gemeinsame Test-Helfer für ems-app (nur für Tests)."""

from __future__ import annotations

from datetime import UTC, datetime

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from bindaems.app.auth.service import AuthService, Role
from bindaems.shared.settings import RuntimeSettings

T_APP = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)  # 10:00 Ortszeit

PASSWORD = "richtig-geheim-1"
# billige Parameter, damit die Tests schnell bleiben; der Dienst nutzt sonst die Standardwerte
FAST_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


def login_as(
    client: TestClient, auth: AuthService, role: Role, username: str | None = None
) -> dict[str, str]:
    """Legt einen Benutzer an, meldet ihn an und liefert den CSRF-Header für Schreibzugriffe."""
    name = username or f"{role}-test"
    auth.create_user(name, PASSWORD, role, actor="test", source="cli")
    response = client.post("/api/auth/login", json={"username": name, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.cookies["bindaems_csrf"]}


def with_grid_usage(settings: RuntimeSettings, value_ct: float | None) -> RuntimeSettings:
    """Kopie mit einem Wert für das Netznutzungsentgelt (Bestandteil ``grid_usage``)."""
    components = [
        c.model_copy(update={"value_ct": value_ct}) if c.id == "grid_usage" else c
        for c in settings.tariff.components
    ]
    tariff = settings.tariff.model_copy(update={"components": components})
    return settings.model_copy(update={"tariff": tariff})
