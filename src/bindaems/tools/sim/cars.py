"""Ladepunkte und Fahrzeuge des Simulators: EVCS mit Auto, Wall Connector und Tesla mit Tessie.

Gefälscht sind die Geräte, nicht die Adapter (Plan 2c-1, Präzisierung 16). Jedes Gerät liefert seine
Rohdaten so, wie es sie an der Anlage liefert: die EVCS ihren Registerblock und die Pfade ihres
GX-Dienstes, der Wall Connector seine Vitals, Tessie den Zustand des Fahrzeugs. In Signale
übersetzen sie die Parser der echten Adapter (``PlantModel.publish``).

- ``Car``: ein Auto mit eigenem Akku. Es zieht, was die Wallbox anbietet, höchstens bis zu seinem
  Höchststrom, auf seinen Phasen und bis zu seinem Ladelimit. Auch der Gast des Modells
  (``model.GUEST_CAR``) ist ein ``Car``: ein Auto ohne Tessie, von dem der core nur den Strom an
  der Wallbox sieht.
- ``Tesla``: ein Auto mit Tessie: Laden an/aus und Ladestrom im Auto, Schlaf, die Befehle des
  Tesla-Aktors über Tessie und die Abfragen des Tessie-Caches. Mit ``elsewhere`` steckt er an
  keiner Wallbox der Anlage, aber an einem Ladekabel woanders (einer Steckdose): Tessie meldet ihn
  „Stopped“, also angesteckt und zu Hause.
- ``Evcs``: die Victron EVCS NS mit dem angesteckten Auto, geschrieben über den GX-Dienst
  ``evcharger``.
- ``WallConnector``: der Tesla Wall Connector Gen3 mit dem angesteckten Auto.

Die Zeit kommt als Argument, der Zufall aus dem ``random.Random`` des Modells.
"""

from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal

import httpx

from bindaems.core.adapters.evcs import (
    BLOCK_COUNT,
    BLOCK_START,
    KNOWN_PRODUCT_IDS,
    REG_ACTUAL_CURRENT_X10,
    REG_FW_HIGH,
    REG_FW_LOW,
    REG_MAX_CURRENT,
    REG_MODE,
    REG_PRODUCT_ID,
    REG_SET_CURRENT,
    REG_START_STOP,
)
from bindaems.core.adapters.tessie import parse_state, poll_interval_s
from bindaems.core.state.derived import PHASES
from bindaems.shared.config import EvcsConfig, LatLon, Phase, PhaseMap, TwcConfig, VehicleConfig
from bindaems.shared.domain import Value
from bindaems.shared.retry import Backoff

DEFAULT_SOC_PCT: Final = 50.0  # SOC eines Autos, solange das Szenario keinen setzt
TESLA_LIMIT_PCT: Final = 80.0  # Ladelimit des Tesla; dort meldet er „Complete“
SLEEP_AFTER: Final = timedelta(minutes=15)  # so lange nach dem letzten Laden oder Befehl schläft er
WAKE_S: Final = 30.0  # so lange weckt Tessie ein schlafendes Auto, bevor der Befehl wirkt
TESSIE_DELAY_S: Final = (0.2, 2.5)  # Antwortzeit von Tessie in s, gleichverteilt
# der erste Strombefehl auf oder von diesem Strom geht verloren (Prüfprotokoll Teil 2)
LOST_AMPS: Final = 5
# Backoff des Tessie-Adapters nach einer gescheiterten Abfrage: 60 s, dann doppelt, höchstens 900 s
RETRY_FIRST_S: Final = 60.0
RETRY_MAX_S: Final = 900.0
# die Befehle des Tesla-Aktors (``core.actuators.tesla``), die Tessie hier kennt
COMMANDS: Final = frozenset({"start_charging", "stop_charging", "set_charging_amps"})

SET_CURRENT_DELAY: Final = timedelta(seconds=1)  # so spät übernimmt die EVCS den Sollstrom
START_STOP_DELAY: Final = timedelta(seconds=2)  # so spät Laden an/aus
PRODUCT_ID: Final = min(KNOWN_PRODUCT_IDS)  # eine Produkt-ID, die der EVCS-Adapter kennt
FIRMWARE: Final = (0x0001, 0x00FF)  # Register 5007/5008: Firmware v1.00

GxPath = Literal["SetCurrent", "StartStop"]


class Car:
    """Ein Auto mit eigenem Akku; Stellgrößen sind ``soc_pct`` und ``limit_pct``.

    Es zieht den Strom, den die Wallbox anbietet, höchstens ``accepts_a()`` je Phase, auf seinen
    ``phases`` Phasen; am Ladelimit nichts mehr. ``wallbox`` ist der Schlüssel der Wallbox, an der
    es steckt (``None``: abgesteckt), ``current_a`` der Strom je Phase im letzten Schritt.
    """

    def __init__(self, key: str, cfg: VehicleConfig) -> None:
        self.key = key
        self.phases: int = cfg.phases
        self.max_a = float(cfg.max_a)
        self.usable_kwh = cfg.usable_kwh
        self.soc_pct = DEFAULT_SOC_PCT
        self.limit_pct = 100.0
        self.wallbox: str | None = None
        self.current_a = 0.0

    @property
    def complete(self) -> bool:
        """Hat das Auto sein Ladelimit erreicht?"""
        return self.soc_pct >= self.limit_pct

    def accepts_a(self) -> float:
        """So viel Strom je Phase zieht das Auto höchstens; am Ladelimit keinen."""
        return 0.0 if self.complete else self.max_a

    def draw(self, offered_a: float, seconds: float, voltage_v: float) -> None:
        """Zieht ``seconds`` lang den angebotenen Strom, soweit es ihn nimmt, und lädt damit."""
        self.current_a = max(0.0, min(float(offered_a), self.accepts_a()))
        if self.current_a > 0:
            kwh = self.current_a * self.phases * voltage_v * seconds / 3_600_000
            self.soc_pct = min(self.limit_pct, self.soc_pct + kwh / self.usable_kwh * 100)


@dataclass
class _Request:
    """Ein Befehl an Tessie, der auf seine Antwort wartet; ``due`` ist ihre Zeit."""

    due: datetime
    name: str
    amps: int | None
    answer: asyncio.Future[httpx.Response]


class Tesla(Car):
    """Ein Tesla mit Tessie; Stellgrößen dazu ``charge_amps``, ``charge_enabled``, ``asleep`` und
    ``elsewhere``.

    - **Laden:** nur wach und mit „Laden an“ im Auto (``charge_enabled``), höchstens mit
      ``charge_amps``; Ladelimit ``TESLA_LIMIT_PCT``.
    - **Woanders:** Mit ``elsewhere`` steckt er an keiner Wallbox der Anlage, aber an einem
      Ladekabel, etwa an einer Steckdose (Plan 2c-2b, Präzisierung 12). Tessie meldet ihn dann
      „Stopped“, also angesteckt, und zu Hause (``state`` liefert immer den Ort der Anlage). So
      steht Tessie im Widerspruch zu dem, was die Wallboxen zeigen. An einer Wallbox gilt
      ``elsewhere`` nicht.
    - **Schlaf:** ``SLEEP_AFTER`` nach dem letzten Laden oder Befehl (``tick``).
    - **Tessie:** ``handle`` nimmt einen Befehl des Tesla-Aktors an. Die Antwort kommt mit
      ``resolve`` nach ``TESSIE_DELAY_S``, bei schlafendem Auto erst nach dem Wecken (``WAKE_S``
      dazu); erst dann wirkt der Befehl. Der erste Strombefehl auf oder von ``LOST_AMPS`` geht
      verloren: Tessie meldet ``result: true``, der Strom bleibt; derselbe Befehl noch einmal
      wirkt.
    - **Cache:** ``poll`` liefert die Werte, wie der Tessie-Adapter sie speichert, nur zu seinen
      Abfragezeiten, nach gescheiterten Abfragen mit seinem Backoff. ``next_poll`` ist die nächste;
      ``None`` heißt sofort.
    """

    def __init__(self, key: str, cfg: VehicleConfig, home: LatLon, rng: random.Random) -> None:
        if cfg.tessie is None:
            raise ValueError(f"Fahrzeug {key}: keine Tessie-Konfiguration")
        super().__init__(key, cfg)
        self.vin = cfg.tessie.vin
        self.limit_pct = TESLA_LIMIT_PCT
        self.charge_amps: int = cfg.max_a
        self.charge_enabled = False
        self.asleep = False
        self.elsewhere = False
        self.next_poll: datetime | None = None
        self._home = home
        self._radius_m = cfg.home_radius_m
        self._rng = rng
        self._last_active: datetime | None = None
        self._last_amps: int | None = None  # der Strom des letzten Befehls ``set_charging_amps``
        self._retry = Backoff(initial=RETRY_FIRST_S, maximum=RETRY_MAX_S)
        self._requests: list[_Request] = []

    def accepts_a(self) -> float:
        """Wie ``Car.accepts_a``, nur wach, mit „Laden an“ und höchstens ``charge_amps``."""
        if self.asleep or not self.charge_enabled:
            return 0.0
        return min(float(self.charge_amps), super().accepts_a())

    def tick(self, now: datetime) -> None:
        """Nach dem Schritt: Lädt er, ist er aktiv; sonst schläft er ``SLEEP_AFTER`` danach ein.

        Vor dem ersten Schritt gilt er als gerade aktiv.
        """
        if self.current_a > 0 or self._last_active is None:
            self._last_active = now
        elif now - self._last_active >= SLEEP_AFTER:
            self.asleep = True

    def wake(self, now: datetime) -> None:
        """Ein Befehl erreicht das Auto: Es ist wach, und die Zeit bis zum Schlaf beginnt neu."""
        self.asleep = False
        self._last_active = now

    def charging_state(self) -> str:
        """Der Ladezustand, wie Tessie ihn meldet; ohne Wallbox „Disconnected“, mit ``elsewhere``
        „Stopped“."""
        if self.wallbox is None:
            return "Stopped" if self.elsewhere else "Disconnected"
        if self.current_a > 0:
            return "Charging"
        return "Complete" if self.complete else "Stopped"

    def state(self, voltage_v: float) -> dict[str, Any]:
        """Der Zustand des Fahrzeugs, wie Tessie ihn liefert (``GET /{vin}/state``); zu Hause."""
        charging = self.current_a > 0
        return {
            "state": "asleep" if self.asleep else "online",
            "charge_state": {
                "battery_level": round(self.soc_pct),
                "charge_limit_soc": round(self.limit_pct),
                "charging_state": self.charging_state(),
                "charge_amps": self.charge_amps,
                "charge_current_request": self.charge_amps,
                "charger_actual_current": round(self.current_a),
                "charger_phases": self.phases if charging else None,
                "charger_power": round(self.current_a * self.phases * voltage_v / 1000),
                "scheduled_charging_mode": "Off",
            },
            "drive_state": {"latitude": self._home.lat, "longitude": self._home.lon},
        }

    def poll(self, now: datetime, voltage_v: float, *, reachable: bool) -> dict[str, Value] | None:
        """Die Werte einer fälligen, gelungenen Abfrage des Tessie-Caches; sonst ``None``.

        Wie im Tessie-Adapter macht ``parse_state`` aus dem Zustand die Werte, und
        ``poll_interval_s`` gibt die Zeit bis zur nächsten Abfrage: 30 s beim Laden, 60 s
        angesteckt, sonst 600 s. Ist Tessie nicht erreichbar (``reachable``), scheitert die fällige
        Abfrage, und die nächste folgt nach dem Backoff des Adapters: ``RETRY_FIRST_S``, dann
        jeweils doppelt so lange, höchstens ``RETRY_MAX_S``. Eine gelungene Abfrage setzt ihn
        zurück.
        """
        if self.next_poll is not None and now < self.next_poll:
            return None
        if not reachable:
            self.next_poll = now + timedelta(seconds=self._retry.next())
            return None
        self._retry.reset()
        values = parse_state(self.state(voltage_v), self._home, self._radius_m)
        charging, plugged = values["charging_state"], values["plugged"]
        wait_s = poll_interval_s(
            charging if isinstance(charging, str) else None,
            plugged if isinstance(plugged, bool) else None,
        )
        self.next_poll = now + timedelta(seconds=wait_s)
        return values

    async def handle(self, name: str, params: httpx.QueryParams, now: datetime) -> httpx.Response:
        """Ein Befehl an Tessie zur Zeit ``now``: die Antwort, sobald ``resolve`` sie freigibt.

        Tessie kennt hier nur die Befehle des Tesla-Aktors; ein anderer ergibt 404, ein Strom, der
        keine ganze Zahl ist, 400.
        """
        if name not in COMMANDS:
            return httpx.Response(404)
        amps: int | None = None
        if name == "set_charging_amps":
            text = params.get("amps", "")
            if not text.isdigit():
                return httpx.Response(400)
            amps = int(text)
        delay_s = self._rng.uniform(*TESSIE_DELAY_S) + (WAKE_S if self.asleep else 0.0)
        answer: asyncio.Future[httpx.Response] = asyncio.get_running_loop().create_future()
        self._requests.append(_Request(now + timedelta(seconds=delay_s), name, amps, answer))
        return await answer

    def resolve(self, now: datetime) -> None:
        """Führt die Befehle aus, deren Antwortzeit gekommen ist, und gibt ihre Antworten frei.

        Hat der Aufrufer schon aufgegeben (abgebrochen), wirkt der Befehl nicht.
        """
        due = sorted((r for r in self._requests if r.due <= now), key=lambda r: r.due)
        self._requests = [r for r in self._requests if r.due > now]
        for request in due:
            if request.answer.done():
                continue
            self._execute(request, now)
            request.answer.set_result(httpx.Response(200, json={"result": True}))

    def _execute(self, request: _Request, now: datetime) -> None:
        """Der Befehl wirkt am Auto; der erste Strombefehl auf oder von ``LOST_AMPS`` nicht."""
        self.wake(now)
        if request.name == "start_charging":
            self.charge_enabled = True
        elif request.name == "stop_charging":
            self.charge_enabled = False
        elif request.amps is not None:
            crossing = (request.amps == LOST_AMPS) != (self.charge_amps == LOST_AMPS)
            lost = crossing and self._last_amps != request.amps
            self._last_amps = request.amps
            if not lost:
                self.charge_amps = request.amps


class Wallbox(ABC):
    """Was beide Wallboxen teilen: das angesteckte Auto und seine Ströme je Phase.

    Das Auto lädt auf den ersten ``phases`` Phasen der Wallbox; ``phase_map`` legt sie auf die
    Netzphasen.
    """

    def __init__(self, key: str, phase_map: PhaseMap) -> None:
        self.key = key
        self.phase_map = phase_map
        self.car: Car | None = None

    def terminals_a(self) -> tuple[float, float, float]:
        """Der Strom je Phase der Wallbox (1, 2, 3)."""
        car = self.car
        if car is None:
            return 0.0, 0.0, 0.0
        first, second, third = (car.current_a if n < car.phases else 0.0 for n in range(3))
        return first, second, third

    def phase_a(self) -> dict[Phase, float]:
        """Der Strom je Netzphase."""
        currents = dict.fromkeys(PHASES, 0.0)
        for phase, amps in zip(self.phase_map, self.terminals_a(), strict=True):
            currents[phase] = amps
        return currents

    @abstractmethod
    def step(self, now: datetime, seconds: float, voltage_v: float) -> None:
        """``seconds`` lang laden; jede Wallbox bietet ihren Strom anders an."""


class Evcs(Wallbox):
    """Die Victron EVCS NS mit angestecktem Auto; Stellgrößen ``set_current_a``, ``start_stop``
    (1 an, 0 aus) und ``mode`` (0 manuell). Sie setzen die Werte direkt am Gerät.

    - **Schreiben** über den GX-Dienst ``evcharger`` (``write``): Den Sollstrom übernimmt sie nach
      ``SET_CURRENT_DELAY``, Laden an/aus nach ``START_STOP_DELAY``.
    - **Strom:** ``min(Sollstrom, was das Auto nimmt)``, nur bei „Laden an“.
    - **Status** wie der GX-Dienst (Präzisierung 4): 0 getrennt, 1 verbunden, 2 lädt, 3 geladen,
      also an, aber das Auto zieht nichts.
    - ``registers`` ist der Registerblock für den EVCS-Adapter, ``gx`` die Pfade des GX-Dienstes.
      ``instance`` ist die Instanz des GX-Dienstes; ohne sie sieht der Cerbo die EVCS nicht.
    """

    def __init__(self, key: str, cfg: EvcsConfig, instance: int | None) -> None:
        super().__init__(key, cfg.phase_map)
        self.cfg = cfg
        self.instance = instance
        self.mode = 0
        self.set_current_a: float = cfg.safe_a
        self.start_stop = 0
        self._writes: list[tuple[datetime, GxPath, float]] = []

    @property
    def status(self) -> int:
        """Der Status wie im GX-Dienst ``evcharger``."""
        if self.car is None:
            return 0
        if self.start_stop != 1:
            return 1
        return 2 if self.car.current_a > 0 else 3

    def write(self, path: GxPath, value: float, now: datetime) -> None:
        """Ein Auftrag über den GX-Dienst: ``SetCurrent`` in A oder ``StartStop`` (1/0)."""
        delay = SET_CURRENT_DELAY if path == "SetCurrent" else START_STOP_DELAY
        self._writes.append((now + delay, path, value))

    def step(self, now: datetime, seconds: float, voltage_v: float) -> None:
        """Übernimmt die fälligen Aufträge der Reihe nach; dann zieht das Auto seinen Strom."""
        for due, path, value in self._writes:
            if due > now:
                continue
            if path == "SetCurrent":
                self.set_current_a = value
            else:
                self.start_stop = 1 if value else 0
        self._writes = [write for write in self._writes if write[0] > now]
        if self.car is not None:
            offered = self.set_current_a if self.start_stop == 1 else 0.0
            self.car.draw(offered, seconds, voltage_v)

    def registers(self) -> list[int]:
        """Der Registerblock ab ``BLOCK_START``, wie der EVCS-Adapter ihn liest."""
        regs = [0] * BLOCK_COUNT
        current = self.car.current_a if self.car is not None else 0.0
        for address, value in (
            (REG_PRODUCT_ID, PRODUCT_ID),
            (REG_FW_HIGH, FIRMWARE[0]),
            (REG_FW_LOW, FIRMWARE[1]),
            (REG_MODE, self.mode),
            (REG_START_STOP, self.start_stop),
            (REG_SET_CURRENT, round(self.set_current_a)),
            (REG_MAX_CURRENT, self.cfg.max_a),
            (REG_ACTUAL_CURRENT_X10, round(current * 10)),
        ):
            regs[address - BLOCK_START] = value
        return regs

    def gx(self, voltage_v: float) -> dict[str, Value]:
        """Die Pfade des GX-Dienstes ``evcharger`` mit ihren Werten."""
        terminals = self.terminals_a()
        return {
            "Ac/Power": sum(terminals) * voltage_v,
            **{f"Ac/L{n}/Power": amps * voltage_v for n, amps in enumerate(terminals, 1)},
            "Current": self.car.current_a if self.car is not None else 0.0,
            "Status": self.status,
            "Mode": self.mode,
            "SetCurrent": self.set_current_a,
            "MaxCurrent": self.cfg.max_a,
            "StartStop": self.start_stop,
        }


class WallConnector(Wallbox):
    """Der Tesla Wall Connector Gen3 mit angestecktem Auto; er bietet immer ``max_a`` an.

    ``vitals`` ist die Antwort von ``/api/1/vitals`` mit den Feldern, die das Modell kennt: Fahrzeug
    verbunden, Schütz geschlossen (das Auto zieht Strom), Ströme und Spannungen je Klemme.
    """

    def __init__(self, key: str, cfg: TwcConfig) -> None:
        super().__init__(key, cfg.phase_map)
        self.cfg = cfg

    def step(self, now: datetime, seconds: float, voltage_v: float) -> None:
        """Das Auto zieht seinen Strom, höchstens ``max_a``."""
        if self.car is not None:
            self.car.draw(float(self.cfg.max_a), seconds, voltage_v)

    def vitals(self, voltage_v: float) -> dict[str, Any]:
        """Die Vitals, wie der Wall Connector sie liefert; Klemmen A, B, C sind die Phasen 1–3."""
        a, b, c = self.terminals_a()
        return {
            "vehicle_connected": self.car is not None,
            "contactor_closed": a + b + c > 0,
            "currentA_a": a,
            "currentB_a": b,
            "currentC_a": c,
            "voltageA_v": voltage_v,
            "voltageB_v": voltage_v,
            "voltageC_v": voltage_v,
        }
