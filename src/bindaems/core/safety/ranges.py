"""Regeln 1–3 der Safety-Schicht: Werte in ihre harten Grenzen bringen oder ablehnen (Spec 8.3).

``check_range`` ist rein: Es klemmt den Wert eines Befehls oder lehnt ihn ab und nennt jeden
Eingriff als ``Finding``. Die Grenzen kommen aus den ``HardLimits``.
"""

from __future__ import annotations

import math

from bindaems.core.safety.limits import CurrentRange, HardLimits
from bindaems.core.safety.types import Finding, Step, de
from bindaems.shared.control import Command, CommandKind


def check_range(command: Command, limits: HardLimits, budget_w: float | None) -> Step:
    """Bringt den Wert des Befehls in seine Grenzen oder lehnt den Befehl ab.

    Aktor und Art sind schon geprüft; für ``current`` muss der Aktor in ``limits.currents`` stehen.
    Zuerst muss der Wert vom richtigen Typ und endlich sein (``value.invalid``). Danach gelten je
    Art die Regeln von oben nach unten, jede auf das Ergebnis der vorigen; jeder Eingriff ergibt ein
    ``Finding``. Zahlen kommen als ``float`` heraus. Bei ``reject`` ist ``value`` gleich ``None``.

    ``budget_w`` ist das 15-min-Budget (``None`` heißt keins) und begrenzt den Netz-Sollwert mit.
    """
    kind = command.kind
    if kind == CommandKind.CHARGING:
        return Step(command.value) if isinstance(command.value, bool) else _invalid(command)
    if kind == CommandKind.GRID_SETPOINT and command.value is None:
        return Step(None)  # hebt den Override auf

    value = _finite_number(command.value)
    if value is None:
        return _invalid(command)
    if kind == CommandKind.CURRENT:
        return _check_current(value, limits.currents[command.actuator])
    if kind == CommandKind.GRID_SETPOINT:
        ceiling, name = limits.grid_setpoint_max(budget_w)
        return _check_grid_setpoint(value, ceiling, name)
    if kind == CommandKind.MAX_DISCHARGE:
        return _check_max_discharge(value, limits.max_discharge_w)
    if kind == CommandKind.MIN_SOC:
        return _check_min_soc(value, limits.reserve_soc_pct)
    return _invalid(command)  # unbekannte Art: im Zweifel ablehnen


def _finite_number(value: object) -> float | None:
    """Der Wert als endliche Zahl; ``bool``, ``None``, NaN, ±∞ und alles andere gelten nicht."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:  # ein int, der größer ist als jedes float
        return None
    return number if math.isfinite(number) else None


def _reject(code: str, text: str, *earlier: Finding) -> Step:
    """Ablehnung mit den früheren Findings und dem neuen; ``value`` ist dabei immer ``None``."""
    return Step(None, (*earlier, Finding(code, text)), reject=True)


def _invalid(command: Command) -> Step:
    return _reject("value.invalid", f"Ungültiger Wert für „{command.kind}“: {command.value!r}.")


def _outside(value: float, unit: str) -> Step:
    return _reject(
        "range.invalid", f"Wert {de(value)} {unit} liegt außerhalb des zulässigen Bereichs."
    )


def _capped(limit: float, unit: str) -> Finding:
    return Finding("range.max", f"Auf den Höchstwert {de(limit)} {unit} begrenzt.")


def _check_current(value: float, current: CurrentRange) -> Step:
    """Ladestrom in A: auf ganze Ampere abrunden, höchstens ``max_a``, mindestens ``min_a``."""
    findings: list[Finding] = []
    whole = math.floor(value)
    if whole != value:
        text = f"Auf ganze Ampere abgerundet: {de(value)} A → {de(whole)} A."
        findings.append(Finding("range.integer", text))
        value = float(whole)
    if value > current.max_a:
        findings.append(_capped(current.max_a, "A"))
        value = float(current.max_a)
    if value < current.min_a:
        text = f"Unter dem Mindeststrom {current.min_a} A; zum Abschalten das Laden beenden."
        return _reject("range.min", text, *findings)
    return Step(value, tuple(findings))


def _check_grid_setpoint(value: float, ceiling: float, name: str) -> Step:
    """Netz-Sollwert in W: nie negativ, höchstens ``ceiling`` (die bindende Grenze ``name``)."""
    findings: list[Finding] = []
    if value < 0:
        text = f"Netz-Sollwert nie negativ (kein Akku ins Netz): {de(value)} W → 0 W."
        findings.append(Finding("range.negative", text))
        value = 0.0
    ceiling = max(ceiling, 0.0)  # ein aufgebrauchtes Budget drückt den Sollwert auf 0, nie darunter
    if value > ceiling:
        findings.append(
            Finding("range.ceiling", f"Netz-Sollwert auf {de(ceiling)} W begrenzt ({name}).")
        )
        value = ceiling
    return Step(value, tuple(findings))


def _check_max_discharge(value: float, limit: float) -> Step:
    """Entladegrenze in W: −1 (unbegrenzt) oder 0 bis ``limit``."""
    if value == -1:
        return Step(value)
    if value < 0:
        return _outside(value, "W")
    if value > limit:
        return Step(limit, (_capped(limit, "W"),))
    return Step(value)


def _check_min_soc(value: float, reserve: float) -> Step:
    """Min-SOC in %: mindestens die USV-Reserve, höchstens 100."""
    if value < reserve:
        text = f"Min-SOC {de(value)} % liegt unter der USV-Reserve {de(reserve)} %."
        return _reject("range.reserve", text)
    if value > 100:
        return _outside(value, "%")
    return Step(value)
