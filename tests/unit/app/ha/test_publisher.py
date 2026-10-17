import asyncio
from collections.abc import Callable

from structlog.testing import capture_logs

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


async def test_dropped_session_reconnects_and_republishes(publisher_env) -> None:
    publisher, transport = publisher_env(stream_error=ConnectionError("Verbindung verloren"))
    await run_publisher_until(
        publisher,
        lambda: (
            transport.count("homeassistant/sensor/bindaems/price_now/config") == 2
            and publisher.component_status().ok
        ),
    )
    assert transport.connections == 2
    assert transport.sleeps == [1.0]
    assert transport.count("bindaems/state/price_now") == 2


async def test_status_names_the_cause_of_a_dropped_session(publisher_env) -> None:
    # Die Aufgabengruppe der Sitzung verpackt den Fehler; der Status nennt die Ursache
    publisher, transport = publisher_env(stream_error=ConnectionError("Verbindung verloren"))
    await run_publisher_until(publisher, lambda: transport.sleeps == [1.0])
    status = publisher.component_status()
    assert status.message == "MQTT nicht verbunden: ConnectionError: Verbindung verloren"


async def test_ended_message_stream_reconnects(publisher_env) -> None:
    publisher, transport = publisher_env(stream_ends=True)
    await run_publisher_until(
        publisher, lambda: transport.connections == 2 and publisher.component_status().ok
    )
    assert transport.sleeps == [1.0]
    assert transport.count("homeassistant/sensor/bindaems/price_now/config") == 2


def test_only_allowed_topics_are_published(cfg) -> None:
    pattern = allowed_topic(cfg.homeassistant.mqtt)
    assert pattern.fullmatch("homeassistant/sensor/bindaems/price_now/config")
    assert pattern.fullmatch("bindaems/state/price_now/attributes")
    assert pattern.fullmatch("bindaems/status")
    assert pattern.fullmatch("bindaems/cmd/tesla_mode")
    for platform in ("select", "switch", "button", "number"):
        assert pattern.fullmatch(f"homeassistant/{platform}/bindaems/tesla_mode/config")
    for topic in (
        "bindaems/cmd/Mode",
        "bindaems/cmd/mode/extra",
        "homeassistant/light/bindaems/x/config",
        "homeassistant/cover/bindaems/x/config",
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


class Commands:
    def __init__(self, error: Exception | None = None) -> None:
        self.received: list[tuple[str, str]] = []
        self._error = error

    async def __call__(self, object_id: str, payload: str) -> None:
        self.received.append((object_id, payload))
        if self._error is not None:
            raise self._error


async def test_commands_are_subscribed_and_forwarded(publisher_env) -> None:
    commands = Commands()
    publisher, transport = publisher_env(
        incoming=[("bindaems/cmd/regulation", b"ON")], commands=commands
    )
    await run_publisher_until(publisher, lambda: transport.count("bindaems/state/regulation") == 2)
    assert commands.received == [("regulation", "ON")]
    assert transport.subscriptions == ["homeassistant/status", "bindaems/cmd/+"]
    # Zustand nach dem Befehl neu, obwohl er sich nicht geändert hat; nur diese Entität
    assert transport.count("bindaems/state/price_now") == 1


async def test_without_a_handler_commands_are_not_subscribed(publisher_env) -> None:
    publisher, transport = publisher_env(incoming=[("bindaems/cmd/regulation", b"ON")])
    await run_publisher_until(publisher, lambda: transport.published_state("price_now") is not None)
    assert transport.subscriptions == ["homeassistant/status"]
    assert transport.count("bindaems/state/regulation") == 1


async def test_failing_handler_does_not_stop_the_session_and_state_is_republished(
    publisher_env,
) -> None:
    commands = Commands(RuntimeError("kaputt"))
    publisher, transport = publisher_env(
        incoming=[("bindaems/cmd/regulation", b"ON"), ("bindaems/cmd/guest_mode", b"Aus")],
        commands=commands,
    )
    await run_publisher_until(
        publisher,
        lambda: (
            transport.count("bindaems/state/regulation") == 2
            and transport.count("bindaems/state/guest_mode") == 2
            and publisher.component_status().ok
        ),
    )
    assert [oid for oid, _ in commands.received] == ["regulation", "guest_mode"]
    assert transport.connections == 1


async def test_payload_is_decoded_and_stripped(publisher_env) -> None:
    commands = Commands()
    publisher, _ = publisher_env(
        incoming=[("bindaems/cmd/tesla_mode", b"Min+PV \n")], commands=commands
    )
    await run_publisher_until(publisher, lambda: commands.received)
    assert commands.received == [("tesla_mode", "Min+PV")]


async def test_unknown_or_stateless_entities_publish_no_state(publisher_env) -> None:
    commands = Commands()
    publisher, transport = publisher_env(
        incoming=[("bindaems/cmd/tesla_boost", b"PRESS"), ("bindaems/cmd/nothing", b"x")],
        commands=commands,
    )
    await run_publisher_until(publisher, lambda: len(commands.received) == 2)
    for _ in range(50):
        await asyncio.sleep(0)
    assert transport.count("bindaems/state/tesla_boost") == 0
    assert transport.count("bindaems/state/nothing") == 0


async def test_retained_commands_are_ignored(publisher_env) -> None:
    # Gehaltene Befehle kämen bei jedem Neustart und jeder neuen Verbindung wieder
    commands = Commands()
    publisher, transport = publisher_env(
        incoming=[
            ("bindaems/cmd/regulation", b"ON", True),
            ("bindaems/cmd/guest_mode", b"Aus", False),
        ],
        commands=commands,
    )
    with capture_logs() as logs:
        await run_publisher_until(publisher, lambda: commands.received)
        for _ in range(50):
            await asyncio.sleep(0)
    assert commands.received == [("guest_mode", "Aus")]
    assert (
        transport.count("bindaems/state/regulation") == 1
    )  # kein Befehl, keine Neuveröffentlichung
    ignored = [entry for entry in logs if entry["event"] == "Gehaltener Befehl ignoriert"]
    assert [entry["topic"] for entry in ignored] == ["bindaems/cmd/regulation"]


async def test_commands_are_subscribed_again_after_a_reconnect(publisher_env) -> None:
    publisher, transport = publisher_env(
        stream_error=ConnectionError("Verbindung verloren"), commands=Commands()
    )
    await run_publisher_until(
        publisher, lambda: transport.connections == 2 and publisher.component_status().ok
    )
    assert transport.subscriptions == ["homeassistant/status", "bindaems/cmd/+"] * 2


def test_number_topics_are_allowed(cfg) -> None:
    pattern = allowed_topic(cfg.homeassistant.mqtt)
    assert pattern.fullmatch("homeassistant/number/bindaems/support_soc/config")
    assert pattern.fullmatch("bindaems/state/support_soc")
    assert pattern.fullmatch("bindaems/cmd/support_soc")
    assert not pattern.fullmatch("homeassistant/number/other/support_soc/config")
    assert not pattern.fullmatch("homeassistant/numbers/bindaems/support_soc/config")
