"""Klimaanlage: Betriebsart aus den Zuständen (Plan 6a, Entscheidung 11).

Die Betriebsart bestimmt das Vorzeichen des Sollwertversatzes; bei ``lueften``, ``aus`` und
``unbekannt`` ist der Versatz 0. Health zeigt sie, die Strategie (Task 6) regelt danach.

Die Schicht ist rein: Sie liest nur den Snapshot.
"""

from __future__ import annotations

from typing import Final, Literal

from bindaems.shared.domain import Snapshot

ThermalMode = Literal["heizen", "kuehlen", "lueften", "aus", "unbekannt"]

# Reihenfolge der Vorgabe: Sind mehrere Bits gesetzt, gilt das erste
_ORDER: Final[tuple[tuple[str, ThermalMode], ...]] = (
    ("heating", "heizen"),
    ("venting", "lueften"),
    ("cooling", "kuehlen"),
)


def thermal_mode(snap: Snapshot, name: str) -> ThermalMode:
    """Die Betriebsart des Geräts ``name`` aus ``thermal.<name>.running``, ``.heating`` usw.

    - ``aus``, wenn ``running`` falsch ist
    - sonst, wenn die Anlage läuft, ``heizen``, ``lueften`` oder ``kuehlen`` nach dem ersten
      gesetzten Bit in dieser Reihenfolge (widersprüchliche Bits: das erste gilt)
    - ``unbekannt``, wenn ``running`` unbekannt ist, die Anlage ohne gesetzte Betriebsart läuft
      oder ein Bit vor dem ersten gesetzten unbekannt ist (es könnte vorgehen)

    Ein Zustand zählt nur als Wahrheitswert mit Qualität ``OK``.
    """
    prefix = f"thermal.{name}."
    running = _flag(snap, prefix + "running")
    if running is None:
        return "unbekannt"
    if not running:
        return "aus"
    for signal, mode in _ORDER:
        flag = _flag(snap, prefix + signal)
        if flag is None:
            return "unbekannt"
        if flag:
            return mode
    return "unbekannt"


def _flag(snap: Snapshot, signal: str) -> bool | None:
    value = snap.ok(signal)
    return value if isinstance(value, bool) else None
