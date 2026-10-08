"""Abbildung der Venus-OS-MQTT-Topics (dbus-flashmq) auf EMS-Signale.

Ausgewertet werden nur Topics ``N/<portal>/<service>/<instance>/<path>`` mit JSON-Payload
``{"value": …}``. Alle anderen Nachrichten werden ignoriert. ``{"value": null}`` ergibt ein
Update mit Wert ``None`` – der Zustandsspeicher markiert das Signal dann als ungültig.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bindaems.shared.config import VictronInstances
from bindaems.shared.domain import SignalKind, Value

M = SignalKind.MEASUREMENT
S = SignalKind.STATE

_Route = tuple[re.Pattern[str], str, SignalKind]


def _routes(*items: tuple[str, str, SignalKind]) -> list[_Route]:
    return [(re.compile(pattern), template, kind) for pattern, template, kind in items]


_PHASE = r"L(?P<n>[123])"

ROUTES: dict[str, list[_Route]] = {
    "grid": _routes(
        (rf"Ac/{_PHASE}/Power", "grid.l{n}.power_w", M),
        (rf"Ac/{_PHASE}/Current", "grid.l{n}.current_a", M),
        (rf"Ac/{_PHASE}/Voltage", "grid.l{n}.voltage_v", M),
        (r"Ac/Power", "grid.power_w", M),
        (r"Ac/Energy/Forward", "grid.energy_import_kwh", M),
        (r"Ac/Energy/Reverse", "grid.energy_export_kwh", M),
    ),
    "pvinverter": _routes(
        (r"Ac/Power", "pv.{id}.power_w", M),
        (rf"Ac/{_PHASE}/Power", "pv.{id}.l{n}.power_w", M),
        (r"Ac/Energy/Forward", "pv.{id}.energy_kwh", M),
        (r"Position", "pv.{id}.position", S),
    ),
    "acload": _routes(
        (r"Ac/Power", "load.{id}.power_w", M),
        (rf"Ac/{_PHASE}/Power", "load.{id}.l{n}.power_w", M),
        (r"Ac/Energy/Forward", "load.{id}.energy_kwh", M),
    ),
    "evcharger": _routes(
        (r"Ac/Power", "wallbox.{id}.power_w", M),
        (rf"Ac/{_PHASE}/Power", "wallbox.{id}.l{n}.power_w", M),
        (r"Ac/Energy/Forward", "wallbox.{id}.total_kwh", M),
        (r"Current", "wallbox.{id}.gx_current_a", M),
        (r"Status", "wallbox.{id}.status", S),
        (r"Mode", "wallbox.{id}.gx_mode", S),
        (r"SetCurrent", "wallbox.{id}.gx_set_current_a", S),
        (r"MaxCurrent", "wallbox.{id}.gx_max_current_a", S),
        (r"StartStop", "wallbox.{id}.gx_start_stop", S),
    ),
    "system": _routes(
        (rf"Ac/Consumption/{_PHASE}/Power", "consumption.l{n}.power_w", M),
        (rf"Ac/ConsumptionOnInput/{_PHASE}/Power", "consumption_input.l{n}.power_w", M),
        (rf"Ac/ConsumptionOnOutput/{_PHASE}/Power", "consumption_output.l{n}.power_w", M),
        (r"Dc/Battery/Soc", "battery.soc_pct", M),
        (r"Dc/Battery/Power", "battery.power_w", M),
        (r"Dc/Battery/Voltage", "battery.voltage_v", M),
        (r"Dc/Battery/Current", "battery.current_a", M),
        (r"Dc/Pv/Power", "pv_dc.power_w", M),
        (r"Ac/ActiveIn/Source", "system.active_in_source", S),
    ),
    "vebus": _routes(
        (r"Ac/NumberOfPhases", "vebus.phases", S),
        (r"State", "vebus.state", S),
        (r"Mode", "vebus.mode", S),
        (rf"Ac/ActiveIn/{_PHASE}/P", "vebus.l{n}.ac_in_power_w", M),
        (rf"Ac/Out/{_PHASE}/P", "vebus.l{n}.ac_out_power_w", M),
    ),
    "battery": _routes(
        (r"Info/MaxChargeCurrent", "battery.ccl_a", S),
        (r"Info/MaxDischargeCurrent", "battery.dcl_a", S),
        (r"InstalledCapacity", "battery.installed_capacity_ah", S),
    ),
    "settings": _routes(
        (r"Settings/CGwacs/Hub4Mode", "ess.hub4_mode", S),
        (r"Settings/CGwacs/BatteryLife/State", "ess.batterylife_state", S),
        (r"Settings/CGwacs/BatteryLife/MinimumSocLimit", "ess.min_soc_pct", S),
        (r"Settings/CGwacs/AcPowerSetPoint", "ess.grid_setpoint_w", S),
        (r"Settings/CGwacs/MaxDischargePower", "ess.max_discharge_w", S),
        (r"Settings/SystemSetup/MaxChargeCurrent", "dvcc.max_charge_current_a", S),
        (r"Settings/DynamicEss/Mode", "dess.mode", S),
        (r"Settings/CGwacs/BatteryLife/Schedule/Charge/(?P<k>\d+)/Day", "ess.schedule.{k}.day", S),
    ),
    "hub4": _routes(
        (r"Overrides/(?P<name>[A-Za-z0-9]+)", "ess.override.{name}", S),
    ),
}

# Diese Settings-Teilbäume werden zusätzlich generisch als ``settings.<pfad>`` abgebildet.
_GENERIC_SETTINGS = re.compile(r"Settings/(CGwacs|DynamicEss|SystemSetup)/.+")


@dataclass(frozen=True)
class SignalUpdate:
    signal: str
    value: Value
    kind: SignalKind


class InstanceResolver:
    """Ordnet Victron-Geräteinstanzen logische IDs zu (siehe ``VictronInstances``)."""

    _SINGLETONS = ("grid", "vebus", "battery")
    _NAMED = ("pvinverter", "acload", "evcharger")
    _SYSTEM = ("system", "settings", "hub4")

    def __init__(self, instances: VictronInstances) -> None:
        self._configured: dict[str, int | None] = {
            "grid": instances.grid,
            "vebus": instances.vebus,
            "battery": instances.battery,
        }
        self._names: dict[str, dict[int, str]] = {
            "pvinverter": dict(instances.pv),
            "acload": dict(instances.acload),
            "evcharger": dict(instances.evcharger),
        }
        self._first_seen: dict[str, int] = {}

    def resolve(self, service: str, instance: int) -> str | None:
        if service in self._SYSTEM:
            return "" if instance == 0 else None
        if service in self._SINGLETONS:
            expected = self._configured[service]
            if expected is None:
                expected = self._first_seen.setdefault(service, instance)
            return "" if instance == expected else None
        if service in self._NAMED:
            return self._names[service].get(instance, f"{service}{instance}")
        return None


def parse_message(
    topic: str, payload: bytes, portal_id: str, resolver: InstanceResolver
) -> list[SignalUpdate]:
    parts = topic.split("/", 4)
    if len(parts) != 5 or parts[0] != "N" or parts[1] != portal_id:
        return []
    _, _, service, instance_text, path = parts
    routes = ROUTES.get(service)
    if routes is None or not instance_text.isdigit():
        return []
    device_id = resolver.resolve(service, int(instance_text))
    if device_id is None:
        return []
    valid, value = _decode_value(payload)
    if not valid:
        return []
    updates: list[SignalUpdate] = []
    for pattern, template, kind in routes:
        match = pattern.fullmatch(path)
        if match:
            groups = {key: text.lower() for key, text in match.groupdict().items()}
            updates.append(SignalUpdate(template.format(id=device_id, **groups), value, kind))
            break
    if service == "settings" and _GENERIC_SETTINGS.fullmatch(path):
        generic = "settings." + ".".join(seg.lower() for seg in path.split("/")[1:])
        updates.append(SignalUpdate(generic, value, S))
    return updates


def _decode_value(payload: bytes) -> tuple[bool, Value]:
    """Liefert ``(gültig, Wert)``.

    Ungültig sind Nicht-JSON, ein fehlendes ``value`` sowie Listen und Objekte.
    """
    try:
        data = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False, None
    if not isinstance(data, dict) or "value" not in data:
        return False, None
    value = data["value"]
    if value is None or isinstance(value, bool | int | float | str):
        return True, value
    return False, None
