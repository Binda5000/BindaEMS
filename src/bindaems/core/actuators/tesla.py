"""Tesla-Aktor: Befehle gehen über Tessie im Hintergrund, das Fahrzeug wird nie geweckt.

Die drei Befehle (Plan 2b, Präzisierung 1) sind ``start_charging``, ``stop_charging`` und
``set_charging_amps``, jeweils als ``POST {TESSIE_BASE_URL}/{vin}/command/<Name>`` mit
``wait_for_completion=true``. Tessie antwortet erst, wenn das Fahrzeug den Befehl ausgeführt hat;
das darf insgesamt höchstens ``COMMAND_TIMEOUT_S`` dauern (Spec 6.5). Der Zyklus wartet darauf
nie: ``send`` startet den Befehl als Aufgabe des Event-Loops und kehrt sofort zurück, ``confirm``
fragt, ob sie fertig ist.
Bestätigt ist ein Befehl, wenn Tessie ``{"result": true}`` liefert (Präzisierung 6). Beim Strom
reicht das nicht: Tessie bestätigt einen Befehl auf oder von 5 A, ohne dass das Auto ihn übernimmt,
und erst der zweite wirkt (Prüfprotokoll Teil 2 vom 12.10.2026). Nach der Bestätigung prüft
``confirm`` den Strom darum im Tessie-Cache (``_verify``). Zeigt Tessie einen anderen Wert, ist das
Ergebnis ``NOT_TAKEN``. Der Aktor sendet dann nicht selbst nach: Denselben Befehl schickt wieder der
Aufrufer, in der Laufzeit über die Safety-Schicht als nächsten Versuch für den sicheren Zustand.
Laden an und aus prüft ``confirm`` nicht im Cache.

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
# Prüfung eines bestätigten Stroms im Tessie-Cache: Eine Meldung zählt erst SETTLE_S nach der
# Bestätigung, wenn der Cache das Auto eingeholt hat; bis VERIFY_S wartet die Prüfung auf eine
# solche. Beim Laden fragt der Adapter alle 30 s ab, eine Meldung fällt also immer in das Fenster.
SETTLE_S: Final = 10.0
VERIFY_S: Final = 40.0
CHECK_MARGIN_S: Final = 5.0  # so lange vor der Frist des Ausführers endet die Prüfung spätestens

# Gründe, warum der Tesla nicht steuerbar ist (Plan „Texte“); „nach Fehlern“ hat die Uhrzeit und
# steht in ``_unavailable_text``
REASON_NOT_REACHABLE: Final = "Tessie nicht erreichbar."
REASON_ASLEEP: Final = "Fahrzeug schläft oder ist offline; es wird nicht geweckt."
REASON_AWAY: Final = "Fahrzeug nicht zu Hause."
REASON_UNPLUGGED: Final = "Fahrzeug nicht angesteckt."

# Texte beim Senden und für gescheiterte Befehle
REASON_RUNNING: Final = "Befehl an Tessie läuft noch."
REASON_CHECKING: Final = "Prüfung des Stroms im Tessie-Cache läuft noch."
FAILURE_RESULT_FALSE: Final = "Tessie meldet result=false."
FAILURE_CANCELLED: Final = "Befehl an Tessie abgebrochen."
FAILURE_NOT_TAKEN: Final = "Fahrzeug übernahm {wanted} A nicht (Tessie meldet {reported} A)."

_MASK: Final = "***"


@dataclass
class _Check:
    """Prüfung eines Strombefehls im Tessie-Cache, vom Senden bis zum Ergebnis.

    ``amps`` ist der gesendete Strom in ganzen Ampere, ``sent`` der Zeitpunkt des Sendens auf der
    Uhr des Aktors. ``since`` ist die Zeit des ersten Snapshots, in dem ``confirm`` die Bestätigung
    von Tessie sah, ``started`` derselbe Moment auf der Uhr des Aktors. ``outcome`` ist das
    Ergebnis, sobald es feststeht; es gilt bis zum nächsten ``send`` der Art.
    """

    amps: int
    sent: datetime
    since: datetime | None = None
    started: datetime | None = None
    outcome: tuple[Confirmation, str] | None = None


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
    # Prüfung im Tessie-Cache (``_verify``); ``verify_s`` 0 schaltet sie ab, für Tests ohne Cache
    settle_s = SETTLE_S
    verify_s = VERIFY_S

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
        die Art noch ein Befehl, ist es ``REASON_RUNNING``. Prüft ``confirm`` denselben Strom noch
        im Cache, ist es ``REASON_CHECKING``, höchstens bis ``CHECK_MARGIN_S`` vor der Frist des
        Ausführers; ein anderer Strom ersetzt die Prüfung. Eine andere Art, einen Strom, der keine
        endliche Zahl ist, und ein Laden, das kein Wahrheitswert ist, schickt der Aktor nie. Ob das
        Fahrzeug erreichbar ist, prüft der Ausführer mit ``capabilities``.
        """
        request = _request(kind, value)
        if isinstance(request, str):
            return request
        running = self._tasks.get(kind)
        if running is not None and not running.done():
            return REASON_RUNNING
        name, params = request
        amps = int(params["amps"]) if kind == CommandKind.CURRENT else None
        check = self._checks.get(kind)
        if check is not None and check.amps == amps and self._checking(check):
            return REASON_CHECKING
        self._tasks[kind] = asyncio.get_running_loop().create_task(self._command(name, params))
        if amps is not None:
            self._checks[kind] = _Check(amps, self._clock())
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
        Tessie bestätigt; beim Strom prüft ``_verify`` das zusätzlich im Tessie-Cache.
        """
        task = self._tasks.get(kind)
        if task is None or not task.done():
            return Confirmation.PENDING, ""
        result = (Confirmation.FAILED, FAILURE_CANCELLED) if task.cancelled() else task.result()
        check = self._checks.get(kind)
        if check is None:
            return result
        if check.outcome is None:
            confirmed = result[0] is Confirmation.CONFIRMED
            check.outcome = self._verify(check, snap) if confirmed else result
        return (Confirmation.PENDING, "") if check.outcome is None else check.outcome

    def _verify(self, check: _Check, snap: Snapshot) -> tuple[Confirmation, str] | None:
        """Zeigt Tessie den bestätigten Strom? ``None``, solange die Prüfung wartet.

        Es zählt die erste Meldung des Stroms mit Qualität ``OK`` und einer Zahl, die mindestens
        ``settle_s`` nach dem ersten Snapshot mit der Bestätigung kam: Zeigt sie den Strom, ist er
        bestätigt, sonst hat das Fahrzeug ihn nicht übernommen (``NOT_TAKEN``; kein Fehler von
        Tessie, er zählt nicht). Ohne Signal im Snapshot gilt die Bestätigung von Tessie, ebenso
        ohne solche Meldung bis ``verify_s`` nach der Bestätigung oder bis ``CHECK_MARGIN_S`` vor
        der Frist des Ausführers. Die Wartezeiten laufen auf der Uhr des Aktors, die Meldungen
        vergleicht die Prüfung nach den Zeitstempeln des Speichers.
        """
        reading = snap.get(f"vehicle.{self._key}.charge_amps")
        if reading is None:
            return Confirmation.CONFIRMED, ""
        now = self._clock()
        if check.since is None or check.started is None:
            check.since, check.started = snap.ts, now
        reported = finite_number(reading.value) if reading.quality is Quality.OK else None
        if reported is not None and reading.ts >= check.since + timedelta(seconds=self.settle_s):
            if matches(CommandKind.CURRENT, float(check.amps), reported):
                return Confirmation.CONFIRMED, ""
            text = FAILURE_NOT_TAKEN.format(wanted=de(check.amps), reported=de(reported))
            return Confirmation.NOT_TAKEN, text
        waited = now - check.started
        if waited >= timedelta(seconds=self.verify_s) or now >= self._check_end(check):
            return Confirmation.CONFIRMED, ""
        return None

    def _checking(self, check: _Check) -> bool:
        """Wartet die Prüfung auf eine Meldung von Tessie, und das vor ihrem spätesten Ende?

        Sie beginnt erst, wenn ``confirm`` die Bestätigung von Tessie sah.
        """
        return (
            check.started is not None
            and check.outcome is None
            and self._clock() < self._check_end(check)
        )

    def _check_end(self, check: _Check) -> datetime:
        """Das späteste Ende der Prüfung: ``CHECK_MARGIN_S`` vor der Frist des Ausführers."""
        return check.sent + timedelta(seconds=self.confirm_timeout_s - CHECK_MARGIN_S)

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
