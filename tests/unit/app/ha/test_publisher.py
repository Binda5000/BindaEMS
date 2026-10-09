import asyncio
from collections.abc import Callable

from bindaems.app.ha.publisher import AiomqttHaTransport, HaPublisher, allowed_topic


async def run_publisher_until(publisher: HaPublisher, condition: Callable[[], bool]) -> None:
    task = asyncio.create_task(publisher.run())
    try:
        for _ in range(10_000):
            if condition():
                return
            if task.done():
                task.result()  # Fehler sichtbar machen
            await asyncio.sleep(0)
        raise AssertionError("Bedingung nicht erreicht")
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_connect_publishes_online_discovery_and_states_retained(publisher_env) -> None:
    publisher, transport = publisher_env()
    await run_publisher_until(
        publisher,
        lambda: (
            transport.published_state("price_now") is not None and publisher.component_status().ok
        ),
    )
    assert transport.published[0] == ("bindaems/status", "online", True)
    assert (
        "homeassistant/sensor/bindaems/price_now/config",
        True,
    ) in transport.topics_with_retain()
    assert all(retain for _, _, retain in transport.published)
    assert transport.subscriptions == ["homeassistant/status"]
    assert transport.published_state("price_now") == "13.44"
    assert transport.count("bindaems/state/price_now/attributes") == 1


async def test_last_will_is_offline(cfg) -> None:
    transport = AiomqttHaTransport(cfg.homeassistant.mqtt, None)
    will = transport.will
    assert (will.topic, will.payload, will.qos, will.retain) == (
        "bindaems/status",
        "offline",
        1,
        True,
    )


async def test_only_changed_states_are_republished(publisher_env) -> None:
    publisher, transport = publisher_env()
    await run_publisher_until(
        publisher, lambda: transport.ticks >= 2
    )  # zwei Intervalle ohne Änderung
    assert transport.count("bindaems/state/price_now") == 1


async def test_ha_birth_message_republishes_everything(publisher_env) -> None:
    publisher, transport = publisher_env(incoming=[("homeassistant/status", b"online")])
    await run_publisher_until(
        publisher,
        lambda: transport.count("homeassistant/sensor/bindaems/price_now/config") == 2,
    )
    assert transport.count("bindaems/state/price_now") == 2


async def test_reconnect_after_error_republishes(publisher_env) -> None:
    publisher, transport = publisher_env(fail_first_connection=True)
    await run_publisher_until(
        publisher, lambda: transport.connections == 2 and transport.count("bindaems/status") >= 1
    )
    assert transport.sleeps[0] == 1.0


async def test_status_reports_connection_error(publisher_env) -> None:
    publisher, transport = publisher_env(fail_first_connection=True)
    assert not publisher.component_status().ok
    await run_publisher_until(publisher, lambda: transport.sleeps == [1.0])
    status = publisher.component_status()
    assert not status.ok and "Mosquitto nicht erreichbar" in status.message


def test_only_allowed_topics_are_published(cfg) -> None:
    pattern = allowed_topic(cfg.homeassistant.mqtt)
    assert pattern.fullmatch("homeassistant/sensor/bindaems/price_now/config")
    assert pattern.fullmatch("bindaems/state/price_now/attributes")
    assert pattern.fullmatch("bindaems/status")
    for topic in (
        "bindaems/cmd/mode",
        "homeassistant/select/bindaems/x/config",
        "R/abc/keepalive",
        "W/abc/x",
    ):
        assert not pattern.fullmatch(topic)


async def test_orderly_stop_reports_offline(publisher_env) -> None:
    # Ein geordnetes DISCONNECT verwirft den Last Will; die app muss selbst „offline“ melden
    publisher, transport = publisher_env()
    task = asyncio.create_task(publisher.run())
    for _ in range(10_000):
        if transport.published_state("price_now") is not None:
            break
        await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert transport.published[-1] == ("bindaems/status", "offline", True)
    assert not publisher.component_status().ok
