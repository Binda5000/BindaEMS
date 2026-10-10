"""Prüfprotokoll Teil 1 (Spec 17.1): sammelt ausschließlich lesend und schreibt einen Bericht.

Aufruf::

    python -m bindaems.tools.verify_part1 --config PATH --duration 120 --out docs/verification/

Ist eine Quelle nicht erreichbar, entsteht ein Befund „FEHLER“ und das Werkzeug läuft weiter.
Token und Passwörter werden nie ausgegeben; Koordinaten und VIN werden in den Rohdaten
geschwärzt.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import email.utils
import json
import re
import sys
from collections import defaultdict
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from bindaems.core.adapters import tessie as tessie_api
from bindaems.core.adapters.evcs import (
    BLOCK_COUNT,
    BLOCK_START,
    ModbusReadError,
    PymodbusReader,
    decode_block,
)
from bindaems.core.adapters.victron_mqtt import (
    KEEPALIVE_SUPPRESS,
    AiomqttTransport,
    MqttMessage,
)
from bindaems.core.adapters.victron_topics import InstanceResolver, parse_message
from bindaems.core.checks.selfcheck import run_selfcheck
from bindaems.core.state.store import StateStore
from bindaems.shared.config import (
    Config,
    ConfigError,
    EvcsConfig,
    Secrets,
    TwcConfig,
    load_config,
    load_secrets,
)
from bindaems.shared.domain import SignalKind, Snapshot
from bindaems.shared.timeutil import LOCAL_TZ, SystemClock
from bindaems.tools.verify_checks import (
    Finding,
    check_battery,
    check_clock_offsets,
    check_evcs_dump,
    check_grid_meter,
    check_ha,
    check_hub4_overrides,
    check_influx,
    check_mqtt_inventory,
    check_peak_shaving,
    check_pv_and_submeter,
    check_selfcheck,
    check_tessie,
    check_twc,
    evcharger_connection,
    render_report,
)

SAMPLE_INTERVAL_S = 5.0
DISCOVERY_TIMEOUT_S = 10.0
HTTP_TIMEOUT_S = 10.0
EVCS_BLOCKS = ((5000, 50), (5050, 50), (5100, 50), (5150, 50))
_SECRET_KEY = re.compile(r"token|password|passwort|secret|latitude|longitude", re.IGNORECASE)
# Nur diese Teilbäume kommen in die Rohdaten – andere Einstellungen (z. B. die BLE-PIN unter
# Settings/Ble) haben im eingecheckten Protokoll nichts zu suchen.
DUMP_SUBTREES = (
    "system/",
    "grid/",
    "pvinverter/",
    "acload/",
    "vebus/",
    "battery/",
    "evcharger/",
    "hub4/",
    "settings/0/Settings/CGwacs/",
    "settings/0/Settings/DynamicEss/",
    "settings/0/Settings/SystemSetup/",
    "platform/0/Firmware/",
)


def redact(data: Any) -> Any:
    """Schwärzt Secrets, Koordinaten und VIN in Rohdaten (rekursiv)."""
    if isinstance(data, Mapping):
        return {
            key: "***"
            if _SECRET_KEY.search(str(key)) or str(key).lower() == "vin"
            else redact(value)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [redact(item) for item in data]
    return data


def keep_in_dump(topic: str, portal: str) -> bool:
    """Gehört das Topic in die Rohdaten des Prüfprotokolls?"""
    prefix = f"N/{portal}/"
    return topic.startswith(prefix) and topic[len(prefix) :].startswith(DUMP_SUBTREES)


def clock_offset_s(date_header: str | None, local_now: datetime) -> float | None:
    """Abweichung einer Gegenstelle laut HTTP-Header ``Date`` (Auflösung 1 s)."""
    if not date_header:
        return None
    try:
        remote = email.utils.parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return None
    return (remote - local_now).total_seconds()


def _error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


class _Mqtt:
    """Liest den Cerbo für ``duration_s`` Sekunden; publiziert nur den Keepalive."""

    def __init__(self, cfg: Config, secrets: Secrets) -> None:
        self.cfg = cfg
        self.secrets = secrets
        self.store = StateStore(SystemClock())
        self.store.register_source("victron", 5.0, activity_based=True)
        self.resolver = InstanceResolver(cfg.victron.instances)
        self.services: dict[str, set[int]] = defaultdict(set)
        self.topics: dict[str, str] = {}
        self.samples: list[Snapshot] = []
        self.portal = cfg.victron.mqtt.portal_id

    async def run(self, duration_s: float) -> None:
        mqtt = self.cfg.victron.mqtt
        async with AiomqttTransport(mqtt, self.secrets.mqtt_password) as transport:
            messages = transport.messages()
            if self.portal is None:
                await transport.subscribe("N/+/system/0/Serial")
                self.portal = await self._discover(messages)
            await transport.subscribe(f"N/{self.portal}/#")
            await transport.publish(f"R/{self.portal}/keepalive", b"")
            self.store.set_connected("victron", True)
            consumer = asyncio.create_task(self._consume(messages, self.portal))
            try:
                await self._sample(transport, duration_s, consumer)
            finally:
                consumer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await consumer
        self.samples.append(self.store.snapshot())

    async def _discover(self, messages: AsyncIterator[MqttMessage]) -> str:
        async with asyncio.timeout(DISCOVERY_TIMEOUT_S):
            async for message in messages:
                parts = message.topic.split("/")
                if len(parts) == 5 and parts[0] == "N" and parts[2:] == ["system", "0", "Serial"]:
                    return parts[1]
        raise ConnectionError("Portal-ID konnte nicht ermittelt werden")

    async def _consume(self, messages: AsyncIterator[MqttMessage], portal: str) -> None:
        async for message in messages:
            self.store.touch("victron")
            if keep_in_dump(message.topic, portal):
                self.topics[message.topic] = message.payload.decode(errors="replace")
            parts = message.topic.split("/")
            if len(parts) >= 4 and parts[0] == "N" and parts[3].isdigit():
                self.services[parts[2]].add(int(parts[3]))
            for update in parse_message(message.topic, message.payload, portal, self.resolver):
                self.store.update(update.signal, update.value, source="victron", kind=update.kind)

    async def _sample(
        self, transport: Any, duration_s: float, consumer: asyncio.Task[None]
    ) -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + duration_s
        next_keepalive = loop.time() + self.cfg.victron.mqtt.keepalive_s
        while (now := loop.time()) < end:
            if consumer.done():
                consumer.result()  # Verbindungsfehler sichtbar machen
                raise ConnectionError("MQTT-Verbindung beendet")
            if now >= next_keepalive:
                await transport.publish(f"R/{self.portal}/keepalive", KEEPALIVE_SUPPRESS)
                next_keepalive += self.cfg.victron.mqtt.keepalive_s
            self.samples.append(self.store.snapshot())
            await asyncio.sleep(min(SAMPLE_INTERVAL_S, max(end - loop.time(), 0.0)))

    def firmware(self) -> dict[str, str]:
        meta = {}
        for topic, payload in self.topics.items():
            label = None
            if topic.endswith("/Firmware/Installed/Version"):
                label = "Venus OS"
            elif re.fullmatch(r"N/[^/]+/vebus/\d+/FirmwareVersion", topic):
                label = "vebus-Firmware"
            if label:
                with contextlib.suppress(ValueError, TypeError, AttributeError):
                    meta[label] = str(json.loads(payload)["value"])
        return meta


async def _read_evcs(wallbox: EvcsConfig) -> tuple[dict[int, int], list[str]]:
    reader = PymodbusReader(wallbox.host, wallbox.port, wallbox.unit_id)
    await reader.connect()
    regs: dict[int, int] = {}
    unreadable: list[str] = []
    try:
        for start, count in EVCS_BLOCKS:
            try:
                values = await reader.read_holding(start, count)
                regs.update({start + i: value for i, value in enumerate(values)})
                continue
            except ModbusReadError:
                pass
            for address in range(start, start + count):  # Block mit Lücken: einzeln lesen
                try:
                    regs[address] = (await reader.read_holding(address, 1))[0]
                except ModbusReadError:
                    unreadable.append(str(address))
    finally:
        await reader.close()
    return regs, unreadable


def _series(response: httpx.Response) -> list[dict[str, Any]]:
    """Zeilen der ersten Serie einer InfluxDB-Antwort als dicts."""
    result = response.json()["results"][0]
    if "error" in result:
        raise RuntimeError(result["error"])
    series = result.get("series") or [{"columns": [], "values": []}]
    columns = series[0].get("columns", [])
    return [dict(zip(columns, row, strict=False)) for row in series[0].get("values", [])]


async def run_verification(
    cfg: Config, secrets: Secrets, config_path: Path, duration_s: float
) -> tuple[str, dict[str, Any]]:
    findings: list[Finding] = []
    raw: dict[str, Any] = {}
    now_local = datetime.now(LOCAL_TZ)
    meta = {
        "Datum": now_local.date().isoformat(),
        "Konfiguration": str(config_path),
        "MQTT-Aufzeichnung": f"{duration_s:g} s",
    }
    offsets: dict[str, float] = {}

    mqtt = _Mqtt(cfg, secrets)
    try:
        await mqtt.run(duration_s)
        findings.append(check_mqtt_inventory(mqtt.services, cfg))
        findings += check_grid_meter(mqtt.samples)
        last = mqtt.samples[-1]
        findings += check_pv_and_submeter(last)
        findings.append(check_battery(last))
        findings.append(check_hub4_overrides(last))
        findings.append(check_peak_shaving(last))
        meta |= mqtt.firmware()
    except Exception as exc:  # Quelle nicht erreichbar: Befund statt Abbruch
        findings.append(Finding("17.1-3", "Cerbo GX (MQTT)", "fail", _error(exc)))
    raw["mqtt"] = {
        "portal": mqtt.portal,
        "services": {service: sorted(ids) for service, ids in mqtt.services.items()},
        "topics": mqtt.topics,
    }

    raw["evcs"] = {}
    for name, wallbox in cfg.wallboxes.items():
        if not isinstance(wallbox, EvcsConfig):
            continue
        try:
            regs, unreadable = await _read_evcs(wallbox)
        except Exception as exc:
            findings.append(Finding("17.1-8", f"EVCS {name}", "fail", _error(exc)))
            continue
        raw["evcs"][name] = {"registers": {str(a): v for a, v in sorted(regs.items())}}
        instance = next((i for i, n in cfg.victron.instances.evcharger.items() if n == name), None)
        link = evcharger_connection(mqtt.topics, instance) if instance is not None else None
        findings += check_evcs_dump(regs, gx_connection=link)
        if unreadable:
            findings.append(
                Finding(
                    "17.1-8", f"EVCS {name} nicht belegte Register", "info", ", ".join(unreadable)
                )
            )
        block = [regs.get(BLOCK_START + i) for i in range(BLOCK_COUNT)]
        if all(value is not None for value in block):
            values = decode_block(BLOCK_START, [v for v in block if v is not None])
            meta[f"EVCS-Firmware {name}"] = str(values["firmware"])
            mqtt.store.register_source(name, 5.0)
            mqtt.store.set_connected(name, True)
            for key, value in values.items():
                mqtt.store.update(
                    f"wallbox.{name}.{key}", value, source=name, kind=SignalKind.STATE
                )

    snap = mqtt.store.snapshot()
    findings[0:0] = check_selfcheck(
        run_selfcheck(snap, cfg, mqtt.store.ok_since, datetime.now(UTC))
    )

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as http:
        raw["twc"] = {}
        for name, wallbox in cfg.wallboxes.items():
            if not isinstance(wallbox, TwcConfig):
                continue
            data: dict[str, Any] = {}
            try:
                for path in ("vitals", "lifetime", "version"):
                    response = await http.get(f"http://{wallbox.host}/api/1/{path}")
                    response.raise_for_status()
                    data[path] = response.json()
                findings.append(check_twc(data["vitals"]))
                firmware = data["version"].get("firmware_version")
                if firmware:
                    meta[f"Wall-Connector-Firmware {name}"] = str(firmware)
            except Exception as exc:
                findings.append(Finding("17.1-9", f"Wall Connector {name}", "fail", _error(exc)))
            raw["twc"][name] = data

        raw["tessie"] = {}
        for vehicle_id, vehicle in cfg.vehicles.items():
            if vehicle.tessie is None:
                continue
            if secrets.tessie_token is None:
                findings.append(
                    Finding("17.1-10", f"Tessie {vehicle_id}", "info", "Kein Token, nicht geprüft.")
                )
                continue
            try:
                started = asyncio.get_running_loop().time()
                response = await http.get(
                    f"{tessie_api.TESSIE_BASE_URL}/{vehicle.tessie.vin}/state",
                    params={"use_cache": "true"},
                    headers={"Authorization": f"Bearer {secrets.tessie_token.get_secret_value()}"},
                )
                latency = asyncio.get_running_loop().time() - started
                response.raise_for_status()
                state = response.json()
                findings.append(check_tessie(state))
                findings.append(
                    Finding("17.1-10", f"Tessie-Latenz {vehicle_id}", "info", f"{latency:.2f} s")
                )
                raw["tessie"][vehicle_id] = state
            except Exception as exc:
                findings.append(Finding("17.1-10", f"Tessie {vehicle_id}", "fail", _error(exc)))

        ha = cfg.homeassistant
        raw["homeassistant"] = {}
        if ha is not None and secrets.ha_token is not None:
            entities: dict[str, Mapping[str, Any] | None] = {}
            try:
                headers = {"Authorization": f"Bearer {secrets.ha_token.get_secret_value()}"}
                for entity_id in sorted(set(ha.entities.values())):
                    response = await http.get(
                        f"{ha.url.rstrip('/')}/api/states/{entity_id}", headers=headers
                    )
                    offset = clock_offset_s(response.headers.get("Date"), datetime.now(UTC))
                    if offset is not None:
                        offsets["homeassistant"] = offset
                    if response.status_code == 404:
                        entities[entity_id] = None
                        continue
                    response.raise_for_status()
                    entities[entity_id] = response.json()
                findings += check_ha(entities)
            except Exception as exc:
                findings.append(Finding("17.1-11", "Home Assistant", "fail", _error(exc)))
            raw["homeassistant"] = entities
        else:
            findings.append(
                Finding("17.1-11", "Home Assistant", "info", "Nicht konfiguriert oder kein Token.")
            )

        influx = cfg.influxdb
        auth = (
            (
                influx.username,
                secrets.influx_password.get_secret_value() if secrets.influx_password else "",
            )
            if influx.username
            else None
        )
        try:
            show: dict[str, Any] = {"ha_database": influx.ha_database}

            async def query(q: str) -> list[dict[str, Any]]:
                response = await http.get(
                    f"{influx.url.rstrip('/')}/query",
                    params={"q": q},
                    auth=auth if auth is not None else httpx.USE_CLIENT_DEFAULT,
                )
                offset = clock_offset_s(response.headers.get("Date"), datetime.now(UTC))
                if offset is not None:
                    offsets["influxdb"] = offset
                response.raise_for_status()
                return _series(response)

            show["databases"] = [row["name"] for row in await query("SHOW DATABASES")]
            show["retention_policies"] = await query('SHOW RETENTION POLICIES ON "bindaems"')
            show["ha_measurements"] = (
                [row["name"] for row in await query(f'SHOW MEASUREMENTS ON "{influx.ha_database}"')]
                if influx.ha_database
                else []
            )
            findings += check_influx(show)
            raw["influxdb"] = show
        except Exception as exc:
            findings.append(Finding("17.1-11", "InfluxDB", "fail", _error(exc)))

    findings.append(check_clock_offsets(offsets))
    findings.sort(key=lambda f: [int(part) for part in re.findall(r"\d+", f.item)])
    raw["offsets_s"] = offsets
    return render_report(findings, meta), redact(raw)


def _secrets_error(exc: ValidationError) -> str:
    details = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors(include_input=False, include_url=False)
    )
    return f"Secrets ungültig: {details}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_part1", description="Prüfprotokoll Teil 1 (nur lesend)"
    )
    parser.add_argument("--config", type=Path, default=Path("/config/config.yaml"))
    parser.add_argument("--duration", type=float, default=120.0, help="MQTT-Aufzeichnung in s")
    parser.add_argument("--out", type=Path, default=Path("docs/verification"))
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        secrets = load_secrets()
    except ValidationError as exc:
        print(_secrets_error(exc), file=sys.stderr)
        return 2
    report, raw = asyncio.run(run_verification(cfg, secrets, args.config, args.duration))
    args.out.mkdir(parents=True, exist_ok=True)
    day = datetime.now(LOCAL_TZ).date().isoformat()
    report_path = args.out / f"{day}-pruefprotokoll-teil1.md"
    raw_path = args.out / f"{day}-pruefprotokoll-teil1-rohdaten.json"
    report_path.write_text(report, encoding="utf-8")
    raw_path.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(f"Bericht: {report_path}\nRohdaten: {raw_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
