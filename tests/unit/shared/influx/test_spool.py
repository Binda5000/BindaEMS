import os
from datetime import timedelta

import pytest
from structlog.testing import capture_logs
from tests.unit.shared.influx.conftest import TS

from bindaems.shared.influx.spool import DiskSpool
from bindaems.shared.timeutil import ManualClock


def test_spool_oldest_first(tmp_path) -> None:
    clock = ManualClock(TS)
    sp = DiskSpool(tmp_path, 10_000_000, timedelta(days=7), clock)
    sp.append("raw", ["a 1"])
    clock.advance(1)
    sp.append("raw", ["b 2"])
    assert [f.lines for f in sp.oldest(2)] == [["a 1"], ["b 2"]]


def test_spool_keeps_order_and_rp_within_same_millisecond(tmp_path) -> None:
    sp = DiskSpool(tmp_path, 10_000_000, timedelta(days=7), ManualClock(TS))
    for i in range(12):
        sp.append("long" if i % 2 else "raw", [f"m{i} v=1"])
    files = sp.oldest(20)
    assert [f.lines[0] for f in files] == [f"m{i} v=1" for i in range(12)]
    assert [f.rp for f in files[:2]] == ["raw", "long"]
    assert files[0].path.name == "1791453600123-000000-raw.lp"


def test_spool_enforces_max_bytes_drops_oldest(tmp_path) -> None:
    clock = ManualClock(TS)
    sp = DiskSpool(tmp_path, 120, timedelta(days=7), clock)
    for i in range(5):
        sp.append("raw", [f"m{i} f=1 {i}" * 4])
        clock.advance(1)
    with capture_logs() as logs:
        assert sp.enforce_limits() >= 1
    assert sp.size_bytes() <= 120
    remaining = [f.lines[0][:2] for f in sp.oldest(10)]
    assert "m0" not in remaining and remaining[-1] == "m4"
    assert any(
        entry["event"] == "Spool-Grenze erreicht, älteste Daten verworfen"
        and entry["log_level"] == "warning"
        for entry in logs
    )


def test_spool_enforces_max_age(tmp_path) -> None:
    clock = ManualClock(TS)
    sp = DiskSpool(tmp_path, 10_000_000, timedelta(days=7), clock)
    sp.append("raw", ["alt 1"])
    clock.advance(timedelta(days=8))
    sp.append("raw", ["neu 1"])
    sp.enforce_limits()
    assert [f.lines for f in sp.oldest(5)] == [["neu 1"]]


def test_spool_remove_and_no_leftover_temp_files(tmp_path) -> None:
    sp = DiskSpool(tmp_path, 10_000_000, timedelta(days=7), ManualClock(TS))
    sp.append("raw", ["a 1", "b 2"])
    (spooled,) = sp.oldest(1)
    assert spooled.lines == ["a 1", "b 2"]
    assert [p.name for p in tmp_path.iterdir()] == [spooled.path.name]  # keine Temp-Reste
    sp.remove(spooled.path)
    assert sp.oldest(1) == [] and sp.size_bytes() == 0


def test_leftover_temp_file_from_crash_is_ignored(tmp_path) -> None:
    (tmp_path / ".1791453600123-000000-raw.lp.tmp").write_text("halb geschrieben")
    sp = DiskSpool(tmp_path, 10_000_000, timedelta(days=7), ManualClock(TS))
    assert sp.oldest(10) == [] and sp.size_bytes() == 0


def test_failed_append_leaves_no_file(tmp_path, monkeypatch) -> None:
    sp = DiskSpool(tmp_path, 10_000_000, timedelta(days=7), ManualClock(TS))

    def disk_full(self, target):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("pathlib.Path.replace", disk_full)
    with pytest.raises(OSError):
        sp.append("raw", ["a 1"])
    assert list(tmp_path.iterdir()) == []


def test_file_being_written_is_not_visible(tmp_path, monkeypatch) -> None:
    sp = DiskSpool(tmp_path, 10_000_000, timedelta(days=7), ManualClock(TS))
    visible_while_writing = []
    real_fsync = os.fsync

    def spy(fd: int) -> None:
        visible_while_writing.append(sp.oldest(10))
        real_fsync(fd)

    monkeypatch.setattr("bindaems.shared.influx.spool.os.fsync", spy)
    sp.append("raw", ["a 1"])
    assert visible_while_writing == [[]]
    assert [f.lines for f in sp.oldest(10)] == [["a 1"]]


def test_no_directory_scans_after_startup(tmp_path, monkeypatch) -> None:
    # bei stundenlangem InfluxDB-Ausfall entstehen zehntausende Dateien: nach dem Start
    # darf keine Operation das Verzeichnis durchsuchen (sie liefe auf der Ereignisschleife)
    clock = ManualClock(TS)
    sp = DiskSpool(tmp_path, 200, timedelta(days=7), clock)

    def no_scan(*args, **kwargs):
        raise AssertionError("Verzeichnis-Scan nach dem Start")

    monkeypatch.setattr("pathlib.Path.glob", no_scan)
    monkeypatch.setattr("pathlib.Path.iterdir", no_scan)
    for i in range(8):
        sp.append("raw", [f"m{i} f=1"])
        clock.advance(1)
        sp.enforce_limits()
    files = sp.oldest(3)
    assert [f.lines[0] for f in files] == ["m0 f=1", "m1 f=1", "m2 f=1"]
    sp.remove(files[0].path)
    assert sp.size_bytes() == 7 * len("m0 f=1\n")
    assert sp.oldest(1)[0].lines == ["m1 f=1"]


def test_existing_files_are_taken_over_at_startup(tmp_path) -> None:
    first = DiskSpool(tmp_path, 10_000_000, timedelta(days=7), ManualClock(TS))
    first.append("raw", ["a 1"])
    first.append("long", ["b 2"])
    restarted = DiskSpool(tmp_path, 10_000_000, timedelta(days=7), ManualClock(TS))
    assert [(f.rp, f.lines) for f in restarted.oldest(5)] == [("raw", ["a 1"]), ("long", ["b 2"])]
    assert restarted.size_bytes() == first.size_bytes() == 8


def test_foreign_files_are_ignored(tmp_path) -> None:
    (tmp_path / "fremd.lp").write_text("x 1\n")
    (tmp_path / "notiz.txt").write_text("hallo")
    clock = ManualClock(TS)
    sp = DiskSpool(tmp_path, 5, timedelta(days=7), clock)  # 2 × 4 Byte > 5 Byte
    sp.append("raw", ["a 1"])
    clock.advance(1)
    sp.append("raw", ["b 2"])
    assert sp.enforce_limits() == 1  # nur eigene Dateien zählen und werden gelöscht
    assert [f.lines for f in sp.oldest(5)] == [["b 2"]]
    assert (tmp_path / "fremd.lp").exists()
