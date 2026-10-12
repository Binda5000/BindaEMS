"""Teil EVCS des Prüfprotokolls Teil 2 (Spec 17.2-6, sicherer Strom über den Watchdog 17.2-5).

Die Schritte schreiben über den GX-Dienst ``evcharger`` wie der core (``Part2Session``):

1. **EVCS bereit:** steuerbar (Instanz, Cerbo verbunden, manueller Modus), Sollstrom bekannt und
   innerhalb der Grenzen (sonst ließe er sich nicht wiederherstellen); nach dem Start wartet der
   Schritt bis ``confirm_s`` auf diese Werte. Sonst endet der Teil ohne Schreiben.
2. **Strom:** ``min_a``, 10 A (falls dazwischen) und ``max_a``, je mit Rücklesen.
3. **Laden aus und an** (Rückfrage); lud ein Fahrzeug (über 500 W), dazu die Zeit, bis die
   Leistung unter 100 W fällt.
4. **Fremde Änderung** (Rückfrage): Der Betreiber stellt den Strom von Hand um; die Erkennung des
   core (``ForeignChangeDetector``, 3 Treffer in Folge) prüft alle ``check_s`` gegen den letzten
   bestätigten Strom.
5. **Verbindung unterbrochen** (Rückfrage): 10 A (höchstens ``max_a``), dann zieht der Betreiber
   das Netzwerkkabel. Nach „Wieder angesteckt?“ zählt nur ein Sollstrom, den die EVCS danach
   meldet; die Antwort selbst ändert daran nichts.
6. **Sicherer Strom über den Watchdog** (Rückfrage, 17.2-5): Strom auf den sicheren Strom plus
   4 A (höchstens ``max_a``), dann die Watchdog-Probe; danach muss die EVCS den sicheren Strom
   haben. Die Befunde der Probe stehen vor diesem Schritt.

Nach jedem Schritt (die Ströme zählen als einer) stellt ``Part2Session.restore_after_step`` die
vorgefundenen Werte wieder her; ihre Zeilen stehen hinter dem Schritt (Präzisierung 11). Mit
``--yes`` (``session.unattended``) entfallen die Schritte 4 und 5, denn sie brauchen einen
Handgriff des Betreibers: INFO „übersprungen (--yes)“.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from bindaems.core.control.actuator import matches
from bindaems.core.control.foreign import ForeignChangeDetector
from bindaems.shared.config import Config, EvcsConfig
from bindaems.shared.control import CommandKind, CommandValue, wallbox_actuator
from bindaems.shared.domain import Quality, Snapshot
from bindaems.shared.numbers import de, finite_number
from bindaems.tools.part2.session import SKIPPED_UNATTENDED, Part2Session, seconds
from bindaems.tools.verify_checks import Finding, Status

ITEM: Final = "17.2-6"
WATCHDOG_ITEM: Final = "17.2-5"
TEST_CURRENT_A: Final = 10  # Strom zwischen Mindest- und Höchststrom, auch vor der Unterbrechung
SAFE_OFFSET_A: Final = 4  # so weit über dem sicheren Strom steht die EVCS vor der Probe
CHARGING_W: Final = 500.0  # darüber lädt ein Fahrzeug
STOPPED_W: Final = 100.0  # darunter gilt das Laden als aus
SKIPPED: Final = "übersprungen"

QUESTION_CHARGING: Final = (
    "Darf die EVCS kurz „Laden aus“ und wieder „an“ schalten? "
    "Ein angestecktes Fahrzeug pausiert dann."
)
QUESTION_FOREIGN: Final = (
    "Bitte jetzt den Ladestrom der EVCS von Hand ändern (VictronConnect oder Cerbo). Bereit?"
)
QUESTION_UNPLUG: Final = "Netzwerkkabel der EVCS für 60 s abziehen?"
QUESTION_REPLUGGED: Final = "Wieder angesteckt?"
QUESTION_WATCHDOG: Final = "Watchdog-Probe mit verstelltem Strom starten? Der Watchdog löst aus."
UNPLUG_HINT: Final = "Jetzt das Netzwerkkabel der EVCS abziehen, 60 s warten und wieder anstecken."
REASON_UNKNOWN_CURRENT: Final = "Sollstrom unbekannt."

# Sollstrom, wie ihn die EVCS meldet: erst das eigene Modbus-Abbild, dann der Cerbo
_CURRENT_SIGNALS: Final = ("set_current_a", "gx_set_current_a")


@dataclass(frozen=True)
class EvcsTimings:
    """Fristen der Schritte in Sekunden.

    - ``confirm_s``: Rücklesen eines geschriebenen Werts; auch das Warten auf die Werte der EVCS
      nach dem Start und auf die Leistung nach „Laden aus“
    - ``check_s``: Abstand der Prüfungen auf eine fremde Änderung
    - ``foreign_max_s``: so lange wartet der Schritt auf die Änderung von Hand
    - ``unplug_s``: so lange wartet er nach dem Wiederanstecken auf Messwerte
    - ``safe_s``: so lange wartet er nach der Probe auf den sicheren Strom
    """

    confirm_s: float = 15.0
    check_s: float = 10.0
    foreign_max_s: float = 120.0
    unplug_s: float = 60.0
    safe_s: float = 30.0


DEFAULT_TIMINGS: Final = EvcsTimings()  # die Fristen an der Anlage


def _amps(value: CommandValue) -> str:
    number = finite_number(value)
    return f"{de(number)} A" if number is not None else "unbekannt"


async def run_evcs(
    session: Part2Session,
    cfg: Config,
    key: str,
    *,
    probe: Callable[[], Awaitable[list[Finding]]],
    timings: EvcsTimings = DEFAULT_TIMINGS,
) -> list[Finding]:
    """Führt den Teil EVCS für die Wallbox ``key`` aus; die Befunde in der Reihenfolge des Berichts.

    ``probe`` ist die Watchdog-Probe (im Werkzeug ``watchdog_probe.run_probe`` mit eigener
    Verbindung). Jeder Befund steht auch sofort in ``session.findings``. Die Werte stellt danach
    ``session.restore`` wieder her. ``key`` muss eine EVCS sein, sonst ``ValueError``.
    """
    wallbox = cfg.wallboxes.get(key)
    if not isinstance(wallbox, EvcsConfig):
        raise ValueError(f"Keine EVCS „{key}“ in wallboxes.")
    first = len(session.findings)
    await _EvcsSteps(session, cfg, key, wallbox, probe, timings).run()
    return session.findings[first:]


class _EvcsSteps:
    """Die Schritte eines Laufs, je mit Wiederherstellen danach."""

    def __init__(
        self,
        session: Part2Session,
        cfg: Config,
        key: str,
        wallbox: EvcsConfig,
        probe: Callable[[], Awaitable[list[Finding]]],
        timings: EvcsTimings,
    ) -> None:
        self._session = session
        self._cfg = cfg
        self._key = key
        self._wallbox = wallbox
        self._id = wallbox_actuator(key)
        self._actuator = session.actuators[self._id]
        evcharger = cfg.victron.instances.evcharger
        self._instance = next((gx for gx, name in evcharger.items() if name == key), None)
        self._probe = probe
        self._t = timings

    async def run(self) -> None:
        if not await self._ready():
            return
        steps = (self._currents, self._charging, self._foreign, self._interruption, self._watchdog)
        for step in steps:
            await step()
            await self._session.restore_after_step(self._t.confirm_s)

    async def _ready(self) -> bool:
        """EVCS bereit: steuerbar und mit bekanntem Sollstrom, sonst FEHLER und Ende."""
        await self._session.wait_for(self._is_ready, self._t.confirm_s)
        snap = self._session.snapshot()
        capabilities = self._actuator.capabilities(snap, snap.ts)
        present = self._actuator.present(snap)
        current = finite_number(present.get(CommandKind.CURRENT))
        if not capabilities.controllable:
            self._record("EVCS bereit", "fail", " ".join(capabilities.reasons))
            return False
        if current is None:
            self._record("EVCS bereit", "fail", REASON_UNKNOWN_CURRENT)
            return False
        problem = self._session.found_problem(self._id, CommandKind.CURRENT, current)
        if problem is not None:
            self._record("EVCS bereit", "fail", problem)
            return False
        charging = present.get(CommandKind.CHARGING)
        state = "an" if charging is True else "aus" if charging is False else "unbekannt"
        detail = (
            f"Instanz {self._instance}, manueller Modus, Sollstrom {de(current)} A, Laden {state}"
        )
        self._record("EVCS bereit", "ok", detail)
        return True

    def _is_ready(self, snap: Snapshot) -> bool:
        controllable = self._actuator.capabilities(snap, snap.ts).controllable
        return controllable and CommandKind.CURRENT in self._actuator.present(snap)

    async def _currents(self) -> None:
        """Mindeststrom, 10 A (falls dazwischen) und Höchststrom, je mit Rücklesen."""
        low, high = self._wallbox.min_a, self._wallbox.max_a
        targets = [low]
        if low < TEST_CURRENT_A < high:
            targets.append(TEST_CURRENT_A)
        if high != low:
            targets.append(high)
        for amps in targets:
            title = f"Strom {de(amps)} A"
            waited = await self._set(CommandKind.CURRENT, float(amps))
            if waited is None:
                self._record(title, "fail", self._failure())
            else:
                self._record(title, "ok", f"bestätigt nach {seconds(waited)} s")

    async def _charging(self) -> None:
        """Laden aus und wieder an; bei ladendem Fahrzeug auch, wann die Leistung fällt."""
        title = "Laden aus und an"
        if not self._session.ask(QUESTION_CHARGING):
            self._record(title, "info", SKIPPED)
            return
        power = finite_number(self._session.snapshot().ok(self._signal("power_w")))
        loaded = power is not None and power > CHARGING_W
        off = await self._set(CommandKind.CHARGING, False)
        if off is None:
            self._record(title, "fail", f"Laden aus: {self._failure()}")
            return
        dropped: float | None = None
        if loaded:
            waited = await self._session.wait_for(self._stopped, self._t.confirm_s)
            dropped = None if waited is None else off + waited
        on = await self._set(CommandKind.CHARGING, True)
        detail = f"aus nach {seconds(off)} s"
        if on is None:
            self._record(title, "fail", f"{detail}, Laden an: {self._failure()}")
            return
        detail += f", an nach {seconds(on)} s"
        if not loaded:
            self._record(title, "ok", detail)
        elif dropped is not None:
            power_text = f"Leistung unter {de(STOPPED_W)} W nach {seconds(dropped)} s"
            self._record(title, "ok", f"{detail}, {power_text}")
        else:
            power_text = f"Leistung nach {de(self._t.confirm_s)} s nicht unter {de(STOPPED_W)} W"
            self._record(title, "warn", f"{detail}, {power_text}")

    def _stopped(self, snap: Snapshot) -> bool:
        power = finite_number(snap.ok(self._signal("power_w")))
        return power is not None and power < STOPPED_W

    async def _foreign(self) -> None:
        """Eine Änderung von Hand erkennen wie der core: 3 Treffer in Folge, alle ``check_s``."""
        title = "Fremde Änderung"
        if self._session.unattended:  # niemand stellt den Strom von Hand um
            self._record(title, "info", SKIPPED_UNATTENDED)
            return
        if not self._session.ask(QUESTION_FOREIGN):
            self._record(title, "info", SKIPPED)
            return
        expected = finite_number(self._session.confirmed.get((self._id, CommandKind.CURRENT)))
        if expected is None:  # kein Strom bestätigt: dann der vorgefundene
            snap = self._session.snapshot()
            expected = finite_number(self._actuator.present(snap).get(CommandKind.CURRENT))
        if expected is None:
            self._record(title, "fail", REASON_UNKNOWN_CURRENT)
            return
        detector = ForeignChangeDetector(self._cfg.safety.lock_release_s)
        detector.expect(self._id, CommandKind.CURRENT, expected)
        deadline = self._session.monotonic() + self._t.foreign_max_s
        checks = 0
        while self._session.monotonic() < deadline:
            await asyncio.sleep(self._t.check_s)
            checks += 1
            snap = self._session.snapshot()
            present = {
                (self._id, kind): value for kind, value in self._actuator.present(snap).items()
            }
            changes = detector.check(present, frozenset(), snap.ts)
            if changes:
                change = changes[0]
                found = f"{_amps(change.actual)} statt {_amps(change.expected)}"
                self._record(title, "ok", f"erkannt nach {checks} Prüfungen ({found})")
                return
        self._record(title, "warn", "keine Änderung erkannt")

    async def _interruption(self) -> None:
        """Behält die EVCS ihren Strom, wenn ihre Verbindung unterbrochen ist?"""
        title = "Verbindung unterbrochen"
        if self._session.unattended:  # niemand zieht das Kabel
            self._record(title, "info", SKIPPED_UNATTENDED)
            return
        if not self._session.ask(QUESTION_UNPLUG):
            self._record(title, "info", SKIPPED)
            return
        target = min(max(TEST_CURRENT_A, self._wallbox.min_a), self._wallbox.max_a)
        if await self._set(CommandKind.CURRENT, float(target)) is None:
            self._record(title, "fail", f"Strom {de(target)} A: {self._failure()}")
            return
        print(UNPLUG_HINT)
        self._session.ask(QUESTION_REPLUGGED)  # wartet nur auf den Betreiber
        since = self._session.snapshot().ts

        def fresh(snap: Snapshot) -> bool:
            return self._fresh_current(snap, since) is not None

        if await self._session.wait_for(fresh, self._t.unplug_s) is None:
            self._record(title, "warn", "keine Messwerte nach der Unterbrechung")
            return
        after = self._fresh_current(self._session.snapshot(), since)
        detail = f"nach der Unterbrechung {_amps(after)} (vorher {de(target)} A)"
        self._record(title, "info", detail)

    def _fresh_current(self, snap: Snapshot, since: datetime) -> float | None:
        """Der Sollstrom aus einer Meldung nach ``since``; ältere Werte zählen nicht."""
        for name in _CURRENT_SIGNALS:
            reading = snap.get(self._signal(name))
            if reading is None or reading.quality is not Quality.OK or reading.ts <= since:
                continue
            current = finite_number(reading.value)
            if current is not None:
                return current
        return None

    async def _watchdog(self) -> None:
        """Setzt der Watchdog bei der Probe den sicheren Strom, und übernimmt ihn die EVCS?"""
        title = "Sicherer Strom über den Watchdog"
        if not self._session.ask(QUESTION_WATCHDOG):
            self._record(title, "info", SKIPPED, item=WATCHDOG_ITEM)
            return
        safe = self._wallbox.safe_a
        shifted = min(safe + SAFE_OFFSET_A, self._wallbox.max_a)
        if await self._set(CommandKind.CURRENT, float(shifted)) is None:
            detail = f"vor der Probe Strom {de(shifted)} A: {self._failure()}"
            self._record(title, "fail", detail, item=WATCHDOG_ITEM)
            return
        for finding in await self._probe():
            self._session.record(finding)

        def at_safe(snap: Snapshot) -> bool:
            current = self._actuator.present(snap).get(CommandKind.CURRENT)
            return matches(CommandKind.CURRENT, float(safe), current)

        if await self._session.wait_for(at_safe, self._t.safe_s) is not None:
            detail = f"Watchdog setzte {de(safe)} A (vorher {de(shifted)} A)"
            self._record(title, "ok", detail, item=WATCHDOG_ITEM)
            return
        current = self._actuator.present(self._session.snapshot()).get(CommandKind.CURRENT)
        detail = f"Strom nach der Auslösung {_amps(current)} statt {de(safe)} A"
        self._record(title, "fail", detail, item=WATCHDOG_ITEM)

    async def _set(self, kind: CommandKind, value: CommandValue) -> float | None:
        """``set_and_confirm`` mit ``confirm_s``."""
        return await self._session.set_and_confirm(self._id, kind, value, self._t.confirm_s)

    def _failure(self) -> str:
        return self._session.failure or f"nicht bestätigt nach {de(self._t.confirm_s)} s"

    def _signal(self, name: str) -> str:
        return f"wallbox.{self._key}.{name}"

    def _record(self, title: str, status: Status, detail: str, *, item: str = ITEM) -> None:
        self._session.record(Finding(item, title, status, detail))
