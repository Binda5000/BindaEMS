"""Alle Bedienelemente in Home Assistant wirken in beide Richtungen (Plan 4b, Entscheidung 15).

In der Demo-Welt (``tests/ui_world.py``, mit echtem core hinter der internen API): Ein Befehl aus
Home Assistant ändert die Absicht oder die Einstellung, und die app veröffentlicht danach genau den
Zustand, den der Befehl setzte. Eine Änderung über die API zeigt Home Assistant mit dem nächsten
Takt der Veröffentlichung. Der Test deckt jedes Bedienelement (``command=True``) ab; ein neues
ohne Fall lässt ihn scheitern.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.ui_world import DEMO_PASSWORD, World, build_world
from tests.unit.app.ha.conftest import FakeTransport

from bindaems.app.ha.commands import HaCommandHandler
from bindaems.app.ha.entities import build_entities
from bindaems.app.ha.publisher import HaPublisher
from bindaems.shared.config import Config, ha_object_id
from bindaems.shared.control import ChargeMode, Intent, VehicleIntent


@dataclass(frozen=True)
class Case:
    """Ein Bedienelement: Nutzlast aus HA, Wirkung, Zustand danach und die Änderung über die API.

    ``published`` ist der Zustand nach dem Befehl (``None``: die Nutzlast selbst); eine Taste hat
    keinen Zustand und keine Änderung über die API (``api`` ``None``).
    """

    payload: str
    effect: Callable[[World], bool]
    published: str | None = None
    # Pfad und Rumpf der Änderung über die API; der Rumpf darf von der Welt abhängen
    api: tuple[str, dict[str, Any] | Callable[[World], dict[str, Any]]] | None = None
    shown: str | None = None
    user: str = "bedienen"  # wer die Änderung über die API macht; die Einstellungen nur ein Admin


class Rejections:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def reject(self, text: str) -> None:
        self.texts.append(text)


def _intent(world: World) -> Intent:
    intent = world.app.intents.cached()
    assert intent is not None
    return intent


def _vehicle(world: World, key: str) -> VehicleIntent:
    return _intent(world).vehicles.get(key, VehicleIntent())


def _thermal_plan_off(world: World) -> dict[str, Any]:
    current = world.app.settings.current()
    thermal = current.settings.learning.thermal.model_copy(update={"enabled": False})
    learning = current.settings.learning.model_copy(update={"thermal": thermal})
    settings = current.settings.model_copy(update={"learning": learning})
    return {"base_version": current.version, "settings": settings.model_dump(mode="json")}


def _cases(cfg: Config) -> dict[str, Case]:
    cases = {
        "regulation": Case(
            "OFF",
            lambda w: _intent(w).control is False,
            api=("/api/control/regulation", {"on": True}),
            shown="ON",
        ),
        "guest_mode": Case(
            "Sofort",
            lambda w: _intent(w).guest.mode is ChargeMode.NOW,
            api=("/api/control/guest", {"mode": "min_pv"}),
            shown="Min+PV",
        ),
        "grid_charge": Case(
            "ON",
            lambda w: w.app.settings.current().settings.planning.grid_charge_allowed,
            api=("/api/plan/grid-charge", {"on": False}),
            shown="OFF",
        ),
        # die Einstellung „Vorheizen und Vorkühlen“ ist ab Werk aus; die API schreibt die ganze
        # Einstellung der aktuellen Version und schaltet sie wieder aus
        "thermal_plan": Case(
            "ON",
            lambda w: w.app.settings.current().settings.learning.thermal.enabled,
            api=("/api/settings", _thermal_plan_off),
            shown="OFF",
            user="admin",
        ),
        "battery_support": Case(
            "OFF",
            lambda w: _intent(w).support.allowed is False,
            api=("/api/control/support", {"allowed": True, "soc_pct": 60}),
            shown="ON",
        ),
        "support_soc": Case(
            "65",
            lambda w: _intent(w).support.soc_pct == 65.0,
            published="65.0",
            api=("/api/control/support", {"allowed": True, "soc_pct": 70}),
            shown="70.0",
        ),
        "replan": Case("PRESS", lambda w: w.app.planning._pending == "manual"),
    }
    for key in cfg.vehicles:
        oid, path = ha_object_id(key), f"/api/control/vehicles/{key}"

        def vehicle(key: str = key) -> Callable[[World], VehicleIntent]:
            return lambda w: _vehicle(w, key)

        get = vehicle()
        # Der Tesla lädt in der Demo schon „Geplant“: HA stellt ihn auf Min+PV, den e-Golf auf
        # „Geplant“
        label, wanted = (
            ("Min+PV", ChargeMode.MIN_PV) if key == "tesla" else ("Geplant", ChargeMode.PLANNED)
        )
        cases |= {
            f"{oid}_mode": Case(
                label,
                lambda w, get=get, wanted=wanted: get(w).mode is wanted,
                api=(f"{path}/mode", {"mode": "now"}),
                shown="Sofort",
            ),
            f"{oid}_target_soc": Case(
                "90",
                lambda w, get=get: get(w).target_soc_pct == 90,
                api=(f"{path}/target", {"target_soc_pct": 75, "departure": None}),
                shown="75",
            ),
            f"{oid}_departure": Case(
                "06:30",
                lambda w, get=get: get(w).departure == "06:30",
                api=(f"{path}/target", {"target_soc_pct": None, "departure": "08:15"}),
                shown="08:15",
            ),
            # die Automatik des Tesla ist in der Demo an, die des e-Golf aus: HA kehrt sie um, die
            # API danach wieder
            f"{oid}_auto_target": Case(
                "OFF" if key == "tesla" else "ON",
                lambda w, key=key: w.app.automation.enabled(key) is (key != "tesla"),
                api=(f"{path}/automation", {"on": key == "tesla"}),
                shown="ON" if key == "tesla" else "OFF",
            ),
            f"{oid}_boost": Case("PRESS", lambda w, get=get: get(w).boost_until is not None),
            f"{oid}_boost_stop": Case("PRESS", lambda w, get=get: get(w).boost_until is None),
        }
    names = {key: vehicle.name for key, vehicle in cfg.vehicles.items()}
    keys = list(cfg.vehicles)
    for index, wallbox in enumerate(cfg.wallboxes):
        # je Wallbox ein anderes Fahrzeug aus HA, über die API das nächste
        ha_key, api_key = keys[index % len(keys)], keys[(index + 1) % len(keys)]
        cases[f"{ha_object_id(wallbox)}_assignment"] = Case(
            names[ha_key],
            lambda w, k=ha_key, b=wallbox: _vehicle(w, k).wallbox == b,
            api=(f"/api/control/wallboxes/{wallbox}/vehicle", {"vehicle": api_key}),
            shown=names[api_key],
        )
    return cases


@pytest.fixture
async def world(cfg: Config, tmp_path) -> AsyncIterator[World]:
    built = await build_world(cfg, tmp_path)
    try:
        yield built
    finally:
        built.close()


async def test_every_command_entity_round_trips(world) -> None:
    app = world.app
    cfg = app._cfg
    mqtt = cfg.homeassistant.mqtt
    assert mqtt is not None
    entities = build_entities(cfg)
    cases = _cases(cfg)
    assert sorted(cases) == sorted(spec.object_id for spec in entities if spec.command)
    rejections = Rejections()
    commands = HaCommandHandler(
        cfg, app.intents, rejections, world.clock, app.planning, app.automation
    )
    transport = FakeTransport([], False, mqtt.publish_interval_s)
    await app.intents.current()  # die gültige Absicht des core im Cache

    def inputs():  # wie der Takt der app: die Werte neu, mit der Absicht nach der Bedienung
        return app._build_ha_inputs(app.intents.cached())

    publisher = HaPublisher(
        mqtt, entities, inputs, transport_factory=lambda: transport, commands=commands
    )
    await publisher._publish_states(transport, force=True)
    client = TestClient(app.app, base_url="https://testserver")
    signed_in: list[Any] = []  # (Benutzer, CSRF-Kopf) der Sitzung des Clients

    async def login(user: str) -> dict[str, str]:
        reply = await asyncio.to_thread(
            client.post, "/api/auth/login", json={"username": user, "password": DEMO_PASSWORD}
        )
        assert reply.status_code == 200, reply.text
        return {"X-CSRF-Token": client.cookies["bindaems_csrf"]}

    lock = asyncio.Lock()
    for spec in entities:
        if not spec.command:
            continue
        oid, case = spec.object_id, cases[spec.object_id]
        published = case.published or case.payload
        stateful = spec.component != "button"
        if stateful:
            assert transport.published_state(oid) != published, oid  # der Befehl ändert etwas
        await publisher._command(transport, lock, oid, case.payload.encode())
        assert rejections.texts == [], oid
        assert case.effect(world), oid
        if not stateful:
            assert transport.published_state(oid) is None, oid
            continue
        assert transport.published_state(oid) == published, oid
        assert case.api is not None and case.shown is not None, oid
        path, body = case.api
        if callable(body):
            body = body(world)
        if not signed_in or signed_in[0][0] != case.user:  # der Client trägt nur eine Sitzung
            signed_in[:] = [(case.user, await login(case.user))]
        csrf = signed_in[0][1]
        reply = await asyncio.to_thread(client.put, path, json=body, headers=csrf)
        assert reply.status_code == 200, (oid, reply.text)
        await publisher._publish_states(transport, force=False)
        assert transport.published_state(oid) == case.shown, oid
