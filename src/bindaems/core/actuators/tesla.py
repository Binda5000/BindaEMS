"""Tesla-Aktor: Befehle gehen über Tessie im Hintergrund, das Fahrzeug wird nie geweckt.

Die drei Befehle (Plan 2b, Präzisierung 1) sind ``start_charging``, ``stop_charging`` und
``set_charging_amps``, jeweils als ``POST {TESSIE_BASE_URL}/{vin}/command/<Name>`` mit
``wait_for_completion=true``. Tessie antwortet erst, wenn das Fahrzeug den Befehl ausgeführt hat;
das darf insgesamt höchstens ``COMMAND_TIMEOUT_S`` dauern (Spec 6.5). Der Zyklus wartet darauf
nie: ``send`` startet den Befehl als Aufgabe des Event-Loops und kehrt sofort zurück, ``confirm``
fragt, ob sie fertig ist.
Bestätigt ist ein Befehl, wenn Tessie ``{"result": true}`` liefert (Präzisierung 6). Beim Strom
reicht das nicht: Tessie bestätigt einen Befehl auf oder von 5 A, ohne dass das Auto ihn übernimmt,
und erst der zweite wirkt (Prüfprotokoll Teil 2 vom 12.10.2026). Nach der Bestätigung wartet
``confirm`` darum bis ``verify_s`` auf die nächste frische Meldung des Stroms im Tessie-Cache. Zeigt
sie einen anderen Wert, geht der Befehl einmal erneut hinaus, aber nur, solange das Auto steuerbar
ist; die Bestätigung dieser Wiederholung zählt dann ohne weitere Prüfung. Kommt keine frische
Meldung, gilt die Bestätigung von Tessie wie bisher. Laden an und aus prüft ``confirm`` nicht im
Cache.

Tessie weckt das Fahrzeug bei Befehlen selbst. Der Tesla ist deshalb nur steuerbar, wenn der Cache
``online`` meldet und das Fahrzeug zu Hause und angesteckt ist (Spec 3.7, 6.5). Das sagt
``capabilities``; der Ausführer sendet nur, wenn der Aktor steuerbar ist.

``FAILURES_ALLOWED`` Fehler binnen ``FAILURE_WINDOW`` machen den Tesla für ``FAILURE_WINDOW`` nicht
steuerbar (Spec 6.5, 8.4), dazu kommt ein Alarm. Der Token erscheint in keinem Text.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

import httpx
import structlog
from pydantic import SecretStr

from bindaems.core.adapters.tessie import TESSIE_BASE_URL
from bindaems.core.control.actuator import Capabilities, Confirmation, matches
from bindaems.shared.config import VehicleConfig
from bindaems.shared.control import CommandKind, CommandValue, vehicle_actuator
from bindaems.shared.domain import Alarm, Quality, Severity, Snapshot
from bindaems.shared.numbers import de, finite_number
from bindaems.shared.timeutil import LOCAL_TZ

COMMAND_TIMEOUT_S: Final = 60.0  # Frist je Befehl, insgesamt und je httpx-Phase (Spec 6.5)
FAILURES_ALLOWED: Final = 3  # so viele Fehler binnen FAILURE_WINDOW machen ihn nicht steuerbar
FAILURE_WINDOW: Final = timedelta(minutes=15)  # Zählfenster und Dauer des Zustands danach
# So lange wartet ein bestätigter Strom auf eine frische Meldung von Tessie: Beim Laden fragt der
# Adapter alle 30 s ab. Mit Befehl und Wiederholung (je meist unter 3 s) bleibt das in der Frist.
VERIFY_S: Final = 40.0

# Gründe, warum der Tesla nicht steuerbar ist (Plan „Texte“); „nach Fehlern“ hat die Uhrzeit und
# steht in ``_unavailable_text``
REASON_NOT_REACHABLE: Final = "Tessie nicht erreichbar."
REASON_ASLEEP: Final = "Fahrzeug schläft oder ist offline; es wird nicht geweckt."
REASON_AWAY: Final = "Fahrzeug nicht zu Hause."
REASON_UNPLUGGED: Final = "Fahrzeug nicht angesteckt."

# Texte beim Senden und für gescheiterte Befehle
REASON_RUNNING: Final = "Befehl an Tessie läuft noch."
FAILURE_RESULT_FALSE: Final = "Tessie meldet result=false."
FAILURE_CANCELLED: Final = "Befehl an Tessie abgebrochen."
FAILURE_NOT_TAKEN: Final = "Fahrzeug übernahm {wanted} A nicht (Tessie meldet {reported} A)."

_MASK: Final = "***"

log = structlog.get_logger(__name__)


@dataclass
class _Check:
    """Prüfung eines bestätigten Strombefehls im Tessie-Cache.

    ``amps`` ist der gesendete Strom in ganzen Ampere, ``since`` die Zeit des ersten Snapshots, in
    dem ``confirm`` die Bestätigung sah: Erst eine Meldung danach ist frisch. ``started`` ist
    derselbe Moment auf der Uhr des Aktors; ab da nimmt ``send`` für die Art nichts an, bis die
    Prüfung endet, höchstens ``confirm_timeout_s`` lang. ``repeated`` sagt, ob der Befehl schon ein
    zweites Mal hinausging.
    """

    amps: int
    since: datetime | None = None
    started: datetime | None = None
    repeated: bool = False


def _unavailable_text(until: datetime) -> str:
    """„3 Fehlern in 15 min bis 12:17 nicht steuerbar“: der Teil, den Grund und Alarm teilen.

    Die Uhrzeit ist Wiener Zeit.
    """
    minutes = de(FAILURE_WINDOW / timedelta(minutes=1))
    until_text = until.astimezone(LOCAL_TZ).strftime("%H:%M")
    return f"{de(FAILURES_ALLOWED)} Fehlern in {minutes} min bis {until_text} nicht steuerbar"


def _request(kind: CommandKind, value: CommandValue) -> tuple[str, dict[str, str]] | str:
    """Name und Parameter des Tessie-Befehls zu Art und Wert, sonst der Grund, warum es keinen gibt.

    Das Ergebnis ist ein Text, wenn die Art unbekannt ist oder der Wert nicht passt: beim Strom
    keine endliche Zahl, beim Laden kein Wahrheitswert. Den Strom nimmt Tessie in ganzen Ampere.
    """
    if kind == CommandKind.CURRENT:
        amps = finite_number(value)
        if amps is None:
            return f"Ungültiger Wert für „{kind}“."
        return "set_charging_amps", {"amps": str(round(amps))}
    if kind == CommandKind.CHARGING:
        if not isinstance(value, bool):
            return f"Ungültiger Wert für „{kind}“."
        return ("start_charging" if value else "stop_charging"), {}
    return f"Tesla kennt die Befehlsart „{kind}“ nicht."


def _amps_text(value: object) -> str:
    """Ein gemeldeter Strom für Texte: die Zahl mit ``de``, sonst „unbekannt“."""
    number = finite_number(value)
    return "unbekannt" if number is None else de(number)


def _answer_failure(response: httpx.Response) -> str | None:
    """Warum die Antwort von Tessie den Befehl nicht bestätigt; ``None`` bei ``result: true``.

    Eine Antwort ohne gültiges JSON wirft (``ValueError``); der Aufrufer behandelt sie wie jede
    Ausnahme.
    """
    if not response.is_success:
        return f"Tessie antwortet mit HTTP {response.status_code}."
    body = response.json()
    if isinstance(body, dict) and body.get("result") is True:
        return None
    return FAILURE_RESULT_FALSE


class TeslaActuator:
    """Ein Tesla über Tessie als Aktor (Plan 2b, Präzisierung 1, 6 und 12).

    ``key`` ist der Schlüssel des Fahrzeugs in ``vehicles``; seine Signale heißen
    ``vehicle.<key>.…``. ``cfg`` braucht einen ``tessie``-Abschnitt, sonst ``ValueError`` wie beim
    Adapter. ``token`` ist der Tessie-Token, ``client`` der gemeinsame HTTP-Client, ``connected``
    sagt, ob Tessie erreichbar ist. ``clock`` ist die Uhr der Safety-Schicht; nach ihr zählen die
    Fehler und endet die Zeit, in der der Tesla nach Fehlern nicht steuerbar ist. ``send``
    braucht den laufenden Event-Loop.

    Je Art läuft höchstens ein Befehl. Die Aufgabe bleibt in ``_tasks``, auch wenn sie fertig ist:
    So hält der Aktor sie am Leben, und ``confirm`` kann ihr Ergebnis melden, bis das nächste
    ``send`` der Art sie ersetzt. Beim Herunterfahren beendet ``aclose`` die laufenden Befehle,
    bevor der Eigentümer den Client schließt.
    """

    confirm_timeout_s = 75.0  # die 60 s des Befehls und etwas Luft, bis die Aufgabe zu Ende ist
    verify_s = VERIFY_S  # Prüfzeit im Cache nach der Bestätigung; 0 prüft nur schon frische Werte

    def __init__(
        self,
        key: str,
        cfg: VehicleConfig,
        token: SecretStr,
        client: httpx.AsyncClient,
        connected: Callable[[], bool],
        *,
        clock: Callable[[], datetime],
    ) -> None:
        if cfg.tessie is None:
            raise ValueError(f"Fahrzeug {key}: keine Tessie-Konfiguration")
        self.id = vehicle_actuator(key)
        self._key = key
        self._name = cfg.name
        self._vin = cfg.tessie.vin
        self._token = token
        self._client = client
        self._connected = connected
        self._clock = clock
        self._tasks: dict[CommandKind, asyncio.Task[tuple[Confirmation, str]]] = {}
        self._checks: dict[CommandKind, _Check] = {}  # nur für den Strom
        # die Zeiten der letzten Fehler: Ist der älteste von drei jünger als das Fenster,
        # ist der Tesla nicht mehr steuerbar
        self._failures: deque[datetime] = deque(maxlen=FAILURES_ALLOWED)
        self._unavailable_until: datetime | None = None

    def capabilities(self, snap: Snapshot, now: datetime) -> Capabilities:
        """Steuerbar, wenn Tessie erreichbar ist und das Fahrzeug bereit steht; sonst die Gründe.

        Gründe in dieser Reihenfolge: Tessie nicht erreichbar, Fahrzeug schläft, nicht zu Hause,
        nicht angesteckt, nach Fehlern noch nicht wieder steuerbar. Die Signale des Fahrzeugs
        zählen nur mit Qualität ``OK`` und genau dem Wert ``True``; ein veraltetes oder
        fehlendes Signal ist ein Grund.
        """
        prefix = f"vehicle.{self._key}."
        reasons: list[str] = []
        if not self._connected():
            reasons.append(REASON_NOT_REACHABLE)
        if snap.ok(prefix + "online") is not True:
            reasons.append(REASON_ASLEEP)
        if snap.ok(prefix + "at_home") is not True:
            reasons.append(REASON_AWAY)
        if snap.ok(prefix + "plugged") is not True:
            reasons.append(REASON_UNPLUGGED)
        if self._unavailable_until is not None and now < self._unavailable_until:
            reasons.append(f"Nach {_unavailable_text(self._unavailable_until)}.")
        return Capabilities(not reasons, tuple(reasons))

    def present(self, snap: Snapshot) -> dict[CommandKind, CommandValue]:
        """Eingestellter Ladestrom in A und Laden an/aus, wie der Tessie-Cache sie meldet.

        Der Strom ist ``charge_amps`` als endliche Zahl mit Qualität ``OK``. Laden ist ``True`` bei
        dem Ladezustand ``Charging`` und ``False`` bei jedem anderen Text, sofern der Ladezustand
        ein Text mit Qualität ``OK`` ist. Was fehlt, ist unbekannt.
        """
        prefix = f"vehicle.{self._key}."
        present: dict[CommandKind, CommandValue] = {}
        amps = finite_number(snap.ok(prefix + "charge_amps"))
        if amps is not None:
            present[CommandKind.CURRENT] = amps
        state = snap.ok(prefix + "charging_state")
        if isinstance(state, str):
            present[CommandKind.CHARGING] = state == "Charging"
        return present

    def send(self, kind: CommandKind, value: CommandValue) -> str | None:
        """Startet den Befehl an Tessie als Aufgabe und kehrt sofort zurück.

        ``None``, wenn die Aufgabe läuft; sonst der Grund, warum nicht gesendet wurde: Läuft für
        die Art noch ein Befehl oder seine Prüfung im Cache, ist es ``REASON_RUNNING``. Eine
        andere Art, einen Strom, der keine endliche Zahl ist, und ein Laden, das kein
        Wahrheitswert ist, schickt der Aktor nie. Ob das Fahrzeug erreichbar ist, prüft der
        Ausführer mit ``capabilities``.
        """
        request = _request(kind, value)
        if isinstance(request, str):
            return request
        running = self._tasks.get(kind)
        if running is not None and not running.done():
            return REASON_RUNNING
        check = self._checks.get(kind)
        if check is not None and check.started is not None:
            if self._clock() - check.started < timedelta(seconds=self.confirm_timeout_s):
                return REASON_RUNNING
        name, params = request
        self._tasks[kind] = asyncio.get_running_loop().create_task(self._command(name, params))
        if kind == CommandKind.CURRENT:
            self._checks[kind] = _Check(int(params["amps"]))
        return None

    async def _command(self, name: str, params: dict[str, str]) -> tuple[Confirmation, str]:
        """Sendet den Befehl ``name`` an Tessie und wartet auf die Antwort; sie ist das Ergebnis.

        Der ganze Austausch darf höchstens ``COMMAND_TIMEOUT_S`` dauern: Die Frist von httpx gilt
        für jede Phase einzeln (Verbinden, Schreiben, Lesen), die äußere ``asyncio.timeout`` für
        alles zusammen. Bestätigt ist der Befehl bei einer Antwort 2xx mit ``{"result": true}``.
        Jedes andere Ende ist ein Fehler: ein anderer Status, ``result`` nicht ``true`` oder eine
        Ausnahme (Netz, Zeitüberschreitung, kein JSON). Jeder Fehler wird gezählt
        (``_note_failure``).

        Ein Abbruch der Aufgabe von außen, etwa beim Herunterfahren, ist kein Fehler des Befehls
        und zählt nicht. Die äußere Frist macht nur ihren eigenen Abbruch zum ``TimeoutError``,
        der wie jede Ausnahme zählt; ein Abbruch von außen bleibt ein Abbruch.
        """
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT_S):
                response = await self._client.post(
                    f"{TESSIE_BASE_URL}/{self._vin}/command/{name}",
                    params={"wait_for_completion": "true", **params},
                    headers={"Authorization": f"Bearer {self._token.get_secret_value()}"},
                    timeout=COMMAND_TIMEOUT_S,
                )
            failure = _answer_failure(response)
        except Exception as exc:  # Netz, Zeitüberschreitung, kein JSON: der Befehl ist gescheitert
            failure = self._exception_text(exc)
        if failure is None:
            return Confirmation.CONFIRMED, ""
        self._note_failure()
        return Confirmation.FAILED, failure

    def _exception_text(self, exc: Exception) -> str:
        """„Typ: Text“ der Ausnahme, jedes Vorkommen des Tokens durch *** ersetzt.

        Ohne Text der Ausnahme steht nur der Typ (httpx meldet Zeitüberschreitungen so).
        """
        text = str(exc)
        token = self._token.get_secret_value()
        if token:  # ein leerer Token träfe zwischen jedem Zeichen
            text = text.replace(token, _MASK)
        name = type(exc).__name__
        return f"{name}: {text}" if text else name

    def _note_failure(self) -> None:
        """Zählt einen Fehler zur Zeit der Uhr und prüft, ob es drei binnen 15 min sind.

        Dann ist der Tesla von jetzt an 15 min nicht steuerbar. Gezählt wird im gleitenden
        Fenster, ein Erfolg setzt nichts zurück; ein Fehler, der 15 min zurückliegt, zählt nicht
        mehr. Nach der Pause liegen die alten Fehler außerhalb des Fensters, die Zählung beginnt
        neu.
        """
        now = self._clock()
        self._failures.append(now)
        if len(self._failures) == FAILURES_ALLOWED and now - self._failures[0] < FAILURE_WINDOW:
            self._unavailable_until = now + FAILURE_WINDOW

    def confirm(
        self, kind: CommandKind, value: CommandValue, snap: Snapshot
    ) -> tuple[Confirmation, str]:
        """Offen, solange der Befehl der Art läuft; danach sein Ergebnis.

        Das Ergebnis bleibt, bis das nächste ``send`` der Art es ersetzt. Ein abgebrochener Befehl
        ist gescheitert. Wurde für die Art nichts gesendet, bleibt es offen. Bestätigt ist, was
        Tessie bestätigt; beim Strom prüft ``_check`` das zusätzlich im Tessie-Cache.
        """
        task = self._tasks.get(kind)
        if task is None or not task.done():
            return Confirmation.PENDING, ""
        if task.cancelled():
            self._checks.pop(kind, None)
            return Confirmation.FAILED, FAILURE_CANCELLED
        result = task.result()
        check = self._checks.get(kind)
        if check is None:
            return result
        if result[0] is not Confirmation.CONFIRMED or check.repeated:
            del self._checks[kind]
            return result
        return self._check(kind, check, snap)

    def _check(self, kind: CommandKind, check: _Check, snap: Snapshot) -> tuple[Confirmation, str]:
        """Zeigt die nächste frische Meldung den bestätigten Strom? Sonst einmal wiederholen.

        Frisch ist eine Meldung mit Qualität ``OK``, die nach dem ersten Snapshot mit der
        Bestätigung kam. Ohne Signal im Snapshot lässt sich nichts prüfen; dann gilt die
        Bestätigung von Tessie, ebenso, wenn bis ``verify_s`` nach der Bestätigung keine frische
        Meldung kam. Zeigt eine frische Meldung den Strom, ist er bestätigt. Zeigt sie einen
        anderen, geht der Befehl ein zweites Mal hinaus, wenn das Auto steuerbar ist; sonst ist er
        gescheitert, und ``capabilities`` nennt die Gründe. Das Auto wird nie geweckt.
        """
        reading = snap.get(f"vehicle.{self._key}.charge_amps")
        if reading is None:
            del self._checks[kind]
            return Confirmation.CONFIRMED, ""
        if check.since is None:
            check.since, check.started = snap.ts, self._clock()
        if reading.quality is not Quality.OK or reading.ts <= check.since:
            if (snap.ts - check.since).total_seconds() >= self.verify_s:
                del self._checks[kind]
                return Confirmation.CONFIRMED, ""
            return Confirmation.PENDING, ""
        if matches(kind, float(check.amps), finite_number(reading.value)):
            del self._checks[kind]
            return Confirmation.CONFIRMED, ""
        wanted, reported = de(check.amps), _amps_text(reading.value)
        capabilities = self.capabilities(snap, self._clock())
        if not capabilities.controllable:
            del self._checks[kind]
            text = FAILURE_NOT_TAKEN.format(wanted=wanted, reported=reported)
            return Confirmation.FAILED, " ".join((text, *capabilities.reasons))
        log.info(
            "Tesla übernahm den Strom nicht, Befehl wiederholt",
            vehicle=self._name,
            wanted_a=check.amps,
            reported_a=reading.value,
        )
        check.repeated = True
        request = self._command("set_charging_amps", {"amps": str(check.amps)})
        self._tasks[kind] = asyncio.get_running_loop().create_task(request)
        return Confirmation.PENDING, ""

    async def aclose(self) -> None:
        """Bricht laufende Befehle ab und wartet, bis ihre Aufgaben zu Ende sind.

        Für das Herunterfahren: Danach nutzt keine Aufgabe mehr den HTTP-Client, und er darf
        geschlossen werden. Ein abgebrochener Befehl zählt nicht als Fehler; ``confirm`` meldet
        ihn als gescheitert. Fertige Ergebnisse bleiben, wie sie sind.
        """
        running = [task for task in self._tasks.values() if not task.done()]
        for task in running:
            task.cancel()
        if running:
            await asyncio.wait(running)

    def alarms(self, now: datetime) -> list[Alarm]:
        """Der Alarm ``actuator.unavailable.<id>`` (Warnung), solange der Tesla nicht steuerbar ist.

        ``since`` ist der Zeitpunkt des Fehlers, der die Pause ausgelöst hat.
        """
        until = self._unavailable_until
        if until is None or now >= until:
            return []
        message = f"{self._name} ist nach {_unavailable_text(until)}."
        return [
            Alarm(
                f"actuator.unavailable.{self.id}", Severity.WARNING, message, until - FAILURE_WINDOW
            )
        ]
