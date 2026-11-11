"""Baum der PV-Anlagen und Strings mit Live-Werten; „Nicht zugeordnet“ steht nur oben.

Strings messen DC, die Anlage AC (Wandlerverluste): unter einer Anlage gibt es deshalb keine
Restzeile, sondern die Summe der Strings.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from bindaems.app.pv.service import PvSource

UNASSIGNED: Final = "Nicht zugeordnet"
UNASSIGNED_COLOR: Final = "#9ca3af"

Energy = tuple[float | None, str | None]


@dataclass(frozen=True)
class PvNode:
    id: int | None  # ``None``: „Nicht zugeordnet“
    name: str
    color: str
    kind: Literal["system", "string", "unassigned"]
    power_w: float | None
    kwh_today: float | None
    kwp: float | None
    kwh_per_kwp: float | None
    note: str | None  # warum ein Wert fehlt
    children: list[PvNode] = field(default_factory=list)
    strings_power_w: float | None = None  # Summe der Strings
    strings_kwh_today: float | None = None


def _sum(values: Sequence[float | None]) -> float | None:
    """Summe; ``None``, wenn kein Wert da ist oder einer fehlt."""
    if not values or any(value is None for value in values):
        return None
    return round(math.fsum(value for value in values if value is not None), 3)


def _rest(total: float | None, parts: Sequence[float | None]) -> float | None:
    summed = _sum(parts)
    if total is None or summed is None:
        return None
    return round(max(0.0, total - summed), 3)


def _order(sources: Sequence[PvSource]) -> list[PvSource]:
    return sorted(sources, key=lambda s: (s.sort, s.name.casefold(), s.id))


def build_pv_tree(
    sources: Sequence[PvSource],
    power: Callable[[PvSource], float | None],
    note: Callable[[PvSource], str | None],
    energy: Callable[[PvSource], Energy],
    total_w: float | None,
    total_kwh: float | None,
) -> list[PvNode]:
    """Anlagen nach ``sort`` und Name, darunter ihre Strings; „Nicht zugeordnet“ zuletzt."""
    ids = {source.id for source in sources}
    children: dict[int | None, list[PvSource]] = {}
    for source in _order(sources):
        parent = source.parent_id if source.parent_id in ids else None
        children.setdefault(parent, []).append(source)

    def node(source: PvSource, kind: Literal["system", "string"]) -> PvNode:
        kwh, energy_note = energy(source)
        watts = power(source)
        kids = [node(child, "string") for child in children.get(source.id, [])]
        per_kwp = None
        if kwh is not None and source.kwp is not None:
            per_kwp = round(kwh / source.kwp, 3)
        return PvNode(
            id=source.id,
            name=source.name,
            color=source.color,
            kind=kind,
            power_w=watts,
            kwh_today=kwh,
            kwp=source.kwp,
            kwh_per_kwp=per_kwp,
            note=note(source) or (energy_note if kwh is None else None),
            children=kids,
            strings_power_w=_sum([k.power_w for k in kids]),
            strings_kwh_today=_sum([k.kwh_today for k in kids]),
        )

    systems = [node(source, "system") for source in children.get(None, [])]
    if systems:
        systems.append(
            PvNode(
                id=None,
                name=UNASSIGNED,
                color=UNASSIGNED_COLOR,
                kind="unassigned",
                power_w=_rest(total_w, [s.power_w for s in systems]),
                kwh_today=_rest(total_kwh, [s.kwh_today for s in systems]),
                kwp=None,
                kwh_per_kwp=None,
                note=None,
            )
        )
    return systems
