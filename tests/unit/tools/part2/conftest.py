"""Hilfen der Teil-2-Tests: eine EVCS hinter dem GX-Dienst, ein Tesla am Wall Connector, Sitzungen
damit und ein Ersatz für main.

Weitere Teile (Victron) legen ihre Fixtures ebenfalls hier ab.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import httpx
import pytest
from pydantic import SecretStr

from bindaems.core.actuators.evcs import EvcsActuator
from bindaems.core.actuators.tesla import TeslaActuator
from bindaems.core.control.actuator import Confirmation
from bindaems.core.state.store import StateStore
from bindaems.shared.config import Config, Secrets
from bindaems.shared.control import CommandKind, CommandValue
from bindaems.shared.domain import SignalKind, Snapshot, Value
from bindaems.shared.numbers import finite_number
from bindaems.shared.timeutil import SystemClock
from bindaems.tools import verify_part2
from bindaems.tools.part2.evcs import DEFAULT_TIMINGS, EvcsTimings
from bindaems.tools.part2.session import Part2Session
from bindaems.tools.part2.tesla import DEFAULT_TIMINGS as TESLA_TIMINGS
from bindaems.tools.part2.tesla import TeslaTimings
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
    bestätigten Strom als Phasenstrom, öffnet und schließt den Schütz (``FakeTwc``). Weitere:
    ``asked`` sammelt die gestellten Rückfragen, ``config`` ersetzt die Beispielkonfiguration,
    ``tessie_connected`` ist die Verbindung zu Tessie, ``unattended`` gilt wie ``--yes``. Nach dem
    Test stoppt die Fixture die Sitzungen: Laufende Tessie-Befehle enden, der Client schließt.
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
        unattended: bool = False,
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

        def ask(question: str) -> bool:
            if asked is not None:
                asked.append(question)
            return next(replies)

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


@dataclass
class FakePart:
    """Was ``fake_part`` in ``verify_part2`` ersetzt und beobachtet.

    Der Teil EVCS und der Teil Tesla liefern ``findings`` oder werfen ``raise_in_step``; vorher
    läuft ``before`` mit der Sitzung (etwa um Werte zu schreiben). ``restored`` sagt, ob
    ``restore`` lief, ``restore_timeouts`` nennt die Fristen, mit denen es lief, ``calls`` nennt
    ``restore`` und ``stop`` in der Reihenfolge ihrer Aufrufe; ``raise_in_restore`` bricht das
    Wiederherstellen gleich zu Beginn ab. ``sessions`` sind die gebauten Sitzungen mit ihrer
    EVCS, ``ask``, ``unattended`` und ``probe`` das, was ``main`` übergab, ``tesla_calls``
    Fahrzeug und ``asleep_check`` jedes Aufrufs des Teils Tesla.
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


@pytest.fixture
def fake_part(monkeypatch: pytest.MonkeyPatch, evcs_session) -> FakePart:
    """Ersetzt ``build_session`` (Sitzung mit ``FakeEvcs``), ``run_evcs`` und ``run_tesla``.

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

    monkeypatch.setattr(verify_part2, "build_session", build_session)
    monkeypatch.setattr(verify_part2, "run_evcs", run_evcs)
    monkeypatch.setattr(verify_part2, "run_tesla", run_tesla)
    return fake
