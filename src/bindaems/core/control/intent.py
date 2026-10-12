"""Absicht der Bedienung (Spec 5.3): Prüfung, Klemmung, Versionierung und Speicherung.

Die app schickt die vollständige Absicht mit einer Versionsnummer. Der ``IntentStore`` prüft sie
gegen den Aktorkatalog und gegen die Fahrzeuge und Wallboxen aus ``config.yaml`` (Plan 2c-1,
Präzisierung 2), klemmt sie auf das, was erlaubt ist (live nur für Aktoren mit ``live_allowed``,
Spec 8.7; ein Boost höchstens ``BOOST_MAX`` nach dem Empfang), vergleicht die Version mit der
gültigen Absicht und speichert eine angenommene Absicht atomar. Nach einem Neustart gilt die
gespeicherte Absicht weiter; der core durchläuft trotzdem immer OBSERVE und das Gate.

Die Schicht ist rein: Die Zeit kommt als Argument, und die Datei wird nur über
``core.persistence.files`` angefasst.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

import structlog

from bindaems.core.persistence.files import read_json, write_json_atomic
from bindaems.core.safety.limits import HardLimits
from bindaems.shared.control import ActuatorMode, Intent
from bindaems.shared.timeutil import LOCAL_TZ, ensure_utc

log = structlog.get_logger(__name__)

INTENT_FILE_PROBLEM: Final = (
    "Gespeicherte Absicht unlesbar; der core startet ohne Absicht (Regelbetrieb aus)."
)

# So weit nach dem Empfang der Absicht darf ein ``boost_until`` höchstens liegen (Präzisierung 2).
BOOST_MAX: Final = timedelta(hours=12)


@dataclass(frozen=True)
class IntentReply:
    """Antwort auf eine gesendete Absicht.

    - ``result``: ``accepted`` (angenommen), ``clamped`` (angenommen, aber geklemmt) oder
      ``rejected`` (abgelehnt)
    - ``version``: die danach gültige Version; ``None`` nur, wenn es keine gültige Absicht gibt
    - ``details``: Hinweise für die Bedienung, je ein deutscher Satz
    - ``rejection``: der Grund einer Ablehnung, sonst ``None``: ``stale`` (Version veraltet oder
      schon mit anderem Inhalt angenommen), ``unknown_actuator`` oder ``invalid`` (unbekanntes
      Fahrzeug oder unbekannte Wallbox, eine Wallbox bei mehreren Fahrzeugen, eine Priorität, die
      nicht jedes Fahrzeug genau einmal nennt)
    """

    result: Literal["accepted", "clamped", "rejected"]
    version: int | None
    details: tuple[str, ...] = ()
    rejection: Literal["stale", "unknown_actuator", "invalid"] | None = None


class IntentStore:
    """Hält die gültige Absicht, prüft neue Absichten und speichert die angenommenen.

    Gültig ist die zuletzt angenommene, also schon geklemmte Absicht oder, nach einem Neustart, die
    geladene. Ohne Absicht läuft der core im Dry-Run und ohne Regelbetrieb.

    ``vehicles`` bildet die Schlüssel der Fahrzeuge in ``config.yaml`` auf ihren Namen ab, in der
    Reihenfolge der Datei; ``wallboxes`` nennt die Schlüssel der Wallboxen. Was dort fehlt, ist für
    die Absicht unbekannt.

    Die interne API ruft ``submit`` aus Threads auf, und ``write_json_atomic`` verträgt je Datei nur
    einen Schreiber. ``submit`` und ``load`` laufen deshalb unter einer Sperre. ``current``,
    ``accepted_at`` und ``load_problem`` lesen ohne Sperre: Die Absicht ist unveränderlich und wird
    nur ersetzt, und eine neue Absicht setzt zuerst ihre Annahmezeit, dann sich selbst. Wer beide
    liest, liest erst ``current``; die Zeit ist dann nie älter als die Annahme dieser Absicht.
    """

    def __init__(
        self,
        path: Path,
        limits: HardLimits,
        *,
        vehicles: Mapping[str, str] | None = None,
        wallboxes: Collection[str] = (),
    ) -> None:
        self._path = path
        self._limits = limits
        self._vehicles = dict(vehicles or {})
        self._wallboxes = frozenset(wallboxes)
        self._current: Intent | None = None
        self._accepted_at: datetime | None = None
        self._problem: str | None = None
        self._lock = threading.Lock()

    @property
    def current(self) -> Intent | None:
        """Die gültige Absicht; ``None``, solange es keine gibt."""
        return self._current

    @property
    def accepted_at(self) -> datetime | None:
        """Annahmezeit der gültigen Absicht (UTC); ``None``, wenn es keine Absicht gibt.

        Nach einem Neustart steht sie in der Datei; fehlt sie dort oder ist sie ungültig, ist sie
        ebenfalls ``None``.
        """
        return self._accepted_at

    @property
    def load_problem(self) -> str | None:
        """``INTENT_FILE_PROBLEM``, wenn die gespeicherte Absicht beim Laden unlesbar war.

        Eine angenommene Absicht beendet das Problem.
        """
        return self._problem

    def load(self) -> None:
        """Lädt die gespeicherte Absicht samt Annahmezeit; die Datei wird nur gelesen.

        Eine fehlende Datei ist der erste Start: keine Absicht, kein Problem. Eine unlesbare oder
        ungültige Datei ergibt ebenfalls keine Absicht, dazu ``load_problem`` und eine Warnung im
        Log. Die gespeicherte Absicht wird nicht noch einmal geklemmt: Ob ein Aktor live läuft,
        entscheidet zur Laufzeit ``effective_modes`` anhand der aktuellen Konfiguration. Eine
        Absicht aus Plan 2b ohne Lademodi lädt mit den Standardwerten. Fehlt die Annahmezeit oder
        ist sie ungültig, ist ``accepted_at`` ``None``; die Absicht bleibt gültig.
        """
        with self._lock:
            self._accepted_at, self._current, self._problem = None, None, None
            try:
                intent, accepted_at = _parse(read_json(self._path))
                self._accepted_at, self._current = accepted_at, intent
            except FileNotFoundError:
                pass  # erster Start: noch keine Absicht
            except (OSError, ValueError) as exc:  # auch ValidationError ist ein ValueError
                log.warning("Gespeicherte Absicht unlesbar", path=str(self._path), error=str(exc))
                self._problem = INTENT_FILE_PROBLEM

    def submit(self, intent: Intent, now: datetime) -> IntentReply:
        """Prüft eine gesendete Absicht und macht sie gültig, wenn sie angenommen wird.

        Die Regeln gelten der Reihe nach, die erste zutreffende entscheidet. Geklemmt wird vor dem
        Vergleich: „Inhalt“ ist die Absicht nach dem Klemmen, ein Aktor mit ``live``, aber ohne
        ``live_allowed`` also schon ``dry_run`` und ein Boost über ``BOOST_MAX`` hinaus schon auf
        dessen Ende gesetzt. Den Boost klemmt der Empfang (Präzisierung 2): bei einer neuen Version
        ist das ``now``, bei der gültigen Version ihre Annahmezeit (``accepted_at``, ohne sie
        ``now``). So bleibt auch eine spätere Wiederholung, selbst nach einem Neustart, eine
        Wiederholung. Ein ``boost_until`` in der Vergangenheit ist kein Fehler: Es gibt dann keinen
        Boost.

        1. Unbekannte Aktor-IDs: abgelehnt (``unknown_actuator``) mit einem Hinweis je ID, sortiert.
           Die gültige Version bleibt.
        2. Ungültiger Inhalt: abgelehnt (``invalid``), die gültige Version bleibt. Je Fund ein
           Hinweis, in dieser Reihenfolge: unbekannte Fahrzeuge (sortiert), unbekannte Wallboxen
           (nach Fahrzeug sortiert), Wallboxen mehrerer Fahrzeuge (sortiert) und eine Priorität, die
           nicht jedes Fahrzeug genau einmal nennt.
        3. Version kleiner als die gültige: abgelehnt (``stale``).
        4. Version gleich der gültigen, Inhalt gleich: eine Wiederholung. Die Antwort entsteht aus
           der gesendeten Absicht neu und ist deshalb dieselbe wie bei der Annahme, auch wenn die
           Absicht damals geklemmt wurde; die app sieht so statt eines Konflikts einen Erfolg.
           Es wird nichts neu gespeichert.
        5. Version gleich der gültigen, Inhalt anders: abgelehnt (``stale``).
        6. Sonst ist die Absicht angenommen: ``clamped`` mit einem Hinweis je geklemmtem Aktor, in
           der Reihenfolge des Aktorkatalogs, danach je geklemmtem Boost, in der Reihenfolge der
           Fahrzeuge in ``config.yaml``, oder ``accepted``. Sie gilt sofort, wird gespeichert
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

        invalid = self._invalid(intent)
        if invalid:
            return IntentReply("rejected", valid, invalid, "invalid")

        # Empfang: bei einer neuen Version jetzt, bei der gültigen ihre Annahme (ohne sie jetzt)
        repeated = current is not None and intent.version == current.version
        received = self._accepted_at if repeated and self._accepted_at is not None else now
        clamped, clamp_details = self._clamp(intent, received)
        result: Literal["accepted", "clamped"] = "clamped" if clamp_details else "accepted"
        if current is not None and intent.version <= current.version:
            if intent.version < current.version:
                return _older(intent.version, current.version)
            if clamped != current:
                return _other_content(intent.version)
            return IntentReply(result, current.version, clamp_details)

        self._accepted_at = now  # zuerst die Zeit, dann die Absicht (siehe Klassendokumentation)
        self._current = clamped
        self._problem = None
        return IntentReply(result, clamped.version, clamp_details + self._save(clamped, now))

    def _invalid(self, intent: Intent) -> tuple[str, ...]:
        """Hinweise zu Fahrzeugen, Wallboxen und Priorität, die nicht zur Konfiguration passen.

        Die Reihenfolge ist: unbekannte Fahrzeuge (sortiert), unbekannte Wallboxen (nach Fahrzeug
        sortiert), Wallboxen mehrerer Fahrzeuge (sortiert), Priorität. Eine passende Absicht ergibt
        ein leeres Tupel.
        """
        details = [
            f"Unbekanntes Fahrzeug „{vehicle}“."
            for vehicle in sorted(intent.vehicles.keys() - self._vehicles.keys())
        ]
        wallbox_of = {
            vehicle: entry.wallbox
            for vehicle, entry in intent.vehicles.items()
            if entry.wallbox is not None
        }
        details += [
            f"Unbekannte Wallbox „{wallbox_of[vehicle]}“ für „{vehicle}“."
            for vehicle in sorted(wallbox_of)
            if wallbox_of[vehicle] not in self._wallboxes
        ]
        users = Counter(wallbox_of.values())
        details += [
            f"Wallbox „{wallbox}“ ist mehreren Fahrzeugen zugeordnet."
            for wallbox in sorted(users)
            if users[wallbox] > 1
        ]
        if intent.priority is not None and sorted(intent.priority) != sorted(self._vehicles):
            details.append("Die Priorität muss jedes Fahrzeug genau einmal nennen.")
        return tuple(details)

    def _clamp(self, intent: Intent, received: datetime) -> tuple[Intent, tuple[str, ...]]:
        """Klemmt die Absicht auf das, was ``config.yaml`` und ``BOOST_MAX`` erlauben.

        - Jeder Aktor mit ``live``, aber ohne ``live_allowed``, wird ``dry_run``.
        - Ein ``boost_until`` später als ``received + BOOST_MAX`` wird auf diesen Zeitpunkt gesetzt.

        Die Hinweise nennen erst die Aktoren, in der Reihenfolge des Aktorkatalogs, dann die
        Fahrzeuge, in der Reihenfolge von ``config.yaml``; nicht die der gesendeten Absicht.
        """
        actuators = dict(intent.actuators)
        details: list[str] = []
        for actuator, spec in self._limits.actuators.items():
            if actuators.get(actuator) == ActuatorMode.LIVE and not spec.live_allowed:
                actuators[actuator] = ActuatorMode.DRY_RUN
                details.append(_not_live_allowed(spec.label))
        latest = received + BOOST_MAX
        vehicles = dict(intent.vehicles)
        for vehicle, name in self._vehicles.items():
            entry = vehicles.get(vehicle)
            if entry is not None and entry.boost_until is not None and entry.boost_until > latest:
                vehicles[vehicle] = entry.model_copy(update={"boost_until": latest})
                details.append(_boost_clamped(name, latest))
        if not details:
            return intent, ()
        update = {"actuators": actuators, "vehicles": vehicles}
        return intent.model_copy(update=update), tuple(details)

    def _save(self, intent: Intent, now: datetime) -> tuple[str, ...]:
        """Speichert die Absicht atomar; ein Fehler wird geloggt und als Hinweis zurückgegeben."""
        document = {"intent": intent.model_dump(mode="json"), "accepted_at": now.isoformat()}
        try:
            write_json_atomic(self._path, document)
        except OSError as exc:
            log.error("Absicht nicht gespeichert", path=str(self._path), error=str(exc))
            return (f"Absicht nicht gespeichert: {exc}",)
        return ()


def _parse(data: Any) -> tuple[Intent, datetime | None]:
    """Absicht und Annahmezeit aus ``{"intent": {…}, "accepted_at": ISO-Zeit}``.

    Ohne ``intent`` oder mit ungültiger Absicht ein ``ValueError``. Eine fehlende oder ungültige
    Annahmezeit macht die Datei nicht unlesbar; sie ist dann ``None``.
    """
    if not isinstance(data, dict) or "intent" not in data:
        raise ValueError('erwartet ein Objekt der Form {"intent": {…}, "accepted_at": ISO-Zeit}')
    return Intent.model_validate(data["intent"]), _parse_time(data.get("accepted_at"))


def _parse_time(value: Any) -> datetime | None:
    """Die ISO-Zeit mit Zeitzone als UTC; alles andere (kein Text, keine Zeitzone) ist ``None``."""
    if not isinstance(value, str):
        return None
    try:
        return ensure_utc(datetime.fromisoformat(value))
    except (ValueError, OverflowError):  # Überlauf beim Umrechnen nach UTC
        return None


def _not_live_allowed(label: str) -> str:
    return f"{label}: in config.yaml nicht für den Live-Betrieb freigegeben; bleibt im Dry-Run."


def _boost_clamped(name: str, until: datetime) -> str:
    return f"{name}: Boost auf 12 h begrenzt, bis {until.astimezone(LOCAL_TZ):%H:%M}."


def _older(version: int, valid: int) -> IntentReply:
    detail = f"Version {version} ist älter als die gültige Version {valid}."
    return IntentReply("rejected", valid, (detail,), "stale")


def _other_content(version: int) -> IntentReply:
    detail = f"Version {version} ist bereits mit anderem Inhalt angenommen."
    return IntentReply("rejected", version, (detail,), "stale")
