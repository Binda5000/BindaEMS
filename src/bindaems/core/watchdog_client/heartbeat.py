"""Heartbeat an den Totmann-Watchdog am Cerbo (Spec 8.6).

Die Laufzeit ruft ``HeartbeatClient.on_cycle`` einmal je Regelzyklus auf. Der Zähler läuft nur,
solange der core wirklich regelt:

- ``required`` ist der Bedarf: Ein Victron- oder EVCS-Aktor mit ``live_allowed`` ist live gewünscht
  (Regelbetrieb an und Betriebsart live) oder wird gerade übergeben. Ohne Regelbetrieb gibt es
  keinen Bedarf.
- ``allowed`` heißt, dass der Zähler jetzt hinausgehen darf: Der Zyklus war fehlerfrei, und das Gate
  lässt es zu. Fehlt es, bleibt der Zähler aus, und der Watchdog löst nach seinem Timeout aus.

Ohne Bedarf gibt der Client den Watchdog mit einer einzelnen 0 frei, aber nur, wenn
``release_allowed`` gilt (kein Aktor kann noch Werte des EMS halten, und für keinen ist ein
Schreibvorgang offen). Ist der Watchdog später noch „scharf“ und meldet einen Zähler dieses core,
ging die 0 verloren und geht erneut hinaus. Einen ausgelösten Watchdog ohne Bedarf räumt der Client
nach einer Weile mit der 0 auf. Die zuletzt gesendeten Zähler stehen in ``recent``: Nur ein
Watchdog, der einen davon zurückmeldet, ist für diesen core scharf (``armed_for_core``). Zeigt der
Watchdog nach der Freigabe das Echo 0 oder „wartet“ ohne einen eigenen Zähler als Echo, ist sie
bestätigt, und der Client vergisst die Zähler. Den ersten Zähler eines Laufs (``first``) bildet
``heartbeat_start`` aus der Startzeit: Er unterscheidet sich von dem früherer Läufe und von den
Zählern der Probe und des Teil-2-Werkzeugs (1, 2, 3, …). Beim Herunterfahren sendet der Client
nichts: Dann soll der Watchdog auslösen, falls der core nicht rechtzeitig zurückkommt. Die Zeit
kommt als Argument.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Final, Literal

from bindaems.core.watchdog_client.status import WatchdogState

HEARTBEAT_MAX: Final = 2**31 - 1  # größter Wert eines D-Bus-Int32
RELEASE: Final = 0  # Der Zähler läuft von 1 bis HEARTBEAT_MAX, die 0 gibt den Watchdog frei.
# Der Regelzyklus darf um diese Zeit früher dran sein als das Intervall (Takt-Jitter).
INTERVAL_TOLERANCE_S: Final = 0.05
# So viele der zuletzt gesendeten Zähler erkennt der core als seine eigenen.
RECENT_KEPT: Final = 3
# Zeigt der Watchdog so viele Intervalle nach der Freigabe noch „scharf“, geht die 0 erneut hinaus.
RELEASE_RESEND_FACTOR: Final = 2.0
# So lange muss „ausgelöst“ ohne Bedarf anstehen, bis der core den Watchdog mit der 0 aufräumt.
TRIPPED_RELEASE_S: Final = 60.0

# Der Startwert eines Laufs liegt in [START_OFFSET, START_OFFSET + START_SPAN): über den Zählern der
# Werkzeuge (1, 2, 3, …) und weit unter HEARTBEAT_MAX.
START_OFFSET: Final = 1_000_000
START_SPAN: Final = 1_000_000_000

HeartbeatPhase = Literal["off", "active", "released"]


def heartbeat_start(now: datetime) -> int:
    """Der erste Zähler eines Laufs: ``START_OFFSET`` plus die Unix-Sekunden modulo ``START_SPAN``.

    Jeder Lauf beginnt so bei einem anderen Wert als die früheren Läufe und als die Probe und das
    Teil-2-Werkzeug, die bei 1 beginnen und 1, 2, 3, … zählen. Ein Zähler, den ein früherer Lauf
    oder ein Werkzeug im Watchdog hinterließ, gilt dann nicht als Zähler dieses core
    (``armed_for_core``). Der Wert liegt unter 1,001 Milliarden, weit unter ``HEARTBEAT_MAX``.
    """
    return START_OFFSET + int(now.timestamp()) % START_SPAN


class HeartbeatClient:
    """Sendet den Heartbeat-Zähler im Takt ``interval_s`` und die Freigabe 0 am Ende.

    ``send`` stellt einen Wert zu und meldet, ob das geklappt hat. Der Zustand ändert sich erst nach
    einem erfolgreichen Senden; sonst folgt der nächste Versuch im nächsten Zyklus. ``first`` ist
    der erste Zähler und liegt zwischen 1 und ``HEARTBEAT_MAX``, sonst gibt es einen
    ``ValueError``: Die 0 ist die Freigabe, und mehr passt nicht in einen D-Bus-Int32. ``status``:

    - ``"off"``: noch kein Heartbeat gesendet
    - ``"active"``: Heartbeats laufen
    - ``"released"``: der Watchdog ist mit der 0 freigegeben

    Eine wiederholte Freigabe und das Aufräumen nach einer Auslösung ändern den Status nicht.
    """

    def __init__(self, send: Callable[[int], bool], interval_s: float, *, first: int = 1) -> None:
        if not 1 <= first <= HEARTBEAT_MAX:
            raise ValueError(
                f"Der erste Zähler {first} muss zwischen 1 und {HEARTBEAT_MAX} liegen."
            )
        self._send = send
        self._interval_s = interval_s
        self._next = first
        self._status: HeartbeatPhase = "off"
        self._last_sent: datetime | None = None
        # Unveränderlich und beim Senden ersetzt: Die API liest den Stand aus einem anderen Thread.
        self._recent: tuple[int, ...] = ()
        self._zero_sent: datetime | None = None  # die letzte 0, gleich aus welchem Grund
        self._tripped_since: datetime | None = None  # seit wann „ausgelöst“ ohne Bedarf ansteht

    @property
    def status(self) -> HeartbeatPhase:
        return self._status

    @property
    def last_sent(self) -> datetime | None:
        """Zeitpunkt des letzten erfolgreich gesendeten Zählers; nach der Freigabe ``None``."""
        return self._last_sent

    @property
    def recent(self) -> tuple[int, ...]:
        """Die zuletzt gesendeten Zähler (höchstens ``RECENT_KEPT``), der neueste zuletzt.

        Die Freigabe löscht sie nicht, erst ihre Bestätigung (``on_cycle``): Eine verlorene 0
        erkennt der core daran, dass der Watchdog noch einen dieser Zähler zeigt.
        """
        return self._recent

    def on_cycle(
        self,
        now: datetime,
        *,
        required: bool,
        allowed: bool = True,
        release_allowed: bool = True,
        watchdog: WatchdogState = WatchdogState.UNKNOWN,
        echo: int | None = None,
    ) -> None:
        """Sendet den Zähler oder eine 0, je nach Bedarf; höchstens einen Wert je Zyklus.

        ``watchdog`` ist der Zustand des Watchdogs und ``echo`` sein ``/Heartbeat`` (``None``:
        unbekannt), beide so, wie der core sie liest.

        Mit ``required`` gibt es nie eine 0. Der Zähler geht nur bei ``allowed`` hinaus, und zwar,
        wenn er fällig ist: wenn noch nichts gesendet wurde oder seit dem letzten Senden mindestens
        das Intervall abzüglich ``INTERVAL_TOLERANCE_S`` vergangen ist.

        Bestätigt ist die Freigabe, wenn der Status ``"released"`` ist und der Watchdog das Echo 0
        zeigt (``_release_confirmed``) oder „wartet“, ohne einen eigenen Zähler aus ``recent`` als
        Echo zu zeigen. Dann sind die Zähler dieses Laufs vergessen (``recent``). Der Zustand, den
        der Zyklus kennt, in dem die 0 hinausgeht, stammt von davor und bestätigt sie nicht; erst
        der nächste Aufruf tut es.

        Ohne ``required`` sendet der Client eine 0 nur mit ``release_allowed``, und zwar

        - die Freigabe, einmal, solange der Status ``"active"`` ist;
        - die Freigabe erneut, wenn der Status ``"released"`` ist, der Watchdog noch „scharf“ zeigt
          und seit der letzten 0 ``RELEASE_RESEND_FACTOR`` × Intervall vergangen sind. Meldet der
          Watchdog dabei einen fremden Zähler (nicht aus ``recent``), hat ein anderer ihn scharf
          gemacht: Dann bleibt er es. Ein unbekanntes Echo hält die Wiederholung nicht auf;
        - zum Aufräumen, wenn der Watchdog „ausgelöst“ meldet und das seit ``TRIPPED_RELEASE_S``
          ansteht (gezählt ab dem ersten Zyklus, der ihn so sieht), danach höchstens einmal je
          ``TRIPPED_RELEASE_S``.
        """
        if self._status == "released" and self._release_confirmed(watchdog, echo):
            self._recent = ()
        if required:
            self._tripped_since = None
            if allowed and self._due(now) and self._send(self._next):
                self._last_sent = now
                self._status = "active"
                self._recent = (*self._recent, self._next)[-RECENT_KEPT:]
                self._next = self._next + 1 if self._next < HEARTBEAT_MAX else 1
            return
        tripped = release_allowed and watchdog is WatchdogState.TRIPPED
        if not tripped:
            self._tripped_since = None
        elif self._tripped_since is None:
            self._tripped_since = now
        if not release_allowed:
            return
        if self._status == "active":
            if self._send_zero(now):
                self._status = "released"
                self._last_sent = None
        elif self._release_lost(now, watchdog, echo) or self._tripped_long_enough(now):
            self._send_zero(now)

    def _due(self, now: datetime) -> bool:
        if self._last_sent is None:
            return True
        return (now - self._last_sent).total_seconds() >= self._interval_s - INTERVAL_TOLERANCE_S

    def _send_zero(self, now: datetime) -> bool:
        """Sendet die 0 und merkt sich den Zeitpunkt für die Wiederholung und das Aufräumen."""
        if not self._send(RELEASE):
            return False
        self._zero_sent = now
        if self._tripped_since is not None:
            self._tripped_since = now  # die nächste 0 zum Aufräumen erst in TRIPPED_RELEASE_S
        return True

    def _release_confirmed(self, watchdog: WatchdogState, echo: int | None) -> bool:
        """Ob der Watchdog die Freigabe angenommen hat.

        Das Echo ist der zuletzt geschriebene ``/Heartbeat``: Die 0 darin ist eindeutig. „Wartet“
        allein genügt nicht, denn vor dem Scharfschalten zeigt der Watchdog es auch, wenn die 0 nie
        ankam; dann steht noch einer der eigenen Zähler im Echo. Ein unbekanntes Echo oder der
        Zähler eines anderen (Probe, Werkzeug) widerspricht dem „wartet“ nicht.
        """
        if echo == RELEASE:
            return True
        return watchdog is WatchdogState.WAITING and echo not in self._recent

    def _release_lost(self, now: datetime, watchdog: WatchdogState, echo: int | None) -> bool:
        """Ob die Freigabe verloren ging.

        Das gilt, wenn der Client freigegeben hat, der Watchdog aber nach ``RELEASE_RESEND_FACTOR``
        × Intervall noch „scharf“ zeigt, mit einem eigenen Zähler oder ohne bekanntes Echo.
        """
        if self._status != "released" or watchdog is not WatchdogState.ARMED:
            return False
        if echo is not None and echo not in self._recent:
            return False
        zero = self._zero_sent
        return (
            zero is not None
            and (now - zero).total_seconds() >= RELEASE_RESEND_FACTOR * self._interval_s
        )

    def _tripped_long_enough(self, now: datetime) -> bool:
        since = self._tripped_since
        return since is not None and (now - since).total_seconds() >= TRIPPED_RELEASE_S
