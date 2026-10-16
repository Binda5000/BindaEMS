import pytest
from tests.unit.app.control.conftest import FakeCoreClient, FakeFeed

from bindaems.app.audit import AuditLog
from bindaems.app.control.intent import (
    ChangeResult,
    ConflictAgain,
    CoreDown,
    IntentService,
    Rejected,
    SetAssignment,
    SetBoost,
    SetControl,
    SetGuestMode,
    SetMode,
    UnknownTarget,
)
from bindaems.app.core_link import CoreUnavailableError, LiveState
from bindaems.app.ha.commands import HaCommandHandler, LogRejections
from bindaems.shared.control import ChargeMode, Intent
from bindaems.shared.timeutil import ManualClock

PREFIX = "Befehl aus Home Assistant abgelehnt: "


class FakeIntents:
    """Zeichnet ``apply`` auf; ``error`` wird beim nächsten Aufruf geworfen."""

    def __init__(self) -> None:
        self.applied: list[tuple[object, str, str]] = []
        self.error: Exception | None = None

    async def apply(self, change, *, actor, source) -> ChangeResult:
        if self.error is not None:
            raise self.error
        self.applied.append((change, actor, source))
        return ChangeResult("accepted", 1, [], Intent(version=1))


class FakeRejections:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.error: Exception | None = None

    async def reject(self, text: str) -> None:
        if self.error is not None:
            raise self.error
        self.texts.append(text)


@pytest.fixture
def intents() -> FakeIntents:
    return FakeIntents()


@pytest.fixture
def rejections() -> FakeRejections:
    return FakeRejections()


@pytest.fixture
def handler(cfg, intents, rejections, clock) -> HaCommandHandler:
    return HaCommandHandler(cfg, intents, rejections, clock)  # type: ignore[arg-type]


async def test_select_sets_the_mode(handler, intents) -> None:
    await handler("tesla_mode", "Sofort")
    assert intents.applied == [(SetMode("tesla", ChargeMode.NOW), "home-assistant", "ha")]


@pytest.mark.parametrize(
    ("label", "mode"),
    [
        ("PV-Überschuss", ChargeMode.PV),
        ("Min+PV", ChargeMode.MIN_PV),
        ("Sofort", ChargeMode.NOW),
        ("Aus", ChargeMode.OFF),
    ],
)
async def test_every_option_maps_back_to_its_mode(handler, intents, label, mode) -> None:
    await handler("egolf_mode", label)
    await handler("guest_mode", label)
    assert [change for change, _, _ in intents.applied] == [
        SetMode("egolf", mode),
        SetGuestMode(mode),
    ]


async def test_switch_sets_the_regulation(handler, intents, clock) -> None:
    await handler("regulation", "ON")
    clock.advance(1.0)
    await handler("regulation", "OFF")
    assert [change for change, _, _ in intents.applied] == [SetControl(True), SetControl(False)]


async def test_buttons_start_and_stop_the_boost(handler, intents, clock) -> None:
    await handler("tesla_boost", "PRESS")
    clock.advance(1.0)
    await handler("tesla_boost_stop", "PRESS")
    assert intents.applied == [
        (SetBoost("tesla", True), "home-assistant", "ha"),
        (SetBoost("tesla", False), "home-assistant", "ha"),
    ]


async def test_assignment_select_maps_names_back_to_vehicle_keys(handler, intents) -> None:
    await handler("evcs_assignment", "Tesla Model 3")
    await handler("twc_assignment", "Automatisch")
    assert [change for change, _, _ in intents.applied] == [
        SetAssignment("evcs", "tesla"),
        SetAssignment("twc", None),
    ]


async def test_invalid_option_is_rejected_and_state_republished(
    handler, intents, rejections
) -> None:
    await handler("tesla_mode", "Turbo")
    assert intents.applied == []
    assert rejections.texts == [PREFIX + "Unbekannte Option „Turbo“ für „Lademodus“."]


async def test_boost_is_not_a_mode_option(handler, intents, rejections) -> None:
    await handler("tesla_mode", "Boost")
    assert intents.applied == []
    assert rejections.texts == [PREFIX + "Unbekannte Option „Boost“ für „Lademodus“."]


async def test_unknown_names_for_the_other_selects(handler, intents, rejections, clock) -> None:
    await handler("evcs_assignment", "Trabant")
    await handler("guest_mode", "boost")
    assert intents.applied == []
    assert rejections.texts == [
        PREFIX + "Unbekannte Option „Trabant“ für „Zuordnung“.",
        PREFIX + "Unbekannte Option „boost“ für „Gast-Lademodus“.",
    ]


async def test_unknown_payload_of_switch_and_buttons(handler, intents, rejections, clock) -> None:
    await handler("regulation", "on")
    await handler("tesla_boost", "ON")
    await handler("tesla_boost_stop", "")
    assert intents.applied == []
    assert rejections.texts == [
        PREFIX + "Unbekannter Befehl „on“ für „Regelbetrieb“.",
        PREFIX + "Unbekannter Befehl „ON“ für „Boost“.",
        PREFIX + "Unbekannter Befehl „“ für „Boost beenden“.",
    ]


async def test_unknown_entity_is_ignored(handler, intents, rejections) -> None:
    await handler("nothing", "ON")
    await handler("tesla_soc", "ON")  # ein Sensor nimmt keine Befehle an
    assert intents.applied == [] and rejections.texts == []


async def test_more_than_one_command_per_second_is_dropped(handler, intents, clock) -> None:
    await handler("regulation", "ON")
    await handler("regulation", "OFF")
    assert len(intents.applied) == 1
    clock.advance(0.9)
    await handler("regulation", "OFF")
    assert len(intents.applied) == 1
    clock.advance(0.1)
    await handler("regulation", "OFF")
    assert len(intents.applied) == 2


async def test_the_limit_is_per_entity(handler, intents) -> None:
    await handler("regulation", "ON")
    await handler("tesla_mode", "Aus")
    await handler("egolf_mode", "Aus")
    assert len(intents.applied) == 3


async def test_a_flood_of_invalid_commands_gives_one_notification_per_second(
    handler, rejections, clock
) -> None:
    for _ in range(5):
        await handler("tesla_mode", "Turbo")
    assert len(rejections.texts) == 1
    clock.advance(1.0)
    await handler("tesla_mode", "Turbo")
    assert len(rejections.texts) == 2


async def test_core_rejection_becomes_a_notification(handler, intents, rejections) -> None:
    intents.error = Rejected(["Wallbox „twc“ ist mehreren Fahrzeugen zugeordnet."])
    await handler("twc_assignment", "e-Golf")
    assert rejections.texts == [PREFIX + "Wallbox „twc“ ist mehreren Fahrzeugen zugeordnet."]


async def test_several_sentences_are_joined(handler, intents, rejections) -> None:
    intents.error = Rejected(["Erster Satz.", "Zweiter Satz."])
    await handler("regulation", "ON")
    assert rejections.texts == [PREFIX + "Erster Satz. Zweiter Satz."]


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (CoreDown(), "core nicht erreichbar."),
        (CoreUnavailableError("weg"), "core nicht erreichbar."),
        (ConflictAgain(), "Die Absicht wurde gleichzeitig geändert. Bitte erneut versuchen."),
        (UnknownTarget("Unbekanntes Fahrzeug „x“."), "Unbekanntes Fahrzeug „x“."),
    ],
)
async def test_other_failures_become_notifications(
    handler, intents, rejections, error, reason
) -> None:
    intents.error = error
    await handler("regulation", "ON")
    assert rejections.texts == [PREFIX + reason]


async def test_unexpected_errors_do_not_escape(handler, intents, rejections) -> None:
    intents.error = RuntimeError("kaputt")
    await handler("regulation", "ON")
    assert rejections.texts == []


async def test_a_failing_notification_does_not_escape(handler, rejections) -> None:
    rejections.error = RuntimeError("HA nicht erreichbar")
    await handler("tesla_mode", "Turbo")


async def test_home_assistant_only_gets_operator_changes(handler, intents, clock) -> None:
    """Weder Live-Schaltung (``SetActuators``) noch ``SetPriority`` sind aus HA erreichbar."""
    payloads = ["ON", "OFF", "PRESS", "Automatisch", "Tesla Model 3", "e-Golf", "Sofort", "live"]
    for object_id in handler._commands:
        for payload in payloads:
            clock.advance(1.0)
            await handler(object_id, payload)
    assert intents.applied
    allowed = (SetControl, SetMode, SetBoost, SetAssignment, SetGuestMode)
    assert all(isinstance(change, allowed) for change, _, _ in intents.applied)


async def test_log_rejections_only_logs() -> None:
    await LogRejections().reject(PREFIX + "x")


async def test_command_through_the_real_service_is_audited_as_ha(
    cfg, audit: AuditLog, clock: ManualClock, rejections
) -> None:
    client = FakeCoreClient()
    service = IntentService(client, LiveState(), FakeFeed(), audit, clock, cfg)  # type: ignore[arg-type]
    handler = HaCommandHandler(cfg, service, rejections, clock)
    await handler("tesla_mode", "Sofort")
    assert client.valid is not None
    assert client.valid.vehicles["tesla"].mode is ChargeMode.NOW
    entry = audit.recent()[0]
    assert (entry.actor, entry.source, entry.action, entry.target) == (
        "home-assistant",
        "ha",
        "intent.mode",
        "tesla",
    )
    client.error = CoreUnavailableError("weg")
    clock.advance(1.0)
    await handler("tesla_mode", "Aus")
    assert rejections.texts == [PREFIX + "core nicht erreichbar."]
