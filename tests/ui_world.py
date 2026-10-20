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
from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx
import respx
from pydantic import SecretStr
from sqlalchemy import insert, select, update
from tests.app_helpers import (
    T_APP,
    influx_q,
    mock_all_sources,
    set_feed_in,
    set_tariff,
    set_wallbox_names,
    with_grid_usage,
)
from tests.helpers import FakeSink
from tests.ui_history import (
    DAYS,
    FIRST_DAY,
    LAST_DAY,
    SESSIONS,
    HistorySlot,
    counter_readings,
    history_slots,
    observations,
)

from bindaems.app.charging.tracker import WallboxSeen
from bindaems.app.consumers.service import ConsumerInput
from bindaems.app.consumers.values import ENTITY_UNITS_QUERY, HaRef
from bindaems.app.db.schema import (
    charging_session,
    model_registry,
    planned_charge,
    user_table,
)
from bindaems.app.forecast.archive import HorizonClass, Kind, RecordValue, actuals
from bindaems.app.forecast.openmeteo import OPEN_METEO_URL
from bindaems.app.learning.backtest import BacktestResult, WeekResult
from bindaems.app.learning.features import TrainingSet, feature_names, local_midnight
from bindaems.app.learning.ha_sensors import _CANDIDATES_QUERY
from bindaems.app.learning.model import MIN_ROWS, TrainedModel, train
from bindaems.app.learning.service import TrainingResult
from bindaems.app.ledger.checks import EXPORT_COUNTER, IMPORT_COUNTER
from bindaems.app.planning.process import solve_inline
from bindaems.app.planning.service import plan_once
from bindaems.app.prices.store import SlotRow
from bindaems.app.runtime import AppRuntime
from bindaems.app.tariff.calc import import_price
from bindaems.core.adapters.base import AdapterHealth
from bindaems.core.adapters.homeassistant import SOURCE as HA_SOURCE
from bindaems.core.api.app import create_api
from bindaems.core.runtime import CoreRuntime
from bindaems.core.safety.types import Outcome
from bindaems.core.watchdog_client.status import WATCHDOG_VERSION
from bindaems.shared.config import Config, Secrets, soc_signal
from bindaems.shared.control import ChargeMode, CommandKind, Intent, VehicleIntent
from bindaems.shared.decisions import NO_VERDICT, Decision, DecisionResult, decision_text
from bindaems.shared.domain import SignalKind, SlotFlows
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.plan import Plan
from bindaems.shared.settings import MonthlyFeeComponent, PowerComponent, TariffSettings
from bindaems.shared.timeutil import LOCAL_TZ, Clock, ManualClock, local_day_slots

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
# Einstellungen der Anlage wie erwartet: die Selbstprüfung des core meldet alles in Ordnung.
# Der Watchdog am Cerbo ist installiert und wartet auf einen stabilen Heartbeat.
VICTRON_STATE: dict[str, float | str] = {
    "vebus.phases": 3.0,
    "ess.hub4_mode": 1.0,
    "ess.batterylife_state": 10.0,
    "ess.min_soc_pct": 20.0,
    "dess.mode": 0.0,
    "vebus.mode": 3.0,  # Multi auf „Ein“
    "settings.cgwacs.acinputlimit": 20.0,  # Importlimit am Cerbo wie konfiguriert
    "wallbox.evcs.gx_mode": 0.0,  # Modus, wie ihn der Cerbo liest
    **{f"ess.schedule.{k}.day": -7.0 for k in range(5)},  # negativ: Ladefenster aus
    "watchdog.state": 0.0,  # 0 = wartet
    "watchdog.version": WATCHDOG_VERSION,
    "watchdog.trip_count": 0.0,
    "watchdog.last_trip_time": 0.0,  # 0 = nie
    "watchdog.last_trip_failures": 0.0,
    "watchdog.timeout_s": 90.0,
}
TESLA_SOURCE = "tessie:tesla"  # nicht verbunden: der Ladestand gilt als veraltet
# Geplantes Laden (Plan 4b): Der Tesla steckt am Wall Connector und lädt „Geplant“ (Ziel 60 %,
# Abfahrt 17:30, im Horizont des Plans); sein Ladestand ist angenommen, denn Tessie ist nicht
# verbunden. Der e-Golf steckt an der EVCS (verbunden, lädt nicht); Home Assistant hat seinen
# Ladestand beim Anstecken gemeldet, seither hat die EVCS 0,4 kWh gezählt: Der core schätzt ihn.
PLUGGED = {"wallbox.twc.vehicle_connected": True, "wallbox.evcs.status": 1.0}
TWC_PHASE_A = 2.0  # 3 × 2 A ≈ die 1,38 kW des Wall Connectors
EGOLF_SOC_PCT = 48.0
EVCS_TOTAL_KWH = 1520.0  # Zähler der EVCS beim Anstecken
EVCS_COUNTED_KWH = 0.4  # seither gezählt, bis T_DEMO
DEMO_TARGET_PCT = 60
DEMO_DEPARTURE = "17:30"
# Vorlauf vor T_DEMO: die Selbstprüfung verlangt seit 30 s aktuelle Messwerte
WARMUP = timedelta(seconds=60)

# Entscheidungen der Demo (Tabelle ``decision`` der app): je Aktor zwei, darunter eine
# ``deferred`` und genau eine ``foreign``; die jüngste je Aktor steht in „Warum?“
_INPUTS = {"soc_pct": 55.0, "grid_l1_a": 1.2}


def _decision(
    id_: str,
    minutes_ago: int,
    actuator: str,
    label: str,
    kind: CommandKind,
    value: float | bool,
    result: DecisionResult,
    reasons: tuple[str, ...],
    *,
    outcome: str = Outcome.ACCEPTED,
    expected: float | None = None,
    dry_run: bool = True,
) -> Decision:
    detail = f"erwartet {expected:g} A" if expected is not None else ""
    return Decision(
        id=id_,
        ts=T_DEMO - timedelta(minutes=minutes_ago),
        actuator=actuator,
        kind=kind,
        requested=expected if expected is not None else value,
        applied=value if outcome != Outcome.DEFERRED else None,
        dry_run=dry_run,
        outcome=str(outcome),
        result=result,
        codes=reasons,
        text=decision_text(label, kind, value, result, detail=detail, reasons=reasons),
        inputs=_INPUTS,
    )


DEMO_DECISIONS = [
    _decision(
        "demo-1",
        3,
        "victron",
        "Victron (ESS)",
        CommandKind.GRID_SETPOINT,
        50.0,
        DecisionResult.DRY_RUN,
        ("pv_surplus",),
    ),
    _decision(
        "demo-2",
        45,
        "victron",
        "Victron (ESS)",
        CommandKind.GRID_SETPOINT,
        120.0,
        DecisionResult.DRY_RUN,
        ("battery_comp",),
    ),
    _decision(
        "demo-3",
        5,
        "wallbox.evcs",
        "EVCS „evcs“",
        CommandKind.CURRENT,
        8.0,
        DecisionResult.DRY_RUN,
        ("mode_pv", "pv_surplus"),
    ),
    _decision(
        "demo-4",
        40,
        "wallbox.evcs",
        "EVCS „evcs“",
        CommandKind.CURRENT,
        10.0,
        DecisionResult.FOREIGN,
        ("foreign_change",),
        outcome=NO_VERDICT,
        expected=8.0,
        dry_run=False,
    ),
    _decision(
        "demo-5",
        8,
        "vehicle.tesla",
        "Tesla Model 3",
        CommandKind.CURRENT,
        7.0,
        DecisionResult.DEFERRED,
        ("mode_min_pv", "pv_surplus"),
        outcome=Outcome.DEFERRED,
    ),
    _decision(
        "demo-6",
        90,
        "vehicle.tesla",
        "Tesla Model 3",
        CommandKind.CHARGING,
        True,
        DecisionResult.DRY_RUN,
        ("mode_min_pv", "wake"),
    ),
]

# HA-Datenbank: Messung je Entität (measurement_attr: entity_id, wie beim Betreiber) und je
# Einheit (HA-Standard); Wert und Einheit je Entität
HA_BY_ENTITY = {
    ("sensor", "kueche_power"): (230.0, "W"),
    ("sensor", "wohnzimmer_temperatur"): (21.5, "°C"),
}
HA_BY_UNIT = {("sensor", "waschmaschine_power"): (2.4, "W")}
# Sensoren zur Auswahl für das Lastmodell (letzter Wert und Einheit je Entität, Schema je Entität);
# ohne Einheit ist der Wert ein Zustand, den HA als Zahl schreibt (an = 1)
HA_SENSOR_CANDIDATES = {
    ("sensor", "aussentemperatur"): (8.2, "°C"),
    ("sensor", "wohnzimmer_temperatur"): (21.5, "°C"),
    ("binary_sensor", "anwesenheit"): (1.0, None),
}
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
        values[f"wallbox.twc.l{n}.current_a"] = TWC_PHASE_A
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
    query = influx_q(request)
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
    if query == _CANDIDATES_QUERY:
        return _influx_result(
            [
                {
                    "name": f"{domain}.{object_id}",
                    "tags": {"domain": domain, "entity_id": object_id},
                    "columns": ["time", "v", "u"],
                    "values": [[epoch_ms(T_DEMO), value, unit]],
                }
                for (domain, object_id), (value, unit) in sorted(HA_SENSOR_CANDIDATES.items())
            ]
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
    router.post(cfg.influxdb.url.rstrip("/") + "/query").mock(side_effect=_influx)
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


def _mount_core_api(
    router: respx.MockRouter, cfg: Config, core: CoreRuntime, secrets: Secrets
) -> None:
    """Leitet die Aufrufe der app an die interne API des core (``app.core_url``) in den
    echten ``CoreRuntime`` der Demo, ohne Netz: so wirken Bedienung und Quittierung wirklich."""
    transport = httpx.ASGITransport(app=create_api(core, secrets.internal_token))
    url = httpx.URL(cfg.app.core_url)

    async def forward(request: httpx.Request) -> httpx.Response:
        await request.aread()
        reply = await transport.handle_async_request(request)
        await reply.aread()
        return httpx.Response(reply.status_code, headers=reply.headers, content=reply.content)

    router.route(host=url.host, port=url.port).mock(side_effect=forward)


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
    core = cfg.core.model_copy(update={"data_dir": data_dir / "core"})  # Absicht der Demo
    return cfg.model_copy(update={"app": app, "core": core})


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
    for signal, value in {**VICTRON_STATE, **PLUGGED}.items():
        store.update(signal, value, source="victron", kind=SignalKind.STATE)
    store.register_source(TESLA_SOURCE, 900.0)
    store.update("vehicle.tesla.soc_pct", 70.0, source=TESLA_SOURCE, kind=SignalKind.MEASUREMENT)
    store.register_source(HA_SOURCE, None, invalidate_on_reconnect=True)
    store.set_connected(HA_SOURCE, True)
    store.update(soc_signal("egolf"), EGOLF_SOC_PCT, source=HA_SOURCE, kind=SignalKind.MEASUREMENT)
    store.update(
        "wallbox.evcs.total_kwh", EVCS_TOTAL_KWH, source="victron", kind=SignalKind.MEASUREMENT
    )
    _cycle(core, 0)
    clock.advance(WARMUP)
    return core


def _create_users(app: AppRuntime) -> None:
    for username, role in (
        ("admin", "admin"),
        ("sicher", "admin"),
        ("bedienen", "operator"),
        ("gast", "viewer"),
    ):
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


def _rate_forecasts(app: AppRuntime) -> None:
    """Archivierte Prognosen für die vier Slots der Abrechnung, damit die Güte Zeilen hat."""
    slots = [T_DEMO - timedelta(hours=1) + k * timedelta(minutes=15) for k in range(4)]
    near = T_DEMO - timedelta(hours=4)  # Vorlauf 3 bis 3,75 h: Klasse 0–6 h
    far = T_DEMO - timedelta(hours=18)  # Vorlauf 17 bis 17,75 h: Klasse 6–24 h
    app.forecast_archive.record(
        "pv", near, {slot: RecordValue(2000.0, 1800.0, "clear") for slot in slots}
    )
    app.forecast_archive.record("load", near, {slot: RecordValue(1300.0, 1300.0) for slot in slots})
    app.forecast_archive.record("load", far, {slot: RecordValue(1100.0, 1100.0) for slot in slots})
    app.forecast_quality.update(T_DEMO.astimezone(LOCAL_TZ).date())


# Lastmodell der Demo (Plan 5a): ein Modell vom Morgen des 09.10. mit Backtest und Schattenbetrieb
# an den 12 Tagen davor. Das Modell ist klein und mit erfundenen Zeilen angelernt; angezeigt werden
# die festen Anteile in ``MODEL_IMPORTANCE``, damit die Vertragsdateien auf jedem Rechner gleich
# sind.
SHADOW_DAYS = 12
SHADOW_WINDOW = (time(7, 0), 8)  # erster Slot (Ortszeit) und Zahl der Slots je Tag
# Fehlerbreite (Anteil der Last) von Profil und Modell je Vorlaufklasse; das Modell ist etwa 30 %
# genauer
SHADOW_SCALE: dict[HorizonClass, tuple[float, float]] = {
    "h0_6": (0.20, 0.14),
    "h6_24": (0.25, 0.175),
    "h24": (0.30, 0.21),
}
SHADOW_LEAD_H: dict[HorizonClass, int] = {"h0_6": 3, "h6_24": 15, "h24": 30}
DEMO_SENSORS = ["sensor.aussentemperatur", "sensor.wohnzimmer_temperatur"]
MODEL_TRAINED_AT = datetime(2026, 10, 9, 1, 15, tzinfo=UTC)  # 03:15 Ortszeit
MODEL_SAMPLES = 5376  # 56 Tage zu 96 Viertelstunden
MODEL_IMPORTANCE = {
    "temp_c": 0.28,
    "profile_w": 0.19,
    "lag_1d_w": 0.16,
    "minute_of_day": 0.12,
    "recent_w": 0.08,
    "temp_day_c": 0.05,
    "lead_h": 0.04,
    "ha:sensor.aussentemperatur": 0.03,
    "lag_7d_w": 0.02,
    "weekday": 0.015,
    "day_type": 0.015,
    "month": 0.0,
    "holiday": 0.0,
    "ha:sensor.wohnzimmer_temperatur": 0.0,  # ohne Werte: „nicht im Modell“
}
# (Modell, Profil) in W je Woche und Vorlaufklasse des Backtests
BACKTEST_MAE: dict[HorizonClass, tuple[float, float]] = {
    "h0_6": (176.0, 221.0),
    "h6_24": (203.0, 247.0),
    "h24": (231.0, 268.0),
}


class _RoundedModel:
    """Das geladene Modell, dessen Vorhersagen auf 0,1 W gerundet sind: Die letzten Stellen von
    LightGBM hängen von Rechner und Wheel ab und dürften nicht in die Vertragsdateien."""

    def __init__(self, model: TrainedModel) -> None:
        self._model = model
        self.features = model.features
        self.max_w = model.max_w
        self.text = model.text

    def predict(self, rows: Sequence[Sequence[float]]) -> list[float]:
        return [round(value, 1) for value in self._model.predict(rows)]

    def importance(self) -> dict[str, float]:
        return self._model.importance()


def _demo_training_set(names: tuple[str, ...]) -> TrainingSet:
    """Erfundene Zeilen, nur damit ein echtes Modell entsteht: Die Last folgt der Uhrzeit
    (Spitzen am Morgen und Abend), die übrigen Merkmale sind Beiwerk; der Sensor ohne Werte bleibt
    leer."""
    rows: list[list[float]] = []
    targets: list[float] = []
    for index in range(MIN_ROWS + 200):
        minute = float(index % 96 * 15)
        temp = 4.0 + 12.0 * math.sin(index * 0.7311)  # unabhängig von der Uhrzeit
        row = dict.fromkeys(names, float("nan"))
        row |= {"lead_h": float(index % 72), "minute_of_day": minute, "temp_c": temp}
        row |= {"ha:sensor.aussentemperatur": temp + 1.0, "weekday": float(index % 7)}
        rows.append([row[name] for name in names])
        hour = minute / 60.0
        morning = 450.0 * math.exp(-(((hour - 7.5) / 1.5) ** 2))
        evening = 700.0 * math.exp(-(((hour - 19.0) / 2.0) ** 2))
        targets.append(550.0 + morning + evening)
    return TrainingSet(rows, targets, [], [])


def _shadow_rows(app: AppRuntime) -> None:
    """Schattenprognosen von Profil und Modell für zwei Stunden am Morgen der letzten Tage, mit
    festen Abweichungen vom Ist; die ausgegebene Last entspricht dem Profil."""
    today = T_DEMO.astimezone(LOCAL_TZ).date()
    first_slot, count = SHADOW_WINDOW
    for back in range(SHADOW_DAYS, 0, -1):
        day = today - timedelta(days=back)
        start = datetime.combine(day, first_slot, tzinfo=LOCAL_TZ).astimezone(UTC)
        slots = [start + k * timedelta(minutes=15) for k in range(count)]
        known = actuals(app.ledger, slots[0], slots[-1] + timedelta(minutes=15))
        for horizon, (profile_scale, model_scale) in SHADOW_SCALE.items():
            issued = start - timedelta(hours=SHADOW_LEAD_H[horizon])
            shadows: tuple[tuple[Kind, float], ...] = (
                ("load_profile", profile_scale),
                ("load_model", model_scale),
            )
            for kind, scale in shadows:
                values = {}
                for k, slot in enumerate(slots):
                    wave = ((back * 5 + k * 3) % 7 - 3) / 3  # −1 bis 1, je Tag und Slot anders
                    power = known[slot].house_w * (1.0 + 0.04 + scale * wave)
                    values[slot] = RecordValue(power, power)
                app.forecast_archive.record(kind, issued, values)
                if kind == "load_profile":  # ausgegeben wurde das Profil
                    app.forecast_archive.record("load", issued, values)
        app.forecast_quality.update(day)


def _demo_backtest() -> BacktestResult:
    today = T_DEMO.astimezone(LOCAL_TZ).date()
    weeks = []
    for weeks_back in (3, 2, 1):
        origin = local_midnight(today - timedelta(days=7 * weeks_back))
        end = local_midnight(today - timedelta(days=7 * (weeks_back - 1)))
        for horizon, (model, profile) in BACKTEST_MAE.items():
            factor = 1.0 + 0.03 * (weeks_back - 2)  # die Wochen unterscheiden sich leicht
            weeks.append(WeekResult(origin, end, horizon, 840, model * factor, profile * factor))
    return BacktestResult(weeks, {})


async def _learning(app: AppRuntime) -> None:
    """Geladenes Lastmodell mit Backtest, Schattenbetrieb und Aktivierung (Modell aktiv); zwei
    Sensoren aus HA sind gewählt, einer davon liefert dem Modell Werte."""
    settings = app.settings.current()
    learning = settings.settings.learning.model_copy(update={"ha_sensors": DEMO_SENSORS})
    app.settings.update(
        settings.settings.model_copy(update={"learning": learning}),
        base_version=settings.version,
        actor="admin",
        source="ui",
    )
    names = feature_names(DEMO_SENSORS)
    model = train(_demo_training_set(names), names)
    entry = app.model_registry.add(
        model,
        trained_at=MODEL_TRAINED_AT,
        data_from=MODEL_TRAINED_AT - timedelta(days=56),
        data_to=MODEL_TRAINED_AT,
        samples=MODEL_SAMPLES,
        backtest=_demo_backtest(),
    )
    with app.engine.begin() as conn:
        conn.execute(
            update(model_registry)
            .where(model_registry.c.id == entry.id)
            .values(importance=MODEL_IMPORTANCE)
        )
    _shadow_rows(app)
    await app.load_model._load_latest()
    loaded = app.load_model._model
    assert loaded is not None
    app.load_model._model = _RoundedModel(loaded)  # type: ignore[assignment]
    await app.load_model.evaluate()
    local = MODEL_TRAINED_AT.astimezone(LOCAL_TZ)
    app.load_model._result = TrainingResult(
        "trained",
        MODEL_TRAINED_AT,
        f"Trainiert am {local:%d.%m. %H:%M} mit {MODEL_SAMPLES} Viertelstunden.",
    )


async def _earlier_plan(app: AppRuntime, plan: Plan) -> None:
    """Ein Plan um 09:01 neben dem von 10:00, damit „Plan gegen Ist“ für 09:00 bis 10:00 Plan
    und Ist zeigt.

    Er entsteht aus den Eingangsdaten des Plans von 10:00, um eine Stunde zurückgesetzt, und geht
    nur in die Ablage: aktuell bleibt der Plan von 10:00, den core erreicht er nicht.
    """
    assert plan.id is not None
    inputs = app.planning._store.inputs_of(plan.id)
    assert inputs is not None
    hour = timedelta(hours=1)
    earlier = inputs.model_copy(
        update={
            "created_at": T_DEMO - timedelta(minutes=59),
            "slots": [
                slot.model_copy(update={"start": slot.start - hour}) for slot in inputs.slots
            ],
        }
    )
    app.planning._store.save(plan_once(earlier, await solve_inline(earlier)), earlier)


def _demo_tariff(app: AppRuntime) -> TariffSettings:
    """Standardtarif mit Netznutzungsentgelt, einem (erfundenen) Leistungspreis, der schon im
    Oktober 2026 gilt (60 €/kW/Jahr netto, Stufe bei 10 kW): „Laden“ zeigt die Mehrkosten, und
    einer Grundgebühr des Netzes (4,80 €/Monat netto) für die Kosten der Statistik."""
    tariff = with_grid_usage(app.settings.current().settings, 8.11).tariff
    components = [
        c.model_copy(update={"value_eur": 60.0, "valid_from": None})
        if isinstance(c, PowerComponent)
        else c.model_copy(update={"value_eur": 4.8})
        if isinstance(c, MonthlyFeeComponent) and c.id == "grid_base_fee"
        else c
        for c in tariff.components
    ]
    return tariff.model_copy(update={"components": components})


def _history_spot(app: AppRuntime, slots: list[HistorySlot]) -> list[SlotRow]:
    """Börsenpreise netto der Slots, aus denen der Tarif der Demo ihren Bezugspreis brutto ergibt
    (der Bezugspreis hängt linear vom Börsenpreis ab)."""
    tariff = app.settings.current().settings.tariff
    rows: list[SlotRow] = []
    for slot in slots:
        base = import_price(slot.start, 0.0, tariff).gross_ct
        per_ct = import_price(slot.start, 1.0, tariff).gross_ct - base
        spot = (slot.price_ct - base) / per_ct
        rows.append(SlotRow(slot.start, None, spot, None, "primary", None))
    return rows


def _ingest_history(app: AppRuntime, slots: list[HistorySlot]) -> None:
    """Die 14 Tage Abrechnung vor ``T_DEMO``. Die Aufnahmen der Preisquellen reichen nur über den
    09.10.; die Börsenpreise davor legt die Demo selbst in den Preisspeicher, und die Abrechnung
    bewertet die Slots damit wie im Betrieb. So ändert „Neu bewerten“ (E2E, Einstellungen) die
    Kosten nur mit dem Tarif und verliert sie nie. Die Punkte für InfluxDB entfallen: dort liegt in
    der Demo nichts."""
    assert app.price_store.save(_history_spot(app, slots)) == len(slots)
    sink, app.ledger._sink = app.ledger._sink, None
    try:
        for slot in slots:
            covered = 0.0 if slot.estimated else 900.0
            flows = SlotFlows(slot.start, covered, slot.flows_wh, slot.counters)
            assert app.ledger.ingest(flows, "estimate" if slot.estimated else "stream")
    finally:
        app.ledger._sink = sink


def _record_sessions(app: AppRuntime) -> None:
    """Sessions und Belegung über den Tracker, mit Beobachtungen wie aus Health: je Wallbox zwei
    beendete Sessions, an der EVCS eine ohne Fahrzeug, dazu die offene an der Garage (``twc``),
    die bis ``T_DEMO`` läuft."""
    for when, states in observations(T_DEMO):
        app.charging.record(
            when,
            [
                WallboxSeen(wallbox, connected, occupant, kind)
                for wallbox, (connected, occupant, kind) in states.items()
            ],
        )


# Geplante Ladungen der Demo je Session (Index in ``SESSIONS``): Abfahrt nach dem Beginn, machbar,
# Ladestand zu Beginn und am Ende, Ergebnis; die offene von heute zählt nicht
DEMO_PLANNED = (
    (0, timedelta(hours=4, minutes=30), True, 35.0, 52.0, "early"),  # e-Golf vorher abgesteckt
    (1, timedelta(hours=3, minutes=45), False, 30.0, 62.0, "missed"),  # zu spät angesteckt
    (3, timedelta(hours=2), True, 55.0, 58.0, "changed"),  # Abfahrt verschoben
    (3, timedelta(hours=4), True, 55.0, 80.0, "reached"),
    (5, timedelta(hours=9, minutes=15), True, 48.0, 51.0, "open"),
)


def _record_planned(app: AppRuntime) -> None:
    """Geplante Ladungen zu den Sessions der Demo, Ziel 80 %."""
    with app.engine.begin() as conn:
        for index, after, feasible, start_soc, end_soc, outcome in DEMO_PLANNED:
            session = SESSIONS[index]
            start = session.start.astimezone(UTC)
            reached_at = start + after - timedelta(minutes=40) if outcome == "reached" else None
            session_id = conn.execute(
                select(charging_session.c.id).where(
                    charging_session.c.wallbox == session.wallbox,
                    charging_session.c.start == start,
                )
            ).scalar_one()
            conn.execute(
                insert(planned_charge).values(
                    session_id=session_id,
                    vehicle=session.vehicle,
                    target_soc_pct=80,
                    departure_at=start + after,
                    started_at=start,
                    start_soc_pct=start_soc,
                    start_soc_source="measured",
                    feasible=feasible,
                    reached_at=reached_at,
                    end_soc_pct=end_soc,
                    end_soc_source="estimated",
                    end_soc_at=start + after - timedelta(minutes=1),
                    outcome=outcome,
                )
            )


def _record_checks(app: AppRuntime, slots: list[HistorySlot]) -> None:
    """Abgleich der letzten 7 Tage vor heute; die Zählerstände sind erfunden: ein Tag weicht beim
    Netzbezug um 3,5 % ab (markiert), einer hat einen fehlenden Zählerstand."""
    checks = app.ledger_checks
    names = {
        "import": [IMPORT_COUNTER],
        "export": [EXPORT_COUNTER],
        "pv": list(checks._pv_signals),
        **{f"wb:{key}": [f"wallbox.{key}.total_kwh"] for key in checks._wallboxes},
    }
    last = LAST_DAY
    days = [last - timedelta(days=6 - n) for n in range(7)]
    midnights = [local_day_slots(day)[0] for day in [*days, last + timedelta(days=1)]]
    readings = counter_readings(slots, days[0], last, names)
    assert checks._store(days, midnights, readings) == len(days)


async def _statistics(app: AppRuntime) -> None:
    """Daten der Statistik: Abrechnung, Einstandspreis, Sessions, geplante Ladungen, Abgleich,
    Verbraucher je Tag."""
    slots = history_slots()
    assert len(slots) == sum(
        len(local_day_slots(FIRST_DAY + timedelta(days=n))) for n in range(DAYS)
    )
    _ingest_history(app, slots)
    app.costing.catch_up()  # füllt ledger_cost wie die Nachführung im Betrieb
    _record_sessions(app)
    _record_planned(app)
    _record_checks(app, slots)
    today = T_DEMO.astimezone(LOCAL_TZ).date()
    assert await app.consumer_days.fill_missing(today, days=7) == 7


async def build_world(cfg: Config, data_dir: Path, *, ui_dir: Path | None = None) -> World:
    clock = ManualClock(T_DEMO - WARMUP)  # der core läuft vor, danach steht die Uhr auf T_DEMO
    local = _demo_config(cfg, data_dir, ui_dir)
    # mit Token meldet die app keine Komponente „ha-notify“ (der Vertrag bleibt); ihre Aufgaben
    # laufen in der Demo nicht, es geht nichts an Home Assistant
    secrets = Secrets(internal_token=SecretStr(TOKEN), ha_token=SecretStr("demo"))
    router = _router(local)
    router.start()
    try:
        core = _build_core(local, secrets, clock)
        _mount_core_api(router, local, core, secrets)
        # Absicht der Demo: Regelbetrieb an, der Tesla „Geplant“; alles andere im Dry-Run
        tesla = VehicleIntent(
            mode=ChargeMode.PLANNED, target_soc_pct=DEMO_TARGET_PCT, departure=DEMO_DEPARTURE
        )
        intent = Intent(version=1, control=True, vehicles={"tesla": tesla})
        assert core.submit_intent(intent).result == "accepted"
        # Der erste geregelte Zyklus sieht den Zähler der EVCS beim Anstecken, danach hat sie
        # gezählt: Ab ``step_live`` ist der Ladestand des e-Golf geschätzt
        _cycle(core, 0)
        counted = EVCS_TOTAL_KWH + EVCS_COUNTED_KWH
        core.store.update(
            "wallbox.evcs.total_kwh", counted, source="victron", kind=SignalKind.MEASUREMENT
        )
        app = AppRuntime(local, secrets, clock=clock, serve=False)
        app._sink.bind(asyncio.get_running_loop())  # wie AppRuntime.run
        world = World(app, core, clock, router)
        _create_users(app)
        app.decisions.add(DEMO_DECISIONS)
        # drei Versionen; Netzverlust, Elektrizitätsabgabe und Förderbeitrag bleiben leer
        set_tariff(app.settings, _demo_tariff(app))
        set_feed_in(app.settings, {"2026-09": 7.3, "2026-10": 7.1})  # OeMAG-Werte
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
        await app.load_forecast.refresh()  # aus den vier Slots der Abrechnung
        _rate_forecasts(app)
        # Der Verlauf vor dem Plan: so plant er mit der Monatsspitze, die auch Boost-Mehrkosten
        # und Statistik zeigen (aus dem Verlauf, nicht nur aus den vier Slots von heute)
        await _statistics(app)
        await _learning(app)
        # Plan aus den Prognosen und Preisen; der Solver läuft im selben Prozess (wie in Tests)
        app.planning._solver = solve_inline
        plan = await app.planning.replan("start")
        assert plan is not None, app.planning.component_status().message
        await _earlier_plan(app, plan)
        # erst nach dem Plan: er bleibt mit dem Profil gerechnet, die Ausgabe nennt das Modell
        await app.load_forecast.refresh()
    except BaseException:
        router.stop(quiet=True)
        raise
    return world
