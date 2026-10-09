from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, text

from bindaems.app.db.engine import migrate, open_database
from bindaems.app.db.schema import metadata


def test_migrations_match_schema(engine: Engine) -> None:
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), metadata) == []


def test_migrate_twice_is_harmless(engine: Engine) -> None:
    migrate(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM alembic_version")).scalar_one() == 1


def test_sqlite_runs_in_wal_mode_with_foreign_keys(engine: Engine) -> None:
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_open_database_creates_directory(tmp_path: Path) -> None:
    engine = open_database(tmp_path / "neu" / "app.sqlite3")
    migrate(engine)
    engine.dispose()
    assert (tmp_path / "neu" / "app.sqlite3").exists()
