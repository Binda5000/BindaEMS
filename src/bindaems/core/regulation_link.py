"""Verdrahtung der Regelung neben der Laufzeit (Plan 2c-2a, Design 4.3 und 4.5).

``RegulationLink`` ist alles, was die Laufzeit von der Regelung sieht:

- Er baut je Zyklus die Eingaben des Kerns (``Regulation``) und gibt Urteile, Ergebnisse,
  ``forget`` und ``idle`` an ihn weiter. Die Eingaben sind die der Laderegelung (Plan 2c-1),
  die des Akku-Sollwerts (PV und Hauslast aus den abgeleiteten Größen, der Basis-Sollwert
  ``ess.grid_setpoint_w`` und die Sperren des Zyklus) und die Viertelstunde des 15-min-Wächters
  aus den Flüssen des Zyklus und dem laufenden Slot der Abrechnung (Präzisierung 9); den
  Netzanteil der Wallboxen rechnet er dafür mit PV und Akku zuerst für die Wallboxen in Boost.
  Dazu kommen je Klimaanlage ihre Eingaben und ob der Betriebszustand DEGRADED ist (Plan 6a).
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

Die Klimaanlagen (Plan 6a, Ruling R19) laufen in eigenen Aufrufen: ihre Eingaben und
``Regulation.thermal_step`` nach ``step``, ihre Urteile und Ergebnisse (``thermal.*``). Wirft einer
davon, gilt nur die Klimaanlage für diesen Zyklus als gescheitert (``thermal_failed``): Sie ruht
(``Regulation.thermal_idle``), ihre Befehle entfallen, die Laufzeit nimmt ihre Freigabe zurück und
der Adapter sendet 0; die Befehle der Autos und des Akkus bleiben, ``failed`` bleibt falsch. Der
Alarm ``control.thermal_exception`` (Warnung) folgt denselben Regeln wie ``control.exception``,
beendet von einem Zyklus, in dem ``thermal_step`` ohne Ausnahme lief; das Log ebenso.

Nicht für mehrere Threads gedacht, außer ``health`` und ``alarms``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Collection, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any, Final, TypeVar

import structlog

from bindaems.core.accounting.flows import SlotProgress, derived_flows, wb_key
from bindaems.core.adapters.coe import coe_source
from bindaems.core.control.actuator import Actuator, Capabilities, WakeStart
from bindaems.core.control.charge_inputs import ChargeInputs, homes, observe, ok_number
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.last_values import ControlCommand, Key
from bindaems.core.control.peak_guard import QuarterObs
from bindaems.core.control.regulation import Regulation, RegulationInputs, ThermalInputs
from bindaems.core.control.thermal import thermal_mode
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import Verdict
from bindaems.core.state.derived import Derived
from bindaems.shared.config import CoeOffsetConfig, Config
from bindaems.shared.control import (
    THERMAL_PREFIX,
    Command,
    CommandValue,
    Intent,
    OperatingState,
    thermal_actuator,
    vehicle_actuator,
)
from bindaems.shared.domain import Alarm, Severity, Snapshot
from bindaems.shared.plan import IntentPlan

log = structlog.get_logger(__name__)

CONTROL_EXCEPTION: Final = "control.exception"
THERMAL_EXCEPTION: Final = "control.thermal_exception"
_FROM_GRID: Final = "grid>"  # der Anfang der Schlüssel aller Flüsse aus dem Netz (``allocate``)

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
        # Dasselbe für die Klimaanlagen (``control.thermal_exception``, Ruling R19)
        self._thermal_failed = False
        self._thermal_regulated = False
        self._thermal_error: str | None = None
        self._thermal_failed_since: datetime | None = None
        self._thermal_traced = False
        self._thermal_exception: Alarm | None = None

    @property
    def failed(self) -> bool:
        """Ob in diesem Zyklus ein Aufruf des Kerns warf."""
        return self._failed

    @property
    def thermal_failed(self) -> bool:
        """Ob in diesem Zyklus ein Aufruf für die Klimaanlagen warf (Ruling R19)."""
        return self._thermal_failed

    def start_cycle(self) -> None:
        """Am Anfang jedes Zyklus, vor dem Rücklesen: Der Zyklus ist noch nicht gescheitert."""
        self._failed = False
        self._regulated = False
        self._error = None
        self._thermal_failed = False
        self._thermal_regulated = False
        self._thermal_error = None

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
        flows: Mapping[str, float] | None,
        slot: SlotProgress | None,
        hourly_count: Callable[[str], int],
        operating: OperatingState,
    ) -> list[ControlCommand]:
        """Ein Zyklus mit Regelung: die Befehle des Kerns, nach einer Ausnahme keine.

        Die Eingaben der Laderegelung (Plan 2c-1, Design 4.2):

        - Die Zeiten der Regelung laufen auf der Uhr der Safety-Schicht (``snow``); die Wanduhr
          ``now`` gilt für ``boost_until`` und den Kalendertag des Weckkontingents.
        - Die Beobachtungen liest ``observe``; ``connected`` sagt, ob eine Quelle verbunden ist.
          Ob Home Assistant ein Fahrzeug zu Hause meldet, liest ``homes`` (Plan 2c-2b).
        - Steuerbar ist ein Aktor nach seinen Fähigkeiten ``capabilities``. Weckbar ist ein Aktor
          mit ``WakeStart``, dessen ``wake_capabilities`` einen Start mit Weckerlaubnis zulassen;
          jeder andere ist es nie.
        - ``blocked`` nennt die Arten, für die in diesem Zyklus kein Befehl kommt: offene
          Schreibvorgänge und die Aktoren in Übergabe.
        - ``accepted_at`` ist die Annahmezeit der Absicht ``intent``.
        - ``hourly_count`` nennt zu einem Aktor die Befehle im Stundenfenster der Safety-Schicht
          (zur Zeit ``snow``); der Link fragt es für jeden Aktor eines Fahrzeugs mit Tessie
          (``ChargeInputs.commands_hour``, Plan 2d, Präzisierung 9).

        Dazu die des Akku-Sollwerts (Plan 2c-2a): PV (``derived.pv_total_w``) und Hauslast
        (``derived.house_load_w``), der Basis-Sollwert ``ess.grid_setpoint_w`` (``ok_number``) und
        die Sperren ``locks``, die Gründe je Aktor wie im ``SafetyContext``. Und die Viertelstunde
        des 15-min-Wächters (``quarter``) aus ``flows``, den Flüssen dieses Zyklus
        (``derived_flows`` mit ``wallbox_order``; ``None`` heißt Bilanz unbekannt), ``slot``, dem
        laufenden Slot der Abrechnung bis zu diesem Zyklus (``SlotAccumulator.current``), und den
        Wallboxen in Boost aus dem letzten Schritt des Kerns (``Regulation.boost_wallboxes``, einen
        Zyklus alt).

        Danach, in einem eigenen Aufruf, die Klimaanlagen (``Regulation.thermal_step``) mit
        denselben Eingaben, dazu je Klimaanlage ihre (``_thermal``) und ob der Betriebszustand
        ``operating`` DEGRADED ist: Dann gehen sie auf 0. Ihre Befehle kommen hinter die des
        Kerns; wirft dieser Aufruf, entfallen nur sie (``thermal_failed``).

        Auch eine Ausnahme beim Bauen der Eingaben setzt nur die Regelung aus.
        """
        self._snow, self._wall_now = snow, now
        no_boost: frozenset[str] = frozenset()  # ohne Ergebnis keine Wallbox in Boost
        boost = self._call("boost_wallboxes", self.regulation.boost_wallboxes, no_boost)

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
                homes=homes(snap, self._cfg),
                commands_hour={
                    vehicle_actuator(key): hourly_count(vehicle_actuator(key))
                    for key, vehicle in self._cfg.vehicles.items()
                    if vehicle.tessie is not None
                },
            )
            built.append(
                RegulationInputs(
                    charge,
                    pv_w=derived.pv_total_w,
                    house_w=derived.house_load_w,
                    base_setpoint_w=ok_number(snap, "ess.grid_setpoint_w"),
                    locks=locks,
                    quarter=self._quarter(flows, slot, derived, boost),
                )
            )
            commands = self.regulation.step(built[0])
            self._regulated = True
            return commands

        def regulate_thermal() -> list[ControlCommand]:
            found = self.regulation.thermal_step(
                replace(
                    built[0],
                    thermal=self._thermal(snap, snow, capabilities, connected, locks),
                    degraded=operating is OperatingState.DEGRADED,
                )
            )
            self._thermal_regulated = True
            return found

        built: list[RegulationInputs] = []
        none: list[ControlCommand] = []
        commands = self._call("step", regulate, none)
        if not self._regulated:
            return commands
        return [*commands, *self._thermal_call("thermal_step", regulate_thermal, none)]

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

    def rest_plan(self, plan: IntentPlan | None, wall_now: datetime) -> None:
        """Nach ``idle``: der Plan der Absicht für Health, solange die Regelung ruht (Plan 3c)."""
        self._call("rest_plan", lambda: self.regulation.rest_plan(plan, wall_now), None)

    def note_verdict(
        self, command: Command, verdict: Verdict | None, *, not_sent: bool = False
    ) -> None:
        """Das Urteil der Safety-Schicht über einen Befehl der Regelung, auch ``None``; das über
        einen Versatz einer Klimaanlage (``thermal.*``) im Aufruf der Klimaanlagen."""
        call = self._thermal_call if command.actuator.startswith(THERMAL_PREFIX) else self._call
        call(
            "note_verdict",
            lambda: self.regulation.note_verdict(command, verdict, not_sent=not_sent),
            None,
        )

    def note_result(self, result: WriteResult) -> None:
        """Das Ergebnis eines abgeschlossenen Schreibvorgangs, auch eines des Keepers; das einer
        Klimaanlage (``thermal.*``) im Aufruf der Klimaanlagen."""
        call = self._thermal_call if result.actuator.startswith(THERMAL_PREFIX) else self._call
        call("note_result", lambda: self.regulation.note_result(result), None)

    def forget(self, actuators: Collection[str]) -> None:
        """Die Regelung vergisst ihre letzten Werte dieser Aktoren; die Klimaanlagen im eigenen
        Aufruf (``_thermal_call``)."""
        self._call("forget", lambda: self.regulation.core_forget(actuators), None)
        self._thermal_call(
            "thermal_forget", lambda: self.regulation.thermal_forget(actuators), None
        )

    def thermal_targets(self) -> dict[str, float]:
        """Je Klimaanlage der Versatz, den die Regelung in diesem Zyklus entschied (Ruling R26).

        Leer, wenn die Klimaanlagen in diesem Zyklus nicht ohne Ausnahme geregelt wurden
        (``thermal_step`` lief nicht oder warf, oder ein späterer Aufruf für sie warf): Dann gibt
        die Laufzeit keine frei.
        """
        if not self._thermal_regulated:
            return {}
        return self._thermal_call("thermal_targets", self.regulation.thermal_targets, {})

    def vehicle_wallbox(self) -> dict[str, str | None]:
        """Fahrzeug-Aktor → Wallbox für den ``SafetyContext``; ohne Ergebnis ``{}`` (unbekannt)."""
        return self._call("vehicle_wallbox", self.regulation.vehicle_wallbox, {})

    def grid_budget_w(self) -> float | None:
        """Das 15-min-Budget für den ``SafetyContext``; ohne Ergebnis ``None``."""
        return self._call("grid_budget_w", self.regulation.grid_budget_w, None)

    def plan_inputs(self) -> dict[str, float]:
        """Die Eingaben des Plans für die Entscheidungen dieses Zyklus; ohne Ergebnis keine."""
        return self._call("plan_inputs", self.regulation.plan_inputs, {})

    def finish(self, wall_now: datetime) -> None:
        """Am Ende des Regelteils: legt Health und Alarme des Kerns ab und führt die Folge.

        - **Mit Ausnahme** in diesem Zyklus, auch in ``health`` oder ``alarms`` selbst: Health und
          Alarme des Kerns bleiben auf dem Stand des letzten Zyklus ohne Ausnahme. Die Folge
          gescheiterter Zyklen beginnt mit ``since`` = ``wall_now`` oder geht weiter, auch in
          einem Zyklus ohne Regelung.
        - **Mit Regelung** (``step``) **ohne Ausnahme:** Die Folge endet.
        - **Ohne Regelung und ohne Ausnahme,** etwa in SAFE: Die Folge bleibt, wie sie ist
          (Präzisierung 15: „solange der letzte Zyklus mit Regelung scheiterte“).

        ``failed`` gilt dagegen nur für diesen Zyklus. Health der Klimaanlagen (``thermal``) kommt
        im eigenen Aufruf (``_thermal_call``): Wirft er, ist es leer, und nur sie ruhen.
        """
        self._wall_now = wall_now
        empty: dict[str, dict[str, Any]] = {}
        thermal = self._thermal_call("thermal_health", self.regulation.thermal_health, empty)
        self._finish_thermal(wall_now)
        health = self._call("health", self.regulation.core_health, self._health)
        alarms = self._call("alarms", lambda: self.regulation.alarms(wall_now), self._alarms)
        if self._failed:
            if self._failed_since is None:
                self._failed_since = wall_now
            message = f"Regelung ausgesetzt: Ausnahme {self._error} im Regelzyklus."
            self._exception = Alarm(CONTROL_EXCEPTION, Severity.ERROR, message, self._failed_since)
            return
        self._health, self._alarms = {**health, "thermal": thermal}, alarms
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
        thermal = [] if self._thermal_exception is None else [self._thermal_exception]
        return [*self._alarms, *exception, *thermal]

    def _finish_thermal(self, wall_now: datetime) -> None:
        """Führt die Folge gescheiterter Zyklen der Klimaanlagen wie ``finish`` die des Kerns."""
        if self._thermal_failed:
            if self._thermal_failed_since is None:
                self._thermal_failed_since = wall_now
            error = self._thermal_error
            message = f"Regelung der Klimaanlage ausgesetzt: Ausnahme {error} im Regelzyklus."
            self._thermal_exception = Alarm(
                THERMAL_EXCEPTION, Severity.WARNING, message, self._thermal_failed_since
            )
        elif self._thermal_regulated:
            self._thermal_failed_since, self._thermal_exception = None, None
            self._thermal_traced = False

    def _thermal_call(self, name: str, call: Callable[[], _T], fallback: _T) -> _T:
        """Ruft den Kern für die Klimaanlagen; nach einer Ausnahme in diesem Zyklus ruhen nur sie.

        Wirft der Aufruf, ruhen die Klimaanlagen (``Regulation.thermal_idle``), und zurück kommt
        ``fallback``; wirft auch das, bleibt es dabei: Ohne Freigabe sendet der Adapter 0, und der
        nächste Zyklus versucht es neu. Nach einer Ausnahme des Kerns gilt ``fallback`` ohne Aufruf.
        """
        if self._failed or self._thermal_failed:
            return fallback
        try:
            return call()
        except Exception as exc:  # nur die Klimaanlagen setzen aus
            self._thermal_failed = True
            if self._thermal_error is None:
                self._thermal_error = type(exc).__name__
            if self._thermal_traced:
                log.error(
                    "Weitere Ausnahme in der Regelung der Klimaanlage", call=name, error=repr(exc)
                )
            else:
                self._thermal_traced = True
                log.exception(
                    "Ausnahme in der Regelung der Klimaanlage, Klimaanlage ausgesetzt", call=name
                )
            with contextlib.suppress(Exception):
                self.regulation.thermal_idle()
            return fallback

    def _thermal(
        self,
        snap: Snapshot,
        snow: datetime,
        capabilities: Mapping[str, Capabilities],
        connected: Callable[[str], bool | None],
        locks: Mapping[str, tuple[str, ...]],
    ) -> dict[str, ThermalInputs]:
        """Je Klimaanlage ihre Eingaben (Plan 6a, Entscheidung 5.1).

        Frisch sind ihre Werte, solange ihre Quelle ``thermal.<name>`` (``coe_source``) verbunden
        ist: Der Adapter trennt sie nach ``stale_after_s`` ohne gültiges Paket. Die Betriebsart
        liest ``thermal_mode``; steuerbar ist der Aktor nach seinen Fähigkeiten (ohne Eintrag
        nicht), gesperrt, wenn ``locks`` für ihn Gründe nennt. Die Zeit ist die der Safety-Schicht
        in Sekunden; sie läuft monoton. Die Leistung ist ``thermal.<name>.power_w`` (``ok_number``,
        sonst unbekannt).
        """
        mono_now = snow.timestamp()
        found: dict[str, ThermalInputs] = {}
        for name, device in self._cfg.thermal.devices.items():
            if not isinstance(device, CoeOffsetConfig):
                continue
            actuator = thermal_actuator(name)
            able = capabilities.get(actuator)
            found[name] = ThermalInputs(
                fresh=connected(coe_source(name)) is True,
                mode=thermal_mode(snap, name),
                controllable=able is not None and able.controllable,
                locked=bool(locks.get(actuator)),
                mono_now=mono_now,
                power_w=ok_number(snap, f"thermal.{name}.power_w"),
            )
        return found

    def _quarter(
        self,
        flows: Mapping[str, float] | None,
        slot: SlotProgress | None,
        derived: Derived,
        boost: Collection[str],
    ) -> QuarterObs | None:
        """Die laufende Viertelstunde für den 15-min-Wächter (Präzisierung 9); ohne Slot ``None``.

        - ``grid_wh``: die Energie der Flüsse aus dem Netz (``grid>*``) im Slot bisher
        - ``grid_w``: die Leistung der Flüsse aus dem Netz in diesem Zyklus; ohne ``flows`` ``None``
        - ``wallbox_grid_w``: je Wallbox in der Reihenfolge von ``wallbox_order`` ihr Netzanteil in
          diesem Zyklus (``grid>wb:<w>``), ohne Eintrag 0 W; ohne ``flows`` leer

        Den Netzanteil je Wallbox zieht der Wächter für die Autos in Boost ab. Die Flüsse der
        Abrechnung geben PV und Akku den Wallboxen in der Reihenfolge von ``wallbox_order``; der
        Akku-Sollwert lässt aber Boost aus dem Akku laden und schickt den Fehlbetrag der anderen
        Autos ans Netz. Mit Wallboxen in Boost (``boost``) kommt der Netzanteil deshalb aus einer
        zweiten Zuordnung derselben abgeleiteten Größen (``derived_flows``), in der die Wallboxen in
        Boost PV und Akku zuerst bekommen, dann die übrigen in der Reihenfolge von
        ``wallbox_order``. So zieht der Wächter nie den Netzbezug eines anderen Autos als Boost ab.
        Ohne Wallboxen in Boost ist es der Netzanteil aus ``flows``; die Summen ändern sich nicht.
        """
        if slot is None:
            return None
        start, energy_wh = slot.slot_start, _from_grid(slot.flows_wh)
        if flows is None:
            return QuarterObs(start, energy_wh, None, {})
        order = self._cfg.wallbox_order
        split: Mapping[str, float] = flows
        if boost:
            first = [*(w for w in order if w in boost), *(w for w in order if w not in boost)]
            # dieselbe Bilanz wie ``flows``; fehlte sie, zöge der Wächter nichts als Boost ab
            split = derived_flows(derived, first) or {}
        shares = {wallbox: split.get(wb_key("grid", wallbox), 0.0) for wallbox in order}
        return QuarterObs(start, energy_wh, _from_grid(flows), shares)

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


def _from_grid(flows: Mapping[str, float]) -> float:
    """Die Summe der Flüsse aus dem Netz (``grid>*``): ins Haus, in die Autos und in den Akku."""
    return sum((value for key, value in flows.items() if key.startswith(_FROM_GRID)), 0.0)
