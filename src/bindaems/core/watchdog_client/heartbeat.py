"""Heartbeat an den Totmann-Watchdog am Cerbo (Spec 8.6).

Die Laufzeit ruft ``HeartbeatClient.on_cycle`` einmal je Regelzyklus auf. Der Client sendet nur,
solange ein Victron- oder EVCS-Aktor live angefordert ist (``required``), und gibt den Watchdog mit
einer einzelnen 0 frei, sobald das endet. Beim Herunterfahren sendet er nichts: Dann soll der
Watchdog auslösen, falls der core nicht rechtzeitig zurückkommt. Die Zeit kommt als Argument.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Final, Literal

HEARTBEAT_MAX: Final = 2**31 - 1  # größter Wert eines D-Bus-Int32
RELEASE: Final = 0  # Der Zähler läuft von 1 bis HEARTBEAT_MAX, die 0 gibt den Watchdog frei.
# Der Regelzyklus darf um diese Zeit früher dran sein als das Intervall (Takt-Jitter).
INTERVAL_TOLERANCE_S: Final = 0.05

HeartbeatPhase = Literal["off", "active", "released"]


class HeartbeatClient:
    """Sendet den Heartbeat-Zähler im Takt ``interval_s`` und die Freigabe 0 am Ende.

    ``send`` stellt einen Wert zu und meldet, ob das geklappt hat. Der Zustand ändert sich erst nach
    einem erfolgreichen Senden; sonst folgt der nächste Versuch im nächsten Zyklus. ``status``:

    - ``"off"``: noch kein Heartbeat gesendet
    - ``"active"``: Heartbeats laufen
    - ``"released"``: der Watchdog ist mit der 0 freigegeben
    """

    def __init__(self, send: Callable[[int], bool], interval_s: float, *, first: int = 1) -> None:
        self._send = send
        self._interval_s = interval_s
        self._next = first
        self._status: HeartbeatPhase = "off"
        self._last_sent: datetime | None = None

    @property
    def status(self) -> HeartbeatPhase:
        return self._status

    @property
    def last_sent(self) -> datetime | None:
        """Zeitpunkt des letzten erfolgreich gesendeten Zählers; nach der Freigabe ``None``."""
        return self._last_sent

    def on_cycle(self, now: datetime, *, required: bool) -> None:
        """Sendet bei ``required`` den Zähler, wenn er fällig ist, sonst einmal die Freigabe.

        Fällig ist der Zähler, wenn noch nichts gesendet wurde oder seit dem letzten Senden
        mindestens das Intervall abzüglich ``INTERVAL_TOLERANCE_S`` vergangen ist. Die Freigabe
        folgt nur auf aktive Heartbeats, einmal.
        """
        if required:
            if self._due(now) and self._send(self._next):
                self._last_sent = now
                self._status = "active"
                self._next = self._next + 1 if self._next < HEARTBEAT_MAX else 1
        elif self._status == "active" and self._send(RELEASE):
            self._status = "released"
            self._last_sent = None

    def _due(self, now: datetime) -> bool:
        if self._last_sent is None:
            return True
        return (now - self._last_sent).total_seconds() >= self._interval_s - INTERVAL_TOLERANCE_S
