"""Nächtliche Online-Sicherung der SQLite-Datenbank (Spec 11.3): 14 Generationen."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from bindaems.shared.timeutil import LOCAL_TZ

PATTERN = "bindaems-*.sqlite3"


def backup_database(db_path: Path, backup_dir: Path, now: datetime, keep: int = 14) -> Path:
    """Kopiert die Datenbank im laufenden Betrieb; ältere Sicherungen über ``keep`` entfallen."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"bindaems-{now.astimezone(LOCAL_TZ):%Y%m%d-%H%M%S}.sqlite3"
    partial = target.with_name(target.name + ".tmp")  # erst vollständig, dann sichtbar
    source = sqlite3.connect(db_path)
    try:
        destination = sqlite3.connect(partial)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    partial.replace(target)
    backups = sorted(
        backup_dir.glob(PATTERN)
    )  # Name enthält den Zeitpunkt, sortiert also nach Alter
    for old in backups[: -max(keep, 1)]:
        old.unlink()
    return target
