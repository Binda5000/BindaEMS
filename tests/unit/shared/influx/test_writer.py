import base64
import gzip

import httpx
from tests.unit.shared.influx.conftest import TS

from bindaems.shared.influx.lineprotocol import Point, to_line

URL = "http://influx.lan:8086/write"


async def test_writer_posts_gzip_line_protocol(respx_mock, writer_env) -> None:
    route = respx_mock.post(URL).respond(204)
    writer, _ = writer_env()
    p = Point("power", {"source": "grid"}, {"p_w": 1.0}, TS)
    writer.write(p)
    await writer.flush_now()
    req = route.calls[0].request
    assert req.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(req.content).decode() == to_line(p)
    assert dict(req.url.params) == {"db": "bindaems", "rp": "raw", "precision": "ms"}
    expected = base64.b64encode(b"bindaems:geheim").decode()
    assert req.headers["Authorization"] == f"Basic {expected}"
    assert writer.stats().sent_lines == 1


async def test_writer_groups_by_retention_policy_and_batches(respx_mock, writer_env) -> None:
    route = respx_mock.post(URL).respond(204)
    writer, _ = writer_env(batch_lines=2)
    for i in range(3):
        writer.write(Point("a", {}, {"v": float(i)}, TS))
    writer.write(Point("b", {}, {"v": 1.0}, TS, rp="long"))
    await writer.flush_now()
    sent = [
        (c.request.url.params["rp"], gzip.decompress(c.request.content).decode())
        for c in route.calls
    ]
    assert [(rp, body.count("\n") + 1) for rp, body in sent] == [
        ("raw", 2),
        ("raw", 1),
        ("long", 1),
    ]


async def test_writer_spools_on_server_error(respx_mock, writer_env) -> None:
    respx_mock.post(URL).respond(503)
    writer, spool = writer_env()
    writer.write(Point("power", {}, {"p_w": 1.0}, TS))
    await writer.flush_now()
    assert len(spool.oldest(10)) == 1 and writer.stats().spooled_lines == 1


async def test_writer_spools_on_network_error_and_auth_error(respx_mock, writer_env) -> None:
    respx_mock.post(URL).mock(side_effect=[httpx.ConnectError("weg"), httpx.Response(401)])
    writer, spool = writer_env()
    writer.write(Point("a", {}, {"v": 1.0}, TS))
    await writer.flush_now()
    writer.write(Point("b", {}, {"v": 1.0}, TS))
    await writer.flush_now()
    assert len(spool.oldest(10)) == 2 and writer.stats().dropped_lines == 0


async def test_writer_drains_spool_after_recovery(respx_mock, writer_env) -> None:
    route = respx_mock.post(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(204), httpx.Response(204)]
    )
    writer, spool = writer_env()
    writer.write(Point("a", {}, {"v": 1.0}, TS))
    await writer.flush_now()
    writer.write(Point("b", {}, {"v": 2.0}, TS))
    await writer.flush_now()
    assert spool.oldest(10) == [] and route.call_count == 3


async def test_drain_stops_at_first_failure_and_keeps_file(respx_mock, writer_env) -> None:
    respx_mock.post(URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(204),
            httpx.Response(503),
        ]
    )
    writer, spool = writer_env()
    for name in ("a", "b"):
        writer.write(Point(name, {}, {"v": 1.0}, TS))
        await writer.flush_now()  # beide landen im Spool
    writer.write(Point("c", {}, {"v": 1.0}, TS))
    await writer.flush_now()  # c gesendet, Nachsenden von a scheitert
    assert [f.lines[0][0] for f in spool.oldest(10)] == ["a", "b"]


async def test_writer_drops_on_400(respx_mock, writer_env) -> None:
    respx_mock.post(URL).respond(400, json={"error": "partial write"})
    writer, spool = writer_env()
    writer.write(Point("a", {}, {"v": 1.0}, TS))
    await writer.flush_now()
    assert writer.stats().dropped_lines == 1 and spool.oldest(10) == []


def test_write_is_non_blocking(writer_env) -> None:
    writer, _ = writer_env()
    writer.write(Point("a", {}, {"v": 1.0}, TS))  # ohne laufende Event-Loop-Aufgabe
    assert writer.stats().sent_lines == 0


def test_invalid_point_is_dropped_without_exception(writer_env) -> None:
    writer, _ = writer_env()
    writer.write(Point("a", {}, {}, TS))
    assert writer.stats().dropped_lines == 1


def test_full_queue_goes_to_spool(writer_env) -> None:
    writer, spool = writer_env(queue_max=3)
    for i in range(4):
        writer.write(Point("a", {}, {"v": float(i)}, TS))
    assert [len(f.lines) for f in spool.oldest(10)] == [3]
    assert writer.stats().spooled_lines == 3


async def test_after_first_failure_remaining_batches_are_spooled(respx_mock, writer_env) -> None:
    route = respx_mock.post(URL).mock(side_effect=[httpx.Response(503)])
    writer, spool = writer_env(batch_lines=1)
    for i in range(3):
        writer.write(Point("a", {}, {"v": float(i)}, TS))
    await writer.flush_now()
    assert route.call_count == 1  # kein weiterer Versuch in derselben Runde
    assert len(spool.oldest(10)) == 3 and writer.stats().spooled_lines == 3


async def test_spool_limits_enforced_after_spooling(respx_mock, writer_env) -> None:
    respx_mock.post(URL).respond(503)
    writer, spool = writer_env(spool_max_bytes=100)
    for i in range(5):
        writer.write(Point("messung", {"quelle": "netz"}, {"wert": float(i)}, TS))
        await writer.flush_now()
    assert 0 < spool.size_bytes() <= 100


async def test_unwritable_spool_drops_lines_instead_of_failing(
    respx_mock, writer_env, monkeypatch
) -> None:
    respx_mock.post(URL).respond(503)
    writer, spool = writer_env()

    def disk_full(rp, lines):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(spool, "append", disk_full)
    writer.write(Point("a", {}, {"v": 1.0}, TS))
    await writer.flush_now()  # darf nicht werfen, sonst stünde run() still
    assert writer.stats().dropped_lines == 1 and writer.stats().spooled_lines == 0
