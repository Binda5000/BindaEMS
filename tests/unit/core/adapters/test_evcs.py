import socket
from collections.abc import AsyncIterator

import pytest
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice
from tests.helpers import run_until

from bindaems.core.adapters.evcs import (
    BLOCK_COUNT,
    BLOCK_START,
    ModbusReadError,
    PymodbusReader,
    decode_block,
)
from bindaems.shared.domain import Quality

REGS = [0xC025, 0, 0, 0, 0, 0, 0, 0x0102, 0x0003, 0, 1, 0, 0, 0, 0, 0, 0, 16, 125, 0]


def test_decode_block() -> None:
    d = decode_block(5000, REGS)
    assert d == {
        "product_id": 0xC025,
        "firmware": "0102.0003",
        "mode": 0,
        "start_stop": 1,
        "set_current_a": 16,
        "current_a": 12.5,
    }


async def test_poll_updates_store(evcs_env) -> None:
    adapter, _, store = evcs_env(blocks=[REGS])

    def polled() -> bool:  # geprüft, solange der Adapter läuft (Abbruch trennt die Quelle)
        snap = store.snapshot()
        return snap.ok("wallbox.evcs.current_a") == 12.5 and snap.ok("wallbox.evcs.mode") == 0

    await run_until(adapter, polled)


async def test_current_goes_stale_while_states_stay_valid(evcs_env) -> None:
    adapter, _, store = evcs_env(blocks=[REGS])

    def stale_after_freshness_limit() -> bool:
        if store.snapshot().ok("wallbox.evcs.current_a") != 12.5:
            return False
        adapter.clock.advance(6)  # Frischegrenze 5 s überschritten, kein neuer Block
        snap = store.snapshot()
        current = snap.get("wallbox.evcs.current_a")
        return current.quality is Quality.STALE and snap.ok("wallbox.evcs.mode") == 0

    await run_until(adapter, stale_after_freshness_limit)


async def test_read_errors_mark_disconnected_and_back_off(evcs_env) -> None:
    adapter, _, store = evcs_env(
        blocks=[REGS, RuntimeError("timeout"), RuntimeError("timeout")], poll_s=0.5
    )
    await run_until(adapter, lambda: adapter.health().error_count >= 2)
    # erst die Abfragepause nach dem erfolgreichen Lesen, dann Backoff 1 s und 2 s
    assert adapter.sleeps[:3] == [0.5, 1.0, 2.0]
    assert store.snapshot().get("wallbox.evcs.mode").quality.value == "stale"


async def test_backoff_resets_after_successful_read(evcs_env) -> None:
    adapter, _, _ = evcs_env(
        blocks=[RuntimeError("timeout"), REGS, RuntimeError("timeout")], poll_s=0.5
    )
    await run_until(adapter, lambda: adapter.health().error_count >= 2)
    assert adapter.sleeps[:3] == [1.0, 0.5, 1.0]


async def test_reader_closed_before_reconnect(evcs_env) -> None:
    adapter, reader, _ = evcs_env(blocks=[REGS, RuntimeError("timeout")])
    await run_until(adapter, lambda: reader.calls.count(("connect",)) >= 2)
    names = [call[0] for call in reader.calls]
    second_connect = names.index("connect", 1)
    assert "close" in names[:second_connect]


async def test_cancel_marks_source_disconnected(evcs_env) -> None:
    adapter, _, store = evcs_env(blocks=[REGS])
    await run_until(adapter, lambda: store.snapshot().ok("wallbox.evcs.mode") == 0)
    # ein beendeter Adapter darf keine Zustandswerte als gültig hinterlassen
    assert store.snapshot().get("wallbox.evcs.mode").quality is Quality.STALE


async def test_only_read_calls(evcs_env) -> None:
    adapter, reader, _ = evcs_env(blocks=[REGS])
    await run_until(adapter, lambda: len(reader.calls) >= 3)
    assert {c[0] for c in reader.calls} <= {"connect", "read_holding", "close"}


async def test_unknown_product_id_reported(evcs_env) -> None:
    adapter, _, store = evcs_env(blocks=[[0x1234, *REGS[1:]]])
    await run_until(
        adapter,
        lambda: (
            adapter.health().last_error is not None
            and store.snapshot().ok("wallbox.evcs.current_a") == 12.5
        ),  # Werte trotzdem übernommen
    )
    assert "Unbekannte Produkt-ID 0x1234" in adapter.health().last_error


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
async def modbus_port() -> AsyncIterator[int]:
    """Lokaler pymodbus-Server mit dem EVCS-Registerblock (Unit 1)."""
    port = _free_port()
    device = SimDevice(1, [SimData(BLOCK_START, values=REGS, datatype=DataType.REGISTERS)])
    server = ModbusTcpServer(device, address=("127.0.0.1", port))
    await server.serve_forever(background=True)
    yield port
    await server.shutdown()


async def test_pymodbus_reader_reads_block(modbus_port: int) -> None:
    reader = PymodbusReader("127.0.0.1", modbus_port, 1)
    await reader.connect()
    try:
        assert await reader.read_holding(BLOCK_START, BLOCK_COUNT) == REGS
    finally:
        await reader.close()


async def test_pymodbus_reader_error_response_raises(modbus_port: int) -> None:
    reader = PymodbusReader("127.0.0.1", modbus_port, 1)
    await reader.connect()
    try:
        with pytest.raises(ModbusReadError):
            await reader.read_holding(6000, BLOCK_COUNT)
    finally:
        await reader.close()


async def test_pymodbus_reader_connect_failure_raises() -> None:
    reader = PymodbusReader("127.0.0.1", _free_port(), 1)
    with pytest.raises(ConnectionError):
        await reader.connect()


async def test_errors_and_recovery_are_logged(evcs_env) -> None:
    from structlog.testing import capture_logs

    adapter, _, _ = evcs_env(blocks=[RuntimeError("timeout"), REGS])
    with capture_logs() as logs:
        await run_until(adapter, lambda: adapter.health().connected)
    assert [e["event"] for e in logs] == ["Adapter-Fehler", "Adapter verbunden"]
    assert logs[0]["adapter"] == "evcs" and "timeout" in logs[0]["error"]
