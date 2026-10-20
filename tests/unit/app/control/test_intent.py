import asyncio
from datetime import date, timedelta
from typing import Any

import pytest
from structlog.testing import capture_logs
from tests.app_helpers import T_APP
from tests.plan_helpers import sample_plan
from tests.unit.app.control.conftest import FakeCoreClient, FakeFeed

from bindaems.app.audit import AuditLog
from bindaems.app.control.intent import (
    ConflictAgain,
    CoreDown,
    IntentService,
    LiveNotAuthorized,
    Rejected,
    SetActuators,
    SetAssignment,
    SetBoost,
    SetControl,
    SetGuestMode,
    SetMode,
    SetPlan,
    SetPriority,
    SetSupport,
    SetTarget,
    UnknownTarget,
    boost_until,
    derive,
)
from bindaems.app.core_link import CoreReply, CoreUnavailableError, LiveState
from bindaems.shared.control import (
    ActuatorMode,
    ChargeMode,
    Intent,
    SupportIntent,
    SurplusOrder,
    SurplusPriority,
    VehicleIntent,
)
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.plan import IntentPlan, intent_plan


def health(*vehicles: dict[str, Any], intent_version: int | None = 1) -> dict[str, Any]:
    """Health, dessen letzter Regelzyklus die Absicht ``intent_version`` benutzte."""
    return {"intent_version": intent_version, "vehicles": list(vehicles)}


def plan_of(plan_id: int = 7) -> IntentPlan:
    """Bindender Teil eines gespeicherten Plans von ``T_APP`` (vier Slots)."""
    plan = sample_plan(T_APP.isoformat(), ["08:00", "08:15", "08:30", "08:45"])
    return intent_plan(plan.model_copy(update={"id": plan_id}), grid_charge_allowed=False)


def shown(key: str, **fields: Any) -> dict[str, Any]:
    base = {
        "id": key,
        "mode": "pv",
        "boost_until": None,
        "boost_pending": False,
        "override_pending": False,
        "assignment": "default",
        "wallbox": None,
    }
    return {**base, **fields}


def test_version_is_milliseconds_or_one_more() -> None:
    assert derive(None, None, SetControl(True), T_APP).version == epoch_ms(T_APP)
    future = Intent(version=epoch_ms(T_APP) + 50)
    assert derive(future, None, SetControl(True), T_APP).version == epoch_ms(T_APP) + 51


def test_without_intent_the_base_is_version_one_defaults() -> None:
    new = derive(None, None, SetMode("tesla", ChargeMode.NOW), T_APP)
    assert new.control is False
    assert new.actuators == {}
    assert new.vehicles == {"tesla": VehicleIntent(mode=ChargeMode.NOW)}


def test_expired_boost_is_not_sent_back() -> None:
    current = Intent(
        version=1, vehicles={"egolf": VehicleIntent(boost_until=T_APP + timedelta(hours=2))}
    )
    shown_egolf = shown("egolf", wallbox="evcs")
    new = derive(current, health(shown_egolf), SetMode("tesla", ChargeMode.NOW), T_APP)
    assert new.vehicles["egolf"].boost_until is None
    assert new.vehicles["tesla"].mode is ChargeMode.NOW


def test_ended_override_is_not_sent_back() -> None:
    current = Intent(version=1, vehicles={"egolf": VehicleIntent(wallbox="twc")})
    new = derive(current, health(shown("egolf", wallbox="evcs")), SetControl(True), T_APP)
    assert new.vehicles["egolf"].wallbox is None


def test_override_at_another_wallbox_is_not_sent_back() -> None:
    current = Intent(version=1, vehicles={"egolf": VehicleIntent(wallbox="twc")})
    h = health(shown("egolf", assignment="override", wallbox="evcs"))
    assert derive(current, h, SetControl(True), T_APP).vehicles["egolf"].wallbox is None


def test_active_boost_and_override_stay() -> None:
    until = T_APP + timedelta(hours=2)
    current = Intent(version=1, vehicles={"egolf": VehicleIntent(boost_until=until, wallbox="twc")})
    h = health(shown("egolf", boost_until=until.isoformat(), assignment="override", wallbox="twc"))
    new = derive(current, h, SetControl(True), T_APP)
    assert new.vehicles["egolf"].boost_until == until
    assert new.vehicles["egolf"].wallbox == "twc"


def test_unknown_regulation_keeps_a_future_boost() -> None:
    future = T_APP + timedelta(hours=2)
    current = Intent(
        version=1,
        vehicles={
            "egolf": VehicleIntent(boost_until=future, wallbox="twc"),
            "tesla": VehicleIntent(boost_until=T_APP - timedelta(minutes=1), wallbox="evcs"),
        },
    )
    h = health(shown("egolf", mode=None), shown("tesla", mode=None), intent_version=None)
    new = derive(current, h, SetControl(True), T_APP)
    assert new.vehicles["egolf"].boost_until == future
    assert new.vehicles["egolf"].wallbox == "twc"
    assert new.vehicles["tesla"].boost_until is None
    assert new.vehicles["tesla"].wallbox == "evcs"


def test_vehicle_without_mode_in_trusted_health_is_unknown() -> None:
    # Präzisierung 2: ``mode`` null heißt unbekannt, auch wenn die Version passt (etwa ein Health
    # zwischen den Zuweisungen in ``ChargeController.idle``)
    future = T_APP + timedelta(hours=2)
    current = Intent(
        version=5,
        vehicles={
            "egolf": VehicleIntent(boost_until=future, wallbox="twc"),
            "tesla": VehicleIntent(boost_until=T_APP - timedelta(minutes=1), wallbox="evcs"),
        },
    )
    h = health(shown("egolf", mode=None), shown("tesla", mode=None), intent_version=5)
    new = derive(current, h, SetControl(True), T_APP)
    assert new.vehicles["egolf"].boost_until == future
    assert new.vehicles["egolf"].wallbox == "twc"
    assert new.vehicles["tesla"].boost_until is None  # ein Boost in der Vergangenheit entfällt
    assert new.vehicles["tesla"].wallbox == "evcs"


def test_health_of_an_older_intent_is_not_trusted() -> None:
    # Boost an, dann sofort eine zweite Bedienung: Health kennt die Version des Boosts noch nicht
    until = T_APP + timedelta(hours=12)
    current = Intent(
        version=5, vehicles={"tesla": VehicleIntent(boost_until=until, wallbox="evcs")}
    )
    stale = health(shown("tesla"), shown("egolf"), intent_version=4)
    new = derive(current, stale, SetMode("egolf", ChargeMode.NOW), T_APP)
    assert new.vehicles["tesla"].boost_until == until
    assert new.vehicles["tesla"].wallbox == "evcs"
    same = health(shown("tesla"), shown("egolf"), intent_version=5)
    assert derive(current, same, SetMode("egolf", ChargeMode.NOW), T_APP).vehicles["tesla"] == (
        VehicleIntent()
    )
    newer = health(shown("tesla"), shown("egolf"), intent_version=6)
    assert derive(current, newer, SetMode("egolf", ChargeMode.NOW), T_APP).vehicles["tesla"] == (
        VehicleIntent()
    )


def test_a_past_boost_goes_even_with_health_of_an_older_intent() -> None:
    current = Intent(version=5, vehicles={"tesla": VehicleIntent(boost_until=T_APP)})
    new = derive(current, health(shown("tesla"), intent_version=4), SetControl(True), T_APP)
    assert new.vehicles["tesla"].boost_until is None


def test_boost_and_override_waiting_for_the_plug_stay() -> None:
    until = T_APP + timedelta(hours=12)
    current = Intent(
        version=5, vehicles={"tesla": VehicleIntent(boost_until=until, wallbox="evcs")}
    )
    waiting = health(
        shown("tesla", boost_pending=True, override_pending=True, assignment="default"),
        intent_version=5,
    )
    new = derive(current, waiting, SetControl(True), T_APP)
    assert new.vehicles["tesla"] == VehicleIntent(boost_until=until, wallbox="evcs")
    only_boost = health(shown("tesla", boost_pending=True), intent_version=5)
    kept = derive(current, only_boost, SetControl(True), T_APP).vehicles["tesla"]
    assert (kept.boost_until, kept.wallbox) == (until, None)


def test_without_health_a_future_boost_stays_and_a_past_one_goes() -> None:
    future = T_APP + timedelta(hours=2)
    current = Intent(
        version=1,
        vehicles={
            "egolf": VehicleIntent(boost_until=future),
            "tesla": VehicleIntent(boost_until=T_APP),
        },
    )
    new = derive(current, None, SetControl(True), T_APP)
    assert new.vehicles["egolf"].boost_until == future
    assert new.vehicles["tesla"].boost_until is None


def test_a_stale_boost_of_the_changed_vehicle_is_dropped_too() -> None:
    until = T_APP + timedelta(hours=2)
    current = Intent(version=1, vehicles={"tesla": VehicleIntent(boost_until=until)})
    new = derive(current, health(shown("tesla")), SetMode("tesla", ChargeMode.OFF), T_APP)
    assert new.vehicles["tesla"] == VehicleIntent(mode=ChargeMode.OFF)


def test_boost_on_sets_12_h_and_off_removes_it() -> None:
    assert boost_until(T_APP) == T_APP + timedelta(hours=12)
    on = derive(None, None, SetBoost("tesla", True), T_APP)
    assert on.vehicles["tesla"].boost_until == T_APP + timedelta(hours=12)
    off = derive(on, health(shown("tesla", boost_until="x")), SetBoost("tesla", False), T_APP)
    assert off.vehicles["tesla"].boost_until is None


def test_assignment_moves_the_override() -> None:
    current = Intent(version=1, vehicles={"tesla": VehicleIntent(wallbox="evcs")})
    h = health(shown("tesla", assignment="override", wallbox="evcs"))
    moved = derive(current, h, SetAssignment("evcs", "egolf"), T_APP)
    assert moved.vehicles["egolf"].wallbox == "evcs"
    assert moved.vehicles["tesla"].wallbox is None
    auto = derive(current, h, SetAssignment("evcs", None), T_APP)
    assert all(item.wallbox != "evcs" for item in auto.vehicles.values())


def test_assignment_leaves_the_override_at_another_wallbox() -> None:
    current = Intent(version=1, vehicles={"tesla": VehicleIntent(wallbox="twc")})
    h = health(shown("tesla", assignment="override", wallbox="twc"))
    new = derive(current, h, SetAssignment("evcs", "egolf"), T_APP)
    assert new.vehicles["tesla"].wallbox == "twc"
    assert new.vehicles["egolf"].wallbox == "evcs"


def test_other_changes_keep_the_rest() -> None:
    current = Intent(
        version=1,
        control=True,
        actuators={"victron": ActuatorMode.LIVE},
        vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW)},
    )
    guest = derive(current, None, SetGuestMode(ChargeMode.OFF), T_APP)
    assert guest.guest.mode is ChargeMode.OFF
    assert guest.control is True
    assert guest.vehicles["tesla"].mode is ChargeMode.NOW
    surplus = SurplusPriority(kind=SurplusOrder.CARS_FIRST)
    prio = derive(current, None, SetPriority(["egolf", "tesla"], surplus), T_APP)
    assert prio.priority == ["egolf", "tesla"]
    assert prio.surplus_priority == surplus
    modes = derive(current, None, SetActuators({"wallbox.evcs": ActuatorMode.LIVE}), T_APP)
    assert modes.actuators == {"victron": ActuatorMode.LIVE, "wallbox.evcs": ActuatorMode.LIVE}
    off = derive(current, None, SetControl(False), T_APP)
    assert off.control is False
    assert off.actuators == {"victron": ActuatorMode.LIVE}


def test_derive_does_not_change_its_input() -> None:
    current = Intent(version=1, vehicles={"tesla": VehicleIntent(wallbox="evcs")})
    derive(current, health(shown("tesla")), SetMode("tesla", ChargeMode.NOW), T_APP)
    assert current.vehicles["tesla"] == VehicleIntent(wallbox="evcs")


async def test_apply_sends_and_reports(
    service: IntentService, fake_client: FakeCoreClient, feed: FakeFeed
) -> None:
    result = await service.apply(SetControl(True), actor="admin", source="ui")
    assert result.result == "accepted"
    assert result.version == epoch_ms(T_APP)
    assert result.intent.control is True
    assert fake_client.sent == [result.intent]
    assert feed.refreshed == 1
    assert service.cached() == result.intent
    assert await service.current() == result.intent


async def test_clamped_is_success_with_details(
    service: IntentService, fake_client: FakeCoreClient
) -> None:
    body = {"result": "clamped", "version": 7, "details": ["live nicht erlaubt"]}
    fake_client.replies.append(CoreReply(200, body))
    result = await service.apply(SetControl(True), actor="admin", source="ui")
    assert (result.result, result.version, result.details) == ("clamped", 7, ["live nicht erlaubt"])


async def test_conflict_reapplies_the_change_on_the_new_intent(
    service: IntentService, fake_client: FakeCoreClient
) -> None:
    def curl() -> CoreReply:
        # zwischen Holen und Senden hat jemand per curl den Regelbetrieb eingeschaltet
        fake_client.valid = Intent(version=epoch_ms(T_APP) + 500, control=True)
        return CoreReply(409, {"result": "rejected", "version": fake_client.valid.version})

    fake_client.replies.append(curl)
    result = await service.apply(SetMode("tesla", ChargeMode.NOW), actor="admin", source="ui")
    assert len(fake_client.sent) == 2
    second = fake_client.sent[1]
    assert second.control is True
    assert second.vehicles["tesla"].mode is ChargeMode.NOW
    assert second.version == epoch_ms(T_APP) + 500 + 1
    assert result.intent == second


async def test_second_conflict_raises(service: IntentService, fake_client: FakeCoreClient) -> None:
    fake_client.replies += [CoreReply(409, {}), CoreReply(409, {})]
    with pytest.raises(ConflictAgain):
        await service.apply(SetControl(True), actor="admin", source="ui")
    assert len(fake_client.sent) == 2


async def test_changes_are_serialised(service: IntentService, fake_client: FakeCoreClient) -> None:
    first, second = await asyncio.gather(
        service.apply(SetMode("tesla", ChargeMode.NOW), actor="a", source="ui"),
        service.apply(SetMode("egolf", ChargeMode.OFF), actor="b", source="ha"),
    )
    assert len(fake_client.sent) == 2
    assert second.intent.vehicles["tesla"].mode is ChargeMode.NOW  # auf der Absicht der ersten
    assert second.intent.vehicles["egolf"].mode is ChargeMode.OFF
    assert second.version > first.version


async def test_rejection_and_core_down(service: IntentService, fake_client: FakeCoreClient) -> None:
    fake_client.replies.append(CoreReply(422, {"result": "rejected", "details": ["Unsinn."]}))
    with pytest.raises(Rejected) as rejected:
        await service.apply(SetControl(True), actor="admin", source="ui")
    assert rejected.value.details == ["Unsinn."]
    fake_client.error = CoreUnavailableError("weg")
    with pytest.raises(CoreDown):
        await service.apply(SetControl(True), actor="admin", source="ui")
    with pytest.raises(CoreDown):
        await service.current()


async def test_failed_change_is_not_audited(
    service: IntentService, fake_client: FakeCoreClient, audit: AuditLog
) -> None:
    fake_client.replies.append(CoreReply(422, {"details": []}))
    with pytest.raises(Rejected):
        await service.apply(SetControl(True), actor="admin", source="ui")
    assert audit.recent() == []


async def test_unknown_targets_change_nothing(
    service: IntentService, fake_client: FakeCoreClient
) -> None:
    for change, text in [
        (SetMode("zoe", ChargeMode.NOW), "Unbekanntes Fahrzeug „zoe“."),
        (SetBoost("zoe", True), "Unbekanntes Fahrzeug „zoe“."),
        (SetAssignment("evcs", "zoe"), "Unbekanntes Fahrzeug „zoe“."),
        (SetAssignment("nirgends", None), "Unbekannte Wallbox „nirgends“."),
        (SetPriority(["tesla", "zoe"], SurplusPriority()), "Unbekanntes Fahrzeug „zoe“."),
    ]:
        with pytest.raises(UnknownTarget) as unknown:
            await service.apply(change, actor="admin", source="ui")
        assert unknown.value.text == text
    assert fake_client.sent == []


async def test_change_is_audited(service: IntentService, audit: AuditLog) -> None:
    result = await service.apply(SetMode("tesla", ChargeMode.NOW), actor="admin", source="ui")
    entry = audit.recent()[0]
    assert (entry.actor, entry.source) == ("admin", "ui")
    assert (entry.action, entry.target) == ("intent.mode", "tesla")
    assert entry.details == {
        "from": "pv",
        "to": "now",
        "version": result.version,
        "result": "accepted",
    }


async def test_every_change_type_has_its_action_and_target(
    service: IntentService, audit: AuditLog
) -> None:
    changes = [
        SetControl(True),
        SetBoost("tesla", True),
        SetAssignment("evcs", "egolf"),
        SetGuestMode(ChargeMode.OFF),
        SetPriority(["egolf", "tesla"], SurplusPriority(kind=SurplusOrder.CARS_FIRST)),
        SetActuators(
            {"wallbox.evcs": ActuatorMode.LIVE, "victron": ActuatorMode.LIVE},
            frozenset({"wallbox.evcs", "victron"}),
        ),
    ]
    for change in changes:
        await service.apply(change, actor="ha", source="ha")
    entries = [(e.action, e.target, e.details) for e in reversed(audit.recent())]
    assert [(a, t) for a, t, _ in entries] == [
        ("intent.control", "regelbetrieb"),
        ("intent.boost", "tesla"),
        ("intent.assignment", "evcs"),
        ("intent.guest_mode", "gast"),
        ("intent.priority", "vorrang"),
        ("intent.actuators", "victron,wallbox.evcs"),
    ]
    details = [d for _, _, d in entries]
    assert details[0] is not None
    assert (details[0]["from"], details[0]["to"]) == (False, True)
    assert (details[1]["from"], details[1]["to"]) == (
        None,
        (T_APP + timedelta(hours=12)).isoformat(),
    )
    assert (details[2]["from"], details[2]["to"]) == (None, "egolf")
    assert (details[3]["from"], details[3]["to"]) == ("pv", "off")
    assert details[4]["to"]["priority"] == ["egolf", "tesla"]
    assert details[5]["from"] == {"victron": "dry_run", "wallbox.evcs": "dry_run"}
    assert details[5]["to"] == {"victron": "live", "wallbox.evcs": "live"}


async def test_health_of_the_live_state_cleans_the_base(
    service: IntentService, fake_client: FakeCoreClient, live: LiveState
) -> None:
    until = T_APP + timedelta(hours=3)
    fake_client.valid = Intent(version=1, vehicles={"egolf": VehicleIntent(boost_until=until)})
    live.health = health(shown("egolf"))
    result = await service.apply(SetControl(True), actor="admin", source="ui")
    assert result.intent.vehicles["egolf"].boost_until is None


async def test_cleanup_is_logged(
    service: IntentService, fake_client: FakeCoreClient, live: LiveState
) -> None:
    until = T_APP + timedelta(hours=3)
    fake_client.valid = Intent(
        version=1, vehicles={"egolf": VehicleIntent(boost_until=until, wallbox="twc")}
    )
    live.health = health(shown("egolf"))
    with capture_logs() as logs:
        await service.apply(SetControl(True), actor="admin", source="ui")
    removed = [(e["event"], e["vehicle"]) for e in logs if e["event"].endswith("zurückgeschickt")]
    assert removed == [
        ("Boost nicht zurückgeschickt", "egolf"),
        ("Übersteuerung nicht zurückgeschickt", "egolf"),
    ]


async def test_cache_follows_the_fetched_and_the_clamped_intent(
    service: IntentService, fake_client: FakeCoreClient
) -> None:
    fake_client.valid = Intent(version=3, control=True)
    fake_client.replies.append(CoreReply(422, {"details": []}))
    with pytest.raises(Rejected):
        await service.apply(SetMode("tesla", ChargeMode.OFF), actor="admin", source="ui")
    assert service.cached() == Intent(version=3, control=True)  # das Geholte, auch ohne Erfolg

    clamped = Intent(version=9, control=True, actuators={"victron": ActuatorMode.DRY_RUN})

    def core_clamps() -> CoreReply:
        fake_client.valid = clamped
        return CoreReply(200, {"result": "clamped", "version": 9, "details": ["geklemmt"]})

    fake_client.replies.append(core_clamps)
    request = SetActuators({"victron": ActuatorMode.LIVE}, frozenset({"victron"}))
    result = await service.apply(request, actor="admin", source="ui")
    assert result.intent.actuators["victron"] is ActuatorMode.LIVE  # die gesendete
    assert service.cached() == clamped  # der Cache zeigt, was der core geklemmt hat


async def test_live_needs_authorization_against_the_base_of_each_attempt(
    service: IntentService, fake_client: FakeCoreClient
) -> None:
    fake_client.valid = Intent(version=3, actuators={"wallbox.evcs": ActuatorMode.LIVE})
    # schon live: bleibt es ohne Prüfung; Dry-Run braucht nie eine
    keep = SetActuators({"wallbox.evcs": ActuatorMode.LIVE, "victron": ActuatorMode.DRY_RUN})
    await service.apply(keep, actor="admin", source="ui")
    with pytest.raises(LiveNotAuthorized) as refused:
        await service.apply(
            SetActuators({"victron": ActuatorMode.LIVE, "wallbox.evcs": ActuatorMode.LIVE}),
            actor="admin",
            source="ui",
        )
    assert refused.value.actuators == ["victron"]
    granted = SetActuators({"victron": ActuatorMode.LIVE}, frozenset({"victron"}))
    result = await service.apply(granted, actor="admin", source="ui")
    assert result.intent.actuators["victron"] is ActuatorMode.LIVE
    assert len(fake_client.sent) == 2


async def test_operator_change_keeps_the_plan(
    service: IntentService, fake_client: FakeCoreClient
) -> None:
    # Review Focus 1: eine Bedienung baut auf der gültigen Absicht samt Plan auf
    fake_client.valid = Intent(version=1, plan=plan_of())
    result = await service.apply(SetMode("tesla", ChargeMode.NOW), actor="admin", source="ui")
    assert fake_client.sent[-1].plan == plan_of()
    assert result.intent.vehicles["tesla"].mode is ChargeMode.NOW


async def test_set_plan_replaces_the_plan_and_keeps_the_rest(
    service: IntentService, fake_client: FakeCoreClient
) -> None:
    fake_client.valid = Intent(version=1, control=True, plan=plan_of(7))
    result = await service.apply(SetPlan(plan_of(8)), actor="planung", source="system")
    assert result.intent.plan == plan_of(8)
    assert result.intent.control is True
    assert result.version == epoch_ms(T_APP)
    result = await service.apply(SetPlan(None), actor="planung", source="system")
    assert result.intent.plan is None


async def test_plan_change_writes_no_audit_entry(
    service: IntentService, audit: AuditLog, feed: FakeFeed
) -> None:
    await service.apply(SetPlan(plan_of()), actor="planung", source="system")
    assert audit.recent() == []
    assert feed.refreshed == 1  # Health folgt wie nach jeder Änderung


def last_audit(audit: AuditLog) -> tuple[str, str | None, Any]:
    """Aktion, Ziel und neuer Wert (der Kommentar) des jüngsten Eintrags."""
    entry = audit.recent()[0]
    assert entry.details is not None
    return entry.action, entry.target, entry.details["to"]


async def test_set_support_changes_the_intent_and_audits(
    service: IntentService, fake_client: FakeCoreClient, audit: AuditLog, feed: FakeFeed
) -> None:
    result = await service.apply(SetSupport(True, 60.0), actor="anna", source="ui")
    assert fake_client.sent[-1].support == SupportIntent(allowed=True, soc_pct=60.0)
    assert result.intent.support == SupportIntent(allowed=True, soc_pct=60.0)
    assert last_audit(audit) == ("control.support", "Akku-Unterstützung", "erlaubt, ab 60 %")
    entry = audit.recent()[0]
    assert (entry.actor, entry.source) == ("anna", "ui")
    assert entry.details == {
        "from": "erlaubt, Schwelle aus der Konfiguration",
        "to": "erlaubt, ab 60 %",
        "version": result.version,
        "result": "accepted",
    }
    assert feed.refreshed == 1


async def test_support_audit_comment_for_each_state(
    service: IntentService, audit: AuditLog
) -> None:
    comments = []
    for change in (SetSupport(True, None), SetSupport(False, 62.5), SetSupport(False, None)):
        await service.apply(change, actor="ha", source="ha")
        comments.append(last_audit(audit)[2])
    assert comments == [
        "erlaubt, Schwelle aus der Konfiguration",
        "gesperrt",
        "gesperrt",
    ]
    await service.apply(SetSupport(True, 62.5), actor="ha", source="ha")
    assert last_audit(audit)[2] == "erlaubt, ab 62.5 %"


async def test_support_change_keeps_the_plan_and_the_rest(
    service: IntentService, fake_client: FakeCoreClient
) -> None:
    fake_client.valid = Intent(version=1, control=True, plan=plan_of())
    result = await service.apply(SetSupport(False, 80.0), actor="admin", source="ui")
    assert result.intent.plan == plan_of() and result.intent.control is True
    assert result.intent.support == SupportIntent(allowed=False, soc_pct=80.0)


async def test_support_threshold_below_reserve_is_rejected(
    service: IntentService, fake_client: FakeCoreClient, audit: AuditLog
) -> None:
    with pytest.raises(Rejected) as rejected:
        await service.apply(SetSupport(True, 19.0), actor="admin", source="ui")
    assert rejected.value.details == [
        "Unterstützungsschwelle 19 % liegt unter der USV-Reserve (20 %)."
    ]
    assert fake_client.sent == [] and audit.recent() == []


async def test_support_threshold_above_100_is_rejected(
    service: IntentService, fake_client: FakeCoreClient
) -> None:
    with pytest.raises(Rejected) as rejected:
        await service.apply(SetSupport(True, 100.5), actor="admin", source="ui")
    assert rejected.value.details == ["Unterstützungsschwelle 100.5 % liegt über 100 %."]
    assert fake_client.sent == []


async def test_support_threshold_at_the_bounds_is_accepted(service: IntentService) -> None:
    for soc in (20.0, 100.0, None):
        result = await service.apply(SetSupport(True, soc), actor="admin", source="ui")
        assert result.intent.support.soc_pct == soc


async def test_set_target_changes_only_the_given_fields(
    service: IntentService, fake_client: FakeCoreClient
) -> None:
    fake_client.valid = Intent(
        version=1,
        vehicles={
            "tesla": VehicleIntent(mode=ChargeMode.PLANNED, target_soc_pct=70, departure="06:30"),
            "egolf": VehicleIntent(target_soc_pct=90, departure="08:00"),
        },
    )
    result = await service.apply(SetTarget("tesla", 85, None), actor="anna", source="ui")
    tesla = result.intent.vehicles["tesla"]
    assert (tesla.mode, tesla.target_soc_pct, tesla.departure) == (ChargeMode.PLANNED, 85, "06:30")
    result = await service.apply(SetTarget("tesla", None, "07:15"), actor="anna", source="ui")
    tesla = result.intent.vehicles["tesla"]
    assert (tesla.target_soc_pct, tesla.departure) == (85, "07:15")
    egolf = fake_client.sent[-1].vehicles["egolf"]
    assert (egolf.target_soc_pct, egolf.departure) == (90, "08:00")


async def test_set_target_for_a_vehicle_without_entry_starts_from_the_defaults(
    service: IntentService,
) -> None:
    result = await service.apply(SetTarget("egolf", 100, "05:45"), actor="ha", source="ha")
    assert result.intent.vehicles["egolf"] == VehicleIntent(target_soc_pct=100, departure="05:45")


async def test_target_change_is_audited(service: IntentService, audit: AuditLog) -> None:
    result = await service.apply(SetTarget("tesla", 80, "07:00"), actor="anna", source="ui")
    entry = audit.recent()[0]
    assert (entry.actor, entry.source) == ("anna", "ui")
    assert (entry.action, entry.target) == ("intent.target", "tesla")
    assert entry.details == {
        "from": "Ziel 80 %, Abfahrt 07:00",
        "to": "Ziel 80 %, Abfahrt 07:00",
        "version": result.version,
        "result": "accepted",
    }
    await service.apply(SetTarget("tesla", 60, None), actor="anna", source="ui")
    assert last_audit(audit) == ("intent.target", "tesla", "Ziel 60 %, Abfahrt 07:00")
    await service.apply(SetTarget("tesla", None, "06:05"), actor="anna", source="ui")
    assert last_audit(audit) == ("intent.target", "tesla", "Ziel 60 %, Abfahrt 06:05")


async def test_set_target_with_time_replaces_date(service: IntentService) -> None:
    dated = await service.apply(
        SetTarget("tesla", None, "07:15", date(2026, 11, 23)), actor="anna", source="ui"
    )
    assert dated.intent.vehicles["tesla"].departure_on == date(2026, 11, 23)
    daily = await service.apply(SetTarget("tesla", None, "06:00"), actor="anna", source="ui")
    tesla = daily.intent.vehicles["tesla"]
    assert (tesla.departure, tesla.departure_on) == ("06:00", None)


async def test_set_target_without_time_keeps_date(service: IntentService) -> None:
    await service.apply(
        SetTarget("tesla", 70, "07:15", date(2026, 11, 23)), actor="anna", source="ui"
    )
    result = await service.apply(SetTarget("tesla", 90, None), actor="anna", source="ui")
    tesla = result.intent.vehicles["tesla"]
    assert (tesla.target_soc_pct, tesla.departure, tesla.departure_on) == (
        90,
        "07:15",
        date(2026, 11, 23),
    )


async def test_date_without_time_is_rejected(
    service: IntentService, fake_client: FakeCoreClient, audit: AuditLog
) -> None:
    with pytest.raises(Rejected) as rejected:
        await service.apply(
            SetTarget("tesla", 80, None, date(2026, 11, 23)), actor="anna", source="ui"
        )
    assert rejected.value.details == ["Ein Abfahrtstag verlangt eine Abfahrtszeit."]
    assert fake_client.sent == [] and audit.recent() == []


async def test_audit_comment_names_the_date(service: IntentService, audit: AuditLog) -> None:
    await service.apply(
        SetTarget("tesla", 80, "07:15", date(2026, 11, 23)), actor="anna", source="ui"
    )
    assert last_audit(audit) == ("intent.target", "tesla", "Ziel 80 %, Abfahrt Mo 23.11. 07:15")
    await service.apply(
        SetTarget("tesla", None, "07:15", date(2026, 11, 29)), actor="anna", source="ui"
    )
    assert last_audit(audit) == ("intent.target", "tesla", "Ziel 80 %, Abfahrt So 29.11. 07:15")
    await service.apply(SetTarget("tesla", None, "07:15"), actor="anna", source="ui")
    assert last_audit(audit) == ("intent.target", "tesla", "Ziel 80 %, Abfahrt 07:15")


@pytest.mark.parametrize(
    ("change", "text"),
    [
        (SetTarget("tesla", 15, None), "Ziel-SOC „15“ liegt außerhalb von 20–100 %."),
        (SetTarget("tesla", 101, "07:00"), "Ziel-SOC „101“ liegt außerhalb von 20–100 %."),
        (SetTarget("tesla", None, "7:00"), "Abfahrt „7:00“ ist keine Uhrzeit (HH:MM)."),
        (SetTarget("tesla", 80, "24:00"), "Abfahrt „24:00“ ist keine Uhrzeit (HH:MM)."),
        (SetTarget("tesla", None, "07:60"), "Abfahrt „07:60“ ist keine Uhrzeit (HH:MM)."),
        (SetTarget("tesla", None, ""), "Abfahrt „“ ist keine Uhrzeit (HH:MM)."),
    ],
)
async def test_invalid_target_is_rejected_before_sending(
    service: IntentService,
    fake_client: FakeCoreClient,
    audit: AuditLog,
    change: SetTarget,
    text: str,
) -> None:
    with pytest.raises(Rejected) as rejected:
        await service.apply(change, actor="admin", source="ui")
    assert rejected.value.details == [text]
    assert fake_client.sent == [] and audit.recent() == []


async def test_target_bounds_are_accepted(service: IntentService) -> None:
    for soc in (20, 100):
        result = await service.apply(SetTarget("tesla", soc, "00:00"), actor="a", source="ui")
        assert result.intent.vehicles["tesla"].target_soc_pct == soc
    result = await service.apply(SetTarget("tesla", None, "23:59"), actor="a", source="ui")
    assert result.intent.vehicles["tesla"].departure == "23:59"


async def test_target_of_an_unknown_vehicle_is_unknown(
    service: IntentService, fake_client: FakeCoreClient
) -> None:
    with pytest.raises(UnknownTarget) as unknown:
        await service.apply(SetTarget("zoe", 80, None), actor="admin", source="ui")
    assert unknown.value.text == "Unbekanntes Fahrzeug „zoe“."
    assert fake_client.sent == []
