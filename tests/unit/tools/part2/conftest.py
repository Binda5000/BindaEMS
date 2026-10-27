"""Hilfen der Teil-2-Tests: eine EVCS hinter dem GX-Dienst, ein Tesla am Wall Connector, ein Cerbo
mit Victron-Anlage, Sitzungen damit und ein Ersatz für main."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Final

import httpx
import pytest
from pydantic import SecretStr
from tests.unit.core.adapters.conftest import FakeDatagram

from bindaems.core.actuators.evcs import EvcsActuator
from bindaems.core.actuators.tesla import TeslaActuator
from bindaems.core.actuators.victron import VictronActuator
from bindaems.core.adapters.victron_mqtt import (
    MAX_DISCHARGE_PATH,
    MIN_SOC_PATH,
    SETPOINT_OVERRIDE_PATH,
)
from bindaems.core.control.actuator import Confirmation
from bindaems.core.state.store import StateStore
from bindaems.shared.coe import CoeValue, decode, encode
from bindaems.shared.config import CoeChannel, Config, Secrets
from bindaems.shared.control import CommandKind, CommandValue
from bindaems.shared.domain import SignalKind, Snapshot, Value
from bindaems.shared.numbers import finite_number
from bindaems.shared.timeutil import SystemClock
from bindaems.tools import verify_part2
from bindaems.tools.part2.evcs import DEFAULT_TIMINGS, EvcsTimings
from bindaems.tools.part2.session import Part2Session
from bindaems.tools.part2.tesla import DEFAULT_TIMINGS as TESLA_TIMINGS
from bindaems.tools.part2.tesla import TeslaTimings
from bindaems.tools.part2.thermal import ThermalSession, ThermalTimings, build_thermal_session
from bindaems.tools.part2.victron import DEFAULT_TIMINGS as VICTRON_TIMINGS
from bindaems.tools.part2.victron import VictronTimings
from bindaems.tools.verify_checks import Finding


class FakeEvcs:
    """EVCS hinter dem GX-Dienst für die Teil-2-Tests.

    ``send`` merkt sich jeden Auftrag in ``writes`` und übernimmt ihn nach ``delay_s`` als
    GX-Wert in den Speicher (``SetCurrent`` → ``gx_set_current_a``, ``StartStop`` →
    ``gx_start_stop``), solange ``follow`` gilt. ``change_by_hand`` setzt den Strom sofort, wie
    eine Änderung in VictronConnect.

    Mit ``accept=False`` nimmt der Cerbo keinen Auftrag an (``send`` liefert ``False`` und merkt
    sich nichts), wie der Victron-Adapter zwischen zwei Sitzungen; ``connected`` ist die Verbindung
    zum Cerbo, die der Aktor abfragt. ``lose`` Aufträge gehen noch verloren: angenommen und
    gemerkt, aber nie übernommen, wie in der Warteschlange einer Sitzung, die gerade endet. Mit
    ``charging_power_w`` meldet die EVCS beim Übernehmen von ``StartStop`` diese Leistung bei „an“
    und 0 W bei „aus“, wie bei einem angesteckten, ladenden Fahrzeug.
    """

    def __init__(
        self,
        store: StateStore,
        *,
        delay_s: float = 0.01,
        follow: bool = True,
        accept: bool = True,
        charging_power_w: float | None = None,
    ) -> None:
        self.store, self.delay_s, self.follow = store, delay_s, follow
        self.accept, self.charging_power_w = accept, charging_power_w
        self.connected = True
        self.lose = 0
        self.writes: list[tuple[str, float | int | None]] = []

    def send(self, path: str, value: float | int | None) -> bool:
        if not self.accept:
            return False
        self.writes.append((path, value))
        if self.lose > 0:
            self.lose -= 1
            return True
        if self.follow:
            signal = "gx_set_current_a" if path.endswith("SetCurrent") else "gx_start_stop"
            asyncio.get_running_loop().call_later(self.delay_s, self._apply, signal, value)
        return True

    def change_by_hand(self, current_a: int) -> None:
        self._apply("gx_set_current_a", current_a)

    def _apply(self, signal: str, value: float | int | None) -> None:
        self.store.update(f"wallbox.evcs.{signal}", value, source="victron", kind=SignalKind.STATE)
        if signal == "gx_start_stop" and self.charging_power_w is not None:
            power = self.charging_power_w if value else 0.0
            self.store.update(
                "wallbox.evcs.power_w", power, source="victron", kind=SignalKind.MEASUREMENT
            )


async def no_probe() -> list[Finding]:
    """Die Watchdog-Probe ohne Befunde."""
    return []


@pytest.fixture
def evcs_session(cfg: Config) -> Callable[..., tuple[Part2Session, FakeEvcs]]:
    """Baut eine Sitzung mit einer ``FakeEvcs`` (Instanz 40) und einem Betreiber, der antwortet.

    ``answers`` sind die Antworten auf die Rückfragen in ihrer Reihenfolge, ``state`` ergänzt oder
    ersetzt die Startwerte (manueller Modus, Sollstrom 16 A). Mit ``hand_change_a`` stellt der
    Betreiber den Strom von Hand um, sobald er die Rückfrage zur fremden Änderung bejaht.

    Erweiterungen für weitere Fälle: ``react`` ordnet einer Rückfrage (genauer Text) eine
    Handlung zu, die nach einem Ja an der EVCS geschieht; ``asked`` sammelt die gestellten
    Rückfragen; ``config`` ersetzt die Beispielkonfiguration; ``accept`` und ``charging_power_w``
    gehen an die ``FakeEvcs``; ``resend_s`` (sonst der Standard der Sitzung) und ``unattended``
    (wie ``--yes``) an die Sitzung.
    """

    def build(
        *,
        answers: Sequence[bool] = (),
        state: Mapping[str, Value] | None = None,
        follow: bool = True,
        hand_change_a: int | None = None,
        react: Mapping[str, Callable[[FakeEvcs], object]] | None = None,
        asked: list[str] | None = None,
        config: Config | None = None,
        accept: bool = True,
        charging_power_w: float | None = None,
        resend_s: float | None = None,
        unattended: bool = False,
    ) -> tuple[Part2Session, FakeEvcs]:
        settings = config if config is not None else cfg
        store = StateStore(SystemClock())
        store.register_source("victron", 5.0)
        store.set_connected("victron", True)
        start = {"wallbox.evcs.gx_mode": 0, "wallbox.evcs.gx_set_current_a": 16} | dict(state or {})
        for name, value in start.items():
            store.update(name, value, source="victron", kind=SignalKind.STATE)
        evcs = FakeEvcs(store, follow=follow, accept=accept, charging_power_w=charging_power_w)
        replies = iter(answers)

        def ask(question: str) -> bool:
            if asked is not None:
                asked.append(question)
            reply = next(replies)
            if (
                reply
                and hand_change_a is not None
                and question.startswith("Bitte jetzt den Ladestrom")
            ):
                evcs.change_by_hand(hand_change_a)
            if reply and react is not None and question in react:
                react[question](evcs)
            return reply

        def connected() -> bool:
            return evcs.connected

        actuator = EvcsActuator("evcs", settings.wallboxes["evcs"], 40, evcs.send, connected)
        extra = {} if resend_s is None else {"resend_s": resend_s}
        session = Part2Session(
            settings,
            store=store,
            adapters=[],
            actuators={actuator.id: actuator},
            ask=ask,
            poll_s=0.01,
            unattended=unattended,
            **extra,
        )
        return session, evcs

    return build


# Die Befehle von Tessie für das Fahrzeug der Beispielkonfiguration
VIN = "5YJ3E7EB0MF000000"
AMPS_URL = f"https://api.tessie.com/{VIN}/command/set_charging_amps"
START_URL = f"https://api.tessie.com/{VIN}/command/start_charging"
STOP_URL = f"https://api.tessie.com/{VIN}/command/stop_charging"

# Das Fahrzeug steht zu Hause, ist angesteckt und online und lädt mit 16 A am Wall Connector.
TESLA_START: Final[Mapping[str, Value]] = {
    "vehicle.tesla.online": True,
    "vehicle.tesla.at_home": True,
    "vehicle.tesla.plugged": True,
    "vehicle.tesla.soc_pct": 64.0,
    "vehicle.tesla.charge_amps": 16,
    "vehicle.tesla.charging_state": "Charging",
    "wallbox.twc.vehicle_connected": True,
    "wallbox.twc.contactor_closed": True,
    "wallbox.twc.l1.current_a": 16.0,
    "wallbox.twc.l2.current_a": 16.0,
    "wallbox.twc.l3.current_a": 16.0,
}


def stray_requests(respx_mock):
    """Eine Route, die jede Anfrage auffängt, die keine andere Route trifft, und mit 500 antwortet.

    Sie muss die letzte Route sein. Ohne sie wirft respx für eine Anfrage ohne Treffer einen
    ``AssertionError`` (``AllMockedAssertionError``), ohne sie aufzuzeichnen. Der Aktor schluckt
    ihn als gescheiterten Befehl, und ``respx_mock.calls`` bliebe leer, auch wenn gesendet wurde.
    Mit ihr zeigen ``route.called`` und ``respx_mock.calls``, dass etwas gesendet wurde, das der
    Test nicht erwartet.
    """
    return respx_mock.route().respond(500)


def put_signal(store: StateStore, name: str, value: Value) -> None:
    """Legt ein Signal des Teils Tesla in den Speicher, wie es sein Adapter täte.

    ``vehicle.…`` kommt von Tessie (Messwerte), ``wallbox.twc.…`` vom Wall Connector: die
    Ströme als Messwerte, Fahrzeug und Schütz als Zustände.
    """
    if name.startswith("vehicle."):
        store.update(name, value, source="tessie:tesla", kind=SignalKind.MEASUREMENT)
        return
    kind = SignalKind.MEASUREMENT if name.endswith(".current_a") else SignalKind.STATE
    store.update(name, value, source="twc", kind=kind)


def tessie_reports(store: StateStore, *, every_s: float, until_s: float) -> None:
    """Tessie meldet sich alle ``every_s`` neu, bis ``until_s`` nach dem Aufruf; dann fällt es aus.

    Jedes Signal des Fahrzeugs bekommt dabei einen neuen Zeitstempel, wie bei einer Abfrage des
    Adapters. Fällt Tessie aus, trennt der Adapter die Quelle nicht: Die Werte bleiben bis zu ihrer
    Frischegrenze gültig, nur ihr Zeitstempel bleibt stehen.
    """
    loop = asyncio.get_running_loop()
    end = loop.time() + until_s

    def report() -> None:
        if loop.time() >= end:
            return
        for name, reading in store.snapshot().matching("vehicle.tesla.").items():
            put_signal(store, name, reading.value)
        loop.call_later(every_s, report)

    loop.call_later(every_s, report)


class FakeTwc:
    """Der Wall Connector für die Teil-2-Tests des Teils Tesla; ``store`` ist der der Sitzung.

    Seine Signale (``wallbox.twc.…``) liegen dort. ``follow`` ruft der Tesla-Aktor, sobald Tessie
    einen Befehl bestätigt hat: Mit ``follows`` übernimmt der Wall Connector nach ``delay_s`` den
    Sollstrom (``CURRENT``) bzw. öffnet und schließt den Schütz (``CHARGING``). Bei geschlossenem
    Schütz ziehen alle drei Phasen den Sollstrom ``limit_a``, sonst fließt nichts. Der Tessie-Cache
    (``vehicle.tesla.…``) bleibt, wie er ist; in der Wirklichkeit hinkt er dem Fahrzeug 30–60 s
    hinterher.
    """

    def __init__(
        self,
        store: StateStore,
        *,
        limit_a: float,
        closed: bool,
        follows: bool,
        delay_s: float = 0.01,
    ) -> None:
        self.store, self.limit_a, self.closed = store, limit_a, closed
        self.follows, self.delay_s = follows, delay_s

    def follow(self, kind: CommandKind, value: CommandValue) -> None:
        if self.follows:
            asyncio.get_running_loop().call_later(self.delay_s, self._apply, kind, value)

    def _apply(self, kind: CommandKind, value: CommandValue) -> None:
        if kind == CommandKind.CURRENT and isinstance(value, int | float):
            self.limit_a = float(value)
        elif kind == CommandKind.CHARGING:
            self.closed = value is True
        put_signal(self.store, "wallbox.twc.contactor_closed", self.closed)
        amps = self.limit_a if self.closed else 0.0
        for phase in (1, 2, 3):
            put_signal(self.store, f"wallbox.twc.l{phase}.current_a", amps)


class WiredTesla(TeslaActuator):
    """Ein ``TeslaActuator``, nach dessen bestätigtem Befehl der Wall Connector folgt (``twc``).

    ``sent`` hält jeden Aufruf von ``send`` fest, auch einen abgelehnten: So zeigt ein Test, dass
    nie ein Befehl versucht wurde, ob Tessie ihn nun erreicht hätte oder nicht.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.twc: FakeTwc | None = None
        self.sent: list[tuple[CommandKind, CommandValue]] = []

    def send(self, kind: CommandKind, value: CommandValue) -> str | None:
        self.sent.append((kind, value))
        return super().send(kind, value)

    def confirm(
        self, kind: CommandKind, value: CommandValue, snap: Snapshot
    ) -> tuple[Confirmation, str]:
        result = super().confirm(kind, value, snap)
        if result[0] == Confirmation.CONFIRMED and self.twc is not None:
            self.twc.follow(kind, value)
        return result


@pytest.fixture
async def tesla_session(
    cfg: Config, respx_mock
) -> AsyncIterator[Callable[..., tuple[Part2Session, FakeTwc]]]:
    """Baut eine Sitzung mit einem Tesla über Tessie am Wall Connector und einem Betreiber.

    Der ``TeslaActuator`` hängt an einem ``httpx.AsyncClient`` unter respx: Der Test legt die
    Antworten von Tessie mit ``respx_mock`` fest, die Aktor-URLs sind ``AMPS_URL``, ``START_URL``
    und ``STOP_URL``. Der Aktor ist ``session.actuators["vehicle.tesla"]``.

    ``answers`` sind die Antworten auf die Rückfragen in ihrer Reihenfolge, ``state`` ergänzt oder
    ersetzt die Startwerte (``TESLA_START``). Mit ``twc_follows`` übernimmt der Wall Connector den
    bestätigten Strom als Phasenstrom, öffnet und schließt den Schütz (``FakeTwc``).

    Nach einer Rückfrage steht der Event-Loop, und mit ihm der Adapter von Tessie. Die Fixture
    stellt nach, was danach geschieht: Tessie meldet nach jeder Antwort ``tessie_delay_s`` später
    neue Werte, jedes Signal des Fahrzeugs bekommt einen neuen Zeitstempel (``tessie_polls``).
    ``after_answer`` ordnet einer Rückfrage (genauer Text) eine Handlung zu, die dieser Meldung
    vorausgeht, etwa dass das Fahrzeug inzwischen schläft.

    Weitere: ``asked`` sammelt die gestellten Rückfragen, ``config`` ersetzt die
    Beispielkonfiguration, ``tessie_connected`` ist die Verbindung zu Tessie, ``unattended`` gilt
    wie ``--yes``. ``check_s`` und ``settle_s`` sind Prüfzeit und Wartezeit des Aktors im
    Tessie-Cache (``verify_s``, ``settle_s``); mit ``check_s`` 0 zählt die Bestätigung von Tessie
    wie vor dieser Prüfung, denn der Cache folgt hier keinem Befehl. Nach dem Test stoppt die
    Fixture die Sitzungen: Laufende Tessie-Befehle enden, der Client schließt.
    """
    built: list[Part2Session] = []

    def build(
        *,
        answers: Sequence[bool] = (),
        state: Mapping[str, Value] | None = None,
        twc_follows: bool = False,
        asked: list[str] | None = None,
        config: Config | None = None,
        tessie_connected: bool = True,
        tessie_polls: bool = True,
        tessie_delay_s: float = 0.02,
        after_answer: Mapping[str, Callable[[FakeTwc], object]] | None = None,
        unattended: bool = False,
        check_s: float = 0.0,
        settle_s: float = 0.0,
    ) -> tuple[Part2Session, FakeTwc]:
        settings = config if config is not None else cfg
        store = StateStore(SystemClock())
        store.register_source("tessie:tesla", 900.0)
        store.set_connected("tessie:tesla", tessie_connected)
        store.register_source("twc", 10.0)
        store.set_connected("twc", True)
        start = TESLA_START | dict(state or {})
        for name, value in start.items():
            put_signal(store, name, value)
        limit = finite_number(start["vehicle.tesla.charge_amps"])
        closed = start["wallbox.twc.contactor_closed"] is True
        twc = FakeTwc(
            store, limit_a=limit if limit is not None else 16.0, closed=closed, follows=twc_follows
        )
        replies = iter(answers)

        def report(question: str) -> None:
            """Tessie fragt wieder ab: erst die Handlung zur Rückfrage, dann neue Zeitstempel."""
            action = (after_answer or {}).get(question)
            if action is not None:
                action(twc)
            if tessie_polls:
                for name, reading in store.snapshot().matching("vehicle.tesla.").items():
                    put_signal(store, name, reading.value)

        def ask(question: str) -> bool:
            if asked is not None:
                asked.append(question)
            reply = next(replies)
            if tessie_polls or question in (after_answer or {}):
                asyncio.get_running_loop().call_later(tessie_delay_s, report, question)
            return reply

        client = httpx.AsyncClient()
        car = WiredTesla(
            "tesla",
            settings.vehicles["tesla"],
            SecretStr("tessie-token"),
            client,
            lambda: store.connected("tessie:tesla") is True,
            clock=SystemClock().now,
        )
        car.twc = twc
        car.verify_s, car.settle_s = check_s, settle_s
        session = Part2Session(
            settings,
            store=store,
            adapters=[],
            actuators={car.id: car},
            ask=ask,
            poll_s=0.01,
            cleanup=[client.aclose],
            unattended=unattended,
        )
        built.append(session)
        return session, twc

    yield build
    for session in built:
        await session.stop()


# Die Schreibwege des Cerbo und das Signal, in dem der Wert danach steht
_CERBO_SIGNALS: Final = {
    SETPOINT_OVERRIDE_PATH: "ess.override.setpoint",
    MAX_DISCHARGE_PATH: "ess.max_discharge_w",
    MIN_SOC_PATH: "ess.min_soc_pct",
}
_CERBO_STATE_PREFIXES: Final = ("dess.", "ess.")  # alles andere sind Messwerte

# Die Anlage zu Beginn: Dynamic ESS aus, kein Override, die Entladegrenze steht bei 5000 W, der Akku
# entlädt mit 50 W, das Netz liefert je Phase 100 W (zusammen 300 W). Keine EVCS lädt.
VICTRON_START: Final[Mapping[str, Value]] = {
    "dess.mode": 0,
    "ess.override.setpoint": None,
    "ess.max_discharge_w": 5000.0,
    "ess.min_soc_pct": 20.0,
    "ess.grid_setpoint_w": 50.0,
    "grid.l1.power_w": 100.0,
    "grid.l2.power_w": 100.0,
    "grid.l3.power_w": 100.0,
    "grid.l1.current_a": 0.5,
    "grid.l2.current_a": 0.5,
    "grid.l3.current_a": 0.5,
    "battery.power_w": -50.0,
}

CerboPlant = Callable[["FakeCerbo", str, float | int | None], object]


class FakeCerbo:
    """Der Cerbo für die Teil-2-Tests des Teils Victron.

    ``send`` merkt sich jeden Auftrag in ``writes`` und übernimmt ihn nach ``delay_s`` in den
    Speicher (``ess.override.setpoint``, ``ess.max_discharge_w``, ``ess.min_soc_pct``), solange
    ``follow`` gilt. Ein geschriebenes ``None`` erreicht den Speicher wie beim echten Adapter als
    ungültiger Wert: ``present`` liest ihn bei verbundenem Cerbo als „kein Override“. Mit
    ``null_clears=False`` bleibt der Override nach ``None`` stehen, mit ``ignore_nulls`` nur
    nach den nächsten so vielen ``None``. Ohne ``accept`` oder bei getrenntem Cerbo nimmt er
    keinen Auftrag an (``send`` liefert ``False``, wie der Adapter ohne Sitzung). Die Messwerte
    für Netz und Akku sind, was der Test hineinlegt (``put``); ``plant`` ist die Anlage dahinter:
    Sie läuft nach jedem übernommenen Auftrag und darf Messwerte ändern.

    ``restart`` trennt den Cerbo wie ein Neustart und lässt ihn danach seine Werte erneut melden,
    auf Wunsch erst ``republish_s`` nach dem Verbinden.
    """

    def __init__(
        self,
        store: StateStore,
        *,
        delay_s: float = 0.01,
        follow: bool = True,
        accept: bool = True,
        null_clears: bool = True,
        plant: CerboPlant | None = None,
    ) -> None:
        self.store, self.delay_s, self.follow = store, delay_s, follow
        self.accept, self.null_clears, self.plant = accept, null_clears, plant
        self.ignore_nulls = 0
        self.connected = True
        self.values: dict[str, Value] = {}  # was der Cerbo meldet, auch nach einem Neustart
        self.writes: list[tuple[str, float | int | None]] = []

    def send(self, path: str, value: float | int | None) -> bool:
        if path not in _CERBO_SIGNALS:
            raise ValueError(f"Schreibpfad nicht freigegeben: {path}")
        if not self.accept or not self.connected:
            return False
        self.writes.append((path, value))
        if self.follow:
            asyncio.get_running_loop().call_later(self.delay_s, self._apply, path, value)
        return True

    def put(self, name: str, value: Value) -> None:
        """Der Cerbo meldet ``name``; Zustände und Einstellungen gelten, Messwerte altern."""
        self.values[name] = value
        kind = (
            SignalKind.STATE if name.startswith(_CERBO_STATE_PREFIXES) else SignalKind.MEASUREMENT
        )
        self.store.update(name, value, source="victron", kind=kind)

    def disconnect(self) -> None:
        self.connected = False
        self.store.set_connected("victron", False)

    def reconnect(self, *, republish_s: float = 0.0) -> None:
        """Die Verbindung steht wieder; der Cerbo meldet alle Werte erneut, nach ``republish_s``."""
        self.connected = True
        self.store.set_connected("victron", True)
        if republish_s > 0:
            asyncio.get_running_loop().call_later(republish_s, self.republish)
        else:
            self.republish()

    def restart(
        self,
        *,
        down_s: float,
        keeps_override: bool,
        comes_back: bool = True,
        republish_s: float = 0.0,
    ) -> None:
        """Ein Neustart: getrennt, nach ``down_s`` wieder da; der Override bleibt, wenn er bleibt.

        ``republish_s`` ist die Zeit zwischen dem Verbinden und den erneut gemeldeten Werten.
        """
        self.disconnect()
        if not keeps_override:
            self.values["ess.override.setpoint"] = None
        if comes_back:
            asyncio.get_running_loop().call_later(
                down_s, partial(self.reconnect, republish_s=republish_s)
            )

    def republish(self) -> None:
        """Der Cerbo meldet alle Werte erneut, wie nach dem Verbinden."""
        for name, value in dict(self.values).items():
            self.put(name, value)

    def _apply(self, path: str, value: float | int | None) -> None:
        if value is None:
            if not self.null_clears:
                return  # der Override bleibt stehen
            if self.ignore_nulls > 0:
                self.ignore_nulls -= 1
                return
        self.put(_CERBO_SIGNALS[path], value)
        if self.plant is not None:
            self.plant(self, path, value)


@pytest.fixture
def victron_session(cfg: Config) -> Callable[..., tuple[Part2Session, FakeCerbo]]:
    """Baut eine Sitzung mit einem ``VictronActuator`` über einem ``FakeCerbo`` und einem Betreiber.

    ``answers`` sind die Antworten auf die Rückfragen in ihrer Reihenfolge, ``state`` ergänzt oder
    ersetzt die Startwerte (``VICTRON_START``, Dynamic ESS aus); ``without`` nimmt Signale aus den
    Startwerten heraus. Der Cerbo meldet sich wie der Adapter: Zustände gelten, solange er
    verbunden ist, und nach einem erneuten Verbinden erst, wenn er sie wieder meldet.

    Weitere: ``null_clears``, ``follow`` und ``accept`` gehen an den ``FakeCerbo``, ``plant`` ist
    die Anlage dahinter (Messwerte nach einem Auftrag). ``react`` ordnet einer Rückfrage (genauer
    Text) eine Handlung zu, die nach einem Ja am Cerbo geschieht; ``asked`` sammelt die gestellten
    Rückfragen; ``config`` ersetzt die Beispielkonfiguration; ``resend_s`` und ``unattended`` (wie
    ``--yes``) gehen an die Sitzung.

    ``freshness_s`` ist die Frische der Messwerte des Cerbo (sonst 600 s, an der Anlage 5 s). Mit
    ``stall_s`` meldet sich der Cerbo laufend (alle 0,02 s ein Lebenszeichen wie seine vielen
    Nachrichten), der Event-Loop steht während jeder Rückfrage so lange wie an der Konsole, und
    der Cerbo meldet sich erst kurz nach der Antwort wieder.
    """

    def build(
        *,
        answers: Sequence[bool] = (),
        state: Mapping[str, Value] | None = None,
        without: Sequence[str] = (),
        freshness_s: float = 600.0,
        stall_s: float = 0.0,
        null_clears: bool = True,
        follow: bool = True,
        accept: bool = True,
        plant: CerboPlant | None = None,
        react: Mapping[str, Callable[[FakeCerbo], object]] | None = None,
        asked: list[str] | None = None,
        config: Config | None = None,
        resend_s: float | None = None,
        unattended: bool = False,
    ) -> tuple[Part2Session, FakeCerbo]:
        settings = config if config is not None else cfg
        store = StateStore(SystemClock())
        # wie der Adapter: aktivitätsbasiert und mit Neuverbinden ungültig; die Frische ist hier
        # lang, damit ein langsamer Lauf die Messwerte nicht altern lässt
        store.register_source(
            "victron", freshness_s, activity_based=True, invalidate_on_reconnect=True
        )
        store.set_connected("victron", True)
        cerbo = FakeCerbo(store, follow=follow, accept=accept, null_clears=null_clears, plant=plant)
        start = {name: value for name, value in VICTRON_START.items() if name not in without}
        for name, value in (start | dict(state or {})).items():
            cerbo.put(name, value)
        replies = iter(answers)
        if stall_s > 0:

            def alive() -> None:
                store.touch("victron")
                asyncio.get_running_loop().call_later(0.02, alive)

            asyncio.get_running_loop().call_later(0.02, alive)

        def ask(question: str) -> bool:
            if asked is not None:
                asked.append(question)
            reply = next(replies)
            if reply and react is not None and question in react:
                react[question](cerbo)
            if stall_s > 0:
                time.sleep(stall_s)  # der Betreiber liest; der Adapter steht mit dem Event-Loop
                asyncio.get_running_loop().call_later(0.02, cerbo.republish)
            return reply

        actuator = VictronActuator(cerbo.send, lambda: cerbo.connected)
        extra = {} if resend_s is None else {"resend_s": resend_s}
        session = Part2Session(
            settings,
            store=store,
            adapters=[],
            actuators={actuator.id: actuator},
            ask=ask,
            poll_s=0.01,
            unattended=unattended,
            **extra,
        )
        return session, cerbo

    return build


@dataclass
class FakePart:
    """Was ``fake_part`` in ``verify_part2`` ersetzt und beobachtet.

    Die Teile EVCS, Tesla und Victron liefern ``findings`` oder werfen ``raise_in_step``; vorher
    läuft ``before`` mit der Sitzung (etwa um Werte zu schreiben). ``restored`` sagt, ob
    ``restore`` lief, ``restore_timeouts`` nennt die Fristen, mit denen es lief, ``calls`` nennt
    ``restore`` und ``stop`` in der Reihenfolge ihrer Aufrufe; ``raise_in_restore`` bricht das
    Wiederherstellen gleich zu Beginn ab. ``sessions`` sind die gebauten Sitzungen mit ihrer
    EVCS, ``ask``, ``unattended`` und ``probe`` das, was ``main`` übergab, ``tesla_calls``
    Fahrzeug und ``asleep_check`` jedes Aufrufs des Teils Tesla, ``victron_timings`` die Fristen
    jedes Aufrufs des Teils Victron.
    """

    raise_in_step: type[BaseException] | None = None
    raise_in_restore: type[BaseException] | None = None
    findings: list[Finding] = field(
        default_factory=lambda: [Finding("17.2-6", "EVCS bereit", "ok", "Instanz 40")]
    )
    before: Callable[[Part2Session], Awaitable[object]] | None = None
    restored: bool = False
    calls: list[str] = field(default_factory=list)
    sessions: list[tuple[Part2Session, FakeEvcs]] = field(default_factory=list)
    ask: Callable[[str], bool] | None = None
    unattended: bool | None = None
    probe: Callable[[], Awaitable[list[Finding]]] | None = None
    restore_timeouts: list[float | None] = field(default_factory=list)
    tesla_calls: list[tuple[str, bool]] = field(default_factory=list)
    victron_timings: list[VictronTimings] = field(default_factory=list)


@pytest.fixture
def fake_part(monkeypatch: pytest.MonkeyPatch, evcs_session) -> FakePart:
    """Ersetzt ``build_session`` (Sitzung mit ``FakeEvcs``) und die Teile EVCS, Tesla und Victron.

    Setzt dazu gültige Secrets in der Umgebung, ohne Tessie-Token. Die Sitzung entsteht aus der
    Konfiguration, die ``main`` geladen hat.
    """
    fake = FakePart()
    for name in (
        "MQTT_PASSWORD",
        "TESSIE_TOKEN",
        "HA_TOKEN",
        "INFLUX_PASSWORD",
        "HA_MQTT_PASSWORD",
    ):
        monkeypatch.delenv(f"BINDAEMS_{name}", raising=False)
    monkeypatch.setenv("BINDAEMS_INTERNAL_TOKEN", "x" * 32)

    def build_session(
        cfg: Config, secrets: Secrets, *, ask: Callable[[str], bool], unattended: bool = False
    ) -> Part2Session:
        session, evcs = evcs_session(unattended=unattended, config=cfg)
        fake.sessions.append((session, evcs))
        fake.ask, fake.unattended = ask, unattended
        restore, stop = session.restore, session.stop

        async def recorded_restore(*args: object, **kwargs: object) -> list[Finding]:
            fake.restored = True
            fake.calls.append("restore")
            timeout = args[0] if args else kwargs.get("timeout_s")
            fake.restore_timeouts.append(timeout if isinstance(timeout, float) else None)
            if fake.raise_in_restore is not None:
                raise fake.raise_in_restore
            return await restore(*args, **kwargs)  # type: ignore[arg-type]

        async def recorded_stop() -> None:
            fake.calls.append("stop")
            await stop()

        session.restore = recorded_restore  # type: ignore[method-assign]
        session.stop = recorded_stop  # type: ignore[method-assign]
        return session

    async def run_part(session: Part2Session) -> list[Finding]:
        """Was jeder ersetzte Teil tut: ``before`` laufen lassen, werfen oder ``findings`` geben."""
        if fake.before is not None:
            await fake.before(session)
        if fake.raise_in_step is not None:
            raise fake.raise_in_step
        return list(fake.findings)

    async def run_evcs(
        session: Part2Session,
        cfg: Config,
        key: str,
        *,
        probe: Callable[[], Awaitable[list[Finding]]],
        timings: EvcsTimings = DEFAULT_TIMINGS,
    ) -> list[Finding]:
        fake.probe = probe
        return await run_part(session)

    async def run_tesla(
        session: Part2Session,
        cfg: Config,
        key: str,
        *,
        asleep_check: bool = False,
        timings: TeslaTimings = TESLA_TIMINGS,
    ) -> list[Finding]:
        fake.tesla_calls.append((key, asleep_check))
        return await run_part(session)

    async def run_victron(
        session: Part2Session, cfg: Config, *, timings: VictronTimings = VICTRON_TIMINGS
    ) -> list[Finding]:
        fake.victron_timings.append(timings)
        return await run_part(session)

    monkeypatch.setattr(verify_part2, "build_session", build_session)
    monkeypatch.setattr(verify_part2, "run_evcs", run_evcs)
    monkeypatch.setattr(verify_part2, "run_tesla", run_tesla)
    monkeypatch.setattr(verify_part2, "run_victron", run_victron)
    return fake


CMI_HOST: Final = "192.168.81.92"  # das C.M.I. der Beispielkonfiguration


class FakeCmi:
    """C.M.I. und UVR hinter dem Ersatzport aus Task 3 (``FakeDatagram``) für den Teil Klimaanlage.

    ``open`` ist die ``PortFactory`` des Adapters. Ab dem Öffnen schickt das C.M.I. alle
    ``every_s`` ein Paket mit den Werten der Empfangstabelle (``values``, als Zahlen in ihrer
    Einheit bzw. Wahrheitswerte; das Echo kommt aus ``echo_k``). Ein Versatz an Knoten 60,
    Ausgang 1 wird zum Echo, solange ``echoes`` gilt, und sofort mit einem Paket gemeldet. Mit
    ``uvr_timeout_s`` fällt das Echo so lange nach dem letzten empfangenen Versatz auf 0.
    ``omit`` nennt Werte, die das C.M.I. nicht sendet. ``offsets`` sind alle empfangenen Versätze
    in K mit ihrer Zeit (``time.monotonic``).
    """

    def __init__(
        self,
        cfg: Config,
        *,
        every_s: float = 0.05,
        echoes: bool = True,
        uvr_timeout_s: float | None = None,
        omit: Sequence[str] = (),
    ) -> None:
        device = cfg.thermal.devices["klima"]
        self.receive = device.receive.channels()
        self.own = (device.own_node, device.own_output)
        self.every_s, self.echoes, self.uvr_timeout_s = every_s, echoes, uvr_timeout_s
        self.omit = set(omit)
        self.values: dict[str, float | bool] = {
            "actual_c": 22.5,
            "setpoint_c": 23.0,
            "power_kw": 1.2,
            "cmd_on": True,
            "cooling": True,
            "venting": False,
            "heating": False,
            "running": True,
        }
        self.echo_k = 0.0
        self.offsets: list[tuple[float, float]] = []
        self.ports: list[FakeDatagram] = []
        self._task: asyncio.Task[None] | None = None

    async def open(
        self, on_datagram: Callable[[bytes, tuple[str, int]], None], port: int
    ) -> FakeDatagram:
        cmi = self

        class _Port(FakeDatagram):
            def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
                super().sendto(data, addr)
                cmi.received(data)

            def close(self) -> None:
                super().close()
                if cmi._task is not None:
                    cmi._task.cancel()

        opened = _Port(on_datagram)
        self.ports.append(opened)
        self._task = asyncio.create_task(self._report())
        return opened

    def received(self, data: bytes) -> None:
        for value in decode(data):
            if (value.node, value.output) == self.own and value.analog:
                self.offsets.append((time.monotonic(), value.raw / 10))
                if self.echoes:
                    self.echo_k = value.raw / 10
                    asyncio.get_running_loop().call_soon(self.send)

    def sent_k(self) -> list[float]:
        """Die empfangenen Versätze in K, ohne Zeit."""
        return [value for _, value in self.offsets]

    def send(self) -> None:
        port = self.ports[-1]
        if port.closed:
            return
        values = [self._value(key, channel) for key, channel in self.receive.items()]
        packet = [value for value in values if value is not None]
        if packet:
            port.on_datagram(encode(packet), (CMI_HOST, 5442))

    def _value(self, key: str, channel: CoeChannel) -> CoeValue | None:
        if key in self.omit:
            return None
        if channel.digital is not None:
            return CoeValue(channel.node, channel.digital, False, 43, int(bool(self.values[key])))
        assert channel.analog is not None
        number = self.echo_k if key == "offset_active_k" else float(self.values[key])
        unit = channel.unit if channel.unit is not None else 1
        factor = 10.0 if unit in (1, 7) else float(channel.scale or 1.0)
        return CoeValue(channel.node, channel.analog, True, unit, round(number * factor))

    async def _report(self) -> None:
        while True:
            last = self.offsets[-1][0] if self.offsets else None
            if (
                self.uvr_timeout_s is not None
                and last is not None
                and time.monotonic() - last >= self.uvr_timeout_s
            ):
                self.echo_k = 0.0
            self.send()
            await asyncio.sleep(self.every_s)


@pytest.fixture
def thermal_session(cfg: Config) -> Callable[..., tuple[ThermalSession, FakeCmi]]:
    """Baut eine Sitzung des Teils Klimaanlage mit einem ``FakeCmi`` und einem Betreiber, der
    antwortet (``answers`` in ihrer Reihenfolge; ``asked`` sammelt die Fragen). Die übrigen
    Schlüsselwörter gehen an das ``FakeCmi``; ``unattended`` wie ``--yes``."""

    def build(
        *,
        answers: Sequence[bool] = (),
        asked: list[str] | None = None,
        unattended: bool = False,
        **cmi_options: Any,
    ) -> tuple[ThermalSession, FakeCmi]:
        cmi = FakeCmi(cfg, **cmi_options)
        replies = iter(answers)

        def ask(question: str) -> bool:
            if asked is not None:
                asked.append(question)
            return next(replies)

        session = build_thermal_session(
            cfg, "klima", ask=ask, unattended=unattended, port_factory=cmi.open, poll_s=0.01
        )
        return session, cmi

    return build


FAST_THERMAL: Final = ThermalTimings(values_s=1.0, echo_s=0.5, timeout_s=0.6)
