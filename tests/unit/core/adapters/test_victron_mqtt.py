from tests.helpers import run_until
from tests.unit.core.adapters.conftest import P

from bindaems.core.adapters.victron_mqtt import KEEPALIVE_SUPPRESS, MqttMessage
from bindaems.shared.domain import Quality


async def test_first_keepalive_requests_full_republish(fake_env) -> None:
    adapter, transports, _ = fake_env(keepalive_s=0.02)
    await run_until(adapter, lambda: bool(transports) and len(transports[0].published) >= 3)
    t = transports[0]
    assert t.published[0] == (f"R/{P}/keepalive", b"")
    assert all(payload == KEEPALIVE_SUPPRESS for _, payload in t.published[1:])


async def test_publishes_only_keepalive_topic(fake_env) -> None:
    adapter, transports, _ = fake_env(keepalive_s=0.02)
    await run_until(adapter, lambda: bool(transports) and len(transports[0].published) >= 2)
    assert {topic for topic, _ in transports[0].published} == {f"R/{P}/keepalive"}


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
