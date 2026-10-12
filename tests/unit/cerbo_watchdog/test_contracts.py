"""Verträge zwischen Cerbo-Watchdog, core und Probe: Zustandscodes und D-Bus-Pfade.

Der Watchdog darf nichts aus ``bindaems`` importieren. Was er veröffentlicht, lesen der core (über
die Topic-Tabelle des Victron-Adapters) und die Probe unabhängig davon; diese Tests halten beide
Seiten zusammen.
"""

import inspect
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from bindaems_watchdog import VERSION, logic, service
from bindaems_watchdog.actions import ActionRunner
from bindaems_watchdog.config import parse_settings
from bindaems_watchdog.logic import WatchdogLogic
from bindaems_watchdog.service import WatchdogService
from bindaems_watchdog.state_file import StoredState
from tests.helpers import T0, snap
from tests.unit.cerbo_watchdog.fakes import FakeAccess

from bindaems.core.adapters import victron_mqtt
from bindaems.core.adapters.victron_topics import ROUTES, InstanceResolver, parse_message
from bindaems.core.watchdog_client.status import (
    WatchdogState,
    WatchdogStatus,
    armed_for_core,
    read_watchdog,
    watchdog_alarms,
)
from bindaems.shared.config import VictronInstances
from bindaems.shared.domain import SignalKind
from bindaems.tools import watchdog_probe

P = "c0619ab1234"
CODES = {"WAITING": 0, "ARMED": 1, "TRIPPED": 2, "DISABLED": 3}  # Werte von /State (Spec 8.6)


def make_service(stored: StoredState, timeout_s: float | None = None) -> WatchdogService:
    """Der Dienst mit den Einstellungen aus ``config.ini.example``, ohne D-Bus.

    ``timeout_s`` ersetzt das Timeout der Datei.
    """
    settings = parse_settings(Path("cerbo-watchdog/config.ini.example").read_text(encoding="utf-8"))
    if timeout_s is not None:
        settings = replace(settings, timeout_s=timeout_s)
    runner = ActionRunner(FakeAccess({}), settings.actions, settings.retries, settings.retry_s)
    return WatchdogService(
        settings,
        WatchdogLogic(settings, stored.armed, 0.0),
        runner,
        stored,
        publish=lambda _path, _value: None,
        save=lambda _state: None,
        wall=lambda: 0.0,
        monotonic=lambda: 0.0,
    )


PUBLISHED = [path.removeprefix("/") for path in make_service(StoredState()).values()]


def published_signals(watchdog: WatchdogService) -> dict[str, object]:
    """Die Signale, die der core über die Topic-Tabelle aus den Werten des Dienstes liest."""
    resolver = InstanceResolver(VictronInstances())
    signals: dict[str, object] = {}
    for path, value in watchdog.values().items():
        payload = json.dumps({"value": value}).encode()
        for update in parse_message(f"N/{P}/bindaems/0{path}", payload, P, resolver):
            signals[update.signal] = update.value
    return signals


def test_state_codes_agree_between_watchdog_probe_and_core() -> None:
    assert {name: getattr(logic, name) for name in CODES} == CODES
    assert {name: getattr(watchdog_probe, name) for name in CODES} == CODES
    for name, code in CODES.items():  # der core liest jeden Code als den Zustand gleichen Namens
        state = read_watchdog(snap({"watchdog.state": code}, kind=SignalKind.STATE)).state
        assert state is WatchdogState[name]


def test_state_names_agree_between_watchdog_probe_and_install_script() -> None:
    assert service.STATE_NAMES == watchdog_probe.STATE_NAMES
    assert sorted(service.STATE_NAMES) == sorted(CODES.values())
    legend = ", ".join(f"{code} {name}" for code, name in sorted(service.STATE_NAMES.items()))
    assert f"({legend})" in Path("cerbo-watchdog/install.sh").read_text(encoding="utf-8")


def test_every_published_path_has_a_route_and_every_route_a_path() -> None:
    patterns = [pattern for pattern, _, _ in ROUTES["bindaems"]]
    assert [p for p in PUBLISHED if not any(r.fullmatch(p) for r in patterns)] == []
    assert [r.pattern for r in patterns if not any(r.fullmatch(p) for p in PUBLISHED)] == []


def test_the_core_reads_what_the_watchdog_publishes() -> None:
    stored = StoredState(armed=True, trip_count=3, last_trip_time=1791600000)
    signals = published_signals(make_service(stored))
    assert signals["watchdog.timeout_s"] == 90 and signals["watchdog.heartbeat"] is None
    assert read_watchdog(snap(signals, kind=SignalKind.STATE)) == WatchdogStatus(
        WatchdogState.ARMED,
        3,
        datetime.fromtimestamp(1791600000, UTC),
        0,
        VERSION,
        heartbeat=None,  # noch kein Zähler geschrieben
        timeout_s=90.0,
    )


def test_the_core_has_no_version_complaint_about_the_watchdog_it_ships_with() -> None:
    # Der Dienst meldet seine VERSION als /Version; stimmt sie nicht mit WATCHDOG_VERSION des core
    # überein, warnt der core „bitte mit install.sh aktualisieren“.
    signals = published_signals(make_service(StoredState()))
    status = read_watchdog(snap(signals, kind=SignalKind.STATE))
    assert status.version == VERSION
    assert [a.id for a in watchdog_alarms(status, True, T0, expected_timeout_s=90.0)] == []


@pytest.mark.parametrize("timeout_s", [90.0, 90.5, 90.999, 120.0, 45.25])
def test_the_core_accepts_the_timeout_the_watchdog_reports(timeout_s: float) -> None:
    # Der Dienst meldet /TimeoutSeconds in ganzen Sekunden, die Konfiguration kennt Bruchteile.
    watchdog = make_service(StoredState(armed=True), timeout_s=timeout_s)
    watchdog.on_heartbeat(service.HEARTBEAT, 7)  # das Echo des EMS
    status = read_watchdog(snap(published_signals(watchdog), kind=SignalKind.STATE))

    def timeout_alarms(expected: float) -> list[str]:
        alarms = watchdog_alarms(status, True, T0, expected_timeout_s=expected)
        return [alarm.id for alarm in alarms if alarm.id == "watchdog.timeout"]

    assert armed_for_core(status, (5, 6, 7), timeout_s)
    assert timeout_alarms(timeout_s) == []
    assert not armed_for_core(status, (5, 6, 7), timeout_s + 30)  # ein anderes Timeout fällt auf
    assert timeout_alarms(timeout_s + 30) == ["watchdog.timeout"]


def test_heartbeat_goes_to_the_only_writable_path_of_the_service(cfg) -> None:
    # core und Probe schreiben W/<portal>/bindaems/0/Heartbeat; dbus-flashmq macht daraus /Heartbeat
    # des Dienstes com.victronenergy.bindaems mit der DeviceInstance 0.
    assert service.SERVICE_NAME == "com.victronenergy.bindaems"
    assert victron_mqtt.HEARTBEAT_PATH == f"bindaems/0{service.HEARTBEAT}"
    # Vom Dienst des Watchdogs darf der core nur den Heartbeat schreiben.
    paths = victron_mqtt.writable_paths(cfg.victron)
    assert {path for path in paths if path.startswith("bindaems/")} == {victron_mqtt.HEARTBEAT_PATH}
    assert watchdog_probe.HEARTBEAT_PATH is victron_mqtt.HEARTBEAT_PATH


def test_the_probe_reads_only_paths_the_watchdog_publishes() -> None:
    source = inspect.getsource(watchdog_probe)
    read = set(re.findall(r"""_values(?:\.get\(|\[)["'](\w+)["']""", source))
    assert {"State", "Version"} <= read  # die Suche findet, was die Probe liest
    assert read <= set(PUBLISHED)
