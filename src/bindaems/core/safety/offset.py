"""Schrittregel für den Sollwertversatz der Klimaanlage (Plan 6a, Entscheidung 8).

Ein Befehl, der den Betrag des Versatzes gegenüber dem vorhandenen Wert erhöht, darf ihn um
höchstens eine Stufe (``step_k``) erhöhen und erst ``interval_s`` nach der letzten Erhöhung. Ein
Vorzeichenwechsel geht nur über 0. Verringerungen des Betrags und 0 sind immer erlaubt.

Vorhanden ist, je nach Betriebsart des Aktors:

- **live:** das Echo der UVR (``present_k``); ohne bekanntes Echo gilt 0
- **Dry-Run:** der zuletzt erfasste Wert, ohne einen solchen 0. Im Dry-Run sendet der Adapter 0, das
  Echo bleibt also 0; gegen das Echo zeigte das Entscheidungslog sonst jeden zweiten Schritt als
  abgelehnt.

Ein Echo, das höchstens ``OFFSET_SAME_K`` vom Wert abweicht, zählt als dieser Wert (die Toleranz der
Bestätigung im Aktor); ein Echo bis ``OFFSET_SAME_K`` um 0 zählt als 0.

Die Zeit ist monoton und kommt in Sekunden als Argument. Sie lebt nur im Speicher: Nach einem
Neustart sendet der Adapter ohnehin 0.
"""

from __future__ import annotations

from collections.abc import Mapping

from bindaems.core.control.actuator import OFFSET_SAME_K
from bindaems.core.safety.limits import OffsetRange
from bindaems.core.safety.types import Finding, de, finite_number, invalid_value
from bindaems.shared.control import Command

_SIGN = Finding("offset.step", "Vorzeichenwechsel nur über 0.")


class OffsetSteps:
    """Merkt sich je Klima-Aktor die letzte Erhöhung und den zuletzt erfassten Versatz."""

    def __init__(self, ranges: Mapping[str, OffsetRange]) -> None:
        self._ranges = ranges
        self._raised_at: dict[str, float] = {}  # Zeit der letzten Erhöhung des Betrags
        self._decided: dict[str, float] = {}  # zuletzt erfasster Wert (Bezug im Dry-Run)

    def check(
        self, command: Command, present_k: float | None, now: float, *, dry_run: bool = False
    ) -> Finding | None:
        """Befund ``offset.step``, wenn der Befehl die Schrittregel verletzt, sonst ``None``.

        ``command.value`` ist der Wert nach den Regeln 1–3, der Aktor steht in ``ranges``. Ein Wert,
        der keine endliche Zahl ist, ergibt ``value.invalid``. ``check`` ändert nichts; erst
        ``record`` merkt sich einen Befehl.
        """
        value = finite_number(command.value)
        if value is None:
            return invalid_value(command.kind, command.value)
        actuator = command.actuator
        before = self._reference(actuator, present_k, dry_run)
        if not _raises(value, before):
            return None
        if value * before < 0:
            return _SIGN
        limits = self._ranges[actuator]
        raised_at = self._raised_at.get(actuator)
        one_step = abs(value) - abs(before) <= limits.step_k + OFFSET_SAME_K
        waited = raised_at is None or now - raised_at >= limits.interval_s
        if one_step and waited:
            return None
        minutes = de(limits.interval_s / 60)
        return Finding("offset.step", f"Versatz nur eine Stufe je {minutes} min weg von 0.")

    def record(
        self, command: Command, present_k: float | None, now: float, *, dry_run: bool = False
    ) -> None:
        """Merkt sich den Befehl: Erhöht er den Betrag gegenüber dem vorhandenen Wert (Bezug wie
        bei ``check``), gilt ``now`` als Zeit der letzten Erhöhung. Der Wert ist danach der Bezug
        im Dry-Run. Ein Wert, der keine endliche Zahl ist, wird nicht erfasst."""
        value = finite_number(command.value)
        if value is None:
            return
        actuator = command.actuator
        if _raises(value, self._reference(actuator, present_k, dry_run)):
            self._raised_at[actuator] = now
        self._decided[actuator] = value

    def _reference(self, actuator: str, present_k: float | None, dry_run: bool) -> float:
        """Der vorhandene Wert: im Dry-Run der zuletzt erfasste, live das Echo; sonst 0."""
        before = self._decided.get(actuator) if dry_run else finite_number(present_k)
        if before is None or abs(before) <= OFFSET_SAME_K:
            return 0.0
        return before


def _raises(value: float, before: float) -> bool:
    """Geht ``value`` dem Betrag nach über ``before`` hinaus oder auf die andere Seite von 0?"""
    if value == 0:
        return False
    return value * before < 0 or abs(value) > abs(before) + OFFSET_SAME_K
