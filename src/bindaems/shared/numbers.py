"""Zahlen in Texten für Nutzer und Logs.

Das liegt in ``shared``, weil Texte nicht nur der core erzeugt: Die Entscheidungstexte
(``shared.decisions``) und die Begründungen der Safety-Schicht schreiben Zahlen gleich.
"""

from __future__ import annotations


def de(value: float) -> str:
    """Zahl für Begründungstexte: zehn signifikante Stellen, Dezimalkomma, echtes Minus (U+2212)."""
    return f"{value:.10g}".replace(".", ",").replace("-", "\N{MINUS SIGN}")
