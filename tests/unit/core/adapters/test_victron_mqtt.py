import asyncio
from collections.abc import AsyncIterator
from typing import Any

import aiomqtt
import pytest
from tests.helpers import run_until
from tests.unit.core.adapters.conftest import FakeTransport, P

from bindaems.core.adapters.victron_mqtt import (
    HEARTBEAT_PATH,
    KEEPALIVE_SUPPRESS,
    MAX_DISCHARGE_PATH,
    MIN_SOC_PATH,
    OUTBOX_SIZE,
    SETPOINT_OVERRIDE_PATH,
    MqttMessage,
    evcharger_path,
    writable_paths,
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


async def connected_session(adapter: Any) -> asyncio.Task[None]:
    """Startet eine Sitzung und kehrt zurück, sobald sie verbunden ist."""
    session = asyncio.create_task(adapter._session())
    async with asyncio.timeout(1.0):
        while not adapter.health().connected:  # noqa: ASYNC110 - der Adapter bietet dafür kein Event
            await asyncio.sleep(0.001)
    return session


async def test_first_keepalive_requests_full_republish(fake_env) -> None:
    adapter, transports, _ = fake_env(keepalive_s=0.02)
    await run_until(adapter, lambda: bool(transports) and len(transports[0].published) >= 3)
    t = transports[0]
    assert t.published[0] == (f"R/{P}/keepalive", b"")
    assert all(payload == KEEPALIVE_SUPPRESS for _, payload in t.published[1:])


async def test_publishes_only_keepalive_and_allowed_writes(fake_env, cfg) -> None:
    adapter, transports, _ = fake_env(
        keepalive_s=0.02, messages=[lambda: adapter.write(HEARTBEAT_PATH, 3)]
    )
    await run_until(adapter, lambda: bool(transports) and len(transports[0].published) >= 3)
    allowed = {f"R/{P}/keepalive"} | {f"W/{P}/{path}" for path in writable_paths(cfg.victron)}
    assert {topic for topic, _ in transports[0].published} <= allowed


async def test_publishes_only_keepalive_without_writes(fake_env) -> None:
    adapter, transports, _ = fake_env(keepalive_s=0.02)
    await run_until(adapter, lambda: bool(transports) and len(transports[0].published) >= 2)
    assert {topic for topic, _ in transports[0].published} == {f"R/{P}/keepalive"}


def test_the_write_paths_are_fixed() -> None:
    assert HEARTBEAT_PATH == "bindaems/0/Heartbeat"
    assert SETPOINT_OVERRIDE_PATH == "hub4/0/Overrides/Setpoint"
    assert MAX_DISCHARGE_PATH == "settings/0/Settings/CGwacs/MaxDischargePower"
    assert MIN_SOC_PATH == "settings/0/Settings/CGwacs/BatteryLife/MinimumSocLimit"
    assert evcharger_path(40, "SetCurrent") == "evcharger/40/SetCurrent"
    assert evcharger_path(41, "StartStop") == "evcharger/41/StartStop"
    assert OUTBOX_SIZE == 100


def test_writable_paths_follow_the_config(cfg) -> None:
    paths = writable_paths(cfg.victron)  # Beispielkonfiguration: evcharger {40: evcs}
    assert paths == {
        "bindaems/0/Heartbeat",
        "hub4/0/Overrides/Setpoint",
        "settings/0/Settings/CGwacs/MaxDischargePower",
        "settings/0/Settings/CGwacs/BatteryLife/MinimumSocLimit",
        "evcharger/40/SetCurrent",
        "evcharger/40/StartStop",
    }


def with_evchargers(cfg, evcharger: dict[int, str]):
    instances = cfg.victron.instances.model_copy(update={"evcharger": evcharger})
    return cfg.victron.model_copy(update={"instances": instances})


def test_writable_paths_without_an_evcharger_instance(cfg) -> None:
    assert writable_paths(with_evchargers(cfg, {})) == {
        HEARTBEAT_PATH,
        SETPOINT_OVERRIDE_PATH,
        MAX_DISCHARGE_PATH,
        MIN_SOC_PATH,
    }


def test_writable_paths_cover_every_evcharger_instance(cfg) -> None:
    paths = writable_paths(with_evchargers(cfg, {40: "evcs", 41: "evcs2"}))
    assert {path for path in paths if path.startswith("evcharger/")} == {
        "evcharger/40/SetCurrent",
        "evcharger/40/StartStop",
        "evcharger/41/SetCurrent",
        "evcharger/41/StartStop",
    }
    assert len(paths) == 8  # die vier festen Pfade und je Instanz zwei


async def test_every_allowed_path_is_published(fake_env, cfg) -> None:
    paths = sorted(writable_paths(cfg.victron))
    results: list[bool] = []
    adapter, transports, _ = fake_env(
        messages=[lambda path=path: results.append(adapter.write(path, 1)) for path in paths]
    )

    def writes() -> list[str]:
        return [topic for topic, _ in transports[0].published if topic.startswith("W/")]

    await run_until(adapter, lambda: bool(transports) and len(writes()) == len(paths))
    assert results == [True] * len(paths)
    assert writes() == [f"W/{P}/{path}" for path in paths]


async def test_the_override_can_be_lifted_with_null(fake_env) -> None:
    adapter, transports, _ = fake_env(
        messages=[lambda: adapter.write(SETPOINT_OVERRIDE_PATH, None)]
    )
    await run_until(
        adapter,
        lambda: (
            bool(transports)
            and (f"W/{P}/hub4/0/Overrides/Setpoint", b'{"value": null}') in transports[0].published
        ),
    )


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


async def test_outbox_does_not_survive_the_session(fake_env, monkeypatch) -> None:
    # Der Sender hängt in der ersten Sitzung am ersten Auftrag fest; der zweite steht noch in der
    # Warteschlange, als die Sitzung endet. Er darf in der nächsten nicht mehr auftauchen.
    publish = FakeTransport.publish
    stuck = asyncio.Event()  # wird nie gesetzt
    first_write: list[str] = []

    async def hang_on_the_first_write(self: FakeTransport, topic: str, payload: bytes) -> None:
        if topic.startswith("W/") and not first_write:
            first_write.append(topic)
            await stuck.wait()
        await publish(self, topic, payload)

    monkeypatch.setattr(FakeTransport, "publish", hang_on_the_first_write)
    adapter, transports, _ = fake_env(
        keepalive_s=0.02,
        sessions=[
            [lambda: adapter.write(HEARTBEAT_PATH, 5), lambda: adapter.write(HEARTBEAT_PATH, 6)],
            [],
        ],
    )
    await run_until(adapter, lambda: len(transports) == 2 and len(transports[1].published) >= 3)
    assert len(first_write) == 1  # der erste Auftrag war unterwegs, der zweite nur eingereiht
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


async def test_dead_sender_ends_the_session(fake_env) -> None:
    adapter, _, _ = fake_env(fail_publish_prefix="W/")  # Veröffentlichen von W/… wirft
    session = asyncio.create_task(adapter._session())
    while not adapter.health().connected:  # noqa: ASYNC110 - der Adapter bietet dafür kein Event
        await asyncio.sleep(0.001)
    assert adapter.write("evcharger/40/SetCurrent", 6) is True  # eingereiht
    with pytest.raises(ConnectionError, match="Hilfs-Task beendet"):
        await asyncio.wait_for(session, 1.0)
    assert adapter.write("evcharger/40/SetCurrent", 6) is False  # ohne Sitzung


async def test_the_error_names_the_dead_sender(fake_env) -> None:
    adapter, _, _ = fake_env(fail_publish_prefix="W/")
    session = await connected_session(adapter)
    assert adapter.write(evcharger_path(40, "SetCurrent"), 6) is True
    with pytest.raises(ConnectionError) as raised:
        await asyncio.wait_for(session, 1.0)
    assert str(raised.value) == "Hilfs-Task beendet: Sender (MqttError: Senden fehlgeschlagen)"


async def test_dead_keepalive_ends_the_session(fake_env, monkeypatch) -> None:
    publish = FakeTransport.publish

    async def failing_publish(self: FakeTransport, topic: str, payload: bytes) -> None:
        # nur der Keepalive im Takt scheitert, nicht der erste nach dem Verbinden
        if payload == KEEPALIVE_SUPPRESS:
            raise aiomqtt.MqttError("Keepalive fehlgeschlagen")
        await publish(self, topic, payload)

    monkeypatch.setattr(FakeTransport, "publish", failing_publish)
    adapter, _, _ = fake_env(keepalive_s=0.01)
    with pytest.raises(ConnectionError) as raised:
        await asyncio.wait_for(adapter._session(), 1.0)
    assert str(raised.value) == (
        "Hilfs-Task beendet: Keepalive (MqttError: Keepalive fehlgeschlagen)"
    )


@pytest.mark.parametrize(("helper", "name"), [("_sender", "Sender"), ("_keepalive", "Keepalive")])
async def test_helper_ending_without_error_ends_the_session(fake_env, helper, name) -> None:
    adapter, _, _ = fake_env()

    async def ended(*args: object) -> None:
        return None

    setattr(adapter, helper, ended)
    with pytest.raises(ConnectionError) as raised:
        await asyncio.wait_for(adapter._session(), 1.0)
    assert str(raised.value) == f"Hilfs-Task beendet: {name} (ohne Fehler)"


async def test_cancelled_helper_ends_the_session_as_a_connection_error(fake_env) -> None:
    # Ein von außen abgebrochener Hilfs-Task gilt nicht als Abbruch des Adapters: Die Sitzung endet
    # mit einem Verbindungsfehler, und run() verbindet neu.
    adapter, _, _ = fake_env()

    async def cancelled(*args: object) -> None:
        task = asyncio.current_task()
        assert task is not None
        task.cancel()
        await asyncio.sleep(0)

    adapter._sender = cancelled
    with pytest.raises(ConnectionError) as raised:
        await asyncio.wait_for(adapter._session(), 1.0)
    assert str(raised.value) == "Hilfs-Task beendet: Sender (abgebrochen)"


async def test_session_end_by_a_helper_leaves_no_task_behind(fake_env) -> None:
    adapter, _, _ = fake_env(fail_publish_prefix="W/")
    session = await connected_session(adapter)
    assert adapter.write(evcharger_path(40, "SetCurrent"), 6) is True
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(session, 1.0)
    assert asyncio.all_tasks() == {asyncio.current_task()}


async def test_dead_sender_leads_to_a_reconnect_with_backoff(fake_env) -> None:
    from structlog.testing import capture_logs

    adapter, transports, _ = fake_env(
        fail_publish_prefix="W/",
        messages=[lambda: adapter.write(evcharger_path(40, "SetCurrent"), 6)],
    )
    # Die zweite Verbindung kommt nicht mehr zustande (der Fake hat keine weitere): der zweite
    # Fehler beweist, dass run() nach der Wartezeit von 1 s neu verbunden hat.
    with capture_logs() as logs:
        await run_until(adapter, lambda: adapter.health().error_count >= 2)
    assert len(transports) == 1
    assert adapter.sleeps[:2] == [1.0, 2.0]
    assert adapter.health().connected is False
    errors = [entry["error"] for entry in logs if entry["event"] == "Adapter-Fehler"]
    assert errors[0] == (
        "ConnectionError: Hilfs-Task beendet: Sender (MqttError: Senden fehlgeschlagen)"
    )


async def test_message_loop_error_ends_the_session_as_before(fake_env) -> None:
    adapter, _, _ = fake_env(end_with_error=True)
    with pytest.raises(ConnectionError, match=r"^Verbindung verloren$"):
        await asyncio.wait_for(adapter._session(), 1.0)
    assert asyncio.all_tasks() == {asyncio.current_task()}


async def test_message_loop_error_wins_when_the_sender_dies_in_the_same_step(fake_env) -> None:
    # Die Verbindung geht verloren: Nachrichtenschleife und Sender scheitern gleichzeitig. Die
    # Sitzung meldet, was die Nachrichtenschleife meldet, wie bisher.
    adapter, _, _ = fake_env(
        fail_publish_prefix="W/",
        sessions=[[lambda: adapter.write(evcharger_path(40, "SetCurrent"), 6)], []],
    )
    with pytest.raises(ConnectionError, match=r"^Verbindung verloren$"):
        await asyncio.wait_for(adapter._session(), 1.0)


@pytest.fixture
def messages_end_at_once(monkeypatch) -> None:
    """Die Nachrichtenschleife des Transports endet sofort und ohne Fehler."""

    async def no_messages(self: FakeTransport) -> AsyncIterator[MqttMessage]:
        return
        yield  # macht daraus einen asynchronen Generator

    monkeypatch.setattr(FakeTransport, "messages", no_messages)


@pytest.mark.usefixtures("messages_end_at_once")
async def test_message_loop_ending_without_error_ends_the_session(fake_env) -> None:
    adapter, _, _ = fake_env()
    await asyncio.wait_for(adapter._session(), 1.0)  # endet ohne Ausnahme
    assert asyncio.all_tasks() == {asyncio.current_task()}


@pytest.mark.usefixtures("messages_end_at_once")
async def test_run_reconnects_after_the_message_loop_ended(fake_env) -> None:
    from structlog.testing import capture_logs

    adapter, _, _ = fake_env()
    with capture_logs() as logs:
        await run_until(adapter, lambda: adapter.health().error_count >= 1)
    errors = [entry["error"] for entry in logs if entry["event"] == "Adapter-Fehler"]
    assert errors[0] == "Verbindung beendet"
    assert adapter.sleeps[0] == 1.0


async def test_dead_sender_does_not_mask_the_cancellation(fake_env, monkeypatch) -> None:
    # Der Abbruch des Adapters trifft die Sitzung im selben Schritt, in dem der Sender scheitert:
    # Der Abbau darf den Fehler des Senders schlucken, aber nicht den Abbruch.
    publish = FakeTransport.publish
    adapter_task: list[asyncio.Task[None]] = []

    async def cancel_then_fail(self: FakeTransport, topic: str, payload: bytes) -> None:
        if topic.startswith("W/"):
            adapter_task[0].cancel()
            raise ConnectionError("Verbindung verloren")
        await publish(self, topic, payload)

    monkeypatch.setattr(FakeTransport, "publish", cancel_then_fail)
    adapter, _, _ = fake_env(messages=[lambda: adapter.write(HEARTBEAT_PATH, 1)])
    adapter_task.append(asyncio.create_task(adapter.run()))
    done, _ = await asyncio.wait(adapter_task, timeout=1.0)
    assert done == {adapter_task[0]}
    assert adapter_task[0].cancelled()
    assert asyncio.all_tasks() == {asyncio.current_task()}


async def test_write_rejects_other_paths(fake_env) -> None:
    adapter, _, _ = fake_env()
    with pytest.raises(ValueError, match="Schreibpfad nicht freigegeben"):
        adapter.write("settings/0/Settings/CGwacs/Hub4Mode", 3)


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
        "hub4/0/Overrides/Setpoint/",
        "/hub4/0/Overrides/Setpoint",
        "hub4/1/Overrides/Setpoint",
        "hub4/0/Overrides/MaxChargePower",
        "settings/0/Settings/CGwacs/MaxChargePower",
        "settings/0/Settings/CGwacs/BatteryLife/State",
        "settings/0/Settings/DynamicEss/Mode",
        "evcharger/40/Mode",
        "evcharger/40/MaxCurrent",
        "evcharger/40/SetCurrent/",
        "evcharger/41/SetCurrent",  # Instanz nicht in der Konfiguration
        "evcharger/0/StartStop",
        f"W/{P}/evcharger/40/SetCurrent",
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
