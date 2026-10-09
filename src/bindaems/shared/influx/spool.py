"""Plattenpuffer für Line-Protocol-Zeilen, solange InfluxDB nicht erreichbar ist.

Eine Datei je Schreibvorgang, Name ``<epoch_ms>-<seq>-<rp>.lp``; die Namensordnung ist
die zeitliche Ordnung. Dateien entstehen atomar (temporäre Datei, dann umbenennen).

Das Verzeichnis wird nur beim Start gelesen; danach führt der Spool einen Index im Speicher.
Bei langen Ausfällen liegen zehntausende Dateien im Verzeichnis – ein Scan je Schreibvorgang
würde die Ereignisschleife des core blockieren.
"""

from __future__ import annotations

import bisect
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import structlog

from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.timeutil import Clock

log = structlog.get_logger(__name__)

_NAME = re.compile(r"(?P<ms>\d{13})-(?P<seq>\d{6})-(?P<rp>[A-Za-z0-9_]+)\.lp")


@dataclass(frozen=True)
class SpoolFile:
    path: Path
    rp: str
    lines: list[str]


# Indexeintrag: (Dateiname, epoch_ms, rp, Größe) – sortiert nach Dateiname
_Entry = tuple[str, int, str, int]


class DiskSpool:
    def __init__(self, directory: Path, max_bytes: int, max_age: timedelta, clock: Clock) -> None:
        self._dir = directory
        self._max_bytes = max_bytes
        self._max_age_ms = max_age // timedelta(milliseconds=1)
        self._clock = clock
        self._seq = 0
        directory.mkdir(parents=True, exist_ok=True)
        self._entries: list[_Entry] = []
        self._total = 0
        foreign = []
        for path in directory.glob("*.lp"):  # einmalig beim Start; Temp-Dateien enden auf .tmp
            match = _NAME.fullmatch(path.name)
            if match is None:
                foreign.append(path.name)
                continue
            size = path.stat().st_size
            self._entries.append((path.name, int(match["ms"]), match["rp"], size))
            self._total += size
        self._entries.sort()
        if foreign:
            log.warning("Fremde Dateien im Spool werden ignoriert", files=sorted(foreign)[:10])

    def append(self, rp: str, lines: Sequence[str]) -> None:
        if not lines:
            return
        now_ms = epoch_ms(self._clock.now())
        while True:
            target = self._dir / f"{now_ms:013d}-{self._seq:06d}-{rp}.lp"
            self._seq = (self._seq + 1) % 1_000_000
            if not target.exists():
                break
        data = ("\n".join(lines) + "\n").encode("utf-8")
        temp = target.with_name(f".{target.name}.tmp")
        try:
            with temp.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            temp.replace(target)
        except OSError:
            temp.unlink(missing_ok=True)
            raise
        bisect.insort(self._entries, (target.name, now_ms, rp, len(data)))
        self._total += len(data)

    def oldest(self, n: int) -> list[SpoolFile]:
        result = []
        for name, _, rp, _ in self._entries[:n]:
            path = self._dir / name
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:  # von außen gelöscht
                self._forget(name)
                continue
            result.append(SpoolFile(path, rp, [line for line in text.splitlines() if line]))
        return result

    def remove(self, path: Path) -> None:
        path.unlink(missing_ok=True)
        self._forget(path.name)

    def size_bytes(self) -> int:
        return self._total

    def enforce_limits(self) -> int:
        """Löscht die ältesten Dateien, bis Größe und Alter eingehalten sind."""
        oldest_allowed_ms = epoch_ms(self._clock.now()) - self._max_age_ms
        removed = 0
        while self._entries:
            name, ms, _, size = self._entries[0]
            if ms >= oldest_allowed_ms and self._total <= self._max_bytes:
                break
            (self._dir / name).unlink(missing_ok=True)
            del self._entries[0]
            self._total -= size
            removed += 1
        if removed:
            log.warning(
                "Spool-Grenze erreicht, älteste Daten verworfen",
                removed_files=removed,
                spool_bytes=self._total,
            )
        return removed

    def _forget(self, name: str) -> None:
        index = bisect.bisect_left(self._entries, (name,))
        if index < len(self._entries) and self._entries[index][0] == name:
            self._total -= self._entries[index][3]
            del self._entries[index]
