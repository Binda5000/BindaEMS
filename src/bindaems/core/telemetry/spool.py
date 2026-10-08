"""Plattenpuffer für Line-Protocol-Zeilen, solange InfluxDB nicht erreichbar ist.

Eine Datei je Schreibvorgang, Name ``<epoch_ms>-<seq>-<rp>.lp``; die Namensordnung ist
die zeitliche Ordnung. Dateien entstehen atomar (temporäre Datei, dann umbenennen).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import structlog

from bindaems.core.telemetry.lineprotocol import epoch_ms
from bindaems.shared.timeutil import Clock

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SpoolFile:
    path: Path
    rp: str
    lines: list[str]


class DiskSpool:
    def __init__(self, directory: Path, max_bytes: int, max_age: timedelta, clock: Clock) -> None:
        self._dir = directory
        self._max_bytes = max_bytes
        self._max_age_ms = max_age // timedelta(milliseconds=1)
        self._clock = clock
        self._seq = 0
        directory.mkdir(parents=True, exist_ok=True)

    def append(self, rp: str, lines: Sequence[str]) -> None:
        if not lines:
            return
        now_ms = epoch_ms(self._clock.now())
        while True:
            target = self._dir / f"{now_ms:013d}-{self._seq:06d}-{rp}.lp"
            self._seq = (self._seq + 1) % 1_000_000
            if not target.exists():
                break
        temp = target.with_name(f".{target.name}.tmp")
        try:
            with temp.open("w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            temp.replace(target)
        except OSError:
            temp.unlink(missing_ok=True)
            raise

    def _files(self) -> list[Path]:
        return sorted(self._dir.glob("*.lp"))  # temporäre Dateien enden auf .tmp

    def oldest(self, n: int) -> list[SpoolFile]:
        result = []
        for path in self._files()[:n]:
            rp = path.stem.split("-", 2)[2]
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
            result.append(SpoolFile(path, rp, lines))
        return result

    def remove(self, path: Path) -> None:
        path.unlink(missing_ok=True)

    def size_bytes(self) -> int:
        return sum(path.stat().st_size for path in self._files())

    def enforce_limits(self) -> int:
        """Löscht die ältesten Dateien, bis Größe und Alter eingehalten sind."""
        files = self._files()
        sizes = {path: path.stat().st_size for path in files}
        total = sum(sizes.values())
        oldest_allowed_ms = epoch_ms(self._clock.now()) - self._max_age_ms
        removed = 0
        for path in files:
            too_old = int(path.name.split("-", 1)[0]) < oldest_allowed_ms
            if not too_old and total <= self._max_bytes:
                break
            path.unlink(missing_ok=True)
            total -= sizes[path]
            removed += 1
        if removed:
            log.warning(
                "Spool-Grenze erreicht, älteste Daten verworfen",
                removed_files=removed,
                spool_bytes=total,
            )
        return removed
