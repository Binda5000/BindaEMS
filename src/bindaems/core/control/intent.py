"""Absicht der Bedienung (Spec 5.3): Prüfung, Klemmung, Versionierung und Speicherung.

Die app schickt die vollständige Absicht mit einer Versionsnummer. Der ``IntentStore`` prüft sie
gegen den Aktorkatalog, klemmt sie auf das, was ``config.yaml`` erlaubt (live nur für Aktoren mit
``live_allowed``, Spec 8.7), vergleicht die Version mit der gültigen Absicht und speichert eine
angenommene Absicht atomar. Nach einem Neustart gilt die gespeicherte Absicht weiter; der core
durchläuft trotzdem immer OBSERVE und das Gate.

Die Schicht ist rein: Die Zeit kommt als Argument, und die Datei wird nur über
``core.persistence.files`` angefasst.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal

import structlog

from bindaems.core.persistence.files import read_json, write_json_atomic
from bindaems.core.safety.limits import HardLimits
from bindaems.shared.control import ActuatorMode, Intent

log = structlog.get_logger(__name__)

INTENT_FILE_PROBLEM: Final = (
    "Gespeicherte Absicht unlesbar; der core startet ohne Absicht (Regelbetrieb aus)."
)


@dataclass(frozen=True)
class IntentReply:
    """Antwort auf eine gesendete Absicht.

    - ``result``: ``accepted`` (angenommen), ``clamped`` (angenommen, aber geklemmt) oder
      ``rejected`` (abgelehnt)
    - ``version``: die danach gültige Version; ``None`` nur, wenn es keine gültige Absicht gibt
    - ``details``: Hinweise für die Bedienung, je ein deutscher Satz
    - ``rejection``: der Grund einer Ablehnung, sonst ``None``: ``stale`` (Version veraltet oder
      schon mit anderem Inhalt angenommen) oder ``unknown_actuator``
    """

    result: Literal["accepted", "clamped", "rejected"]
    version: int | None
    details: tuple[str, ...] = ()
    rejection: Literal["stale", "unknown_actuator"] | None = None


class IntentStore:
    """Hält die gültige Absicht, prüft neue Absichten und speichert die angenommenen.

    Gültig ist die zuletzt angenommene, also schon geklemmte Absicht oder, nach einem Neustart, die
    geladene. Ohne Absicht läuft der core im Dry-Run und ohne Regelbetrieb.

    Die interne API ruft ``submit`` aus Threads auf, und ``write_json_atomic`` verträgt je Datei nur
    einen Schreiber. ``submit`` und ``load`` laufen deshalb unter einer Sperre. ``current`` und
    ``load_problem`` lesen ohne Sperre: Die Absicht ist unveränderlich und wird nur ersetzt.
    """

    def __init__(self, path: Path, limits: HardLimits) -> None:
        self._path = path
        self._limits = limits
        self._current: Intent | None = None
        self._problem: str | None = None
        self._lock = threading.Lock()

    @property
    def current(self) -> Intent | None:
        """Die gültige Absicht; ``None``, solange es keine gibt."""
        return self._current

    @property
    def load_problem(self) -> str | None:
        """``INTENT_FILE_PROBLEM``, wenn die gespeicherte Absicht beim Laden unlesbar war.

        Eine angenommene Absicht beendet das Problem.
        """
        return self._problem

    def load(self) -> None:
        """Lädt die gespeicherte Absicht; die Datei wird nur gelesen.

        Eine fehlende Datei ist der erste Start: keine Absicht, kein Problem. Eine unlesbare oder
        ungültige Datei ergibt ebenfalls keine Absicht, dazu ``load_problem`` und eine Warnung im
        Log. Die gespeicherte Absicht wird nicht noch einmal geklemmt: Ob ein Aktor live läuft,
        entscheidet zur Laufzeit ``effective_modes`` anhand der aktuellen Konfiguration.
        """
        with self._lock:
            self._current, self._problem = None, None
            try:
                self._current = _parse(read_json(self._path))
            except FileNotFoundError:
                pass  # erster Start: noch keine Absicht
            except (OSError, ValueError) as exc:  # auch ValidationError ist ein ValueError
                log.warning("Gespeicherte Absicht unlesbar", path=str(self._path), error=str(exc))
                self._problem = INTENT_FILE_PROBLEM

    def submit(self, intent: Intent, now: datetime) -> IntentReply:
        """Prüft eine gesendete Absicht und macht sie gültig, wenn sie angenommen wird.

        Die Regeln gelten der Reihe nach, die erste zutreffende entscheidet. Geklemmt wird vor dem
        Vergleich: „Inhalt“ ist die Absicht nach dem Klemmen, ein Aktor mit ``live``, aber ohne
        ``live_allowed`` also schon ``dry_run``.

        1. Unbekannte Aktor-IDs: abgelehnt (``unknown_actuator``) mit einem Hinweis je ID, sortiert.
           Die gültige Version bleibt.
        2. Version kleiner als die gültige: abgelehnt (``stale``).
        3. Version gleich der gültigen, Inhalt gleich: eine Wiederholung. Die Antwort entsteht aus
           der gesendeten Absicht neu und ist deshalb dieselbe wie bei der Annahme, auch wenn die
           Absicht damals geklemmt wurde; die app sieht so statt eines Konflikts einen Erfolg.
           Es wird nichts neu gespeichert.
        4. Version gleich der gültigen, Inhalt anders: abgelehnt (``stale``).
        5. Sonst ist die Absicht angenommen: ``clamped`` mit einem Hinweis je geklemmtem Aktor, in
           der Reihenfolge des Aktorkatalogs, oder ``accepted``. Sie gilt sofort, wird gespeichert
           (``{"intent": …, "accepted_at": ISO-Zeit}``) und beendet ``load_problem``.

        Scheitert das Speichern, gilt die Absicht trotzdem im Speicher. Die Antwort nennt das als
        letzten Hinweis, und der Fehler steht im Log. Eine Wiederholung versucht es nicht erneut;
        die nächste neue Version speichert wieder.
        """
        with self._lock:
            return self._submit(intent, now)

    def _submit(self, intent: Intent, now: datetime) -> IntentReply:
        current = self._current
        valid = current.version if current is not None else None
        unknown = sorted(set(intent.actuators) - self._limits.actuators.keys())
        if unknown:
            details = tuple(f"Unbekannter Aktor „{actuator}“." for actuator in unknown)
            return IntentReply("rejected", valid, details, "unknown_actuator")

        clamped, clamp_details = self._clamp(intent)
        result: Literal["accepted", "clamped"] = "clamped" if clamp_details else "accepted"
        if current is not None and intent.version <= current.version:
            if intent.version < current.version:
                return _older(intent.version, current.version)
            if clamped != current:
                return _other_content(intent.version)
            return IntentReply(result, current.version, clamp_details)

        self._current = clamped
        self._problem = None
        return IntentReply(result, clamped.version, clamp_details + self._save(clamped, now))

    def _clamp(self, intent: Intent) -> tuple[Intent, tuple[str, ...]]:
        """Setzt jeden Aktor mit ``live``, aber ohne ``live_allowed`` auf ``dry_run``.

        Die Hinweise folgen dem Aktorkatalog, nicht der Reihenfolge der gesendeten Absicht.
        """
        actuators = dict(intent.actuators)
        details: list[str] = []
        for actuator, spec in self._limits.actuators.items():
            if actuators.get(actuator) == ActuatorMode.LIVE and not spec.live_allowed:
                actuators[actuator] = ActuatorMode.DRY_RUN
                details.append(_not_live_allowed(spec.label))
        if not details:
            return intent, ()
        return intent.model_copy(update={"actuators": actuators}), tuple(details)

    def _save(self, intent: Intent, now: datetime) -> tuple[str, ...]:
        """Speichert die Absicht atomar; ein Fehler wird geloggt und als Hinweis zurückgegeben."""
        document = {"intent": intent.model_dump(mode="json"), "accepted_at": now.isoformat()}
        try:
            write_json_atomic(self._path, document)
        except OSError as exc:
            log.error("Absicht nicht gespeichert", path=str(self._path), error=str(exc))
            return (f"Absicht nicht gespeichert: {exc}",)
        return ()


def _parse(data: Any) -> Intent:
    """Die Absicht aus ``{"intent": {…}, "accepted_at": ISO-Zeit}``; sonst ein ``ValueError``."""
    if not isinstance(data, dict) or "intent" not in data:
        raise ValueError('erwartet ein Objekt der Form {"intent": {…}, "accepted_at": ISO-Zeit}')
    return Intent.model_validate(data["intent"])


def _not_live_allowed(label: str) -> str:
    return f"{label}: in config.yaml nicht für den Live-Betrieb freigegeben; bleibt im Dry-Run."


def _older(version: int, valid: int) -> IntentReply:
    detail = f"Version {version} ist älter als die gültige Version {valid}."
    return IntentReply("rejected", valid, (detail,), "stale")


def _other_content(version: int) -> IntentReply:
    detail = f"Version {version} ist bereits mit anderem Inhalt angenommen."
    return IntentReply("rejected", version, (detail,), "stale")
