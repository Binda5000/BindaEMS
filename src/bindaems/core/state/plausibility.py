"""Plausibilitätsprüfung der Messwerte; liefert Alarme, greift aber nicht ein."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

from bindaems.core.state.derived import PHASES, Derived, number
from bindaems.shared.domain import Alarm, Severity, Snapshot

VOLTAGE_RANGE_V = (180.0, 270.0)
MAX_GRID_PHASE_W = 30_000.0
BALANCE_MIN_LIMIT_W = 500.0
BALANCE_RELATIVE_LIMIT = 0.15


class PlausibilityMonitor:
    """Prüft Grenzwerte sofort und die Energiebilanz als gleitenden Mittelwert über ``window_s``.

    ``Alarm.since`` bleibt der Zeitpunkt des ersten Auftretens, solange der Alarm ansteht.
    """

    def __init__(self, window_s: float = 60.0) -> None:
        self._window = timedelta(seconds=window_s)
        self._residuals: deque[tuple[datetime, float]] = deque()
        self._since: dict[str, datetime] = {}

    def evaluate(self, snap: Snapshot, derived: Derived, now: datetime) -> list[Alarm]:
        findings: dict[str, str] = {}
        low, high = VOLTAGE_RANGE_V
        for phase in PHASES:
            n = phase[1]
            volts = number(snap, f"grid.l{n}.voltage_v")
            if volts is not None and not low <= volts <= high:
                findings[f"plaus.voltage.{phase}"] = (
                    f"Netzspannung {phase} unplausibel: {volts:.1f} V"
                )
            power = number(snap, f"grid.l{n}.power_w")
            if power is not None and abs(power) > MAX_GRID_PHASE_W:
                findings[f"plaus.grid_power.{phase}"] = (
                    f"Netzleistung {phase} unplausibel: {power:.0f} W"
                )
        soc = number(snap, "battery.soc_pct")
        if soc is not None and not 0.0 <= soc <= 100.0:
            findings["plaus.soc"] = f"Akku-SOC unplausibel: {soc:.1f} %"
        balance = self._balance(snap, derived, now)
        if balance is not None:
            findings["plaus.balance"] = balance

        for alarm_id in set(self._since) - set(findings):
            del self._since[alarm_id]
        return [
            Alarm(alarm_id, Severity.WARNING, message, self._since.setdefault(alarm_id, now))
            for alarm_id, message in findings.items()
        ]

    def _balance(self, snap: Snapshot, derived: Derived, now: datetime) -> str | None:
        residual = derived.balance_residual_w
        if residual is None:
            self._residuals.clear()  # Lücke: das Fenster füllt sich neu
            return None
        self._residuals.append((now, abs(residual)))
        while now - self._residuals[0][0] > self._window:
            self._residuals.popleft()
        if now - self._residuals[0][0] < self._window:
            return None  # Fenster noch nicht vollständig gefüllt
        mean = sum(value for _, value in self._residuals) / len(self._residuals)
        battery = abs(number(snap, "battery.power_w") or 0.0)
        consumption = derived.consumption_w or 0.0
        limit = max(BALANCE_MIN_LIMIT_W, BALANCE_RELATIVE_LIMIT * max(consumption, battery))
        if mean <= limit:
            return None
        return f"Energiebilanz unplausibel: Abweichung {mean:.0f} W (Grenze {limit:.0f} W)"
