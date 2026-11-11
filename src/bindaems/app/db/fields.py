"""Gemeinsame Feldtypen und Hilfen für Stammdaten in SQLite (Verbraucher, PV-Quellen)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import Field, StringConstraints
from sqlalchemy import Row

HA_REF_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
MAX_ID = 2**63 - 1  # größte Ganzzahl in SQLite

Rows = Row[*tuple[Any, ...]]

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
DbId = Annotated[int, Field(ge=1, le=MAX_ID)]


def changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Geänderte Felder als ``{feld: {"old": …, "new": …}}`` für das Änderungsprotokoll."""
    return {
        key: {"old": before[key], "new": value}
        for key, value in after.items()
        if before[key] != value
    }
