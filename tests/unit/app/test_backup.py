import sqlite3
from datetime import UTC, datetime

from bindaems.app.backup import backup_database


def test_backup_creates_readable_copy_and_keeps_14(tmp_path) -> None:
    db = tmp_path / "bindaems.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (42)")
    for day in range(16):
        path = backup_database(
            db, tmp_path / "backup", datetime(2026, 10, 1 + day, 1, 15, tzinfo=UTC)
        )
    assert path.name == "bindaems-20261016-031500.sqlite3"
    backups = sorted((tmp_path / "backup").glob("bindaems-*.sqlite3"))
    assert len(backups) == 14 and backups[0].name == "bindaems-20261003-031500.sqlite3"
    assert not list((tmp_path / "backup").glob("*.tmp"))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT x FROM t").fetchone() == (42,)


def test_backup_leaves_foreign_files_alone(tmp_path) -> None:
    db = tmp_path / "bindaems.sqlite3"
    sqlite3.connect(db).close()
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "notizen.txt").write_text("bleibt")
    for day in range(3):
        backup_database(db, backup_dir, datetime(2026, 10, 1 + day, tzinfo=UTC), keep=1)
    assert sorted(p.name for p in backup_dir.iterdir()) == [
        "bindaems-20261003-020000.sqlite3",
        "notizen.txt",
    ]
