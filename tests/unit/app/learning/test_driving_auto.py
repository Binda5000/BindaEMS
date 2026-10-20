import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, insert, select
from structlog.testing import capture_logs
from tests.app_helpers import T_APP
from tests.plan_helpers import until
from tests.unit.app.control.conftest import FakeCoreClient, FakeFeed

from bindaems.app.audit import AuditLog
from bindaems.app.charging.planned import AUTO_ADOPT
from bindaems.app.control.intent import IntentService, SetTarget
from bindaems.app.core_link import CoreReply, CoreUnavailableError, LiveState
from bindaems.app.db.schema import charging_session, driving_automation, planned_charge
from bindaems.app.learning.driving_auto import AUTO_RECENT, DrivingAutomation
from bindaems.app.learning.driving_profile import Confidence, VehicleProfile, WeekdayProfile
from bindaems.app.settings.service import SettingsService
from bindaems.shared.config import Config
from bindaems.shared.control import ChargeMode, Intent, VehicleIntent, next_departure
from bindaems.shared.timeutil import ManualClock

# ``T_APP`` ist Freitag, 9. Oktober 2026, 10:00 Ortszeit: die Abfahrt um 07:15 ist heute vorbei,
# Samstag und Sonntag fährt das Auto nicht, also gilt Montag
MONDAY = date(2026, 10, 12)


def weekday(
    day: int,
    departure_min: int | None = None,
    departure_confidence: Confidence | None = None,
    target: int | None = None,
    target_confidence: Confidence | None = None,
) -> WeekdayProfile:
    trips = 8 if departure_min is not None else 0
    return WeekdayProfile(
        weekday=day,
        basis="weekday",
        days=8,
        trips=trips,
        trip_share=trips / 8,
        departure_min=departure_min,
        departure_p50_min=departure_min,
        departure_p80_min=departure_min,
        departure_confidence=departure_confidence,
        arrival_p50_min=None,
        arrival_soc_p50=None,
        consumption_p50_pp=None,
        consumption_p80_pp=None,
        target_soc_pct=target,
        target_clamped=False,
        target_confidence=target_confidence,
    )


def commuter(
    departure: Confidence | None = "high", target: Confidence | None = "high", soc: int = 60
) -> VehicleProfile:
    """Fährt werktags um 07:15 weg; Vorschlag Ziel ``soc`` %."""
    days = [weekday(day, 435, departure, soc, target) for day in range(5)]
    days += [weekday(5), weekday(6)]
    return VehicleProfile(
        key="tesla",
        source="tessie",
        data_from=T_APP - timedelta(days=56),
        data_to=T_APP,
        days=56,
        weekdays=days,
        presence=[[0.0] * 96 for _ in range(7)],
    )


class Profiles:
    """Stand-in für ``DrivingService.profile``."""

    def __init__(self) -> None:
        self.by_key: dict[str, VehicleProfile] = {"tesla": commuter()}
        self.hook: Callable[[], None] | None = None  # läuft bei jeder Abfrage

    def __call__(self, key: str) -> VehicleProfile | None:
        if self.hook is not None:
            self.hook()
        return self.by_key.get(key)


def planned_intent(**fields: Any) -> Intent:
    vehicle = VehicleIntent(mode=ChargeMode.PLANNED, target_soc_pct=80, departure="07:00")
    return Intent(version=1, vehicles={"tesla": vehicle.model_copy(update=fields)})


def trusted(version: int = 1) -> dict[str, Any]:
    return {"intent": {"version": version}, "intent_version": version, "vehicles": []}


def set_switch(settings: SettingsService, key: str, on: bool) -> None:
    current = settings.current()
    driving = current.settings.learning.driving
    automation = {**driving.automation, key: on}
    learning = current.settings.learning.model_copy(
        update={"driving": driving.model_copy(update={"automation": automation})}
    )
    settings.update(
        current.settings.model_copy(update={"learning": learning}),
        base_version=current.version,
        actor="test",
        source="cli",
    )


def tesla(client: FakeCoreClient) -> VehicleIntent:
    assert client.valid is not None
    return client.valid.vehicles["tesla"]


def stored(engine: Engine) -> dict[str, Any]:
    with engine.connect() as conn:
        return {row.vehicle: row for row in conn.execute(select(driving_automation))}


def open_row(engine: Engine, started_at: datetime, departure_at: datetime) -> None:
    with engine.begin() as conn:
        session_id = conn.execute(
            insert(charging_session)
            .returning(charging_session.c.id)
            .values(wallbox="twc", vehicle="tesla", start=started_at, end=None, last_seen=T_APP)
        ).scalar_one()
        conn.execute(
            insert(planned_charge).values(
                session_id=session_id,
                vehicle="tesla",
                target_soc_pct=80,
                departure_at=departure_at,
                started_at=started_at,
                start_soc_pct=40.0,
                start_soc_source="measured",
                feasible=True,
                outcome="open",
            )
        )


@pytest.fixture
def live() -> LiveState:
    state = LiveState()
    state.connected = True
    state.health = trusted()
    return state


@pytest.fixture
def client() -> FakeCoreClient:
    fake = FakeCoreClient()
    fake.valid = planned_intent()
    return fake


@pytest.fixture
def intents(client, live, audit, clock, cfg) -> IntentService:
    return IntentService(client, live, FakeFeed(), audit, clock, cfg)  # type: ignore[arg-type]


@pytest.fixture
def profiles() -> Profiles:
    return Profiles()


@pytest.fixture
def make_auto(
    intents: IntentService,
    live: LiveState,
    profiles: Profiles,
    settings_service: SettingsService,
    audit: AuditLog,
    engine: Engine,
    cfg: Config,
    clock: ManualClock,
) -> Callable[..., DrivingAutomation]:
    def make(**kwargs: Any) -> DrivingAutomation:
        return DrivingAutomation(
            intents=intents,
            live=live,
            profiles=profiles,
            settings=settings_service,
            audit=audit,
            engine=engine,
            cfg=cfg,
            clock=clock,
            **kwargs,
        )

    return make


@pytest.fixture
def auto(make_auto) -> DrivingAutomation:
    return make_auto()


@pytest.fixture
def enabled(settings_service: SettingsService) -> None:
    set_switch(settings_service, "tesla", True)


@pytest.mark.usefixtures("enabled")
async def test_applies_next_trip_in_planned_mode(auto, client, audit, engine, clock) -> None:
    await auto.tick()
    assert len(client.sent) == 1
    car = tesla(client)
    assert (car.target_soc_pct, car.departure, car.departure_on) == (60, "07:15", MONDAY)
    assert car.mode is ChargeMode.PLANNED  # den Modus ändert sie nie
    entry = audit.recent(1)[0]
    assert (entry.actor, entry.source, entry.action, entry.target) == (
        "Automatik",
        "system",
        "intent.target",
        "tesla",
    )
    assert entry.details is not None
    assert entry.details["to"] == "Ziel 60 %, Abfahrt Mo 12.10. 07:15"
    assert auto.state("tesla") == {
        "enabled": True,
        "state": "applied",
        "text": "Automatik: Mo 07:15, Ziel 60 % (gesetzt 09.10. 10:00).",
        "paused_until": None,
        "last_set_at": T_APP,
    }
    row = stored(engine)["tesla"]
    assert row.last_set_at == T_APP
    assert row.last_values == {
        "target_soc_pct": 60,
        "departure": "07:15",
        "departure_on": "2026-10-12",
    }
    assert auto.recently_set("tesla", T_APP + AUTO_RECENT) is True
    assert auto.recently_set("tesla", T_APP + AUTO_RECENT + timedelta(seconds=1)) is False
    assert auto.recently_set("egolf", T_APP) is False
    clock.advance(timedelta(minutes=1))
    await auto.tick()
    assert len(client.sent) == 1  # gesetzt bleibt gesetzt


async def test_does_nothing_when_off(auto, client, engine) -> None:
    await auto.tick()
    assert client.sent == []
    assert auto.state("tesla") == {
        "enabled": False,
        "state": "off",
        "text": "Automatik aus.",
        "paused_until": None,
        "last_set_at": None,
    }
    assert stored(engine) == {}


@pytest.mark.usefixtures("enabled")
async def test_inactive_outside_planned_mode(auto, client) -> None:
    client.valid = planned_intent(mode=ChargeMode.PV)
    await auto.tick()
    assert client.sent == []
    state = auto.state("tesla")
    assert (state["state"], state["text"]) == (
        "inactive",
        "Automatik wartet: Das Auto steht nicht auf „Geplant“.",
    )
    client.valid = Intent(version=1)  # ohne Eintrag lädt das Auto im PV-Überschuss
    await auto.tick()
    assert (client.sent, auto.state("tesla")["state"]) == ([], "inactive")


@pytest.mark.usefixtures("enabled")
async def test_no_change_while_planned_charge_runs(auto, client, engine) -> None:
    saturday = datetime(2026, 10, 10, 5, 0, tzinfo=UTC)  # Sa 07:00 Ortszeit
    open_row(engine, T_APP - AUTO_ADOPT - timedelta(seconds=1), saturday)
    await auto.tick()
    assert client.sent == []
    state = auto.state("tesla")
    assert (state["state"], state["text"]) == (
        "waiting",
        "Automatik wartet: Eine geplante Ladung läuft bis Sa 07:00.",
    )


@pytest.mark.usefixtures("enabled")
async def test_fresh_planned_charge_still_gets_the_suggestion(auto, client, engine) -> None:
    # nach einer Abfahrt, zu der das Auto stecken blieb, beginnt sofort die nächste Zeile
    open_row(engine, T_APP - AUTO_ADOPT, datetime(2026, 10, 10, 5, 0, tzinfo=UTC))
    await auto.tick()
    assert tesla(client).departure_on == MONDAY
    assert auto.state("tesla")["state"] == "applied"


@pytest.mark.usefixtures("enabled")
async def test_manual_change_pauses_until_departure(auto, intents, client, clock, engine) -> None:
    await intents.apply(SetTarget("tesla", 90, "08:00"), actor="anna", source="ui")
    until_at = next_departure("08:00", T_APP)  # Sa 08:00 Ortszeit
    assert until_at == datetime(2026, 10, 10, 6, 0, tzinfo=UTC)
    await auto.tick()
    assert len(client.sent) == 1  # nur die Bedienung
    assert auto.state("tesla") == {
        "enabled": True,
        "state": "paused",
        "text": "Automatik pausiert bis Sa 08:00, weil Ziel oder Abfahrt von Hand geändert wurden.",
        "paused_until": until_at,
        "last_set_at": None,
    }
    assert stored(engine)["tesla"].paused_until == until_at
    clock.advance(until_at - clock.now() - timedelta(seconds=1))
    await auto.tick()
    assert len(client.sent) == 1
    clock.advance(timedelta(seconds=1))  # die Pause endet mit ihrer Abfahrt
    await auto.tick()
    assert len(client.sent) == 2
    assert (tesla(client).target_soc_pct, tesla(client).departure_on) == (60, MONDAY)
    assert auto.state("tesla")["paused_until"] is None
    assert stored(engine)["tesla"].paused_until is None


@pytest.mark.usefixtures("enabled")
async def test_manual_change_without_departure_pauses_until_midnight(auto, engine) -> None:
    auto.on_manual_target("tesla", None)
    midnight = datetime(2026, 10, 9, 22, 0, tzinfo=UTC)  # Sa 00:00 Ortszeit
    assert auto.state("tesla")["paused_until"] == midnight
    await auto.tick()
    assert stored(engine)["tesla"].paused_until == midnight


async def test_manual_change_while_off_is_ignored(auto, intents, client, engine) -> None:
    await intents.apply(SetTarget("tesla", 90, "08:00"), actor="anna", source="ui")
    await auto.tick()
    assert auto.state("tesla")["paused_until"] is None
    assert stored(engine) == {}


@pytest.mark.usefixtures("enabled")
async def test_manual_change_ends_the_recent_set(auto, intents) -> None:
    await auto.tick()
    assert auto.recently_set("tesla", T_APP) is True
    await intents.apply(SetTarget("tesla", 90, None), actor="anna", source="ha")
    assert auto.recently_set("tesla", T_APP) is False  # die Zeile übernimmt das nicht


@pytest.mark.usefixtures("enabled")
async def test_uncertain_values_are_kept(auto, client, profiles) -> None:
    profiles.by_key["tesla"] = commuter(departure="medium", target="low")
    await auto.tick()
    car = tesla(client)
    assert (car.target_soc_pct, car.departure, car.departure_on) == (80, "07:15", MONDAY)
    assert auto.state("tesla")["text"] == "Automatik: Mo 07:15, Ziel 80 % (gesetzt 09.10. 10:00)."


@pytest.mark.usefixtures("enabled")
async def test_uncertain_departure_keeps_the_intent_departure(auto, client, profiles) -> None:
    profiles.by_key["tesla"] = commuter(departure="low", target="medium")
    await auto.tick()
    car = tesla(client)
    assert (car.target_soc_pct, car.departure, car.departure_on) == (60, "07:00", None)
    assert auto.state("tesla")["text"] == "Automatik: Sa 07:00, Ziel 60 % (gesetzt 09.10. 10:00)."


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("suggestion", ["low", "missing", "no_trip"])
async def test_without_a_confident_suggestion_nothing_is_sent(
    auto, client, profiles, suggestion
) -> None:
    if suggestion == "low":
        profiles.by_key["tesla"] = commuter(departure="low", target="low")
    elif suggestion == "missing":
        del profiles.by_key["tesla"]
    else:
        profiles.by_key["tesla"] = commuter().model_copy(
            update={"weekdays": [weekday(day) for day in range(7)]}
        )
    await auto.tick()
    assert client.sent == []
    state = auto.state("tesla")
    assert (state["state"], state["text"]) == (
        "uncertain",
        "Automatik wartet: Der Vorschlag ist noch zu unsicher.",
    )


@pytest.mark.usefixtures("enabled")
async def test_no_send_when_values_already_match(auto, client) -> None:
    client.valid = planned_intent(target_soc_pct=60, departure="07:15", departure_on=MONDAY)
    await auto.tick()
    assert client.sent == []
    state = auto.state("tesla")
    assert (state["state"], state["text"], state["last_set_at"]) == (
        "applied",
        "Automatik: Mo 07:15, Ziel 60 %.",  # nie selbst gesetzt: ohne „gesetzt“
        None,
    )
    assert auto.recently_set("tesla", T_APP) is False


@pytest.mark.usefixtures("enabled")
async def test_core_down_sets_error_and_retries(auto, client, clock) -> None:
    client.error = CoreUnavailableError("weg")
    await auto.tick()
    state = auto.state("tesla")
    assert (state["state"], state["text"]) == (
        "error",
        "Automatik fehlgeschlagen: core nicht erreichbar.",
    )
    client.error = None
    client.replies = [CoreReply(422, {"result": "rejected", "details": ["Ziel passt nicht."]})]
    clock.advance(timedelta(minutes=1))
    await auto.tick()
    assert auto.state("tesla")["text"] == "Automatik fehlgeschlagen: Ziel passt nicht."
    assert auto.state("tesla")["last_set_at"] is None
    clock.advance(timedelta(minutes=1))
    await auto.tick()  # nächster Versuch eine Minute später
    assert auto.state("tesla")["state"] == "applied"
    assert tesla(client).departure_on == MONDAY


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    "health",
    [
        None,
        {"intent": {"version": 2}, "intent_version": 1, "vehicles": []},
        {"intent_version": None, "vehicles": []},
    ],
)
async def test_untrusted_health_sets_error_without_sending(auto, client, live, health) -> None:
    live.health = health
    await auto.tick()
    assert client.sent == []
    state = auto.state("tesla")
    assert (state["state"], state["text"]) == (
        "error",
        "Automatik fehlgeschlagen: Der Stand der Regelung ist unbekannt.",
    )


@pytest.mark.usefixtures("enabled")
async def test_disconnected_core_counts_as_untrusted(auto, client, live) -> None:
    live.connected = False
    await auto.tick()
    assert (client.sent, auto.state("tesla")["state"]) == ([], "error")


async def test_switch_on_and_off_is_a_settings_version_with_audit(
    auto, client, settings_service, audit
) -> None:
    before = settings_service.current().version
    await auto.set_enabled("tesla", True, actor="anna", source="ui")
    current = settings_service.current()
    assert current.version == before + 1
    assert current.settings.learning.driving.automation == {"tesla": True}
    entries = [(e.actor, e.source, e.action, e.target, e.details) for e in audit.recent(5)]
    assert (
        "anna",
        "ui",
        "control.automation",
        "tesla",
        {
            "from": "Automatik aus",
            "to": "Automatik an",
            "version": before + 1,
        },
    ) in entries
    assert len(client.sent) == 1  # die Automatik prüft gleich
    assert auto.state("tesla")["state"] == "applied"
    await auto.set_enabled("tesla", True, actor="anna", source="ui")  # schon an
    assert settings_service.current().version == before + 1
    await auto.set_enabled("tesla", False, actor="otto", source="ha")
    assert settings_service.current().settings.learning.driving.automation == {"tesla": False}
    last = audit.recent(1)[0]
    assert (last.actor, last.source, last.action, last.details) == (
        "otto",
        "ha",
        "control.automation",
        {"from": "Automatik an", "to": "Automatik aus", "version": before + 2},
    )


@pytest.mark.usefixtures("enabled")
async def test_switch_off_stops_immediately(auto, client, profiles, settings_service) -> None:
    await auto.tick()
    assert len(client.sent) == 1
    await auto.set_enabled("tesla", False, actor="anna", source="ui")
    assert auto.state("tesla")["state"] == "off"
    profiles.by_key["tesla"] = commuter(soc=70)
    await auto.tick()
    assert len(client.sent) == 1
    # während einer Prüfung ausgeschaltet: sie sendet nicht mehr
    set_switch(settings_service, "tesla", True)
    profiles.hook = lambda: set_switch(settings_service, "tesla", False)
    await auto.tick()
    assert len(client.sent) == 1
    assert auto.state("tesla")["state"] == "off"


@pytest.mark.usefixtures("enabled")
async def test_switch_on_clears_the_pause(auto, intents, client) -> None:
    await intents.apply(SetTarget("tesla", 90, "08:00"), actor="anna", source="ui")
    await auto.set_enabled("tesla", False, actor="anna", source="ui")
    await auto.set_enabled("tesla", True, actor="anna", source="ui")
    assert auto.state("tesla")["paused_until"] is None
    assert tesla(client).departure_on == MONDAY  # gleich wieder gesetzt


async def test_unknown_vehicle_cannot_be_switched(auto) -> None:
    from bindaems.app.control.intent import UnknownTarget

    with pytest.raises(UnknownTarget):
        await auto.set_enabled("bus", True, actor="anna", source="ui")


@pytest.mark.usefixtures("enabled")
async def test_state_survives_restart(make_auto, intents, client, engine) -> None:
    first = make_auto()
    await first.tick()  # setzt
    await intents.apply(SetTarget("tesla", 90, "08:00"), actor="anna", source="ui")
    await first.tick()  # speichert die Pause
    until_at = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)
    second = make_auto()
    await second.tick()
    assert len(client.sent) == 2  # gesetzt, dann von Hand; nach dem Neustart pausiert
    state = second.state("tesla")
    assert (state["state"], state["paused_until"], state["last_set_at"]) == (
        "paused",
        until_at,
        T_APP,
    )


@pytest.mark.usefixtures("enabled")
async def test_unreadable_stored_state_blocks_sending(auto, client, engine, monkeypatch) -> None:
    def broken() -> None:
        raise RuntimeError("Datenbank gesperrt")

    monkeypatch.setattr(auto, "_load", broken)
    with capture_logs() as logs:
        await auto.tick()
    assert client.sent == []
    assert auto.state("tesla")["text"] == (
        "Automatik fehlgeschlagen: Zustand der Automatik nicht lesbar."
    )
    assert any(entry["log_level"] == "error" for entry in logs)


@pytest.mark.usefixtures("enabled")
async def test_run_ticks_on_request_and_survives_errors(auto, client, monkeypatch) -> None:
    ticks: list[int] = []
    real_tick = auto.tick

    async def flaky() -> None:
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("kaputt")
        await real_tick()

    monkeypatch.setattr(auto, "tick", flaky)
    with capture_logs() as logs:
        task = asyncio.create_task(auto.run())
        await until(lambda: len(ticks) == 1)
        auto.request_tick()  # etwa nach einem Lauf des Fahrverhaltens
        await until(lambda: len(client.sent) == 1)
    assert "Automatik für Ziel und Abfahrt nicht geprüft" in [e["event"] for e in logs]
    assert await auto.close(1.0) is True
    assert task.done()


@pytest.mark.usefixtures("enabled")
async def test_run_ticks_every_interval(make_auto, monkeypatch) -> None:
    auto = make_auto(interval_s=0.01)
    ticks: list[int] = []
    real_tick = auto.tick

    async def counted() -> None:
        ticks.append(1)
        await real_tick()

    monkeypatch.setattr(auto, "tick", counted)
    task = asyncio.create_task(auto.run())
    await until(lambda: len(ticks) >= 3)  # ohne Anstoß, nur nach der Wartezeit
    assert await auto.close(1.0) is True
    assert task.cancelled()
