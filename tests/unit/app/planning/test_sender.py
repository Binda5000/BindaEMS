"""Tests der Übergabe des Plans an den core (Plan 3c, Task 10, Präzisierung 11)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from datetime import timedelta

import pytest
from structlog.testing import capture_logs
from tests.plan_helpers import EV, sample_plan, set_planning, until
from tests.unit.app.control.conftest import FakeCoreClient, FakeFeed
from tests.unit.core.control.charge_helpers import NONE, evcs, tesla, twc

from bindaems.app.audit import AuditLog
from bindaems.app.control.intent import IntentService
from bindaems.app.core_link import CoreReply, CoreUnavailableError, LiveState
from bindaems.app.planning.sender import ACTOR, SOURCE, PlanSender
from bindaems.app.settings.service import SettingsService
from bindaems.core.control.assignment import Assignment, AssignmentState
from bindaems.shared.config import Config
from bindaems.shared.control import Intent, VehicleIntent
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind, Plan, intent_plan
from bindaems.shared.timeutil import ManualClock

NETZLADEN = {"intent": BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=3000.0)}


class FakePlanning:
    """Stand-in für ``PlanningService``: ``current`` und ``on_plan``."""

    def __init__(self, plan: Plan | None) -> None:
        self.plan = plan
        self.listeners: list[Callable[[Plan], None]] = []

    def current(self) -> Plan | None:
        return self.plan

    def on_plan(self, listener: Callable[[Plan], None]) -> None:
        self.listeners.append(listener)

    def publish(self, plan: Plan) -> None:
        self.plan = plan
        for listener in self.listeners:
            listener(plan)


def stored_plan(plan_id: int = 7, intents: list[dict[str, object]] | None = None) -> Plan:
    """Gespeicherter Plan von 12:01 Ortszeit, gültig bis 10:46 UTC; vier Slots."""
    plan = sample_plan(
        "2026-10-16T10:01:00+00:00", ["10:00", "10:15", "10:30", "10:45"], intents=intents
    )
    return plan.model_copy(update={"id": plan_id})


def live_reports(live: LiveState, intent: Intent) -> None:
    """Health des core zeigt ``intent`` als gültige Absicht."""
    live.health = {"intent": intent.model_dump(mode="json"), "intent_version": intent.version}


def grid_charge(settings: SettingsService, on: bool) -> None:
    planning = settings.current().settings.planning
    set_planning(settings, planning.model_copy(update={"grid_charge_allowed": on}))


# Die gültige Absicht des core ohne Plan; er regelt mit ihr
CORE_INTENT = Intent(version=1, control=True)


def trusted_health() -> dict[str, object]:
    """Health des core, der mit ``CORE_INTENT`` regelt: bekannt, der core hat keinen Plan."""
    return {
        "mode": "CONTROL",
        "intent": CORE_INTENT.model_dump(mode="json"),
        "intent_version": CORE_INTENT.version,
    }


@pytest.fixture
def live(live: LiveState) -> LiveState:
    """Verbunden (Fixture der Planung) und mit Health, dem die app traut."""
    live.health = trusted_health()
    return live


@pytest.fixture
def fake_client() -> FakeCoreClient:
    client = FakeCoreClient()
    client.valid = CORE_INTENT
    return client


@pytest.fixture
def intents(
    fake_client: FakeCoreClient, live: LiveState, audit: AuditLog, clock: ManualClock, cfg: Config
) -> IntentService:
    return IntentService(fake_client, live, FakeFeed(), audit, clock, cfg)  # type: ignore[arg-type]


@pytest.fixture
def planning() -> FakePlanning:
    return FakePlanning(stored_plan())


@pytest.fixture
def sender(
    intents: IntentService,
    planning: FakePlanning,
    settings_service: SettingsService,
    live: LiveState,
    clock: ManualClock,
) -> PlanSender:
    return PlanSender(intents, planning, settings_service, live, clock)  # type: ignore[arg-type]


async def test_new_plan_is_sent_once(
    sender: PlanSender, fake_client: FakeCoreClient, planning: FakePlanning, live: LiveState
) -> None:
    assert await sender.sync() == "sent"
    assert fake_client.sent[-1].plan == intent_plan(planning.current(), grid_charge_allowed=False)
    live_reports(live, fake_client.sent[-1])  # Health zeigt den Plan
    assert await sender.sync() == "current"
    assert len(fake_client.sent) == 1


async def test_a_newer_plan_is_sent(
    sender: PlanSender, fake_client: FakeCoreClient, planning: FakePlanning, live: LiveState
) -> None:
    assert await sender.sync() == "sent"
    live_reports(live, fake_client.sent[-1])
    planning.plan = stored_plan(plan_id=8)
    assert await sender.sync() == "sent"
    assert fake_client.sent[-1].plan is not None
    assert fake_client.sent[-1].plan.id == 8


async def test_expired_plan_is_not_sent(
    sender: PlanSender, clock: ManualClock, fake_client: FakeCoreClient
) -> None:
    clock.advance(timedelta(minutes=50))
    assert await sender.sync() == "none"
    assert fake_client.sent == []


async def test_without_plan_nothing_is_sent(
    sender: PlanSender, planning: FakePlanning, fake_client: FakeCoreClient
) -> None:
    planning.plan = None
    assert await sender.sync() == "none"
    assert fake_client.sent == []


async def test_without_connection_the_sync_fails(
    sender: PlanSender, live: LiveState, fake_client: FakeCoreClient
) -> None:
    live.connected = False
    assert await sender.sync() == "failed"
    assert fake_client.sent == []


async def test_without_health_nothing_is_sent(
    sender: PlanSender, live: LiveState, fake_client: FakeCoreClient
) -> None:
    # Ohne Health ist unbekannt, ob der core den Plan hat; eine Version auf diesem Stand behielte
    # Boost und Übersteuerung (Review Focus 1)
    live.health = None
    with capture_logs() as logs:
        assert await sender.sync() == "failed"
        assert await sender.sync() == "failed"
    assert fake_client.sent == []
    assert [entry["event"] for entry in logs] == ["Plan wartet auf Health des core"]  # einmal
    live.health = trusted_health()
    assert await sender.sync() == "sent"


@pytest.mark.parametrize(
    "regulated",
    [
        pytest.param(None, id="core regelt nicht"),
        pytest.param(CORE_INTENT.version - 1, id="mit einer älteren Absicht"),
        pytest.param("1", id="Version keine Zahl"),
    ],
)
async def test_untrusted_health_sends_nothing(
    sender: PlanSender, live: LiveState, fake_client: FakeCoreClient, regulated: object
) -> None:
    # Gesamtreview I1: Auf einer Basis ohne Stand der Regelung bliebe jeder Boost, und die neue
    # Version bände ihn im core neu
    live.health = {**trusted_health(), "intent_version": regulated}
    with capture_logs() as logs:
        assert await sender.sync() == "failed"
        assert await sender.sync() == "failed"
    assert fake_client.sent == []
    assert [entry["event"] for entry in logs] == [
        "Plan wartet, bis der core mit der Absicht regelt"
    ]
    live.health = trusted_health()
    assert await sender.sync() == "sent"


async def test_no_wait_line_when_core_already_has_the_plan(
    sender: PlanSender, live: LiveState, fake_client: FakeCoreClient, planning: FakePlanning
) -> None:
    # K12: Hat der core den Plan schon, ist nichts zu übergeben; die Zeile „wartet“ wäre falsch
    wanted = sender.wanted()
    assert wanted is not None
    live.health = {
        **trusted_health(),
        "intent": {**CORE_INTENT.model_dump(mode="json"), "plan": wanted.model_dump(mode="json")},
        "intent_version": CORE_INTENT.version - 1,  # regelt noch nicht mit der Absicht
    }
    with capture_logs() as logs:
        assert await sender.sync() == "current"
    assert fake_client.sent == []
    assert not [entry for entry in logs if "wartet" in entry["event"]]


async def test_health_without_a_core_intent_sends_nothing(
    sender: PlanSender, live: LiveState, fake_client: FakeCoreClient
) -> None:
    # Ohne Absicht regelt der core nicht; der Plan wirkte dort ohnehin nicht
    live.health = {"mode": "CONTROL", "intent": None, "intent_version": None}
    assert await sender.sync() == "failed"
    assert fake_client.sent == []


async def test_core_down_is_retried_on_the_next_sync(
    sender: PlanSender, fake_client: FakeCoreClient
) -> None:
    fake_client.error = CoreUnavailableError("weg")
    assert await sender.sync() == "failed"
    fake_client.error = None
    assert await sender.sync() == "sent"


async def test_rejection_is_retried_on_the_next_sync(
    sender: PlanSender, fake_client: FakeCoreClient
) -> None:
    body = {"result": "rejected", "details": ["Plan: Netzladen, obwohl ausgeschaltet."]}
    fake_client.replies.append(CoreReply(422, body))
    assert await sender.sync() == "failed"
    assert await sender.sync() == "sent"


async def test_switching_grid_charge_off_sends_the_plan_without_grid_charge(
    sender: PlanSender,
    fake_client: FakeCoreClient,
    planning: FakePlanning,
    settings_service: SettingsService,
    live: LiveState,
) -> None:
    # Review Focus 5: der nächste Anstoß schickt denselben Plan ohne NETZLADEN
    planning.plan = stored_plan(intents=[NETZLADEN, NETZLADEN, EV, EV])
    grid_charge(settings_service, True)
    assert await sender.sync() == "sent"
    first = fake_client.sent[-1].plan
    assert first is not None and first.grid_charge_allowed is True
    assert first.slots[0].intent == NETZLADEN["intent"]
    live_reports(live, fake_client.sent[-1])
    assert await sender.sync() == "current"

    grid_charge(settings_service, False)
    assert await sender.sync() == "sent"
    second = fake_client.sent[-1].plan
    assert second is not None
    assert (second.id, second.grid_charge_allowed) == (first.id, False)
    assert [slot.intent.kind for slot in second.slots] == [BatteryIntentKind.EIGENVERBRAUCH] * 4


async def test_plan_send_retries_once_on_conflict(
    sender: PlanSender, fake_client: FakeCoreClient, planning: FakePlanning, clock: ManualClock
) -> None:
    # Review Focus 1: zwischen Holen und Senden schaltet jemand den Regelbetrieb ein
    def operator() -> CoreReply:
        fake_client.valid = Intent(version=epoch_ms(clock.now()) + 500, control=True)
        return CoreReply(409, {"result": "rejected", "version": fake_client.valid.version})

    fake_client.replies.append(operator)
    assert await sender.sync() == "sent"
    assert len(fake_client.sent) == 2
    second = fake_client.sent[1]
    assert second.control is True  # die Bedienung bleibt
    assert second.plan == intent_plan(planning.current(), grid_charge_allowed=False)


async def test_second_conflict_fails_the_sync(
    sender: PlanSender, fake_client: FakeCoreClient
) -> None:
    fake_client.replies += [CoreReply(409, {}), CoreReply(409, {})]
    assert await sender.sync() == "failed"
    assert len(fake_client.sent) == 2


async def test_plan_is_sent_by_the_planning_system(sender: PlanSender, audit: AuditLog) -> None:
    assert (ACTOR, SOURCE) == ("planung", "system")
    assert await sender.sync() == "sent"
    assert audit.recent() == []  # ohne Eintrag im Änderungsprotokoll


@contextlib.asynccontextmanager
async def running(sender: PlanSender) -> AsyncIterator[None]:
    task = asyncio.create_task(sender.run())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_reconnect_wakes_the_sender(
    intents: IntentService,
    planning: FakePlanning,
    settings_service: SettingsService,
    live: LiveState,
    clock: ManualClock,
    fake_client: FakeCoreClient,
) -> None:
    sender = PlanSender(intents, planning, settings_service, live, clock, interval_s=3600.0)  # type: ignore[arg-type]
    live.connected = False
    async with running(sender):
        await asyncio.sleep(0.05)  # erster Abgleich ohne Verbindung: nichts gesendet
        assert fake_client.sent == []
        live.connected = True
        await sender.connected()
        await until(lambda: len(fake_client.sent) == 1)


async def test_after_reconnect_the_plan_waits_for_health(
    intents: IntentService,
    planning: FakePlanning,
    settings_service: SettingsService,
    live: LiveState,
    clock: ManualClock,
    fake_client: FakeCoreClient,
) -> None:
    # Nach einer Störung fehlt Health bis zur nächsten Abfrage: erst danach geht der Plan hinaus
    sender = PlanSender(intents, planning, settings_service, live, clock, interval_s=0.02)  # type: ignore[arg-type]
    live.health = None
    async with running(sender):
        await sender.connected()
        await asyncio.sleep(0.1)  # mehrere Abgleiche im Takt
        assert fake_client.sent == []
        live.health = trusted_health()
        await until(lambda: len(fake_client.sent) == 1)


async def test_new_plan_and_settings_wake_the_sender(
    intents: IntentService,
    planning: FakePlanning,
    settings_service: SettingsService,
    live: LiveState,
    clock: ManualClock,
    fake_client: FakeCoreClient,
) -> None:
    sender = PlanSender(intents, planning, settings_service, live, clock, interval_s=3600.0)  # type: ignore[arg-type]
    async with running(sender):
        await until(lambda: len(fake_client.sent) == 1)  # beim Start
        live_reports(live, fake_client.sent[-1])
        planning.publish(stored_plan(plan_id=8))
        await until(lambda: len(fake_client.sent) == 2)
        live_reports(live, fake_client.sent[-1])
        # aus einem Worker-Thread, wie die Einstellungen aus den Routen
        await asyncio.to_thread(grid_charge, settings_service, True)
        await until(lambda: len(fake_client.sent) == 3)
    sent = fake_client.sent[-1].plan
    assert sent is not None and (sent.id, sent.grid_charge_allowed) == (8, True)


async def test_the_interval_retries_without_a_wake(
    intents: IntentService,
    planning: FakePlanning,
    settings_service: SettingsService,
    live: LiveState,
    clock: ManualClock,
    fake_client: FakeCoreClient,
) -> None:
    sender = PlanSender(intents, planning, settings_service, live, clock, interval_s=0.01)  # type: ignore[arg-type]
    fake_client.error = CoreUnavailableError("weg")
    async with running(sender):
        await asyncio.sleep(0.05)
        fake_client.error = None
        await until(lambda: len(fake_client.sent) >= 1)


async def test_a_health_refresh_wakes_the_sender(
    intents: IntentService,
    planning: FakePlanning,
    settings_service: SettingsService,
    live: LiveState,
    clock: ManualClock,
    fake_client: FakeCoreClient,
) -> None:
    # Gesamtreview I1: Das erste Health, dem die app traut, liefert den Plan ohne den Takt
    sender = PlanSender(intents, planning, settings_service, live, clock, interval_s=3600.0)  # type: ignore[arg-type]
    live.health = {**trusted_health(), "mode": "OBSERVE", "intent_version": None}
    async with running(sender):
        await asyncio.sleep(0.05)  # erster Abgleich: der core regelt nicht
        assert fake_client.sent == []
        live.health = trusted_health()
        sender.wake()  # wie ``LiveFeed`` nach jeder Abfrage von Health
        await until(lambda: len(fake_client.sent) == 1)
        live_reports(live, fake_client.sent[-1])
        sender.wake()  # der core hat den Plan: nichts mehr zu senden
        await asyncio.sleep(0.05)
    assert len(fake_client.sent) == 1


class RestartedCore:
    """Die Zuordnung eines core, der um ``started`` neu startete, mit seiner Absicht.

    Gerechnet wird auf der Uhr ``clock`` der app; der e-Golf steht an der EVCS (Standard).
    """

    def __init__(self, cfg: Config, clock: ManualClock, client: FakeCoreClient) -> None:
        self.clock, self.client = clock, client
        self.assignment = Assignment(cfg, started=clock.now())
        self.accepted_at = clock.now() - timedelta(hours=1)  # vor dem Neustart angenommen
        self.version = client.valid.version if client.valid is not None else None

    def cycle(self, *, plugged: bool) -> AssignmentState:
        """Ein Zyklus eine Sekunde später, mit der jetzt gültigen Absicht des core."""
        self.clock.advance(timedelta(seconds=1))
        now, intent = self.clock.now(), self.client.valid
        if intent is not None and intent.version != self.version:  # neu angenommen
            self.version, self.accepted_at = intent.version, now
        wallboxes = {"evcs": evcs(connected=plugged), "twc": twc()}
        return self.assignment.update(
            now, now, intent, self.accepted_at, wallboxes, {"tesla": tesla(), "egolf": NONE}
        )

    def health(self, state: AssignmentState | None) -> dict[str, object]:
        """Health: ohne ``state`` ruht die Regelung (OBSERVE), sonst regelt sie mit der Absicht."""
        intent = self.client.valid
        assert intent is not None
        if state is None:
            return {
                "mode": "OBSERVE",
                "intent": intent.model_dump(mode="json"),
                "intent_version": None,
            }
        egolf = state.vehicles["egolf"]
        vehicle = {
            "id": "egolf",
            "mode": intent.vehicles["egolf"].mode.value,
            "boost_until": None if egolf.boost_until is None else egolf.boost_until.isoformat(),
            "boost_pending": egolf.boost_pending,
            "wallbox": egolf.wallbox,
            "assignment": egolf.kind.value,
            "override_pending": egolf.override_pending,
        }
        return {
            "mode": "CONTROL",
            "intent": intent.model_dump(mode="json"),
            "intent_version": intent.version,
            "vehicles": [vehicle],
        }


async def test_a_restored_boost_is_not_revived_by_plan_sends_after_a_restart(
    sender: PlanSender,
    live: LiveState,
    fake_client: FakeCoreClient,
    clock: ManualClock,
    cfg: Config,
) -> None:
    # Gesamtreview I1, durchgehend: Sender → Absicht → Zuordnung des core. Der e-Golf hatte vor
    # dem Neustart einen Boost, der auf sein Anstecken wartete. Wiederhergestellt bindet er nur an
    # einen laufenden Vorgang; ein Plan danach darf ihn nicht für das nächste Anstecken scharf
    # machen.
    boost = VehicleIntent(boost_until=clock.now() + timedelta(hours=2))
    fake_client.valid = Intent(version=5, control=True, vehicles={"egolf": boost})
    core = RestartedCore(cfg, clock, fake_client)
    core.cycle(plugged=False)
    live.health = core.health(None)  # START/OBSERVE nach dem Neustart
    results = [await sender.sync()]
    state = core.cycle(plugged=False)  # der core regelt mit der Absicht, die er jetzt hat
    live.health = core.health(state)
    results.append(await sender.sync())
    core.cycle(plugged=False)
    assert core.cycle(plugged=True).vehicles["egolf"].boost_until is None  # e-Golf steckt an
    assert results == ["failed", "sent"]
    assert fake_client.valid is not None
    assert fake_client.valid.vehicles["egolf"].boost_until is None  # nicht zurückgeschickt


async def test_sender_sends_vehicle_power(
    sender: PlanSender, fake_client: FakeCoreClient, planning: FakePlanning
) -> None:
    slots = [
        slot.model_copy(update={"ev_w": {"tesla": 6899.6} if index == 0 else {}})
        for index, slot in enumerate(planning.current().slots)
    ]
    planning.plan = planning.current().model_copy(update={"slots": slots})
    assert await sender.sync() == "sent"
    sent = fake_client.sent[-1].plan
    assert sent is not None
    assert [slot.vehicles for slot in sent.slots] == [{"tesla": 6900}, {}, {}, {}]
