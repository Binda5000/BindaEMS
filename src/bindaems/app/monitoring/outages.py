"""Netzausfälle erkennen und protokollieren.

Ein Ausfall beginnt, wenn die aktive Eingangsquelle des Victron auf Inselbetrieb (240) steht, und
endet mit der Rückkehr einer anderen Quelle. Solange die Quelle unbekannt ist (getrennt oder
veraltet), ändert sich nichts. Die Energie zählt nur an AC-Out (die USV-gestützten Lasten); ein
Abschnitt zählt nur, wenn alle drei Phasen einen Wert haben.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import Engine, Row, insert, select, update

from bindaems.app.core_link import LiveState
from bindaems.app.db.schema import grid_outage
from bindaems.shared.timeutil import Clock

log = structlog.get_logger(__name__)

ISLAND_SOURCE = 240  # active_in_source im Inselbetrieb (der core ist der app verwehrt)
MIN_COVERED_S = 900.0  # darunter taugt der Mittelwert nicht
MAX_STEP_S = 30.0  # ein längerer Abstand zwischen zwei Messungen zählt höchstens so lang
FLUSH_S = 60.0  # laufende Werte höchstens so oft schreiben

SOURCE = "system.active_in_source"
SOC = "battery.soc_pct"
LOADS = tuple(f"consumption_output.l{n}.power_w" for n in (1, 2, 3))


@dataclass(frozen=True)
class Outage:
    id: int
    start: datetime
    end: datetime | None
    end_estimated: bool
    soc_start: float | None
    soc_end: float | None
    soc_min: float | None
    load_wh: float  # Energie an AC-Out
    covered_s: float  # Zeit mit Lastwert aller drei Phasen

    @property
    def mean_w(self) -> float | None:
        """Mittlere Last an AC-Out; ``None``, solange zu wenig Zeit abgedeckt ist."""
        if self.covered_s < MIN_COVERED_S:
            return None
        return self.load_wh * 3600.0 / self.covered_s


def _outage(row: Row[Any]) -> Outage:
    m = row._mapping
    return Outage(
        id=m["id"],
        start=m["start"],
        end=m["end"],
        end_estimated=bool(m["end_estimated"]),
        soc_start=m["soc_start"],
        soc_end=m["soc_end"],
        soc_min=m["soc_min"],
        load_wh=m["load_wh"],
        covered_s=m["covered_s"],
    )


class OutageTracker:
    def __init__(self, engine: Engine, live: LiveState, clock: Clock, *, poll_s: float = 10.0):
        self._engine = engine
        self._live = live
        self._clock = clock
        self._poll_s = poll_s
        self._open: Outage | None = None
        self._checked = False  # erste bekannte Quelle seit dem Start ausgewertet
        self._last_poll: datetime | None = None
        self._last_flush: datetime | None = None

    async def run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.poll)
            except Exception:
                log.exception("Netzausfall-Erkennung fehlgeschlagen")
            await asyncio.sleep(self._poll_s)

    def poll(self) -> None:
        """Ein Schritt: Quelle lesen, Ausfall beginnen, fortschreiben oder beenden."""
        now = self._clock.now()
        previous, self._last_poll = self._last_poll, now
        source = self._live.value(SOURCE)
        if not isinstance(source, int | float) or isinstance(source, bool):
            return
        soc = self._live.value(SOC)
        soc = float(soc) if isinstance(soc, int | float) else None
        island = source == ISLAND_SOURCE
        if not self._checked:
            self._checked = True
            self._open = self._load_open()
            if self._open is not None and not island:
                self._close(self._open, now, soc, estimated=True)
                return
        if self._open is not None:
            self._open = outage = self._account(self._open, now, previous, soc)
            if island:
                self._flush(outage, now)
            else:
                self._close(outage, now, soc, estimated=False)
        elif island:
            self._begin(now, soc)

    def current(self) -> Outage | None:
        if self._open is not None:
            return self._open
        if self._checked:
            return None
        return self._load_open()

    def recent(self, limit: int = 20) -> list[Outage]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(grid_outage).order_by(grid_outage.c.start.desc()).limit(limit)
            ).all()
        outages = [_outage(row) for row in rows]
        if self._open is not None:
            outages = [self._open if o.id == self._open.id else o for o in outages]
        return outages

    def _load_open(self) -> Outage | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(grid_outage)
                .where(grid_outage.c.end.is_(None))
                .order_by(grid_outage.c.start.desc())
                .limit(1)
            ).first()
        return _outage(row) if row is not None else None

    def _begin(self, now: datetime, soc: float | None) -> None:
        values = {
            "start": now,
            "end": None,
            "end_estimated": False,
            "soc_start": soc,
            "soc_end": None,
            "soc_min": soc,
            "load_wh": 0.0,
            "covered_s": 0.0,
        }
        with self._engine.begin() as conn:
            result = conn.execute(insert(grid_outage).values(**values))
            outage_id = int(result.inserted_primary_key[0])  # type: ignore[index]
        self._open = Outage(id=outage_id, **values)  # type: ignore[arg-type]
        self._last_flush = now
        log.info("Netzausfall beginnt", soc_pct=soc)

    def _account(
        self, outage: Outage, now: datetime, previous: datetime | None, soc: float | None
    ) -> Outage:
        if soc is not None and (outage.soc_min is None or soc < outage.soc_min):
            outage = replace(outage, soc_min=soc)
        loads = [self._live.value(signal) for signal in LOADS]
        if previous is not None and all(isinstance(w, int | float) for w in loads):
            step = min(max((now - previous).total_seconds(), 0.0), MAX_STEP_S)
            outage = replace(
                outage,
                load_wh=outage.load_wh + sum(loads) * step / 3600.0,  # type: ignore[arg-type]
                covered_s=outage.covered_s + step,
            )
        return outage

    def _flush(self, outage: Outage, now: datetime) -> None:
        if self._last_flush is not None and (now - self._last_flush).total_seconds() < FLUSH_S:
            return
        self._store(outage)
        self._last_flush = now

    def _close(self, outage: Outage, now: datetime, soc: float | None, *, estimated: bool) -> None:
        closed = replace(outage, end=now, end_estimated=estimated, soc_end=soc)
        self._store(closed)
        self._open = None
        log.info("Netzausfall endet", soc_pct=soc, geschaetzt=estimated)

    def _store(self, outage: Outage) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(grid_outage)
                .where(grid_outage.c.id == outage.id)
                .values(
                    end=outage.end,
                    end_estimated=outage.end_estimated,
                    soc_end=outage.soc_end,
                    soc_min=outage.soc_min,
                    load_wh=outage.load_wh,
                    covered_s=outage.covered_s,
                )
            )
