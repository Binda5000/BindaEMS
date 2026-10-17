"""Gemeinsame Vorrichtungen der Werkzeug-Tests."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
import structlog


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """Setzt structlog zurück und stellt Handler und Stufe der Wurzel wieder her: Ein Werkzeug,
    das die Logs einstellt, darf spätere Tests nicht an eine geschlossene Ausgabe binden."""
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    structlog.reset_defaults()
    root.handlers[:] = handlers
    root.setLevel(level)
