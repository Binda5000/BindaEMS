"""Verbindung zur SQLite-Datei der app und Migrationen."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, create_engine, event

DB_FILENAME = "bindaems.sqlite3"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def open_database(path: Path) -> Engine:
    """Engine für ``path``; legt das Verzeichnis an und schaltet WAL und Fremdschlüssel ein."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Dienste laufen im Threadpool von FastAPI; der Pool gibt jede Verbindung nur an einen
    # Thread zur Zeit aus
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")  # bleibt in der Datei gespeichert
    return engine


def migrate(engine: Engine) -> None:
    """Bringt das Schema auf den neuesten Stand (Alembic ``upgrade head``)."""
    config = AlembicConfig()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
