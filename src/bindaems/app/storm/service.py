"""Unwetterwarnungen von GeoSphere Austria: Abruf alle 15 min, bekannte Warnungen im Speicher.

Nur eine erfolgreiche Antwort ersetzt die Liste (eine fehlende Warnung ist zurückgenommen); ein
Ausfall der Quelle lässt die bekannten Warnungen bis zu ihrem Ende gelten, denn eine höhere
Reserve ist die sichere Richtung (Plan Unwetter-Reserve, Entscheidungen 1, 8 und 9).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Final

import httpx
import structlog

from bindaems.app.settings.service import SettingsService, SettingsVersion
from bindaems.app.status import ComponentStatus
from bindaems.app.storm.geosphere import WarningsUnavailable, fetch_warnings
from bindaems.app.storm.windows import relevant
from bindaems.shared.config import Config, LatLon
from bindaems.shared.plan import STORM_KIND_LABELS, STORM_LEVEL_LABELS, StormWarning
from bindaems.shared.timeutil import LOCAL_TZ, Clock

POLL: Final = timedelta(minutes=15)
STALE_AFTER: Final = timedelta(hours=1)
NAME: Final = "storm"  # Schlüssel wie bei den anderen; die UI zeigt „Unwetterwarnungen“

log = structlog.get_logger(__name__)

Fetch = Callable[[httpx.AsyncClient, Clock, LatLon], Awaitable[list[StormWarning]]]


class StormService:
    def __init__(
        self,
        cfg: Config,
        settings: SettingsService,
        http: httpx.AsyncClient,
        clock: Clock,
        fetch: Fetch = fetch_warnings,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._cfg = cfg
        self._settings = settings
        self._http = http
        self._clock = clock
        self._fetch = fetch
        self._sleep = sleep
        self._known: list[StormWarning] = []
        self._last_ok: datetime | None = None
        self._first_try: datetime | None = None
        self._failing = False
        # Das Einschalten weckt ``run``; ``on_change`` kommt aus Worker-Threads
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._was_enabled = settings.current().settings.planning.storm.enabled
        settings.on_change(self._on_settings)

    def _on_settings(self, version: SettingsVersion) -> None:
        enabled = version.settings.planning.storm.enabled
        was, self._was_enabled = self._was_enabled, enabled
        if enabled == was:
            return
        # Ein- wie Ausschalten verwirft den alten Stand: nichts davon darf später durchscheinen
        self._known = []
        self._last_ok = self._first_try = None
        self._failing = False
        if enabled and self._loop is not None:
            with contextlib.suppress(RuntimeError):  # Schleife schon geschlossen
                self._loop.call_soon_threadsafe(self._wake.set)

    def warnings(self) -> list[StormWarning]:
        """Bekannte, noch nicht zu Ende gegangene Warnungen, die nach den Einstellungen zählen."""
        now = self._clock.now()
        live = [w for w in self._known if w.end > now]
        return relevant(live, self._settings.current().settings.planning.storm)

    def fingerprint(self) -> tuple[tuple[int, int, datetime, datetime], ...]:
        return tuple((w.warn_id, w.version, w.begin, w.end) for w in self.warnings())

    async def refresh(self) -> None:
        """Ein Abruf; bei ausgeschaltetem Schalter keiner. Ein Fehler lässt die Liste stehen."""
        if not self._settings.current().settings.planning.storm.enabled:
            return
        now = self._clock.now()
        if self._first_try is None:
            self._first_try = now
        try:
            found = await self._fetch(self._http, self._clock, self._cfg.site.location)
        except WarningsUnavailable as exc:
            self._failing = True
            log.warning("Unwetterwarnungen nicht abrufbar", error=str(exc))
            return
        self._failing = False
        self._last_ok = now
        # je Warnung gilt die höchste Version
        newest: dict[int, StormWarning] = {}
        for warning in found:
            kept = newest.get(warning.warn_id)
            if kept is None or warning.version >= kept.version:
                newest[warning.warn_id] = warning
        self._known = list(newest.values())

    async def run(self) -> None:
        """Sofort abrufen, dann alle ``POLL``; ausgeschaltet wird nur gewartet."""
        self._loop = asyncio.get_running_loop()
        while True:
            self._wake.clear()
            try:
                await self.refresh()
            except Exception:  # die Schleife darf nie stehen bleiben
                log.exception("Unwetterwarnungen fehlgeschlagen")
            waiting = asyncio.ensure_future(self._sleep(POLL.total_seconds()))
            woken = asyncio.ensure_future(self._wake.wait())
            try:
                await asyncio.wait({waiting, woken}, return_when=asyncio.FIRST_COMPLETED)
                if waiting.done():
                    waiting.result()  # ein Abbruch im Warten (Test) geht weiter
            finally:
                for task in (waiting, woken):
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

    def component_status(self) -> ComponentStatus:
        if not self._settings.current().settings.planning.storm.enabled:
            return ComponentStatus(NAME, True, "aus")
        now = self._clock.now()
        since = self._last_ok or self._first_try
        if self._failing and since is not None and now - since > STALE_AFTER:
            local = since.astimezone(LOCAL_TZ)
            return ComponentStatus(NAME, False, f"Warnungen seit {local:%H:%M} nicht abrufbar")
        active = self.warnings()
        if not active:
            return ComponentStatus(NAME, True, "keine Warnung")
        parts = [
            f"{STORM_KIND_LABELS[w.kind]} ({STORM_LEVEL_LABELS[w.level]}) bis "
            f"{w.end.astimezone(LOCAL_TZ):%H:%M}"
            for w in active
        ]
        return ComponentStatus(NAME, True, "; ".join(parts))
