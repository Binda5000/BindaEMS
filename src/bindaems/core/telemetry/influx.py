"""Schreibt Messpunkte gebündelt und gzip-komprimiert in InfluxDB 1.x.

``write()`` ist synchron und wartet nie auf das Netz. Was nicht gesendet werden kann, landet
im Spool und wird nach der nächsten erfolgreichen Übertragung nachgesendet. Eine Antwort 400
(fehlerhafte Zeilen) verwirft die Zeilen – ein erneuter Versuch würde wieder scheitern.
"""

from __future__ import annotations

import asyncio
import gzip
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum

import httpx
import structlog
from pydantic import SecretStr

from bindaems.core.telemetry.lineprotocol import Point, to_line
from bindaems.core.telemetry.spool import DiskSpool
from bindaems.shared.config import InfluxConfig

log = structlog.get_logger(__name__)

TIMEOUT_S = 10.0
DRAIN_FILES = 10


@dataclass(frozen=True)
class WriterStats:
    sent_lines: int
    spooled_lines: int
    dropped_lines: int
    spool_bytes: int


class _Outcome(Enum):
    SENT = "sent"
    REJECTED = "rejected"  # 400: Zeilen fehlerhaft
    RETRY = "retry"  # Netzwerk, 5xx, 429, Anmeldung …: später erneut


class InfluxWriter:
    def __init__(
        self,
        cfg: InfluxConfig,
        password: SecretStr | None,
        client: httpx.AsyncClient,
        spool: DiskSpool,
        *,
        flush_s: float = 1.0,
        batch_lines: int = 5000,
        queue_max: int = 100_000,
    ) -> None:
        self._url = cfg.url.rstrip("/") + "/write"
        self._database = cfg.database
        self._auth = (
            (cfg.username, password.get_secret_value() if password is not None else "")
            if cfg.username is not None
            else None
        )
        self._client = client
        self._spool = spool
        self._flush_s = flush_s
        self._batch_lines = batch_lines
        self._queue_max = queue_max
        self._queue: deque[tuple[str, str]] = deque()
        self._lock = asyncio.Lock()
        self._sent = 0
        self._spooled = 0
        self._dropped = 0

    def write(self, point: Point) -> None:
        try:
            line = to_line(point)
        except ValueError as exc:
            self._dropped += 1
            log.error("Messpunkt verworfen", measurement=point.measurement, error=str(exc))
            return
        if len(self._queue) >= self._queue_max:
            pending, self._queue = self._queue, deque()
            self._to_spool(pending)
        self._queue.append((point.rp, line))

    def stats(self) -> WriterStats:
        return WriterStats(self._sent, self._spooled, self._dropped, self._spool.size_bytes())

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._flush_s)
            await self.flush_now()

    async def flush_now(self) -> None:
        async with self._lock:
            pending, self._queue = self._queue, deque()
            by_rp: dict[str, list[str]] = defaultdict(list)
            for rp, line in pending:
                by_rp[rp].append(line)
            reachable = True
            sent_any = False
            for rp, lines in by_rp.items():
                for start in range(0, len(lines), self._batch_lines):
                    batch = lines[start : start + self._batch_lines]
                    if not reachable:
                        self._spool_lines(rp, batch)
                        continue
                    outcome = await self._send(rp, batch)
                    if outcome is _Outcome.SENT:
                        sent_any = True
                    elif outcome is _Outcome.RETRY:
                        reachable = False
                        self._spool_lines(rp, batch)
            if reachable and sent_any:
                await self._drain()

    async def _drain(self) -> None:
        try:
            for spooled in self._spool.oldest(DRAIN_FILES):
                outcome = await self._send(spooled.rp, spooled.lines)
                if outcome is _Outcome.RETRY:
                    return
                self._spool.remove(spooled.path)
        except OSError as exc:
            log.error("Spool nicht lesbar", error=str(exc))

    async def _send(self, rp: str, lines: list[str]) -> _Outcome:
        body = gzip.compress(("\n".join(lines)).encode("utf-8"))
        try:
            response = await self._client.post(
                self._url,
                params={"db": self._database, "rp": rp, "precision": "ms"},
                content=body,
                headers={
                    "Content-Encoding": "gzip",
                    "Content-Type": "text/plain; charset=utf-8",
                },
                auth=self._auth if self._auth is not None else httpx.USE_CLIENT_DEFAULT,
                timeout=TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            log.warning("InfluxDB nicht erreichbar", error=f"{type(exc).__name__}: {exc}")
            return _Outcome.RETRY
        if response.is_success:
            self._sent += len(lines)
            return _Outcome.SENT
        if response.status_code == 400:
            self._dropped += len(lines)
            log.error("InfluxDB lehnt Zeilen ab", lines=len(lines), response=response.text[:500])
            return _Outcome.REJECTED
        log.warning("InfluxDB-Schreibfehler", status=response.status_code)
        return _Outcome.RETRY

    def _to_spool(self, pending: deque[tuple[str, str]]) -> None:
        by_rp: dict[str, list[str]] = defaultdict(list)
        for rp, line in pending:
            by_rp[rp].append(line)
        for rp, lines in by_rp.items():
            self._spool_lines(rp, lines)

    def _spool_lines(self, rp: str, lines: list[str]) -> None:
        try:
            self._spool.append(rp, lines)
        except OSError as exc:  # z. B. Platte voll: verwerfen statt den Schreiber anzuhalten
            self._dropped += len(lines)
            log.error(
                "Spool nicht beschreibbar, Zeilen verworfen", lines=len(lines), error=str(exc)
            )
            return
        self._spooled += len(lines)
        try:
            self._spool.enforce_limits()
        except OSError as exc:
            log.error("Spool-Grenzen nicht durchsetzbar", error=str(exc))
