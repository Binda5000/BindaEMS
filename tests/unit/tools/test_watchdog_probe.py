import asyncio
import json
import re
import signal
import statistics
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import yaml
from aiomqtt import MqttError
from bindaems_watchdog import VERSION
from bindaems_watchdog.actions import ActionRunner
from bindaems_watchdog.config import parse_settings
from bindaems_watchdog.logic import ARMED, TRIPPED, WAITING, WatchdogLogic
from bindaems_watchdog.service import WatchdogService
from bindaems_watchdog.state_file import StoredState
from tests.unit.cerbo_watchdog.fakes import FakeAccess

from bindaems.core.adapters.victron_mqtt import KEEPALIVE_SUPPRESS, MqttMessage
from bindaems.shared.config import Config
from bindaems.shared.timeutil import LOCAL_TZ
from bindaems.tools import watchdog_probe
from bindaems.tools.verify_checks import Finding
from bindaems.tools.watchdog_config import RETRIES, RETRY_S
from bindaems.tools.watchdog_probe import ProbeTimings, main, run_probe, timings_from_config

P = "c0619ab1234"
OTHER = "d1730bc2345"  # eine fremde Anlage am selben Broker
NOTIFY = f"N/{P}/bindaems/0/"
HEARTBEAT = f"W/{P}/bindaems/0/Heartbeat"
KEEPALIVE = f"R/{P}/keepalive"
SIM_RETRIES = 2  # Wiederholungen einer gescheiterten Aktion im nachgebildeten Cerbo …
SIM_RETRY_S = 0.05  # … und ihr Abstand: Ein Lauf mit Fehlern dauert 0,1 s.
FAST = ProbeTimings(
    heartbeat_s=0.05,
    answer_wait_s=1.0,
    arm_wait_s=2.0,
    timeout_s=0.8,
    trip_slack_s=1.0,
    actions_wait_s=(SIM_RETRIES + 1) * SIM_RETRY_S + 0.15,  # 0,3 s
    rearm_wait_s=2.0,
    release_wait_s=1.0,
)
# Zusätze der Zeile „Auslösung ohne Heartbeat“, wenn die Fehlerzahl noch nicht der Endstand ist
IN_THE_PAUSE = " (Abbruch vor dem Ende des Aktionslaufs)"  # die Probe endete in der Pause
NOT_REARMED = " (nicht wieder scharf; Zahl noch nicht der Endstand)"
TITLES = [
    "Watchdog erreichbar",
    "Watchdog-Version",
    "Scharf nach Heartbeat",
    "Auslösung ohne Heartbeat",
    "Wieder scharf",
    "Freigabe ohne Auslösung",
]

# Die Aktionen aus ``config.ini.example``: EVCS (evcharger mit Instanz 40), Min-SOC, Entladegrenze.
EXAMPLE = parse_settings(Path("cerbo-watchdog/config.ini.example").read_text(encoding="utf-8"))
EVCS_ITEM = ("com.victronenergy.evcharger.sim", "/SetCurrent")
MIN_SOC_ITEM = ("com.victronenergy.settings", "/Settings/CGwacs/BatteryLife/MinimumSocLimit")
DISCHARGE_ITEM = ("com.victronenergy.settings", "/Settings/CGwacs/MaxDischargePower")


def plant(*, evcs: bool = True, failing_sets: int = 0) -> FakeAccess:
    """Die Werte am nachgebildeten Cerbo, alle abseits ihres Sicherwerts: 16 A, 30 %, 5000 W.

    ``evcs=False``: Es gibt keinen evcharger mit der Instanz 40. Die ersten ``failing_sets``
    Schreibzugriffe lehnt der Cerbo ab.
    """
    services = {("com.victronenergy.evcharger", 40): EVCS_ITEM[0]} if evcs else {}
    values = {EVCS_ITEM: 16, MIN_SOC_ITEM: 30, DISCHARGE_ITEM: 5000}
    return FakeAccess(values, services, failing_sets)


class SimulatedCerbo:
    """Der Cerbo als ``MqttTransport`` mit dem echten ``WatchdogService`` und kurzen Zeiten.

    Logik, Aktionslauf und Veröffentlichung sind die des Dienstes am Cerbo, mit den Aktionen aus
    ``config.ini.example``; nur D-Bus ersetzt ``access`` (Standard: ``plant()``). Mit der Auslösung
    steht ``LastTripFailures`` also auf 0 und bekommt den Endstand, wenn der Lauf endet; verlässt
    der Watchdog vorher ``TRIPPED``, bricht der Lauf ab wie am Cerbo. Wie dbus-flashmq meldet der
    Cerbo einen Pfad, wenn sich sein Wert ändert, und auf den leeren Keepalive alle Pfade. Der Takt
    kommt alle 10 ms. Die Abstände sind großzügig (Heartbeat der Probe 0,05 s gegen 0,5 s Lücke),
    damit der Test auf einer belasteten CI nicht flackert. ``enabled=False`` ergibt einen
    deaktivierten Watchdog.

    Mit ``failures_after_rearm`` meldet der Cerbo den Endstand von ``LastTripFailures`` erst beim
    Wechsel nach „scharf“, der auf eine Auslösung folgt: Bis dahin bleibt der Pfad auf 0, dann
    springt er auf diesen Wert, noch vor dem Zustand (wie bei einem Abbruch des Aktionslaufs).
    """

    def __init__(
        self,
        *,
        access: FakeAccess | None = None,
        version: str = VERSION,
        enabled: bool = True,
        timeout_s: float = 0.8,
        rearm_s: float = 0.15,
        gap_s: float = 0.5,
        retries: int = SIM_RETRIES,
        retry_s: float = SIM_RETRY_S,
        failures_after_rearm: int | None = None,
    ) -> None:
        settings = replace(
            EXAMPLE,
            enabled=enabled,
            timeout_s=timeout_s,
            rearm_s=rearm_s,
            gap_s=gap_s,
            retries=retries,
            retry_s=retry_s,
        )
        self.access = access if access is not None else plant()
        self._failures_after_rearm = failures_after_rearm
        self._logic = WatchdogLogic(settings, armed_at_start=False, now=time.monotonic())
        self._service = WatchdogService(
            settings,
            self._logic,
            ActionRunner(self.access, settings.actions, settings.retries, settings.retry_s),
            StoredState(),
            publish=self._set,
            save=lambda _state: None,
            wall=time.time,
            monotonic=time.monotonic,
        )
        self._paths = {**self._service.values(), "/Version": version}  # wie auf D-Bus
        self._messages: asyncio.Queue[MqttMessage] = asyncio.Queue()
        self._ticker: asyncio.Task[None] | None = None
        self.subscribed: list[str] = []
        self.published: list[tuple[str, bytes]] = []
        self.published_at: list[float] = []
        self.last_beat_at = time.monotonic()
        self.tripped_at: float | None = None  # wann /State zuletzt auf „ausgelöst“ ging
        # Haken für Tests: wird nach jeder Veröffentlichung der Probe und nach jedem Takt gerufen
        self.on_change: Callable[[SimulatedCerbo], None] | None = None

    @property
    def state(self) -> int:
        return self._logic.state

    @property
    def trip_count(self) -> object:
        return self._service.values()["/TripCount"]

    def heartbeats(self) -> list[int]:
        """Die gesendeten Heartbeat-Werte der Probe, in der Reihenfolge des Sendens."""
        return [json.loads(p)["value"] for topic, p in self.published if topic == HEARTBEAT]

    def silent_for(self) -> float:
        """Sekunden seit dem letzten Heartbeat."""
        return time.monotonic() - self.last_beat_at

    async def __aenter__(self) -> "SimulatedCerbo":
        self._ticker = asyncio.create_task(self._tick_every_10_ms())
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._ticker is not None
        self._ticker.cancel()
        await asyncio.gather(self._ticker, return_exceptions=True)

    async def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)
        if topic == "N/+/system/0/Serial":  # so findet die Probe die Portal-ID
            self._messages.put_nowait(MqttMessage(f"N/{P}/system/0/Serial", b'{"value": "HQ0"}'))

    async def publish(self, topic: str, payload: bytes) -> None:
        self.published.append((topic, payload))
        self.published_at.append(time.monotonic())
        if topic == HEARTBEAT:
            self._service.on_heartbeat("/Heartbeat", json.loads(payload)["value"])
            self.last_beat_at = time.monotonic()
        elif topic == KEEPALIVE and payload == b"":  # leer: alles neu veröffentlichen
            for path, value in self._paths.items():
                self._notify(path[1:], value)
        if self.on_change is not None:
            self.on_change(self)

    async def messages(self) -> AsyncIterator[MqttMessage]:
        while True:
            yield await self._messages.get()

    async def _tick_every_10_ms(self) -> None:
        while True:
            await asyncio.sleep(0.01)
            self._service.tick()
            if self.on_change is not None:
                self.on_change(self)

    def _set(self, path: str, value: object) -> None:
        """``publish`` des Dienstes: Wie ein D-Bus-Pfad meldet er nur eine Änderung."""
        if self._paths.get(path) == value:
            return
        if self._failures_after_rearm is not None:
            if path == "/LastTripFailures":
                return  # der Endstand erscheint erst, wenn der Watchdog wieder scharf ist
            if path == "/State" and value == ARMED and self.tripped_at is not None:
                self._announce("/LastTripFailures", self._failures_after_rearm)  # vor dem Zustand
        if path == "/State" and value == TRIPPED:
            self.tripped_at = time.monotonic()
        self._announce(path, value)

    def _announce(self, path: str, value: object) -> None:
        """Setzt den Pfad und meldet ihn, wenn sich der Wert ändert."""
        if self._paths.get(path) == value:
            return
        self._paths[path] = value
        self._notify(path[1:], value)

    def _notify(self, name: str, value: object) -> None:
        payload = json.dumps({"value": value}).encode()
        self._messages.put_nowait(MqttMessage(NOTIFY + name, payload))


class GarbledCerbo(SimulatedCerbo):
    """Streut Unlesbares und Fremdes ein und veröffentlicht die Zähler als ``null``."""

    async def messages(self) -> AsyncIterator[MqttMessage]:
        yield MqttMessage(f"N/{OTHER}/battery/0/Soc", b'{"value": 50}')  # fremde Anlage
        yield MqttMessage(NOTIFY + "State", b"")  # Wert entfernt
        yield MqttMessage(NOTIFY + "State", b"kein JSON")
        yield MqttMessage(NOTIFY + "State", b'["Liste"]')
        yield MqttMessage(NOTIFY + "Fremd", b'{"value": 1}')  # unbekannter Pfad
        async for message in super().messages():
            yield message

    def _notify(self, name: str, value: object) -> None:
        super()._notify(name, None if name in {"TripCount", "LastTripFailures"} else value)
        if name == "State":  # die fremde Anlage meldet dazu etwas anderes
            self._messages.put_nowait(MqttMessage(f"N/{OTHER}/bindaems/0/State", b'{"value": 3}'))


class StuckCerbo(SimulatedCerbo):
    """Ignoriert die Freigabe 0: Der Watchdog bleibt scharf."""

    async def publish(self, topic: str, payload: bytes) -> None:
        if topic == HEARTBEAT and json.loads(payload)["value"] == 0:
            return
        await super().publish(topic, payload)


class DroppingCerbo(SimulatedCerbo):
    """Der Nachrichtenstrom reißt ab, sobald der Watchdog ``drop_at`` meldet; Senden geht weiter."""

    def __init__(self, drop_at: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._drop_at = drop_at
        self.dropped = False

    async def messages(self) -> AsyncIterator[MqttMessage]:
        async for message in super().messages():
            yield message
            if (
                message.topic == NOTIFY + "State"
                and json.loads(message.payload)["value"] == self._drop_at
            ):
                self.dropped = True
                raise ConnectionError("Verbindung verloren")


class DeadCerbo(DroppingCerbo):
    """Nach dem Abriss lässt sich auch nichts mehr senden."""

    async def publish(self, topic: str, payload: bytes) -> None:
        if self.dropped:
            raise ConnectionError("nicht verbunden")
        await super().publish(topic, payload)


class HangingCerbo(DroppingCerbo):
    """Nach dem Abriss kehrt das Senden nie zurück."""

    hanging = False

    async def publish(self, topic: str, payload: bytes) -> None:
        if self.dropped:
            self.hanging = True
            await asyncio.Event().wait()
        await super().publish(topic, payload)


class HangingRelease(SimulatedCerbo):
    """Die Freigabe 0 hängt: Das Senden kehrt für sie nie zurück."""

    hanging = False

    async def publish(self, topic: str, payload: bytes) -> None:
        if topic == HEARTBEAT and json.loads(payload)["value"] == 0:
            self.hanging = True
            await asyncio.Event().wait()
        await super().publish(topic, payload)


class BuggyCerbo(SimulatedCerbo):
    """Ein Fehler der Probe selbst: Der erste Heartbeat nach der Auslösung löst einen Fehler aus."""

    async def publish(self, topic: str, payload: bytes) -> None:
        if topic == HEARTBEAT and self.state == TRIPPED and json.loads(payload)["value"] > 0:
            raise RuntimeError("kaputt")
        await super().publish(topic, payload)


class RefusingCerbo(SimulatedCerbo):
    """Das Abonnieren schlägt fehl, noch vor dem ersten Heartbeat."""

    async def subscribe(self, topic: str) -> None:
        raise ConnectionError("Abo abgelehnt")


class InterruptedCerbo(SimulatedCerbo):
    """Strg+C kommt, sobald der dritte Heartbeat gesendet ist."""

    async def publish(self, topic: str, payload: bytes) -> None:
        await super().publish(topic, payload)
        if topic == HEARTBEAT and len(self.heartbeats()) == 3:
            signal.raise_signal(signal.SIGINT)


class SilentTransport:
    """Nimmt alles an und liefert keine Nachrichten."""

    async def __aenter__(self) -> "SilentTransport":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def subscribe(self, topic: str) -> None:
        return None

    async def publish(self, topic: str, payload: bytes) -> None:
        return None

    async def messages(self) -> AsyncIterator[MqttMessage]:
        await asyncio.Event().wait()  # blockiert bis zum Abbruch
        yield MqttMessage("", b"")  # nie erreicht; macht die Methode zum Async-Generator


class PartialAnswer(SilentTransport):
    """Antwortet auf den Keepalive nur mit den angegebenen Werten."""

    def __init__(self, **values: object) -> None:
        self._values = values

    async def messages(self) -> AsyncIterator[MqttMessage]:
        for name, value in self._values.items():
            yield MqttMessage(NOTIFY + name, json.dumps({"value": value}).encode())
        await asyncio.Event().wait()  # blockiert bis zum Abbruch


class EndingTransport(SilentTransport):
    """Antwortet auf den Keepalive, dann endet der Nachrichtenstrom (mit ``error`` oder ohne)."""

    def __init__(self, error: Exception | None) -> None:
        self._error = error

    async def messages(self) -> AsyncIterator[MqttMessage]:
        yield MqttMessage(NOTIFY + "State", b'{"value": 0}')
        yield MqttMessage(NOTIFY + "Version", f'{{"value": "{VERSION}"}}'.encode())
        if self._error is not None:
            raise self._error


async def test_probe_passes_against_simulated_watchdog() -> None:
    async with SimulatedCerbo() as cerbo:
        findings = await run_probe(cerbo, P, FAST)
    assert [f.title for f in findings] == TITLES
    assert {f.status for f in findings} == {"ok"}


async def test_trip_failures_are_read_after_rearming() -> None:
    # meldet den Endstand erst beim Wiederscharfwerden
    async with SimulatedCerbo(failures_after_rearm=1) as cerbo:
        findings = await run_probe(cerbo, P, FAST)
    trip = next(f for f in findings if f.title == "Auslösung ohne Heartbeat")
    assert "fehlgeschlagene Aktionen 1" in trip.detail
    assert trip.detail.endswith("fehlgeschlagene Aktionen 1")  # der Endstand, ohne Vorbehalt
    assert [f.title for f in findings] == TITLES  # die Reihenfolge der Zeilen bleibt
    assert [f.status for f in findings] == ["ok", "ok", "ok", "warn", "ok", "ok"]


@pytest.mark.parametrize(
    "make_cerbo",
    [
        # Der Endstand (1) erscheint erst beim Wiederscharfwerden, und das dauert 0,5 s.
        pytest.param(lambda: SimulatedCerbo(failures_after_rearm=1, rearm_s=0.5), id="Endstand"),
        # Der Aktionslauf hängt in Wiederholungen und läuft noch, wenn die Probe die Zahl liest.
        pytest.param(
            lambda: SimulatedCerbo(access=plant(failing_sets=1000), retries=1000, rearm_s=0.5),
            id="Lauf läuft noch",
        ),
    ],
)
async def test_failure_count_is_no_all_clear_when_the_watchdog_is_not_rearmed(
    make_cerbo: Callable[[], SimulatedCerbo],
) -> None:
    # Die Wartezeit für „Wieder scharf“ (0,1 s) ist kürzer als das Wiederscharfwerden (0,5 s). Die
    # Zahl, die die Probe bis dahin gelesen hat (0), ist nicht der Endstand: Die Zeile zur
    # Auslösung darf nicht „OK, 0“ lauten, sondern ist eine Warnung mit dem Grund.
    async with make_cerbo() as cerbo:
        findings = await run_probe(cerbo, P, replace(FAST, rearm_wait_s=0.1))
    assert [(f.title, f.status) for f in findings] == [
        ("Watchdog erreichbar", "ok"),
        ("Watchdog-Version", "ok"),
        ("Scharf nach Heartbeat", "ok"),
        ("Auslösung ohne Heartbeat", "warn"),
        ("Wieder scharf", "fail"),
        ("Freigabe ohne Auslösung", "ok"),
    ]
    assert re.fullmatch(
        r"ausgelöst nach [12] s, Auslösungen 0 → 1, fehlgeschlagene Aktionen 0"
        + re.escape(NOT_REARMED),
        findings[3].detail,
    )


async def test_probe_without_answer_stops_after_first_step() -> None:
    async with SilentTransport() as silent:
        findings = await run_probe(silent, P, FAST)
    assert findings == [
        Finding(
            "17.2-5", "Watchdog erreichbar", "fail", "Keine Antwort von com.victronenergy.bindaems."
        )
    ]


def test_main_aborts_without_confirmation(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    transport = Mock(side_effect=AssertionError("keine Verbindung"))
    monkeypatch.setattr("bindaems.tools.watchdog_probe.AiomqttTransport", transport)
    assert main(["--config", "deploy/config.example.yaml"]) == 1
    transport.assert_not_called()


async def test_probe_reports_each_step_with_its_values() -> None:
    async with SimulatedCerbo() as cerbo:
        findings = await run_probe(cerbo, P, FAST)
    assert {f.item for f in findings} == {"17.2-5"}
    details = [f.detail for f in findings]
    assert details[0] == f"Version {VERSION}, Zustand wartet"
    assert details[1] == VERSION
    assert re.fullmatch(r"scharf nach \d+ s", details[2])
    assert re.fullmatch(  # 0,8 s Timeout seit dem letzten Heartbeat: gerundet 1 s
        r"ausgelöst nach [12] s, Auslösungen 0 → 1, fehlgeschlagene Aktionen 0", details[3]
    )
    assert re.fullmatch(r"scharf nach \d+ s", details[4])
    assert details[5] == "Heartbeat 0: Zustand wartet"
    assert asyncio.all_tasks() == {asyncio.current_task()}  # kein Hilfstask läuft weiter


async def test_probe_counts_failed_actions_at_the_end_of_the_trip_run() -> None:
    # Mit der Auslösung steht /LastTripFailures auf 0; der Endstand kommt erst nach allen
    # Wiederholungen. Hier scheitern alle drei Aktionen: keine EVCS, jedes Schreiben abgelehnt.
    async with SimulatedCerbo(access=plant(evcs=False, failing_sets=1000)) as cerbo:
        findings = await run_probe(cerbo, P, FAST)
    assert [f.status for f in findings] == ["ok", "ok", "ok", "warn", "ok", "ok"]
    assert findings[3].detail.endswith("Auslösungen 0 → 1, fehlgeschlagene Aktionen 3")


# Zeiten wie am Cerbo, durch 100 geteilt: Timeout 90 s, wieder scharf nach 30 s, Lücke 25 s,
# 6 Wiederholungen im Abstand von 5 s; die Heartbeats der Probe kommen ohne Verzögerung an.
CERBO_TIMES = {"timeout_s": 0.9, "rearm_s": 0.3, "gap_s": 0.25, "retries": 6, "retry_s": 0.05}
CERBO_PROBE = ProbeTimings(
    heartbeat_s=0.05,
    answer_wait_s=1.0,
    arm_wait_s=1.0,
    timeout_s=0.9,
    trip_slack_s=1.0,
    actions_wait_s=0.8,  # mehr als 6 × 0,05 s, auch auf einer belasteten CI
    rearm_wait_s=1.0,
    release_wait_s=1.0,
)


@pytest.mark.parametrize("rearm_s", [0.3, 0.2], ids=["wie am Cerbo", "kürzer als der Lauf"])
async def test_probe_reports_a_failing_evcs_action_with_fast_heartbeats(rearm_s: float) -> None:
    # Befund aus dem Gesamtreview: Am Cerbo fehlt die EVCS (kein evcharger mit Instanz 40). Ihre
    # Wiederholungen dauern 6 × 5 s, so lange wie das Wiederscharfwerden. Nahm die Probe die
    # Heartbeats gleich nach der Auslösung wieder auf, brach das Wiederscharfwerden den Lauf ab,
    # bevor die EVCS als fehlgeschlagen zählte, und die Probe meldete „0“ und OK.
    times = CERBO_TIMES | {"rearm_s": rearm_s}
    async with SimulatedCerbo(access=plant(evcs=False), **times) as cerbo:
        findings = await run_probe(cerbo, P, CERBO_PROBE)
    assert [f.status for f in findings] == ["ok", "ok", "ok", "warn", "ok", "ok"]
    assert findings[3].detail.endswith("Auslösungen 0 → 1, fehlgeschlagene Aktionen 1")
    assert cerbo.access.values[MIN_SOC_ITEM] == 20  # die übrigen Aktionen griffen
    assert cerbo.access.values[DISCHARGE_ITEM] == -1


async def test_probe_lets_the_action_run_finish_before_the_heartbeats_resume() -> None:
    # Der Cerbo lehnt die ersten fünf Runden ab (je drei Schreibzugriffe); die sechste gelingt,
    # 0,25 s nach der Auslösung und damit nach dem Wiederscharfwerden (0,15 s), hätte die Probe
    # die Heartbeats gleich wieder aufgenommen.
    async with SimulatedCerbo(access=plant(failing_sets=15), retries=6) as cerbo:
        findings = await run_probe(cerbo, P, replace(FAST, actions_wait_s=0.6))
    assert [f.status for f in findings] == ["ok"] * 6
    assert findings[3].detail.endswith("fehlgeschlagene Aktionen 0")
    assert cerbo.access.values[EVCS_ITEM] == 6  # der sichere Strom aus config.ini.example
    assert (cerbo.access.values[MIN_SOC_ITEM], cerbo.access.values[DISCHARGE_ITEM]) == (20, -1)
    assert cerbo.tripped_at is not None
    resumed = min(
        at
        for (topic, _), at in zip(cerbo.published, cerbo.published_at, strict=True)
        if topic == HEARTBEAT and at > cerbo.tripped_at
    )
    assert resumed - cerbo.tripped_at >= 0.6  # die Pause ohne Heartbeat


async def test_probe_reports_a_disabled_watchdog_of_another_version() -> None:
    timings = replace(
        FAST,
        arm_wait_s=0.51,
        timeout_s=0.3,
        trip_slack_s=0.4,
        rearm_wait_s=0.51,
        release_wait_s=0.2,
    )
    async with SimulatedCerbo(version="0.9.0", enabled=False) as cerbo:
        findings = await run_probe(cerbo, P, timings)
    assert [(f.title, f.status, f.detail) for f in findings] == [
        ("Watchdog erreichbar", "ok", "Version 0.9.0, Zustand deaktiviert"),
        ("Watchdog-Version", "warn", f"0.9.0, erwartet {VERSION}"),
        ("Scharf nach Heartbeat", "fail", "nach 1 s nicht scharf (Zustand deaktiviert)"),
        ("Auslösung ohne Heartbeat", "fail", "nach 1 s nicht ausgelöst"),
        ("Wieder scharf", "fail", "nach 1 s nicht scharf (Zustand deaktiviert)"),
        ("Freigabe ohne Auslösung", "fail", "Zustand deaktiviert statt wartet"),
    ]


async def test_probe_gives_each_step_its_own_wait() -> None:
    # Der Watchdog braucht 0,5 s lückenlose Heartbeats, um wieder scharf zu werden: 0,1 s
    # reichen nicht, die 2 s zum Scharfmachen am Anfang aber schon.
    async with SimulatedCerbo(rearm_s=0.5) as cerbo:
        findings = await run_probe(cerbo, P, replace(FAST, rearm_wait_s=0.1))
    # Ohne Wiederscharfwerden hat die Probe das Ende des Aktionslaufs nicht gesehen: Die Zeile zur
    # Auslösung ist eine Warnung, auch wenn sie 0 fehlgeschlagene Aktionen zeigt.
    assert [f.status for f in findings] == ["ok", "ok", "ok", "warn", "fail", "ok"]
    assert findings[3].detail.endswith(NOT_REARMED)
    assert findings[4].detail == "nach 0 s nicht scharf (Zustand ausgelöst)"


async def test_probe_reports_a_release_that_does_not_arrive() -> None:
    async with StuckCerbo() as cerbo:
        findings = await run_probe(cerbo, P, replace(FAST, release_wait_s=0.2))
    assert [f.status for f in findings] == ["ok", "ok", "ok", "ok", "ok", "fail"]
    assert findings[5].detail == "Zustand scharf statt wartet"


@pytest.mark.parametrize(
    "values",
    [{"State": 0}, {"Version": VERSION}, {"State": None, "Version": VERSION}],
    ids=["nur State", "nur Version", "State ohne Wert"],
)
async def test_probe_needs_state_and_version_for_an_answer(values: dict[str, object]) -> None:
    async with PartialAnswer(**values) as partial:
        findings = await run_probe(partial, P, replace(FAST, answer_wait_s=0.2))
    assert findings == [
        Finding(
            "17.2-5", "Watchdog erreichbar", "fail", "Keine Antwort von com.victronenergy.bindaems."
        )
    ]


async def test_probe_survives_unreadable_values() -> None:
    async with GarbledCerbo() as cerbo:
        findings = await run_probe(cerbo, P, FAST)
    assert [f.status for f in findings] == ["ok", "ok", "ok", "warn", "ok", "ok"]
    assert findings[3].detail.endswith("Auslösungen ? → ?, fehlgeschlagene Aktionen ?")


async def test_probe_writes_only_keepalive_and_a_counting_heartbeat(monkeypatch) -> None:
    monkeypatch.setattr(watchdog_probe, "KEEPALIVE_S", 0.2)
    async with SimulatedCerbo() as cerbo:
        await run_probe(cerbo, P, FAST)
    assert cerbo.subscribed == [f"N/{P}/bindaems/0/#"]
    assert {topic for topic, _ in cerbo.published} == {KEEPALIVE, HEARTBEAT}
    assert cerbo.published[0] == (KEEPALIVE, b"")  # der erste Keepalive veröffentlicht alles neu
    keepalives = [payload for topic, payload in cerbo.published if topic == KEEPALIVE]
    assert len(keepalives) > 2  # und er wiederholt sich im Takt …
    assert set(keepalives[1:]) == {KEEPALIVE_SUPPRESS}  # … ohne erneutes Veröffentlichen
    beats = [
        (json.loads(payload)["value"], at)
        for (topic, payload), at in zip(cerbo.published, cerbo.published_at, strict=True)
        if topic == HEARTBEAT
    ]
    values = [value for value, _ in beats]
    assert values[:-1] == list(range(1, len(values)))  # 1, 2, 3, … auch nach der Pause
    assert values[-1] == 0  # am Ende die Freigabe
    gaps = [later - earlier for (_, earlier), (_, later) in pairwise(beats)]
    assert FAST.heartbeat_s / 2 < statistics.median(gaps) < FAST.heartbeat_s * 4  # im Takt


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (ConnectionError("Verbindung verloren"), "ConnectionError: Verbindung verloren"),
        (None, "ConnectionError: MQTT-Verbindung beendet"),
    ],
)
async def test_probe_reports_a_connection_that_ends_at_once(
    error: Exception | None, detail: str
) -> None:
    async with EndingTransport(error) as ending:
        findings = await run_probe(ending, P, FAST)
    assert [(f.title, f.status) for f in findings] == [
        ("Watchdog erreichbar", "ok"),
        ("Watchdog-Version", "ok"),
        ("Verbindung zum Cerbo", "fail"),
    ]
    assert findings[-1].detail == detail
    assert asyncio.all_tasks() == {asyncio.current_task()}


async def until(predicate: Callable[[], bool], limit_s: float = 5.0) -> None:
    deadline = time.monotonic() + limit_s
    while not predicate():
        assert time.monotonic() < deadline, "Bedingung nicht eingetreten"
        await asyncio.sleep(0.005)


def cancel_when(
    task: asyncio.Task[Any], condition: Callable[[SimulatedCerbo], bool]
) -> Callable[[SimulatedCerbo], None]:
    """Haken für ``SimulatedCerbo.on_change``: bricht ``task`` einmal ab, sobald es zutrifft."""
    fired = False

    def hook(cerbo: SimulatedCerbo) -> None:
        nonlocal fired
        if not fired and condition(cerbo):
            fired = True
            task.cancel()

    return hook


CANCEL_POINTS: dict[str, Callable[[SimulatedCerbo], bool]] = {
    "arming": lambda c: len(c.heartbeats()) == 2,  # der Lauf zum Scharfmachen ist angefangen
    "pause": lambda c: c.state == ARMED and c.trip_count == 0 and c.silent_for() > 0.2,
    "trip": lambda c: c.state == TRIPPED,  # die Aktionen laufen
    # die Probe wartet ohne Heartbeat, bis der Aktionslauf zu Ende sein muss
    "actions": lambda c: c.tripped_at is not None and time.monotonic() - c.tripped_at > 0.1,
    "rearmed": lambda c: c.state == ARMED and c.trip_count == 1,
}


@pytest.mark.parametrize("point", list(CANCEL_POINTS))
async def test_probe_releases_the_watchdog_when_cancelled(point: str, capsys) -> None:
    async with SimulatedCerbo() as cerbo:
        task = asyncio.create_task(run_probe(cerbo, P, FAST))
        cerbo.on_change = cancel_when(task, CANCEL_POINTS[point])
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cerbo.heartbeats()[-1] == 0  # die Freigabe ging noch raus
        assert cerbo.state == WAITING
        assert "Der Watchdog ist mit dem Heartbeat 0 freigegeben." in capsys.readouterr().out
        # Der angefangene Lauf zum Scharfmachen darf den Watchdog nicht nachträglich scharf machen.
        if point == "arming":
            await asyncio.sleep(0.4)
            assert cerbo.state == WAITING
    assert asyncio.all_tasks() == {asyncio.current_task()}


async def test_probe_releases_and_keeps_its_findings_when_the_connection_drops() -> None:
    async with DroppingCerbo(drop_at=ARMED) as cerbo:
        findings = await run_probe(cerbo, P, FAST)
    assert [(f.title, f.status) for f in findings] == [
        ("Watchdog erreichbar", "ok"),
        ("Watchdog-Version", "ok"),
        ("Scharf nach Heartbeat", "ok"),
        ("Verbindung zum Cerbo", "fail"),
    ]
    assert (
        findings[-1].detail == "ConnectionError: Verbindung verloren"
    )  # freigegeben: kein Hinweis
    assert cerbo.heartbeats()[-1] == 0
    assert cerbo.state == WAITING
    assert asyncio.all_tasks() == {asyncio.current_task()}


async def test_probe_keeps_the_trip_row_when_the_connection_drops_after_the_trip() -> None:
    async with DroppingCerbo(drop_at=TRIPPED) as cerbo:
        findings = await run_probe(cerbo, P, FAST)
    assert [(f.title, f.status) for f in findings] == [
        ("Watchdog erreichbar", "ok"),
        ("Watchdog-Version", "ok"),
        ("Scharf nach Heartbeat", "ok"),
        ("Auslösung ohne Heartbeat", "warn"),  # der Aktionslauf war noch nicht zu Ende
        ("Verbindung zum Cerbo", "fail"),
    ]
    assert re.fullmatch(
        r"ausgelöst nach [12] s, Auslösungen 0 → 1, fehlgeschlagene Aktionen 0"
        r" \(Abbruch vor dem Ende des Aktionslaufs\)",
        findings[3].detail,
    )
    assert findings[3].detail.endswith(IN_THE_PAUSE)
    assert cerbo.heartbeats()[-1] == 0
    assert cerbo.state == WAITING


async def test_probe_tells_when_the_release_was_impossible(capsys) -> None:
    async with DeadCerbo(drop_at=ARMED) as cerbo:
        findings = await run_probe(cerbo, P, FAST)
    assert [f.title for f in findings][-1] == "Verbindung zum Cerbo"
    assert findings[-1].detail == (
        f"ConnectionError: Verbindung verloren. {watchdog_probe.UNRELEASED}"
    )
    assert watchdog_probe.UNRELEASED in capsys.readouterr().err
    assert cerbo.heartbeats()[-1] != 0  # die Freigabe kam nicht an
    assert "Heartbeat 0" in watchdog_probe.UNRELEASED  # und der Hinweis sagt, wie es zurückgeht
    # … und wie man danach prüft, dass der Watchdog wartet
    assert "/State 0" in watchdog_probe.UNRELEASED
    assert "dbus -y com.victronenergy.bindaems /State GetValue" in watchdog_probe.UNRELEASED


async def test_probe_gives_up_on_a_release_that_hangs(monkeypatch, capsys) -> None:
    monkeypatch.setattr(watchdog_probe, "RELEASE_ATTEMPT_S", 0.1)
    async with HangingCerbo(drop_at=ARMED) as cerbo:
        findings = await run_probe(cerbo, P, FAST)
    assert findings[-1].detail.endswith(watchdog_probe.UNRELEASED)
    assert watchdog_probe.UNRELEASED in capsys.readouterr().err
    assert asyncio.all_tasks() == {asyncio.current_task()}  # auch der hängende Versuch ist weg


async def test_probe_does_not_release_what_it_never_touched() -> None:
    async with RefusingCerbo() as cerbo:
        findings = await run_probe(cerbo, P, FAST)
    assert [(f.title, f.status, f.detail) for f in findings] == [
        ("Verbindung zum Cerbo", "fail", "ConnectionError: Abo abgelehnt")
    ]
    assert cerbo.published == []  # ohne Heartbeats auch keine Freigabe


async def test_probe_cleans_up_when_cancelled_again_during_the_release(monkeypatch) -> None:
    monkeypatch.setattr(watchdog_probe, "RELEASE_ATTEMPT_S", 30.0)  # hängt, bis Strg+C erneut kommt
    async with HangingCerbo(drop_at=ARMED) as cerbo:
        task = asyncio.create_task(run_probe(cerbo, P, FAST))
        await until(lambda: cerbo.hanging)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5.0)  # kein zweiter Versuch, der wieder 30 s hinge
    assert asyncio.all_tasks() == {asyncio.current_task()}


async def test_probe_cleans_up_when_cancelled_twice(monkeypatch) -> None:
    monkeypatch.setattr(watchdog_probe, "RELEASE_ATTEMPT_S", 30.0)  # hängt, bis Strg+C erneut kommt
    async with HangingRelease() as cerbo:
        task = asyncio.create_task(run_probe(cerbo, P, FAST))
        cerbo.on_change = cancel_when(task, CANCEL_POINTS["arming"])  # das erste Strg+C
        await until(lambda: cerbo.hanging)  # die Freigabe hängt …
        task.cancel()  # … und das zweite Strg+C kommt
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5.0)
    assert asyncio.all_tasks() == {asyncio.current_task()}  # die Hilfstasks sind trotzdem weg


async def test_probe_names_an_internal_error_and_still_releases() -> None:
    async with BuggyCerbo() as cerbo:
        findings = await run_probe(cerbo, P, FAST)
    assert [(f.title, f.status) for f in findings] == [
        ("Watchdog erreichbar", "ok"),
        ("Watchdog-Version", "ok"),
        ("Scharf nach Heartbeat", "ok"),
        # Der Fehler kommt beim ersten Heartbeat nach der ganzen Pause, vor dem Wiederscharfwerden:
        # Die Zeile bleibt, aber die Zahl der fehlgeschlagenen Aktionen ist noch nicht der Endstand.
        ("Auslösung ohne Heartbeat", "warn"),
        ("Interner Fehler der Probe", "fail"),
    ]
    assert findings[3].detail.endswith(NOT_REARMED)  # nicht „Abbruch vor dem Ende des Aktionslaufs“
    assert findings[-1].detail == "RuntimeError: kaputt"
    assert cerbo.heartbeats()[-1] == 0
    assert cerbo.state == WAITING


def test_timings_from_config(cfg: Config) -> None:
    assert timings_from_config(cfg) == ProbeTimings(
        heartbeat_s=10,
        answer_wait_s=10,
        arm_wait_s=45,
        timeout_s=90,
        trip_slack_s=30,
        actions_wait_s=45,
        rearm_wait_s=45,
        release_wait_s=15,
    )
    # Die Pause nach der Auslösung folgt den Wiederholungen, die watchdog_config schreibt: Der
    # letzte Versuch kommt nach RETRIES × RETRY_S = 30 s.
    assert timings_from_config(cfg).actions_wait_s == (RETRIES + 1) * RETRY_S + 10
    watchdog = cfg.victron.watchdog.model_copy(update={"heartbeat_s": 4, "timeout_s": 60})
    changed = cfg.model_copy(
        update={"victron": cfg.victron.model_copy(update={"watchdog": watchdog})}
    )
    timings = timings_from_config(changed)
    assert (timings.heartbeat_s, timings.timeout_s) == (4, 60)
    assert watchdog_probe.KEEPALIVE_S == 30  # alle 30 s


def _today() -> str:
    return datetime.now(LOCAL_TZ).date().isoformat()


def _config_file(tmp_path: Path, portal_id: str | None) -> Path:
    raw = yaml.safe_load(Path("deploy/config.example.yaml").read_text(encoding="utf-8"))
    raw["victron"]["mqtt"]["portal_id"] = portal_id
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return config


def _connect_to(monkeypatch, transport: object, timings: ProbeTimings = FAST) -> Mock:
    """Lenkt ``main`` auf ``transport`` und kurze Zeiten um; liefert den Ersatz der Verbindung."""
    factory = Mock(return_value=transport)
    monkeypatch.setattr("bindaems.tools.watchdog_probe.AiomqttTransport", factory)
    monkeypatch.setattr("bindaems.tools.watchdog_probe.timings_from_config", lambda _cfg: timings)
    return factory


@pytest.fixture
def secrets_env(monkeypatch) -> None:
    for name in ("TESSIE_TOKEN", "HA_TOKEN", "INFLUX_PASSWORD", "HA_MQTT_PASSWORD"):
        monkeypatch.delenv(f"BINDAEMS_{name}", raising=False)
    monkeypatch.setenv("BINDAEMS_INTERNAL_TOKEN", "x" * 32)
    monkeypatch.setenv("BINDAEMS_MQTT_PASSWORD", "mqtt-geheim")


@pytest.mark.parametrize("answer", ["", "n", "nein", "ja"])
def test_main_aborts_on_every_answer_but_j(monkeypatch, capsys, answer: str) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    transport = Mock(side_effect=AssertionError("keine Verbindung"))
    secrets = Mock(side_effect=AssertionError("keine Secrets"))
    monkeypatch.setattr("bindaems.tools.watchdog_probe.AiomqttTransport", transport)
    monkeypatch.setattr("bindaems.tools.watchdog_probe.load_secrets", secrets)
    assert main(["--config", "deploy/config.example.yaml"]) == 1
    transport.assert_not_called()
    secrets.assert_not_called()
    assert (
        "Die Probe löst den Watchdog aus. Er setzt die EVCS auf den sicheren Strom und schreibt "
        "Min-SOC und Entladegrenze, wenn sie abweichen. Mit victron.live_allowed hebt er auch "
        "den Netz-Sollwert-Override auf."
    ) in capsys.readouterr().out


def test_main_aborts_when_there_is_no_input(monkeypatch) -> None:
    def eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    transport = Mock(side_effect=AssertionError("keine Verbindung"))
    monkeypatch.setattr("bindaems.tools.watchdog_probe.AiomqttTransport", transport)
    assert main(["--config", "deploy/config.example.yaml"]) == 1
    transport.assert_not_called()


def test_main_rejects_invalid_config_before_asking(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("builtins.input", Mock(side_effect=AssertionError("keine Rückfrage")))
    broken = tmp_path / "config.yaml"
    broken.write_text("grid: 1\n")
    assert main(["--config", str(broken)]) == 2
    assert "Konfiguration ungültig" in capsys.readouterr().err


def test_main_rejects_invalid_secrets_without_connecting(monkeypatch, capsys) -> None:
    monkeypatch.delenv("BINDAEMS_INTERNAL_TOKEN", raising=False)
    transport = Mock(side_effect=AssertionError("keine Verbindung"))
    monkeypatch.setattr("bindaems.tools.watchdog_probe.AiomqttTransport", transport)
    assert main(["--config", "deploy/config.example.yaml", "--yes"]) == 2
    assert capsys.readouterr().err.startswith("Secrets ungültig")
    transport.assert_not_called()


@pytest.mark.usefixtures("secrets_env")
def test_main_runs_the_probe_and_writes_the_report(
    cfg: Config, monkeypatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "j")
    cerbo = SimulatedCerbo()
    transport = _connect_to(monkeypatch, cerbo)
    out = tmp_path / "verification" / "bericht.md"
    before = _today()
    assert main(["--config", "deploy/config.example.yaml", "--out", str(out)]) == 0
    after = _today()
    mqtt_config, password = transport.call_args.args
    assert mqtt_config == cfg.victron.mqtt
    assert password.get_secret_value() == "mqtt-geheim"
    assert cerbo.subscribed == ["N/+/system/0/Serial", f"N/{P}/bindaems/0/#"]  # Portal-ID erkannt
    report = out.read_text(encoding="utf-8")
    heading = re.match(r"# Prüfprotokoll Teil 2 – Watchdog-Probe – (\d{4}-\d\d-\d\d)\n", report)
    assert heading and heading[1] in {before, after}
    assert "| Werkzeug | watchdog_probe |" in report
    for title in TITLES:
        assert f"| 17.2-5 | {title} | OK |" in report
    assert P not in report and "mqtt-geheim" not in report  # keine Anlagendaten im Bericht
    shown = capsys.readouterr().out
    assert "Die Probe löst den Watchdog aus." in shown
    assert f"Bericht: {out}" in shown


@pytest.mark.usefixtures("secrets_env")
def test_main_exits_0_when_a_step_only_warns(monkeypatch, tmp_path: Path) -> None:
    _connect_to(monkeypatch, GarbledCerbo())  # auch die Portal-Erkennung trifft auf Fremdes
    out = tmp_path / "bericht.md"
    assert main(["--config", "deploy/config.example.yaml", "--yes", "--out", str(out)]) == 0
    assert "| 17.2-5 | Auslösung ohne Heartbeat | WARNUNG |" in out.read_text(encoding="utf-8")


@pytest.mark.usefixtures("secrets_env")
def test_main_exits_1_and_still_writes_the_report_when_a_step_fails(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("builtins.input", Mock(side_effect=AssertionError("--yes fragt nicht")))
    _connect_to(monkeypatch, SilentTransport(), replace(FAST, answer_wait_s=0.2))
    config = _config_file(tmp_path, portal_id=P)
    monkeypatch.chdir(tmp_path)
    before = _today()
    assert main(["--config", str(config), "--yes"]) == 1
    after = _today()
    (report,) = tmp_path.glob("docs/verification/*-watchdog-probe.md")  # der Standardpfad
    assert report.name in {f"{before}-watchdog-probe.md", f"{after}-watchdog-probe.md"}
    assert (
        "| 17.2-5 | Watchdog erreichbar | FEHLER | Keine Antwort von com.victronenergy.bindaems. |"
        in report.read_text(encoding="utf-8")
    )


@pytest.mark.usefixtures("secrets_env")
@pytest.mark.parametrize(
    ("error", "title", "detail"),
    [
        (
            ConnectionRefusedError("Verbindung abgelehnt"),
            "Verbindung zum Cerbo",
            "ConnectionRefusedError: Verbindung abgelehnt",
        ),
        (MqttError("Zeitüberschreitung"), "Verbindung zum Cerbo", "MqttError: Zeitüberschreitung"),
        (RuntimeError("kaputt"), "Interner Fehler der Probe", "RuntimeError: kaputt"),
    ],
)
def test_main_names_the_kind_of_error(
    monkeypatch, tmp_path: Path, error: Exception, title: str, detail: str
) -> None:
    transport = Mock(side_effect=error)
    monkeypatch.setattr("bindaems.tools.watchdog_probe.AiomqttTransport", transport)
    out = tmp_path / "bericht.md"
    assert main(["--config", "deploy/config.example.yaml", "--yes", "--out", str(out)]) == 1
    assert f"| 17.2-5 | {title} | FEHLER | {detail} |" in out.read_text(encoding="utf-8")


@pytest.mark.usefixtures("secrets_env")
def test_main_reports_a_missing_portal_id(monkeypatch, tmp_path: Path) -> None:
    _connect_to(monkeypatch, SilentTransport())
    monkeypatch.setattr(watchdog_probe, "DISCOVERY_TIMEOUT_S", 0.1)
    out = tmp_path / "bericht.md"
    assert main(["--config", "deploy/config.example.yaml", "--yes", "--out", str(out)]) == 1
    detail = "ConnectionError: Portal-ID konnte nicht ermittelt werden"
    assert f"| 17.2-5 | Verbindung zum Cerbo | FEHLER | {detail} |" in out.read_text("utf-8")


@pytest.mark.usefixtures("secrets_env")
def test_main_prints_the_report_when_it_cannot_be_written(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    _connect_to(monkeypatch, SilentTransport(), replace(FAST, answer_wait_s=0.2))
    blocker = tmp_path / "datei"
    blocker.write_text("")  # ein Verzeichnis lässt sich unter einer Datei nicht anlegen
    config = _config_file(tmp_path, portal_id=P)
    code = main(["--config", str(config), "--yes", "--out", str(blocker / "bericht.md")])
    captured = capsys.readouterr()
    assert code == 1
    assert "Bericht nicht geschrieben" in captured.err
    assert "# Prüfprotokoll Teil 2 – Watchdog-Probe" in captured.out


@pytest.mark.skipif(
    signal.getsignal(signal.SIGINT) is not signal.default_int_handler,
    reason="SIGINT ist hier anders belegt; asyncio.run fängt Strg+C dann nicht",
)
@pytest.mark.usefixtures("secrets_env")
def test_main_releases_the_watchdog_and_exits_130_on_ctrl_c(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    cerbo = InterruptedCerbo()
    _connect_to(monkeypatch, cerbo)
    out = tmp_path / "bericht.md"
    try:
        code = main(["--config", "deploy/config.example.yaml", "--yes", "--out", str(out)])
    except KeyboardInterrupt:
        pytest.fail("Strg+C darf nicht als KeyboardInterrupt aus main kommen")
    assert code == 130
    assert not out.exists()  # ein abgebrochener Lauf ergibt keinen halben Bericht
    shown = capsys.readouterr()
    assert "Der Watchdog ist mit dem Heartbeat 0 freigegeben." in shown.out
    assert "Abgebrochen." in shown.out
    assert "Traceback" not in shown.err
    assert cerbo.heartbeats()[-1] == 0  # der Watchdog ist freigegeben
    assert cerbo.state == WAITING


def test_main_exits_130_on_ctrl_c_at_the_prompt(monkeypatch, capsys) -> None:
    def interrupt(_prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    transport = Mock(side_effect=AssertionError("keine Verbindung"))
    secrets = Mock(side_effect=AssertionError("keine Secrets"))
    monkeypatch.setattr("bindaems.tools.watchdog_probe.AiomqttTransport", transport)
    monkeypatch.setattr("bindaems.tools.watchdog_probe.load_secrets", secrets)
    try:
        code = main(["--config", "deploy/config.example.yaml"])
    except KeyboardInterrupt:
        pytest.fail("Strg+C darf nicht als KeyboardInterrupt aus main kommen")
    assert code == 130
    transport.assert_not_called()
    secrets.assert_not_called()
    assert "Abgebrochen." in capsys.readouterr().out


@pytest.mark.usefixtures("secrets_env")
def test_main_tells_when_the_watchdog_could_not_be_released(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    _connect_to(monkeypatch, DeadCerbo(drop_at=ARMED))
    out = tmp_path / "bericht.md"
    assert main(["--config", "deploy/config.example.yaml", "--yes", "--out", str(out)]) == 1
    report = out.read_text(encoding="utf-8")
    assert "| 17.2-5 | Scharf nach Heartbeat | OK |" in report  # das Bisherige bleibt im Bericht
    assert watchdog_probe.UNRELEASED in report
    assert watchdog_probe.UNRELEASED in capsys.readouterr().err


@pytest.mark.usefixtures("secrets_env")
def test_main_keeps_the_findings_when_disconnecting_fails(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    class ExitFails(SilentTransport):
        async def __aexit__(self, *exc: object) -> None:
            raise MqttError("Trennen fehlgeschlagen")

    _connect_to(monkeypatch, ExitFails(), replace(FAST, answer_wait_s=0.2))
    config = _config_file(tmp_path, portal_id=P)
    out = tmp_path / "bericht.md"
    assert main(["--config", str(config), "--yes", "--out", str(out)]) == 1
    assert out.read_text(encoding="utf-8").count("| 17.2-5 |") == 1  # nur der Befund der Probe
    assert "MqttError: Trennen fehlgeschlagen" in capsys.readouterr().err
