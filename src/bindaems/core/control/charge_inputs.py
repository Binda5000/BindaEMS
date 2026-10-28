"""Eingaben der Laderegelung: Beobachtungen aus dem Snapshot und das Bündel eines Zyklus.

- ``WallboxObs``, ``VehicleObs`` und ``GridObs``: was die Regelung von Wallboxen und Fahrzeugen
  sieht und was sie am Netz und am Akku misst
- ``ChargeInputs``: alles, was ein Zyklus der Regelung braucht
- ``observe``: liest die Beobachtungen aus dem Snapshot und den abgeleiteten Größen
- ``wallbox_connected``: liest je Wallbox, ob sie ein Fahrzeug meldet (``WallboxObs.connected``)
- ``homes``: liest je Fahrzeug mit ``home_entity``, ob Home Assistant es zu Hause meldet
- ``ok_number``: der Wert eines Signals als endliche Zahl mit Qualität ``OK``, sonst ``None``

Ein Wert zählt nur mit Qualität ``OK`` und als endliche Zahl (``finite_number``); sonst ist er
unbekannt (``None``). Geschätzt wird hier nicht; den Ladestand schätzt ``vehicle_soc``. Was die
Beobachtungen für Zuordnung, Last und Modi bedeuten, entscheiden die folgenden Bausteine. Die
Schicht ist rein: Sie liest nur ihre Argumente.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Final

from bindaems.core.state.derived import MIN_VOLTAGE_FOR_CURRENT_V, PHASES, Derived
from bindaems.shared.config import Config, EvcsConfig, Phase, TwcConfig, home_signal, soc_signal
from bindaems.shared.control import CommandKind, CommandValue, Intent
from bindaems.shared.domain import Snapshot
from bindaems.shared.numbers import finite_number

# Status der EVCS nach dem GX-Dienst ``evcharger``: 0 getrennt, 1 verbunden, 2 lädt, 3 geladen
EVCS_DISCONNECTED: Final = 0
EVCS_CHARGING: Final = 2
EVCS_CHARGED: Final = 3
TWC_CHARGING_A: Final = 1.0  # ab dieser Summe der Phasenströme lädt der Wall Connector

_TESSIE_CHARGING: Final = "Charging"  # Ladezustand, den Tessie meldet, solange das Auto lädt
_TESSIE_COMPLETE: Final = "Complete"  # Ladezustand, wenn das Auto sein Ladelimit erreicht hat
_PHASE_COUNTS: Final = (1, 2, 3)  # so viele Phasen kann ein Fahrzeug laden
_HA_HOME: Final = "home"  # Zustand des device_tracker, wenn das Auto zu Hause ist

_State = tuple[bool | None, bool | None, bool | None]  # verbunden, lädt, geladen


@dataclass(frozen=True)
class WallboxObs:
    """Was die Regelung von einer Wallbox sieht; ``None`` heißt unbekannt.

    - ``reachable``: die Quelle der Wallbox ist erreichbar; eine Quelle, die sich nie gemeldet hat,
      zählt als erreichbar
    - ``connected``: die Wallbox meldet ein Fahrzeug
    - ``charging``: die Wallbox lädt
    - ``complete``: die Wallbox meldet „geladen“; das kann nur die EVCS
    - ``phase_a``: der gemessene Strom je Netzphase, alle drei oder gar nicht
    - ``power_w``: die gemessene Leistung
    - ``total_kwh``: der Zähler der Wallbox (``wallbox.<Schlüssel>.total_kwh``)
    """

    reachable: bool
    connected: bool | None
    charging: bool | None
    complete: bool | None
    phase_a: Mapping[Phase, float] | None
    power_w: float | None
    total_kwh: float | None = None


@dataclass(frozen=True)
class VehicleObs:
    """Was die Regelung über Tessie von einem Fahrzeug sieht; ``None`` heißt unbekannt.

    Von einem Fahrzeug ohne Tessie sieht sie nur den Ladestand aus Home Assistant
    (``soc_pct``, ``soc_at``); alles andere ist wie in ``NO_VEHICLE_DATA``.

    - ``reachable``: die Verbindung zu Tessie steht
    - ``plugged``: das Fahrzeug ist angesteckt
    - ``charging``, ``complete``: sein Ladezustand ist „Charging“ bzw. „Complete“
    - ``asleep``: das Fahrzeug ist nicht online
    - ``at_home``: das Fahrzeug ist zu Hause
    - ``actual_current_a``: der Strom, den es zieht, je Phase
    - ``phases``: die Zahl der Phasen, auf denen es lädt: 1, 2 oder 3
    - ``plugged_at``: die Wanduhr der Abfrage, aus der ``plugged`` stammt (der Zeitstempel des
      Werts), nicht die des Ansteckens; ``None``, solange ``plugged`` unbekannt ist
    - ``soc_pct``: der gemessene Ladestand (``vehicle.<Schlüssel>.soc_pct``); bei Tessie ist ein
      Wert nach dessen Frische veraltet und damit unbekannt
    - ``soc_at``: der Zeitstempel dieses Werts (wie bei ``plugged_at``); ``None`` ohne Ladestand
    - ``charge_limit_pct``: das Ladelimit im Fahrzeug; nur mit Tessie
    - ``climate_on``: das Fahrzeug klimatisiert (Klima an oder Vorkonditionierung, aus
      ``vehicle.<Schlüssel>.climate_on``); nur mit Tessie
    """

    reachable: bool
    plugged: bool | None
    charging: bool | None
    complete: bool | None
    asleep: bool | None
    at_home: bool | None
    actual_current_a: float | None
    phases: int | None
    plugged_at: datetime | None = None
    soc_pct: float | None = None
    soc_at: datetime | None = None
    charge_limit_pct: float | None = None
    climate_on: bool | None = None


# Ein Fahrzeug ohne Tessie: Die Regelung sieht nichts von ihm, nur die Wallbox sieht es
# (``observe`` ergänzt nur den Ladestand aus Home Assistant).
NO_VEHICLE_DATA: Final = VehicleObs(False, None, None, None, None, None, None, None)


@dataclass(frozen=True)
class GridObs:
    """Was die Regelung am Netz und am Akku sieht; ``None`` heißt unbekannt.

    - ``phase_import_a``: der Netzstrom je Phase, positiv bei Bezug; alle drei Phasen
    - ``battery_feed_a``: was der Multi je Phase gerade aus dem Akku liefert (Design 5.1); alle
      drei Phasen
    - ``grid_w``: die Netzleistung, positiv bei Bezug
    - ``battery_w``: die Akkuleistung, positiv beim Laden
    - ``soc_pct``: der SOC des Akkus
    """

    phase_import_a: Mapping[Phase, float | None]
    battery_feed_a: Mapping[Phase, float | None]
    grid_w: float | None
    battery_w: float | None
    soc_pct: float | None


@dataclass(frozen=True)
class ChargeInputs:
    """Die Eingaben eines Zyklus der Laderegelung.

    - ``now``: die Zeit auf der Uhr der Safety-Schicht; auf ihr laufen alle Zeiten der Regelung
    - ``wall_now``: die Wanduhr, für ``boost_until`` und den Kalendertag des Weckkontingents
    - ``grid``, ``wallboxes``, ``vehicles``: die Beobachtungen (``observe``); die Schlüssel sind die
      aus ``config.yaml``
    - ``present``: der Wert, den der Aktor gerade hat, je Aktor und Art; was fehlt, ist unbekannt
    - ``controllable``: je Aktor-ID, ob er steuerbar ist
    - ``wakeable``: je Aktor-ID, ob ein Start mit Weckerlaubnis möglich ist
    - ``blocked``: Aktor und Art mit offenem Schreibvorgang oder in Übergabe; dafür kommt in diesem
      Zyklus kein Befehl
    - ``intent``: die gültige Absicht; ``None``, solange es keine gibt
    - ``accepted_at``: wann die Absicht angenommen wurde; ``None``, wenn das unbekannt ist
    - ``homes``: je Fahrzeug mit ``home_entity``, ob Home Assistant es zu Hause meldet
      (``homes``); ``None`` heißt unbekannt, ein Fahrzeug ohne ``home_entity`` fehlt
    - ``commands_hour``: je Aktor eines Fahrzeugs mit Tessie die Befehle im gleitenden
      Stundenfenster der Safety-Schicht (``SafetyLayer.hourly_count``); andere Aktoren fehlen
    """

    now: datetime
    wall_now: datetime
    grid: GridObs
    wallboxes: Mapping[str, WallboxObs]
    vehicles: Mapping[str, VehicleObs]
    present: Mapping[tuple[str, CommandKind], CommandValue]
    controllable: Mapping[str, bool]
    wakeable: Mapping[str, bool]
    blocked: frozenset[tuple[str, CommandKind]]
    intent: Intent | None
    accepted_at: datetime | None
    homes: Mapping[str, bool | None] = field(default_factory=dict)
    commands_hour: Mapping[str, int] = field(default_factory=dict)


def observe(
    snap: Snapshot,
    derived: Derived,
    cfg: Config,
    connected: Callable[[str], bool | None],
) -> tuple[GridObs, dict[str, WallboxObs], dict[str, VehicleObs]]:
    """Die Beobachtungen zu Netz, Wallboxen und Fahrzeugen, in der Reihenfolge von ``config.yaml``.

    ``connected`` sagt zu einem Namen der Quelle, ob sie verbunden ist; ``None`` heißt, sie hat sich
    nie gemeldet. Die Quelle einer Wallbox heißt wie ihr Schlüssel, die eines Fahrzeugs mit Tessie
    ``tessie:<Schlüssel>``. Eine Wallbox ist erreichbar, solange ihre Quelle nicht getrennt ist; ein
    Fahrzeug nur, solange Tessie verbunden ist. Ein Fahrzeug ohne ``tessie`` ist
    ``NO_VEHICLE_DATA`` mit seinem Ladestand aus Home Assistant, ohne dass ``connected`` gefragt
    wird.
    """
    wallboxes = {
        key: _wallbox_obs(snap, derived, key, wallbox, reachable=connected(key) is not False)
        for key, wallbox in cfg.wallboxes.items()
    }
    vehicles = {
        key: (
            _vehicle_without_tessie(snap, key)
            if vehicle.tessie is None
            else _vehicle_obs(snap, key, reachable=connected(f"tessie:{key}") is True)
        )
        for key, vehicle in cfg.vehicles.items()
    }
    return _grid_obs(snap, derived), wallboxes, vehicles


def wallbox_connected(snap: Snapshot, cfg: Config) -> dict[str, bool | None]:
    """Ob die Wallbox ein Fahrzeug meldet, je Wallbox, wie in ``WallboxObs.connected``.

    ``None`` heißt unbekannt; die Reihenfolge ist die von ``config.yaml``.
    """
    return {
        key: (_twc_state(snap, key) if isinstance(wallbox, TwcConfig) else _evcs_state(snap, key))[
            0
        ]
        for key, wallbox in cfg.wallboxes.items()
    }


def homes(snap: Snapshot, cfg: Config) -> dict[str, bool | None]:
    """Ob Home Assistant ein Fahrzeug zu Hause meldet, je Fahrzeug mit ``home_entity``.

    ``True`` heißt Zustand ``home``, ``False`` jeder andere Text. Alles andere ist unbekannt
    (``None``): ``unknown`` und ``unavailable`` (der HA-Adapter macht sie zu ``None``), ein
    veralteter Wert und ein Wert, der kein Text ist. Ein Fahrzeug ohne ``home_entity`` fehlt,
    auch eins mit Tessie; die Reihenfolge ist die von ``config.yaml``.
    """
    result: dict[str, bool | None] = {}
    for key, vehicle in cfg.vehicles.items():
        if vehicle.home_entity is not None:
            state = _text(snap, home_signal(key))
            result[key] = None if state is None else state == _HA_HOME
    return result


def _grid_obs(snap: Snapshot, derived: Derived) -> GridObs:
    """Netzstrom und -leistung aus den abgeleiteten Größen, Akku aus ``battery.*``."""
    return GridObs(
        phase_import_a={p: finite_number(derived.phase_import_a.get(p)) for p in PHASES},
        battery_feed_a={p: _battery_feed_a(snap, p) for p in PHASES},
        grid_w=finite_number(derived.grid_w),
        battery_w=ok_number(snap, "battery.power_w"),
        soc_pct=ok_number(snap, "battery.soc_pct"),
    )


def _battery_feed_a(snap: Snapshot, phase: Phase) -> float | None:
    """Der Strom, den der Multi auf der Phase aus dem Akku liefert: ``max(0, aus − ein) / U``.

    Unbekannt, wenn die Leistung am Ein- oder Ausgang fehlt oder die Spannung fehlt oder unter
    ``MIN_VOLTAGE_FOR_CURRENT_V`` liegt.
    """
    line = phase.lower()
    out_w = ok_number(snap, f"vebus.{line}.ac_out_power_w")
    in_w = ok_number(snap, f"vebus.{line}.ac_in_power_w")
    volts = ok_number(snap, f"grid.{line}.voltage_v")
    if out_w is None or in_w is None or volts is None or volts < MIN_VOLTAGE_FOR_CURRENT_V:
        return None
    return max(0.0, out_w - in_w) / volts


def _wallbox_obs(
    snap: Snapshot,
    derived: Derived,
    key: str,
    wallbox: EvcsConfig | TwcConfig,
    *,
    reachable: bool,
) -> WallboxObs:
    """Die Beobachtung einer Wallbox; Strom und Leistung kommen aus den abgeleiteten Größen."""
    state = _twc_state(snap, key) if isinstance(wallbox, TwcConfig) else _evcs_state(snap, key)
    connected, charging, complete = state
    return WallboxObs(
        reachable=reachable,
        connected=connected,
        charging=charging,
        complete=complete,
        phase_a=_phase_a(derived.wallbox_phase_a.get(key)),
        power_w=finite_number(derived.wallbox_w.get(key)),
        total_kwh=ok_number(snap, f"wallbox.{key}.total_kwh"),
    )


def _evcs_state(snap: Snapshot, key: str) -> _State:
    """Verbunden, lädt und geladen nach dem Status der EVCS; ohne Status alle drei unbekannt.

    Die Codes sind ``EVCS_DISCONNECTED`` (getrennt), ``EVCS_CHARGING`` (lädt) und ``EVCS_CHARGED``
    (geladen); 1 heißt verbunden. Jeder andere Code heißt „verbunden, lädt nicht“; das ist eine
    Annahme (Präzisierung 4).
    """
    status = ok_number(snap, f"wallbox.{key}.status")
    if status is None:
        return None, None, None
    return status != EVCS_DISCONNECTED, status == EVCS_CHARGING, status == EVCS_CHARGED


def _twc_state(snap: Snapshot, key: str) -> _State:
    """Verbunden, lädt und geladen nach den Signalen des Wall Connectors; geladen meldet er nie."""
    prefix = f"wallbox.{key}."
    return _flag(snap, prefix + "vehicle_connected"), _twc_charging(snap, prefix), None


def _twc_charging(snap: Snapshot, prefix: str) -> bool | None:
    """Der Wall Connector lädt, wenn die Summe seiner Phasenströme ``TWC_CHARGING_A`` erreicht.

    Fehlt ein Strom, gilt, ob das Schütz geschlossen ist; das ist unbekannt, wenn es fehlt.
    """
    currents = [ok_number(snap, f"{prefix}{p.lower()}.current_a") for p in PHASES]
    known = [current for current in currents if current is not None]
    if len(known) < len(currents):
        return _flag(snap, prefix + "contactor_closed")
    return sum(known) >= TWC_CHARGING_A


def _phase_a(currents: Mapping[Phase, float] | None) -> dict[Phase, float] | None:
    """Der gemessene Strom der Wallbox je Netzphase: alle drei oder, wenn einer fehlt, keiner.

    Ein Strom, der fehlt oder keine endliche Zahl ist, macht den ganzen Strom unbekannt.
    """
    if currents is None:
        return None
    result: dict[Phase, float] = {}
    for phase in PHASES:
        amps = finite_number(currents.get(phase))
        if amps is None:
            return None
        result[phase] = amps
    return result


def _vehicle_obs(snap: Snapshot, key: str, *, reachable: bool) -> VehicleObs:
    """Die Beobachtung eines Fahrzeugs mit Tessie aus seinen Signalen ``vehicle.<Schlüssel>.*``."""
    prefix = f"vehicle.{key}."
    state = _text(snap, prefix + "charging_state")
    online = _flag(snap, prefix + "online")
    return VehicleObs(
        reachable=reachable,
        plugged=_flag(snap, prefix + "plugged"),
        charging=None if state is None else state == _TESSIE_CHARGING,
        complete=None if state is None else state == _TESSIE_COMPLETE,
        asleep=None if online is None else not online,
        at_home=_flag(snap, prefix + "at_home"),
        actual_current_a=ok_number(snap, prefix + "actual_current_a"),
        phases=_phase_count(ok_number(snap, prefix + "phases")),
        plugged_at=_flag_at(snap, prefix + "plugged"),
        soc_pct=ok_number(snap, soc_signal(key)),
        soc_at=_number_at(snap, soc_signal(key)),
        charge_limit_pct=ok_number(snap, prefix + "charge_limit_pct"),
        climate_on=_flag(snap, prefix + "climate_on"),
    )


def _vehicle_without_tessie(snap: Snapshot, key: str) -> VehicleObs:
    """``NO_VEHICLE_DATA`` mit dem Ladestand, den Home Assistant unter ``soc_signal`` meldet."""
    return replace(
        NO_VEHICLE_DATA,
        soc_pct=ok_number(snap, soc_signal(key)),
        soc_at=_number_at(snap, soc_signal(key)),
    )


def _phase_count(value: float | None) -> int | None:
    """Die Zahl der Phasen, wenn sie 1, 2 oder 3 ist; alles andere ist unbekannt."""
    if value is None or value not in _PHASE_COUNTS:
        return None
    return int(value)


def ok_number(snap: Snapshot, signal: str) -> float | None:
    """Der Wert des Signals als endliche Zahl mit Qualität ``OK``; sonst ``None`` (unbekannt)."""
    return finite_number(snap.ok(signal))


def _flag(snap: Snapshot, signal: str) -> bool | None:
    """Der Wert des Signals, wenn er ein Wahrheitswert mit Qualität ``OK`` ist; sonst ``None``."""
    value = snap.ok(signal)
    return value if isinstance(value, bool) else None


def _flag_at(snap: Snapshot, signal: str) -> datetime | None:
    """Der Zeitstempel des Signals, wenn sein Wert ein Wahrheitswert mit Qualität ``OK`` ist.

    Das ist die Wanduhr der Abfrage, aus der der Wert stammt; sonst ``None``.
    """
    reading = snap.get(signal)
    return reading.ts if reading is not None and _flag(snap, signal) is not None else None


def _number_at(snap: Snapshot, signal: str) -> datetime | None:
    """Der Zeitstempel des Signals, wenn sein Wert eine endliche Zahl mit Qualität ``OK`` ist."""
    reading = snap.get(signal)
    return reading.ts if reading is not None and ok_number(snap, signal) is not None else None


def _text(snap: Snapshot, signal: str) -> str | None:
    """Der Wert des Signals, wenn er ein Text mit Qualität ``OK`` ist; sonst ``None``."""
    value = snap.ok(signal)
    return value if isinstance(value, str) else None
