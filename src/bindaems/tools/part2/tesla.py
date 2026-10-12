"""Teil Tesla des Prüfprotokolls Teil 2 (Spec 17.2-7): Befehle über Tessie, Wirkung am TWC.

Die Schritte schreiben über den ``TeslaActuator`` des core (``Part2Session``). Tessie weckt ein
Fahrzeug bei Befehlen selbst; die Sitzung sendet darum nur, solange der Aktor steuerbar ist
(online, zu Hause, angesteckt), und der Teil weckt den Tesla nie (Spec 3.7, 6.5). Die Wirkung misst
er am Wall Connector, an dem das Fahrzeug lädt (``default_wallbox``). Der Ladestrom dort ist der
größte Phasenstrom (``wallbox.<twc>.l1.current_a`` bis ``l3``).

1. **Tesla bereit:** steuerbar, Ladestrom bekannt und innerhalb der Grenzen (sonst ließe er sich
   nicht wiederherstellen); nach dem Start wartet der Schritt bis ``confirm_s`` auf die ersten
   Werte von Tessie und Wall Connector. Sonst endet der Teil ohne Schreiben.
2. **Ladestrom 10 A** (falls zwischen Mindest- und Höchststrom) und **Verhalten bei** ``min_a``:
   Tessie bestätigt den Befehl, dann wartet der Schritt bis ``effect_s``, bis der Strom am Wall
   Connector höchstens 1,5 A vom Sollstrom abweicht. Ist kein Fahrzeug am Wall Connector oder lag
   der Strom dort schon beim Sollstrom, ist die Wirkung nicht messbar. Das steht im Befund.
3. **Laden aus und an** (Rückfrage): Die Rückfrage hält den Adapter von Tessie an, und das Auto
   kann inzwischen schlafen. Nach der Antwort wartet der Schritt darum bis ``confirm_s`` auf neue
   Werte von Tessie; kommen keine, endet er mit FEHLER, und es geht nichts hinaus. Dann bestätigt
   Tessie „aus“, und der Schritt wartet bis ``effect_s``, bis der Schütz des Wall Connectors offen
   ist; danach geht das Laden wieder an.

Nach jedem Block (die beiden Ströme zählen als einer) stellt ``Part2Session.restore_after_step`` die
vorgefundenen Werte wieder her, mit ``confirm_s`` als Frist je Wert: Ein Befehl an Tessie kann bis
zu 60 s dauern. Die Zeilen stehen hinter dem Block (Präzisierung 11). Alle Schritte laufen auch
mit ``--yes``; keiner braucht einen Handgriff des Betreibers.

Mit ``asleep_check`` (``--schlafend``) läuft nur der letzte Schritt, und es wird nichts gesendet:
Er verlangt ein schlafendes Fahrzeug und prüft dann ``sleep_total_s`` lang alle ``sleep_check_s``
über ``capabilities``, dass der Aktor Befehle verweigert, weil das Fahrzeug schläft. Wacht es auf,
ist das eine Warnung: Das Werkzeug hat nichts gesendet. OK ist der Schritt nur, wenn Tessie zu
Beginn und am Ende mit Qualität OK meldet, dass das Fahrzeug schläft, und die Meldung am Ende frisch
ist. Fällt Tessie aus, bleiben seine Werte bis zu 900 s gültig, ohne dass jemand das Fahrzeug
beobachtet: Dann ist der Schritt FEHLER „nicht beobachtbar: …“ mit dem Grund.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from bindaems.core.actuators.tesla import REASON_ASLEEP, REASON_NOT_REACHABLE
from bindaems.shared.config import Config, TwcConfig, VehicleConfig
from bindaems.shared.control import CommandKind, CommandValue, vehicle_actuator
from bindaems.shared.domain import Quality, Snapshot
from bindaems.shared.numbers import de, finite_number
from bindaems.tools.part2.session import STALE_AFTER_QUESTION, Part2Session, minutes, seconds
from bindaems.tools.verify_checks import Finding, Status

ITEM: Final = "17.2-7"
TEST_CURRENT_A: Final = 10  # Strom zwischen Mindest- und Höchststrom
TOLERANCE_A: Final = 1.5  # so weit darf der Strom am Wall Connector vom Sollstrom abweichen
SKIPPED: Final = "übersprungen"
NO_VEHICLE: Final = "Kein Fahrzeug „{}“ mit Tessie in vehicles."  # mit dem Schlüssel des Fahrzeugs

QUESTION_CHARGING: Final = "Darf der Tesla kurz „Laden aus“ und wieder „an“ schalten?"
REASON_UNKNOWN_CURRENT: Final = "Ladestrom unbekannt."
REASON_AWAKE: Final = "Fahrzeug ist wach; die Prüfung braucht ein schlafendes Fahrzeug."
REASON_UNKNOWN_STATE: Final = (
    "Zustand des Fahrzeugs unbekannt; die Prüfung braucht ein schlafendes Fahrzeug."
)
# Der Schlaf-Test ohne frische Meldung von Tessie: Er hat nichts beobachtet.
UNOBSERVABLE: Final = "nicht beobachtbar: "
REASON_STALE: Final = "Wert veraltet; Tessie hat sich zum Ende der Prüfung nicht neu gemeldet."
# Warum sich die Wirkung am Wall Connector nicht messen lässt
NOT_AT_TWC: Final = "nicht am Wall Connector"
TWC_SILENT: Final = "Wall Connector ohne Messwerte"


@dataclass(frozen=True)
class TeslaTimings:
    """Fristen der Schritte in Sekunden.

    - ``confirm_s``: Frist für Tessie je Befehl, auch beim Wiederherstellen (die 60 s des Befehls
      und etwas Luft, wie ``TeslaActuator.confirm_timeout_s``); so lange wartet der Schritt
      „Tesla bereit“ auch auf die ersten Werte
    - ``effect_s``: so lange wartet ein Schritt nach der Bestätigung auf die Wirkung am Wall
      Connector
    - ``sleep_total_s``: so lange dauert die Prüfung des schlafenden Fahrzeugs
    - ``sleep_check_s``: Abstand ihrer Prüfungen
    """

    confirm_s: float = 75.0
    effect_s: float = 90.0
    sleep_total_s: float = 600.0
    sleep_check_s: float = 60.0


DEFAULT_TIMINGS: Final = TeslaTimings()  # die Fristen an der Anlage


def _amps(value: float | None) -> str:
    return f"{de(round(value, 1))} A" if value is not None else "unbekannt"


async def run_tesla(
    session: Part2Session,
    cfg: Config,
    key: str,
    *,
    asleep_check: bool = False,
    timings: TeslaTimings = DEFAULT_TIMINGS,
) -> list[Finding]:
    """Führt den Teil Tesla für das Fahrzeug ``key`` aus; die Befunde stehen in Berichtsreihenfolge.

    Mit ``asleep_check`` läuft nur die Prüfung des schlafenden Fahrzeugs, ohne zu senden. Jeder
    Befund steht auch sofort in ``session.findings``. Die Werte stellt der Teil nach jedem Block
    selbst wieder her; ``session.restore`` räumt danach auf. ``key`` muss ein Fahrzeug mit
    ``tessie`` sein, sonst ``ValueError``.
    """
    vehicle = cfg.vehicles.get(key)
    if vehicle is None or vehicle.tessie is None:
        raise ValueError(NO_VEHICLE.format(key))
    first = len(session.findings)
    await _TeslaSteps(session, cfg, key, vehicle, timings).run(asleep_check)
    return session.findings[first:]


class _TeslaSteps:
    """Die Schritte eines Laufs, je Block mit Wiederherstellen danach."""

    def __init__(
        self,
        session: Part2Session,
        cfg: Config,
        key: str,
        vehicle: VehicleConfig,
        timings: TeslaTimings,
    ) -> None:
        self._session = session
        self._key = key
        self._vehicle = vehicle
        self._id = vehicle_actuator(key)
        self._actuator = session.actuators[self._id]
        # Der Wall Connector, an dem das Fahrzeug lädt. An der EVCS gäbe es dort nichts zu messen,
        # und ein anderer Wall Connector könnte ein anderes Auto zeigen.
        home = vehicle.default_wallbox
        self._twc = home if isinstance(cfg.wallboxes.get(home), TwcConfig) else None
        self._t = timings

    async def run(self, asleep_check: bool) -> None:
        if asleep_check:
            await self._asleep()
            return
        if not await self._ready():
            return
        for step in (self._currents, self._charging):
            await step()
            await self._session.restore_after_step(self._t.confirm_s)

    async def _ready(self) -> bool:
        """Tesla bereit: steuerbar und mit bekanntem Ladestrom, sonst FEHLER und Ende."""
        title = "Tesla bereit"
        await self._session.wait_for(self._reported, self._t.confirm_s)
        snap = self._session.snapshot()
        capabilities = self._actuator.capabilities(snap, snap.ts)
        if not capabilities.controllable:
            self._record(title, "fail", " ".join(capabilities.reasons))
            return False
        current = finite_number(self._actuator.present(snap).get(CommandKind.CURRENT))
        if current is None:
            self._record(title, "fail", REASON_UNKNOWN_CURRENT)
            return False
        problem = self._session.found_problem(self._id, CommandKind.CURRENT, current)
        if problem is not None:
            self._record(title, "fail", problem)
            return False
        soc = finite_number(snap.ok(self._signal("soc_pct")))
        state = snap.ok(self._signal("charging_state"))
        soc_text = f"SOC {de(soc)} %" if soc is not None else "SOC unbekannt"
        state_text = state if isinstance(state, str) else "Ladezustand unbekannt"
        self._record(title, "ok", f"{soc_text}, Ladestrom {de(current)} A, {state_text}")
        return True

    def _reported(self, snap: Snapshot) -> bool:
        """Haben Tessie und, falls es einen gibt, der Wall Connector schon Werte gemeldet?"""
        if not self._online_known(snap):
            return False
        return self._twc is None or snap.ok(self._twc_signal("vehicle_connected")) is not None

    def _online_known(self, snap: Snapshot) -> bool:
        return snap.ok(self._signal("online")) is not None

    async def _currents(self) -> None:
        """10 A (falls zwischen Mindest- und Höchststrom) und der Mindeststrom."""
        low, high = self._vehicle.min_a, self._vehicle.max_a
        if low < TEST_CURRENT_A <= high:
            await self._current(f"Ladestrom {de(TEST_CURRENT_A)} A", TEST_CURRENT_A)
        await self._current(f"Verhalten bei {de(low)} A", low)

    async def _current(self, title: str, amps: int) -> None:
        """Setzt den Strom; Tessie bestätigt, und der Wall Connector soll ihm folgen.

        Die Zeit am Wall Connector zählt ab dem Senden. Lag der Strom dort schon bei der Bestätigung
        am Sollwert, ist es die Zeit bis dahin: Die Wirkung kam spätestens dann.
        """
        session = self._session
        before = session.snapshot()
        tessie = await self._set(CommandKind.CURRENT, float(amps))
        if tessie is None:
            self._record(title, "fail", self._failure())
            return
        detail = f"Tessie {seconds(tessie)} s"
        reason = self._unmeasurable(before)
        if reason is None and self._at_current(before, amps):
            reason = f"Strom am Wall Connector schon bei {de(amps)} A"
        if reason is not None:
            self._record(title, "ok", f"{detail}; Wirkung nicht messbar ({reason})")
            return
        waited = await session.wait_for(lambda snap: self._at_current(snap, amps), self._t.effect_s)
        if waited is not None:
            self._record(title, "ok", f"{detail}, Wall Connector {seconds(tessie + waited)} s")
            return
        actual = _amps(self._wall_connector_current(session.snapshot()))
        text = f"Wall Connector nach {de(self._t.effect_s)} s nicht bei {de(amps)} A (Ist {actual})"
        self._record(title, "warn", f"{detail}; {text}")

    def _wall_connector_current(self, snap: Snapshot) -> float | None:
        """Der größte Phasenstrom am Wall Connector; ``None``, wenn keine Phase bekannt ist."""
        phases = (finite_number(snap.ok(self._twc_signal(f"l{n}.current_a"))) for n in (1, 2, 3))
        known = [current for current in phases if current is not None]
        return max(known) if known else None

    def _at_current(self, snap: Snapshot, amps: float) -> bool:
        current = self._wall_connector_current(snap)
        return current is not None and abs(current - amps) <= TOLERANCE_A

    def _unmeasurable(self, before: Snapshot) -> str | None:
        """Warum sich am Wall Connector nichts messen lässt; ``None``, wenn dort ein Auto hängt."""
        if self._twc is None:
            return NOT_AT_TWC
        connected = before.ok(self._twc_signal("vehicle_connected"))
        if connected is None:
            return TWC_SILENT
        return None if connected is True else NOT_AT_TWC

    async def _charging(self) -> None:
        """Laden aus und wieder an; am Wall Connector soll der Schütz öffnen."""
        title = "Laden aus und an"
        session = self._session
        if not session.ask(QUESTION_CHARGING):
            self._record(title, "info", SKIPPED)
            return
        # Solange die Frage offen war, stand der Adapter von Tessie. Erst wenn er sich wieder
        # gemeldet hat, ist bekannt, ob das Auto noch wach ist; ein Befehl weckte ein schlafendes.
        if not await session.fresh_after_question(self._id, self._t.confirm_s):
            self._record(title, "fail", STALE_AFTER_QUESTION)
            return
        before = session.snapshot()
        off = await self._set(CommandKind.CHARGING, False)
        if off is None:
            self._record(title, "fail", f"Laden aus: {self._failure()}")
            return
        status, contactor = await self._contactor(before, off)
        on = await self._set(CommandKind.CHARGING, True)
        detail = f"aus nach {seconds(off)} s ({contactor})"
        if on is None:
            self._record(title, "fail", f"{detail}, Laden an: {self._failure()}")
            return
        self._record(title, status, f"{detail}, an nach {seconds(on)} s")

    async def _contactor(self, before: Snapshot, off: float) -> tuple[Status, str]:
        """Öffnet der Schütz des Wall Connectors? Status und Text für den Befund.

        ``off`` sind die Sekunden, bis Tessie „aus“ bestätigte; die Zeit bis zum offenen Schütz
        zählt wie bei den Strömen ab dem Senden.
        """
        reason = self._unmeasurable(before)
        if reason is not None:
            return "ok", f"Wirkung nicht messbar: {reason}"
        if before.ok(self._twc_signal("contactor_closed")) is False:
            return "ok", "Schütz schon offen"
        waited = await self._session.wait_for(self._contactor_open, self._t.effect_s)
        if waited is not None:
            return "ok", f"Schütz offen nach {seconds(off + waited)} s"
        return "warn", f"Schütz nach {de(self._t.effect_s)} s nicht offen"

    def _contactor_open(self, snap: Snapshot) -> bool:
        return snap.ok(self._twc_signal("contactor_closed")) is False

    async def _asleep(self) -> None:
        """Schläft das Fahrzeug weiter, ohne dass etwas gesendet wird? Der Schritt sendet nie.

        Zu Beginn muss Tessie erreichbar sein und mit Qualität OK „schläft“ melden. Am Ende wartet
        der Schritt bis ``confirm_s`` auf eine frische Meldung: mit Qualität OK und mindestens
        ``sleep_total_s`` nach der Meldung zu Beginn. So überspannen die beiden Beobachtungen die
        ganze Prüfung, auch wenn Tessie ein schlafendes Fahrzeug nur alle 10 min abfragt. Ohne
        sie ist der Schritt FEHLER „nicht beobachtbar: …“: Tessie nicht erreichbar oder der Wert
        veraltet.
        """
        title = "Schlafendes Auto wird nicht geweckt"
        session = self._session
        await session.wait_for(self._online_known, self._t.confirm_s)
        snap = session.snapshot()
        if not self._reachable(snap):
            self._record(title, "fail", f"{UNOBSERVABLE}{REASON_NOT_REACHABLE}")
            return
        online = snap.get(self._signal("online"))
        if online is None or online.quality is not Quality.OK or online.value is not False:
            awake = online is not None and online.quality is Quality.OK and online.value is True
            self._record(title, "fail", REASON_AWAKE if awake else REASON_UNKNOWN_STATE)
            return
        fresh_from = online.ts + timedelta(seconds=self._t.sleep_total_s)
        started = session.monotonic()
        deadline = started + self._t.sleep_total_s
        while (remaining := deadline - session.monotonic()) > 0:
            await asyncio.sleep(min(self._t.sleep_check_s, remaining))
            if not self._refuses(session.snapshot()):
                self._woke(title, started)
                return
        await session.wait_for(lambda snap: self._fresh(snap, fresh_from), self._t.confirm_s)
        snap = session.snapshot()
        if not self._fresh(snap, fresh_from):
            reason = REASON_STALE if self._reachable(snap) else REASON_NOT_REACHABLE
            self._record(title, "fail", f"{UNOBSERVABLE}{reason}")
            return
        if not self._refuses(snap):
            self._woke(title, started)
            return
        total = minutes(self._t.sleep_total_s)
        self._record(title, "ok", f"{total} min ohne Wecken, Befehle verweigert")

    def _woke(self, title: str, started: float) -> None:
        woke = minutes(self._session.monotonic() - started)
        detail = f"Fahrzeug wurde nach {woke} min wach; Ursache prüfen (App, Zeitplan)"
        self._record(title, "warn", detail)

    def _fresh(self, snap: Snapshot, since: datetime) -> bool:
        """Hat Tessie ``online`` seit ``since`` mit Qualität OK gemeldet?"""
        online = snap.get(self._signal("online"))
        return online is not None and online.quality is Quality.OK and online.ts >= since

    def _reachable(self, snap: Snapshot) -> bool:
        """Ist Tessie für das Fahrzeug erreichbar? Das sagen die Gründe der ``capabilities``."""
        return REASON_NOT_REACHABLE not in self._actuator.capabilities(snap, snap.ts).reasons

    def _refuses(self, snap: Snapshot) -> bool:
        """Verweigert der Aktor Befehle mit dem Grund, dass das Fahrzeug schläft?"""
        capabilities = self._actuator.capabilities(snap, snap.ts)
        return not capabilities.controllable and REASON_ASLEEP in capabilities.reasons

    async def _set(self, kind: CommandKind, value: CommandValue) -> float | None:
        """``set_and_confirm`` mit ``confirm_s``."""
        return await self._session.set_and_confirm(self._id, kind, value, self._t.confirm_s)

    def _failure(self) -> str:
        return self._session.failure or f"nicht bestätigt nach {de(self._t.confirm_s)} s"

    def _signal(self, name: str) -> str:
        return f"vehicle.{self._key}.{name}"

    def _twc_signal(self, name: str) -> str:
        return f"wallbox.{self._twc}.{name}"

    def _record(self, title: str, status: Status, detail: str) -> None:
        self._session.record(Finding(ITEM, title, status, detail))
