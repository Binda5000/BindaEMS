"""Bausteine, die core und app brauchen, liegen in shared – nicht doppelt im core."""

import ast
import importlib.util
import re
from pathlib import Path

import pytest

MOVED = {
    "bindaems.core.telemetry.lineprotocol": "bindaems.shared.influx.lineprotocol",
    "bindaems.core.telemetry.spool": "bindaems.shared.influx.spool",
    "bindaems.core.telemetry.influx": "bindaems.shared.influx.writer",
    "bindaems.core.logging": "bindaems.shared.logging",
    "bindaems.core.broadcast": "bindaems.shared.broadcast",
}


@pytest.mark.parametrize(("old", "new"), MOVED.items())
def test_shared_building_blocks_moved(old: str, new: str) -> None:
    assert importlib.util.find_spec(old) is None
    assert importlib.util.find_spec(new) is not None


def test_retry_tls_and_sink_are_defined_in_shared() -> None:
    from bindaems.shared.influx.lineprotocol import PointSink
    from bindaems.shared.mqtt import build_tls_context
    from bindaems.shared.retry import Backoff, StatusLog

    assert Backoff.__module__ == StatusLog.__module__ == "bindaems.shared.retry"
    assert build_tls_context.__module__ == "bindaems.shared.mqtt"
    assert PointSink.__module__ == "bindaems.shared.influx.lineprotocol"


def test_every_package_directory_has_an_init() -> None:
    # Namespace-Pakete sieht import-linter nicht; ihre Importgrenzen blieben ungeprüft.
    # Ausnahme: die Alembic-Migrationen werden über den Pfad geladen, nicht als Paket.
    root = Path("src/bindaems")
    missing = [
        str(directory)
        for directory in [root, *root.rglob("*")]
        if directory.is_dir()
        and directory.name != "__pycache__"
        and "migrations" not in directory.parts
        and any(directory.glob("*.py"))
        and not (directory / "__init__.py").exists()
    ]
    assert missing == []


LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}


def test_log_events_are_german_messages() -> None:
    # Das Log liest der Betreiber; Ereignisse sind deutsche Sätze wie „Adapter verbunden“,
    # keine englischen Bezeichner wie ``adapter_connected``.
    identifiers = []
    for path in Path("src/bindaems").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in LOG_METHODS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "log"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and re.fullmatch(r"[a-z0-9_]+", node.args[0].value)
            ):
                identifiers.append(f"{path}:{node.lineno}: {node.args[0].value}")
    assert identifiers == []
