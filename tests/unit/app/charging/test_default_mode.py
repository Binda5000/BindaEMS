"""Standard-Lademodus nach dem Abstecken (``DefaultModes``) mit einer gefälschten Absicht."""

from collections.abc import Callable
from datetime import timedelta

import pytest
from structlog.testing import capture_logs
from tests.app_helpers import T_APP

from bindaems.app.charging.default_mode import ACTOR, DefaultModes
from bindaems.app.charging.sessions import GUEST
from bindaems.app.charging.tracker import Unplugged, WallboxSeen
from bindaems.app.control.intent import (
    ChangeResult,
    ConflictAgain,
    CoreDown,
    PreconditionFailed,
    Rejected,
    SetGuestMode,
    SetMode,
)
from bindaems.app.settings.service import SettingsService
from bindaems.shared.config import Config
from bindaems.shared.control import ChargeMode, GuestIntent, Intent, VehicleIntent


class FakeIntents:
    """Stand-in für ``IntentService``: die gültige Absicht des core, der Cache und die Aufrufe.

    ``errors`` wirft ``apply`` der Reihe nach, ``down`` lässt ``current`` scheitern;
    ``meanwhile`` läuft in ``apply`` vor der Vorbedingung (eine Bedienung kurz davor).
    """

    def __init__(self, intent: Intent | None) -> None:
        self.intent = intent
        self._cached = intent
        self.applied: list[tuple[object, str, str]] = []
        self.errors: list[Exception] = []
        self.down = False
        self.reads = 0
        self.meanwhile: Callable[[], None] | None = None

    def cached(self) -> Intent | None:
        return self._cached

    async def current(self) -> Intent | None:
        self.reads += 1
        if self.down:
            raise CoreDown
        self._cached = self.intent
        return self.intent

    def operate(self, change: SetMode | SetGuestMode) -> None:
        """Eine angenommene Bedienung: neue Absicht beim core und im Cache."""
        base = self.intent or Intent(version=1)
        if isinstance(change, SetMode):
            vehicle = base.vehicles.get(change.vehicle, VehicleIntent())
            vehicles = base.vehicles | {
                change.vehicle: vehicle.model_copy(update={"mode": change.mode})
            }
            new = base.model_copy(update={"vehicles": vehicles, "version": base.version + 1})
        else:
            guest = base.guest.model_copy(update={"mode": change.mode})
            new = base.model_copy(update={"guest": guest, "version": base.version + 1})
        self.intent = self._cached = new

    async def apply(self, change, *, actor, source, precondition=None) -> ChangeResult:
        if self.meanwhile is not None:
            self.meanwhile()
        if precondition is not None and not precondition():
            raise PreconditionFailed
        if self.errors:
            raise self.errors.pop(0)
        self.applied.append((change, actor, source))
        self.operate(change)
        assert self.intent is not None
        return ChangeResult("accepted", self.intent.version, [], self.intent)


def tesla_in(mode: ChargeMode) -> Intent:
    return Intent(version=3, vehicles={"tesla": VehicleIntent(mode=mode)})


def set_defaults(settings: SettingsService, defaults: dict[str, ChargeMode]) -> None:
    current = settings.current()
    changed = current.settings.model_copy(update={"wallbox_default_modes": defaults})
    settings.update(changed, base_version=current.version, actor="test", source="ui")


def unplugged(wallbox: str, vehicle: str | None) -> list[Unplugged]:
    return [Unplugged(wallbox, vehicle, T_APP)]


def free(wallbox: str) -> list[WallboxSeen]:
    return [WallboxSeen(wallbox, False, None, None)]


@pytest.fixture
def service(settings_service: SettingsService, cfg: Config) -> Callable[..., DefaultModes]:
    def build(intents: FakeIntents) -> DefaultModes:
        return DefaultModes(intents, settings_service, cfg)  # type: ignore[arg-type]

    return build


async def test_unplug_sets_wallbox_default_mode(service, settings_service) -> None:
    intents = FakeIntents(tesla_in(ChargeMode.NOW))
    set_defaults(settings_service, {"twc": ChargeMode.PV})
    with capture_logs() as logs:
        await service(intents).observed(unplugged("twc", "tesla"), free("twc"))
    assert intents.applied == [(SetMode("tesla", ChargeMode.PV), "Standard-Lademodus", "system")]
    assert ACTOR == "Standard-Lademodus"
    [entry] = [e for e in logs if e["event"] == "Standard-Lademodus gesetzt"]
    assert (entry["log_level"], entry["wallbox"], entry["vehicle"], entry["mode"]) == (
        "info",
        "twc",
        "tesla",
        "pv",
    )


async def test_no_default_means_no_change(service, settings_service) -> None:
    intents = FakeIntents(tesla_in(ChargeMode.NOW))
    set_defaults(settings_service, {"evcs": ChargeMode.PV})  # nur die andere Ladestation
    modes = service(intents)
    await modes.observed(unplugged("twc", "tesla"), free("twc"))
    await modes.observed([], free("twc"))
    assert (intents.applied, intents.reads) == ([], 0)  # nicht einmal gelesen


async def test_same_mode_sends_nothing(service, settings_service) -> None:
    intents = FakeIntents(tesla_in(ChargeMode.PV))
    set_defaults(settings_service, {"twc": ChargeMode.PV})
    modes = service(intents)
    await modes.observed(unplugged("twc", "tesla"), free("twc"))
    assert (intents.applied, intents.reads) == ([], 1)
    await modes.observed([], free("twc"))  # erledigt: kein weiterer Aufruf beim core
    assert intents.reads == 1


async def test_guest_unplug_sets_guest_mode(service, settings_service) -> None:
    intents = FakeIntents(Intent(version=2, guest=GuestIntent(mode=ChargeMode.PV)))
    set_defaults(settings_service, {"evcs": ChargeMode.NOW})
    await service(intents).observed(unplugged("evcs", GUEST), free("evcs"))
    assert intents.applied == [(SetGuestMode(ChargeMode.NOW), ACTOR, "system")]


async def test_planned_default_leaves_guest_unchanged(service, settings_service) -> None:
    intents = FakeIntents(Intent(version=2))
    # „Geplant“ gibt es für Gäste nicht
    set_defaults(settings_service, {"evcs": ChargeMode.PLANNED})
    modes = service(intents)
    await modes.observed(unplugged("evcs", GUEST), free("evcs"))
    await modes.observed([], free("evcs"))
    assert (intents.applied, intents.reads) == ([], 0)


@pytest.mark.parametrize("vehicle", [None, "fremd"])
async def test_unknown_or_foreign_vehicle_is_ignored(service, settings_service, vehicle) -> None:
    intents = FakeIntents(tesla_in(ChargeMode.NOW))
    set_defaults(settings_service, {"twc": ChargeMode.PV})
    modes = service(intents)
    await modes.observed(unplugged("twc", vehicle), free("twc"))
    await modes.observed([], free("twc"))
    assert (intents.applied, intents.reads) == ([], 0)


@pytest.mark.parametrize("failure", ["read", "core_down", "conflict"])
async def test_core_down_retries_on_next_observation(service, settings_service, failure) -> None:
    intents = FakeIntents(tesla_in(ChargeMode.NOW))
    set_defaults(settings_service, {"twc": ChargeMode.PV})
    if failure == "read":
        intents.down = True
    else:
        intents.errors = [CoreDown() if failure == "core_down" else ConflictAgain()]
    modes = service(intents)
    await modes.observed(unplugged("twc", "tesla"), free("twc"))
    assert intents.applied == []
    intents.down = False
    await modes.observed([], free("twc"))  # der nächste Takt von Health holt es nach
    assert intents.applied == [(SetMode("tesla", ChargeMode.PV), ACTOR, "system")]
    reads = intents.reads
    await modes.observed([], free("twc"))
    assert (len(intents.applied), intents.reads) == (1, reads)  # erledigt


async def test_replugged_vehicle_drops_pending_change(service, settings_service) -> None:
    intents = FakeIntents(tesla_in(ChargeMode.NOW))
    set_defaults(settings_service, {"twc": ChargeMode.PV})
    intents.down = True
    modes = service(intents)
    await modes.observed(unplugged("twc", "tesla"), free("twc"))
    intents.down = False
    # wieder angesteckt, an der anderen Ladestation: der Wunsch ist hinfällig
    seen = [*free("twc"), WallboxSeen("evcs", True, "tesla", "auto")]
    await modes.observed([], seen)
    await modes.observed([], free("twc"))
    assert intents.applied == []
    assert intents.reads == 1  # nur der gescheiterte Versuch


async def test_unknown_plug_state_keeps_pending_change(service, settings_service) -> None:
    intents = FakeIntents(tesla_in(ChargeMode.NOW))
    set_defaults(settings_service, {"twc": ChargeMode.PV})
    intents.down = True
    modes = service(intents)
    await modes.observed(unplugged("twc", "tesla"), free("twc"))
    intents.down = False
    await modes.observed([], [WallboxSeen("twc", None, "tesla", "auto")])  # nicht „verbunden“
    assert intents.applied == [(SetMode("tesla", ChargeMode.PV), ACTOR, "system")]


async def test_manual_mode_after_unplug_wins_over_retry(service, settings_service) -> None:
    intents = FakeIntents(tesla_in(ChargeMode.NOW))
    set_defaults(settings_service, {"twc": ChargeMode.PV})
    intents.down = True
    modes = service(intents)
    await modes.observed(unplugged("twc", "tesla"), free("twc"))
    intents.down = False
    intents.operate(SetMode("tesla", ChargeMode.OFF))  # jemand bedient nach dem Abstecken
    await modes.observed([], free("twc"))
    await modes.observed([], free("twc"))
    assert intents.applied == []
    assert intents.intent == tesla_in(ChargeMode.OFF).model_copy(update={"version": 4})
    assert intents.reads == 2  # ein Versuch beim Nachholen, dann erledigt


async def test_operation_between_read_and_send_wins(service, settings_service) -> None:
    intents = FakeIntents(tesla_in(ChargeMode.NOW))
    set_defaults(settings_service, {"twc": ChargeMode.PV})
    intents.meanwhile = lambda: intents.operate(SetMode("tesla", ChargeMode.MIN_PV))
    modes = service(intents)
    await modes.observed(unplugged("twc", "tesla"), free("twc"))
    intents.meanwhile = None
    await modes.observed([], free("twc"))
    assert intents.applied == []
    assert intents.reads == 1  # die Bedienung geht vor: erledigt
    assert intents.intent is not None and intents.intent.vehicles["tesla"].mode == ChargeMode.MIN_PV


async def test_rejected_change_is_logged_not_retried(service, settings_service) -> None:
    intents = FakeIntents(tesla_in(ChargeMode.NOW))
    set_defaults(settings_service, {"twc": ChargeMode.PV})
    intents.errors = [Rejected(["Modus unzulässig."])]
    modes = service(intents)
    with capture_logs() as logs:
        await modes.observed(unplugged("twc", "tesla"), free("twc"))
    [entry] = [e for e in logs if e["event"] == "Standard-Lademodus abgelehnt"]
    assert (entry["log_level"], entry["details"]) == ("warning", ["Modus unzulässig."])
    await modes.observed([], free("twc"))
    assert (intents.applied, intents.reads) == ([], 1)


async def test_new_unplug_replaces_pending_change(service, settings_service) -> None:
    intents = FakeIntents(tesla_in(ChargeMode.NOW))
    set_defaults(settings_service, {"twc": ChargeMode.PV, "evcs": ChargeMode.MIN_PV})
    intents.down = True
    modes = service(intents)
    await modes.observed(unplugged("twc", "tesla"), free("twc"))
    # der Steckzustand dazwischen war unbekannt
    await modes.observed(unplugged("evcs", "tesla"), free("evcs"))
    intents.down = False
    await modes.observed([], free("twc"))
    assert intents.applied == [(SetMode("tesla", ChargeMode.MIN_PV), ACTOR, "system")]


async def test_settings_read_at_unplug_time(service, settings_service) -> None:
    intents = FakeIntents(tesla_in(ChargeMode.NOW))
    modes = service(intents)
    set_defaults(settings_service, {"twc": ChargeMode.PV})
    await modes.observed(unplugged("twc", "tesla"), free("twc"))
    intents.operate(SetMode("tesla", ChargeMode.NOW))
    set_defaults(settings_service, {"twc": ChargeMode.MIN_PV})
    later = [Unplugged("twc", "tesla", T_APP + timedelta(hours=2))]
    await modes.observed(later, free("twc"))
    assert [change for change, _, _ in intents.applied] == [
        SetMode("tesla", ChargeMode.PV),
        SetMode("tesla", ChargeMode.MIN_PV),
    ]


async def test_failed_attempt_takes_read_mode_as_baseline(service, settings_service) -> None:
    intents = FakeIntents(tesla_in(ChargeMode.OFF))  # der core steht schon auf „Aus“
    intents._cached = tesla_in(ChargeMode.NOW)  # der Cache beim Abstecken ist veraltet
    set_defaults(settings_service, {"twc": ChargeMode.PV})
    intents.errors = [CoreDown()]
    modes = service(intents)
    await modes.observed(unplugged("twc", "tesla"), free("twc"))
    assert intents.applied == []
    await modes.observed([], free("twc"))  # „Aus“ war gelesen, nicht bedient: nachholen
    assert intents.applied == [(SetMode("tesla", ChargeMode.PV), ACTOR, "system")]


async def test_baseline_after_empty_cache_protects_manual_change(service, settings_service) -> None:
    intents = FakeIntents(tesla_in(ChargeMode.NOW))
    intents._cached = None  # nach dem Start noch keine Absicht im Cache
    set_defaults(settings_service, {"twc": ChargeMode.PV})
    intents.errors = [ConflictAgain()]
    modes = service(intents)
    await modes.observed(unplugged("twc", "tesla"), free("twc"))
    intents.operate(SetMode("tesla", ChargeMode.OFF))  # jemand bedient vor dem Nachholen
    await modes.observed([], free("twc"))
    await modes.observed([], free("twc"))
    assert intents.applied == []
    assert intents.intent is not None and intents.intent.vehicles["tesla"].mode == ChargeMode.OFF


async def test_guest_wish_dropped_while_a_guest_is_plugged(service, settings_service) -> None:
    # Der Gast-Lademodus ist gemeinsam: steckt irgendwo ein Gast, gilt der Wunsch nicht mehr
    intents = FakeIntents(Intent(version=2, guest=GuestIntent(mode=ChargeMode.NOW)))
    set_defaults(settings_service, {"evcs": ChargeMode.PV})
    intents.down = True
    modes = service(intents)
    await modes.observed(unplugged("evcs", GUEST), free("evcs"))
    intents.down = False
    seen = [*free("evcs"), WallboxSeen("twc", True, GUEST, "default")]
    await modes.observed([], seen)
    await modes.observed([], free("evcs"))
    assert intents.applied == []
    assert intents.reads == 1  # nur der gescheiterte Versuch


async def test_replugged_guest_drops_pending_change(service, settings_service) -> None:
    intents = FakeIntents(Intent(version=2, guest=GuestIntent(mode=ChargeMode.NOW)))
    set_defaults(settings_service, {"evcs": ChargeMode.PV})
    intents.down = True
    modes = service(intents)
    await modes.observed(unplugged("evcs", GUEST), free("evcs"))
    intents.down = False
    await modes.observed([], [WallboxSeen("evcs", True, GUEST, "default")])  # wieder angesteckt
    await modes.observed([], free("evcs"))
    assert (intents.applied, intents.reads) == ([], 1)


async def test_unexpected_error_is_logged_once_and_dropped(service, settings_service) -> None:
    intents = FakeIntents(
        Intent(
            version=3,
            vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW)},
            guest=GuestIntent(mode=ChargeMode.NOW),
        )
    )
    set_defaults(settings_service, {"twc": ChargeMode.PV, "evcs": ChargeMode.MIN_PV})
    intents.errors = [RuntimeError("kaputt")]
    modes = service(intents)
    reports = [*unplugged("twc", "tesla"), *unplugged("evcs", GUEST)]
    with capture_logs() as logs:
        await modes.observed(reports, [*free("twc"), *free("evcs")])
        await modes.observed([], [*free("twc"), *free("evcs")])
    [entry] = [e for e in logs if e["event"] == "Standard-Lademodus fehlgeschlagen"]
    assert (entry["log_level"], entry["vehicle"], entry["exc_info"]) == ("error", "tesla", True)
    assert intents.applied == [(SetGuestMode(ChargeMode.MIN_PV), ACTOR, "system")]
    assert intents.reads == 1  # danach ist nichts mehr offen
