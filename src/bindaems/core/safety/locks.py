"""Sperren für Teilsysteme (Spec 7.9): Ursachen ermitteln, Sperren halten und wieder freigeben.

Ein mitregelndes System oder ein Prüfpunkt der Selbstprüfung, der nicht stimmt oder nicht
prüfbar ist, sperrt das Schreiben auf das betroffene Teilsystem. ``lock_causes`` ordnet die
Alarme und Prüfpunkte den Aktoren zu. Der ``LockManager`` hält die Sperre, solange eine Ursache
besteht, und gibt sie erst frei, wenn die Ursache eine einstellbare Zeit lang weg war.
``controlled_elsewhere`` nennt die Aktoren, deren Sperre ein anderes System belegt; nur sie
verlieren die Werte des EMS (Plan 2b, Präzisierung 3).

Die Schicht ist rein: Die Zeit kommt als Argument. ``core.checks`` wird nicht importiert; die
Prüfpunkte der Selbstprüfung beschreibt das Protokoll ``CheckLike``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final, Protocol

from bindaems.core.safety.limits import HardLimits
from bindaems.shared.control import VICTRON, vehicle_actuator, wallbox_actuator
from bindaems.shared.domain import Alarm

_ALARM_DESS: Final = "competitor.dess"
_ALARM_SCHEDULE: Final = "competitor.schedule."
_ALARM_EVCS_MODE: Final = "competitor.evcs_mode."
_ALARM_VEHICLE_SCHEDULE: Final = "competitor.vehicle_schedule."
_ALARM_FOREIGN: Final = "competitor.foreign."

_CHECK_EVCS_MODE: Final = "evcs_mode."

# Prüfpunkte, die Victron sperren, mit den Status, bei denen sie es tun. Bei ``dess`` und
# ``schedules`` steckt ein ``fail`` schon im Alarm des mitregelnden Systems.
_VICTRON_CHECKS: Final[Mapping[str, frozenset[str]]] = {
    "ess_mode": frozenset({"fail", "unknown"}),
    "batterylife": frozenset({"fail", "unknown"}),
    "min_soc": frozenset({"fail", "unknown"}),
    "dess": frozenset({"unknown"}),
    "schedules": frozenset({"unknown"}),
}


class CheckLike(Protocol):
    """Was ``lock_causes`` von einem Prüfpunkt der Selbstprüfung liest, nur lesbar.

    Ein ``CheckResult`` aus ``core.checks.selfcheck`` passt dazu.
    """

    @property
    def id(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def message(self) -> str: ...


def lock_causes(
    competitors: Sequence[Alarm], selfcheck: Sequence[CheckLike], limits: HardLimits
) -> dict[str, list[str]]:
    """Die Sperrgründe je Aktor aus den Alarmen mitregelnder Systeme und der Selbstprüfung.

    Zuerst kommen die Alarme in ihrer Reihenfolge, dann die Prüfpunkte. Die Gründe eines Aktors
    stehen in dieser Reihenfolge, ein gleicher Text nur einmal. Aktoren ohne Grund fehlen im
    Ergebnis, ebenso alles, was nicht in ``limits.actuators`` steht (etwa der Wall Connector).

    Der Grund ist der Alarmtext bzw. die Meldung des Prüfpunkts. Es sperren:

    - ``victron``: die Alarme ``competitor.dess`` und ``competitor.schedule.<k>``; die Prüfpunkte
      ``ess_mode``, ``batterylife`` und ``min_soc`` bei Status ``fail`` oder ``unknown``; die
      Prüfpunkte ``dess`` und ``schedules`` bei Status ``unknown`` (ein ``fail`` steckt schon im
      Alarm)
    - ``wallbox.<n>``: der Alarm ``competitor.evcs_mode.<n>``; der Prüfpunkt ``evcs_mode.<n>`` bei
      Status ``unknown``
    - ``vehicle.<v>``: der Alarm ``competitor.vehicle_schedule.<v>``
    - jeder Aktor: der Alarm ``competitor.foreign.<aktor>`` (Strom oder Override von außen
      geändert, ``core.control.foreign``) sperrt den Aktor, dessen ID der Rest der Alarm-ID ist

    Alles andere sperrt nichts: ``phases`` und ``fresh_data`` wirken über das Gate des
    Betriebszustands, und Status ``ok`` und ``warn`` sind keine Ursache.
    """
    found = [(_alarm_actuator(alarm.id), alarm.message) for alarm in competitors]
    found += [(_check_actuator(check), check.message) for check in selfcheck]
    causes: dict[str, list[str]] = {}
    for actuator, reason in found:
        if actuator is None or actuator not in limits.actuators:
            continue
        reasons = causes.setdefault(actuator, [])
        if reason not in reasons:
            reasons.append(reason)
    return causes


def controlled_elsewhere(
    competitors: Sequence[Alarm], selfcheck: Sequence[CheckLike], limits: HardLimits
) -> frozenset[str]:
    """Die Aktoren, die nachweislich ein anderes System regelt (7.9; Plan 2b, Präzisierung 3).

    Das sind die Aktoren aus ``lock_causes`` mit einer Ursache, die ein mitregelndes System belegt:
    einem Alarm ``competitor.*`` (auch ``competitor.foreign.*``) oder einem Prüfpunkt mit Status
    ``fail``. Ein Prüfpunkt, der nur nicht lesbar ist (``unknown``), sperrt den Aktor zwar, belegt
    aber kein anderes System: Sein Gerät kann noch Werte des EMS halten.
    """
    found = [_alarm_actuator(alarm.id) for alarm in competitors]
    found += [_check_actuator(check) for check in selfcheck if check.status == "fail"]
    return frozenset(
        actuator for actuator in found if actuator is not None and actuator in limits.actuators
    )


def _alarm_actuator(alarm_id: str) -> str | None:
    """Der Aktor, den der Alarm sperrt; ``None``, wenn er keinen sperrt."""
    if alarm_id == _ALARM_DESS or alarm_id.startswith(_ALARM_SCHEDULE):
        return VICTRON
    if alarm_id.startswith(_ALARM_EVCS_MODE):
        return wallbox_actuator(alarm_id.removeprefix(_ALARM_EVCS_MODE))
    if alarm_id.startswith(_ALARM_VEHICLE_SCHEDULE):
        return vehicle_actuator(alarm_id.removeprefix(_ALARM_VEHICLE_SCHEDULE))
    if alarm_id.startswith(_ALARM_FOREIGN):
        # Der Rest ist die Aktor-ID selbst; ob es sie gibt, prüft ``lock_causes``.
        return alarm_id.removeprefix(_ALARM_FOREIGN)
    return None


def _check_actuator(check: CheckLike) -> str | None:
    """Der Aktor, den der Prüfpunkt mit seinem Status sperrt; ``None``, wenn er keinen sperrt."""
    if check.status in _VICTRON_CHECKS.get(check.id, frozenset()):
        return VICTRON
    if check.id.startswith(_CHECK_EVCS_MODE) and check.status == "unknown":
        return wallbox_actuator(check.id.removeprefix(_CHECK_EVCS_MODE))
    return None


@dataclass(frozen=True)
class LockInfo:
    """Eine Sperre: die letzten Gründe, die erste Sperrzeit und der Beginn der Freigabewartezeit.

    ``cleared_at`` ist ``None``, solange eine Ursache besteht, sonst der Zeitpunkt, zu dem sie zum
    ersten Mal fehlte.
    """

    reasons: tuple[str, ...]
    since: datetime
    cleared_at: datetime | None


class LockManager:
    """Hält die Sperren je Aktor und gibt sie erst frei, wenn die Ursache lange genug fehlte.

    ``update`` meldet in jedem Prüfzyklus die aktuellen Ursachen, ``active`` liefert die
    bestehenden Sperren und gibt dabei die frei, deren Ursache seit ``release_after_s`` Sekunden
    fehlt. Kehrt eine Ursache zurück, beginnt die Wartezeit von vorn und die Sperre behält ihre
    erste Sperrzeit. Das gilt auch, wenn die Wartezeit schon abgelaufen ist, ``active`` die Sperre
    aber noch nicht entfernt hat: Im Zweifel bleibt der Aktor gesperrt.
    """

    def __init__(self, release_after_s: float) -> None:
        self._release_after = timedelta(seconds=release_after_s)
        self._locks: dict[str, LockInfo] = {}

    def update(self, now: datetime, causes: Mapping[str, Sequence[str]]) -> None:
        """Übernimmt die Ursachen je Aktor zum Zeitpunkt ``now``.

        - Ein Aktor mit Ursachen ist gesperrt: ``since`` bleibt die erste Sperrzeit, ``reasons``
          sind die neuen Gründe, ``cleared_at`` ist ``None``.
        - Fehlt bei einem gesperrten Aktor jede Ursache (auch ein Eintrag mit leerer Liste), wird
          beim ersten Mal ``cleared_at = now`` gesetzt. Die Gründe bleiben.
        """
        for actuator, reasons in causes.items():
            if reasons:
                held = self._locks.get(actuator)
                since = now if held is None else held.since
                self._locks[actuator] = LockInfo(tuple(reasons), since, None)
        for actuator, info in list(self._locks.items()):
            if info.cleared_at is None and not causes.get(actuator):
                self._locks[actuator] = replace(info, cleared_at=now)

    def active(self, now: datetime) -> dict[str, LockInfo]:
        """Die bestehenden Sperren zum Zeitpunkt ``now``.

        Eine Sperre, deren Ursache seit mindestens ``release_after_s`` fehlt (``now − cleared_at``),
        wird dabei freigegeben und ist auch für spätere Aufrufe weg; eine neue Ursache beginnt dann
        eine neue Sperre.
        """
        self._locks = {
            actuator: info
            for actuator, info in self._locks.items()
            if info.cleared_at is None or now - info.cleared_at < self._release_after
        }
        return dict(self._locks)
