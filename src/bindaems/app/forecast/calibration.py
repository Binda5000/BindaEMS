"""Kalibrierung der PV-Prognose (Plan 3a, Präzisierung 9); hier vorerst nur die Klarheit."""

from __future__ import annotations

from bindaems.app.forecast.archive import Clearness

MIN_GHI_W_M2 = 50.0  # darunter (Nacht, Dämmerung) gibt es keine Klasse
CLEAR_FROM = 0.6
MIXED_FROM = 0.3


def clearness_class(ghi_w_m2: float | None, diffuse_w_m2: float | None) -> Clearness | None:
    """Klarheit aus ``k = 1 − diffus / global``; ohne Werte oder unter 50 W/m² global ``None``."""
    if ghi_w_m2 is None or diffuse_w_m2 is None or ghi_w_m2 < MIN_GHI_W_M2:
        return None
    k = round(1.0 - diffuse_w_m2 / ghi_w_m2, 9)
    if k >= CLEAR_FROM:
        return "clear"
    if k >= MIXED_FROM:
        return "mixed"
    return "overcast"
