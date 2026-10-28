"""Regelungs-Kern: die einzige Schnittstelle der Laufzeit zur Regelung (Plan 2c-2a, Design 4.1).

- ``RegulationInputs``: die Eingaben eines Zyklus der Regelung
- ``Regulation``: der Kern; er hält die Bausteine der Regelung und verteilt jeden Aufruf an sie
- ``battery_cars``: die Autos für den Akku-Sollwert (Präzisierung 4)
- ``peak_wanted_w``: der gewollte Netzbezug für den 15-min-Wächter (Präzisierung 10)
- ``peak_cars``: die Autos für den 15-min-Wächter (Präzisierung 11)

Die Bausteine sind die Laderegelung (``ChargeController``), der Akku-Sollwert
(``BatteryController``) und der 15-min-Wächter (``PeakGuard``). Ein Zyklus (``step``) bestimmt
als Erstes den gültigen Slot des Plans zur Wanduhr (``plan_exec``, Plan 3c) und rechnet dann in der
Reihenfolge von Präzisierung 3:

1. die Laderegelung mit den Grenzen und der Start-Sperre, die der Wächter im vorigen Zyklus
   bestimmt hat, und dem gültigen Slot (Ladeleistung je Auto in „Geplant“, Plan 4b),
2. die Autos je Wallbox aus der Zuordnung und den Modi dieses Zyklus
   (``ChargeController.cars``, auch ein Gast), dazu die Wallboxen ohne Ladepunkt
   (``battery_cars``),
3. Fehlbetrag und erlaubten Akku-Anteil (``BatteryController.assess``),
4. den Wächter (``PeakGuard.step``) mit dem gewollten Netzbezug, solange Victron handeln kann
   (``peak_wanted_w``), und mit seinem Ziel: dem Budget des Plan-Slots, sonst
   ``control.peak_target_kw`` (``guard_target_kw``); er kennt die feste Grenze von Regel 2: das
   Budget für diesen Zyklus, die Grenzen für den nächsten,
5. den Override (``BatteryController.decide``) mit diesem Budget,
6. danach, in einem eigenen Aufruf (``thermal_step``), je Klimaanlage die Strategie
   (``ThermalStrategy``, Plan 6a) mit dem Rest-Überschuss nach Akku und Autos: Einspeisung und
   Netzbezug, SOC und Schwelle des Slots, Druck von Wächter und Überlast, was die Autos noch
   aufnehmen könnten (``ChargeController.unmet_surplus_w``). ``RegulationLink`` ruft ihn getrennt
   ab: Eine Ausnahme dort setzt nur die Klimaanlagen aus (Ruling R19).

Die Grenzen des Wächters wirken so einen Zyklus später; dafür rechnen Akku-Sollwert und Wächter mit
Zuordnung und Modi desselben Zyklus wie die Laderegelung. Die Befehle kommen in dieser Reihenfolge:
erst die der Ladepunkte, dann der Override, zuletzt die Versätze der Klimaanlagen. Laderegelung,
Akku-Sollwert und Klimaanlagen laufen auf der Uhr der Safety-Schicht (``ChargeInputs.now``), der
Wächter auf der Wanduhr (``ChargeInputs.wall_now``), wie die Viertelstunden der Abrechnung. Sein
Budget gilt nach ``step`` auch für die Safety-Schicht (``grid_budget_w``, Regel 2), sein Alarm
``peak.exceeded`` bis zum Ende seiner Viertelstunde (``alarms``).

Urteile und Ergebnisse zum Override (``victron``, ``grid_setpoint``) gehen nur an den
Akku-Sollwert, die zu einer Klimaanlage (``thermal.<name>``) an deren Strategie, alle anderen an die
Laderegelung; ``forget`` erreicht alle, ``idle`` alle Bausteine.

Der Kern fängt keine Ausnahme; das tut die Verdrahtung neben der Laufzeit (``RegulationLink``,
Plan 2c-2a, Präzisierung 15). Die Schicht ist rein: Die Zeit kommt mit den Eingaben. Nicht für
mehrere Threads gedacht.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from bindaems.core.control.actuator import OFFSET_SAME_K, matches
from bindaems.core.control.battery import BatteryCar, BatteryController, BatteryNeed, cycle_wanted_w
from bindaems.core.control.charge_inputs import ChargeInputs, WallboxObs
from bindaems.core.control.charge_modes import Effective, effective_of
from bindaems.core.control.charging import CarView, ChargeController
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.last_values import ControlCommand, Key, LastValues
from bindaems.core.control.peak_guard import (
    NO_LIMITS,
    PeakCar,
    PeakGuard,
    PeakLimits,
    QuarterObs,
    guard_target_kw,
)
from bindaems.core.control.phase_guard import OverloadLevel
from bindaems.core.control.plan_exec import ActiveSlot, active_slot, plan_health
from bindaems.core.control.thermal import (
    ThermalMode,
    ThermalObs,
    ThermalStrategy,
    ThermalWish,
    offset_for,
)
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import SENDABLE, Outcome, Verdict
from bindaems.shared.config import CoeOffsetConfig, Config
from bindaems.shared.control import (
    THERMAL_PREFIX,
    VICTRON,
    ChargeMode,
    Command,
    CommandKind,
    GuestIntent,
    SupportIntent,
    thermal_actuator,
)
from bindaems.shared.decisions import DecisionResult
from bindaems.shared.domain import Alarm
from bindaems.shared.numbers import finite_number
from bindaems.shared.plan import IntentPlan, IntentPlanSlot, ThermalHint
from bindaems.shared.timeutil import slot_end

_SETPOINT: Final[Key] = (VICTRON, CommandKind.GRID_SETPOINT)  # der Override des Akku-Sollwerts
_OFFSET: Final = CommandKind.SETPOINT_OFFSET  # der Sollwertversatz einer Klimaanlage
# So lange darf das Echo nach einer Bestätigung vom bestätigten Versatz abweichen, bevor die
# Strategie neu beginnt: so lang wie die Bestätigung einer Klimaanlage (``confirm_timeout_s``).
THERMAL_RESYNC_S: Final = 60.0
# Die Leistung einer Klimaanlage ohne Versatz (Ruling R28): gelernt erst, wenn der Versatz so lange
# 0 war (die Leistung folgt ihm träge), dann gleitend mit dieser Zeitkonstante
THERMAL_BASE_SETTLE_S: Final = 300.0
THERMAL_BASE_TAU_S: Final = 300.0
_OFFSET_MODES: Final = frozenset({"heizen", "kuehlen"})  # nur dort wirkt ein Versatz


@dataclass(frozen=True)
class ThermalInputs:
    """Die Eingaben eines Zyklus für eine Klimaanlage (Plan 6a, Entscheidung 5.1).

    - ``fresh``: Ihre Quelle ``thermal.<name>`` ist verbunden; der Adapter trennt sie nach
      ``stale_after_s`` ohne gültiges Paket.
    - ``mode``: die Betriebsart (``thermal_mode``)
    - ``controllable``: Der Aktor ``thermal.<name>`` ist steuerbar (seine Fähigkeiten).
    - ``locked``: Der Aktor ist gesperrt (Gründe in den Sperren).
    - ``mono_now``: die Zeit der Safety-Schicht in Sekunden; sie läuft monoton (``AnchoredClock``)
    - ``power_w``: ihre elektrische Leistung jetzt in W (``thermal.<name>.power_w``); ``None``
      heißt unbekannt
    """

    fresh: bool
    mode: ThermalMode
    controllable: bool
    locked: bool
    mono_now: float
    power_w: float | None = None


@dataclass(frozen=True)
class RegulationInputs:
    """Die Eingaben eines Zyklus der Regelung; ``None`` heißt unbekannt.

    - ``charge``: die Eingaben der Laderegelung; ihre Uhren, die Ist-Werte (``present``), die
      Fähigkeiten (``controllable``) und die blockierten Arten (``blocked``) gelten für den ganzen
      Kern
    - ``pv_w``, ``house_w``: PV-Leistung und Hauslast in W (``Derived.pv_total_w``,
      ``Derived.house_load_w``)
    - ``base_setpoint_w``: der Basis-Sollwert ``ess.grid_setpoint_w`` in W
    - ``locks``: die Gründe der Sperren je Aktor, wie im ``SafetyContext``
    - ``quarter``: die laufende Viertelstunde für den 15-min-Wächter (``QuarterObs``,
      Präzisierung 9): Energie und Leistung wie die Abrechnung, der Netzanteil je Wallbox mit den
      Wallboxen in Boost zuerst; ``None`` heißt nicht da
    - ``thermal``: je Klimaanlage aus ``thermal.devices`` ihre Eingaben (Plan 6a); eine, die hier
      fehlt, regelt dieser Zyklus nicht
    - ``degraded``: Der Betriebszustand ist DEGRADED; die Klimaanlagen gehen dann auf 0
    """

    charge: ChargeInputs
    pv_w: float | None = None
    house_w: float | None = None
    base_setpoint_w: float | None = None
    locks: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    quarter: QuarterObs | None = None
    thermal: Mapping[str, ThermalInputs] = field(default_factory=dict)
    degraded: bool = False


class _Thermal:
    """Eine Klimaanlage im Kern: ihre Strategie, der Abgleich mit dem Echo und ihre Leistung ohne
    Versatz.

    ``confirmed`` ist der Versatz, den das letzte Ergebnis ``OK`` bestätigt hat (das Echo zeigte
    ihn), ``apart_since`` die Zeit, seit der das Echo davon abweicht; beide ``None`` ohne.
    ``base_w`` ist die Leistung bei Versatz 0 in der Betriebsart ``base_mode`` (``learn``), ohne
    Messung ``None``. ``spare_w`` ist die Leistung, die ``freeable_w`` im letzten Zyklus nannte
    (für Health; ``None`` vor dem ersten Zyklus).
    """

    def __init__(self, cfg: CoeOffsetConfig) -> None:
        self.step_k = cfg.step_k
        self.soc_gate_pct = cfg.strategy.battery_soc_pct
        self.strategy = ThermalStrategy(cfg)
        self.confirmed: float | None = None
        self.apart_since: float | None = None
        self.base_w: float | None = None
        self.base_mode: ThermalMode | None = None
        self.spare_w: float | None = None
        # der zuletzt angenommene eigene Versatz und die Zeit des letzten ``thermal_step``: für die
        # Wartezeit der Strategie ab dem Senden einer Erhöhung (``ThermalStrategy.note_raised``)
        self.accepted_k: float | None = None
        self.now: float | None = None
        self._base_at: float | None = None  # Zeit der letzten Messung in ``base_w``
        self._zero_since: float | None = None  # seit wann der Versatz 0 ist

    def reset(self) -> None:
        """Die Strategie beginnt neu bei 0; der Abgleich mit dem Echo ebenso. Die Leistung ohne
        Versatz bleibt: Sie ist eine Messung, kein Stand der Strategie."""
        self.strategy.reset()
        self.confirmed = None
        self.apart_since = None
        self.accepted_k = None

    def note_accepted(self, value_k: float) -> None:
        """Die Safety-Schicht nahm den eigenen Versatz ``value_k`` an. Erhöht er den Betrag
        gegenüber dem zuletzt angenommenen (oder wechselt das Vorzeichen), merkt sie sich die Zeit
        als letzte Erhöhung; die Strategie zählt ihre Wartezeit ebenfalls ab dort."""
        before = self.accepted_k or 0.0
        raised = value_k * before < 0 or abs(value_k) > abs(before) + OFFSET_SAME_K
        if value_k != 0 and raised and self.now is not None:
            self.strategy.note_raised(self.now)
        self.accepted_k = value_k

    def freeable_w(self, seen: ThermalInputs, *, wish_steps: int) -> float:
        """Die Leistung in W, die die Strategie freigeben könnte, ginge sie auf Versatz 0 zurück
        (Ruling R28): beim Wunsch ``wish_steps`` über 0 die Leistung über ``base_w``, ohne
        gelernte Leistung die ganze (wie Ruling R23 c). Bei einem Wunsch von höchstens 0, außer
        beim Heizen und Kühlen und ohne bekannte Leistung ist es 0: Weiter zurück als 0 geht sie
        für die Autos nicht.

        Ihre Grundlast bei Versatz 0 könnte ein Auto nie bekommen; zählte sie mit, wiche die
        Klimaanlage einem Auto, das mit dem Überschuss trotzdem nie startete.
        """
        power = seen.power_w
        if wish_steps <= 0 or power is None or seen.mode not in _OFFSET_MODES:
            return 0.0
        if self.base_w is None or seen.mode != self.base_mode:
            return power
        return max(0.0, power - self.base_w)

    def learn(self, seen: ThermalInputs, offset_k: float) -> None:
        """Lernt die Leistung bei Versatz 0 aus diesem Zyklus mit dem entschiedenen ``offset_k``.

        Gelernt wird nur beim Heizen und Kühlen und erst, wenn der Versatz ``THERMAL_BASE_SETTLE_S``
        lang 0 war: Die Leistung folgt ihm träge. Die erste Messung gilt sofort, danach gleitend
        mit ``THERMAL_BASE_TAU_S``. Eine neue Betriebsart beginnt ohne gelernte Leistung.
        """
        if seen.mode != self.base_mode:
            self.base_mode, self.base_w, self._base_at = seen.mode, None, None
            self._zero_since = None
        if offset_k != 0.0 or seen.mode not in _OFFSET_MODES:
            self._zero_since = None
            return
        now, power = seen.mono_now, seen.power_w
        if self._zero_since is None:
            self._zero_since = now
        if power is None or now - self._zero_since < THERMAL_BASE_SETTLE_S:
            return
        if self.base_w is None or self._base_at is None:
            self.base_w = power
        else:
            weight = min(1.0, max(0.0, now - self._base_at) / THERMAL_BASE_TAU_S)
            self.base_w += weight * (power - self.base_w)
        self._base_at = now


class Regulation:
    """Der Regelungs-Kern: je Zyklus die Befehle seiner Bausteine.

    ``charger`` ist die Laderegelung, ``battery`` der Akku-Sollwert, ``peak_guard`` der
    15-min-Wächter. ``started`` ist die Wanduhr beim Start der Regelung (``ChargeController``).
    Der Kern hält die Vorgaben des Wächters aus dem letzten Zyklus (``PeakLimits``): ``NO_LIMITS``
    nach dem Start und nach ``idle``. Dazu hält er den gültigen Slot des Plans (``active``) und den
    Eintrag ``plan`` für Health, und je Klimaanlage (``thermal.devices``) eine ``ThermalStrategy``
    mit den letzten eigenen Versätzen (``LastValues``). Der Zustand lebt nur im Speicher.
    """

    def __init__(self, cfg: Config, limits: HardLimits, *, started: datetime) -> None:
        self._cfg = cfg
        self.charger = ChargeController(cfg, limits, started=started)
        self.battery = BatteryController(cfg)
        # Der Wächter kennt die feste Grenze des Overrides nach Regel 2, ohne sein Budget.
        ceiling_w = limits.grid_setpoint_max(None)[0]
        self.peak_guard = PeakGuard(cfg.control, cfg.battery.reserve_soc_pct, ceiling_w)
        self._peak: PeakLimits = NO_LIMITS  # die Vorgaben des Wächters aus dem letzten Zyklus
        self._active: ActiveSlot | None = None  # der gültige Slot des Plans im letzten Zyklus
        # derselbe Slot, wie ihn die Absicht nennt: mit dem Planwert der Klimaanlage (Plan 5c)
        self._slot: IntentPlanSlot | None = None
        self._plan = plan_health(None, None, started)  # der Eintrag ``plan`` für Health
        self._thermal = {
            name: _Thermal(device)
            for name, device in cfg.thermal.devices.items()
            if isinstance(device, CoeOffsetConfig)
        }
        # Der Planwert gilt dem ersten Gerät der Art ``coe_offset``, für das die app plant
        # (``thermal_device`` der app); jedes weitere bekommt keinen
        self._planned_device = next(iter(self._thermal), None)
        self._thermal_last = LastValues()  # die letzten eigenen Versätze der Klimaanlagen
        self._thermal_health: dict[str, dict[str, Any]] = {}  # je Klimaanlage des letzten ``step``
        # je Klimaanlage der Versatz, den der letzte ``thermal_step`` entschied (Ruling R26)
        self._thermal_targets: dict[str, float] = {}

    def step(self, inputs: RegulationInputs) -> list[ControlCommand]:
        """Ein Regelzyklus (Präzisierung 3): die Befehle der Ladepunkte in der Reihenfolge der
        Priorität, dahinter der Override. Die Versätze der Klimaanlagen kommen danach aus
        ``thermal_step`` mit denselben Eingaben.

        Die Laderegelung bekommt Grenzen und Start-Sperre des 15-min-Wächters aus dem vorigen
        Zyklus; sein Budget aus diesem Zyklus gilt schon für den Override. Der Wächter misst
        ``inputs.quarter`` mit den Autos von ``peak_cars``, dem gewollten Netzbezug aus
        ``peak_wanted_w`` (``W`` vor dem Wächter, ungekappt; unbekannt, solange Victron nicht
        handeln kann), dem Basis-Sollwert und dem SOC; die feste Grenze von Regel 2 bekam er beim
        Bau.

        Victron ist für den Override gesperrt, wenn ``inputs.locks`` für ``victron`` Gründe nennt,
        und steuerbar nach ``charge.controllable``. Ein offener Schreibvorgang für den Override oder
        eine Übergabe von Victron (``charge.blocked``) hält nur seinen Befehl dieses Zyklus zurück.

        Als Erstes bestimmt der Zyklus den gültigen Slot des Plans der Absicht zur Wanduhr
        (``active_slot``, Plan 3c, Präzisierung 5); ``active`` und ``health()["plan"]`` zeigen ihn.
        Die Laderegelung bekommt ihn für die Ladeleistung je Auto in „Geplant“ (Plan 4b,
        Präzisierung 7). Wächter und Override bekommen ihn: Bei ``HALTEN`` und ``NETZLADEN`` ändert
        er den gewollten Netzbezug (``plan_wanted_w``, Präzisierung 7). Der Bedarf (``assess``)
        bekommt ihn mit der Akku-Unterstützung der Absicht (Präzisierung 8): Plan-Schwelle, Schalter
        und die Sperre bei ``HALTEN`` und ``NETZLADEN``; dazu den Akkuanteil für „Geplant“ und den
        übrigen Akku für „Sofort“ (Plan Akku ins Auto).
        """
        charge = inputs.charge
        plan = None if charge.intent is None else charge.intent.plan
        self._active = active_slot(plan, charge.wall_now)
        self._slot = None if plan is None or self._active is None else plan.slot_at(charge.wall_now)
        self._plan = plan_health(plan, self._active, charge.wall_now)
        commands = self.charger.step(charge, self._peak, self._active)
        guest = (GuestIntent() if charge.intent is None else charge.intent.guest).mode
        support = SupportIntent() if charge.intent is None else charge.intent.support
        views = self.charger.cars()
        cars = battery_cars(self._cfg, views, charge.wallboxes, guest)
        battery = self.battery
        need = battery.assess(
            charge.now,
            charge.grid.soc_pct,
            inputs.pv_w,
            inputs.house_w,
            cars,
            support=support,
            active=self._active,
        )
        controllable = charge.controllable.get(VICTRON) is True
        locked = bool(inputs.locks.get(VICTRON))
        base_w = inputs.base_setpoint_w
        acting = controllable and not locked
        self._peak = self.peak_guard.step(
            charge.wall_now,
            inputs.quarter,
            peak_cars(views, need),
            wanted_w=peak_wanted_w(need, acting=acting, base_w=base_w, active=self._active),
            base_w=base_w,
            soc_pct=charge.grid.soc_pct,
            target_kw=guard_target_kw(self._cfg.control, self._active),
        )
        override = battery.decide(
            charge.now,
            need,
            base_w=base_w,
            budget_w=self._peak.budget_w,
            present=charge.present,
            controllable=controllable,
            locked=locked,
            blocked=_SETPOINT in charge.blocked,
            active=self._active,
        )
        return [*commands, *override]

    def thermal_step(self, inputs: RegulationInputs) -> list[ControlCommand]:
        """Nach ``step`` mit denselben Eingaben: je Klimaanlage mit Eingaben (``inputs.thermal``)
        ihr Wunsch und, wenn er sich ändert, ihr Versatz (Plan 6a, Entscheidungen 4 bis 7).

        Die Strategie sieht den Rest nach Akku und Autos (Entscheidung 6): Einspeisung
        ``max(0, −grid_w)`` und Netzbezug ``max(0, grid_w)`` (``None``, wenn die Netzleistung
        unbekannt ist), den SOC, als Schwelle ``soc_end_pct`` des gültigen Slots (ohne Slot oder
        Wert ``strategy.battery_soc_pct``) und dessen Hinweis (ohne Slot ``neutral``), den Druck des
        15-min-Wächters (``PeakGuard.pressure``) und der Überlast (über ``NONE``) und den offenen
        Wunsch der Autos (``unmet_surplus_w``), alles aus diesem Zyklus. Ein Auto bei „Laden aus“
        zählt darin nur, wenn die Einspeisung und die Leistung, die die Klimaanlagen mit Eingaben
        freigeben könnten, seine Startleistung erreichen (Ruling R23 c): je Klimaanlage nur ihre
        Leistung über der bei Versatz 0 (``_Thermal.freeable_w``, Ruling R28; unbekannt: 0).
        Steuerbar ist sie nur, wenn der Aktor steuerbar und nicht gesperrt ist; DEGRADED kommt
        aus ``inputs.degraded``.

        Ein Befehl ``setpoint_offset`` mit dem Grund des Wunschs kommt nur, wenn der Versatz vom
        letzten eigenen abweicht, der Aktor steuerbar und nicht gesperrt ist und die Art nicht in
        ``charge.blocked`` steht. Seine Eingaben sind ``thermal_steps``, ``export_w`` und
        ``soc_pct``. Vorher gleicht ``_resync`` den Stand mit dem Echo ab.

        Den entschiedenen Versatz jeder Klimaanlage dieses Zyklus nennt ``thermal_targets``, auch
        wenn kein Befehl kommt (Ruling R26).

        Den Planwert (Plan 5c, Entscheidung 9) bekommt die Strategie aus dem gültigen Slot
        (``thermal_steps`` und ``thermal_grid_w``), nur wenn dessen ``thermal_mode`` die
        Betriebsart der Klimaanlage in diesem Zyklus ist; sonst, ohne gültigen Slot und ohne
        Planwert ``None`` (6a). Dazu die Entladeleistung des Akkus ``max(0, −battery_w)``
        (``None``, wenn die Akkuleistung unbekannt ist). Mit Planwert nennt der Befehl ihn in
        seinen Eingaben (``planned_steps``), Health je Klimaanlage in ``planned``.
        """
        charge, active, slot = inputs.charge, self._active, self._slot
        hint: ThermalHint = "neutral" if active is None else active.thermal
        grid_w, soc = charge.grid.grid_w, charge.grid.soc_pct
        export_w = None if grid_w is None else max(0.0, -grid_w)
        import_w = None if grid_w is None else max(0.0, grid_w)
        battery_w = charge.grid.battery_w
        discharge_w = None if battery_w is None else max(0.0, -battery_w)
        peak = self.peak_guard.pressure()
        phase = self.charger.overload_level() > OverloadLevel.NONE
        thermal_w = 0.0
        for name, device in self._thermal.items():
            if (seen := inputs.thermal.get(name)) is not None:
                device.spare_w = device.freeable_w(seen, wish_steps=device.strategy.wish.steps)
                thermal_w += device.spare_w
        unmet_w = self.charger.unmet_surplus_w((export_w or 0.0) + thermal_w)
        found: list[ControlCommand] = []
        health: dict[str, dict[str, Any]] = {}
        targets: dict[str, float] = {}
        for name, device in self._thermal.items():
            seen = inputs.thermal.get(name)
            if seen is None:
                continue
            actuator = thermal_actuator(name)
            key = (actuator, _OFFSET)
            blocked = key in charge.blocked
            self._resync(device, key, charge.present.get(key), blocked, seen.mono_now)
            gate = device.soc_gate_pct
            if active is not None and active.soc_end_pct is not None:
                gate = active.soc_end_pct
            acting = seen.controllable and not seen.locked
            device.now = seen.mono_now
            planned = _planned_steps(slot, seen.mode) if name == self._planned_device else None
            wish = device.strategy.step(
                ThermalObs(
                    now=seen.mono_now,
                    fresh=seen.fresh,
                    mode=seen.mode,
                    degraded=inputs.degraded,
                    controllable=acting,
                    export_w=export_w,
                    import_w=import_w,
                    soc_pct=soc,
                    soc_gate_pct=gate,
                    hint=hint,
                    peak_pressure=peak,
                    phase_pressure=phase,
                    unmet_cars_w=unmet_w,
                    planned_steps=planned,
                    planned_grid_w=0.0 if planned is None or slot is None else slot.thermal_grid_w,
                    battery_discharge_w=discharge_w,
                )
            )
            offset = offset_for(wish, seen.mode, device.step_k)
            targets[name] = offset + 0.0  # aus -0.0 wird 0.0
            device.learn(seen, offset)
            health[name] = _thermal_json(wish, hint, device, device.strategy.planned)
            if not acting or blocked or not self._thermal_last.changed(key, offset):
                continue
            command = self._thermal_last.command(actuator, _OFFSET, offset, (wish.reason,))
            facts = {"thermal_steps": float(wish.steps), "export_w": export_w, "soc_pct": soc}
            if device.strategy.planned is not None:
                facts["planned_steps"] = float(device.strategy.planned)
            found.append(ControlCommand(command, facts))
        self._thermal_health = health
        self._thermal_targets = targets
        return found

    def _resync(
        self, device: _Thermal, key: Key, present: object, blocked: bool, now: float
    ) -> None:
        """Gleicht den letzten eigenen Versatz mit dem Echo ab (Plan 6a, Entscheidung 3).

        Nach einer Lücke der Freigabe (hängender Zyklus, neuer Port des Adapters) sendet der
        Adapter 0, während Strategie und letzter eigener Wert noch den alten Versatz halten; ein
        Befehl käme dann nie, und die Schrittregel lehnte den alten Wert ab 0 ab. Zeigt das Echo
        nach einer Bestätigung (``confirmed``) länger als ``THERMAL_RESYNC_S`` einen anderen Wert,
        ohne dass ein Schreibvorgang offen ist, beginnt die Strategie neu bei 0 und vergisst den
        letzten eigenen Wert. Ohne Bestätigung (Dry-Run, vor dem ersten Senden) gibt es keinen
        Abgleich: Im Dry-Run bleibt das Echo 0.
        """
        echo, confirmed = finite_number(present), device.confirmed
        if blocked or confirmed is None or echo is None or matches(_OFFSET, confirmed, echo):
            device.apart_since = None
            return
        if device.apart_since is None:
            device.apart_since = now
        if now - device.apart_since >= THERMAL_RESYNC_S:
            device.reset()
            self._thermal_last.forget_key(key)

    def note_verdict(
        self, command: Command, verdict: Verdict | None, *, not_sent: bool = False
    ) -> None:
        """Das Urteil der Safety-Schicht über einen eigenen Befehl: zum Override an den
        Akku-Sollwert, zu einer Klimaanlage an ihre Strategie, sonst an die Laderegelung.

        Für eine Klimaanlage setzt ein Urteil mit Wert oder ``UNCHANGED`` den letzten eigenen
        Versatz (``LastValues.judged``). Lehnt die Safety-Schicht einen Versatz außer 0 ab (etwa die
        Schrittregel nach einer Lücke), beginnt ihre Strategie neu bei 0: Der Wunsch bleibt so nie
        bei einem Wert stehen, den die Schrittregel nicht zulässt. Entschieden ist dann sofort 0
        (``thermal_targets``).
        """
        key = (command.actuator, command.kind)
        if key == _SETPOINT:
            self.battery.note_verdict(command, verdict, not_sent=not_sent)
            return
        device = self._device(command.actuator)
        if device is None:
            self.charger.note_verdict(command, verdict, not_sent=not_sent)
            return
        self._thermal_last.judged(command, verdict, not_sent=not_sent)
        applied = None if verdict is None else finite_number(verdict.value)
        if applied is not None and verdict is not None and verdict.outcome in SENDABLE:
            device.note_accepted(applied)
        rejected = verdict is not None and verdict.outcome == Outcome.REJECTED
        if rejected and command.value != 0:
            device.reset()
            self._thermal_last.forget_key(key)
            name = command.actuator.removeprefix(THERMAL_PREFIX)
            if name in self._thermal_targets:
                self._thermal_targets[name] = 0.0

    def note_result(self, result: WriteResult) -> None:
        """Das Ergebnis eines abgeschlossenen Schreibvorgangs, auch eines des Keepers: zum Override
        an den Akku-Sollwert, zu einer Klimaanlage an ihre Strategie, sonst an die Laderegelung.

        Für eine Klimaanlage löscht ein gescheitertes Ergebnis den letzten eigenen Versatz
        (``LastValues.finished``); ``OK`` merkt den bestätigten Wert für den Abgleich mit dem Echo
        (``_resync``), jedes andere Ergebnis löscht ihn.
        """
        if (result.actuator, result.kind) == _SETPOINT:
            self.battery.note_result(result)
            return
        device = self._device(result.actuator)
        if device is None:
            self.charger.note_result(result)
            return
        self._thermal_last.finished(result)
        ok = result.result is DecisionResult.OK
        device.confirmed = finite_number(result.value) if ok else None
        device.apart_since = None

    def forget(self, actuators: Collection[str]) -> None:
        """Vergisst die letzten eigenen Werte dieser Aktoren, in allen Bausteinen; die Strategie
        einer Klimaanlage unter ihnen beginnt neu bei 0 (das Gerät ändert sich ohne die Regelung,
        etwa beim Wechsel Dry-Run ↔ live, nach dem der Adapter 0 hält)."""
        self.core_forget(actuators)
        self.thermal_forget(actuators)

    def core_forget(self, actuators: Collection[str]) -> None:
        """``forget`` ohne die Klimaanlagen: für die Laderegelung und den Akku."""
        self.charger.forget(actuators)
        self.battery.forget(actuators)

    def thermal_forget(self, actuators: Collection[str]) -> None:
        """``forget`` nur für die Klimaanlagen unter ``actuators``; entschieden ist für sie 0."""
        thermal = {a: device for a in actuators if (device := self._device(a)) is not None}
        for device in thermal.values():
            device.reset()
        self._thermal_last.forget(thermal.keys())
        for actuator in thermal:
            name = actuator.removeprefix(THERMAL_PREFIX)
            if name in self._thermal_targets:
                self._thermal_targets[name] = 0.0

    def idle(self, now: datetime) -> None:
        """Ein Zyklus ohne Regelung für alle drei Bausteine.

        ``now`` ist die Zeit der Safety-Schicht. Für den Wächter ist die laufende Viertelstunde
        danach lückenhaft, und seine Vorgaben verfallen (``NO_LIMITS``): Solange die Regelung ruht,
        gibt es kein Budget.
        """
        self.charger.idle(now)
        self.battery.idle()
        self.peak_guard.idle()
        self._peak = NO_LIMITS
        self._active = None
        self._slot = None
        self._plan = plan_health(None, None, now)
        self.thermal_idle()

    def thermal_idle(self) -> None:
        """Die Klimaanlagen ruhen: Ihre Strategien beginnen neu bei 0, die letzten Versätze sind
        vergessen, Health und ``thermal_targets`` nennen keine. Ohne Freigabe sendet der Adapter
        ohnehin 0."""
        for device in self._thermal.values():
            device.reset()
        self._thermal_last.forget()
        self._thermal_health = {}
        self._thermal_targets = {}

    def rest_plan(self, plan: IntentPlan | None, wall_now: datetime) -> None:
        """Legt in einem Zyklus ohne Regelung den Plan der Absicht für Health ab (nach ``idle``).

        In Ruhe bindet kein Slot (``active`` bleibt ``None``); ``health()["plan"]`` zeigt den Stand
        aus der Gültigkeit zur Wanduhr ``wall_now``, ohne Plan ``none``.
        """
        self._active = None
        self._slot = None
        self._plan = plan_health(plan, None, wall_now)

    def active(self) -> ActiveSlot | None:
        """Der gültige Slot des Plans im letzten ``step``; ``None`` ohne gültigen Plan, vor dem
        ersten ``step`` und solange die Regelung ruht."""
        return self._active

    def plan_inputs(self) -> dict[str, float]:
        """Die Eingaben, die der Plan zu den Entscheidungen des letzten ``step`` beisteuert
        (Plan 3c, Präzisierung 10): ``plan_slot_ts`` (Beginn des Slots in Sekunden seit 1970),
        ``price_ct`` (``import_ct`` des Slots, nur wenn bekannt) und ``budget_kw`` (das Ziel des
        Wächters in diesem Zyklus, nur wenn gesetzt).

        Ohne gültigen Slot, vor dem ersten ``step`` und solange die Regelung ruht ist es leer: Die
        Entscheidungen bekommen dann keinen neuen Schlüssel.
        """
        active = self._active
        if active is None:
            return {}
        found = {"plan_slot_ts": active.start.timestamp()}
        if active.import_ct is not None:
            found["price_ct"] = active.import_ct
        target_kw = self.peak_guard.target_kw()
        if target_kw is not None:
            found["budget_kw"] = target_kw
        return found

    def vehicle_wallbox(self) -> dict[str, str | None]:
        """Fahrzeug-Aktor → Wallbox aus der Zuordnung der Laderegelung, für die Safety-Schicht."""
        return self.charger.vehicle_wallbox()

    def boost_wallboxes(self) -> frozenset[str]:
        """Die Wallboxen der Autos in Boost im letzten ``step`` (``ChargeController.cars``), für
        den Boost-Anteil der Viertelstunde des Wächters (Präzisierung 9); vor dem ersten ``step``
        und nach ``idle`` keine. Eine Wallbox ohne Fahrzeug ist nie in Boost: Der Modus des Gastes
        kennt ihn nicht."""
        return frozenset(car.wallbox for car in self.charger.cars() if car.mode is Effective.BOOST)

    def health(self, wall_now: datetime) -> dict[str, Any]:
        """Der Stand des letzten Zyklus zur Wanduhr ``wall_now``: ``intent_version`` (die Version
        der Absicht des letzten ``step``, sonst ``None``), ``vehicles`` je Fahrzeug
        (``ChargeController.health``), ``battery`` (``BatteryController.health``), ``peak``
        (``PeakGuard.health``, mit ``wall_now``) und ``wallboxes`` je Wallbox
        (``ChargeController.wallbox_health``, Plan 2c-2b), ``plan`` (``plan_health``, Plan 3c) und
        ``thermal`` je Klimaanlage, die der letzte ``step`` regelte: ``wish`` (Stufen), ``reason``
        (Code) und ``hint`` (Plan 6a, Präzisierung 4) und ``planned`` (der Planwert in Stufen,
        ``None`` ohne, Plan 5c). Vor dem ersten ``step`` und solange die Regelung ruht, ist
        ``thermal`` leer."""
        return {**self.core_health(wall_now), "thermal": self.thermal_health()}

    def core_health(self, wall_now: datetime) -> dict[str, Any]:
        """``health`` ohne ``thermal``."""
        return {
            "intent_version": self.charger.intent_version,
            "vehicles": self.charger.health(),
            "battery": self.battery.health(),
            "peak": self.peak_guard.health(wall_now),
            "wallboxes": self.charger.wallbox_health(),
            "plan": self._plan,
        }

    def thermal_health(self) -> dict[str, dict[str, Any]]:
        """``health()["thermal"]``: je Klimaanlage ``wish``, ``reason``, ``hint`` und mehr
        (``_thermal_json``)."""
        return {name: dict(entry) for name, entry in self._thermal_health.items()}

    def thermal_targets(self) -> dict[str, float]:
        """Je Klimaanlage, die der letzte ``thermal_step`` regelte, der Versatz in K, den er
        entschied (``offset_for``), auch ohne Befehl (Ruling R26).

        Danach gibt die Laufzeit frei: nur für einen Versatz außer 0, sonst nimmt sie die Freigabe
        zurück, und der Adapter sendet sofort 0, auch wenn ein Schreibvorgang offen ist oder die
        Safety-Schicht den Befehl 0 nicht braucht. Eine Ablehnung (``note_verdict``) und
        ``forget`` setzen ihn auf 0; vor dem ersten ``thermal_step`` und nach ``thermal_idle`` ist
        es leer.
        """
        return dict(self._thermal_targets)

    def _device(self, actuator: str) -> _Thermal | None:
        """Die Klimaanlage des Aktors ``thermal.<name>``; ``None`` für jeden anderen."""
        if not actuator.startswith(THERMAL_PREFIX):
            return None
        return self._thermal.get(actuator.removeprefix(THERMAL_PREFIX))

    def alarms(self, wall_now: datetime) -> list[Alarm]:
        """Die Alarme der Regelung zur Wanduhr ``wall_now``: die des Wächters (``peak.exceeded``),
        solange ``wall_now`` in der Viertelstunde ihres ``since`` liegt.

        Der Wächter kennt die Zeit nur aus seinen Schritten. Ruht die Regelung, etwa in OBSERVE
        oder bei ausgeschaltetem Regelbetrieb, hielte er den Alarm über das Ende seiner
        Viertelstunde hinaus; ``RegulationLink.finish`` fragt deshalb in jedem Zyklus.
        """
        return [alarm for alarm in self.peak_guard.alarms() if wall_now < slot_end(alarm.since)]

    def grid_budget_w(self) -> float | None:
        """Das 15-min-Budget für den ``SafetyContext`` (Regel 2): ``budget_w`` des Wächters aus dem
        letzten ``step`` (Präzisierung 12); ``None`` ohne Budget und solange die Regelung ruht."""
        return self._peak.budget_w


def _thermal_json(
    wish: ThermalWish, hint: ThermalHint, device: _Thermal, planned: int | None
) -> dict[str, Any]:
    """Eine Klimaanlage in ``health()["thermal"]``: Wunsch in Stufen, Grund und Hinweis, dazu die
    gelernte Grundlast bei Versatz 0 und die zuletzt freigebbare Leistung (ganze W, ``None`` ohne)
    und der Planwert in Stufen (``None`` ohne, Plan 5c).
    """
    return {
        "wish": wish.steps,
        "reason": wish.reason,
        "hint": hint,
        "base_w": None if device.base_w is None else round(device.base_w),
        "spare_w": None if device.spare_w is None else round(device.spare_w),
        "planned": planned,
    }


def _planned_steps(slot: IntentPlanSlot | None, mode: ThermalMode) -> int | None:
    """Der Planwert des Slots in Stufen, wenn er für die Betriebsart ``mode`` gilt; sonst und ohne
    Slot ``None``. Ein Plan für Heizen wirkt so nie beim Kühlen, und ohne bekannte Betriebsart
    gilt keiner (Plan 5c, Entscheidung 9)."""
    if slot is None or slot.thermal_steps is None or slot.thermal_mode != mode:
        return None
    return slot.thermal_steps


def battery_cars(
    cfg: Config,
    cars: Sequence[CarView],
    wallboxes: Mapping[str, WallboxObs],
    guest: ChargeMode,
) -> list[BatteryCar]:
    """Die Autos für den Akku-Sollwert in der Reihenfolge von Präzisierung 4.

    Erst je Ladepunkt sein Auto (``cars``, aus ``ChargeController.cars`` in der Reihenfolge der
    Priorität; ein Gast dort mit dem Modus des Gastes) mit seinem Fahrzeug-Schlüssel, auch dem
    eines Gastes, für den Akkuanteil des Plans (Plan Akku ins Auto, Entscheidung 4). Dann jede
    Wallbox ohne Ladepunkt in der Reihenfolge von ``config.yaml``: ohne Fahrzeug, mit dem Modus des
    Gastes (``guest``), der Leistung der Wallbox und ob sie lädt. Eine Wallbox, die ``wallboxes``
    nicht nennt, ist ganz unbekannt.
    """
    found = [
        BatteryCar(car.wallbox, car.mode, car.power_w, car.charging, vehicle=car.vehicle)
        for car in cars
    ]
    taken = {car.wallbox for car in cars}
    mode = effective_of(guest)
    for key in cfg.wallboxes:
        if key in taken:
            continue
        seen = wallboxes.get(key)
        if seen is None:
            found.append(BatteryCar(key, mode, None, None))
        else:
            found.append(BatteryCar(key, mode, seen.power_w, seen.charging))
    return found


def peak_wanted_w(
    need: BatteryNeed | None,
    *,
    acting: bool,
    base_w: float | None,
    active: ActiveSlot | None,
) -> float | None:
    """Der gewollte Netzbezug ``W`` für den 15-min-Wächter in W (Präzisierung 10); ``None`` heißt
    unbekannt, dann zählt für die verlangte Senkung nur das Mittel.

    - **Victron kann nicht handeln** (``acting`` falsch: gesperrt oder nicht steuerbar): ``None``.
      Kein Override setzt dann ``Basis + W`` durch.
    - **Ohne Bedarf** (``need`` ist ``None``): ``None``; ebenso bei ``HALTEN`` und ``NETZLADEN``
      ohne Basis ``base_w``.
    - **Sonst** ``W`` vor dem Wächter, ungekappt: dasselbe ``W`` wie beim Override, mit dem
      gültigen Slot ``active`` (``cycle_wanted_w``, Plan 3c, Präzisierung 7). Was davon über der
      festen Grenze ``C`` von Regel 2 zählt, entscheidet der Wächter: mit Budget für den Akku
      nichts, denn darüber liefert der Akku (Design 2c-2, Entscheidung 3 und 5.4). Ohne Budget
      hält Regel 2 den Override bei ``C``: Liegt ``P_zul`` darunter, zählt alles, denn nur die
      ganze Senkung bringt den Netzbezug unter ``P_zul``; sonst ebenfalls nichts.
    """
    if not acting:
        return None
    return cycle_wanted_w(need, base_w, active)


def peak_cars(cars: Sequence[CarView], need: BatteryNeed | None) -> list[PeakCar]:
    """Die Autos für den 15-min-Wächter aus ``cars``, in dieser Reihenfolge: jedes, dessen
    Ladepunkt die Laderegelung regelt (``CarView.regulated``), und jedes in Boost.

    ``cars`` kommt aus ``ChargeController.cars``, in der Reihenfolge der Priorität; der Rang ist
    die Stelle darin, 0 die höchste, auch wenn ein Auto davor fehlt. Der Netzanteil ist der von
    ``need`` für die Wallbox (``BatteryNeed.grid_w``, Präzisierung 11), ohne Bedarf 0 W. Bei
    ``HALTEN`` und ``NETZLADEN`` ist er der ganze Fehlbetrag, auch in Boost: Der Akku liefert dann
    keinem Auto etwas (``BatteryController.assess``, Plan 3c, Entscheidung 3).

    - **Ungeregelt, nicht in Boost:** Das Auto fehlt. Eine Grenze wirkte an seinem Ladepunkt nicht,
      und was es ihr nicht folgte, stünde bis zum Ende der Viertelstunde aus (``pending_w``) und
      schmälerte die Drosselung der anderen. Weil es fehlt, verliert es beim Wächter still auch
      eine Grenze aus der Zeit, als sein Ladepunkt noch geregelt war; Drosselung, ``pending_w`` und
      Start-Sperre übergehen es. Sein Netzanteil steht weiter im gewollten Netzbezug ``W``.
    - **In Boost:** Das Auto zählt immer, geregelt oder nicht: Seinen Netzanteil zieht der Wächter
      als Energie in Boost ab. Gedrosselt wird es nie.

    Ein Gast zählt wie jedes Auto; an einer EVCS kann der Wächter ihn drosseln (Plan 2c-2b,
    Präzisierung 6). Wallboxen ohne Ladepunkt sind keine Autos des Wächters: Er kann sie nicht
    drosseln, ihr Netzanteil steht aber in ``W``.
    """
    grid_w: Mapping[str, float] = {} if need is None else need.grid_w
    return [
        PeakCar(
            vehicle=car.vehicle,
            wallbox=car.wallbox,
            mode=car.mode,
            rank=rank,
            power_w=car.power_w,
            grid_w=grid_w.get(car.wallbox, 0.0),
            min_w=car.min_w,
        )
        for rank, car in enumerate(cars)
        if car.regulated or car.mode is Effective.BOOST
    ]
