import inspect
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO

import pytest
from structlog.testing import capture_logs
from tests.helpers import T0
from tests.unit.shared.test_decisions import decision

from bindaems.core.persistence.decision_spool import DecisionSpool
from bindaems.shared.decisions import decision_from_json
from bindaems.shared.timeutil import LOCAL_TZ


def names(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


def day_file(directory: Path, day: str) -> Path:
    return directory / f"decisions-{day}.jsonl"


def warned(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Die Warnungen aus ``capture_logs``."""
    return [entry for entry in logs if entry["log_level"] == "warning"]


# Schreiben und Lesen


def test_read_since_across_days_with_limit(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path)
    for i, ts in enumerate([T0, T0 + timedelta(hours=1), T0 + timedelta(days=1)]):
        assert spool.append(decision(id=f"d{i}", ts=ts))
    found, more = spool.read_since(T0, limit=1)
    assert [d.id for d in found] == ["d1"] and more is True
    found, more = spool.read_since(T0, limit=10)
    assert [d.id for d in found] == ["d1", "d2"] and more is False


def test_prune_drops_old_days_and_keeps_size(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path, max_bytes=10_000)
    spool.append(decision(ts=T0 - timedelta(days=8)))
    spool.append(decision(ts=T0))
    spool.prune(T0)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["decisions-2026-10-08.jsonl"]


def test_unwritable_directory_returns_false(tmp_path: Path) -> None:
    blocker = tmp_path / "datei"
    blocker.write_text("x")
    assert DecisionSpool(blocker).append(decision()) is False


def test_broken_line_is_skipped(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path)
    spool.append(decision(id="ok1", ts=T0 + timedelta(seconds=1)))
    with (tmp_path / "decisions-2026-10-08.jsonl").open("a", encoding="utf-8") as f:
        f.write("{kaputt\n")
    assert [d.id for d in spool.read_since(T0, 10)[0]] == ["ok1"]


def test_limits_are_keyword_only_with_the_planned_defaults() -> None:
    params = inspect.signature(DecisionSpool).parameters
    assert params["max_bytes"].default == 50 * 1024 * 1024
    assert params["max_age"].default == timedelta(days=7)
    assert params["max_bytes"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["max_age"].kind is inspect.Parameter.KEYWORD_ONLY


def test_append_writes_one_json_line_per_decision_and_creates_the_directory(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "data" / "decisions"
    spool = DecisionSpool(directory)
    assert not directory.exists()  # der Konstruktor legt nichts an
    assert spool.append(decision(id="a")) is True
    assert spool.append(decision(id="b", text="Größe „ok“ – 5 A")) is True
    assert names(directory) == ["decisions-2026-10-08.jsonl"]
    lines = day_file(directory, "2026-10-08").read_text(encoding="utf-8").splitlines()
    assert [decision_from_json(json.loads(line)).id for line in lines] == ["a", "b"]
    assert "Größe „ok“ – 5 A" in lines[1]  # Umlaute und Satzzeichen bleiben lesbar


def test_the_file_is_chosen_by_the_utc_date_of_the_timestamp(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path)
    spool.append(decision(id="late", ts=datetime(2026, 10, 8, 23, 59, 59, tzinfo=UTC)))
    spool.append(decision(id="next", ts=datetime(2026, 10, 9, 0, 0, tzinfo=UTC)))
    # 01:30 in Wien (Sommerzeit) ist 23:30 UTC des Vortags
    spool.append(decision(id="vienna", ts=datetime(2026, 10, 9, 1, 30, tzinfo=LOCAL_TZ)))
    assert names(tmp_path) == ["decisions-2026-10-08.jsonl", "decisions-2026-10-09.jsonl"]
    in_files = {
        day: [
            json.loads(line)["id"]
            for line in day_file(tmp_path, day).read_text(encoding="utf-8").splitlines()
        ]
        for day in ("2026-10-08", "2026-10-09")
    }
    assert in_files == {"2026-10-08": ["late", "vienna"], "2026-10-09": ["next"]}


def test_a_decision_survives_the_trip_through_the_file(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path)
    d = decision(ts=T0 + timedelta(microseconds=7), text="Größe „ok“", codes=("a", "b"))
    spool.append(d)
    assert spool.read_since(T0, 10) == ([d], False)


def test_line_breaking_characters_in_the_text_stay_inside_their_line(tmp_path: Path) -> None:
    # \u2028, \u2029 und \x85 trennen bei ``str.splitlines`` Zeilen; im Spool zählt nur \n.
    spool = DecisionSpool(tmp_path)
    text = "eins\ntwo\r\nthree\u2028four\u2029five\x85six\x0cseven"
    d = decision(text=text, ts=T0 + timedelta(seconds=1))
    spool.append(d)
    assert len(day_file(tmp_path, "2026-10-08").read_bytes().splitlines()) == 1
    assert spool.read_since(T0, 10) == ([d], False)


def test_read_since_is_exclusive_and_ascending_even_if_appended_out_of_order(
    tmp_path: Path,
) -> None:
    spool = DecisionSpool(tmp_path)
    spool.append(decision(id="third", ts=T0 + timedelta(seconds=3)))
    spool.append(decision(id="first", ts=T0 + timedelta(seconds=1)))
    spool.append(decision(id="same", ts=T0))  # genau since: nicht dabei
    spool.append(decision(id="second", ts=T0 + timedelta(seconds=2)))
    found, more = spool.read_since(T0, 10)
    assert [d.id for d in found] == ["first", "second", "third"]
    assert more is False


def test_limit_takes_the_oldest_even_if_the_file_is_out_of_order(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path)
    spool.append(decision(id="third", ts=T0 + timedelta(seconds=3)))
    spool.append(decision(id="first", ts=T0 + timedelta(seconds=1)))
    spool.append(decision(id="second", ts=T0 + timedelta(seconds=2)))
    found, more = spool.read_since(T0, 2)
    assert [d.id for d in found] == ["first", "second"] and more is True


@pytest.mark.parametrize(
    ("limit", "expected", "more"),
    [
        (4, ["d0", "d1", "d2"], False),
        (3, ["d0", "d1", "d2"], False),
        (2, ["d0", "d1"], True),
        (1, ["d0"], True),
        (0, [], True),
        (-1, [], True),
    ],
)
def test_more_tells_whether_decisions_beyond_the_limit_exist(
    tmp_path: Path, limit: int, expected: list[str], more: bool
) -> None:
    spool = DecisionSpool(tmp_path)
    for i in range(3):
        spool.append(decision(id=f"d{i}", ts=T0 + timedelta(seconds=1 + i)))
    found, has_more = spool.read_since(T0, limit)
    assert ([d.id for d in found], has_more) == (expected, more)


# Mehrere auf einmal: die Entscheidungen eines Zyklus


def test_append_many_writes_every_decision_in_its_day_file(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path)
    batch = [
        decision(id="a", ts=T0 + timedelta(seconds=1)),
        decision(id="b", ts=T0 + timedelta(seconds=1)),
        decision(id="next-day", ts=T0 + timedelta(days=1)),
    ]
    assert spool.append_many(batch) is True
    assert names(tmp_path) == ["decisions-2026-10-08.jsonl", "decisions-2026-10-09.jsonl"]
    assert spool.read_since(T0, 10) == (batch, False)


def test_append_many_without_decisions_writes_nothing(tmp_path: Path) -> None:
    directory = tmp_path / "decisions"
    assert DecisionSpool(directory).append_many([]) is True
    assert not directory.exists()


def test_append_many_after_a_partial_line_starts_on_a_new_line(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path)
    tmp_path.joinpath("decisions-2026-10-08.jsonl").write_bytes(b'{"id": "abgebrochen"')
    batch = [decision(id=f"d{i}", ts=T0 + timedelta(seconds=1)) for i in range(2)]
    assert spool.append_many(batch) is True
    with capture_logs() as logs:
        assert spool.read_since(T0, 10) == (batch, False)
    assert len(warned(logs)) == 1  # nur der abgebrochene Rest


def test_append_many_with_a_naive_timestamp_writes_none_of_them(tmp_path: Path) -> None:
    batch = [decision(id="ok"), decision(id="naiv", ts=datetime(2026, 10, 8, 10, 0))]
    with pytest.raises(ValueError, match="Zeitzone"):
        DecisionSpool(tmp_path).append_many(batch)
    assert list(tmp_path.iterdir()) == []


def test_append_many_failure_is_logged_and_returns_false(tmp_path: Path) -> None:
    blocker = tmp_path / "datei"
    blocker.write_text("x")
    with capture_logs() as logs:
        assert DecisionSpool(blocker).append_many([decision(), decision(id="b")]) is False
    assert [e["event"] for e in logs if e["log_level"] == "error"] == [
        "Entscheidung nicht in den Spool geschrieben"
    ]


class _PausedWrite:
    """Eine Datei, deren ``write`` nach der Hälfte anhält, bis ``go`` gesetzt ist."""

    def __init__(self, handle: BinaryIO, halfway: threading.Event, go: threading.Event) -> None:
        self._handle = handle
        self._halfway = halfway
        self._go = go

    def __enter__(self) -> "_PausedWrite":
        return self

    def __exit__(
        self, kind: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self._handle.close()

    def seek(self, *args: Any) -> int:
        return self._handle.seek(*args)

    def read(self, *args: Any) -> bytes:
        return self._handle.read(*args)

    def write(self, data: bytes) -> int:
        middle = len(data) // 2
        self._handle.write(data[:middle])
        self._handle.flush()  # die halbe Gruppe steht in der Datei
        self._halfway.set()
        assert self._go.wait(5)
        return middle + self._handle.write(data[middle:])


def test_a_reader_never_sees_half_of_a_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = DecisionSpool(tmp_path)
    halfway, go = threading.Event(), threading.Event()
    real_open = Path.open

    def open_(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        handle = real_open(self, mode, *args, **kwargs)
        return _PausedWrite(handle, halfway, go) if "a" in mode else handle

    monkeypatch.setattr(Path, "open", open_)
    batch = [decision(id=f"d{i}", ts=T0 + timedelta(seconds=1)) for i in range(3)]
    writer = threading.Thread(target=spool.append_many, args=(batch,))
    writer.start()
    assert halfway.wait(5)  # der Schreiber steckt mitten in der Gruppe
    pages: list[tuple[list[Any], bool]] = []
    reader = threading.Thread(target=lambda: pages.append(spool.read_since(T0, 10)))
    reader.start()
    reader.join(0.2)
    assert reader.is_alive()  # er wartet, statt die halbe Gruppe zu lesen
    go.set()
    writer.join(5)
    reader.join(5)
    assert pages == [(batch, False)]


def test_a_reader_reads_only_what_was_there_when_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = DecisionSpool(tmp_path)
    spool.append(decision(id="old", ts=T0 + timedelta(seconds=1)))
    reading, go = threading.Event(), threading.Event()
    real_open = Path.open

    def open_(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if mode == "rb":  # der Leser hat die Dateien festgehalten und beginnt zu lesen
            reading.set()
            assert go.wait(5)
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_)
    pages: list[tuple[list[Any], bool]] = []
    reader = threading.Thread(target=lambda: pages.append(spool.read_since(T0, 10)))
    reader.start()
    assert reading.wait(5)
    batch = [decision(id=f"new{i}", ts=T0 + timedelta(seconds=2)) for i in range(3)]
    writer = threading.Thread(target=spool.append_many, args=(batch,))
    writer.start()
    writer.join(5)
    assert not writer.is_alive()  # Schreiben wartet nicht, solange der Leser liest
    go.set()
    reader.join(5)
    assert [[d.id for d in found] for found, _ in pages] == [["old"]]
    assert [d.id for d in spool.read_since(T0, 10)[0]] == ["old", "new0", "new1", "new2"]


# Gruppen gleicher Zeit: alle Entscheidungen eines Zyklus tragen dieselbe Zeit und werden nie
# getrennt; sonst ginge der Rest beim nächsten Abruf (``since`` = letzte Zeit) verloren.


def spool_with(tmp_path: Path, spec: list[tuple[str, int]]) -> DecisionSpool:
    """Ein Spool mit den Entscheidungen (Kennung, Sekunden nach T0) in dieser Reihenfolge."""
    spool = DecisionSpool(tmp_path)
    for id_, seconds in spec:
        assert spool.append(decision(id=id_, ts=T0 + timedelta(seconds=seconds)))
    return spool


GROUPS = [("a", 1), ("b1", 2), ("b2", 2), ("b3", 2), ("c", 3)]


@pytest.mark.parametrize(
    ("limit", "expected", "more"),
    [
        (1, ["a"], True),  # die Grenze liegt zwischen zwei Gruppen: nichts zu ergänzen
        (2, ["a", "b1", "b2", "b3"], True),  # mitten in der Gruppe: die ganze Gruppe
        (3, ["a", "b1", "b2", "b3"], True),
        (4, ["a", "b1", "b2", "b3"], True),  # genau am Ende der Gruppe
        (5, ["a", "b1", "b2", "b3", "c"], False),
        (6, ["a", "b1", "b2", "b3", "c"], False),
    ],
)
def test_limit_inside_a_group_of_equal_timestamps_returns_the_whole_group(
    tmp_path: Path, limit: int, expected: list[str], more: bool
) -> None:
    found, has_more = spool_with(tmp_path, GROUPS).read_since(T0, limit)
    assert ([d.id for d in found], has_more) == (expected, more)


def test_a_group_at_the_end_leaves_nothing_more(tmp_path: Path) -> None:
    spool = spool_with(tmp_path, [("a", 1), ("b1", 2), ("b2", 2)])
    found, more = spool.read_since(T0, 2)  # das Limit liegt in der letzten Gruppe
    assert [d.id for d in found] == ["a", "b1", "b2"] and more is False


def test_a_group_larger_than_the_limit_is_returned_whole_and_the_next_call_goes_on(
    tmp_path: Path,
) -> None:
    spool = spool_with(tmp_path, [("g1", 1), ("g2", 1), ("g3", 1), ("x", 2), ("y", 3)])
    first, more = spool.read_since(T0, 1)
    assert [d.id for d in first] == ["g1", "g2", "g3"] and more is True
    second, more = spool.read_since(first[-1].ts, 1)
    assert [d.id for d in second] == ["x"] and more is True
    third, more = spool.read_since(second[-1].ts, 1)
    assert [d.id for d in third] == ["y"] and more is False
    assert spool.read_since(third[-1].ts, 1) == ([], False)


def test_a_group_keeps_the_order_of_the_file(tmp_path: Path) -> None:
    spool = spool_with(tmp_path, [("z", 2), ("m", 2), ("a", 2), ("early", 1)])
    found, _ = spool.read_since(T0, 1)
    assert [d.id for d in found] == ["early"]
    found, _ = spool.read_since(T0, 2)  # das Limit liegt in der Gruppe: alle drei, wie angehängt
    assert [d.id for d in found] == ["early", "z", "m", "a"]


def test_the_same_instant_in_another_zone_belongs_to_the_group(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path)
    spool.append(decision(id="utc", ts=T0 + timedelta(seconds=5)))
    spool.append(decision(id="vienna", ts=(T0 + timedelta(seconds=5)).astimezone(LOCAL_TZ)))
    spool.append(decision(id="later", ts=T0 + timedelta(seconds=6)))
    found, more = spool.read_since(T0, 1)
    assert [d.id for d in found] == ["utc", "vienna"] and more is True


def test_a_group_at_the_end_of_a_day_looks_into_the_next_day_for_more(tmp_path: Path) -> None:
    spool = spool_with(tmp_path, [("a", 1), ("b1", 2), ("b2", 2)])
    spool.append(decision(id="next", ts=T0 + timedelta(days=1)))
    found, more = spool.read_since(T0, 2)
    assert [d.id for d in found] == ["a", "b1", "b2"] and more is True


def test_a_next_day_without_readable_decisions_is_no_more(tmp_path: Path) -> None:
    spool = spool_with(tmp_path, [("a", 1), ("b1", 2), ("b2", 2)])
    day_file(tmp_path, "2026-10-09").write_text("{kaputt\n")
    with capture_logs() as logs:
        found, more = spool.read_since(T0, 2)
    assert [d.id for d in found] == ["a", "b1", "b2"] and more is False
    assert len(warned(logs)) == 1  # um das zu wissen, wurde der nächste Tag gelesen


def test_limit_zero_never_returns_a_group(tmp_path: Path) -> None:
    assert spool_with(tmp_path, GROUPS).read_since(T0, 0) == ([], True)
    equal = DecisionSpool(tmp_path / "gleich")  # auch nicht, wenn alle dieselbe Zeit tragen
    for id_ in ("g1", "g2"):
        equal.append(decision(id=id_, ts=T0 + timedelta(seconds=1)))
    assert equal.read_since(T0, 0) == ([], True)


def test_read_since_without_any_decision(tmp_path: Path) -> None:
    assert DecisionSpool(tmp_path).read_since(T0, 10) == ([], False)
    assert DecisionSpool(tmp_path / "fehlt").read_since(T0, 10) == ([], False)


def test_read_since_when_the_directory_is_a_file(tmp_path: Path) -> None:
    blocker = tmp_path / "datei"
    blocker.write_text("x")
    assert DecisionSpool(blocker).read_since(T0, 10) == ([], False)


def test_read_since_when_the_directory_cannot_be_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = DecisionSpool(tmp_path)
    spool.append(decision(ts=T0 + timedelta(seconds=1)))

    def forbidden(self: Path) -> Any:
        raise PermissionError("gesperrt")

    monkeypatch.setattr(Path, "iterdir", forbidden)
    with capture_logs() as logs:
        assert spool.read_since(T0, 10) == ([], False)
    [warning] = warned(logs)
    assert warning["event"] == "Verzeichnis der Entscheidungen nicht lesbar"
    assert warning["path"] == str(tmp_path)


def test_read_since_leaves_out_days_before_since(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path)
    spool.append(decision(id="old", ts=T0 - timedelta(days=3)))
    day_file(tmp_path, "2026-10-05").write_text("{kaputt\n")  # würde warnen, wenn gelesen
    spool.append(decision(id="new", ts=T0 + timedelta(seconds=1)))
    with capture_logs() as logs:
        found, more = spool.read_since(T0, 10)
    assert ([d.id for d in found], more) == (["new"], False)
    assert warned(logs) == []


def test_read_since_spans_day_files_in_order_and_stops_after_the_limit(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path)
    for i in range(4):
        spool.append(decision(id=f"d{i}", ts=T0 + timedelta(days=i, seconds=1)))
    day_file(tmp_path, "2026-10-11").write_text("{kaputt\n")  # d3 ersetzt: läge hinter dem Limit
    with capture_logs() as logs:
        found, more = spool.read_since(T0, 2)
    assert [d.id for d in found] == ["d0", "d1"] and more is True
    assert warned(logs) == []  # die Datei des 11.10. wurde gar nicht erst gelesen


# Unlesbares

UNREADABLE = [
    pytest.param(b"{kaputt\n", id="no-json"),
    pytest.param(b'{"id": "x"}\n', id="wrong-shape"),
    pytest.param(b"[1, 2]\n", id="not-an-object"),
    pytest.param(b"\xff\xfe\x00\n", id="no-utf8"),
    pytest.param(b"[" * 100_000 + b"\n", id="too-deep"),
]


@pytest.mark.parametrize("junk", UNREADABLE)
def test_each_unreadable_line_is_skipped_with_one_warning(tmp_path: Path, junk: bytes) -> None:
    spool = DecisionSpool(tmp_path)
    spool.append(decision(id="a", ts=T0 + timedelta(seconds=1)))
    path = day_file(tmp_path, "2026-10-08")
    with path.open("ab") as handle:
        handle.write(junk)
    spool.append(decision(id="b", ts=T0 + timedelta(seconds=2)))
    with capture_logs() as logs:
        found, more = spool.read_since(T0, 10)
    assert ([d.id for d in found], more) == (["a", "b"], False)
    [warning] = warned(logs)
    assert warning["event"] == "Unlesbare Zeile im Entscheidungs-Spool übersprungen"
    assert (warning["file"], warning["line"]) == ("decisions-2026-10-08.jsonl", 2)
    assert warning["error"]


def test_blank_lines_are_ignored_quietly(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path)
    spool.append(decision(id="a", ts=T0 + timedelta(seconds=1)))
    with day_file(tmp_path, "2026-10-08").open("ab") as handle:
        handle.write(b"\n   \n\r\n")
    spool.append(decision(id="b", ts=T0 + timedelta(seconds=2)))
    with capture_logs() as logs:
        found, _ = spool.read_since(T0, 10)
    assert [d.id for d in found] == ["a", "b"]
    assert warned(logs) == []


def test_a_partial_last_line_does_not_swallow_the_next_decision(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path)
    spool.append(decision(id="a", ts=T0 + timedelta(seconds=1)))
    with day_file(tmp_path, "2026-10-08").open("ab") as handle:
        handle.write(b'{"id": "abgebrochen", "ts": "2026-10')  # Platte voll oder Absturz: kein \n
    spool.append(decision(id="b", ts=T0 + timedelta(seconds=2)))
    with capture_logs() as logs:
        found, _ = spool.read_since(T0, 10)
    assert [d.id for d in found] == ["a", "b"]
    assert len(warned(logs)) == 1


def test_a_file_that_cannot_be_read_is_skipped_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = DecisionSpool(tmp_path)
    spool.append(decision(id="a", ts=T0 + timedelta(seconds=1)))
    spool.append(decision(id="b", ts=T0 + timedelta(days=1)))
    real_open = Path.open

    def open_(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == "decisions-2026-10-08.jsonl":
            raise PermissionError("gesperrt")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_)
    with capture_logs() as logs:
        found, _ = spool.read_since(T0, 10)
    assert [d.id for d in found] == ["b"]
    [warning] = warned(logs)
    assert warning["event"] == "Entscheidungsdatei nicht lesbar"
    assert warning["file"] == "decisions-2026-10-08.jsonl"


def test_other_files_in_the_directory_are_never_read(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path)
    spool.append(decision(id="a", ts=T0 + timedelta(seconds=1)))
    for foreign in ("notizen.txt", "decisions-2026-10-08.jsonl.tmp", "decisions-2026-13-45.jsonl"):
        (tmp_path / foreign).write_text(json.dumps({"kaputt": True}))
    (tmp_path / "decisions-2026-10-09.jsonl").mkdir()
    with capture_logs() as logs:
        found, more = spool.read_since(T0, 10)
    assert ([d.id for d in found], more) == (["a"], False)
    assert warned(logs) == []


# Schreibfehler


def test_append_failure_is_logged_as_an_error(tmp_path: Path) -> None:
    blocker = tmp_path / "datei"
    blocker.write_text("x")
    with capture_logs() as logs:
        assert DecisionSpool(blocker).append(decision()) is False
    [entry] = [e for e in logs if e["log_level"] == "error"]
    assert entry["event"] == "Entscheidung nicht in den Spool geschrieben"
    assert entry["error"]


def test_append_failure_while_writing_returns_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(self: Path, *args: Any, **kwargs: Any) -> Any:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "open", full)
    with capture_logs() as logs:
        assert DecisionSpool(tmp_path).append(decision()) is False
    assert any(e["log_level"] == "error" for e in logs)


def test_a_lone_surrogate_in_the_text_does_not_break_append(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path)
    d = decision(text="Datei \ud800 kaputt", ts=T0 + timedelta(seconds=1))
    assert spool.append(d) is True
    assert spool.read_since(T0, 10) == ([d], False)


def test_a_naive_timestamp_is_a_programming_error_and_not_swallowed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Zeitzone"):
        DecisionSpool(tmp_path).append(decision(ts=datetime(2026, 10, 8, 10, 0)))
    assert list(tmp_path.iterdir()) == []


# Aufräumen


def size_of_one_line(tmp_path_factory: pytest.TempPathFactory) -> int:
    probe = tmp_path_factory.mktemp("probe")
    DecisionSpool(probe).append(decision())
    return day_file(probe, "2026-10-08").stat().st_size


def test_prune_keeps_the_day_of_the_cutoff(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path)
    spool.append(decision(ts=datetime(2026, 9, 30, 23, 59, tzinfo=UTC)))
    spool.append(decision(ts=datetime(2026, 10, 1, 0, 0, tzinfo=UTC)))
    spool.append(decision(ts=datetime(2026, 10, 1, 23, 59, tzinfo=UTC)))
    spool.prune(datetime(2026, 10, 8, 10, 0, tzinfo=UTC))  # Stichtag: 1.10. (jetzt − 7 Tage)
    assert names(tmp_path) == ["decisions-2026-10-01.jsonl"]


def test_prune_uses_the_utc_date_of_now_minus_max_age(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path, max_age=timedelta(days=2))
    for day in (5, 6, 7, 8):
        spool.append(decision(ts=datetime(2026, 10, day, 12, 0, tzinfo=UTC)))
    # 9.10. 01:00 in Wien (Sommerzeit) = 8.10. 23:00 UTC; minus 2 Tage: der 6.10. bleibt
    spool.prune(datetime(2026, 10, 9, 1, 0, tzinfo=LOCAL_TZ))
    assert names(tmp_path) == [f"decisions-2026-10-0{d}.jsonl" for d in (6, 7, 8)]


def test_prune_honours_a_shorter_max_age(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path, max_age=timedelta(hours=1))
    spool.append(decision(ts=T0 - timedelta(days=1)))
    spool.append(decision(ts=T0))
    spool.prune(T0)
    assert names(tmp_path) == ["decisions-2026-10-08.jsonl"]


def test_prune_default_age_is_seven_days(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path)
    spool.append(decision(ts=T0 - timedelta(days=8)))
    spool.append(decision(ts=T0 - timedelta(days=7)))
    spool.prune(T0)
    assert names(tmp_path) == ["decisions-2026-10-01.jsonl"]


def test_prune_drops_the_oldest_files_until_the_size_fits(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    line = size_of_one_line(tmp_path_factory)
    spool = DecisionSpool(tmp_path, max_bytes=2 * line)
    for day in (5, 6, 7):
        spool.append(decision(ts=datetime(2026, 10, day, 12, 0, tzinfo=UTC)))
    with capture_logs() as logs:
        spool.prune(T0)
    assert names(tmp_path) == ["decisions-2026-10-06.jsonl", "decisions-2026-10-07.jsonl"]
    [warning] = warned(logs)
    assert warning["event"] == "Spool-Grenze erreicht, älteste Entscheidungen verworfen"
    assert (warning["removed_files"], warning["spool_bytes"]) == (1, 2 * line)


def test_prune_keeps_everything_that_fits(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    line = size_of_one_line(tmp_path_factory)
    spool = DecisionSpool(tmp_path, max_bytes=3 * line)  # genau die Summe: ≤ gilt
    for day in (5, 6, 7):
        spool.append(decision(ts=datetime(2026, 10, day, 12, 0, tzinfo=UTC)))
    with capture_logs() as logs:
        spool.prune(T0)
    assert len(names(tmp_path)) == 3
    assert warned(logs) == []


def test_prune_drops_even_the_newest_file_when_it_alone_exceeds_the_size(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    line = size_of_one_line(tmp_path_factory)
    spool = DecisionSpool(tmp_path, max_bytes=line - 1)
    spool.append(decision(ts=datetime(2026, 10, 7, 12, 0, tzinfo=UTC)))
    spool.append(decision(ts=T0))
    spool.prune(T0)
    assert names(tmp_path) == []
    assert spool.append(decision(ts=T0)) is True  # danach geht es mit einer neuen Datei weiter


def test_prune_leaves_other_files_alone(tmp_path: Path) -> None:
    spool = DecisionSpool(tmp_path, max_bytes=1000)
    spool.append(decision(ts=T0))
    foreign = [
        "notizen.txt",
        "decisions-2026-09-01.jsonl.tmp",
        "decisions-2026-13-45.jsonl",
        "decisions-2026-9-1.jsonl",
        "decisions-abc.jsonl",
        "Decisions-2026-09-01.jsonl",
        "rate_limits.json",
    ]
    for name in foreign:
        (tmp_path / name).write_text("x" * 5000)  # zusammen weit über max_bytes
    (tmp_path / "decisions-2026-09-02.jsonl").mkdir()  # ein Verzeichnis ist keine Tagesdatei
    others = sorted([*foreign, "decisions-2026-09-02.jsonl"])
    with capture_logs() as logs:
        spool.prune(T0)  # die Tagesdatei passt; fremde Dateien zählen nicht zur Größe
        assert names(tmp_path) == sorted([*others, "decisions-2026-10-08.jsonl"])
        spool.prune(T0 + timedelta(days=30))  # die Tagesdatei ist zu alt, fremde Dateien nie
        assert names(tmp_path) == others
    assert logs == []


def test_prune_without_a_directory_does_nothing(tmp_path: Path) -> None:
    DecisionSpool(tmp_path / "fehlt").prune(T0)
    blocker = tmp_path / "datei"
    blocker.write_text("x")
    DecisionSpool(blocker).prune(T0)
    assert names(tmp_path) == ["datei"]


def test_prune_on_an_empty_directory_does_nothing(tmp_path: Path) -> None:
    DecisionSpool(tmp_path).prune(T0)
    assert names(tmp_path) == []


def test_failed_deletion_is_logged_and_prune_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = DecisionSpool(tmp_path)
    for day in (1, 2, 3):
        spool.append(decision(ts=datetime(2026, 9, day, 12, 0, tzinfo=UTC)))
    real_unlink = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self.name == "decisions-2026-09-02.jsonl":
            raise PermissionError("gesperrt")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    with capture_logs() as logs:
        spool.prune(T0)
    assert names(tmp_path) == ["decisions-2026-09-02.jsonl"]
    [warning] = warned(logs)
    assert warning["event"] == "Entscheidungsdatei nicht gelöscht"
    assert warning["file"] == "decisions-2026-09-02.jsonl"


def test_a_file_that_vanishes_during_prune_is_no_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = DecisionSpool(tmp_path, max_bytes=1)
    spool.append(decision(ts=T0))
    spool.append(decision(ts=T0 + timedelta(days=1)))
    real_stat = Path.stat

    def stat(self: Path, **kwargs: Any) -> Any:
        if self.name == "decisions-2026-10-08.jsonl":
            raise FileNotFoundError(self.name)
        return real_stat(self, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    spool.prune(T0 + timedelta(days=1))
    monkeypatch.undo()
    # die Datei, die nicht mehr da schien, bleibt unberührt; die andere ist über der Grenze
    assert names(tmp_path) == ["decisions-2026-10-08.jsonl"]
