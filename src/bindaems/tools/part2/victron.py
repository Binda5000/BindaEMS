"""Teil Victron des Prüfprotokolls Teil 2 (Spec 17.2-1 bis 17.2-4 und 17.2-8).

Die Schritte schreiben über den ``VictronActuator`` des core (``Part2Session``): den flüchtigen
Netz-Sollwert-Override ``hub4/0/Overrides/Setpoint`` (``None`` hebt ihn auf; ob das stimmt,
bestätigt dieser Teil, Plan 2b, Präzisierung 1) und die persistente Entladegrenze. Netzleistung ist
in jedem Schritt der Mittelwert über ``observe_s``. Die Wirkung auf den Akku beobachtet er
``effect_s`` lang und beurteilt sie über Fenster von ``observe_s``: bei Entladesperre und „kein
Akku ins Auto“ über das letzte (das Ausklingen gleich nach dem Schreiben zählt nicht), beim
Netzladen über das mit der höchsten Leistung („binnen“ der Beobachtung).

1. **Victron bereit** (17.2-1): steuerbar (Cerbo verbunden), Dynamic ESS aus (``dess.mode`` 0;
   DESS nullt fremde Overrides, Spec 3.2) und kein Netz-Sollwert-Override aktiv: Steht einer, regelt
   ein anderes System, und der Teil schriebe darüber. Die vorgefundene Entladegrenze muss die
   Regeln 1–3 passieren (sonst ließe sie sich nicht wiederherstellen). Vorher wartet der Schritt
   bis ``confirm_s`` auf Override und Entladegrenze. Sonst endet der Teil ohne Schreiben.
2. **Netz-Sollwert-Override:** setzen, Wirkung am Netz, mit ``None`` aufheben, Wirkung danach.
   Bestätigt der Cerbo das Aufheben nicht, schreibt der Teil keinen weiteren Override mehr.
3. **Override ohne Timeout** (Rückfrage): Override setzen und ``hold_s`` halten.
4. **Override nach Neustart des Cerbo** (Rückfrage): Override setzen, der Betreiber startet den
   Cerbo neu, der Teil wartet auf die Neuverbindung. Der Neustart ist ein Handgriff: Mit ``--yes``
   (``session.unattended``) entfällt der Schritt.
5. **Zusammenspiel mit dem persistenten Sollwert:** ``ess.grid_setpoint_w`` (AcPowerSetPoint) vor
   und nach den Schritten 2 bis 4; der Teil schreibt ihn nie.
6. **Entladesperre** (17.2-2): ``MAX_DISCHARGE`` 0, Wirkung am Akku; das Wiederherstellen setzt den
   Originalwert zurück. Die Entladegrenze ist eine persistente Einstellung: Die Sitzung schreibt
   sie je Lauf höchstens viermal (``Part2Session``).
7. **Netzladen** (17.2-3): Override ``min(2000, Obergrenze)``, Ladeleistung des Akkus.
8. **Kein Akku ins Auto** (17.2-4): nur bei ladender EVCS (über 1000 W, alle EVCS zusammen);
   Override = EVCS-Leistung in ganzen Watt, höchstens die Obergrenze, Entladeleistung des Akkus.
9. **Importlimit** (17.2-8): Der Betreiber schaltet eine Last ein; der Teil beobachtet ``effect_s``
   lang den höchsten Netzstrom. Das ist ein Handgriff: Mit ``--yes`` entfällt der Schritt.

Obergrenze ist der Höchstwert des Netz-Sollwerts nach Regel 2 (``HardLimits.grid_setpoint_max``).

Nach jedem Block (jeder schreibende Schritt ist einer) stellt ``Part2Session.restore_after_step``
die vorgefundenen Werte wieder her; ihre Zeilen stehen hinter dem Schritt (Präzisierung 11). Der
vorgefundene Override ist stets „keiner“, denn sonst endet „bereit“: Am Ende ist er aufgehoben.
Das letzte ``restore`` am Ende macht der Aufrufer.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from bindaems.core.control.actuator import matches
from bindaems.shared.config import Config, EvcsConfig
from bindaems.shared.control import VICTRON, CommandKind, CommandValue
from bindaems.shared.domain import Snapshot
from bindaems.shared.numbers import de, finite_number
from bindaems.tools.part2.session import SKIPPED_UNATTENDED, Part2Session, minutes, seconds
from bindaems.tools.verify_checks import Finding, Status

ITEM: Final = "17.2-1"
DISCHARGE_ITEM: Final = "17.2-2"
CHARGING_ITEM: Final = "17.2-3"
CAR_ITEM: Final = "17.2-4"
IMPORT_ITEM: Final = "17.2-8"

OVERRIDE_W: Final = 1000.0  # Override der Schritte 2 bis 4 (höchstens die Obergrenze)
GRID_CHARGE_W: Final = 2000.0  # Override beim Netzladen (höchstens die Obergrenze)
LOCK_MAX_W: Final = 100  # Entladesperre: bis so viel Entladung gilt sie als wirksam
CHARGING_MIN_W: Final = 500  # Netzladen: über so viel Ladeleistung gilt es als wirksam
CAR_MIN_W: Final = 1000.0  # über so viel Leistung lädt ein Auto an der EVCS
CAR_MAX_W: Final = 200  # kein Akku ins Auto: bis so viel Entladung ist noch hinnehmbar
DISCHARGE_LIMIT_W: Final = 0.0  # die Entladesperre
OVERRIDE_PREFIX: Final = "ess.override."
OVERRIDE_DISCHARGE_SIGNAL: Final = "ess.override.maxdischargepower"

SKIPPED: Final = "übersprungen"
SKIPPED_NO_CAR: Final = "übersprungen: kein Laden an der EVCS"
SKIPPED_STUCK: Final = "übersprungen: Override ließ sich nicht aufheben"
SKIPPED_NO_OVERRIDE: Final = "übersprungen: kein Override gesetzt"
RELEASE_FAILED: Final = "Aufheben mit null nicht bestätigt; Weg für die Rücknahme klären."
NO_BATTERY: Final = "Akkuleistung nicht gemeldet"

QUESTION_RESTART: Final = "Cerbo jetzt neu starten?"
# Die Rückfrage hält die Adapter an. Antwortet der Betreiber zuerst und startet dann neu, sieht der
# Teil die Trennung und die Neuverbindung; wer lange vor der Antwort neu startet oder wartet, lässt
# die Verbindung auch ohne Neustart abreißen.
RESTART_HINT: Final = "Jetzt den Cerbo neu starten; das Werkzeug wartet auf die Neuverbindung."
QUESTION_IMPORT: Final = "Last so einschalten, dass eine Phase über 20 A käme?"
QUESTION_DISCHARGE: Final = (
    "Entladegrenze kurz auf 0 W setzen? Der Akku entlädt dann nicht; die Einstellung wird "
    "gespeichert."
)


@dataclass(frozen=True)
class VictronTimings:
    """Fristen der Schritte in Sekunden.

    - ``confirm_s``: Rücklesen eines geschriebenen Werts; auch das Warten auf die Werte des Cerbo
      nach dem Start und beim Wiederherstellen
    - ``observe_s``: Dauer einer Messung der Netzleistung (Mittelwert) und Länge des Fensters,
      in dem der Teil die Leistung des Akkus beurteilt
    - ``effect_s``: so lange beobachtet der Teil nach dem Schreiben den Akku; so lange beobachtet
      das Importlimit den Netzstrom
    - ``hold_s``: so lange bleibt der Override ohne Auffrischen stehen
    - ``reboot_s``: so lange wartet der Teil auf die Trennung und die Neuverbindung des Cerbo
    """

    confirm_s: float = 15.0
    observe_s: float = 30.0
    effect_s: float = 60.0
    hold_s: float = 600.0
    reboot_s: float = 600.0


DEFAULT_TIMINGS: Final = VictronTimings()  # die Fristen an der Anlage


def _grid_w(snap: Snapshot) -> float | None:
    """Netzleistung: die Summe der drei Phasen, sonst der Gesamtwert des Zählers."""
    phases = [finite_number(snap.ok(f"grid.l{n}.power_w")) for n in (1, 2, 3)]
    known = [power for power in phases if power is not None]
    if len(known) == len(phases):
        return sum(known)
    return finite_number(snap.ok("grid.power_w"))


def _battery_w(snap: Snapshot) -> float | None:
    """Akkuleistung wie der Cerbo sie meldet: positiv beim Laden, negativ beim Entladen."""
    return finite_number(snap.ok("battery.power_w"))


def _discharge_w(snap: Snapshot) -> float | None:
    power = _battery_w(snap)
    return None if power is None else max(-power, 0.0)


def _mean(values: Iterable[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return statistics.fmean(known) if known else None


def _peak(values: Iterable[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return max(known) if known else None


def _tail(snapshots: Sequence[Snapshot], window_s: float) -> list[Snapshot]:
    """Die Zustände der letzten ``window_s`` Sekunden (``snapshots`` ist nicht leer)."""
    start = snapshots[-1].ts - timedelta(seconds=window_s)
    return [snap for snap in snapshots if snap.ts >= start]


def _best_mean(
    snapshots: Sequence[Snapshot], read: Callable[[Snapshot], float | None], window_s: float
) -> float | None:
    """Der höchste Mittelwert von ``read`` über ein Fenster von ``window_s`` Sekunden.

    Es zählen die Fenster, die ganz in der Beobachtung liegen. Reicht sie für kein ganzes Fenster,
    gilt der Mittelwert der ganzen Beobachtung. ``snapshots`` ist nicht leer.
    """
    span = timedelta(seconds=window_s)
    best: float | None = None
    for end in snapshots:
        if end.ts - span < snapshots[0].ts:
            continue
        mean = _mean(read(snap) for snap in snapshots if end.ts - span <= snap.ts <= end.ts)
        if mean is not None and (best is None or mean > best):
            best = mean
    return best if best is not None else _mean(read(snap) for snap in snapshots)


def _watts(value: float | None) -> str:
    """Leistung in ganzen Watt für Texte; ohne Wert „unbekannt“."""
    return "unbekannt" if value is None else f"{de(round(value))} W"


def _setpoint(value: float | None) -> str:
    """Ein Sollwert für Texte, so wie er gemeldet wird (nicht gerundet); ohne Wert „unbekannt“."""
    return "unbekannt" if value is None else f"{de(value)} W"


def _value(value: object) -> str:
    """Ein gemeldeter Wert für Texte: Zahlen mit Komma, ``None`` als „null“."""
    number = finite_number(value)
    if number is not None:
        return de(number)
    return "null" if value is None else str(value)


def _mode_text(mode: object) -> str:
    return "unbekannt" if mode is None else _value(mode)


def _overrides(snap: Snapshot) -> str:
    """Die hub4-Overrides, die der Cerbo meldet, mit ihren Werten; sonst „keine“."""
    found = [
        f"{name.removeprefix(OVERRIDE_PREFIX)} = {_value(reading.value)}"
        for name, reading in sorted(snap.matching(OVERRIDE_PREFIX).items())
    ]
    return ", ".join(found) or "keine"


async def run_victron(
    session: Part2Session, cfg: Config, *, timings: VictronTimings = DEFAULT_TIMINGS
) -> list[Finding]:
    """Führt den Teil Victron aus; die Befunde stehen in Berichtsreihenfolge.

    Jeder Befund steht auch sofort in ``session.findings``. Die Werte stellt der Teil nach jedem
    Block selbst wieder her; ``session.restore`` räumt danach auf. Die Sitzung braucht den Aktor
    ``victron``, sonst ``ValueError``.
    """
    if VICTRON not in session.actuators:
        raise ValueError(f"Kein Aktor „{VICTRON}“ in dieser Sitzung.")
    first = len(session.findings)
    await _VictronSteps(session, cfg, timings).run()
    return session.findings[first:]


class _VictronSteps:
    """Die Schritte eines Laufs, je Block mit Wiederherstellen danach."""

    def __init__(self, session: Part2Session, cfg: Config, timings: VictronTimings) -> None:
        self._session = session
        self._actuator = session.actuators[VICTRON]
        self._t = timings
        # höchster Netz-Sollwert nach Regel 2, ohne Leistungsbudget (das gibt es im Werkzeug nicht)
        self._ceiling = session.limits.grid_setpoint_max(None)[0]
        wallboxes = cfg.wallboxes.items()
        self._evcs = [key for key, wallbox in wallboxes if isinstance(wallbox, EvcsConfig)]
        self._ac_before: float | None = None  # AcPowerSetPoint vor den Override-Schritten
        self._overridden = False  # hat der Cerbo in den Schritten 2 bis 4 einen Override bestätigt?
        self._stuck = False  # steht noch ein Override, den das Wiederherstellen nicht aufhob?
        self._wrote = False  # hat der Block begonnen, etwas zu schreiben?

    async def run(self) -> None:
        if not await self._ready():
            return
        for step in (self._override, self._hold, self._reboot):
            await self._block(step)
        self._together()
        for step in (self._discharge, self._grid_charging, self._car, self._import_limit):
            await self._block(step)

    async def _block(self, step: Callable[[], Awaitable[None]]) -> None:
        """Ein Schritt; hat er geschrieben, folgen die vorgefundenen Werte, dann zählt der Override.

        Ein Schritt, der nichts schreibt (abgelehnt, übersprungen, Importlimit), braucht kein
        Wiederherstellen. Es wiederholte sonst nach einem Override, der sich nicht aufheben ließ,
        bei jedem Schritt dieselbe FEHLER-Zeile samt Wartezeit.
        """
        self._wrote = False
        await step()
        if self._wrote:
            await self._session.restore_after_step(self._t.confirm_s)
        self._stuck = self._override_stuck()

    # Victron bereit

    async def _ready(self) -> bool:
        """Victron bereit: steuerbar, Dynamic ESS aus, kein Override, Entladegrenze innerhalb."""
        title = "Victron bereit"
        await self._session.wait_for(self._is_ready, self._t.confirm_s)
        snap = self._session.snapshot()
        capabilities = self._actuator.capabilities(snap, snap.ts)
        if not capabilities.controllable:
            self._record(title, "fail", " ".join(capabilities.reasons))
            return False
        mode = snap.ok("dess.mode")
        # DESS nullt fremde Overrides (Spec 3.2); ein unbekannter Modus gilt als an
        if finite_number(mode) != 0:
            self._record(
                title,
                "fail",
                f"Dynamic ESS ist aktiv (Modus {_mode_text(mode)}); die Victron-Punkte erst nach "
                "dem Abschalten von DESS.",
            )
            return False
        present = self._actuator.present(snap)
        override = finite_number(present.get(CommandKind.GRID_SETPOINT))
        if override is not None:  # Ein anderes System regelt über den Override.
            self._record(
                title,
                "fail",
                f"Netz-Sollwert-Override aktiv ({de(override)} W); ein anderes System regelt – "
                "vor dem Test aufheben.",
            )
            return False
        if CommandKind.MAX_DISCHARGE in present:
            problem = self._session.found_problem(
                VICTRON, CommandKind.MAX_DISCHARGE, present[CommandKind.MAX_DISCHARGE]
            )
            if problem is not None:
                self._record(title, "fail", problem)
                return False
        self._ac_before = self._ac_setpoint(snap)
        self._record(title, "ok", f"hub4-Overrides vorher: {_overrides(snap)}")
        return True

    def _is_ready(self, snap: Snapshot) -> bool:
        """Steuerbar, mit bekanntem Modus von Dynamic ESS und mit Override und Entladegrenze?

        Der Cerbo meldet seine Werte nach dem Start nach und nach. „bereit“ entscheidet erst, wenn
        alles da ist, spätestens nach ``confirm_s``; was dann fehlt, melden die Schritte als
        unbekannt.
        """
        if not self._controllable(snap) or finite_number(snap.ok("dess.mode")) is None:
            return False
        present = self._actuator.present(snap)
        return all(
            kind in present for kind in (CommandKind.GRID_SETPOINT, CommandKind.MAX_DISCHARGE)
        )

    # Netz-Sollwert-Override

    async def _override(self) -> None:
        """Override setzen, Wirkung am Netz beobachten, mit ``None`` aufheben, wieder beobachten."""
        title = "Netz-Sollwert-Override"
        value = self._value_w(OVERRIDE_W)
        question = (
            f"Netz-Sollwert-Override kurz auf {de(value)} W setzen und mit null wieder aufheben? "
            "Der Akku lädt dabei aus dem Netz."
        )
        if not self._session.ask(question):
            self._record(title, "info", SKIPPED)
            return
        before = await self._grid_mean()
        set_s = await self._set(CommandKind.GRID_SETPOINT, value)
        if set_s is None:
            self._record(title, "fail", f"Override {de(value)} W: {self._failure()}")
            return
        self._overridden = True
        during = await self._grid_mean()
        release_s = await self._set(CommandKind.GRID_SETPOINT, None)
        if release_s is None:
            self._record(title, "fail", RELEASE_FAILED)
            return
        after = await self._grid_mean()
        self._record(
            title,
            "ok",
            f"{de(value)} W nach {seconds(set_s)} s bestätigt; Netz vorher {_watts(before)}, "
            f"mit Override {_watts(during)}; aufgehoben nach {seconds(release_s)} s, "
            f"danach {_watts(after)}",
        )

    async def _grid_mean(self) -> float | None:
        """Die Netzleistung als Mittelwert über ``observe_s``."""
        snapshots = await self._session.observe(self._t.observe_s)
        return _mean(_grid_w(snap) for snap in snapshots)

    # Override ohne Timeout

    async def _hold(self) -> None:
        """Bleibt der Override ``hold_s`` lang stehen, ohne dass das Werkzeug ihn auffrischt?"""
        title = "Override ohne Timeout"
        if self._blocked(title):
            return
        value = self._value_w(OVERRIDE_W)
        question = (
            f"Override auf {de(value)} W setzen und {minutes(self._t.hold_s)} min halten, um "
            "einen Timeout zu erkennen?"
        )
        if not self._session.ask(question):
            self._record(title, "info", SKIPPED)
            return
        if await self._set(CommandKind.GRID_SETPOINT, value) is None:
            self._record(title, "fail", f"Override {de(value)} W: {self._failure()}")
            return
        self._overridden = True
        changed: list[CommandValue] = []  # was der Cerbo stattdessen meldet

        def lost(snap: Snapshot) -> bool:
            known, current = self._override_state(snap)
            if known and not matches(CommandKind.GRID_SETPOINT, value, current):
                changed.append(current)
                return True
            return False

        waited = await self._session.wait_for(lost, self._t.hold_s)
        if waited is not None and changed[0] is None:
            self._record(title, "info", f"nach {minutes(waited)} min verschwunden")
        elif waited is not None:
            text = f"nach {minutes(waited)} min geändert auf {_value(changed[0])} W"
            self._record(title, "warn", text)
        elif self._override_state(self._session.snapshot())[0]:
            self._record(title, "ok", f"nach {minutes(self._t.hold_s)} min noch aktiv")
        else:  # Der Cerbo meldet ihn nicht (getrennt?): „noch aktiv“ ließe sich nicht belegen.
            text = f"nach {minutes(self._t.hold_s)} min nicht prüfbar: Override nicht gemeldet"
            self._record(title, "warn", text)

    # Override nach Neustart des Cerbo

    async def _reboot(self) -> None:
        """Überlebt der Override einen Neustart des Cerbo? Der Betreiber startet ihn neu."""
        title = "Override nach Neustart des Cerbo"
        if self._session.unattended:  # niemand startet den Cerbo neu
            self._record(title, "info", SKIPPED_UNATTENDED)
            return
        if self._blocked(title):
            return
        value = self._value_w(OVERRIDE_W)
        question = (
            f"Override auf {de(value)} W setzen und den Cerbo dann neu starten, um die Wirkung "
            "des Neustarts zu prüfen?"
        )
        if not self._session.ask(question):
            self._record(title, "info", SKIPPED)
            return
        if await self._set(CommandKind.GRID_SETPOINT, value) is None:
            self._record(title, "fail", f"Override {de(value)} W: {self._failure()}")
            return
        self._overridden = True
        if not self._session.ask(QUESTION_RESTART):
            self._record(title, "info", SKIPPED)
            return
        print(RESTART_HINT)
        status, detail = await self._after_restart()
        self._record(title, status, detail)

    async def _after_restart(self) -> tuple[Status, str]:
        """Wartet auf die Trennung und die Neuverbindung des Cerbo; dann gilt, was er meldet.

        Nach dem Verbinden fehlt der Override, bis der Cerbo ihn erneut meldet: Ein fehlender Wert
        ist nicht „kein Override“ (das wäre ``None`` bei verbundenem Cerbo).
        """
        reboot_s = self._t.reboot_s
        down = await self._session.wait_for(lambda snap: not self._controllable(snap), reboot_s)
        if down is None:
            return "warn", f"Cerbo nach {de(reboot_s)} s nicht getrennt; kein Neustart erkannt"
        back = await self._session.wait_for(self._reported, reboot_s - down)
        if back is None:
            return "warn", f"Cerbo nach {de(reboot_s)} s nicht wieder verbunden"
        _, current = self._override_state(self._session.snapshot())
        if current is None:
            return "ok", "nach dem Neustart aufgehoben"
        return "warn", f"nach dem Neustart noch aktiv ({_value(current)} W)"

    def _reported(self, snap: Snapshot) -> bool:
        """Ist der Cerbo verbunden und meldet er seinen Override wieder?"""
        return self._controllable(snap) and self._override_state(snap)[0]

    # Zusammenspiel mit dem persistenten Sollwert

    def _together(self) -> None:
        """Hat sich der persistente Sollwert (AcPowerSetPoint) geändert?"""
        title = "Zusammenspiel mit dem persistenten Sollwert"
        if not self._overridden:  # ohne Override gäbe es nichts zu vergleichen
            self._record(title, "info", SKIPPED_NO_OVERRIDE)
            return
        before, after = self._ac_before, self._ac_setpoint(self._session.snapshot())
        if before is None or after is None:
            text = f"AcPowerSetPoint vorher {_setpoint(before)}, nachher {_setpoint(after)}"
        elif before == after:
            text = f"AcPowerSetPoint {de(before)} W blieb unverändert"
        else:
            text = f"AcPowerSetPoint änderte sich von {de(before)} W auf {de(after)} W"
        self._record(title, "info", text)

    # Entladesperre

    async def _discharge(self) -> None:
        """Die Entladegrenze auf 0 W: Entlädt der Akku danach nicht mehr?"""
        title = "Entladesperre"
        exists = self._session.snapshot().get(OVERRIDE_DISCHARGE_SIGNAL) is not None
        existence = f"{OVERRIDE_DISCHARGE_SIGNAL} existiert" + ("" if exists else " nicht")
        self._record("Override für die Entladegrenze", "info", existence, item=DISCHARGE_ITEM)
        if not self._session.ask(QUESTION_DISCHARGE):
            self._record(title, "info", SKIPPED, item=DISCHARGE_ITEM)
            return
        if await self._set(CommandKind.MAX_DISCHARGE, DISCHARGE_LIMIT_W) is None:
            detail = f"Entladegrenze {de(DISCHARGE_LIMIT_W)} W: {self._failure()}"
            self._record(title, "fail", detail, item=DISCHARGE_ITEM)
            return
        snapshots = await self._session.observe(self._t.effect_s)
        status, detail = self._max_discharge(snapshots, LOCK_MAX_W)
        self._record(title, status, detail, item=DISCHARGE_ITEM)

    def _max_discharge(self, snapshots: Sequence[Snapshot], limit_w: int) -> tuple[Status, str]:
        """Entlädt der Akku am Ende der Beobachtung höchstens mit ``limit_w``?

        Maßgeblich ist das Fenster der letzten ``observe_s``: Gleich nach dem Schreiben darf der
        Akku noch ausklingen.
        """
        peak = _peak(_discharge_w(snap) for snap in _tail(snapshots, self._t.observe_s))
        if peak is None:
            return "warn", NO_BATTERY
        shown = round(peak)  # entschieden wird nach dem gezeigten Wert
        if shown <= limit_w:
            return "ok", f"Akku entlädt höchstens {de(shown)} W"
        return "warn", f"Akku entlädt bis zu {de(shown)} W; erwartet höchstens {de(limit_w)} W"

    # Netzladen

    async def _grid_charging(self) -> None:
        """Lädt der Akku mit einem Override aus dem Netz? Der Override wird wieder aufgehoben."""
        title = "Netzladen"
        if self._blocked(title, CHARGING_ITEM):
            return
        value = self._value_w(GRID_CHARGE_W)
        question = (
            f"Netzladen testen: Override kurz auf {de(value)} W setzen? "
            "Der Akku lädt dann aus dem Netz."
        )
        if not self._session.ask(question):
            self._record(title, "info", SKIPPED, item=CHARGING_ITEM)
            return
        await self._forced(title, CHARGING_ITEM, value, self._charge_power)

    def _charge_power(self, snapshots: Sequence[Snapshot]) -> tuple[Status, str]:
        """Lädt der Akku binnen der Beobachtung mit mehr als 500 W?

        Maßgeblich ist das Fenster von ``observe_s`` mit dem höchsten Mittelwert: Eine einzelne
        Spitze genügt nicht, und ein Akku, der nach kurzem Laden voll ist, besteht trotzdem.
        """
        mean = _best_mean(snapshots, _battery_w, self._t.observe_s)
        if mean is None:
            return "warn", NO_BATTERY
        shown = round(mean)
        if shown > CHARGING_MIN_W:
            return "ok", f"Akku lädt mit {de(shown)} W"
        text = f"Akkuleistung {de(shown)} W (positiv = Laden), erwartet über {de(CHARGING_MIN_W)} W"
        return "warn", text

    # Kein Akku ins Auto

    async def _car(self) -> None:
        """Entlädt der Akku nicht ins Auto, wenn der Override die Ladeleistung der EVCS deckt?"""
        title = "Kein Akku ins Auto"
        power = self._evcs_w()
        if power is None or power <= CAR_MIN_W:
            self._record(title, "info", SKIPPED_NO_CAR, item=CAR_ITEM)
            return
        if self._blocked(title, CAR_ITEM):
            return
        question = (
            f"Die EVCS lädt mit {de(round(power))} W. Override auf {de(self._car_w(power))} W "
            "setzen? Der Akku soll nicht ins Auto entladen."
        )
        if not self._session.ask(question):
            self._record(title, "info", SKIPPED, item=CAR_ITEM)
            return
        power = self._evcs_w()  # die Rückfrage kann lange gedauert haben
        if power is None or power <= CAR_MIN_W:
            self._record(title, "info", SKIPPED_NO_CAR, item=CAR_ITEM)
            return
        value = self._car_w(power)
        note = ""
        if value < round(power):  # die Obergrenze greift: Der Befund nennt sie
            note = f" (Override auf {de(value)} W begrenzt; EVCS-Leistung {de(round(power))} W)"
        await self._forced(
            title, CAR_ITEM, value, lambda snaps: self._max_discharge(snaps, CAR_MAX_W), note
        )

    def _car_w(self, power_w: float) -> float:
        """Der Override für ``power_w`` Ladeleistung: in ganzen Watt, nie über der Obergrenze."""
        return float(max(0, min(round(power_w), math.floor(self._ceiling))))

    def _evcs_w(self) -> float | None:
        """Die Leistung aller EVCS zusammen; ``None``, wenn keine einen Messwert meldet."""
        snap = self._session.snapshot()
        powers = [finite_number(snap.ok(f"wallbox.{key}.power_w")) for key in self._evcs]
        known = [power for power in powers if power is not None]
        return sum(known) if known else None

    async def _forced(
        self,
        title: str,
        item: str,
        value: float,
        verdict: Callable[[Sequence[Snapshot]], tuple[Status, str]],
        note: str = "",
    ) -> None:
        """Override ``value`` setzen, ``effect_s`` beobachten, bewerten und aufheben.

        ``note`` hängt der Befund an das Urteil. Bestätigt der Cerbo das Aufheben nicht, bleibt das
        Urteil im Text und der Befund ist ein FEHLER.
        """
        if await self._set(CommandKind.GRID_SETPOINT, value) is None:
            self._record(title, "fail", f"Override {de(value)} W: {self._failure()}", item=item)
            return
        self._overridden = True
        status, detail = verdict(await self._session.observe(self._t.effect_s))
        detail += note
        if await self._set(CommandKind.GRID_SETPOINT, None) is None:
            status, detail = "fail", f"{detail}; {RELEASE_FAILED}"
        self._record(title, status, detail, item=item)

    # Importlimit

    async def _import_limit(self) -> None:
        """Wie hoch wird der Netzstrom, wenn der Betreiber Last einschaltet? Schreibt nichts."""
        title = "Importlimit"
        if self._session.unattended:  # niemand schaltet eine Last ein
            self._record(title, "info", SKIPPED_UNATTENDED, item=IMPORT_ITEM)
            return
        if not self._session.ask(QUESTION_IMPORT):
            self._record(title, "info", SKIPPED, item=IMPORT_ITEM)
            return
        best: tuple[float, int, float | None] | None = None  # Strom, Phase, Akkuleistung dabei
        for snap in await self._session.observe(self._t.effect_s):
            for phase in (1, 2, 3):
                current = finite_number(snap.ok(f"grid.l{phase}.current_a"))
                if current is not None and (best is None or current > best[0]):
                    best = (current, phase, _battery_w(snap))
        if best is None:
            self._record(title, "warn", "keine Netzströme gemeldet", item=IMPORT_ITEM)
            return
        current, phase, battery = best
        detail = f"höchster Netzstrom {de(round(current, 1))} A (L{phase}), Akku {_watts(battery)}"
        self._record(title, "info", detail, item=IMPORT_ITEM)

    # gemeinsam

    def _controllable(self, snap: Snapshot) -> bool:
        return self._actuator.capabilities(snap, snap.ts).controllable

    def _override_state(self, snap: Snapshot) -> tuple[bool, CommandValue]:
        """``(bekannt, Wert)`` des Overrides; ``None`` ist „kein Override“, wenn er bekannt ist."""
        present = self._actuator.present(snap)
        return CommandKind.GRID_SETPOINT in present, present.get(CommandKind.GRID_SETPOINT)

    def _override_stuck(self) -> bool:
        """Steht ein Override? Vorgefunden war keiner. Unbekannt zählt nicht."""
        known, current = self._override_state(self._session.snapshot())
        return known and current is not None

    def _blocked(self, title: str, item: str = ITEM) -> bool:
        """Steht ein Override, den das Wiederherstellen nicht aufhob? Dann kommt kein weiterer."""
        if not self._stuck:
            return False
        self._record(title, "info", SKIPPED_STUCK, item=item)
        return True

    def _value_w(self, wanted_w: float) -> float:
        """Der Override für einen Schritt: ``wanted_w``, höchstens die Obergrenze."""
        return max(0.0, min(wanted_w, self._ceiling))

    @staticmethod
    def _ac_setpoint(snap: Snapshot) -> float | None:
        return finite_number(snap.ok("ess.grid_setpoint_w"))

    async def _set(self, kind: CommandKind, value: CommandValue) -> float | None:
        """``set_and_confirm`` mit ``confirm_s``."""
        self._wrote = True
        return await self._session.set_and_confirm(VICTRON, kind, value, self._t.confirm_s)

    def _failure(self) -> str:
        return self._session.failure or f"nicht bestätigt nach {de(self._t.confirm_s)} s"

    def _record(self, title: str, status: Status, detail: str, *, item: str = ITEM) -> None:
        self._session.record(Finding(item, title, status, detail))
