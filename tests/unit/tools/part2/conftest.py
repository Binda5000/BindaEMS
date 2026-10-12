"""Hilfen der Teil-2-Tests: eine EVCS hinter dem GX-Dienst, Sitzungen damit und ein Ersatz für main.

Weitere Teile (Tesla, Victron) legen ihre Fixtures ebenfalls hier ab.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field

import pytest

from bindaems.core.actuators.evcs import EvcsActuator
from bindaems.core.state.store import StateStore
from bindaems.shared.config import Config, Secrets
from bindaems.shared.domain import SignalKind, Value
from bindaems.shared.timeutil import SystemClock
from bindaems.tools import verify_part2
from bindaems.tools.part2.evcs import DEFAULT_TIMINGS, EvcsTimings
from bindaems.tools.part2.session import Part2Session
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


@dataclass
class FakePart:
    """Was ``fake_part`` in ``verify_part2`` ersetzt und beobachtet.

    Der Teil EVCS liefert ``findings`` oder wirft ``raise_in_step``; vorher läuft ``before`` mit
    der Sitzung (etwa um Werte zu schreiben). ``restored`` sagt, ob ``restore`` lief, ``calls``
    nennt ``restore`` und ``stop`` in der Reihenfolge ihrer Aufrufe; ``raise_in_restore`` bricht
    das Wiederherstellen gleich zu Beginn ab. ``sessions`` sind die gebauten Sitzungen mit ihrer
    EVCS, ``ask``, ``unattended`` und ``probe`` das, was ``main`` übergab.
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


@pytest.fixture
def fake_part(monkeypatch: pytest.MonkeyPatch, evcs_session) -> FakePart:
    """Ersetzt ``build_session`` (Sitzung mit ``FakeEvcs``) und ``run_evcs`` in ``verify_part2``.

    Setzt dazu gültige Secrets in der Umgebung.
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
        session, evcs = evcs_session(unattended=unattended)
        fake.sessions.append((session, evcs))
        fake.ask, fake.unattended = ask, unattended
        restore, stop = session.restore, session.stop

        async def recorded_restore(*args: object, **kwargs: object) -> list[Finding]:
            fake.restored = True
            fake.calls.append("restore")
            if fake.raise_in_restore is not None:
                raise fake.raise_in_restore
            return await restore(*args, **kwargs)  # type: ignore[arg-type]

        async def recorded_stop() -> None:
            fake.calls.append("stop")
            await stop()

        session.restore = recorded_restore  # type: ignore[method-assign]
        session.stop = recorded_stop  # type: ignore[method-assign]
        return session

    async def run_evcs(
        session: Part2Session,
        cfg: Config,
        key: str,
        *,
        probe: Callable[[], Awaitable[list[Finding]]],
        timings: EvcsTimings = DEFAULT_TIMINGS,
    ) -> list[Finding]:
        fake.probe = probe
        if fake.before is not None:
            await fake.before(session)
        if fake.raise_in_step is not None:
            raise fake.raise_in_step
        return list(fake.findings)

    monkeypatch.setattr(verify_part2, "build_session", build_session)
    monkeypatch.setattr(verify_part2, "run_evcs", run_evcs)
    return fake
