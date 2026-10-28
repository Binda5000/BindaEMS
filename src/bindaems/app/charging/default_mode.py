"""Standard-Lademodus je Ladepunkt nach dem Abstecken (Plan Standard-Lademodus, Entscheidungen 3
bis 6).

Der ``ChargingTracker`` meldet nach jeder Beobachtung die abgesteckten Autos (``Unplugged``) und
den Stand der Wallboxen (``WallboxSeen``); ``DefaultModes.observed`` setzt daraufhin den Modus:

- Je Meldung gilt der Standard des Ladepunkts aus den **aktuellen** Einstellungen
  (``wallbox_default_modes``); fehlt er, geschieht nichts. Ziel ist das Fahrzeug der Session (nur
  ein Schlüssel aus ``config.yaml``) oder bei ``guest`` der Gast; „Geplant“ gibt es für den Gast
  nicht, dann geschieht ebenfalls nichts.
- Offen bleibt je Ziel höchstens ein Wunsch; eine neue Meldung für dasselbe Ziel ersetzt ihn.
- Bei jedem Aufruf entfallen die Wünsche, deren Ziel wieder an einem Ladepunkt steckt
  (``connected`` wahr). Dann wird jeder Wunsch einmal versucht: Der Modus des Ziels kommt aus
  ``IntentService.current()``; ist er schon der Standard, ist der Wunsch erledigt. Sonst geht
  ``SetMode`` bzw. ``SetGuestMode`` mit Urheber „Standard-Lademodus“ und Quelle ``system`` an
  den core, mit der Vorbedingung, dass der Modus seit dem Lesen unverändert ist. Erfolg,
  ``PreconditionFailed`` (jemand hat bedient, die Bedienung geht vor) und ``Rejected`` (Log)
  erledigen den Wunsch; bei ``CoreDown`` und ``ConflictAgain`` bleibt er offen und wird mit der
  nächsten Beobachtung (Health, etwa alle 10 s) nachgeholt.
- Bedient jemand den Modus, nachdem ein Wunsch beim Abstecken nicht ging, geht die Bedienung vor:
  Der Wunsch merkt sich den Modus des Ziels beim Abstecken (aus ``IntentService.cached()``,
  ``None`` ohne Absicht); weicht der gelesene Modus beim Nachholen davon ab, ist er erledigt. Die
  Version der Absicht taugt dafür nicht, der Plan erhöht sie laufend.

Ohne neue Meldung und ohne offenen Wunsch fragt ``observed`` den core nicht.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import structlog

from bindaems.app.charging.sessions import GUEST
from bindaems.app.charging.tracker import Unplugged, WallboxSeen
from bindaems.app.control.intent import (
    ConflictAgain,
    CoreDown,
    IntentService,
    PreconditionFailed,
    Rejected,
    SetGuestMode,
    SetMode,
)
from bindaems.app.settings.service import CurrentSettings
from bindaems.shared.config import Config
from bindaems.shared.control import ChargeMode, GuestIntent, Intent, VehicleIntent

log = structlog.get_logger(__name__)

ACTOR: Final = "Standard-Lademodus"  # Urheber im Änderungsprotokoll


@dataclass
class _Wish:
    """Ein offener Wechsel auf den Standard: Ladepunkt, Modus und der Modus beim Abstecken."""

    wallbox: str
    mode: ChargeMode
    before: ChargeMode | None  # ``None``: beim Abstecken war keine Absicht bekannt
    retry: bool = False  # schon einmal versucht: jetzt gilt eine Bedienung seit dem Abstecken


def _mode(intent: Intent | None, key: str) -> ChargeMode:
    """Modus des Fahrzeugs ``key`` oder des Gastes in der Absicht (ohne Eintrag der Standard)."""
    if key == GUEST:
        return (intent.guest if intent is not None else GuestIntent()).mode
    vehicles = intent.vehicles if intent is not None else {}
    return vehicles.get(key, VehicleIntent()).mode


class DefaultModes:
    """Setzt nach dem Abstecken den Standard-Lademodus des Ladepunkts."""

    def __init__(self, intents: IntentService, settings: CurrentSettings, cfg: Config) -> None:
        self._intents = intents
        self._settings = settings
        self._cfg = cfg
        self._wishes: dict[str, _Wish] = {}  # Fahrzeug oder ``guest``

    async def observed(self, unplugged: Sequence[Unplugged], seen: Sequence[WallboxSeen]) -> None:
        """Nimmt die Meldungen auf und versucht jeden offenen Wunsch einmal."""
        for report in unplugged:
            self._note(report)
        plugged = {item.occupant for item in seen if item.connected is True}
        for key in [key for key in self._wishes if key in plugged]:
            del self._wishes[key]  # wieder angesteckt: der Wunsch ist hinfällig
        if not self._wishes:
            return
        try:
            intent = await self._intents.current()
        except CoreDown:
            self._keep(list(self._wishes))
            return
        for key, wish in list(self._wishes.items()):
            if await self._try(key, wish, intent):
                del self._wishes[key]
            else:
                self._keep([key])

    def _note(self, report: Unplugged) -> None:
        """Merkt den Wunsch für das Ziel der Meldung, wenn der Ladepunkt einen Standard hat."""
        mode = self._settings.current().settings.wallbox_default_modes.get(report.wallbox)
        key = report.vehicle
        if mode is None or key is None:
            return
        if key == GUEST and mode is ChargeMode.PLANNED:
            return  # „Geplant“ gibt es für Gäste nicht
        if key != GUEST and key not in self._cfg.vehicles:
            return
        cached = self._intents.cached()
        before = _mode(cached, key) if cached is not None else None
        self._wishes[key] = _Wish(report.wallbox, mode, before)

    def _keep(self, keys: Sequence[str]) -> None:
        for key in keys:
            self._wishes[key].retry = True

    async def _try(self, key: str, wish: _Wish, intent: Intent | None) -> bool:
        """Ein Versuch; wahr, wenn der Wunsch erledigt ist."""
        read = _mode(intent, key)
        if read is wish.mode:
            return True
        if wish.retry and wish.before is not None and read is not wish.before:
            log.info("Standard-Lademodus entfällt, der Modus wurde bedient", vehicle=key)
            return True
        change = SetGuestMode(wish.mode) if key == GUEST else SetMode(key, wish.mode)
        try:
            await self._intents.apply(
                change,
                actor=ACTOR,
                source="system",
                precondition=lambda: _mode(self._intents.cached(), key) is read,
            )
        except PreconditionFailed:
            log.info("Standard-Lademodus entfällt, weil gerade bedient wurde", vehicle=key)
            return True
        except Rejected as exc:
            log.warning("Standard-Lademodus abgelehnt", vehicle=key, details=exc.details)
            return True
        except (CoreDown, ConflictAgain):
            return False
        log.info(
            "Standard-Lademodus gesetzt", wallbox=wish.wallbox, vehicle=key, mode=wish.mode.value
        )
        return True
