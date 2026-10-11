"""Einlesen und Prüfen der ``config.ini`` des Watchdogs.

Die Datei erzeugt ``python -m bindaems.tools.watchdog_config`` aus der ``config.yaml``; das EMS
kann sie zur Laufzeit nicht ändern (Spec 8.6). Jeder Fehler in der Datei ist ein ``ConfigError``
mit einer deutschen Meldung, damit der Dienst beim Start mit einer klaren Ursache endet.
"""

from __future__ import annotations

import configparser
import json
import math
from dataclasses import dataclass
from typing import List, NoReturn, Optional, Tuple

DEFAULT_STATE_FILE = "/data/bindaems-watchdog/state.json"
_ACTION_PREFIX = "action:"


class ConfigError(Exception):
    """Die ``config.ini`` fehlt, ist unlesbar, unvollständig oder enthält ungültige Werte."""


@dataclass(frozen=True)
class Action:
    """Eine Aktion bei der Auslösung: ``value`` an ``path`` des D-Bus-Dienstes ``service``.

    ``value`` ist ein JSON-Wert; ``None`` steht für „ungültig“ (auf D-Bus ein leeres Array).
    Mit ``instance`` wählt der Watchdog unter den Diensten eines Typs den mit dieser
    DeviceInstance. Zahlen gelten bis ``tolerance`` als gleich.
    """

    name: str
    service: str
    path: str
    value: object
    instance: Optional[int] = None
    tolerance: float = 0.5


@dataclass(frozen=True)
class Settings:
    """Die Einstellungen des Watchdogs; Zeiten in Sekunden.

    ``gap_s`` steht in der Datei als ``heartbeat_gap_s``.
    """

    enabled: bool
    timeout_s: float
    rearm_s: float
    gap_s: float
    retries: int
    retry_s: float
    state_file: str
    actions: Tuple[Action, ...]


def load_settings(path: str) -> Settings:
    """Liest die Einstellungen aus der ``config.ini`` unter ``path``."""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"config.ini: Datei lässt sich nicht lesen: {exc}") from exc
    return parse_settings(text)


def parse_settings(text: str) -> Settings:
    """Liest die Einstellungen aus dem Text einer ``config.ini``.

    Der Abschnitt ``[watchdog]`` ist Pflicht, jeder Schlüssel darin hat einen Standardwert. Dazu
    kommt je Aktion ein Abschnitt ``[action:<name>]``; die Aktionen behalten die Reihenfolge der
    Datei. Unbekannte Schlüssel überliest der Watchdog, damit eine neuere ``config.ini`` mit einer
    älteren Version läuft.
    """
    parser = configparser.ConfigParser(interpolation=None)  # ein % im Wert ist nur ein %
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise ConfigError(f"config.ini: Aufbau ungültig: {' '.join(str(exc).split())}") from exc
    if not parser.has_section("watchdog"):
        raise ConfigError("config.ini: Abschnitt [watchdog] fehlt.")
    section = parser["watchdog"]
    try:
        enabled = section.getboolean("enabled", True)
    except ValueError:
        raise ConfigError("config.ini: enabled muss true oder false sein.") from None
    timeout_s = _positive(section, "timeout_s", "90")
    rearm_s = _positive(section, "rearm_s", "30")
    gap_s = _positive(section, "heartbeat_gap_s", "25")
    retries = _whole(section.get("retries", "6"))
    if retries is None or retries < 0:
        raise ConfigError("config.ini: retries muss eine ganze Zahl ≥ 0 sein.")
    retry_s = _positive(section, "retry_s", "5")
    if gap_s >= timeout_s:
        raise ConfigError("config.ini: heartbeat_gap_s muss kleiner als timeout_s sein.")
    state_file = section.get("state_file", DEFAULT_STATE_FILE)
    if not state_file:  # ohne Zustandsdatei startete der Watchdog nach einem Neustart nie scharf
        raise ConfigError("config.ini: state_file darf nicht leer sein.")
    actions: List[Action] = [
        _parse_action(name, parser[name])
        for name in parser.sections()
        if name.startswith(_ACTION_PREFIX)
    ]
    return Settings(
        enabled=enabled,
        timeout_s=timeout_s,
        rearm_s=rearm_s,
        gap_s=gap_s,
        retries=retries,
        retry_s=retry_s,
        state_file=state_file,
        actions=tuple(actions),
    )


def _parse_action(section_name: str, section: configparser.SectionProxy) -> Action:
    service, path, raw = section.get("service"), section.get("path"), section.get("value")
    if not service or not path or not raw:
        raise ConfigError(f"config.ini: [{section_name}] braucht service, path und value.")
    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except ValueError:
        raise ConfigError(f"config.ini: [{section_name}] value ist kein JSON-Wert: {raw}") from None
    instance = None
    if "instance" in section:
        instance = _whole(section["instance"])
        if instance is None:
            raise ConfigError(f"config.ini: [{section_name}] instance muss eine ganze Zahl sein.")
    tolerance = _number(section.get("tolerance", "0.5"))
    if not (math.isfinite(tolerance) and tolerance >= 0):
        raise ConfigError(f"config.ini: [{section_name}] tolerance muss eine Zahl ≥ 0 sein.")
    return Action(
        name=section_name[len(_ACTION_PREFIX) :],
        service=service,
        path=path,
        value=value,
        instance=instance,
        tolerance=tolerance,
    )


def _positive(section: configparser.SectionProxy, key: str, default: str) -> float:
    number = _number(section.get(key, default))
    if not (math.isfinite(number) and number > 0):
        raise ConfigError(f"config.ini: {key} muss eine positive Zahl sein.")
    return number


def _number(raw: str) -> float:
    """Die Zahl in ``raw``; ``nan`` für alles, was keine Zahl ist."""
    try:
        return float(raw)
    except ValueError:
        return math.nan


def _whole(raw: str) -> Optional[int]:
    """Die ganze Zahl in ``raw`` oder ``None``."""
    try:
        return int(raw)
    except ValueError:
        return None


def _reject_constant(name: str) -> NoReturn:
    """``NaN`` und ``Infinity`` kennt Pythons ``json``, aber nicht JSON."""
    raise ValueError(name)
