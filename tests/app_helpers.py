"""Gemeinsame Test-Helfer für ems-app (nur für Tests)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from bindaems.app.auth.service import AuthService, Role
from bindaems.app.consumers.service import Consumer, ConsumerInput
from bindaems.app.fetch import RawResponse
from bindaems.app.ha.entities import HaInputs
from bindaems.app.prices.sources import AwattarSource, EnergyChartsSource, SmartEnergySource
from bindaems.app.prices.store import SlotRow
from bindaems.app.settings.service import SettingsService
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


FIX_SE = Path("tests/fixtures/smartenergy_2026-10-09.json")
FIX_EC = Path("tests/fixtures/energycharts_at_2026-10-09.json")
FIX_AW = Path("tests/fixtures/awattar_2026-10-09.json")


def mock_all_sources(
    respx_mock: Any,
    *,
    smartenergy: Path | int = FIX_SE,
    energy_charts: Path | int = FIX_EC,
    awattar: Path | int = FIX_AW,
) -> None:
    """respx-Routen für die drei Preisquellen: Aufnahme oder HTTP-Status."""
    for url, response in (
        (SmartEnergySource.URL, smartenergy),
        (EnergyChartsSource.URL, energy_charts),
        (AwattarSource.URL, awattar),
    ):
        if isinstance(response, int):
            respx_mock.get(url).respond(response, text="Wartung")
        else:
            respx_mock.get(url).respond(200, text=response.read_text())


class StopScheduler(Exception):
    """Beendet eine Endlosschleife im Test (ausgelöst vom Ersatz für ``sleep``)."""


def record_then_stop(sleeps: list[float], after: int) -> Callable[[float], Awaitable[None]]:
    """Zeichnet Wartezeiten auf und löst beim ``after``-ten Aufruf ``StopScheduler`` aus."""

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= after:
            raise StopScheduler

    return sleep


def _update_settings(service: SettingsService, settings: RuntimeSettings) -> None:
    current = service.current()
    service.update(settings, base_version=current.version, actor="test", source="ui")


def set_grid_usage(service: SettingsService, value_ct: float | None) -> None:
    _update_settings(service, with_grid_usage(service.current().settings, value_ct))


def set_feed_in(service: SettingsService, monthly_ct: dict[str, float]) -> None:
    settings = service.current().settings
    feed_in = settings.feed_in.model_copy(update={"monthly_ct": monthly_ct})
    _update_settings(service, settings.model_copy(update={"feed_in": feed_in}))


# Nachricht ``slot_flows`` des core für den Slot 2026-10-09T08:00Z (10:00 Ortszeit)
SLOT_FLOWS_MSG: dict[str, Any] = {
    "slot_start": "2026-10-09T08:00:00+00:00",
    "covered_s": 900.0,
    "flows_wh": {
        "pv>house": 400.0,
        "pv>wb:evcs": 200.0,
        "pv>battery": 100.0,
        "pv>grid": 300.0,
        "battery>house": 50.0,
        "grid>house": 150.0,
        "grid>wb:twc": 250.0,
    },
    "counters": {"grid.energy_import_kwh": [100.0, 100.4], "grid.energy_export_kwh": [50.0, 50.3]},
}


def sample_inputs(**changes: Any) -> HaInputs:
    """``HaInputs`` mit plausiblen Werten; einzelne Felder lassen sich überschreiben."""
    values: dict[str, Any] = {
        "core_connected": True,
        "mode": "OBSERVE",
        "derived": {
            "grid_w": 512.0,
            "pv_total_w": 3000.0,
            "battery_w": -200.0,
            "house_load_w": 1800.0,
            "wallbox_w": {"evcs": 0.0, "twc": 1380.0},
        },
        "signals": {"battery.soc_pct": 55.0, "vehicle.tesla.soc_pct": 70.0},
        "alarm_ids": [],
        "adapters_offline": [],
        "price_now_ct": 13.44,
        "prices_ahead": [(T_APP, 13.44)],
        "feed_in_ct": 7.0,
        "prices_missing": False,
        "pv_today_kwh": 12.34,
        "pv_tomorrow_kwh": 8.1,
    }
    values.update(changes)
    return HaInputs(**values)
