"""Bausteine der Tesla-Regelung: gleitendes Mittel und Weckkontingent (Design 6.5, 7.1, 7.2).

- ``MovingAverage``: das Mittel des Wunschstroms über ``control.tesla_average_s``. Der Tesla folgt
  ihm statt dem Augenblickswert; wie oft er den Strom ändern darf, begrenzt die Safety-Schicht
  (90-s-Regel).
- ``WakeQuota``: wie dicht und wie oft ein Fahrzeug geweckt werden darf, je Fahrzeug
  (``vehicles.<name>.wake``): mindestens ``interval_s`` Abstand und höchstens ``per_day`` Weckstarts
  je Kalendertag in Wiener Zeit.

Die Schicht ist rein: Die Zeit kommt als Argument. ``now`` ist die Zeit der Safety-Schicht; auf ihr
laufen das Fenster und der Abstand. ``wall_now`` ist die Wanduhr und gibt nur den Kalendertag an.
"""

from __future__ import annotations

import math
from collections import deque
from datetime import date, datetime

from bindaems.shared.numbers import finite_number
from bindaems.shared.timeutil import LOCAL_TZ, ensure_utc


class MovingAverage:
    """Das Mittel der Werte der letzten ``window_s`` Sekunden (Design 6.5).

    ``add`` und ``value`` bekommen die Zeit der Safety-Schicht; sie steigt von Aufruf zu Aufruf.
    Jeder gemerkte Wert zählt gleich, ohne Gewicht nach der Zeit: Die Regelung fügt je Zyklus einen
    hinzu. Das Mittel wird bei jeder Frage aus den Werten im Fenster berechnet und nicht
    mitgeführt, so sammelt es keine Rundungsfehler.
    """

    def __init__(self, window_s: float) -> None:
        self._window_s = window_s
        self._entries: deque[tuple[datetime, float]] = deque()

    def add(self, now: datetime, value: float) -> None:
        """Merkt ``value`` zum Zeitpunkt ``now`` und vergisst, was dann schon zu alt ist.

        Ein Wert, der keine endliche Zahl ist (``finite_number``), wird nicht gemerkt: Ein einziges
        NaN machte sonst das Mittel des ganzen Fensters unbrauchbar.
        """
        entries = self._entries
        while entries and not self._within(entries[0][0], now):
            entries.popleft()
        number = finite_number(value)
        if number is not None:
            entries.append((now, number))

    def value(self, now: datetime) -> float | None:
        """Das Mittel der Werte mit ``now − t ≤ window_s``; ``None``, wenn es keinen gibt.

        Das Lesen ändert nichts: Die Antwort hängt nur von den Werten und von ``now`` ab.
        """
        values = [value for added, value in self._entries if self._within(added, now)]
        return math.fsum(values) / len(values) if values else None

    def clear(self) -> None:
        """Vergisst alle Werte; das Fenster beginnt neu (neuer Ansteckvorgang, Wiederaufnahme)."""
        self._entries.clear()

    def _within(self, added: datetime, now: datetime) -> bool:
        return (now - added).total_seconds() <= self._window_s


class WakeQuota:
    """Das Weckkontingent eines Fahrzeugs: Mindestabstand und Zahl je Kalendertag (Design 7.1).

    ``interval_s`` ist der Mindestabstand zwischen zwei Weckstarts, ``per_day`` die höchste Zahl je
    Kalendertag; mit 0 weckt die Regelung nie.

    Zwei Uhren: Der Abstand läuft auf ``now``, der Zeit der Safety-Schicht, die kein Sprung der
    Wanduhr stört. Der Tag ist ``wall_now.astimezone(LOCAL_TZ).date()``, der Kalendertag in Wiener
    Zeit. Er wechselt um 0 Uhr Wiener Zeit, auch am Tag der Zeitumstellung mit 23 oder 25 Stunden;
    es ist kein gleitendes Fenster von 24 h.

    Der Zähler lebt nur im Speicher. Springt die Wanduhr zurück in einen früheren Tag, beginnt der
    Zähler dieses Tages neu; der Abstand gilt weiter, und die Safety-Schicht begrenzt die Befehle
    je Stunde ohnehin.
    """

    def __init__(self, interval_s: float, per_day: int) -> None:
        self._interval_s = interval_s
        self._per_day = per_day
        self._last: datetime | None = None  # ``now`` des letzten Weckstarts
        self._day: date | None = None  # der Tag, für den ``_count`` gilt
        self._count = 0

    def allowed(self, now: datetime, wall_now: datetime) -> bool:
        """Darf jetzt ein Weckstart folgen? Der Abstand ist erreicht und der Tag nicht voll.

        Ein Abstand von genau ``interval_s`` genügt. Die Frage ändert nichts; erst ``note`` zählt
        einen Weckstart.
        """
        if self._last is not None and (now - self._last).total_seconds() < self._interval_s:
            return False
        return self.today(wall_now) < self._per_day

    def note(self, now: datetime, wall_now: datetime) -> None:
        """Zählt einen Weckstart: der Abstand läuft ab ``now``, der Zähler gilt für den Tag."""
        day = _local_day(wall_now)
        self._count = self._count + 1 if day == self._day else 1
        self._day = day
        self._last = now

    def today(self, wall_now: datetime) -> int:
        """Die Zahl der Weckstarts am Kalendertag (Wiener Zeit) von ``wall_now``.

        An einem anderen Tag als dem des Zählers sind es 0. Ein naiver Zeitpunkt ist ein
        Programmierfehler (``ValueError``).
        """
        return self._count if _local_day(wall_now) == self._day else 0


def _local_day(wall_now: datetime) -> date:
    """Der Kalendertag in Wiener Zeit zur Wanduhr ``wall_now``."""
    return ensure_utc(wall_now).astimezone(LOCAL_TZ).date()
