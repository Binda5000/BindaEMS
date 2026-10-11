"""Plattenspeicher des Entscheidungslogs: JSON-Zeilen, eine Datei je Tag (Spec 7.10).

Jede Entscheidung ist eine Zeile in ``decisions-JJJJ-MM-TT.jsonl``; das Datum ist das UTC-Datum
ihres Zeitpunkts. Die Dateien werden nur ergänzt, nie umgeschrieben. ``prune`` hält sie auf
höchstens ``max_age`` und ``max_bytes``; andere Dateien im Verzeichnis bleiben unberührt. Ein
Fehler beim Schreiben kommt ins Log und unterbricht den Zyklus des core nicht.
"""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final, NamedTuple

import structlog

from bindaems.shared.decisions import Decision, decision_from_json, decision_to_json
from bindaems.shared.timeutil import ensure_utc

log = structlog.get_logger(__name__)

_NAME: Final = re.compile(r"decisions-([0-9]{4}-[0-9]{2}-[0-9]{2})\.jsonl")


class _DayFile(NamedTuple):
    day: date
    path: Path
    size: int


def _file_name(day: date) -> str:
    return f"decisions-{day.isoformat()}.jsonl"


def _day_of(name: str) -> date | None:
    """Das Datum aus dem Namen einer Tagesdatei; ``None``, wenn der Name nicht passt."""
    match = _NAME.fullmatch(name)
    if match is None:
        return None
    try:
        return date.fromisoformat(match[1])
    except ValueError:  # etwa 2026-13-45
        return None


class DecisionSpool:
    """Das Entscheidungslog auf der Platte.

    Der Konstruktor legt nichts an: Das Verzeichnis entsteht beim ersten ``append``. Je Tag gibt
    es eine Datei; ``prune`` löscht Dateien, deren Tag mehr als ``max_age`` zurückliegt, und
    danach die ältesten, bis alle zusammen höchstens ``max_bytes`` belegen. Der Aufrufer ruft
    ``prune`` von Zeit zu Zeit auf. Der Spool hat einen Schreiber: den core, aus einer Aufgabe.
    """

    def __init__(
        self,
        directory: Path,
        *,
        max_bytes: int = 50 * 1024 * 1024,
        max_age: timedelta = timedelta(days=7),
    ) -> None:
        self._dir = directory
        self._max_bytes = max_bytes
        self._max_age = max_age

    def append(self, decision: Decision) -> bool:
        """Hängt die Entscheidung als eine JSON-Zeile an die Datei ihres Tages an.

        Der Tag ist das UTC-Datum von ``ts``. ``True``, wenn die Zeile geschrieben ist. Bei einem
        ``OSError`` (Verzeichnis nicht beschreibbar, Platte voll) kommt ein ``log.error``, und das
        Ergebnis ist ``False``. Ein ``ts`` ohne Zeitzone ist ein Programmierfehler (``ValueError``).

        Endet die Datei nicht auf einen Zeilenumbruch, etwa nach einem Abbruch beim Schreiben, wird
        der Rest erst abgeschlossen: Er bleibt eine unlesbare Zeile und verschluckt nicht die
        nächste Entscheidung.
        """
        text = json.dumps(decision_to_json(decision), ensure_ascii=False)
        # Ein einzelnes Surrogat ließe sich nicht als UTF-8 schreiben; als Escape bleibt es lesbar.
        data = f"{text}\n".encode(errors="backslashreplace")
        path = self._dir / _file_name(ensure_utc(decision.ts).date())
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with path.open("ab+") as handle:
                if handle.seek(0, os.SEEK_END) > 0:
                    handle.seek(-1, os.SEEK_END)
                    if handle.read(1) != b"\n":
                        handle.write(b"\n")
                handle.write(data)
        except OSError as exc:
            log.error("Entscheidung nicht in den Spool geschrieben", path=str(path), error=str(exc))
            return False
        return True

    def read_since(self, since: datetime, limit: int) -> tuple[list[Decision], bool]:
        """Die Entscheidungen mit ``ts`` nach ``since``, aufsteigend, höchstens ``limit``.

        Das Zweite sagt, ob es noch mehr gibt. Gelesen werden nur die Tagesdateien ab dem Tag von
        ``since`` und nur so viele, wie das Limit braucht. Eine Zeile, die sich nicht lesen lässt,
        kommt mit einer Warnung ins Log und fehlt im Ergebnis; ebenso eine Datei, die sich nicht
        öffnen lässt. Ein Limit unter 0 gilt als 0.
        """
        since = ensure_utc(since)
        limit = max(limit, 0)
        found: list[Decision] = []
        for day_file in self._day_files():
            if day_file.day < since.date():
                continue  # alle Zeitpunkte dieses Tages liegen vor ``since``
            found.extend(self._read_file(day_file.path, since))
            if len(found) > limit:
                break  # die späteren Tage ändern an den ersten ``limit`` nichts mehr
        return found[:limit], len(found) > limit

    def prune(self, now: datetime) -> None:
        """Löscht Dateien, deren Tag vor dem Tag von ``now − max_age`` liegt (UTC).

        Danach löscht es die ältesten, bis die übrigen zusammen höchstens ``max_bytes`` belegen;
        das kommt als Warnung ins Log. Dateien, die nicht ``decisions-JJJJ-MM-TT.jsonl`` heißen,
        bleiben unberührt und zählen nicht zur Größe. Eine Datei, die sich nicht löschen lässt,
        kommt ins Log; die übrigen werden trotzdem behandelt.
        """
        cutoff = (ensure_utc(now) - self._max_age).date()
        kept: list[_DayFile] = []
        for day_file in self._day_files():
            if day_file.day < cutoff:
                self._delete(day_file.path)
            else:
                kept.append(day_file)
        total = sum(day_file.size for day_file in kept)
        removed = 0
        for day_file in kept:  # die ältesten zuerst
            if total <= self._max_bytes:
                break
            if self._delete(day_file.path):
                total -= day_file.size
                removed += 1
        if removed:
            log.warning(
                "Spool-Grenze erreicht, älteste Entscheidungen verworfen",
                removed_files=removed,
                spool_bytes=total,
            )

    def _day_files(self) -> list[_DayFile]:
        """Die Tagesdateien, älteste zuerst; andere Namen und Verzeichnisse fehlen hier."""
        try:
            entries = list(self._dir.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            return []  # noch nichts geschrieben
        except OSError as exc:
            log.warning(
                "Verzeichnis der Entscheidungen nicht lesbar", path=str(self._dir), error=str(exc)
            )
            return []
        files: list[_DayFile] = []
        for path in entries:
            day = _day_of(path.name)
            if day is None:
                continue
            try:
                info = path.stat()
            except OSError:  # zwischen Auflisten und Prüfen verschwunden
                continue
            if stat.S_ISREG(info.st_mode):
                files.append(_DayFile(day, path, info.st_size))
        return sorted(files, key=lambda day_file: day_file.day)

    def _read_file(self, path: Path, since: datetime) -> list[Decision]:
        """Die Entscheidungen der Datei mit ``ts`` nach ``since``, nach Zeit geordnet."""
        decisions: list[Decision] = []
        try:
            with path.open("rb") as handle:
                for number, raw in enumerate(handle, start=1):
                    decision = _parse_line(path, number, raw)
                    if decision is not None and decision.ts > since:
                        decisions.append(decision)
        except OSError as exc:
            log.warning("Entscheidungsdatei nicht lesbar", file=path.name, error=str(exc))
        # stabil: bei gleicher Zeit bleibt die Reihenfolge der Datei
        decisions.sort(key=lambda decision: decision.ts)
        return decisions

    @staticmethod
    def _delete(path: Path) -> bool:
        """Löscht die Datei; ``False`` mit Log, wenn es nicht ging."""
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Entscheidungsdatei nicht gelöscht", file=path.name, error=str(exc))
            return False
        return True


def _parse_line(path: Path, number: int, raw: bytes) -> Decision | None:
    """Die Entscheidung aus einer Zeile; ``None`` bei einer leeren und (mit Warnung) unlesbaren."""
    if not raw.strip():
        return None
    try:
        return decision_from_json(json.loads(raw.decode("utf-8")))
    except (ValueError, RecursionError) as exc:  # kein UTF-8, kein JSON oder falsche Form
        log.warning(
            "Unlesbare Zeile im Entscheidungs-Spool übersprungen",
            file=path.name,
            line=number,
            error=str(exc),
        )
        return None
