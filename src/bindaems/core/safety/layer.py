"""SafetyLayer: die Prüfung jedes ausgehenden Befehls gegen alle Regeln aus Spec 8.3.

``authorize`` führt die Regeln in fester Reihenfolge aus: Aktorkatalog, Betriebszustand (Regel 8),
Sperren (7.9), Betriebsart, Eingangsdaten (Regel 7), Wertebereiche (Regeln 1–3), Phasenvorhersage
(Regel 4), Totband und Rate-Limits (Regel 5) und das Schreibbudget (Regel 6). Das Ergebnis ist ein
``Verdict``; im Dry-Run wird genauso geprüft und erfasst wie live, nur nicht gesendet.

Ein dringender Befehl, der nachweislich Richtung sicherer Zustand geht (``toward_safe``), darf auch
in SAFE schreiben und trotz unplausibler Eingangsdaten. Nachweislich heißt: mit dem angeforderten
Wert und mit dem Wert nach den Regeln 1–3; Regel 4 verringert nur noch oder lockert die
Entladegrenze. Er umgeht Mindestabstände, Stundenlimits und Totbänder, außer bei gleichem Wert, und
das Schreibbudget nur mit dem exakten Sicherwert seiner Art. Sperren und die Regeln 1–4 gelten auch
für ihn.

Zeit, Messwerte, Freigaben und Sperren kommen über den ``SafetyContext``. Gedächtnis haben nur der
Rate-Limiter und das Schreibbudget: Sie erfassen jeden Befehl, der einen Wert zum Senden ergibt.
Das Schreibbudget liegt in einer Datei; die Stundeneinträge des Rate-Limiters (Tesla) legt die
Schicht in ``rate_file``, damit auch sie einen Neustart überstehen.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import structlog

from bindaems.core.persistence.files import read_json, write_json_atomic
from bindaems.core.safety.budget import PersistentWriteBudget
from bindaems.core.safety.limits import HardLimits, toward_safe
from bindaems.core.safety.phases import check_phases
from bindaems.core.safety.ranges import check_range
from bindaems.core.safety.rate import RateLimiter, deadband
from bindaems.core.safety.types import Finding, Outcome, SafetyContext, Verdict
from bindaems.shared.control import (
    PERSISTENT_KINDS,
    ActuatorMode,
    Command,
    CommandKind,
    CommandValue,
    OperatingState,
)
from bindaems.shared.domain import Alarm
from bindaems.shared.timeutil import ensure_utc

log = structlog.get_logger(__name__)

URGENT_IGNORED: Final = Finding(
    "urgent.ignored",
    "Als dringend markiert, aber nicht Richtung sicherer Zustand; es gelten die normalen Regeln.",
)
URGENT_BYPASS: Final = Finding(
    "urgent.bypass", "Dringend (Richtung sicherer Zustand): ohne Mindestabstand und Limits."
)

_CONTROL_STATES: Final = frozenset({OperatingState.CONTROL, OperatingState.DEGRADED})
_RATE_FILE_VERSION: Final = 1


class SafetyLayer:
    """Prüft jeden Befehl an einen Aktor, bevor er gesendet werden darf (Spec 8.3).

    Die Schicht hält den Rate-Limiter (Regel 5) selbst; das Schreibbudget (Regel 6) bekommt sie
    von außen, denn es liegt in einer Datei unter ``core.data_dir``.
    """

    def __init__(
        self,
        limits: HardLimits,
        budget: PersistentWriteBudget,
        *,
        rate_file: Path | None = None,
        now: datetime | None = None,
    ) -> None:
        """Die Schicht mit ihren Grenzen und dem Schreibbudget.

        Mit ``rate_file`` schreibt sie nach jedem erfassten Befehl eines Aktors mit Stundenlimits
        (Tesla) dessen Einträge atomar dorthin: ``{"version": 1, "entries": [{"actuator": …,
        "kind": …, "ts": ISO-Zeit}, …]}``. Mit ``rate_file`` und ``now`` lädt sie die Datei beim
        Start; Einträge, die zu ``now`` eine Stunde oder älter sind, fallen weg. Eine fehlende Datei
        ergibt einen leeren Start, eine unlesbare oder nicht im Format (ganz oder gar nicht) auch,
        dazu eine Warnung im Log. Das Laden schreibt nichts; die Datei entsteht oder heilt mit dem
        ersten Eintrag. Ohne ``now`` wird nicht geladen, wohl aber geschrieben.
        """
        self._limits = limits
        self._budget = budget
        self._rate = RateLimiter(limits)
        self._rate_file = rate_file
        if rate_file is not None and now is not None:
            self._load_rates(rate_file, now)

    def authorize(self, command: Command, ctx: SafetyContext) -> Verdict:
        """Prüft den Befehl im Kontext ``ctx`` und liefert das Urteil.

        Die Schritte, genau in dieser Reihenfolge:

        1. Aktor im Katalog und Art beim Aktor erlaubt, sonst ``REJECTED``.
        2. ``urgent_ok``: dringend, und ``toward_safe`` gilt für den angeforderten Wert und für den
           Wert nach den Regeln 1–3 (lehnen sie ab, nie). Ist der Befehl als dringend markiert,
           aber nicht ``urgent_ok``, kommt ``urgent.ignored`` dazu, und er wird normal geprüft.
        3. Zustand CONTROL oder DEGRADED, oder ``urgent_ok`` in SAFE; sonst ``REJECTED``.
        4. Gesperrter Aktor: ``REJECTED``, auch bei ``urgent_ok``.
        5. Betriebsart: Dry-Run, außer der Aktor steht in ``ctx.modes`` auf ``LIVE``.
        6. Unplausible Eingangsdaten ohne ``urgent_ok``: ``REJECTED``.
        7. Regeln 1–3 (``check_range``) und 8. Regel 4 (``check_phases``): Ablehnung ergibt
           ``REJECTED``, sonst gilt der Wert danach.
        9. Totband: ``UNCHANGED``; bei ``urgent_ok`` nur ein gleicher Wert (``rate.unchanged``).
           Ein Totband, das dabei nicht zählt, erscheint nicht unter den Befunden.
        10. Rate-Limit: ``DEFERRED``; bei ``urgent_ok`` stattdessen ``urgent.bypass``.
        11. Persistente Art bei erschöpftem Schreibbudget: ``REJECTED`` samt Alarm, außer bei
            ``urgent_ok``, wenn der Wert nach den Regeln 1–4 genau der Sicherwert der Art ist.
        12. Erfassen im Rate-Limiter und, bei persistenten Arten, im Schreibbudget; auch im
            Dry-Run, aber nie bei ``UNCHANGED``, ``DEFERRED`` oder ``REJECTED``. Bei einem Aktor
            mit Stundenlimits schreibt die Schicht danach ``rate_file`` neu.
        13. ``REDUCED``, wenn Regel 4 den Wert geändert hat; sonst ``CLAMPED``, wenn die Regeln 1–3
            ihn geändert haben; sonst ``ACCEPTED``.

        ``findings`` sammelt alle Befunde in der Reihenfolge ihres Entstehens. ``value`` ist bei
        ``REJECTED`` ``None``, sonst der Wert nach den Regeln 1–4. ``dry_run`` gibt die Betriebsart
        des Aktors an, auch wenn der Befehl abgelehnt wird.
        """
        limits = self._limits
        actuator = command.actuator
        now = ctx.now
        findings: list[Finding] = []
        # Schritt 5 steht vorn, denn jedes Urteil nennt die Betriebsart. Nur das Mitglied LIVE
        # schaltet live; alles andere, auch ein fehlender Eintrag, ist Dry-Run.
        dry_run = ctx.modes.get(actuator, ActuatorMode.DRY_RUN) is not ActuatorMode.LIVE

        def verdict(outcome: Outcome, value: CommandValue, *last: Finding) -> Verdict:
            return Verdict(command, outcome, value, (*findings, *last), dry_run)

        def rejected(*last: Finding) -> Verdict:
            return verdict(Outcome.REJECTED, None, *last)

        # 1. Aktor und Art
        spec = limits.actuators.get(actuator)
        if spec is None:
            return rejected(Finding("actuator.unknown", f"Unbekannter Aktor „{actuator}“."))
        if command.kind not in spec.kinds:
            text = f"„{spec.label}“ kennt den Befehl „{command.kind}“ nicht."
            return rejected(Finding("kind.unsupported", text))

        # 2. Dringend und nachweislich Richtung sicherer Zustand, mit dem angeforderten Wert und mit
        #    dem Wert nach den Regeln 1–3, denn diese können ihn wieder davon wegbringen. Die Regeln
        #    sind rein: Ihr Ergebnis wird hier schon berechnet, zählt aber erst in Schritt 7.
        ranged = check_range(command, limits, ctx.grid_budget_w)
        urgent_ok = (
            command.urgent
            and not ranged.reject
            and toward_safe(command, ctx, limits)
            and toward_safe(replace(command, value=ranged.value), ctx, limits)
        )
        if command.urgent and not urgent_ok:
            findings.append(URGENT_IGNORED)

        # 3. Betriebszustand (Regel 8): in SAFE nur, um den sicheren Zustand herzustellen
        state = ctx.operating
        if not (state in _CONTROL_STATES or (urgent_ok and state == OperatingState.SAFE)):
            return rejected(
                Finding("state.inactive", f"Regelbetrieb nicht aktiv (Zustand {state}).")
            )

        # 4. Sperre (7.9), auch für dringende Befehle
        reasons = ctx.locks.get(actuator)
        if reasons:
            return rejected(Finding("lock", f"Gesperrt: {'; '.join(reasons)}"))

        # 6. Eingangsdaten (Regel 7)
        if not ctx.inputs.ok and not urgent_ok:
            problems = "; ".join(ctx.inputs.problems)
            return rejected(Finding("inputs", f"Eingangsdaten unplausibel: {problems}"))

        # 7. Regeln 1–3: Wertebereiche (in Schritt 2 berechnet)
        findings.extend(ranged.findings)
        if ranged.reject:
            return rejected()

        # 8. Regel 4: Phasenvorhersage mit dem Wert nach den Regeln 1–3
        phased = check_phases(command, ranged.value, ctx, limits)
        findings.extend(phased.findings)
        if phased.reject:
            return rejected()
        value = phased.value

        # 9. Totband (Regel 5); dringend zählt nur ein gleicher Wert
        band = deadband(command, value, ctx, limits)
        if band is not None and (not urgent_ok or band.code == "rate.unchanged"):
            return verdict(Outcome.UNCHANGED, value, band)

        # 10. Mindestabstände und Stundenlimits (Regel 5)
        limited = self._rate.check(command, now)
        if limited is not None:
            if not urgent_ok:
                return verdict(Outcome.DEFERRED, value, limited)
            findings.append(URGENT_BYPASS)

        # 11. Schreibbudget für persistente Einstellungen (Regel 6)
        persistent = command.kind in PERSISTENT_KINDS
        if persistent:
            exhausted = self._budget.check(now)
            if exhausted is not None and not (urgent_ok and self._is_safe_value(command, value)):
                self._budget.note_rejection(now)
                return rejected(exhausted)

        # 12. Erfassen, auch im Dry-Run
        self._rate.record(command, now)
        if self._rate_file is not None and self._rate.has_hourly_limits(actuator):
            self._save_rates(self._rate_file)
        if persistent:
            self._budget.record(now)

        # 13. Ergebnis
        if phased.value != ranged.value:
            return verdict(Outcome.REDUCED, value)
        if ranged.value != command.value:
            return verdict(Outcome.CLAMPED, value)
        return verdict(Outcome.ACCEPTED, value)

    def alarms(self, now: datetime) -> list[Alarm]:
        """Die aktiven Alarme der Schicht zum Zeitpunkt ``now``: die des Schreibbudgets."""
        return self._budget.alarms(now)

    def _is_safe_value(self, command: Command, value: CommandValue) -> bool:
        """Ist ``value`` der Sicherwert der Art des Befehls (``safe_commands``, Spec 8.5)?"""
        return any(
            safe.kind == command.kind and safe.value == value
            for safe in self._limits.safe_commands(command.actuator)
        )

    def _load_rates(self, path: Path, now: datetime) -> None:
        """Spielt die Einträge aus ``path`` in den Rate-Limiter ein; ohne Datei beginnt er leer.

        Eine Datei, die sich nicht lesen lässt oder nicht im Format ist, gilt als Ganzes als leer
        und ergibt eine Warnung. Die Datei bleibt unberührt.
        """
        try:
            entries = _parse_rates(read_json(path))
        except FileNotFoundError:
            return  # erster Start: nichts zu laden
        except (OSError, ValueError) as exc:
            log.warning("Datei der Rate-Limits unlesbar", path=str(path), error=str(exc))
            return
        self._rate.restore(entries, now)

    def _save_rates(self, path: Path) -> None:
        """Schreibt die Stundeneinträge des Rate-Limiters atomar nach ``path``.

        Ein Schreibfehler kommt ins Log und ändert das Urteil nicht: Der Speicher gilt weiter, und
        der nächste erfolgreiche Eintrag bringt die Datei auf den Stand.
        """
        entries = [
            {"actuator": actuator, "kind": str(kind), "ts": ensure_utc(recorded).isoformat()}
            for actuator, kind, recorded in self._rate.hourly_entries()
        ]
        try:
            write_json_atomic(path, {"version": _RATE_FILE_VERSION, "entries": entries})
        except OSError as exc:
            log.error("Datei der Rate-Limits nicht geschrieben", path=str(path), error=str(exc))


def _parse_rates(data: Any) -> list[tuple[str, CommandKind, datetime]]:
    """Die Einträge aus ``{"version": 1, "entries": [{"actuator", "kind", "ts"}, …]}``.

    ``ValueError``, wenn die Form abweicht: andere Version, fremde oder fehlende Schlüssel, Werte,
    die keine Zeichenketten sind, eine unbekannte Art, eine Zeit ohne Zeitzone oder keine ISO-Zeit.
    """
    if not isinstance(data, dict) or data.keys() != {"version", "entries"}:
        raise ValueError('erwartet ein Objekt der Form {"version": 1, "entries": […]}')
    if data["version"] != _RATE_FILE_VERSION:
        raise ValueError(f"unbekannte Version: {data['version']!r}")
    items = data["entries"]
    if not isinstance(items, list):
        raise ValueError('"entries" ist keine Liste')
    entries: list[tuple[str, CommandKind, datetime]] = []
    for item in items:
        if not isinstance(item, dict) or item.keys() != {"actuator", "kind", "ts"}:
            raise ValueError(f"Eintrag ist kein Objekt der Form {{actuator, kind, ts}}: {item!r}")
        actuator, kind, ts = item["actuator"], item["kind"], item["ts"]
        if not (isinstance(actuator, str) and isinstance(kind, str) and isinstance(ts, str)):
            raise ValueError(f"Aktor, Art und Zeit eines Eintrags sind Zeichenketten: {item!r}")
        moment = datetime.fromisoformat(ts)  # ValueError, wenn es keine ISO-Zeit ist
        if moment.tzinfo is None:
            raise ValueError(f"Eintrag ohne Zeitzone: {ts!r}")
        entries.append((actuator, CommandKind(kind), moment))  # ValueError bei unbekannter Art
    return entries
