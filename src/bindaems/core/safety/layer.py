"""SafetyLayer: die Prüfung jedes ausgehenden Befehls gegen alle Regeln aus Spec 8.3.

``authorize`` führt die Regeln in fester Reihenfolge aus: Aktorkatalog, Betriebszustand (Regel 8),
Sperren (7.9), Betriebsart, Eingangsdaten (Regel 7), Wertebereiche (Regeln 1–3), Phasenvorhersage
(Regel 4), Totband und Rate-Limits (Regel 5) und das Schreibbudget (Regel 6). Das Ergebnis ist ein
``Verdict``; im Dry-Run wird genauso geprüft und erfasst wie live, nur nicht gesendet.

Ein dringender Befehl, der nachweislich Richtung sicherer Zustand geht (``toward_safe``), darf auch
in SAFE schreiben und trotz unplausibler Eingangsdaten. Er umgeht Mindestabstände, Stundenlimits
und Totbänder, außer bei gleichem Wert, und das Schreibbudget nur mit dem exakten Sicherwert seiner
Art. Sperren und die Regeln 1–4 gelten auch für ihn.

Zeit, Messwerte, Freigaben und Sperren kommen über den ``SafetyContext``. Gedächtnis haben nur der
Rate-Limiter und das Schreibbudget: Sie erfassen jeden Befehl, der einen Wert zum Senden ergibt.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

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
    CommandValue,
    OperatingState,
)
from bindaems.shared.domain import Alarm

URGENT_IGNORED: Final = Finding(
    "urgent.ignored",
    "Als dringend markiert, aber nicht Richtung sicherer Zustand; es gelten die normalen Regeln.",
)
URGENT_BYPASS: Final = Finding(
    "urgent.bypass", "Dringend (Richtung sicherer Zustand): ohne Mindestabstand und Limits."
)

_CONTROL_STATES: Final = frozenset({OperatingState.CONTROL, OperatingState.DEGRADED})


class SafetyLayer:
    """Prüft jeden Befehl an einen Aktor, bevor er gesendet werden darf (Spec 8.3).

    Die Schicht hält den Rate-Limiter (Regel 5) selbst; das Schreibbudget (Regel 6) bekommt sie
    von außen, denn es liegt in einer Datei unter ``core.data_dir``.
    """

    def __init__(self, limits: HardLimits, budget: PersistentWriteBudget) -> None:
        self._limits = limits
        self._budget = budget
        self._rate = RateLimiter(limits)

    def authorize(self, command: Command, ctx: SafetyContext) -> Verdict:
        """Prüft den Befehl im Kontext ``ctx`` und liefert das Urteil.

        Die Schritte, genau in dieser Reihenfolge:

        1. Aktor im Katalog und Art beim Aktor erlaubt, sonst ``REJECTED``.
        2. ``urgent_ok``: dringend und ``toward_safe``. Ist der Befehl als dringend markiert, geht
           aber nicht Richtung sicher, kommt ``urgent.ignored`` dazu, und er wird normal geprüft.
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
            Dry-Run, aber nie bei ``UNCHANGED``, ``DEFERRED`` oder ``REJECTED``.
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

        # 2. Dringend und nachweislich Richtung sicherer Zustand?
        urgent_ok = command.urgent and toward_safe(command, ctx, limits)
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

        # 7. Regeln 1–3: Wertebereiche
        ranged = check_range(command, limits, ctx.grid_budget_w)
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
