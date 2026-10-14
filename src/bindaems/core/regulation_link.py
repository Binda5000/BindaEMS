"""Verdrahtung der Regelung neben der Laufzeit (Plan 2c-2a, Design 4.3 und 4.5).

``RegulationLink`` ist alles, was die Laufzeit von der Regelung sieht:

- Er baut je Zyklus die Eingaben des Kerns (``Regulation``) und gibt Urteile, Ergebnisse,
  ``forget`` und ``idle`` an ihn weiter. Die Eingaben sind die der Laderegelung (Plan 2c-1) und
  die des Akku-Sollwerts: PV und Hauslast aus den abgeleiteten Größen, der Basis-Sollwert
  ``ess.grid_setpoint_w`` und die Sperren des Zyklus.
- Er legt am Ende des Regelteils Health und Alarme des Kerns ab (``finish``). ``health`` und
  ``alarms`` lesen nur diesen Stand und dürfen deshalb aus einem anderen Thread kommen.
- Er fängt jede Ausnahme des Kerns (Präzisierung 15).

Wirft ein Aufruf des Kerns, gilt der Zyklus als gescheitert (``failed``, ab ``start_cycle``):

- Der Kern bekommt sofort ``idle``, über ``self.idle``: So sieht ein Einhüllen von außen
  (Simulator) auch diese Pause. Bis zum Ende des Zyklus erreicht ihn danach nur noch ``idle``; die
  anderen Aufrufe antworten wie ohne Ergebnis (keine Befehle, ``{}``, ``None``). Wirft auch
  ``idle``, baut der Link den Kern neu.
- Die Laufzeit führt danach keinen Befehl der Regelung mehr aus, und der Heartbeat bleibt aus; die
  Befehle des Keepers laufen weiter.
- Health und Alarme des Kerns bleiben auf dem Stand des letzten Zyklus ohne Ausnahme.
- Der Alarm ``control.exception`` (Fehler) gilt, solange der letzte Zyklus mit Regelung scheiterte:
  Eine Folge gescheiterter Zyklen beginnt mit dem ersten Zyklus mit Ausnahme, auch einem ohne
  Regelung, und endet erst mit einem Zyklus, in dem der Kern regelte (``step``) und nichts warf.
  Ein Zyklus ohne Regelung und ohne Ausnahme, etwa in SAFE, lässt den Alarm stehen. ``since`` ist
  die Wanduhr des ersten gescheiterten Zyklus der Folge; der Text nennt den Typ der ersten
  Ausnahme des letzten.
- Das Log nennt die erste Ausnahme einer Folge mit Traceback, jede weitere als eine Zeile.

Nicht für mehrere Threads gedacht, außer ``health`` und ``alarms``.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from datetime import datetime
from typing import Any, Final, TypeVar

import structlog

from bindaems.core.control.actuator import Actuator, Capabilities, WakeStart
from bindaems.core.control.charge_inputs import ChargeInputs, observe, ok_number
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.last_values import ControlCommand, Key
from bindaems.core.control.regulation import Regulation, RegulationInputs
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import Verdict
from bindaems.core.state.derived import Derived
from bindaems.shared.config import Config
from bindaems.shared.control import Command, CommandValue, Intent
from bindaems.shared.domain import Alarm, Severity, Snapshot

log = structlog.get_logger(__name__)

CONTROL_EXCEPTION: Final = "control.exception"

_T = TypeVar("_T")


class RegulationLink:
    """Die Regelung, wie die Laufzeit sie sieht: Eingaben, Ausnahmen, Health und Alarme.

    ``regulation`` ist der Kern, ``started`` die Wanduhr beim Start der Regelung.
    """

    def __init__(self, cfg: Config, limits: HardLimits, *, started: datetime) -> None:
        self._cfg = cfg
        self._limits = limits
        self.regulation = Regulation(cfg, limits, started=started)
        # Die Uhren des letzten Zyklus, davor ``started``: für ``idle`` nach einer Ausnahme (Uhr der
        # Safety-Schicht) und für den Start eines neu gebauten Kerns (Wanduhr)
        self._snow = self._wall_now = started
        self._failed = False
        self._regulated = False  # in diesem Zyklus regelte der Kern (``step``) ohne Ausnahme
        self._error: str | None = None  # der Typ der ersten Ausnahme im Zyklus
        # Wanduhr des ersten gescheiterten Zyklus der laufenden Folge; ``None`` ohne Folge
        self._failed_since: datetime | None = None
        self._traced = False  # die Folge gescheiterter Zyklen hat ihren Traceback im Log
        self._health = self.regulation.health()
        self._alarms: list[Alarm] = []  # die des Kerns
        self._exception: Alarm | None = None  # ``control.exception``

    @property
    def failed(self) -> bool:
        """Ob in diesem Zyklus ein Aufruf des Kerns warf."""
        return self._failed

    def start_cycle(self) -> None:
        """Am Anfang jedes Zyklus, vor dem Rücklesen: Der Zyklus ist noch nicht gescheitert."""
        self._failed = False
        self._regulated = False
        self._error = None

    def step(
        self,
        *,
        snap: Snapshot,
        derived: Derived,
        now: datetime,
        snow: datetime,
        present: Mapping[Key, CommandValue],
        capabilities: Mapping[str, Capabilities],
        actuators: Mapping[str, Actuator],
        intent: Intent,
        accepted_at: datetime | None,
        blocked: frozenset[Key],
        connected: Callable[[str], bool | None],
        locks: Mapping[str, tuple[str, ...]],
    ) -> list[ControlCommand]:
        """Ein Zyklus mit Regelung: die Befehle des Kerns, nach einer Ausnahme keine.

        Die Eingaben der Laderegelung (Plan 2c-1, Design 4.2):

        - Die Zeiten der Regelung laufen auf der Uhr der Safety-Schicht (``snow``); die Wanduhr
          ``now`` gilt für ``boost_until`` und den Kalendertag des Weckkontingents.
        - Die Beobachtungen liest ``observe``; ``connected`` sagt, ob eine Quelle verbunden ist.
        - Steuerbar ist ein Aktor nach seinen Fähigkeiten ``capabilities``. Weckbar ist ein Aktor
          mit ``WakeStart``, dessen ``wake_capabilities`` einen Start mit Weckerlaubnis zulassen;
          jeder andere ist es nie.
        - ``blocked`` nennt die Arten, für die in diesem Zyklus kein Befehl kommt: offene
          Schreibvorgänge und die Aktoren in Übergabe.
        - ``accepted_at`` ist die Annahmezeit der Absicht ``intent``.

        Dazu die des Akku-Sollwerts (Plan 2c-2a): PV (``derived.pv_total_w``) und Hauslast
        (``derived.house_load_w``), der Basis-Sollwert ``ess.grid_setpoint_w`` (``ok_number``) und
        die Sperren ``locks``, die Gründe je Aktor wie im ``SafetyContext``.

        Auch eine Ausnahme beim Bauen der Eingaben setzt nur die Regelung aus.
        """
        self._snow, self._wall_now = snow, now

        def regulate() -> list[ControlCommand]:
            grid, wallboxes, vehicles = observe(snap, derived, self._cfg, connected)
            charge = ChargeInputs(
                now=snow,
                wall_now=now,
                grid=grid,
                wallboxes=wallboxes,
                vehicles=vehicles,
                present=present,
                controllable={actuator: c.controllable for actuator, c in capabilities.items()},
                wakeable={
                    actuator_id: isinstance(actuator, WakeStart)
                    and actuator.wake_capabilities(snap, snow).controllable
                    for actuator_id, actuator in actuators.items()
                },
                blocked=blocked,
                intent=intent,
                accepted_at=accepted_at,
            )
            commands = self.regulation.step(
                RegulationInputs(
                    charge,
                    pv_w=derived.pv_total_w,
                    house_w=derived.house_load_w,
                    base_setpoint_w=ok_number(snap, "ess.grid_setpoint_w"),
                    locks=locks,
                )
            )
            self._regulated = True
            return commands

        return self._call("step", regulate, [])

    def idle(self, snow: datetime) -> None:
        """Ein Zyklus ohne Regelung; ``snow`` ist die Zeit der Safety-Schicht.

        Wirft der Kern dabei, baut der Link ihn neu, mit der Wanduhr des letzten Zyklus als Start
        der Regelung: Was der alte Kern wusste, gilt nicht mehr.
        """
        self._snow = snow
        try:
            self.regulation.idle(snow)
        except Exception as exc:  # die Regelung setzt aus, nicht der Zyklus
            self._fail("idle", exc)
            self.regulation = Regulation(self._cfg, self._limits, started=self._wall_now)

    def note_verdict(
        self, command: Command, verdict: Verdict | None, *, not_sent: bool = False
    ) -> None:
        """Das Urteil der Safety-Schicht über einen Befehl der Regelung, auch ``None``."""
        self._call(
            "note_verdict",
            lambda: self.regulation.note_verdict(command, verdict, not_sent=not_sent),
            None,
        )

    def note_result(self, result: WriteResult) -> None:
        """Das Ergebnis eines abgeschlossenen Schreibvorgangs, auch eines des Keepers."""
        self._call("note_result", lambda: self.regulation.note_result(result), None)

    def forget(self, actuators: Collection[str]) -> None:
        """Die Regelung vergisst ihre letzten Werte dieser Aktoren."""
        self._call("forget", lambda: self.regulation.forget(actuators), None)

    def vehicle_wallbox(self) -> dict[str, str | None]:
        """Fahrzeug-Aktor → Wallbox für den ``SafetyContext``; ohne Ergebnis ``{}`` (unbekannt)."""
        return self._call("vehicle_wallbox", self.regulation.vehicle_wallbox, {})

    def grid_budget_w(self) -> float | None:
        """Das 15-min-Budget für den ``SafetyContext``; ohne Ergebnis ``None``."""
        return self._call("grid_budget_w", self.regulation.grid_budget_w, None)

    def finish(self, wall_now: datetime) -> None:
        """Am Ende des Regelteils: legt Health und Alarme des Kerns ab und führt die Folge.

        - **Mit Ausnahme** in diesem Zyklus, auch in ``health`` oder ``alarms`` selbst: Health und
          Alarme des Kerns bleiben auf dem Stand des letzten Zyklus ohne Ausnahme. Die Folge
          gescheiterter Zyklen beginnt mit ``since`` = ``wall_now`` oder geht weiter, auch in
          einem Zyklus ohne Regelung.
        - **Mit Regelung** (``step``) **ohne Ausnahme:** Die Folge endet.
        - **Ohne Regelung und ohne Ausnahme,** etwa in SAFE: Die Folge bleibt, wie sie ist
          (Präzisierung 15: „solange der letzte Zyklus mit Regelung scheiterte“).

        ``failed`` gilt dagegen nur für diesen Zyklus.
        """
        self._wall_now = wall_now
        health = self._call("health", self.regulation.health, self._health)
        alarms = self._call("alarms", lambda: self.regulation.alarms(wall_now), self._alarms)
        if self._failed:
            if self._failed_since is None:
                self._failed_since = wall_now
            message = f"Regelung ausgesetzt: Ausnahme {self._error} im Regelzyklus."
            self._exception = Alarm(CONTROL_EXCEPTION, Severity.ERROR, message, self._failed_since)
            return
        self._health, self._alarms = health, alarms
        if self._regulated:
            self._failed_since, self._exception, self._traced = None, None, False

    def health(self) -> dict[str, Any]:
        """Der abgelegte Stand des Kerns; vor dem ersten ``finish`` der des frisch gebauten."""
        return self._health

    def alarms(self) -> list[Alarm]:
        """Die abgelegten Alarme des Kerns, dazu ``control.exception``, solange eine Folge
        gescheiterter Zyklen läuft: ab dem ``finish`` ihres ersten Zyklus bis zu dem eines
        Zyklus mit Regelung ohne Ausnahme."""
        exception = [] if self._exception is None else [self._exception]
        return [*self._alarms, *exception]

    def _call(self, name: str, call: Callable[[], _T], fallback: _T) -> _T:
        """Ruft den Kern; nach einer Ausnahme in diesem Zyklus ruht er, und es gilt ``fallback``.

        Wirft der Aufruf, ist der Zyklus gescheitert: Der Kern bekommt ``idle`` mit der Zeit der
        Safety-Schicht des letzten Zyklus, und zurück kommt ``fallback``.
        """
        if self._failed:
            return fallback
        try:
            return call()
        except Exception as exc:  # die Regelung setzt aus, nicht der Zyklus
            self._fail(name, exc)
            self.idle(self._snow)
            return fallback

    def _fail(self, name: str, exc: Exception) -> None:
        """Der Zyklus ist gescheitert; das Log bekommt die Ausnahme, aus ``except`` gerufen.

        Die erste einer Folge kommt mit Traceback, jede weitere als eine Zeile.
        """
        self._failed = True
        if self._error is None:
            self._error = type(exc).__name__
        if self._traced:
            log.error("Weitere Ausnahme in der Regelung", call=name, error=repr(exc))
        else:
            self._traced = True
            log.exception("Ausnahme in der Regelung, Regelung ausgesetzt", call=name)
