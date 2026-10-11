import asyncio
from typing import Any

import pytest
from tests.helpers import run_until
from tests.unit.core.adapters.conftest import FakeTransport, P

from bindaems.core.adapters.victron_mqtt import (
    HEARTBEAT_PATH,
    KEEPALIVE_SUPPRESS,
    OUTBOX_SIZE,
    WRITABLE_PATHS,
    MqttMessage,
)
from bindaems.shared.domain import Quality


async def shutdown_cleanly(adapter: Any) -> None:
    """Lässt die Sitzung anlaufen und bricht den Adapter ab; er muss binnen 1 s enden.

    ``run_until`` wartet nach dem Abbruch ohne Grenze und bliebe bei einem Fehler im Abbau hängen.
    """
    task = asyncio.create_task(adapter.run())
    try:
        await asyncio.sleep(0.05)
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=1.0)
        assert done == {task}
        assert task.cancelled()
    finally:
        task.cancel()  # nur nötig, wenn der Adapter den Abbruch verschluckt oder blockiert
        await asyncio.wait({task}, timeout=1.0)


async def test_first_keepalive_requests_full_republish(fake_env) -> None:
    adapter, transports, _ = fake_env(keepalive_s=0.02)
    await run_until(adapter, lambda: bool(transports) and len(transports[0].published) >= 3)
    t = transports[0]
    assert t.published[0] == (f"R/{P}/keepalive", b"")
    assert all(payload == KEEPALIVE_SUPPRESS for _, payload in t.published[1:])


async def test_publishes_only_keepalive_and_allowed_writes(fake_env) -> None:
    adapter, transports, _ = fake_env(
        keepalive_s=0.02, messages=[lambda: adapter.write(HEARTBEAT_PATH, 3)]
    )
    await run_until(adapter, lambda: bool(transports) and len(transports[0].published) >= 3)
    allowed = {f"R/{P}/keepalive"} | {f"W/{P}/{path}" for path in WRITABLE_PATHS}
    assert {topic for topic, _ in transports[0].published} <= allowed


async def test_publishes_only_keepalive_without_writes(fake_env) -> None:
    adapter, transports, _ = fake_env(keepalive_s=0.02)
    await run_until(adapter, lambda: bool(transports) and len(transports[0].published) >= 2)
    assert {topic for topic, _ in transports[0].published} == {f"R/{P}/keepalive"}


def test_the_heartbeat_is_the_only_write_path() -> None:
    assert HEARTBEAT_PATH == "bindaems/0/Heartbeat"
    assert WRITABLE_PATHS == {"bindaems/0/Heartbeat"}
    assert OUTBOX_SIZE == 100


async def test_write_publishes_json_value_when_connected(fake_env) -> None:
    results: list[bool] = []
    adapter, transports, _ = fake_env(
        messages=[lambda: results.append(adapter.write(HEARTBEAT_PATH, 7))]
    )
    await run_until(
        adapter,
        lambda: (
            bool(transports)
            and (f"W/{P}/bindaems/0/Heartbeat", b'{"value": 7}') in transports[0].published
        ),
    )
    assert results == [True]


async def test_write_uses_the_discovered_portal(fake_env) -> None:
    adapter, transports, _ = fake_env(
        portal_id=None,
        messages=[
            MqttMessage("N/abc123/system/0/Serial", b'{"value": "abc123"}'),
            lambda: adapter.write(HEARTBEAT_PATH, 1),
        ],
    )
    await run_until(
        adapter,
        lambda: (
            bool(transports)
            and ("W/abc123/bindaems/0/Heartbeat", b'{"value": 1}') in transports[0].published
        ),
    )


async def test_writes_are_sent_in_order(fake_env) -> None:
    adapter, transports, _ = fake_env(
        messages=[
            lambda: adapter.write(HEARTBEAT_PATH, 1),
            lambda: adapter.write(HEARTBEAT_PATH, 2),
        ]
    )
    await run_until(
        adapter,
        lambda: (
            bool(transports) and sum(t.startswith("W/") for t, _ in transports[0].published) == 2
        ),
    )
    writes = [payload for topic, payload in transports[0].published if topic.startswith("W/")]
    assert writes == [b'{"value": 1}', b'{"value": 2}']


def test_write_returns_false_without_session(fake_env) -> None:
    adapter, _, _ = fake_env()
    assert adapter.write(HEARTBEAT_PATH, 1) is False


async def test_write_returns_false_after_the_session_ended(fake_env) -> None:
    adapter, _, _ = fake_env(end_with_error=True)
    await run_until(adapter, lambda: adapter.health().error_count >= 1)
    assert adapter.write(HEARTBEAT_PATH, 1) is False


async def test_write_returns_false_when_outbox_is_full(fake_env) -> None:
    results: list[bool] = []
    adapter, transports, _ = fake_env(
        messages=[
            lambda n=n: results.append(adapter.write(HEARTBEAT_PATH, n))
            for n in range(OUTBOX_SIZE + 1)
        ]
    )
    await run_until(
        adapter,
        lambda: (
            bool(transports)
            and len(results) == OUTBOX_SIZE + 1
            and len(transports[0].published) > OUTBOX_SIZE
        ),
    )
    assert results == [True] * OUTBOX_SIZE + [False]
    sent = [payload for topic, payload in transports[0].published if topic.startswith("W/")]
    assert sent == [b'{"value": %d}' % n for n in range(OUTBOX_SIZE)]


async def test_outbox_does_not_survive_the_session(fake_env) -> None:
    # Die erste Sitzung endet im selben Schritt, in dem der Heartbeat eingestellt wird: er wird nie
    # gesendet und darf auch in der nächsten Sitzung nicht mehr auftauchen.
    adapter, transports, _ = fake_env(
        keepalive_s=0.02, sessions=[[lambda: adapter.write(HEARTBEAT_PATH, 5)], []]
    )
    await run_until(adapter, lambda: len(transports) == 2 and len(transports[1].published) >= 3)
    assert not [topic for t in transports for topic, _ in t.published if topic.startswith("W/")]


async def test_session_end_stops_keepalive_and_sender(fake_env) -> None:
    adapter, _, _ = fake_env(keepalive_s=0.02)
    await shutdown_cleanly(adapter)
    assert asyncio.all_tasks() == {asyncio.current_task()}


async def test_dead_sender_does_not_swallow_the_shutdown(fake_env, monkeypatch) -> None:
    # Scheitert das Veröffentlichen, endet der Sender-Task mit einem Fehler. Der Abbruch des
    # Adapters muss trotzdem durchkommen und darf nicht als Verbindungsfehler gelten.
    publish = FakeTransport.publish

    async def failing_publish(self: FakeTransport, topic: str, payload: bytes) -> None:
        if topic.startswith("W/"):
            raise ConnectionError("Verbindung verloren")
        await publish(self, topic, payload)

    monkeypatch.setattr(FakeTransport, "publish", failing_publish)
    adapter, _, _ = fake_env(messages=[lambda: adapter.write(HEARTBEAT_PATH, 1)])
    await shutdown_cleanly(adapter)  # der Sender ist beim Veröffentlichen gescheitert


def test_write_rejects_other_paths(fake_env) -> None:
    adapter, _, _ = fake_env()
    with pytest.raises(
        ValueError, match="Schreibpfad nicht freigegeben: hub4/0/Overrides/Setpoint"
    ):
        adapter.write("hub4/0/Overrides/Setpoint", 0)


@pytest.mark.parametrize(
    "path",
    [
        "",
        "bindaems/0/State",
        "bindaems/0/Heartbeat/",
        "/bindaems/0/Heartbeat",
        "bindaems/1/Heartbeat",
        "bindaems/0/Heartbeat/x",
        f"W/{P}/bindaems/0/Heartbeat",
        "settings/0/Settings/CGwacs/Hub4Mode",
    ],
)
def test_write_rejects_near_miss_paths(fake_env, path: str) -> None:
    adapter, _, _ = fake_env()
    with pytest.raises(ValueError, match="Schreibpfad nicht freigegeben"):
        adapter.write(path, 1)


async def test_messages_update_store(fake_env) -> None:
    adapter, _, store = fake_env(
        messages=[MqttMessage(f"N/{P}/grid/30/Ac/L1/Power", b'{"value": 500.0}')]
    )
    await run_until(adapter, lambda: store.snapshot().ok("grid.l1.power_w") == 500.0)


async def test_subscribes_expected_filters(fake_env) -> None:
    adapter, transports, _ = fake_env()
    await run_until(adapter, lambda: bool(transports) and len(transports[0].subscribed) >= 11)
    assert f"N/{P}/grid/+/#" in transports[0].subscribed
    assert f"N/{P}/settings/0/Settings/DynamicEss/#" in transports[0].subscribed


async def test_subscribes_watchdog_topics(fake_env) -> None:
    adapter, transports, _ = fake_env()
    # der erste Keepalive folgt erst nach allen Abonnements
    await run_until(adapter, lambda: bool(transports) and bool(transports[0].published))
    assert f"N/{P}/bindaems/0/#" in transports[0].subscribed


async def test_portal_discovery_when_not_configured(fake_env) -> None:
    adapter, transports, _ = fake_env(
        portal_id=None,
        messages=[MqttMessage("N/abc123/system/0/Serial", b'{"value": "abc123"}')],
    )
    await run_until(
        adapter, lambda: bool(transports) and "N/abc123/grid/+/#" in transports[0].subscribed
    )
    assert adapter.portal_id == "abc123"


async def test_reconnect_requests_full_republish_again(fake_env) -> None:
    adapter, transports, _ = fake_env(first_fails=True, keepalive_s=0.02)
    await run_until(adapter, lambda: len(transports) == 2 and bool(transports[1].published))
    assert adapter.sleeps[0] == 1.0
    assert transports[1].published[0] == (f"R/{P}/keepalive", b"")


async def test_disconnect_marks_state_signals_stale(fake_env) -> None:
    adapter, _, store = fake_env(
        end_with_error=True,
        messages=[MqttMessage(f"N/{P}/settings/0/Settings/CGwacs/Hub4Mode", b'{"value": 1}')],
    )
    await run_until(adapter, lambda: adapter.health().error_count >= 1)
    reading = store.snapshot().get("ess.hub4_mode")
    assert reading is not None and reading.quality is Quality.STALE


async def test_backoff_resets_after_session_with_messages(fake_env) -> None:
    adapter, _, _ = fake_env(
        first_fails=True,
        end_with_error=True,
        messages=[MqttMessage(f"N/{P}/grid/30/Ac/Power", b'{"value": 1.0}')],
    )
    await run_until(adapter, lambda: len(adapter.sleeps) >= 3)
    assert adapter.sleeps[:3] == [1.0, 1.0, 2.0]


async def test_unchanged_values_stay_valid_while_cerbo_sends(fake_env) -> None:
    holder = []

    def advance(seconds: float):
        return lambda: holder[0].clock.advance(seconds)

    adapter, _, store = fake_env(
        messages=[
            MqttMessage(f"N/{P}/system/0/Dc/Battery/Soc", b'{"value": 55.0}'),
            advance(4),
            MqttMessage(f"N/{P}/system/0/Dc/Battery/Temperature", b'{"value": 21.0}'),
            advance(4),
        ]
    )
    holder.append(adapter)
    start = adapter.clock.now()

    def soc_still_valid_after_8s() -> bool:  # SOC unverändert, aber der Cerbo sendet weiter
        snap = store.snapshot()
        return (snap.ts - start).total_seconds() == 8 and snap.ok("battery.soc_pct") == 55.0

    await run_until(adapter, soc_still_valid_after_8s)


async def test_values_not_resent_after_reconnect_are_stale(fake_env) -> None:
    holder = []
    pv = MqttMessage(f"N/{P}/pvinverter/31/Ac/Power", b'{"value": 3500.0}')
    first = MqttMessage(f"N/{P}/grid/30/Ac/Power", b'{"value": 500.0}')
    second = MqttMessage(f"N/{P}/grid/30/Ac/Power", b'{"value": 600.0}')
    adapter, _, store = fake_env(
        sessions=[[pv, first, lambda: holder[0].clock.advance(1)], [second]]
    )
    holder.append(adapter)

    def pv_stale_after_reconnect() -> bool:  # der PV-Zähler kam nach dem Neustart nicht wieder
        snap = store.snapshot()
        reading = snap.get("pv.huawei.power_w")
        return (
            snap.ok("grid.power_w") == 600.0
            and reading is not None
            and reading.quality is Quality.STALE
        )

    await run_until(adapter, pv_stale_after_reconnect)


async def test_connection_changes_are_logged(fake_env) -> None:
    from structlog.testing import capture_logs

    adapter, _, _ = fake_env(first_fails=True)
    with capture_logs() as logs:
        await run_until(adapter, lambda: adapter.health().connected)
    assert [(e["event"], e["adapter"]) for e in logs] == [
        ("Adapter-Fehler", "victron"),
        ("Adapter verbunden", "victron"),
    ]
