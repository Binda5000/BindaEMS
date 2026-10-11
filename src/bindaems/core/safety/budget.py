"""Regel 6 der Safety-Schicht: Schreibbudget für persistente Victron-Einstellungen (Spec 8.3).

Die klassischen Victron-Register sind persistente Settings; häufiges Schreiben verschleißt den
Flash. ``PersistentWriteBudget`` hält die Zeitpunkte der Schreibvorgänge in einer JSON-Datei
``{"writes": [ISO-Zeit, …]}``, damit ein Neustart des core das Budget nicht zurücksetzt, und
meldet mit ``check``, wenn das Stunden- oder das Tageslimit erreicht ist.

Die Fenster sind halboffen wie beim Rate-Limiter: Ein Eintrag zum Zeitpunkt ``t`` zählt, solange
``now − t`` kleiner ist als 1 h bzw. 24 h. Die Zeit kommt als Argument; die Datei wird nur über
``core.persistence.files`` angefasst.

Fehlt die Datei, ist das Budget leer (erster Start). Ist sie unlesbar oder nicht im Format, ist
unbekannt, wie viel schon verbraucht ist: Der Flash geht vor, und die persistenten Einstellungen
bleiben ab dem Laden 1 h gesperrt.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import structlog

from bindaems.core.persistence.files import read_json, write_json_atomic
from bindaems.core.safety.types import Finding
from bindaems.shared.domain import Alarm, Severity
from bindaems.shared.timeutil import ensure_utc

log = structlog.get_logger(__name__)

_HOUR: Final = timedelta(hours=1)
_DAY: Final = timedelta(hours=24)
_FILE_BLOCK: Final = timedelta(hours=1)  # Sperre ab dem Laden einer unlesbaren Datei


class PersistentWriteBudget:
    """Höchstens ``per_hour`` persistente Schreibvorgänge je Stunde und ``per_day`` je Tag.

    - ``check`` fragt, ob noch ein Schreibvorgang frei ist; es verbraucht nichts.
    - ``record`` zählt einen Schreibvorgang. Der Aufrufer meldet jeden, auch einen, der das Budget
      umgeht oder nur im Dry-Run geprüft wurde.
    - ``note_rejection`` merkt sich, dass ein Befehl am Budget scheiterte; das löst den Alarm aus.
    - ``alarms`` liefert die Alarme.

    Alle Zeitpunkte sind zeitzonenbewusst (wie ``SafetyContext.now``). Ein Eintrag, der nach ``now``
    liegt, zählt weiter: Läuft die Uhr zurück, ist das Budget eher zu streng als zu lasch.
    """

    def __init__(self, per_hour: int, per_day: int, path: Path, now: datetime) -> None:
        """Lädt die Datei ``path``; ``now`` ist die Ladezeit.

        Eine fehlende Datei ergibt ein leeres Budget. Eine unlesbare Datei, oder eine, die nicht das
        Format ``{"writes": [ISO-Zeit, …]}`` mit Zeitzonen hat, sperrt die persistenten
        Einstellungen bis ``now + 1 h``, ergibt den Alarm ``safety.write_budget_file`` und einen
        Fehler im Log. Das Laden schreibt nichts; die Datei entsteht oder heilt mit dem ersten
        ``record``.
        """
        self._per_hour = per_hour
        self._per_day = per_day
        self._path = path
        self._writes: list[datetime] = []
        self._unreadable_since: datetime | None = None  # Ladezeit, wenn die Datei unlesbar war
        self._rejected_since: datetime | None = None  # erste Ablehnung der laufenden Folge
        try:
            self._writes = _parse_writes(read_json(path))
        except FileNotFoundError:
            pass  # erster Start: leeres Budget
        except (OSError, ValueError) as exc:
            log.error("Datei des Schreibbudgets unlesbar", path=str(path), error=str(exc))
            self._unreadable_since = now

    def check(self, now: datetime) -> Finding | None:
        """Ergibt ``budget.exhausted``, solange gesperrt oder aufgebraucht; sonst ``None``.

        Gesperrt heißt: Die Sperre wegen der unlesbaren Datei läuft. Aufgebraucht heißt:
        ``per_hour`` Schreibvorgänge mit ``now − t < 1 h`` oder ``per_day`` mit ``now − t < 24 h``.
        Ein ``None`` beendet auch eine laufende Folge von Ablehnungen (Alarm
        ``safety.write_budget``).
        """
        if (
            self._file_block_since(now) is not None
            or self._count(now, _HOUR) >= self._per_hour
            or self._count(now, _DAY) >= self._per_day
        ):
            return Finding(
                "budget.exhausted",
                "Schreibbudget für persistente Einstellungen erschöpft "
                f"({self._per_hour}/h, {self._per_day}/Tag).",
            )
        self._rejected_since = None
        return None

    def record(self, now: datetime) -> None:
        """Zählt einen Schreibvorgang zum Zeitpunkt ``now`` und schreibt die Datei atomar.

        Einträge, die zu ``now`` mindestens 24 h zurückliegen, fallen weg. Läuft die Uhr zurück,
        kann am alten Rand ein Eintrag fehlen, den ein früheres ``now`` noch gezählt hätte; er wäre
        um den Rückschritt später ohnehin ausgeschieden.

        Ein naiver Zeitpunkt ist ein Fehler (``ValueError``): Die Datei bekäme sonst Einträge, die
        der nächste Start als unlesbar sperrt. Ein Schreibfehler wird geloggt; der Zähler im
        Speicher gilt weiter, und der nächste erfolgreiche ``record`` bringt die Datei auf den
        Stand.
        """
        moment = ensure_utc(now)
        self._writes = [t for t in self._writes if moment - t < _DAY]
        self._writes.append(moment)
        try:
            write_json_atomic(self._path, {"writes": [t.isoformat() for t in self._writes]})
        except OSError as exc:
            log.error(
                "Datei des Schreibbudgets nicht geschrieben", path=str(self._path), error=str(exc)
            )

    def note_rejection(self, now: datetime) -> None:
        """Merkt sich die Ablehnung eines Befehls wegen des Budgets; die erste beginnt den Alarm."""
        if self._rejected_since is None:
            self._rejected_since = now

    def alarms(self, now: datetime) -> list[Alarm]:
        """Die aktiven Alarme zum Zeitpunkt ``now``, in dieser Reihenfolge:

        - ``safety.write_budget``: ab der ersten ``note_rejection``, bis ``check`` wieder ``None``
          liefert; ``since`` ist die erste Ablehnung.
        - ``safety.write_budget_file``: bis zum Ende der Sperre wegen der unlesbaren Datei;
          ``since`` ist die Ladezeit.
        """
        self.check(now)  # ist wieder Platz, endet damit die Ablehnungsfolge und ihr Alarm
        alarms: list[Alarm] = []
        if self._rejected_since is not None:
            alarms.append(
                Alarm(
                    "safety.write_budget",
                    Severity.WARNING,
                    "Schreibbudget für persistente Einstellungen erschöpft.",
                    self._rejected_since,
                )
            )
        loaded_at = self._file_block_since(now)
        if loaded_at is not None:
            alarms.append(
                Alarm(
                    "safety.write_budget_file",
                    Severity.WARNING,
                    "Datei des Schreibbudgets unlesbar; "
                    "persistente Einstellungen bleiben 1 h gesperrt.",
                    loaded_at,
                )
            )
        return alarms

    def _count(self, now: datetime, window: timedelta) -> int:
        """Zahl der erfassten Schreibvorgänge im Fenster: ``now − t`` kleiner als ``window``."""
        return sum(1 for written in self._writes if now - written < window)

    def _file_block_since(self, now: datetime) -> datetime | None:
        """Die Ladezeit, solange die Sperre wegen der unlesbaren Datei läuft; sonst ``None``."""
        loaded_at = self._unreadable_since
        if loaded_at is not None and now - loaded_at < _FILE_BLOCK:
            return loaded_at
        return None


def _parse_writes(data: Any) -> list[datetime]:
    """Die Zeitpunkte aus ``{"writes": [ISO-Zeit, …]}``; ``ValueError``, wenn die Form abweicht."""
    if not isinstance(data, dict) or data.keys() != {"writes"}:
        raise ValueError('erwartet ein Objekt der Form {"writes": [ISO-Zeit, …]}')
    items = data["writes"]
    if not isinstance(items, list):
        raise ValueError('"writes" ist keine Liste')
    writes: list[datetime] = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError(f"Eintrag ist keine Zeichenkette: {item!r}")
        moment = datetime.fromisoformat(item)  # ValueError, wenn es keine ISO-Zeit ist
        if moment.tzinfo is None:
            raise ValueError(f"Eintrag ohne Zeitzone: {item!r}")
        writes.append(moment)
    return writes
