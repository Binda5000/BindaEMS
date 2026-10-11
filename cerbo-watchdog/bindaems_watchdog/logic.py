"""Zustandsautomat des Watchdogs (Spec 8.6).

Rein und ohne eigene Uhr: Alle Uhrzeiten sind monotone Sekunden, die der Aufrufer übergibt. Mit
einer monotonen Uhr (``time.monotonic``) können weder ein Sprung der Systemzeit (NTP, Zeitzone)
noch die Zeit seit dem Start den Watchdog fälschlich auslösen oder aufhalten.

Zustände:

- ``WAITING``: wartet auf einen stabilen Heartbeat; greift nie ein
- ``ARMED``: scharf; löst aus, wenn der Heartbeat ``timeout_s`` lang ausbleibt
- ``TRIPPED``: hat ausgelöst; wird wieder scharf, sobald der Heartbeat ``rearm_s`` lang stabil
  läuft
- ``DISABLED``: abgeschaltet; ignoriert alles
"""

from __future__ import annotations

from typing import Optional

from bindaems_watchdog.config import Settings

WAITING, ARMED, TRIPPED, DISABLED = 0, 1, 2, 3


class WatchdogLogic:
    """Die Zustände und Übergänge des Watchdogs.

    Ein Heartbeat mit einem Wert über 0 ist ein Lebenszeichen des EMS, die 0 gibt den Watchdog
    frei. Ein Lauf ist eine Folge von Heartbeats, deren Abstand höchstens ``gap_s`` beträgt; er
    macht den Watchdog scharf, sobald er ``rearm_s`` gedauert hat.
    """

    def __init__(self, settings: Settings, armed_at_start: bool, now: float) -> None:
        self._settings = settings
        self._last_seen = now
        self._run_start: Optional[float] = None
        self.state: int = WAITING
        if not settings.enabled:
            self.state = DISABLED
        elif armed_at_start:
            self.state = ARMED  # Das EMS hat ab jetzt ``timeout_s``, sich zu melden.

    def heartbeat(self, value: int, now: float) -> None:
        """Verarbeitet einen empfangenen Heartbeat-Wert.

        Ein Wert über 0 ist ein Lebenszeichen. Die 0 ist die Freigabe: Der Watchdog wartet dann
        wieder und zählt einen neuen Lauf, ohne auszulösen. Negative Werte ignoriert er.
        """
        if value > 0:
            if self._run_start is None or now - self._last_seen > self._settings.gap_s:
                self._run_start = now
            self._last_seen = now
        elif value == 0:
            if self.state in (ARMED, TRIPPED):
                self.state = WAITING
            self._run_start = None
            self._last_seen = now

    def tick(self, now: float) -> bool:
        """Prüft die Zeiten; ``True`` heißt: jetzt auslösen (einmal je Auslösung)."""
        if self.state == ARMED:
            if now - self._last_seen > self._settings.timeout_s:
                self.state = TRIPPED
                self._run_start = None
                return True
        elif self.state in (WAITING, TRIPPED):
            if now - self._last_seen > self._settings.gap_s:
                self._run_start = None  # Der Lauf ist abgerissen.
            elif self._run_start is not None and now - self._run_start >= self._settings.rearm_s:
                self.state = ARMED
        return False
