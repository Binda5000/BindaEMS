import asyncio
import threading
from datetime import UTC, datetime

from bindaems.app.runtime import LoopSink
from bindaems.shared.influx.lineprotocol import Point

POINT = Point("price", {"kind": "spot_raw"}, {"ct_kwh": 1.0}, datetime(2026, 10, 9, tzinfo=UTC))


class ThreadRecorder:
    def __init__(self) -> None:
        self.threads: list[int] = []

    def write(self, point: Point) -> None:
        self.threads.append(threading.get_ident())


async def test_points_from_worker_threads_are_written_in_the_event_loop() -> None:
    recorder = ThreadRecorder()
    sink = LoopSink(recorder)
    sink.bind(asyncio.get_running_loop())
    await asyncio.to_thread(sink.write, POINT)
    sink.write(POINT)
    await asyncio.sleep(0)
    assert recorder.threads == [threading.get_ident()] * 2


def test_unbound_sink_writes_directly() -> None:
    recorder = ThreadRecorder()
    LoopSink(recorder).write(POINT)
    assert recorder.threads == [threading.get_ident()]
