"""Versionierte Ablage der Lastmodelle (Plan 5a, Entscheidung 9, Präzisierungen 1 und 7).

Jedes Training legt eine Version an: eine Zeile in ``model_registry`` (Merkmale, Backtest,
Wichtigkeit, Aktivierungsentscheidung) und der Modelltext als Datei ``load-<id>.txt`` im
Modellverzeichnis. Behalten werden die neuesten Versionen und die aktive.

Synchron wie die anderen Archive: Aufrufer in der Ereignisschleife nehmen ``asyncio.to_thread``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import structlog
from sqlalchemy import Engine, Row, delete, insert, select, update

from bindaems.app.db.schema import model_registry
from bindaems.app.learning.backtest import BacktestResult
from bindaems.app.learning.model import TrainedModel, TrainingError, load_model
from bindaems.shared.timeutil import ensure_utc

log = structlog.get_logger(__name__)

KIND: Final = "load"
KEEP: Final = 14  # so viele neueste Versionen bleiben (Entscheidung 9); die aktive zusätzlich


@dataclass(frozen=True)
class RegistryEntry:
    id: int
    trained_at: datetime
    data_from: datetime
    data_to: datetime
    samples: int
    features: tuple[str, ...]
    max_w: float
    backtest: dict[str, Any] | None
    importance: dict[str, float]
    usable: bool
    active: bool
    decided_at: datetime | None
    decision: dict[str, Any] | None
    file: str = ""  # Dateiname relativ zum Modellverzeichnis


def _improvement_pct(model_mae_w: float, profile_mae_w: float) -> float | None:
    return (1.0 - model_mae_w / profile_mae_w) * 100.0 if profile_mae_w > 0.0 else None


def backtest_json(result: BacktestResult) -> dict[str, Any]:
    """Der Backtest in der Form von ``GET /api/learning/load`` (Präzisierung 7), ohne ``daily``.
    Die Verbesserung steht in Prozent und fehlt (``None``), wenn das Profil keinen Fehler hat."""
    weeks = [
        {
            "origin": ensure_utc(week.origin).isoformat(),
            "end": ensure_utc(week.end).isoformat(),
            "horizon": week.horizon,
            "n": week.n,
            "model_mae_w": week.model_mae_w,
            "profile_mae_w": week.profile_mae_w,
            "improvement_pct": _improvement_pct(week.model_mae_w, week.profile_mae_w),
        }
        for week in result.weeks
    ]
    slots = sum(week.n for week in result.weeks)
    model = sum(week.n * week.model_mae_w for week in result.weeks) / slots if slots else 0.0
    profile = sum(week.n * week.profile_mae_w for week in result.weeks) / slots if slots else 0.0
    return {
        "weeks": weeks,
        "total": {
            "model_mae_w": model,
            "profile_mae_w": profile,
            "improvement_pct": _improvement_pct(model, profile),
        },
    }


def _entry(row: Row[Any]) -> RegistryEntry:
    return RegistryEntry(
        id=row.id,
        trained_at=row.trained_at,
        data_from=row.data_from,
        data_to=row.data_to,
        samples=row.samples,
        features=tuple(row.features),
        max_w=row.max_w,
        backtest=row.backtest,
        importance=dict(row.importance),
        usable=row.usable,
        active=row.active,
        decided_at=row.decided_at,
        decision=row.decision,
        file=row.file,
    )


class ModelRegistry:
    def __init__(self, engine: Engine, directory: Path) -> None:
        self._engine = engine
        self._directory = directory

    def add(
        self,
        model: TrainedModel,
        *,
        trained_at: datetime,
        data_from: datetime,
        data_to: datetime,
        samples: int,
        backtest: BacktestResult | None,
    ) -> RegistryEntry:
        """Legt eine neue Version an: Zeile und Modelldatei ``load-<id>.txt``.

        Zeile und Datei entstehen in einer Transaktion. Scheitert das Schreiben der Datei, geht die
        Zeile mit zurück, und es bleibt weder ein Eintrag ohne Datei noch eine halbe Datei."""
        self._directory.mkdir(parents=True, exist_ok=True)
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(model_registry).values(
                    kind=KIND,
                    trained_at=ensure_utc(trained_at),
                    data_from=ensure_utc(data_from),
                    data_to=ensure_utc(data_to),
                    samples=samples,
                    features=list(model.features),
                    max_w=model.max_w,
                    file="",  # folgt, sobald die Nummer feststeht
                    backtest=backtest_json(backtest) if backtest is not None else None,
                    importance=model.importance(),
                )
            )
            entry_id = int(result.lastrowid)
            name = f"{KIND}-{entry_id}.txt"
            self._write(name, model.text)
            conn.execute(
                update(model_registry).where(model_registry.c.id == entry_id).values(file=name)
            )
            row = conn.execute(select(model_registry).where(model_registry.c.id == entry_id)).one()
        return _entry(row)

    def _write(self, name: str, text: str) -> None:
        target = self._directory / name
        tmp = target.with_name(target.name + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, target)
        except BaseException:
            tmp.unlink(missing_ok=True)  # das Ziel entsteht erst mit dem letzten Schritt
            raise

    def get(self, entry_id: int) -> RegistryEntry | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(model_registry).where(
                    model_registry.c.id == entry_id, model_registry.c.kind == KIND
                )
            ).first()
        return _entry(row) if row is not None else None

    def latest_usable(self) -> RegistryEntry | None:
        """Die neueste Version, die noch brauchbar ist; ohne eine ``None``."""
        query = (
            select(model_registry)
            .where(model_registry.c.kind == KIND, model_registry.c.usable.is_(True))
            .order_by(model_registry.c.trained_at.desc(), model_registry.c.id.desc())
            .limit(1)
        )
        with self._engine.connect() as conn:
            row = conn.execute(query).first()
        return _entry(row) if row is not None else None

    def load(self, entry: RegistryEntry) -> TrainedModel | None:
        """Liest das Modell der Version. Fehlt die Datei oder ist sie nicht lesbar, wird die
        Version unbrauchbar (``usable = False``), eine Warnung geht ins Log, und das Ergebnis ist
        ``None``; der Aufrufer fällt dann auf das Profil zurück."""
        try:
            text = (self._directory / entry.file).read_text(encoding="utf-8")
            return load_model(text, entry.features, entry.max_w)
        except (OSError, ValueError, TrainingError) as exc:
            log.warning(
                "Lastmodell nicht lesbar, die Version wird nicht mehr verwendet",
                version=entry.id,
                error=str(exc),
            )
            with self._engine.begin() as conn:
                conn.execute(
                    update(model_registry)
                    .where(model_registry.c.id == entry.id)
                    .values(usable=False)
                )
            return None

    def decide(
        self, entry_id: int, decision: Mapping[str, Any], active: bool, at: datetime
    ) -> None:
        """Hält die Aktivierungsentscheidung zu einer Version fest.

        ``decision`` (als JSON) und ``decided_at = at`` stehen danach in dieser Zeile, ``active``
        ebenso. Höchstens eine Version ist aktiv: Bei ``active=True`` werden alle anderen
        inaktiv. Bei ``active=False`` wird nur diese Zeile inaktiv, doch auch dann wird ``active``
        überall gelöscht, denn dann gilt das Profil und keine Version ist in Gebrauch."""
        with self._engine.begin() as conn:
            exists = conn.execute(
                select(model_registry.c.id).where(model_registry.c.id == entry_id)
            ).first()
            if exists is None:
                raise LookupError(f"Keine Modellversion mit der Nummer {entry_id}")
            conn.execute(update(model_registry).values(active=False))
            conn.execute(
                update(model_registry)
                .where(model_registry.c.id == entry_id)
                .values(decision=dict(decision), decided_at=ensure_utc(at), active=active)
            )

    def prune(self, keep: int = KEEP) -> int:
        """Löscht Versionen und Dateien bis auf die ``keep`` neuesten (nach ``trained_at``, bei
        Gleichstand nach Nummer) und die aktive. Gibt die Zahl der gelöschten Zeilen zurück."""
        query = (
            select(model_registry.c.id, model_registry.c.file, model_registry.c.active)
            .where(model_registry.c.kind == KIND)
            .order_by(model_registry.c.trained_at.desc(), model_registry.c.id.desc())
        )
        with self._engine.begin() as conn:
            rows = conn.execute(query).all()
            doomed = [row for row in rows[keep:] if not row.active]
            if doomed:
                conn.execute(
                    delete(model_registry).where(model_registry.c.id.in_([r.id for r in doomed]))
                )
        for row in doomed:
            (self._directory / row.file).unlink(missing_ok=True)
        return len(doomed)
