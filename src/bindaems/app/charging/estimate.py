"""Ladeschätzung je Modus für ein angestecktes Auto (reine Rechnung, ohne Ein- und Ausgabe).

Aus den Slots des aktuellen Plans schätzt sie für Geplant, Sofort, PV-Überschuss und Min+PV, was
das Laden bis zur Abfahrt kostet, wie viel Energie fließt und wann es fertig ist.

Entscheidungen:

- Wo das Laden stoppt, hängt vom Modus ab (wie im core, Spezifikation 7.5): „Geplant“ stoppt am
  Ziel, die anderen Modi kennen kein Ziel, das Auto stoppt an seinem eigenen Ladelimit, ohne
  bekanntes Limit bei 100 %.

- Der Hausakku zählt nicht: Als Überschuss gilt nur, was ins Netz ginge, und das eigene PV-Laden
  des Autos im Plan (``pv_for_car``); Akku- und Netzanteil des Plans zählen nicht dazu.
- Alle Zahlen sind „ca.“: Preise können geschätzt sein (``price_estimated``), und der Plan ändert
  sich mit jedem neuen Lauf.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from bindaems.app.planning.vehicles import need_kwh
from bindaems.shared.control import ChargeMode
from bindaems.shared.plan import PlanSlot
from bindaems.shared.timeutil import SLOT

ESTIMATED_MODES: Final = (ChargeMode.PLANNED, ChargeMode.NOW, ChargeMode.PV, ChargeMode.MIN_PV)
REACH_EPS_KWH: Final = 0.1  # wie ``reachable`` im Plan
_DONE_KWH: Final = 1e-9  # darunter gilt der Bedarf als gedeckt


@dataclass(frozen=True)
class ModeEstimate:
    """Schätzung für einen Modus; ``target_soc_pct`` ist der Ladestand, an dem das Laden in
    diesem Modus stoppt, ``energy_kwh`` die Energie an der Wallbox, ``soc_pct`` der Ladestand am
    Fensterende (nie unter dem jetzigen), ``power_cost_eur`` nur bei ``NOW``."""

    target_soc_pct: float
    energy_kwh: float
    cost_eur: float
    finish_at: datetime | None
    soc_pct: float
    reachable: bool
    power_cost_eur: float | None = None


@dataclass(frozen=True)
class ChargeEstimate:
    soc_pct: float
    target_soc_pct: float  # das Ziel von „Geplant“
    departure_at: datetime
    until: datetime
    need_kwh: float  # der Bedarf bis zum Ziel von „Geplant“
    price_estimated: bool
    modes: Mapping[ChargeMode, ModeEstimate]


@dataclass(frozen=True)
class _Piece:
    """Der Teil eines Slots, der im Fenster liegt."""

    slot: PlanSlot
    start: datetime
    hours: float
    pv_w: float  # Überschuss des Autos in diesem Slot


@dataclass
class _Run:
    """Zwischenstand einer Modus-Rechnung."""

    kwh: float = 0.0
    cost_ct: float = 0.0
    finish_at: datetime | None = None
    graded: bool = False  # ein Slot mit Netzanteil und geschätztem Preis wurde genutzt


def pv_for_car(slot: PlanSlot, key: str) -> float:
    """PV-Überschuss in W, den das Auto ``key`` im Slot hätte: Einspeisung plus sein eigenes
    PV-Laden im Plan (sein Ladestrom ohne Akku- und Netzanteil)."""
    own = slot.ev_w.get(key, 0.0) - slot.ev_battery_w.get(key, 0.0) - slot.import_w
    return slot.export_w + max(0.0, own)


def _window(
    slots: Sequence[PlanSlot], key: str, now: datetime, departure_at: datetime
) -> tuple[list[_Piece], datetime]:
    pieces: list[_Piece] = []
    until = now
    for s in sorted(slots, key=lambda x: x.start):
        end = s.start + SLOT
        if end <= now or s.start >= departure_at:
            continue
        a, b = max(s.start, now), min(end, departure_at)
        pieces.append(_Piece(s, a, (b - a).total_seconds() / 3600.0, pv_for_car(s, key)))
        until = b
    return pieces, until


def _charge_in_order(pieces: Sequence[_Piece], need: float, power_w: Sequence[float]) -> _Run:
    """Lädt die Stücke in zeitlicher Reihenfolge mit ``power_w`` je Stück, bis ``need`` gedeckt
    ist; das Netz zahlt, was über dem Überschuss liegt."""
    run = _Run()
    for piece, p in zip(pieces, power_w, strict=True):
        remaining = need - run.kwh
        if remaining <= _DONE_KWH:
            break
        if p <= 0:
            continue
        piece_kwh = p * piece.hours / 1000.0
        take = min(piece_kwh, remaining)
        grid_share = max(0.0, p - piece.pv_w) / p
        run.kwh += take
        run.cost_ct += take * grid_share * piece.slot.import_ct
        if take * grid_share > 0 and piece.slot.estimated:
            run.graded = True
        run.finish_at = piece.start + timedelta(hours=piece.hours * take / piece_kwh)
    return run


def _planned(pieces: Sequence[_Piece], need: float, max_w: float) -> _Run:
    """Erst alle freien PV-Stücke, dann die Netzstücke nach Preis (bei Gleichstand das frühere).
    Die Mindestleistung zählt hier nicht: Der Planer mischt PV und Netz."""
    run = _Run()
    free = [(p, min(p.pv_w, max_w) * p.hours / 1000.0) for p in pieces]
    grid = [(p, (max_w - min(p.pv_w, max_w)) * p.hours / 1000.0) for p in pieces]
    for is_grid, parts in ((False, free), (True, sorted(grid, key=lambda t: t[0].slot.import_ct))):
        for piece, avail in parts:
            remaining = need - run.kwh
            if remaining <= _DONE_KWH:
                return run
            if avail <= 0:
                continue
            take = min(avail, remaining)
            run.kwh += take
            end = piece.start + timedelta(hours=piece.hours * take / avail)
            if run.finish_at is None or end > run.finish_at:
                run.finish_at = end
            if is_grid:
                run.cost_ct += take * piece.slot.import_ct
                if piece.slot.estimated:
                    run.graded = True
    return run


def estimate_charge(
    slots: Sequence[PlanSlot],
    *,
    key: str,
    now: datetime,
    soc_pct: float,
    target_soc_pct: float,
    limit_soc_pct: float | None,
    departure_at: datetime,
    usable_kwh: float,
    efficiency: float,
    min_w: float,
    max_w: float,
    now_w: float,
    power_cost_eur: float | None,
) -> ChargeEstimate:
    """Schätzt das Laden des Autos ``key`` bis ``departure_at`` in den vier Modi.

    Das Fenster sind die Slots ab ``now`` bis zur Abfahrt (erster und letzter anteilig). Endet der
    Plan früher, endet das Fenster dort (``until``). ``departure_at`` muss nach ``now`` liegen;
    den nächsten Abfahrtstag wählt der Aufrufer.

    ``target_soc_pct`` ist das Ziel von „Geplant“, so wie der core es nimmt (unter einem nicht
    abgeglichenen Ladelimit das Limit, ``effective_target``); das rechnet der Aufrufer.
    ``limit_soc_pct`` ist das Ladelimit des Autos, an dem es in den anderen Modi stoppt;
    ``None`` heißt unbekannt, dann lädt es bis 100 %.
    """
    if departure_at <= now:
        raise ValueError("Die Abfahrt muss nach dem jetzigen Zeitpunkt liegen")
    stop_pct = {mode: 100.0 if limit_soc_pct is None else limit_soc_pct for mode in ESTIMATED_MODES}
    stop_pct[ChargeMode.PLANNED] = target_soc_pct
    need = {
        mode: need_kwh(soc_pct, stop, usable_kwh, efficiency) for mode, stop in stop_pct.items()
    }
    pieces, until = _window(slots, key, now, departure_at)

    pv_only = [min(p.pv_w, max_w) if p.pv_w >= min_w else 0.0 for p in pieces]
    min_pv = [min(max_w, max(min_w, p.pv_w)) for p in pieces]
    runs: dict[ChargeMode, _Run] = {
        ChargeMode.PLANNED: _planned(pieces, need[ChargeMode.PLANNED], max_w),
        ChargeMode.NOW: _charge_in_order(pieces, need[ChargeMode.NOW], [now_w] * len(pieces)),
        ChargeMode.PV: _charge_in_order(pieces, need[ChargeMode.PV], pv_only),
        ChargeMode.MIN_PV: _charge_in_order(pieces, need[ChargeMode.MIN_PV], min_pv),
    }

    modes: dict[ChargeMode, ModeEstimate] = {}
    for mode in ESTIMATED_MODES:
        r, stop = runs[mode], stop_pct[mode]
        gained = soc_pct + r.kwh * efficiency / usable_kwh * 100.0
        soc_end = max(soc_pct, min(stop, gained))  # über dem Stopp lädt nichts, nie darunter
        modes[mode] = ModeEstimate(
            target_soc_pct=stop,
            energy_kwh=round(r.kwh, 1),
            cost_eur=round(r.cost_ct / 100.0, 2),
            finish_at=r.finish_at,
            soc_pct=round(soc_end, 1),
            reachable=r.kwh >= need[mode] - REACH_EPS_KWH,
            power_cost_eur=power_cost_eur if mode is ChargeMode.NOW else None,
        )
    return ChargeEstimate(
        soc_pct=soc_pct,
        target_soc_pct=target_soc_pct,
        departure_at=departure_at,
        until=until,
        need_kwh=need[ChargeMode.PLANNED],
        price_estimated=any(r.graded for r in runs.values()),
        modes=modes,
    )
