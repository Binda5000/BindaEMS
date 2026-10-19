"""Laufzeit mit der Klimaanlage: CoE-Adapter, Aktor, Freigabe je Zyklus und Health (Plan 6a)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from tests.helpers import T0
from tests.integration.conftest import GOOD_STATE, run
from tests.unit.core.adapters.conftest import FakeDatagram, GatedSleep

from bindaems.core.adapters.coe import CoeAdapter
from bindaems.core.control.last_values import ControlCommand
from bindaems.core.control.regulation import Regulation
from bindaems.core.runtime import CoreRuntime
from bindaems.shared.coe import CoeValue, decode, encode
from bindaems.shared.config import CoeOffsetConfig, Config
from bindaems.shared.control import ActuatorMode, Command, CommandKind, Intent
from bindaems.shared.decisions import Decision, DecisionResult
from bindaems.shared.domain import SignalKind, Snapshot
from bindaems.shared.timeutil import ManualClock

THERMAL = "thermal.klima"
OFFSET = CommandKind.SETPOINT_OFFSET
CMI = ("192.168.81.92", 5442)
RECEIVE = {
    "actual_c": {"node": 56, "analog": 1, "unit": 1},
    "setpoint_c": {"node": 56, "analog": 2, "unit": 1},
    "offset_active_k": {"node": 56, "analog": 3, "unit": 1},
    "cooling": {"node": 56, "digital": 2},
    "venting": {"node": 56, "digital": 3},
    "heating": {"node": 56, "digital": 4},
    "running": {"node": 56, "digital": 5},
}


def packet(offset_k: float, *, running: bool = True, cooling: bool = True) -> bytes:
    """Ein Paket des C.M.I. nach der Empfangstabelle des Beispiels (Knoten 56)."""
    return encode(
        [
            CoeValue(56, 1, True, 1, 240),  # Ist 24,0 °C
            CoeValue(56, 2, True, 1, 230 + round(offset_k * 10)),  # Soll 23,0 °C plus Versatz
            CoeValue(56, 3, True, 1, round(offset_k * 10)),  # Echo des Versatzes
            CoeValue(56, 4, True, 10, 123),  # 1,23 kW
            CoeValue(56, 1, False, 0, 1),  # cmd_on
            CoeValue(56, 2, False, 0, int(cooling)),
            CoeValue(56, 3, False, 0, 0),  # venting
            CoeValue(56, 4, False, 0, 0),  # heating
            CoeValue(56, 5, False, 0, int(running)),
        ]
    )


def allow_thermal_live(cfg: Config) -> Config:
    klima = cfg.thermal.devices["klima"].model_copy(update={"live_allowed": True})
    thermal = cfg.thermal.model_copy(update={"devices": {"klima": klima}})
    return cfg.model_copy(update={"thermal": thermal})


def intent(version: int, mode: ActuatorMode) -> Intent:
    return Intent(version=version, control=True, actuators={THERMAL: mode})


class CoeRuntime:
    """Laufzeit mit echtem CoE-Adapter am Ersatzport; die Regelung fordert ``wanted`` an."""

    def __init__(self, rt: CoreRuntime, clock: ManualClock, device: CoeOffsetConfig) -> None:
        self.rt, self.clock = rt, clock
        self.ports: list[FakeDatagram] = []
        # Die Uhr des Adapters folgt der Uhr der Laufzeit: Die Freigabe läuft mit ihr ab.
        self.adapter = CoeAdapter(
            "klima",
            device,
            rt.store,
            self._open,
            clock=self.mono,
            sleep=GatedSleep(self.mono),
        )
        rt.adapters.append(self.adapter)
        self.wanted: list[float] = []  # was die Regelung je Zyklus anfordert
        self.task: asyncio.Task[None] | None = None

    def mono(self) -> float:
        return self.clock.now().timestamp()

    async def _open(
        self, on_datagram: Callable[[bytes, tuple[str, int]], None], listen_port: int
    ) -> FakeDatagram:
        port = FakeDatagram(on_datagram)
        self.ports.append(port)
        return port

    async def start(self) -> None:
        self.task = asyncio.create_task(self.adapter.run())
        for _ in range(10):
            await asyncio.sleep(0)

    def echo(self, offset_k: float, **kw: bool) -> None:
        """Die UVR meldet den Versatz ``offset_k`` (und die übrigen Werte)."""
        self.ports[-1].on_datagram(packet(offset_k, **kw), CMI)

    def sent(self) -> list[int]:
        """Die gesendeten Versätze in Zehntel-K."""
        return [decode(data)[0].raw for data, _ in self.ports[-1].sent]

    def thermal_decisions(self) -> list[Decision]:
        return [d for d in self.rt.decisions(T0, 1000)[0] if d.actuator == THERMAL]


@pytest.fixture
async def runtime_with_coe(runtime_env, monkeypatch) -> AsyncIterator[Callable[..., Any]]:
    envs: list[CoeRuntime] = []

    def regulate(self: Regulation, inputs: Any) -> list[ControlCommand]:
        env = envs[-1]
        return [ControlCommand(Command(THERMAL, OFFSET, value), {}) for value in env.wanted]

    monkeypatch.setattr(Regulation, "step", regulate)

    async def build(*, live: bool = True, **kw: Any) -> CoeRuntime:
        rt, _, clock = runtime_env(state=GOOD_STATE, configure=allow_thermal_live, **kw)
        device = rt._cfg.thermal.devices["klima"]
        assert isinstance(device, CoeOffsetConfig)
        env = CoeRuntime(rt, clock, device)
        envs.append(env)
        await env.start()
        mode = ActuatorMode.LIVE if live else ActuatorMode.DRY_RUN
        rt.submit_intent(intent(1, mode))
        return env

    yield build
    for env in envs:
        if env.task is not None:
            env.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await env.task


def regulate_until_control(env: CoeRuntime, offset_k: float = 0.0) -> None:
    """Zyklen bis CONTROL; die UVR meldet dabei ``offset_k``."""
    for _ in range(40):
        env.echo(offset_k)
        run(env.rt, env.clock, 1)
        if env.rt.health().mode == "CONTROL":
            return
    raise AssertionError("CONTROL nicht erreicht")


def permitted(env: CoeRuntime) -> bool:
    """Gilt die Freigabe? Mit ihr hält der Adapter einen neuen Wert, ohne sie 0."""
    held = env.adapter.held_k
    env.adapter.set_offset(0.5)
    result = env.adapter.held_k == 0.5
    env.adapter.set_offset(held)
    return result


async def test_runtime_builds_the_thermal_actuator(runtime_with_coe) -> None:
    env = await runtime_with_coe()
    assert THERMAL in env.rt.actuators
    assert env.rt.actuators[THERMAL].id == THERMAL


async def test_runtime_permits_only_when_live(runtime_with_coe) -> None:
    env = await runtime_with_coe(live=False)
    env.wanted = [0.5]
    regulate_until_control(env)
    run(env.rt, env.clock, 2)
    # Dry-Run: Die Regelung hat entschieden, das Log zeigt es, hinaus geht nur 0
    decisions = env.thermal_decisions()
    assert decisions and {d.result for d in decisions} == {DecisionResult.DRY_RUN}
    assert {d.applied for d in decisions} == {0.5}
    assert set(env.sent()) == {0}
    assert not permitted(env)
    # Live: Die Laufzeit gibt frei, bevor der Ausführer sendet. Die Erhöhung im Dry-Run zählt für
    # die Schrittregel (Plan 6a, Entscheidung 8): erst nach step_interval_s die nächste.
    env.rt.submit_intent(intent(2, ActuatorMode.LIVE))
    env.clock.advance(600)
    env.echo(0.0)
    run(env.rt, env.clock, 1)
    assert env.sent()[-1] == 5
    assert permitted(env)
    assert (THERMAL, OFFSET) in env.rt.executor.pending()
    env.echo(0.5)
    run(env.rt, env.clock, 1)
    assert env.thermal_decisions()[-1].result is DecisionResult.OK
    assert THERMAL in env.rt.ownership.owned


async def test_no_permit_before_control(runtime_with_coe) -> None:
    env = await runtime_with_coe()
    env.wanted = [0.5]
    env.echo(0.0)
    run(env.rt, env.clock, 5)
    assert env.rt.health().mode == "OBSERVE"
    assert not permitted(env)
    assert set(env.sent()) == {0}


async def test_leaving_control_withdraws_the_permit(runtime_with_coe) -> None:
    env = await runtime_with_coe()
    env.wanted = [0.5]
    regulate_until_control(env)
    assert env.sent()[-1] == 5
    env.rt.store.update(
        "system.active_in_source", 240, source="victron", kind=SignalKind.STATE
    )  # Inselbetrieb
    env.echo(0.5)
    run(env.rt, env.clock, 1)
    assert env.rt.health().mode == "SAFE"
    assert env.sent()[-1] == 0 and env.adapter.held_k == 0.0
    assert not permitted(env)


async def test_runtime_handback_sends_zero(runtime_with_coe) -> None:
    env = await runtime_with_coe()
    env.wanted = [0.5]
    regulate_until_control(env)
    env.echo(0.5)
    run(env.rt, env.clock, 1)
    assert THERMAL in env.rt.ownership.owned
    # live → Dry-Run: Der Keeper sendet dringend 0, die Freigabe endet
    env.wanted = []
    env.rt.submit_intent(intent(2, ActuatorMode.DRY_RUN))
    env.echo(0.5)
    run(env.rt, env.clock, 1)
    assert env.sent()[-1] == 0
    assert not permitted(env)
    assert (THERMAL, OFFSET) in env.rt.executor.pending()  # der Sicherwert wartet auf das Echo
    assert THERMAL in env.rt.ownership.owned  # bis das Echo 0 zeigt
    env.echo(0.0)
    run(env.rt, env.clock, 1)
    handback = env.thermal_decisions()[-1]
    assert (handback.applied, handback.dry_run, handback.result) == (0.0, False, DecisionResult.OK)
    assert handback.codes[0] == "handback"
    assert THERMAL not in env.rt.ownership.owned
    [health] = [a for a in env.rt.health().actuators if a["id"] == THERMAL]
    assert (health["owned"], health["effective"]) == (False, "dry_run")


async def test_thermal_outage_does_not_degrade(runtime_with_coe) -> None:
    env = await runtime_with_coe()
    env.wanted = [0.5]
    run(env.rt, env.clock, 40)  # das C.M.I. sendet nie
    health = env.rt.health()
    assert (health.mode, health.mode_reasons) == ("CONTROL", [])
    [actuator] = [a for a in health.actuators if a["id"] == THERMAL]
    assert actuator["controllable"] is False
    assert actuator["capability_reasons"] == ["Keine Werte der Klimaanlage."]
    assert set(env.sent()) == {0}
    assert health.thermal["klima"]["connected"] is False


async def test_health_has_thermal_block(runtime_with_coe) -> None:
    env = await runtime_with_coe()
    regulate_until_control(env, offset_k=-1.0)
    block = env.rt.health().thermal["klima"]
    assert block.pop("last_packet_at") is not None
    assert block == {
        "label": "Klimaanlage",
        "connected": True,
        "foreign_packets": 0,
        "bad_packets": 0,
        "mode": "kuehlen",
        "running": True,
        "actual_c": 24.0,
        "setpoint_c": 22.0,
        "base_setpoint_c": 23.0,  # die UVR rechnet das Echo −1,0 K in setpoint_c ein
        "offset_sent_k": 0.0,
        "offset_active_k": -1.0,
        "power_w": pytest.approx(1230.0),
        "wish": None,
        "reason": None,
        "hint": None,
    }


async def test_health_thermal_block_without_values(runtime_with_coe) -> None:
    env = await runtime_with_coe()
    run(env.rt, env.clock, 1)
    block = env.rt.health().thermal["klima"]
    assert block["mode"] == "unbekannt"
    assert block["connected"] is False
    assert block["actual_c"] is None and block["base_setpoint_c"] is None
    assert (block["foreign_packets"], block["bad_packets"]) == (0, 0)


def test_health_thermal_block_without_adapter(runtime_env) -> None:
    rt, _, clock = runtime_env()
    assert rt.health().thermal == {}  # vor dem ersten Zyklus
    run(rt, clock, 1)
    block = rt.health().thermal["klima"]
    assert (block["connected"], block["last_packet_at"], block["foreign_packets"]) == (
        False,
        None,
        0,
    )
    snap: Snapshot = rt.store.snapshot()
    assert snap.get("thermal.klima.offset_sent_k") is None


def test_base_setpoint_falls_back_to_the_sent_offset() -> None:
    from tests.helpers import snap as make_snap

    from bindaems.core.runtime import _thermal_json

    device = CoeOffsetConfig.model_validate(
        {"kind": "coe_offset", "cmi_host": "192.168.81.92", "receive": RECEIVE}
    )
    p = "thermal.klima."
    without_echo = make_snap({p + "setpoint_c": 23.5, p + "offset_sent_k": 0.5})
    assert _thermal_json("klima", device, without_echo, True, {})["base_setpoint_c"] == 23.0
    with_echo = make_snap(
        {p + "setpoint_c": 23.5, p + "offset_sent_k": 0.5, p + "offset_active_k": 1.0}
    )
    assert _thermal_json("klima", device, with_echo, True, {})["base_setpoint_c"] == 22.5
    no_setpoint = make_snap({p + "offset_sent_k": 0.5, p + "offset_active_k": 1.0})
    assert _thermal_json("klima", device, no_setpoint, True, {})["base_setpoint_c"] is None


async def test_failing_regulation_withdraws_the_permit(runtime_with_coe, monkeypatch) -> None:
    env = await runtime_with_coe()
    env.wanted = [0.5]
    regulate_until_control(env)
    env.echo(0.5)
    run(env.rt, env.clock, 1)
    assert env.sent()[-1] == 5 and permitted(env)

    def broken(self: Regulation, inputs: Any) -> list[ControlCommand]:
        raise RuntimeError("Testfehler in der Regelung")

    monkeypatch.setattr(Regulation, "step", broken)
    env.echo(0.5)
    run(env.rt, env.clock, 1)
    assert env.rt.regulation.failed
    assert env.sent()[-1] == 0 and env.adapter.held_k == 0.0
    # solange sie wirft, gibt es keine neue Freigabe
    for _ in range(15):
        env.echo(0.0)
        run(env.rt, env.clock, 1)
        assert not permitted(env)
    assert set(env.sent()[-15:]) <= {0}
    assert "control.exception" in {alarm["id"] for alarm in env.rt.health().alarms}


async def test_permit_in_degraded(runtime_with_coe) -> None:
    env = await runtime_with_coe()
    env.wanted = [0.5]
    # Die EVCS ist nicht erreichbar: eingeschränkter Betrieb, die Regelung läuft weiter
    env.rt.store.register_source("evcs", 5.0)
    for _ in range(40):
        env.echo(0.0)
        run(env.rt, env.clock, 1)
        if env.rt.health().mode == "DEGRADED":
            break
    health = env.rt.health()
    assert (health.mode, health.mode_reasons) == ("DEGRADED", ["EVCS „evcs“ nicht erreichbar."])
    assert env.sent()[-1] == 5
    assert permitted(env)


async def test_source_does_not_clash_with_a_wallbox_of_the_same_key(runtime_with_coe) -> None:
    # Ruling R12: die Quelle heißt thermal.<name>; eine Wallbox „klima“ teilt nichts mit ihr
    env = await runtime_with_coe()
    env.rt.store.register_source("klima", 5.0)  # so registriert sich ein Wallbox-Adapter
    env.echo(0.0)
    run(env.rt, env.clock, 1)
    assert env.adapter.source == "thermal.klima"
    assert env.rt.store.connected("klima") is False
    assert env.rt.store.connected("thermal.klima") is True
    assert env.rt.health().thermal["klima"]["connected"] is True
    [actuator] = [a for a in env.rt.health().actuators if a["id"] == THERMAL]
    assert actuator["controllable"] is True
