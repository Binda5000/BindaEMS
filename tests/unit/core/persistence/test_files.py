import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from bindaems.core.persistence.files import read_json, write_json_atomic


def test_roundtrip_without_leftovers(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "x.json"
    write_json_atomic(target, {"a": 1})
    assert read_json(target) == {"a": 1}
    assert [p.name for p in target.parent.iterdir()] == ["x.json"]


def test_read_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_json(tmp_path / "fehlt.json")
    (tmp_path / "kaputt.json").write_text("kaputt")
    with pytest.raises(ValueError):
        read_json(tmp_path / "kaputt.json")


def test_failed_replace_keeps_old_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "x.json"
    write_json_atomic(target, {"v": 1})
    monkeypatch.setattr(
        "bindaems.core.persistence.files.os.replace", Mock(side_effect=OSError("voll"))
    )
    with pytest.raises(OSError):
        write_json_atomic(target, {"v": 2})
    assert read_json(target) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


# Weitere Randfälle


def test_overwrite_replaces_the_content(tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    write_json_atomic(target, {"v": 1, "alt": True})
    write_json_atomic(target, {"v": 2})
    assert read_json(target) == {"v": 2}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_creates_nested_directories(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c" / "x.json"
    write_json_atomic(target, [1, 2])
    assert read_json(target) == [1, 2]


def test_umlauts_stay_readable_in_the_file(tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    write_json_atomic(target, {"text": "Größe in Äpfeln"})
    assert "Größe in Äpfeln" in target.read_bytes().decode("utf-8")
    assert read_json(target) == {"text": "Größe in Äpfeln"}


def test_stale_temp_file_of_an_aborted_write_is_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    (tmp_path / "x.json.tmp").write_text("Rest eines abgebrochenen Schreibens")
    write_json_atomic(target, {"v": 1})
    assert read_json(target) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_content_is_flushed_and_synced_before_it_replaces_the_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, temp = tmp_path / "x.json", tmp_path / "x.json.tmp"
    steps: list[tuple[str, str]] = []  # Schritt und das, was die temporäre Datei dabei enthält
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd: int) -> None:
        steps.append(("fsync", temp.read_text()))
        real_fsync(fd)

    def replace(src: Path, dst: Path) -> None:
        steps.append(("replace", temp.read_text()))
        real_replace(src, dst)

    monkeypatch.setattr("bindaems.core.persistence.files.os.fsync", fsync)
    monkeypatch.setattr("bindaems.core.persistence.files.os.replace", replace)
    write_json_atomic(target, {"a": 1})
    assert [step for step, _ in steps] == ["fsync", "replace"]
    assert [json.loads(content) for _, content in steps] == [{"a": 1}, {"a": 1}]


@pytest.mark.parametrize("failing", ["fsync", "replace"])
def test_failed_step_removes_the_temp_file_and_keeps_the_old_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    target = tmp_path / "x.json"
    write_json_atomic(target, {"v": 1})
    monkeypatch.setattr(
        f"bindaems.core.persistence.files.os.{failing}", Mock(side_effect=OSError("voll"))
    )
    with pytest.raises(OSError, match="voll"):
        write_json_atomic(target, {"v": 2})
    assert read_json(target) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_interrupt_removes_the_temp_file_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "x.json"
    write_json_atomic(target, {"v": 1})
    monkeypatch.setattr(
        "bindaems.core.persistence.files.os.replace", Mock(side_effect=KeyboardInterrupt)
    )
    with pytest.raises(KeyboardInterrupt):
        write_json_atomic(target, {"v": 2})
    assert read_json(target) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_unserializable_data_leaves_everything_as_it_was(tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    write_json_atomic(target, {"v": 1})
    with pytest.raises(TypeError):
        write_json_atomic(target, {"v": object()})
    assert read_json(target) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_unserializable_data_creates_nothing(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        write_json_atomic(tmp_path / "neu" / "x.json", {"v": object()})
    assert list(tmp_path.iterdir()) == []


def test_failed_cleanup_does_not_hide_the_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bindaems.core.persistence.files.os.replace", Mock(side_effect=OSError("voll"))
    )
    monkeypatch.setattr(
        "bindaems.core.persistence.files.os.unlink", Mock(side_effect=PermissionError("gesperrt"))
    )
    with pytest.raises(OSError, match="voll"):
        write_json_atomic(tmp_path / "x.json", {"v": 1})


@pytest.mark.parametrize("value", [[1, "zwei"], "text", 3.5, None, True])
def test_any_json_value_roundtrips(tmp_path: Path, value: object) -> None:
    write_json_atomic(tmp_path / "x.json", value)
    assert read_json(tmp_path / "x.json") == value


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"{",
        b'{"a": 1,}',
        b"\xff\xfe{\x00}\x00",
        '{"ü": 1}'.encode("latin-1"),
        b"\xef\xbb\xbf{}",
    ],
    ids=["leer", "abgeschnitten", "Komma", "UTF-16", "Latin-1", "BOM"],
)
def test_broken_content_is_a_value_error(tmp_path: Path, content: bytes) -> None:
    (tmp_path / "x.json").write_bytes(content)
    with pytest.raises(ValueError):
        read_json(tmp_path / "x.json")


def test_too_deeply_nested_json_is_a_value_error(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text("[" * 100_000 + "]" * 100_000)
    with pytest.raises(ValueError):
        read_json(tmp_path / "x.json")


def test_other_read_errors_stay_os_errors(tmp_path: Path) -> None:
    with pytest.raises(IsADirectoryError):
        read_json(tmp_path)
