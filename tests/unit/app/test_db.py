from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.engine import Inspector

from bindaems.app.db.engine import MIGRATIONS_DIR, migrate, open_database
from bindaems.app.db.schema import metadata

HEAD = "0011"  # neueste Migration


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
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == HEAD


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


def test_migration_0009_creates_the_plan_tables_and_downgrades(engine: Engine) -> None:
    inspector = inspect(engine)
    plan = {column["name"]: column for column in inspector.get_columns("plan")}
    assert list(plan) == [
        "id",
        "created_at",
        "trigger",
        "source",
        "solver_status",
        "fallback_reason",
        "valid_until",
        "settings_version",
        "expected_cost_ct",
        "trimmed",
        "data",
    ]
    assert [name for name, column in plan.items() if column["nullable"]] == [
        "solver_status",
        "fallback_reason",
    ]
    assert inspector.get_pk_constraint("plan")["constrained_columns"] == ["id"]
    assert [i["column_names"] for i in inspector.get_indexes("plan")] == [["created_at"]]
    inputs = {column["name"]: column for column in inspector.get_columns("plan_inputs")}
    assert list(inputs) == ["plan_id", "data"]
    assert not any(column["nullable"] for column in inputs.values())
    assert inspector.get_pk_constraint("plan_inputs")["constrained_columns"] == ["plan_id"]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == HEAD

    config = AlembicConfig()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0008")
    inspector = inspect(engine)
    assert "plan" not in inspector.get_table_names()
    assert "plan_inputs" not in inspector.get_table_names()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0008"


def _columns(inspector: Inspector, table: str) -> dict[str, bool]:
    """Spaltenname -> darf leer sein, in der Reihenfolge der Tabelle."""
    return {column["name"]: column["nullable"] for column in inspector.get_columns(table)}


def test_migration_0010_creates_the_statistics_tables_and_downgrades(engine: Engine) -> None:
    inspector = inspect(engine)
    assert _columns(inspector, "assignment_log") == {
        "id": False,
        "ts": False,
        "wallbox": False,
        "occupant": False,
        "kind": False,
    }
    assert inspector.get_pk_constraint("assignment_log")["constrained_columns"] == ["id"]
    assert [i["column_names"] for i in inspector.get_indexes("assignment_log")] == [
        ["wallbox", "ts"]
    ]
    assert _columns(inspector, "charging_session") == {
        "id": False,
        "wallbox": False,
        "vehicle": False,
        "start": False,
        "end": True,
        "last_seen": False,
    }
    assert inspector.get_pk_constraint("charging_session")["constrained_columns"] == ["id"]
    assert [i["column_names"] for i in inspector.get_indexes("charging_session")] == [["start"]]
    assert set(_columns(inspector, "ledger_cost")) >= {
        "slot_start",
        "stock_kwh",
        "avg_ct",
        "discharge_ct",
    }  # Spalten und Leerwerte nach 0011: test_migration_0011_…
    assert inspector.get_pk_constraint("ledger_cost")["constrained_columns"] == ["slot_start"]
    assert _columns(inspector, "ledger_check") == {
        "day": False,
        "signal": False,
        "ledger_kwh": False,
        "counter_kwh": True,
        "deviation_pct": True,
        "flagged": False,
        "note": True,
        "checked_at": False,
    }
    assert inspector.get_pk_constraint("ledger_check")["constrained_columns"] == ["day", "signal"]
    assert _columns(inspector, "consumer_day") == {
        "day": False,
        "consumer_id": False,
        "kwh": True,
        "note": True,
    }
    assert inspector.get_pk_constraint("consumer_day")["constrained_columns"] == [
        "day",
        "consumer_id",
    ]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == HEAD

    config = AlembicConfig()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0009")
    tables = inspect(engine).get_table_names()
    for name in (
        "assignment_log",
        "charging_session",
        "ledger_cost",
        "ledger_check",
        "consumer_day",
    ):
        assert name not in tables
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0009"


def _downgrade(engine: Engine, revision: str) -> None:
    config = AlembicConfig()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, revision)


def test_migration_0011_lets_the_discharge_cost_be_unknown_and_downgrades(engine: Engine) -> None:
    assert _columns(inspect(engine), "ledger_cost") == {
        "slot_start": False,
        "stock_kwh": False,
        "avg_ct": True,
        "discharge_ct": True,
    }
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ledger_cost (slot_start, stock_kwh, avg_ct, discharge_ct) "
                "VALUES ('2026-10-09T08:00:00+00:00', 1.0, NULL, NULL)"
            )
        )
    _downgrade(engine, "0010")
    assert _columns(inspect(engine), "ledger_cost")["discharge_ct"] is False
    with engine.connect() as conn:
        # abgeleitete Zeilen: die Migration leert die Tabelle, die app rechnet sie neu
        assert conn.execute(text("SELECT count(*) FROM ledger_cost")).scalar_one() == 0
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0010"
