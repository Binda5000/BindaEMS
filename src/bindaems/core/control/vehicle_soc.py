"""Ladestand je Fahrzeug: gemessen, aus dem Zähler der Wallbox geschätzt oder angenommen.

- ``SocReading``: ein Ladestand und seine Quelle
- ``SocTracker``: führt je Fahrzeug den Anker der Schätzung und liefert den Ladestand eines Zyklus
- ``SOC_ANCHOR_BEFORE``: so lange vor dem Anstecken darf eine Messung zurückliegen

Gemessen ist ein Wert mit Qualität ``OK``; für Tessie heißt das auch nicht älter als dessen
Frische (das prüft schon die Qualität des Signals). Ein Anker ist eine Messung mit dem Stand des
Zählers der Wallbox in dem Moment, in dem sie kam. Fehlt eine neue Messung, schätzt der Tracker:
Anker plus die Energie, die der Zähler seither gezählt hat, mal Wirkungsgrad, geteilt durch die
nutzbare Kapazität. Ohne brauchbaren Anker ist der Ladestand angenommen (0 %): Unbekannt bleibt
unbekannt, er heißt nie „gemessen“. Die Schicht ist rein: Sie liest nur ihre Argumente.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final, Literal

SOC_ANCHOR_BEFORE: Final = timedelta(minutes=60)  # Messung höchstens so lange vor dem Anstecken
ASSUMED_PCT: Final = 0.0  # angenommener Ladestand ohne Messung und ohne Schätzung

SocSource = Literal["measured", "estimated", "assumed"]


@dataclass(frozen=True)
class SocReading:
    """Ein Ladestand in % und woher er kommt.

    - ``measured``: gemessen (Tessie oder Home Assistant)
    - ``estimated``: aus dem letzten Messwert und dem Zähler der Wallbox geschätzt
    - ``assumed``: weder gemessen noch schätzbar; ``pct`` ist dann ``ASSUMED_PCT``
    """

    pct: float | None
    source: SocSource


_ASSUMED: Final = SocReading(ASSUMED_PCT, "assumed")


@dataclass(frozen=True)
class _Anchor:
    """Eine Messung: Ladestand, Zählerstand der Wallbox, Zeitpunkt der Messung.

    ``session`` ist der Ansteckvorgang, in dem der Zählerstand gelesen wurde (``None``: keiner).
    """

    pct: float
    counter_kwh: float | None
    at: datetime
    session: datetime | None


class SocTracker:
    """Der Ladestand je Fahrzeug, gemessen oder geschätzt (Präzisierung 5 von Plan 4b)."""

    def __init__(self) -> None:
        self._anchors: dict[str, _Anchor] = {}

    def update(
        self,
        key: str,
        *,
        wall_now: datetime,
        measured: float | None,
        measured_at: datetime | None,
        counter_kwh: float | None,
        session_start: datetime | None,
        efficiency: float,
        usable_kwh: float,
    ) -> SocReading:
        """Der Ladestand des Fahrzeugs ``key`` in diesem Zyklus.

        - ``measured``, ``measured_at``: der gemessene Ladestand und der Zeitstempel des Werts
          (ohne Zeitstempel gilt ``wall_now``); ``None``, wenn es keinen gültigen gibt
        - ``counter_kwh``: der Zähler ``wallbox.<w>.total_kwh`` der Wallbox, an der das Fahrzeug
          steht; ``None``, wenn er unbekannt ist
        - ``session_start``: Beginn des Ansteckvorgangs, für einen Vorgang immer derselbe Wert;
          ``None`` ohne Ansteckvorgang
        - ``efficiency``: Wirkungsgrad des Ladens als Anteil (0,9 für 90 %)
        - ``usable_kwh``: nutzbare Kapazität des Fahrzeugakkus

        Eine Messung mit neuem Wert oder neuem Zeitstempel wird der Anker, mit dem Zählerstand
        von jetzt. Derselbe Wert mit demselben Zeitstempel ist keine neue Messung (Home Assistant
        meldet den Ladestand des e-Golf nur beim Parken): Hat der Zähler seither gezählt, ist der
        Ladestand geschätzt. Der Anker gilt nur, wenn er höchstens ``SOC_ANCHOR_BEFORE`` vor dem
        Anstecken liegt; sonst kann das Auto seither gefahren sein. Stammt sein Zählerstand nicht
        aus diesem Ansteckvorgang, gilt der erste bekannte Zählerstand darin: Was die Wallbox
        vorher gezählt hat, ging nicht in dieses Auto, und es kann an einer anderen Wallbox
        stecken. Ein Zähler unter dem des Ankers (Tausch) macht ihn ungültig. Geschätzt wird nur
        in einem Ansteckvorgang und mit beiden Zählerständen, höchstens bis 100 %. Sonst ist der
        Ladestand angenommen.
        """
        anchor = self._anchors.get(key)
        if (
            anchor is not None
            and session_start is not None
            and anchor.session != session_start
            and counter_kwh is not None
        ):
            anchor = replace(anchor, counter_kwh=counter_kwh, session=session_start)
            self._anchors[key] = anchor
        if (
            anchor is not None
            and counter_kwh is not None
            and anchor.counter_kwh is not None
            and counter_kwh < anchor.counter_kwh
        ):
            del self._anchors[key]  # Zähler getauscht: Der Anker taugt nicht mehr
            anchor = None
        if measured is not None:
            at = measured_at if measured_at is not None else wall_now
            if anchor is None or (anchor.pct, anchor.at) != (measured, at):
                anchor = _Anchor(measured, counter_kwh, at, session_start)
                self._anchors[key] = anchor
        if anchor is None:
            return _ASSUMED
        if session_start is not None and anchor.at < session_start - SOC_ANCHOR_BEFORE:
            return _ASSUMED
        counted = (
            None
            if counter_kwh is None or anchor.counter_kwh is None
            else counter_kwh - anchor.counter_kwh
        )
        if measured is not None and not counted:
            return SocReading(anchor.pct, "measured")
        if session_start is None or counted is None:
            return _ASSUMED
        return SocReading(
            min(100.0, anchor.pct + counted * efficiency / usable_kwh * 100.0), "estimated"
        )
