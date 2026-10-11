import ast
import re
from pathlib import Path

import bindaems_watchdog
import pytest

from bindaems.core.watchdog_client.status import WATCHDOG_VERSION

SOURCES = sorted(Path("cerbo-watchdog").rglob("*.py"))


def test_sources_found() -> None:
    assert len(SOURCES) >= 4


@pytest.mark.parametrize("path", SOURCES, ids=str)
def test_runs_on_python_38(path: Path) -> None:
    ast.parse(path.read_text(), filename=str(path), feature_version=(3, 8))


def test_independent_of_bindaems() -> None:
    offenders = [
        p for p in SOURCES if re.search(r"^\s*(from|import)\s+bindaems\b", p.read_text(), re.M)
    ]
    assert offenders == []


def test_versions_match() -> None:
    assert bindaems_watchdog.VERSION == WATCHDOG_VERSION
