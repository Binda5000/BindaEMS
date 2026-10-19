from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable
from pathlib import Path

import pytest
from tests.unit.core.adapters.conftest import CoeEnv

from bindaems.core.adapters import coe
from bindaems.core.adapters.coe import udp_port
from bindaems.shared.coe import CoeValue, decode, encode, offset_packet
from bindaems.shared.domain import Quality

CMI = ("192.168.81.92", 5442)
FIXTURE = (Path(__file__).resolve().parents[3] / "fixtures" / "coe_cmi.bin").read_bytes()
CmiEnv = Callable[[], CoeEnv]


def sent_raw(env: CoeEnv, index: int = -1) -> int:
    return decode(env.port.sent[index][0])[0].raw


async def test_maps_packet_from_fixture(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.receive(FIXTURE, CMI)
    snap = env.store.snapshot()
    assert snap.ok("thermal.klima.actual_c") == 24.0
    assert snap.ok("thermal.klima.setpoint_c") == 24.5
    assert snap.ok("thermal.klima.offset_active_k") == 0.5
    assert snap.ok("thermal.klima.running") is True
    assert snap.ok("thermal.klima.cmd_on") is True
    assert snap.ok("thermal.klima.cooling") is True
    assert snap.ok("thermal.klima.venting") is False
    assert snap.ok("thermal.klima.heating") is False
    assert snap.ok("thermal.klima.power_w") == pytest.approx(1230.0)
    assert env.adapter.health().connected is True
    assert env.adapter.health().last_ok is not None
    assert env.adapter.stats()["last_packet_at"] is not None


async def test_signal_kinds(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.receive(FIXTURE, CMI)
    readings = env.store.snapshot().readings
    kinds = {name.removeprefix("thermal.klima."): r.kind.value for name, r in readings.items()}
    assert kinds == {
        "actual_c": "measurement",
        "setpoint_c": "measurement",
        "offset_active_k": "measurement",
        "power_w": "measurement",
        "cmd_on": "state",
        "cooling": "state",
        "venting": "state",
        "heating": "state",
        "running": "state",
        "offset_sent_k": "state",
    }
    assert {r.source for r in readings.values()} == {"klima"}


async def test_ignores_foreign_ip(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.receive(FIXTURE, ("192.168.81.77", 5442))
    assert env.store.snapshot().get("thermal.klima.actual_c") is None
    assert env.adapter.stats()["foreign_packets"] == 1
    assert env.adapter.health().connected is False


async def test_bad_packet_counted_and_survives(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.receive(FIXTURE[:10], CMI)  # zu kurz für seine Werte
    await env.receive(b"", CMI)
    await env.receive(bytes([2, 0, 4, 0]), CMI)  # n = 0
    assert env.adapter.stats()["bad_packets"] == 3
    assert env.store.snapshot().get("thermal.klima.actual_c") is None
    await env.receive(FIXTURE, CMI)
    assert env.store.snapshot().ok("thermal.klima.actual_c") == 24.0
    assert env.adapter.stats()["bad_packets"] == 3


async def test_foreign_and_bad_packets_are_logged_once(coe_env: CmiEnv) -> None:
    env = coe_env()
    events: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(coe.log, "warning", lambda event, **_: events.append(event))
        for _ in range(5):
            await env.receive(FIXTURE, ("10.0.0.1", 1))
            await env.receive(b"\x00", CMI)
    assert len(events) == 2
    assert env.adapter.stats()["foreign_packets"] == 5
    assert env.adapter.stats()["bad_packets"] == 5


async def test_packet_without_table_values_does_not_count_as_alive(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.receive(encode([CoeValue(12, 1, True, 1, 100)]), CMI)
    assert env.adapter.health().connected is False
    assert env.adapter.stats()["last_packet_at"] is None
    assert env.adapter.stats()["foreign_packets"] == 1


async def test_partial_packet_updates_only_its_values(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.receive(FIXTURE, CMI)
    await env.receive(encode([CoeValue(56, 1, True, 1, 261)]), CMI)
    snap = env.store.snapshot()
    assert snap.ok("thermal.klima.actual_c") == 26.1
    assert snap.ok("thermal.klima.running") is True


async def test_unit_mismatch_makes_value_invalid(coe_env: CmiEnv) -> None:
    env = coe_env()
    values = decode(FIXTURE)
    values[3] = CoeValue(56, 4, True, 11, 123)  # Leistung in einer anderen Einheit als konfiguriert
    await env.receive(encode(values), CMI)
    snap = env.store.snapshot()
    assert snap.get("thermal.klima.power_w").quality is Quality.INVALID  # type: ignore[union-attr]
    assert snap.ok("thermal.klima.actual_c") == 24.0
    assert env.adapter.stats()["unit_mismatch"] == 1
    await env.receive(FIXTURE, CMI)  # das nächste passende Paket macht den Wert wieder gültig
    assert env.store.snapshot().ok("thermal.klima.power_w") == pytest.approx(1230.0)


async def test_stale_after_180s_sets_disconnected(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.receive(FIXTURE, CMI)
    env.advance(170)
    await env.tick()
    assert env.adapter.health().connected is True
    assert env.store.snapshot().ok("thermal.klima.running") is True
    env.advance(11)
    await env.tick()
    assert env.adapter.health().connected is False
    reading = env.store.snapshot().get("thermal.klima.running")
    assert reading is not None and reading.quality is Quality.STALE
    await env.receive(FIXTURE, CMI)
    assert env.adapter.health().connected is True
    assert env.store.snapshot().ok("thermal.klima.running") is True


async def test_sends_zero_on_start(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.start()
    assert env.port.sent[0] == (offset_packet(0.0, node=60, output=1), CMI)
    assert env.adapter.held_k == 0.0


async def test_resends_every_30s(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.start()
    assert len(env.port.sent) == 1
    for expected in (2, 3):
        env.advance(30)
        await env.tick()
        assert len(env.port.sent) == expected
    assert 30.0 in env.sleep.calls
    assert {data for data, _ in env.port.sent} == {offset_packet(0.0, node=60, output=1)}


async def test_sends_zero_without_permit(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.start()
    env.adapter.permit(env.mono() + 10)
    env.adapter.set_offset(-1.0)
    assert sent_raw(env) == -10
    env.advance(11)
    await env.tick()
    assert sent_raw(env) == 0
    assert env.adapter.held_k == 0.0  # mit der Freigabe endet auch das Halten


async def test_never_sends_held_value_without_permit(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.start()
    assert env.adapter.set_offset(1.5) is True
    assert sent_raw(env) == 0
    env.advance(30)
    await env.tick()
    assert sent_raw(env) == 0
    env.adapter.permit(env.mono() + 10)
    env.advance(5)
    await env.tick()
    assert sent_raw(env) == 15


async def test_permit_ends_exactly_at_its_time(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.start()
    env.adapter.permit(env.mono() + 10)
    env.adapter.set_offset(1.0)
    env.advance(10)  # until ist ausgeschlossen
    await env.tick()
    assert sent_raw(env) == 0


async def test_set_offset_sends_immediately(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.start()
    env.adapter.permit(env.mono() + 10)
    before = len(env.port.sent)
    assert env.adapter.set_offset(0.5) is True
    assert len(env.port.sent) == before + 1
    assert env.port.sent[-1] == (offset_packet(0.5, node=60, output=1), CMI)
    assert env.adapter.held_k == 0.5


async def test_set_offset_without_session_is_false(coe_env: CmiEnv) -> None:
    env = coe_env()
    assert env.adapter.set_offset(0.5) is False
    assert env.adapter.held_k == 0.0


@pytest.mark.parametrize("value", [3.5, -3.5, float("nan"), float("inf")])
async def test_set_offset_rejects_values_outside_the_limit(coe_env: CmiEnv, value: float) -> None:
    env = coe_env()
    await env.start()
    env.adapter.permit(env.mono() + 10)
    sent = len(env.port.sent)
    assert env.adapter.set_offset(value) is False
    assert env.adapter.held_k == 0.0
    assert len(env.port.sent) == sent


async def test_set_offset_accepts_the_limit(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.start()
    env.adapter.permit(env.mono() + 10)
    assert env.adapter.set_offset(env.cfg.max_offset_k) is True
    assert env.adapter.set_offset(-env.cfg.max_offset_k) is True
    assert sent_raw(env) == -30


async def test_offset_sent_signal(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.receive(FIXTURE, CMI)  # STATE-Signale gelten nur bei verbundener Quelle
    assert env.store.snapshot().ok("thermal.klima.offset_sent_k") == 0.0
    env.adapter.permit(env.mono() + 10)
    env.adapter.set_offset(-1.5)
    assert env.store.snapshot().ok("thermal.klima.offset_sent_k") == -1.5
    env.advance(11)
    await env.tick()
    assert env.store.snapshot().ok("thermal.klima.offset_sent_k") == 0.0  # was gesendet wurde


async def test_offset_sent_is_zero_not_negative_zero(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.receive(FIXTURE, CMI)
    env.adapter.permit(env.mono() + 10)
    env.adapter.set_offset(-0.0)
    value = env.store.snapshot().ok("thermal.klima.offset_sent_k")
    assert value == 0.0 and str(value) == "0.0"


async def test_reopens_port_with_backoff_and_starts_at_zero(coe_env: CmiEnv) -> None:
    env = coe_env()
    env.open_errors.append(OSError("Adresse belegt"))
    await env.start()
    assert env.ports == []
    health = env.adapter.health()
    assert health.error_count == 1 and health.last_error == "OSError: Adresse belegt"
    assert env.sleep.calls == [1.0]
    assert env.adapter.set_offset(0.5) is False
    await env.tick()
    assert env.port.sent[0][0] == offset_packet(0.0, node=60, output=1)


async def test_send_error_restarts_session_with_zero_held(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.start()
    first = env.port
    env.adapter.permit(env.mono() + 100)
    env.adapter.set_offset(1.0)

    def broken(data: bytes, addr: tuple[str, int]) -> None:
        raise OSError("Netzwerk nicht erreichbar")

    first.sendto = broken  # type: ignore[method-assign]
    env.advance(30)
    await env.tick()
    assert first.closed is True  # auch wenn das letzte Senden von 0 scheitert
    assert env.adapter.health().error_count == 1
    assert env.adapter.set_offset(0.5) is False  # zwischen den Sitzungen
    await env.tick()
    assert env.port is not first
    assert env.adapter.held_k == 0.0
    assert env.port.sent[0][0] == offset_packet(0.0, node=60, output=1)  # trotz Freigabe: 0


async def test_cancel_closes_port_and_disconnects(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.receive(FIXTURE, CMI)
    await env.stop()
    assert env.port.closed is True
    assert env.adapter.health().connected is False
    assert env.adapter.set_offset(0.5) is False


async def test_udp_port_receives_and_sends_on_loopback() -> None:
    received: list[tuple[bytes, tuple[str, int]]] = []
    got = asyncio.Event()

    def on_datagram(data: bytes, addr: tuple[str, int]) -> None:
        received.append((data, addr))
        got.set()

    port = await udp_port(on_datagram, 0)
    try:
        listen = port._transport.get_extra_info("sockname")  # type: ignore[attr-defined]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
            peer.bind(("127.0.0.1", 0))
            peer.settimeout(2)
            peer.sendto(FIXTURE, ("127.0.0.1", listen[1]))
            await asyncio.wait_for(got.wait(), 2)
            assert received[0][0] == FIXTURE and received[0][1][0] == "127.0.0.1"
            port.sendto(b"antwort", ("127.0.0.1", peer.getsockname()[1]))
            assert peer.recvfrom(100)[0] == b"antwort"
    finally:
        port.close()


async def test_zero_goes_out_when_the_permit_lapses_not_at_the_next_cycle(
    coe_env: CmiEnv,
) -> None:
    env = coe_env()
    await env.start()
    env.adapter.permit(env.mono() + 10)
    env.adapter.set_offset(-1.0)
    await env.settle()
    sent = len(env.port.sent)
    await env.run_for(9.5)
    assert len(env.port.sent) == sent and sent_raw(env) == -10
    await env.run_for(1.5)  # nach 11 s, weit vor dem Takt von 30 s
    assert len(env.port.sent) == sent + 1
    assert sent_raw(env) == 0
    assert env.adapter.held_k == 0.0


async def test_periodic_resend_continues_with_real_durations(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.start()
    env.adapter.permit(env.mono() + 10)
    env.adapter.set_offset(-1.0)
    await env.settle()
    await env.run_for(61)
    # Start 0, set_offset -10, Ablauf 0, dann der Takt bei 30 s und 60 s
    assert [decode(data)[0].raw for data, _ in env.port.sent] == [0, -10, 0, 0, 0]


async def test_a_new_permit_does_not_revive_the_old_value(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.start()
    env.adapter.permit(env.mono() + 10)
    env.adapter.set_offset(-1.0)
    await env.settle()
    await env.run_for(11)
    env.adapter.permit(env.mono() + 40)
    await env.run_for(30)  # ein Takt unter Freigabe
    assert sent_raw(env) == 0
    assert env.adapter.set_offset(-1.0) is True
    assert sent_raw(env) == -10


async def test_withdrawing_the_permit_sends_zero_at_once(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.start()
    env.adapter.permit(env.mono() + 100)
    env.adapter.set_offset(2.0)
    assert sent_raw(env) == 20
    env.adapter.permit(float("-inf"))
    await env.settle()
    assert sent_raw(env) == 0 and env.adapter.held_k == 0.0


async def test_extending_the_permit_defers_the_lapse(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.start()
    env.adapter.permit(env.mono() + 10)
    env.adapter.set_offset(1.0)
    await env.settle()
    await env.run_for(8)
    env.adapter.permit(env.mono() + 10)  # der Zyklus gibt weiter frei
    await env.run_for(8)
    assert sent_raw(env) == 10 and env.adapter.held_k == 1.0
    await env.run_for(4)
    assert sent_raw(env) == 0


async def test_old_states_are_stale_after_an_outage_until_reported_again(
    coe_env: CmiEnv,
) -> None:
    env = coe_env()
    await env.receive(FIXTURE, CMI)
    env.advance(181)
    await env.tick()
    assert env.adapter.health().connected is False
    await env.receive(encode([CoeValue(56, 1, True, 1, 261)]), CMI)  # nur der Istwert
    snap = env.store.snapshot()
    assert snap.ok("thermal.klima.actual_c") == 26.1
    for old in ("running", "cooling", "heating", "setpoint_c"):
        reading = snap.get(f"thermal.klima.{old}")
        assert reading is not None and reading.quality is Quality.STALE, old
    assert snap.ok("thermal.klima.offset_sent_k") == 0.0  # eigener Wert, gilt weiter
    env.advance(1)
    await env.receive(FIXTURE, CMI)
    snap = env.store.snapshot()
    assert snap.ok("thermal.klima.running") is True
    assert snap.ok("thermal.klima.power_w") == pytest.approx(1230.0)


async def test_stale_disconnect_and_recovery_are_logged(coe_env: CmiEnv) -> None:
    from bindaems.shared import retry

    env = coe_env()
    warnings: list[str] = []
    infos: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(retry.log, "warning", lambda event, **kw: warnings.append(kw["error"]))
        mp.setattr(retry.log, "info", lambda event, **kw: infos.append(event))
        await env.receive(FIXTURE, CMI)
        env.advance(181)
        await env.tick()
        env.advance(30)
        await env.tick()  # kein zweites Mal melden
        await env.receive(FIXTURE, CMI)
    assert warnings == ["Seit 181 Sekunden ist kein gültiges CoE-Paket vom C.M.I. eingegangen."]
    assert infos == ["Adapter verbunden", "Adapter verbunden"]
    assert env.adapter.health().last_error == warnings[0]


async def test_unit_mismatch_is_logged_once(coe_env: CmiEnv) -> None:
    env = coe_env()
    values = decode(FIXTURE)
    values[3] = CoeValue(56, 4, True, 11, 123)
    events: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(coe.log, "warning", lambda event, **_: events.append(event))
        for _ in range(3):
            await env.receive(encode(values), CMI)
    assert len(events) == 1
    assert env.adapter.stats()["unit_mismatch"] == 3


async def test_cancel_sends_a_last_zero_before_closing(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.start()
    env.adapter.permit(env.mono() + 100)
    env.adapter.set_offset(2.0)
    assert sent_raw(env) == 20
    await env.stop()
    assert sent_raw(env) == 0
    assert env.port.closed is True


async def test_unexpected_error_in_a_packet_is_counted_and_survived(coe_env: CmiEnv) -> None:
    env = coe_env()
    await env.start()
    events: list[str] = []
    real_update = env.store.update
    failing = [True]

    def update(*args: object, **kwargs: object) -> None:
        if failing[0]:
            raise RuntimeError("Speicher kaputt")
        real_update(*args, **kwargs)  # type: ignore[arg-type]

    env.store.update = update  # type: ignore[method-assign]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(coe.log, "warning", lambda event, **_: events.append(event))
        await env.receive(FIXTURE, CMI)
        await env.receive(FIXTURE, CMI)
    assert env.adapter.stats()["bad_packets"] == 2
    assert len(events) == 1
    failing[0] = False
    await env.receive(FIXTURE, CMI)
    assert env.store.snapshot().ok("thermal.klima.actual_c") == 24.0
