"""Zahlen in Texten und die Zahlenregel für Werte.

Das liegt in ``shared``, weil nicht nur der core so mit Zahlen umgeht: Die Safety-Schicht und die
Entscheidungen (``shared.decisions``) schreiben Zahlen gleich und prüfen sie nach derselben Regel.
"""

from __future__ import annotations

import math


def de(value: float) -> str:
    """Zahl für Begründungstexte: zehn signifikante Stellen, Dezimalkomma, echtes Minus (U+2212)."""
    return f"{value:.10g}".replace(".", ",").replace("-", "\N{MINUS SIGN}")


def finite_number(value: object) -> float | None:
    """Der Wert als endliche Zahl; ``bool``, ``None``, NaN, ±∞ und alles andere gelten nicht.

    Das ist die Zahlenregel der Safety-Regeln: Was hier ``None`` ergibt, ist ungültig (der Wert
    eines Befehls) oder unbekannt (ein Wert aus dem Kontext).
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:  # ein int, der größer ist als jedes float
        return None
    return number if math.isfinite(number) else None
