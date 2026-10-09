"""Gemeinsame Test-Helfer für ems-app (nur für Tests)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Literal

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from bindaems.app.auth.service import AuthService, Role
from bindaems.app.consumers.service import Consumer, ConsumerInput
from bindaems.app.fetch import RawResponse
from bindaems.app.prices.store import SlotRow
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


def k(id_: int, power: float | None, parent: int | None = None) -> tuple[Consumer, float | None]:
    """Verbraucher ``V<id>`` mit core-Signal ``load.v<id>.power_w`` und seiner Leistung."""
    consumer = Consumer(
        id=id_,
        name=f"V{id_}",
        group=None,
        parent_id=parent,
        color="#000000",
        source_kind="core",
        power_ref=f"load.v{id_}.power_w",
        power_unit=None,
        sort=0,
    )
    return consumer, power


def tree_input(
    pairs: Sequence[tuple[Consumer, float | None]],
) -> tuple[list[Consumer], Callable[[Consumer], float | None]]:
    powers = {consumer.id: power for consumer, power in pairs}
    return [consumer for consumer, _ in pairs], lambda consumer: powers[consumer.id]


def core_input(
    name: str, parent_id: int | None = None, ref: str = "load.x.power_w"
) -> ConsumerInput:
    return ConsumerInput(
        name=name, color="#000000", source_kind="core", power_ref=ref, parent_id=parent_id
    )


def raw_response(source: str, body: str, status: int = 200) -> RawResponse:
    return RawResponse(source, f"https://{source}.example/", T_APP, status, body)


def price_row(
    slot: datetime,
    net: float,
    origin: Literal["primary", "fallback"],
    reference_ct: float | None = None,
) -> SlotRow:
    raw = net if origin == "primary" else None
    return SlotRow(slot, raw, net, reference_ct, origin, None)
