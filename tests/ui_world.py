"""Demo-Welt für das UI: app und core mit festen Werten, ohne Geräte und ohne Internet.

Die Uhr steht auf dem 09.10.2026, 10:00 Ortszeit (Mittagssituation mit PV-Überschuss). Alle
Netzzugriffe beantwortet ein ``respx``-Router mit Aufnahmen und einer Ersatz-InfluxDB; jeder
andere Zugriff ist ein Fehler. Nutzer: Vertragstest (``tests/integration/test_ui_contract.py``)
und Demo-Backend (``tests/e2e/ui_server.py``). Testwerkzeug: private Attribute und SQL sind hier
erlaubt.
"""

from __future__ import annotations

import asyncio
import math
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import respx
from pydantic import SecretStr
from sqlalchemy import update
from tests.app_helpers import (
    T_APP,
    mock_all_sources,
    set_feed_in,
    set_grid_usage,
    set_wallbox_names,
)
from tests.helpers import FakeSink

from bindaems.app.consumers.service import ConsumerInput
from bindaems.app.consumers.values import ENTITY_UNITS_QUERY, HaRef
from bindaems.app.db.schema import user_table
from bindaems.app.forecast.openmeteo import OPEN_METEO_URL
from bindaems.app.runtime import AppRuntime
from bindaems.core.adapters.base import AdapterHealth
from bindaems.core.runtime import CoreRuntime
from bindaems.shared.config import Config, Secrets
from bindaems.shared.domain import SignalKind
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.timeutil import Clock, ManualClock

T_DEMO = T_APP  # 09.10.2026, 10:00 Ortszeit
DEMO_PASSWORD = "demo-passwort-1"
DEMO_TOTP_SECRET = "JBSWY3DPEHPK3PXP"
TOKEN = "demo-token-" + "x" * 21
OPEN_METEO_FIXTURE = Path("tests/fixtures/openmeteo_sued_2026-10-09.json")

# Mittagssituation: PV 3,2 kW, Netzbezug 0,4 kW, Akku lädt 1 kW, Wall Connector 1,38 kW
PV_W = 3200.0
HOUSE_W = 1220.0
BATTERY_AC_W = 1000.0  # = Σ AC-In − Σ AC-Out der Multis
WALLBOX_W = {"evcs": 0.0, "twc": 1380.0}
GRID_SHARES = (0.375, 0.3, 0.325)
VEBUS_IN_W = (450.0, 420.0, 430.0)
VEBUS_OUT_W = (100.0, 100.0, 100.0)
VOLTAGES_V = (231.0, 229.5, 230.4)
FIXED_SIGNALS = {
    "battery.power_w": 980.0,  # DC-seitig, etwas weniger als AC (Verluste)
    "battery.soc_pct": 55.0,
    "load.obergeschoss.power_w": 600.0,
    "load.buero.power_w": 150.0,
}
# Einstellungen der Anlage wie erwartet: die Selbstprüfung des core meldet alles in Ordnung
VICTRON_STATE = {
    "vebus.phases": 3.0,
    "ess.hub4_mode": 1.0,
    "ess.batterylife_state": 10.0,
    "ess.min_soc_pct": 20.0,
    "dess.mode": 0.0,
    "wallbox.evcs.gx_mode": 0.0,  # Modus, wie ihn der Cerbo liest
    **{f"ess.schedule.{k}.day": -7.0 for k in range(5)},  # negativ: Ladefenster aus
}
TESLA_SOURCE = "tessie:tesla"  # nicht verbunden: der Ladestand gilt als veraltet
# Vorlauf vor T_DEMO: die Selbstprüfung verlangt seit 30 s aktuelle Messwerte
WARMUP = timedelta(seconds=60)

# HA-Datenbank: Messung je Entität (measurement_attr: entity_id, wie beim Betreiber) und je
# Einheit (HA-Standard); Wert und Einheit je Entität
HA_BY_ENTITY = {
    ("sensor", "kueche_power"): (230.0, "W"),
    ("sensor", "wohnzimmer_temperatur"): (21.5, "°C"),
}
HA_BY_UNIT = {("sensor", "waschmaschine_power"): (2.4, "W")}
MISSING_POINT = 2  # der dritte Punkt jeder Verlaufsreihe fehlt (Lücke)

_MEAN = re.compile(
    r'SELECT mean\("(?P<field>[^"]+)"\) AS "v" FROM "[^"]+"\."(?P<measurement>[^"]+)" '
    r"WHERE .*time >= (?P<start>\d+)ms AND time < (?P<end>\d+)ms "
    r"GROUP BY time\((?P<step>\d+)s\)"
)
_ID_TAG = re.compile(r"\"id\"='(?P<id>[^']*)'")
# Leistung je Reihe in W: Grundwert und Anteil, der mit der Sonne steigt
POWER_SHAPES = {
    "grid": (500.0, -1400.0),
    "pv_total": (0.0, 3400.0),
    "battery": (-250.0, 1300.0),
    "house_load": (900.0, 450.0),
    "consumption": (1100.0, 1800.0),
    "obergeschoss": (450.0, 300.0),
    "buero": (120.0, 60.0),
}
_LAST = re.compile(r'^SELECT last\("value"\) AS "v", ')
# Energie seit Mitternacht: Minutenmittel des core und HA-Werte (heute und der letzte davor)
_CORE_ENERGY = re.compile(
    r'^SELECT mean\("p_w"\) AS "w" FROM "raw"\."power" .*time >= (?P<start>\d+)ms '
    r"AND time < (?P<end>\d+)ms"
)
_SOURCE_ID = re.compile(r"\"source\"='(?P<source>[^']*)' AND \"id\"='(?P<id>[^']*)'")
_HA_ENERGY = re.compile(
    r'^SELECT (?P<agg>mean|last)\("value"\) AS "w" FROM .*time >= (?P<start>\d+)ms '
    r"AND time < (?P<end>\d+)ms"
)
HA_ENERGY_STEP_MS = 900_000  # HA schreibt bei Änderung; hier alle 15 min
_ENTITY = re.compile(r"\"domain\"='(?P<domain>[^']*)' AND \"entity_id\"='(?P<object_id>[^']*)'")
_SHOW_SERIES = re.compile(r'^SHOW SERIES FROM "W","kW"$')


def measurements(tick: int) -> dict[str, float]:
    """Messwerte für Takt ``tick``: PV und Haus schwanken um wenige Prozent, das Netz folgt."""
    pv = PV_W * (1 + 0.03 * math.sin(0.7 * tick))
    house = HOUSE_W * (1 + 0.04 * math.sin(0.5 * tick))
    # Hauslast = Netz + PV − Akku(AC) − Wallboxen (Ableitung des core)
    grid = house + sum(WALLBOX_W.values()) + BATTERY_AC_W - pv
    values = {
        "grid.power_w": grid,
        "pv.huawei.power_w": pv,
        **{f"wallbox.{name}.power_w": power for name, power in WALLBOX_W.items()},
        **FIXED_SIGNALS,
    }
    for n, (share, ac_in, ac_out, volts) in enumerate(
        zip(GRID_SHARES, VEBUS_IN_W, VEBUS_OUT_W, VOLTAGES_V, strict=True), start=1
    ):
        values[f"grid.l{n}.power_w"] = grid * share
        values[f"grid.l{n}.voltage_v"] = volts
        values[f"pv.huawei.l{n}.power_w"] = pv / 3
        values[f"vebus.l{n}.ac_in_power_w"] = ac_in
        values[f"vebus.l{n}.ac_out_power_w"] = ac_out
    return {signal: round(value, 1) for signal, value in values.items()}


def _ledger_messages() -> list[dict[str, Any]]:
    """Vier ``slot_flows`` des core für 09:00–10:00 Ortszeit."""
    messages: list[dict[str, Any]] = []
    imported, exported = 4321.0, 1234.0
    for k in range(4):
        flows = {
            "pv>house": 250.0,
            "pv>battery": 150.0 + 50.0 * k,
            "pv>grid": 40.0 + 10.0 * k,
            "battery>house": 0.0,
            "grid>house": 100.0 - 20.0 * k,
            "grid>wb:twc": 345.0,
        }
        grid_in = round((flows["grid>house"] + flows["grid>wb:twc"]) / 1000, 3)
        grid_out = round(flows["pv>grid"] / 1000, 3)
        start = T_DEMO - timedelta(hours=1) + k * timedelta(minutes=15)
        messages.append(
            {
                "slot_start": start.isoformat(),
                "covered_s": 900.0,
                "flows_wh": flows,
                "counters": {
                    "grid.energy_import_kwh": [imported, round(imported + grid_in, 3)],
                    "grid.energy_export_kwh": [exported, round(exported + grid_out, 3)],
                },
            }
        )
        imported, exported = round(imported + grid_in, 3), round(exported + grid_out, 3)
    return messages


def _curve(measurement: str, ms: int, series_id: str) -> float:
    """Deterministische Tageskurve je Messgröße und Reihe (Maximum der Sonne um 12:00 UTC)."""
    hour = (ms / 3_600_000) % 24
    sun = math.sin(2 * math.pi * (hour - 6) / 24)
    if measurement == "soc":
        return round(55 + 30 * sun if series_id == "battery" else 60 + 10 * sun, 1)
    if measurement == "price":
        return round(14 + 5 * math.sin(2 * math.pi * (hour - 12) / 24), 2)  # Abends teuer
    if measurement == "forecast":
        return round(max(0.0, 3.5 * sun), 3)  # kW
    base, solar = POWER_SHAPES.get(series_id, (150.0, 700.0))
    return round(base + solar * max(0.0, sun), 1)  # Leistung in W


def _influx_result(series: list[dict[str, Any]]) -> httpx.Response:
    result: dict[str, Any] = {"statement_id": 0}
    if series:
        result["series"] = series
    return httpx.Response(200, json={"results": [result]})


def _ha_power_entities() -> dict[tuple[str, str], tuple[str, float]]:
    """HA-Leistungsentitäten mit dem Namen ihrer Messung und dem Wert in deren Einheit."""
    found = {
        entity: (f"{entity[0]}.{entity[1]}", value)
        for entity, (value, unit) in HA_BY_ENTITY.items()
        if unit in ("W", "kW")
    }
    found |= {entity: (unit, value) for entity, (value, unit) in HA_BY_UNIT.items()}
    return found


def _energy_influx(query: str) -> httpx.Response | None:
    """Antworten für ``consumers.energy``: Minutenmittel des core, HA-Werte bei Änderung."""
    if core := _CORE_ENERGY.search(query):
        start, end = int(core["start"]), int(core["end"])
        first = -(-start // 60_000) * 60_000
        return _influx_result(
            [
                {
                    "name": "power",
                    "tags": {"source": source, "id": id_},
                    "columns": ["time", "w"],
                    "values": [[ms, _curve("power", ms, id_)] for ms in range(first, end, 60_000)],
                }
                for source, id_ in _SOURCE_ID.findall(query)
            ]
        )
    if ha := _HA_ENERGY.search(query):
        start, end = int(ha["start"]), int(ha["end"])
        series = []
        for domain, object_id in _ENTITY.findall(query):
            found = _ha_power_entities().get((domain, object_id))
            if found is None:
                continue
            name, value = found
            if ha["agg"] == "last":
                values = [[end - 60_000, value]]
            else:
                first = -(-start // HA_ENERGY_STEP_MS) * HA_ENERGY_STEP_MS
                values = [
                    [ms, round(value * (1 + 0.3 * math.sin(ms / 7_200_000)), 1)]
                    for ms in range(first, end, HA_ENERGY_STEP_MS)
                ]
            tags = {"domain": domain, "entity_id": object_id}
            series.append({"name": name, "tags": tags, "columns": ["time", "w"], "values": values})
        return _influx_result(series)
    return None


def _influx(request: httpx.Request) -> httpx.Response:
    """Ersatz für InfluxDB ``/query``: Verlauf, Energie, letzte HA-Werte und HA-Reihen."""
    query = request.url.params.get("q", "")
    if (energy := _energy_influx(query)) is not None:
        return energy
    if mean := _MEAN.search(query):
        start, end = int(mean["start"]), int(mean["end"])
        interval = max(int(mean["step"]), 900) * 1000
        tag = _ID_TAG.search(query)
        first = -(-start // interval) * interval
        values = [
            [ms, _curve(mean["measurement"], ms, tag["id"] if tag else "")]
            for index, ms in enumerate(range(first, end, interval))
            if index != MISSING_POINT
        ]
        columns = ["time", "v"]
        return _influx_result(
            [{"name": mean["measurement"], "columns": columns, "values": values}] if values else []
        )
    if _LAST.search(query):
        series = []
        for domain, object_id in _ENTITY.findall(query):
            tags = {"domain": domain, "entity_id": object_id}
            if (domain, object_id) in HA_BY_ENTITY:
                value, unit = HA_BY_ENTITY[(domain, object_id)]
                name, recorded = f"{domain}.{object_id}", unit
            elif (domain, object_id) in HA_BY_UNIT:
                value, unit = HA_BY_UNIT[(domain, object_id)]
                name, recorded = unit, None
            else:
                continue
            row = [epoch_ms(T_DEMO), value, recorded]
            series.append(
                {"name": name, "tags": tags, "columns": ["time", "v", "u"], "values": [row]}
            )
        return _influx_result(series)
    if _SHOW_SERIES.search(query):
        keys = [[f"{u},domain={d},entity_id={o}"] for (d, o), (_, u) in sorted(HA_BY_UNIT.items())]
        return _influx_result([{"columns": ["key"], "values": keys}])
    if query == ENTITY_UNITS_QUERY:
        series = [
            {
                "name": f"{domain}.{object_id}",
                "tags": {"domain": domain, "entity_id": object_id},
                "columns": ["time", "u"],
                "values": [[epoch_ms(T_DEMO), unit]],
            }
            for (domain, object_id), (_, unit) in sorted(HA_BY_ENTITY.items())
        ]
        return _influx_result(series)
    return httpx.Response(400, json={"error": f"Demo-InfluxDB kennt die Abfrage nicht: {query}"})


def _router(cfg: Config) -> respx.MockRouter:
    router = respx.mock(assert_all_mocked=True, assert_all_called=False)
    mock_all_sources(router)
    router.get(OPEN_METEO_URL).respond(200, text=OPEN_METEO_FIXTURE.read_text())
    router.get(cfg.influxdb.url.rstrip("/") + "/query").mock(side_effect=_influx)
    return router


class _DemoVictron:
    """Ersatz für den Victron-Adapter: meldet sich verbunden, die Werte setzt ``step_live``."""

    name = "victron"

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def run(self) -> None:
        await asyncio.Event().wait()

    def health(self) -> AdapterHealth:
        return AdapterHealth(name=self.name, connected=True, last_ok=self._clock.now())


def _cycle(core: CoreRuntime, tick: int) -> None:
    for signal, value in measurements(tick).items():
        core.store.update(signal, value, source="victron", kind=SignalKind.MEASUREMENT)
    core.cycle_once()


class World:
    def __init__(
        self, app: AppRuntime, core: CoreRuntime, clock: ManualClock, router: respx.MockRouter
    ) -> None:
        self.app = app
        self.core = core
        self.clock = clock
        self._router = router

    def step_live(self, tick: int = 0) -> None:
        """Neue Messwerte, ein Zyklus des core und dessen Stand in der app (wie der Stream)."""
        _cycle(self.core, tick)
        live = self.app.live
        live.connected = True
        live.state = self.core.state()
        live.health = self.core.health().model_dump(mode="json")
        live.alarms = list(live.health["alarms"])
        live.updated_at = self.clock.now()
        live.publish({"type": "state", "data": live.state})

    def close(self) -> None:
        self._router.stop(quiet=True)
        self.app.engine.dispose()


def _demo_config(cfg: Config, data_dir: Path, ui_dir: Path | None) -> Config:
    data_dir.mkdir(parents=True, exist_ok=True)
    app = cfg.app.model_copy(
        update={
            "data_dir": data_dir,
            "backup_dir": data_dir / "backup",
            "ui_dir": ui_dir,
            "cookie_secure": False,
            "core_url": "http://127.0.0.1:9",  # nie benutzt: den Stream ersetzt step_live
        }
    )
    return cfg.model_copy(update={"app": app})


def _build_core(cfg: Config, secrets: Secrets, clock: ManualClock) -> CoreRuntime:
    core = CoreRuntime(
        cfg,
        secrets,
        clock=clock,
        adapters=[_DemoVictron(clock)],
        sink=FakeSink(),
        serve_api=False,
        timer=lambda: 0.0,  # stabile Zykluszeit
    )
    store = core.store
    store.register_source("victron", 5.0, activity_based=True)
    store.set_connected("victron", True)
    for signal, value in VICTRON_STATE.items():
        store.update(signal, value, source="victron", kind=SignalKind.STATE)
    store.register_source(TESLA_SOURCE, 900.0)
    store.update("vehicle.tesla.soc_pct", 70.0, source=TESLA_SOURCE, kind=SignalKind.MEASUREMENT)
    _cycle(core, 0)
    clock.advance(WARMUP)
    return core


def _create_users(app: AppRuntime) -> None:
    for username, role in (("admin", "admin"), ("sicher", "admin"), ("gast", "viewer")):
        app.auth.create_user(username, DEMO_PASSWORD, role, actor="cli", source="cli")
    with app.engine.begin() as conn:
        conn.execute(
            update(user_table)
            .where(user_table.c.username == "sicher")
            .values(totp_secret=DEMO_TOTP_SECRET, totp_enabled=True)
        )


def _create_consumers(app: AppRuntime) -> list[HaRef]:
    def create(data: ConsumerInput) -> int:
        return app.consumers.create(data, actor="admin", source="ui").id

    upstairs = create(
        ConsumerInput(
            name="Obergeschoss",
            color="#4e79a7",
            source_kind="core",
            power_ref="load.obergeschoss.power_w",
        )
    )
    create(
        ConsumerInput(
            name="Büro",
            color="#f28e2b",
            source_kind="core",
            power_ref="load.buero.power_w",
            parent_id=upstairs,
        )
    )
    create(
        ConsumerInput(
            name="Küche",
            color="#59a14f",
            source_kind="ha",
            power_ref="sensor.kueche_power",
            power_unit="W",
        )
    )
    return [HaRef.from_consumer(c) for c in app.consumers.list() if c.source_kind == "ha"]


async def build_world(cfg: Config, data_dir: Path, *, ui_dir: Path | None = None) -> World:
    clock = ManualClock(T_DEMO - WARMUP)  # der core läuft vor, danach steht die Uhr auf T_DEMO
    local = _demo_config(cfg, data_dir, ui_dir)
    secrets = Secrets(internal_token=SecretStr(TOKEN))
    router = _router(local)
    router.start()
    try:
        core = _build_core(local, secrets, clock)
        app = AppRuntime(local, secrets, clock=clock, serve=False)
        app._sink.bind(asyncio.get_running_loop())  # wie AppRuntime.run
        world = World(app, core, clock, router)
        _create_users(app)
        # drei Versionen; Netzverlust, Elektrizitätsabgabe und Förderbeitrag bleiben leer
        set_grid_usage(app.settings, 8.11)
        set_feed_in(app.settings, {"2026-09": 7.3})
        set_wallbox_names(app.settings, {"twc": "Garage"})  # die EVCS behält ihren Typnamen
        refs = _create_consumers(app)
        assert app.ha_values is not None
        await app.ha_values.refresh(refs)
        await app.energy.refresh(app.consumers.list())
        await app.pipeline.refresh()
        await app.forecast.refresh()
        world.step_live()
        for message in _ledger_messages():
            await app.ledger.handle_stream_message(message)
    except BaseException:
        router.stop(quiet=True)
        raise
    return world
