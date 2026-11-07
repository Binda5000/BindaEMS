"""Lebenszeichen an einen externen Dienst (healthchecks.io-Stil).

Alle fünf Minuten geht ein ``POST <url>`` (in Ordnung) oder ``POST <url>/fail`` (Störung) mit einer
deutschen Textzeile als Körper hinaus. Bleibt der Ping aus oder meldet er eine Störung, schickt der
Dienst dem Betreiber eine Nachricht: bei Ausfall von VM, Cluster, Internet, core oder Netz.
Die URL ist ein Geheimnis und wird nie protokolliert oder angezeigt.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
import structlog
from pydantic import SecretStr

from bindaems.app.monitoring.backup_range import hours_text
from bindaems.app.monitoring.outages import Outage
from bindaems.app.status import ComponentStatus
from bindaems.shared.timeutil import LOCAL_TZ, Clock

log = structlog.get_logger(__name__)

CORE_GRACE = timedelta(seconds=120)
FIRST_PING_S = 30.0
REQUEST_TIMEOUT_S = 10.0
NAME = "Lebenszeichen"
SEP = " · "


@dataclass(frozen=True)
class Verdict:
    ok: bool
    text: str


@dataclass(frozen=True)
class Reading:
    """Eingaben des Urteils, ohne den Verbindungsverlauf (den führt der Heartbeat selbst)."""

    connected: bool
    mode: str | None
    reasons: Sequence[str]
    outage: Outage | None
    soc_pct: float | None
    range_now: float | None


def _hhmm(moment: datetime) -> str:
    return moment.astimezone(LOCAL_TZ).strftime("%H:%M")


def _soc_text(soc_pct: float | None) -> str | None:
    return None if soc_pct is None else f"SOC {round(soc_pct):d} %"


def verdict(
    *,
    connected: bool,
    down_since: datetime | None,
    now: datetime,
    mode: str | None,
    reasons: Sequence[str],
    outage: Outage | None,
    soc_pct: float | None,
    range_now: float | None,
) -> Verdict:
    """Urteil in der Reihenfolge core weg, Sicherer Zustand, Netzausfall, sonst in Ordnung."""
    if not connected and down_since is not None and now - down_since >= CORE_GRACE:
        return Verdict(False, f"core nicht erreichbar seit {_hhmm(down_since)}")
    if mode == "SAFE":
        return Verdict(False, "Sicherer Zustand: " + " ".join(reasons))
    soc = _soc_text(soc_pct)
    if outage is not None:
        parts = [f"Netzausfall seit {_hhmm(outage.start)}"]
        if soc is not None:
            parts.append(soc)
        if range_now is not None:
            parts.append(f"reicht {hours_text(range_now)} ohne PV")
        return Verdict(False, SEP.join(parts))
    parts = ["in Ordnung"]
    if mode is not None:
        parts.append(mode)
    if soc is not None:
        parts.append(soc)
    if range_now is not None:
        parts.append(f"USV {hours_text(range_now)}")
    return Verdict(True, SEP.join(parts))


class Heartbeat:
    def __init__(
        self,
        url: SecretStr | None,
        http: httpx.AsyncClient,
        clock: Clock,
        *,
        interval_s: float,
        inputs: Callable[[], Reading],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._url = None if url is None else url.get_secret_value().rstrip("/")
        self._http = http
        self._clock = clock
        self._interval_s = interval_s
        self._inputs = inputs
        self._sleep = sleep
        self._down_since: datetime | None = None
        self._failed_since: datetime | None = None
        self._last_ok: datetime | None = None
        self._last_text: str | None = None

    async def run(self) -> None:
        if self._url is None:
            log.info("Lebenszeichen aus: keine Ping-URL gesetzt")
            await asyncio.Event().wait()  # schläft dauerhaft, kein Neustart-Kreislauf
            return
        await self._sleep(FIRST_PING_S)
        while True:
            await self.send_once()
            await self._sleep(self._interval_s)

    def _current(self) -> Verdict:
        reading = self._inputs()
        now = self._clock.now()
        if reading.connected:
            self._down_since = None
        elif self._down_since is None:
            self._down_since = now
        return verdict(
            connected=reading.connected,
            down_since=self._down_since,
            now=now,
            mode=reading.mode,
            reasons=reading.reasons,
            outage=reading.outage,
            soc_pct=reading.soc_pct,
            range_now=reading.range_now,
        )

    async def send_once(self) -> bool:
        if self._url is None:
            return False
        current = self._current()
        target = self._url if current.ok else f"{self._url}/fail"
        now = self._clock.now()
        try:
            response = await self._http.post(
                target,
                content=current.text.encode("utf-8"),
                headers={"Content-Type": "text/plain; charset=utf-8"},
                timeout=REQUEST_TIMEOUT_S,
            )
            response.raise_for_status()
        except Exception as exc:  # nie nach außen; nur der Typ, nie die URL
            if self._failed_since is None:
                self._failed_since = now
                log.warning("Lebenszeichen nicht gesendet", fehler=type(exc).__name__)
            return False
        if self._failed_since is not None:
            log.info("Lebenszeichen wieder gesendet")
        self._failed_since = None
        self._last_ok = now
        self._last_text = current.text
        return True

    def component_status(self) -> ComponentStatus:
        if self._url is None:
            return ComponentStatus(NAME, True, "aus (keine Ping-URL)")
        if self._failed_since is not None:
            return ComponentStatus(
                NAME,
                False,
                f"nicht gesendet seit {_hhmm(self._failed_since)}",
                since=self._failed_since,
            )
        if self._last_ok is None:
            return ComponentStatus(NAME, True, "noch nicht gesendet")
        return ComponentStatus(
            NAME,
            True,
            f"zuletzt gesendet {_hhmm(self._last_ok)}",
            since=self._last_ok,
            details={"text": self._last_text},
        )
