import json
import os
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
        steps.append(("fsync", temp.read_text()))
        real_fsync(fd)

    def replace(src: str, dst: str) -> None:
        steps.append(("replace", temp.read_text()))
        real_replace(src, dst)

    monkeypatch.setattr("bindaems_watchdog.state_file.os.fsync", fsync)
    monkeypatch.setattr("bindaems_watchdog.state_file.os.replace", replace)
    write_state(str(tmp_path / "state.json"), StoredState(armed=True, trip_count=3))
    written = (tmp_path / "state.json").read_text()
    assert steps == [("fsync", written), ("replace", written)]
