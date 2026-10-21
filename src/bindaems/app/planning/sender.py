"""Übergabe des Plans an den core (Plan 3c, Entscheidung 8, Präzisierung 11).

Der core bekommt in der Absicht den bindenden Teil des neuesten gültigen Plans
(``intent_plan``), mit dem Schalter „Netzladen erlaubt“, wie er jetzt steht. Er hat ihn, wenn der
Plan der Absicht in seinem Health dieselbe ``id`` und dasselbe ``grid_charge_allowed`` trägt. So
geht nach dem Ausschalten des Schalters sofort derselbe Plan ohne ``NETZLADEN`` hinaus.

Abgeglichen wird beim Start, nach jedem Planungslauf, nach jeder Verbindung zum core, nach jeder
Änderung der Einstellungen und sonst alle ``interval_s`` s. Ein Fehler wird geloggt, der nächste
Anstoß versucht es neu. Der Plan kommt nicht ins Änderungsprotokoll (Entscheidung 1).

Der Sender sendet nur auf einem Health, dem die app traut (``health_trusted``): Der core regelt
mit der Absicht, die er als gültige nennt. Sonst ist unbekannt, ob er den Plan hat, und die neue
Version entstünde auf einem Stand, dem ``IntentService`` nicht traut (Boost und Übersteuerung
blieben). Jede neue Version bindet im core Boost und Übersteuerung neu und gilt als nach seinem
Start angenommen: Nach einem Neustart bände sie einen wiederhergestellten oder schon verbrauchten
Boost an den nächsten Ansteckvorgang (Review Focus 1, Gesamtreview I1). Nicht zu senden schadet
nicht: Ein core, der so nicht regelt, führt auch keinen Plan aus. Nach einer Trennung verwirft
``LiveFeed`` das alte Health; jede neue Abfrage stößt den Sender an, so folgt der Plan dem ersten
Health, dem die app traut, binnen einer Abfrage.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import Final, Literal

import structlog

from bindaems.app.audit import Source
from bindaems.app.control.intent import (
    ConflictAgain,
    CoreDown,
    IntentService,
    Rejected,
    SetPlan,
    health_trusted,
)
from bindaems.app.core_link import LiveState
from bindaems.app.planning.execution import core_has_plan
from bindaems.app.planning.service import PlanningService
from bindaems.app.settings.service import SettingsService
from bindaems.shared.plan import IntentPlan, intent_plan
from bindaems.shared.timeutil import Clock

log = structlog.get_logger(__name__)

ACTOR: Final = "planung"
SOURCE: Final[Source] = "system"

SyncResult = Literal["sent", "current", "none", "failed"]


class PlanSender:
    """Hält den Plan in der Absicht des core aktuell.

    ``wake`` ist threadsicher: Die Einstellungen ändern sich auch aus Worker-Threads.
    """

    def __init__(
        self,
        intents: IntentService,
        planning: PlanningService,
        settings: SettingsService,
        live: LiveState,
        clock: Clock,
        *,
        interval_s: float = 30.0,
    ) -> None:
        self._intents = intents
        self._planning = planning
        self._settings = settings
        self._live = live
        self._clock = clock
        self._interval_s = interval_s
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None
        self._waiting: str | None = None  # der Grund, der zuletzt geloggt wurde, bis zum Senden
        planning.on_plan(lambda _plan: self.wake())
        settings.on_change(lambda _version: self.wake())

    def wake(self) -> None:
        """Stößt einen Abgleich an (threadsicher); vor ``run`` ohne Wirkung, ``run`` gleicht beim
        Start ohnehin ab."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        if threading.get_ident() == self._loop_thread:
            self._wake.set()
        else:
            with contextlib.suppress(RuntimeError):  # Schleife inzwischen geschlossen
                loop.call_soon_threadsafe(self._wake.set)

    async def connected(self) -> None:
        """Für ``LiveFeed.connect_handlers``: nach jeder Verbindung zum core abgleichen."""
        self.wake()

    def wanted(self) -> IntentPlan | None:
        """Der Plan, den der core haben soll; ``None`` ohne gültigen Plan."""
        plan = self._planning.current()
        if plan is None or not plan.valid_at(self._clock.now()):
            return None
        allowed = self._settings.current().settings.planning.grid_charge_allowed
        return intent_plan(plan, grid_charge_allowed=allowed)

    async def sync(self) -> SyncResult:
        """Ein Abgleich: ``none`` ohne gültigen Plan, ``current``, wenn der core ihn hat,
        ``sent`` nach dem Senden und ``failed`` ohne Verbindung, ohne Health, auf einem Health,
        dem die app nicht traut, oder bei einem Fehler. Das Warten auf Health loggt der Sender
        einmal je Grund."""
        wanted = self.wanted()
        if wanted is None:
            return "none"
        if not self._live.connected:
            return "failed"
        health = self._live.health
        if health is None:
            return self._wait("Plan wartet auf Health des core", wanted.id)
        # Hat der core den Plan schon, ist nichts zu übergeben; erst sonst zählt, ob er regelt
        if core_has_plan(health, wanted, grid_charge_allowed=wanted.grid_charge_allowed):
            self._waiting = None
            return "current"
        if not health_trusted(health):
            return self._wait("Plan wartet, bis der core mit der Absicht regelt", wanted.id)
        self._waiting = None
        fields = {"plan": wanted.id, "grid_charge_allowed": wanted.grid_charge_allowed}
        try:
            result = await self._intents.apply(SetPlan(wanted), actor=ACTOR, source=SOURCE)
        except CoreDown:
            log.info("Plan nicht übergeben: core nicht erreichbar", **fields)
            return "failed"
        except ConflictAgain:
            log.info("Plan nicht übergeben: Absicht zweimal gleichzeitig geändert", **fields)
            return "failed"
        except Rejected as exc:
            log.warning("Plan vom core abgelehnt", details=exc.details, **fields)
            return "failed"
        log.info(
            "Plan an den core übergeben", result=result.result, version=result.version, **fields
        )
        return "sent"

    def _wait(self, reason: str, plan_id: int) -> SyncResult:
        if self._waiting != reason:
            log.info(reason, plan=plan_id)
            self._waiting = reason
        return "failed"

    async def run(self) -> None:
        """Gleicht beim Start ab, dann auf jeden Anstoß, spätestens alle ``interval_s`` s."""
        self._loop, self._loop_thread = asyncio.get_running_loop(), threading.get_ident()
        while True:
            self._wake.clear()  # vor dem Abgleich: ein Anstoß währenddessen löst den nächsten aus
            try:
                await self.sync()
            except Exception:
                log.exception("Übergabe des Plans fehlgeschlagen")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval_s)
