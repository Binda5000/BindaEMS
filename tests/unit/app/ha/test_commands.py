import pytest
from structlog.testing import capture_logs
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
    SetSupport,
    SetTarget,
    UnknownTarget,
)
from bindaems.app.core_link import CoreUnavailableError, LiveState
from bindaems.app.ha.commands import HaCommandHandler, LogRejections
from bindaems.app.settings.service import VersionConflictError
from bindaems.shared.control import ChargeMode, Intent, SupportIntent
from bindaems.shared.timeutil import ManualClock

PREFIX = "Befehl aus Home Assistant abgelehnt: "


class FakeIntents:
    """Zeichnet ``apply`` auf; ``error`` wird beim nächsten Aufruf geworfen."""

    def __init__(self) -> None:
        self.applied: list[tuple[object, str, str]] = []
        self.error: Exception | None = None
        self.intent: Intent | None = None  # die gültige Absicht, die ``current`` liefert
        self.current_error: Exception | None = None

    async def current(self) -> Intent | None:
        if self.current_error is not None:
            raise self.current_error
        return self.intent

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


class FakePlanning:
    """Zeichnet die Befehle an die Planung auf; ``error`` wird beim Schalten geworfen."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.error: Exception | None = None

    def set_grid_charge(self, on, *, actor, source):
        if self.error is not None:
            raise self.error
        self.calls.append(("set_grid_charge", on, actor, source))

    def request(self, trigger) -> None:
        self.calls.append(("request", trigger))


class FakeAutomation:
    """Zeichnet ``set_enabled`` auf; ``error`` wird beim nächsten Aufruf geworfen."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, str, str]] = []
        self.error: Exception | None = None

    async def set_enabled(self, key, on, *, actor, source) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append((key, on, actor, source))


@pytest.fixture
def automation() -> FakeAutomation:
    return FakeAutomation()


@pytest.fixture
def planning() -> FakePlanning:
    return FakePlanning()


@pytest.fixture
def intents() -> FakeIntents:
    return FakeIntents()


@pytest.fixture
def rejections() -> FakeRejections:
    return FakeRejections()


@pytest.fixture
def handler(cfg, intents, rejections, clock, planning, automation) -> HaCommandHandler:
    return HaCommandHandler(
        cfg,
        intents,  # type: ignore[arg-type]
        rejections,
        clock,
        planning,
        automation,  # type: ignore[arg-type]
    )


@pytest.fixture
def bare_handler(cfg, intents, rejections, clock) -> HaCommandHandler:
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
    with capture_logs() as logs:
        await handler("regulation", "ON")
    assert rejections.texts == []
    assert [(e["event"], e["log_level"], e["object_id"]) for e in logs] == [
        ("Befehl aus Home Assistant fehlgeschlagen", "error", "regulation")
    ]


async def test_a_failing_notification_does_not_escape(handler, rejections) -> None:
    rejections.error = RuntimeError("HA nicht erreichbar")
    with capture_logs() as logs:
        await handler("tesla_mode", "Turbo")
    assert [(e["event"], e["log_level"]) for e in logs] == [("Ablehnung nicht gemeldet", "error")]


async def test_home_assistant_only_gets_operator_changes(handler, intents, clock) -> None:
    """Weder Live-Schaltung (``SetActuators``) noch ``SetPriority`` sind aus HA erreichbar."""
    payloads = [
        "ON",
        "OFF",
        "PRESS",
        "Automatisch",
        "Tesla Model 3",
        "e-Golf",
        "Sofort",
        "live",
        "60",
    ]
    for object_id in handler._commands:
        for payload in payloads:
            clock.advance(1.0)
            await handler(object_id, payload)
    assert intents.applied
    allowed = (SetControl, SetMode, SetTarget, SetBoost, SetAssignment, SetGuestMode, SetSupport)
    assert all(isinstance(change, allowed) for change, _, _ in intents.applied)


async def test_log_rejections_only_logs() -> None:
    with capture_logs() as logs:
        await LogRejections().reject(PREFIX + "x")
    assert logs == [
        {
            "event": "Befehl aus Home Assistant abgelehnt",
            "text": PREFIX + "x",
            "log_level": "warning",
        }
    ]


async def test_a_clock_that_jumps_back_does_not_block_commands(handler, intents, clock) -> None:
    clock.advance(10.0)
    await handler("regulation", "ON")
    clock.advance(-5.0)  # die Uhr springt zurück: der letzte Befehl liegt „in der Zukunft“
    await handler("regulation", "OFF")
    assert [change for change, _, _ in intents.applied] == [SetControl(True), SetControl(False)]


async def test_the_payload_in_a_rejection_is_cleaned_and_truncated(
    handler, rejections, clock
) -> None:
    await handler("tesla_mode", "Tur\nbo\x00\u202e")
    clock.advance(1.0)
    await handler("tesla_mode", "x" * 64)
    clock.advance(1.0)
    await handler("tesla_mode", "y" * 65)
    assert rejections.texts == [
        PREFIX + "Unbekannte Option „Turbo“ für „Lademodus“.",
        PREFIX + f"Unbekannte Option „{'x' * 64}“ für „Lademodus“.",
        PREFIX + f"Unbekannte Option „{'y' * 64}…“ für „Lademodus“.",
    ]


async def test_commands_for_unknown_entities_are_only_debug_logged(handler) -> None:
    with capture_logs() as logs:
        await handler("nothing", "ON")
    assert [(e["event"], e["log_level"]) for e in logs] == [
        ("Befehl für unbekannte Entität", "debug")
    ]


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


@pytest.mark.parametrize(("payload", "on"), [("ON", True), ("OFF", False)])
async def test_grid_charge_switch_command(handler, planning, intents, payload, on) -> None:
    await handler("grid_charge", payload)
    assert planning.calls == [("set_grid_charge", on, "home-assistant", "ha")]
    assert intents.applied == []


async def test_replan_button_requests_manual(handler, planning) -> None:
    await handler("replan", "PRESS")
    assert planning.calls == [("request", "manual")]


async def test_unknown_grid_charge_payload_is_rejected(handler, planning, rejections) -> None:
    await handler("grid_charge", "VIELLEICHT")
    assert planning.calls == []
    assert rejections.texts == [PREFIX + "Unbekannter Befehl „VIELLEICHT“ für „Netzladen erlaubt“."]


async def test_unknown_replan_payload_is_rejected(handler, planning, rejections) -> None:
    await handler("replan", "OFF")
    assert planning.calls == []
    assert rejections.texts == [PREFIX + "Unbekannter Befehl „OFF“ für „Neu planen“."]


async def test_version_conflict_is_rejected_with_its_text(handler, planning, rejections) -> None:
    planning.error = VersionConflictError(5)
    await handler("grid_charge", "ON")
    assert rejections.texts == [PREFIX + str(VersionConflictError(5))]


async def test_other_planning_error_is_logged_not_raised(handler, planning, rejections) -> None:
    planning.error = RuntimeError("kaputt")
    with capture_logs() as logs:
        await handler("grid_charge", "ON")
    assert rejections.texts == []
    assert any(entry["event"] == "Befehl aus Home Assistant fehlgeschlagen" for entry in logs)


async def test_without_planning_the_plan_commands_are_unknown(bare_handler, rejections) -> None:
    await bare_handler("grid_charge", "ON")
    await bare_handler("replan", "PRESS")
    assert rejections.texts == []


async def test_support_switch_keeps_the_threshold(handler, intents, clock) -> None:
    intents.intent = Intent(version=4, support=SupportIntent(allowed=True, soc_pct=65.0))
    await handler("battery_support", "OFF")
    clock.advance(1.0)
    await handler("battery_support", "ON")
    assert intents.applied == [
        (SetSupport(False, 65.0), "home-assistant", "ha"),
        (SetSupport(True, 65.0), "home-assistant", "ha"),
    ]


async def test_support_switch_without_a_threshold_keeps_the_config_value(handler, intents) -> None:
    intents.intent = Intent(version=4)
    await handler("battery_support", "OFF")
    assert intents.applied == [(SetSupport(False, None), "home-assistant", "ha")]


async def test_support_switch_without_an_intent_starts_from_the_defaults(handler, intents) -> None:
    await handler("battery_support", "OFF")
    assert intents.applied == [(SetSupport(False, None), "home-assistant", "ha")]


async def test_number_sets_the_threshold_and_keeps_the_switch(handler, intents, clock) -> None:
    intents.intent = Intent(version=4, support=SupportIntent(allowed=False))
    await handler("support_soc", "60")
    clock.advance(1.0)
    await handler("support_soc", "62.5")
    clock.advance(1.0)
    await handler("support_soc", " 100.0 ")
    assert [change for change, _, _ in intents.applied] == [
        SetSupport(False, 60.0),
        SetSupport(False, 62.5),
        SetSupport(False, 100.0),
    ]


async def test_number_command_out_of_range_is_rejected(handler, intents, rejections, clock) -> None:
    for payload in ("150", "19.9", "-5"):
        await handler("support_soc", payload)
        clock.advance(1.0)
    assert intents.applied == []
    assert rejections.texts == [
        PREFIX + "Unterstützungsschwelle „150“ liegt außerhalb von 20–100 %.",
        PREFIX + "Unterstützungsschwelle „19.9“ liegt außerhalb von 20–100 %.",
        PREFIX + "Unterstützungsschwelle „-5“ liegt außerhalb von 20–100 %.",
    ]


async def test_number_command_that_is_no_number_is_rejected(
    handler, intents, rejections, clock
) -> None:
    for payload in ("", "viel", "nan", "inf", "6 0"):
        await handler("support_soc", payload)
        clock.advance(1.0)
    assert intents.applied == []
    assert rejections.texts == [
        PREFIX + f"Unbekannter Befehl „{shown}“ für „Unterstützungsschwelle“."
        for shown in ("", "viel", "nan", "inf", "6 0")
    ]


async def test_support_command_while_the_core_is_down_is_rejected(
    handler, intents, rejections
) -> None:
    intents.current_error = CoreDown()
    await handler("support_soc", "60")
    assert intents.applied == []
    assert rejections.texts == [PREFIX + "core nicht erreichbar."]


async def test_support_commands_through_the_real_service_are_audited(
    cfg, audit: AuditLog, clock: ManualClock, rejections
) -> None:
    client = FakeCoreClient()
    service = IntentService(client, LiveState(), FakeFeed(), audit, clock, cfg)  # type: ignore[arg-type]
    handler = HaCommandHandler(cfg, service, rejections, clock)
    await handler("support_soc", "60")
    clock.advance(1.0)
    await handler("battery_support", "OFF")
    assert client.valid is not None
    assert client.valid.support == SupportIntent(allowed=False, soc_pct=60.0)
    entry = audit.recent()[0]
    assert (entry.actor, entry.source, entry.action, entry.target) == (
        "home-assistant",
        "ha",
        "control.support",
        "Akku-Unterstützung",
    )
    assert entry.details is not None and entry.details["to"] == "gesperrt"
    assert rejections.texts == []


async def test_planned_is_an_option_for_vehicles_but_not_for_guests(
    handler, intents, rejections
) -> None:
    await handler("tesla_mode", "Geplant")
    await handler("guest_mode", "Geplant")
    assert [change for change, _, _ in intents.applied] == [SetMode("tesla", ChargeMode.PLANNED)]
    assert rejections.texts == [PREFIX + "Unbekannte Option „Geplant“ für „Gast-Lademodus“."]


async def test_target_number_sets_only_the_target(handler, intents, clock) -> None:
    for payload in ("90", "85.0", "20", "100"):
        await handler("tesla_target_soc", payload)
        clock.advance(1.0)
    await handler("egolf_target_soc", "70")
    assert [change for change, _, _ in intents.applied] == [
        SetTarget("tesla", 90, None),
        SetTarget("tesla", 85, None),
        SetTarget("tesla", 20, None),
        SetTarget("tesla", 100, None),
        SetTarget("egolf", 70, None),
    ]


async def test_target_number_out_of_range_or_no_whole_number_is_rejected(
    handler, intents, rejections, clock
) -> None:
    for payload in ("15", "101", "80.5", "viel", "nan", ""):
        await handler("tesla_target_soc", payload)
        clock.advance(1.0)
    assert intents.applied == []
    assert rejections.texts == [
        PREFIX + "Ziel-SOC „15“ liegt außerhalb von 20–100 %.",
        PREFIX + "Ziel-SOC „101“ liegt außerhalb von 20–100 %.",
        *(
            PREFIX + f"Unbekannter Befehl „{shown}“ für „Ziel-SOC“."
            for shown in ("80.5", "viel", "nan", "")
        ),
    ]


async def test_departure_text_sets_only_the_departure(handler, intents, clock) -> None:
    for payload in ("06:30", "00:00", "23:59"):
        await handler("tesla_departure", payload)
        clock.advance(1.0)
    assert [change for change, _, _ in intents.applied] == [
        SetTarget("tesla", None, "06:30"),
        SetTarget("tesla", None, "00:00"),
        SetTarget("tesla", None, "23:59"),
    ]


async def test_departure_text_rejects_bad_time(handler, intents, rejections, clock) -> None:
    for payload in ("7.00", "7:00", "24:00", "06:60", ""):
        await handler("tesla_departure", payload)
        clock.advance(1.0)
    assert intents.applied == []
    assert rejections.texts == [
        PREFIX + f"Abfahrt „{shown}“ ist keine Uhrzeit (HH:MM)."
        for shown in ("7.00", "7:00", "24:00", "06:60", "")
    ]


async def test_target_and_departure_through_the_real_service(
    cfg, audit: AuditLog, clock: ManualClock, rejections
) -> None:
    client = FakeCoreClient()
    service = IntentService(client, LiveState(), FakeFeed(), audit, clock, cfg)  # type: ignore[arg-type]
    handler = HaCommandHandler(cfg, service, rejections, clock)
    await handler("tesla_target_soc", "90")
    await handler("tesla_departure", "06:30")
    assert client.valid is not None
    tesla = client.valid.vehicles["tesla"]
    assert (tesla.target_soc_pct, tesla.departure) == (90, "06:30")
    entries = audit.recent()
    assert [(e.source, e.action, e.target) for e in entries] == [
        ("ha", "intent.target", "tesla")
    ] * 2
    assert rejections.texts == []


@pytest.mark.parametrize(("payload", "on"), [("ON", True), ("OFF", False)])
@pytest.mark.parametrize("key", ["tesla", "egolf"])
async def test_auto_target_switch_calls_the_automation(
    handler, automation, intents, rejections, key, payload, on
) -> None:
    await handler(f"{key}_auto_target", payload)
    # Urheber und Quelle wie bei den übrigen Befehlen aus Home Assistant
    assert automation.calls == [(key, on, "home-assistant", "ha")]
    assert intents.applied == [] and rejections.texts == []


async def test_unknown_auto_target_payload_is_rejected(handler, automation, rejections) -> None:
    await handler("tesla_auto_target", "VIELLEICHT")
    assert automation.calls == []
    assert rejections.texts == [
        PREFIX + "Unbekannter Befehl „VIELLEICHT“ für „Automatik Ziel und Abfahrt“."
    ]


async def test_auto_target_failures_are_reported(handler, automation, rejections) -> None:
    automation.error = VersionConflictError(5)
    await handler("tesla_auto_target", "ON")
    assert rejections.texts == [PREFIX + str(VersionConflictError(5))]
    automation.error = UnknownTarget("Unbekanntes Fahrzeug „tesla“.")
    await handler("egolf_auto_target", "ON")
    assert rejections.texts[-1] == PREFIX + "Unbekanntes Fahrzeug „tesla“."


async def test_unexpected_auto_target_errors_do_not_escape(handler, automation, rejections) -> None:
    automation.error = RuntimeError("kaputt")
    with capture_logs() as logs:
        await handler("tesla_auto_target", "ON")
    assert rejections.texts == []
    assert any(entry["event"] == "Befehl aus Home Assistant fehlgeschlagen" for entry in logs)


async def test_without_automation_the_switch_is_unknown(bare_handler, rejections) -> None:
    await bare_handler("tesla_auto_target", "ON")
    assert rejections.texts == []  # unbekannte Entität: nur im Log
