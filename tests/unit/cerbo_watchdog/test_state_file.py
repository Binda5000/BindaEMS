import json
import os
import stat
from pathlib import Path
from unittest.mock import Mock

import pytest
from bindaems_watchdog.state_file import StoredState, read_state, write_state


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert read_state(str(tmp_path / "state.json")) == (StoredState(), None)


def test_roundtrip(tmp_path: Path) -> None:
    path = str(tmp_path / "state.json")
    write_state(path, StoredState(armed=True, trip_count=2, last_trip_time=1791600000))
    assert read_state(path) == (StoredState(True, 2, 1791600000), None)


def test_corrupt_state_file_starts_armed(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text("kaputt")
    assert read_state(str(tmp_path / "state.json")) == (
        StoredState(armed=True),
        "state.json unlesbar; der Watchdog startet scharf.",
    )


# Weitere Randfälle

UNREADABLE = "state.json unlesbar; der Watchdog startet scharf."
VALID = {"armed": False, "trip_count": 0, "last_trip_time": 0}


def names(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


@pytest.mark.parametrize(
    "content",
    [
        "",
        "   \n",
        "[]",
        "null",
        "42",
        '"armed"',
        "{}",
        '{"armed": true}',
        '{"trip_count": 1, "last_trip_time": 2}',
        '{"armed": "ja", "trip_count": 0, "last_trip_time": 0}',
        '{"armed": 1, "trip_count": 0, "last_trip_time": 0}',
        '{"armed": null, "trip_count": 0, "last_trip_time": 0}',
        '{"armed": false, "trip_count": -1, "last_trip_time": 0}',
        '{"armed": false, "trip_count": 1.5, "last_trip_time": 0}',
        '{"armed": false, "trip_count": true, "last_trip_time": 0}',
        '{"armed": false, "trip_count": "1", "last_trip_time": 0}',
        '{"armed": false, "trip_count": 0, "last_trip_time": null}',
        '{"armed": false, "trip_count": 0, "last_trip_time": -5}',
        '{"armed": false, "trip_count": 0, "last_trip_time": 0',
        pytest.param("[" * 100_000, id="zu tief verschachtelt"),
        pytest.param(b"\xff\xfe\x00\x01", id="keine UTF-8-Kodierung"),
    ],
)
def test_unusable_content_starts_armed(tmp_path: Path, content: str | bytes) -> None:
    path = tmp_path / "state.json"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    assert read_state(str(path)) == (StoredState(armed=True), UNREADABLE)


def test_directory_in_place_of_the_file_starts_armed(tmp_path: Path) -> None:
    (tmp_path / "state.json").mkdir()
    assert read_state(str(tmp_path / "state.json")) == (StoredState(armed=True), UNREADABLE)


def test_missing_directory_is_a_first_start(tmp_path: Path) -> None:
    assert read_state(str(tmp_path / "fehlt" / "state.json")) == (StoredState(), None)


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text(json.dumps({**VALID, "armed": True, "später": [1]}))
    assert read_state(str(tmp_path / "state.json")) == (StoredState(armed=True), None)


def test_file_format(tmp_path: Path) -> None:
    write_state(str(tmp_path / "state.json"), StoredState(True, 2, 1791600000))
    content = json.loads((tmp_path / "state.json").read_text())
    assert content == {"armed": True, "trip_count": 2, "last_trip_time": 1791600000}


def test_write_leaves_only_the_state_file(tmp_path: Path) -> None:
    path = str(tmp_path / "state.json")
    write_state(path, StoredState(armed=True))
    write_state(path, StoredState(armed=False, trip_count=1))
    assert names(tmp_path) == ["state.json"]
    assert read_state(path) == (StoredState(False, 1, 0), None)


def test_write_to_a_path_without_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_state("state.json", StoredState(armed=True))
    assert read_state("state.json") == (StoredState(armed=True), None)


def test_write_creates_the_directory(tmp_path: Path) -> None:
    path = str(tmp_path / "neu" / "state.json")
    write_state(path, StoredState(armed=True))
    assert read_state(path) == (StoredState(armed=True), None)


def test_failed_replace_keeps_the_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = str(tmp_path / "state.json")
    write_state(path, StoredState(armed=True, trip_count=1))
    monkeypatch.setattr(
        "bindaems_watchdog.state_file.os.replace", Mock(side_effect=OSError("voll"))
    )
    with pytest.raises(OSError, match="voll"):
        write_state(path, StoredState(armed=False, trip_count=2))
    assert read_state(path) == (StoredState(armed=True, trip_count=1), None)
    assert names(tmp_path) == ["state.json"]


def test_stale_temp_file_of_an_aborted_write_is_overwritten(tmp_path: Path) -> None:
    (tmp_path / "state.json.tmp").write_text("Rest eines abgebrochenen Schreibens")
    path = str(tmp_path / "state.json")
    write_state(path, StoredState(armed=True))
    assert read_state(path) == (StoredState(armed=True), None)
    assert names(tmp_path) == ["state.json"]


def test_content_is_on_disk_before_it_replaces_the_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fällt der Strom aus, darf die Datei danach nicht leer sein: erst auf die Platte, dann
    # ersetzen.
    temp = tmp_path / "state.json.tmp"
    steps: list[tuple[str, str]] = []  # Schritt und das, was die temporäre Datei dabei enthält
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):  # nach dem Umbenennen: das Verzeichnis
            steps.append(("fsync Verzeichnis", ""))
        else:
            steps.append(("fsync", temp.read_text()))
        real_fsync(fd)

    def replace(src: str, dst: str) -> None:
        steps.append(("replace", temp.read_text()))
        real_replace(src, dst)

    monkeypatch.setattr("bindaems_watchdog.state_file.os.fsync", fsync)
    monkeypatch.setattr("bindaems_watchdog.state_file.os.replace", replace)
    write_state(str(tmp_path / "state.json"), StoredState(armed=True, trip_count=3))
    written = (tmp_path / "state.json").read_text()
    assert steps == [("fsync", written), ("replace", written), ("fsync Verzeichnis", "")]


# Das Verzeichnis sichern: Erst damit übersteht auch das Umbenennen einen Stromausfall.


def test_write_state_syncs_the_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd) or real_fsync(fd))
    write_state(str(tmp_path / "state.json"), StoredState(armed=True))
    assert len(synced) == 2  # Datei und Verzeichnis


def record_steps(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Merkt sich Datei-fsync, Umbenennen und Verzeichnis-fsync in der Reihenfolge des Aufrufs."""
    steps: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd: int) -> None:
        steps.append("fsync Verzeichnis" if stat.S_ISDIR(os.fstat(fd).st_mode) else "fsync Datei")
        real_fsync(fd)

    def replace(src: str, dst: str) -> None:
        steps.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    return steps


def test_directory_is_synced_after_the_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps = record_steps(monkeypatch)
    write_state(str(tmp_path / "state.json"), StoredState(armed=True))
    assert steps == ["fsync Datei", "replace", "fsync Verzeichnis"]


def test_the_directory_of_a_relative_path_is_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    steps = record_steps(monkeypatch)
    write_state("state.json", StoredState(armed=True))
    assert steps == ["fsync Datei", "replace", "fsync Verzeichnis"]


def test_directory_is_opened_read_only_and_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[tuple[str, int, int]] = []  # Pfad, Flags, Deskriptor
    closed: list[int] = []
    real_open, real_close = os.open, os.close

    def tracking_open(path: str, flags: int, *args: int) -> int:
        fd = real_open(path, flags, *args)
        opened.append((path, flags, fd))
        return fd

    def tracking_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "close", tracking_close)
    write_state(str(tmp_path / "state.json"), StoredState(armed=True))
    ((path, flags, fd),) = opened
    assert (path, flags) == (str(tmp_path), os.O_RDONLY)
    assert closed == [fd]


def test_a_directory_that_cannot_be_opened_does_not_fail_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "open", Mock(side_effect=PermissionError(13, "Permission denied")))
    path = str(tmp_path / "state.json")
    write_state(path, StoredState(armed=True, trip_count=4))  # der Inhalt steht schon
    assert read_state(path) == (StoredState(armed=True, trip_count=4), None)
    assert names(tmp_path) == ["state.json"]


def test_a_failing_directory_sync_does_not_fail_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[int] = []
    real_fsync, real_close = os.fsync, os.close

    def fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(22, "Invalid argument")  # z. B. ein Dateisystem ohne Verzeichnis-fsync
        real_fsync(fd)

    def close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "close", close)
    path = str(tmp_path / "state.json")
    write_state(path, StoredState(armed=True, trip_count=4))
    assert read_state(path) == (StoredState(armed=True, trip_count=4), None)
    assert names(tmp_path) == ["state.json"]
    assert len(closed) == 1  # der Deskriptor des Verzeichnisses ist trotzdem zu


def test_a_failing_file_sync_still_fails_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Nur das Sichern des Verzeichnisses ist nachsichtig: Steht der Inhalt nicht sicher auf der
    # Platte, kommt der Fehler durch, und die alte Datei bleibt.
    path = str(tmp_path / "state.json")
    write_state(path, StoredState(armed=True, trip_count=1))
    monkeypatch.setattr(os, "fsync", Mock(side_effect=OSError(5, "Input/output error")))
    with pytest.raises(OSError, match="Input/output error"):
        write_state(path, StoredState(armed=False, trip_count=2))
    assert read_state(path) == (StoredState(armed=True, trip_count=1), None)
    assert names(tmp_path) == ["state.json"]
