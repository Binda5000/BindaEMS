from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text

from bindaems.app.db.engine import MIGRATIONS_DIR, migrate, open_database
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


def test_migration_0007_creates_the_decision_table(engine: Engine) -> None:
    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("decision")}
    assert list(columns) == [
        "seq",
        "id",
        "ts",
        "actuator",
        "kind",
        "requested",
        "applied",
        "dry_run",
        "outcome",
        "result",
        "codes",
        "text",
        "inputs",
    ]
    assert columns["seq"]["primary_key"] == 1
    assert [name for name, column in columns.items() if column["nullable"]] == [
        "requested",
        "applied",
    ]
    assert [c["column_names"] for c in inspector.get_unique_constraints("decision")] == [["id"]]
    assert [i["column_names"] for i in inspector.get_indexes("decision")] == [["ts"]]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0008"


def test_migration_0008_creates_the_forecast_tables_and_downgrades(engine: Engine) -> None:
    inspector = inspect(engine)
    record = {column["name"]: column for column in inspector.get_columns("forecast_record")}
    assert list(record) == [
        "kind",
        "slot_start",
        "horizon",
        "issued_at",
        "p50_w",
        "raw_w",
        "clearness",
    ]
    assert [name for name, column in record.items() if column["nullable"]] == ["clearness"]
    assert inspector.get_pk_constraint("forecast_record")["constrained_columns"] == [
        "kind",
        "slot_start",
        "horizon",
    ]
    metric = {column["name"]: column for column in inspector.get_columns("forecast_metric")}
    assert list(metric) == [
        "day",
        "kind",
        "horizon",
        "n",
        "abs_err_sum_w",
        "err_sum_w",
        "actual_sum_w",
    ]
    assert not any(column["nullable"] for column in metric.values())
    assert inspector.get_pk_constraint("forecast_metric")["constrained_columns"] == [
        "day",
        "kind",
        "horizon",
    ]

    config = AlembicConfig()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0007")
    inspector = inspect(engine)
    assert "forecast_record" not in inspector.get_table_names()
    assert "forecast_metric" not in inspector.get_table_names()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0007"
