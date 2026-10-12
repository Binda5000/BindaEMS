import logging
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bindaems_watchdog.actions import ActionRunner
from bindaems_watchdog.config import Action, Settings
from bindaems_watchdog.logic import WatchdogLogic
from bindaems_watchdog.service import DbusItemAccess, WatchdogService, main, register_paths
from bindaems_watchdog.state_file import StoredState, read_state, write_state
from tests.unit.cerbo_watchdog.fakes import DbusArray, FakeAccess

SETTINGS = Settings(
    enabled=True,
    timeout_s=90.0,
    rearm_s=30.0,
    gap_s=25.0,
    retries=0,
    retry_s=5.0,
    state_file="state.json",
    actions=(
        Action(
            "min_soc",
            "com.victronenergy.settings",
            "/Settings/CGwacs/BatteryLife/MinimumSocLimit",
            20,
        ),
    ),
)


class Env:
    def __init__(self, stored: StoredState | None = None) -> None:
        stored = stored or StoredState()
        self.now = 0.0
        self.published: list[tuple[str, object]] = []
        self.saved: list[StoredState] = []
        access = FakeAccess(
            {("com.victronenergy.settings", "/Settings/CGwacs/BatteryLife/MinimumSocLimit"): 30}
        )
        self.service = WatchdogService(
            SETTINGS,
            WatchdogLogic(SETTINGS, stored.armed, 0.0),
            ActionRunner(access, SETTINGS.actions, SETTINGS.retries, SETTINGS.retry_s),
            stored,
            publish=lambda p, v: self.published.append((p, v)),
            save=self.saved.append,
            wall=lambda: 1791600000.0,
            monotonic=lambda: self.now,
        )

    def at(self, t: float, beat: bool = False) -> None:
        self.now = t
        if beat:
            self.service.on_heartbeat("/Heartbeat", 1)
        self.service.tick()


def test_heartbeat_callback_accepts_int_only() -> None:
    service = Env().service
    assert service.on_heartbeat("/Heartbeat", 5)
    assert not any(service.on_heartbeat("/Heartbeat", v) for v in ("x", True, -1, 1.5))


def test_trip_counts_runs_actions_and_publishes() -> None:
    env = Env()
    for t in (0, 10, 20, 30):
        env.at(t, beat=True)
    env.at(121)
    assert ("/State", 2) in env.published and ("/TripCount", 1) in env.published
    assert ("/LastTripTime", 1791600000) in env.published and (
        "/LastTripFailures",
        0,
    ) in env.published
    # ausgelöst gilt in der Zustandsdatei als scharf: Es übersteht einen Neustart des Cerbo
    assert env.saved[-1] == StoredState(armed=True, trip_count=1, last_trip_time=1791600000)


def test_state_saved_on_arm_and_release() -> None:
    env = Env()
    for t in (0, 10, 20, 30):
        env.at(t, beat=True)
    assert env.saved[-1].armed is True
    env.service.on_heartbeat("/Heartbeat", 0)
    env.at(31)
    assert env.saved[-1].armed is False and env.published[-1] == ("/State", 0)


class FakeDbusService:
    def __init__(self) -> None:
        self.paths: list[tuple[str, object, bool]] = []

    def add_path(
        self,
        path: str,
        value: object,
        writeable: bool = False,
        onchangecallback: Any = None,
        **_: Any,
    ) -> None:
        self.paths.append((path, value, writeable))


def test_register_paths_only_heartbeat_writeable() -> None:
    dbus_service = FakeDbusService()
    register_paths(dbus_service, Env().service)
    assert dbus_service.paths == [
        ("/Heartbeat", None, True),
        ("/State", 0, False),
        ("/TripCount", 0, False),
        ("/LastTripTime", 0, False),
        ("/LastTripFailures", 0, False),
        ("/TimeoutSeconds", 90, False),
        ("/Version", "1.1.0", False),
    ]


def test_initial_values_from_stored_state() -> None:
    values = Env(StoredState(armed=True, trip_count=3, last_trip_time=5)).service.values()
    assert (values["/State"], values["/TripCount"], values["/LastTripTime"]) == (1, 3, 5)


# Weitere Randfälle

MIN_SOC_ITEM = ("com.victronenergy.settings", "/Settings/CGwacs/BatteryLife/MinimumSocLimit")
EVCS = Action("evcs_evcs", "com.victronenergy.evcharger", "/SetCurrent", 6, instance=40)


class Rig:
    """Wie ``Env``, dazu wählbare Einstellungen und Fehler beim Veröffentlichen und Speichern."""

    def __init__(
        self,
        settings: Settings = SETTINGS,
        stored: StoredState | None = None,
        fail_publish: bool = False,
        fail_save: bool = False,
        access: FakeAccess | None = None,
    ) -> None:
        stored = stored or StoredState()
        self.access = access or FakeAccess({MIN_SOC_ITEM: 30})
        self.now = 0.0
        self.clock_broken = False
        self.fail_publish, self.fail_save = fail_publish, fail_save
        self.published: list[tuple[str, object]] = []
        self.saved: list[StoredState] = []
        runner = ActionRunner(self.access, settings.actions, settings.retries, settings.retry_s)
        self.service = WatchdogService(
            settings,
            WatchdogLogic(settings, stored.armed, 0.0),
            runner,
            stored,
            publish=self._publish,
            save=self._save,
            wall=lambda: 1791600000.0,
            monotonic=self._clock,
        )

    def _publish(self, path: str, value: object) -> None:
        if self.fail_publish:
            raise RuntimeError("D-Bus nicht erreichbar")
        self.published.append((path, value))

    def _save(self, state: StoredState) -> None:
        if self.fail_save:
            raise OSError(28, "No space left on device")
        self.saved.append(state)

    def _clock(self) -> float:
        if self.clock_broken:
            raise ValueError("Uhr kaputt")
        return self.now

    def at(self, t: float, beat: object = None) -> bool:
        self.now = t
        if beat is not None:
            self.service.on_heartbeat("/Heartbeat", beat)
        return self.service.tick()

    def arm(self) -> None:
        for t in (0, 10, 20, 30):
            self.at(t, beat=1)


class RecordingDbusService:
    """Merkt sich je Pfad Wert, Schreibbarkeit und Callback wie ``VeDbusService.add_path``."""

    def __init__(self) -> None:
        self.paths: dict[str, tuple[object, bool, Any]] = {}

    def add_path(
        self, path: str, value: object, writeable: bool = False, onchangecallback: Any = None
    ) -> None:
        self.paths[path] = (value, writeable, onchangecallback)


def test_heartbeat_path_feeds_the_watchdog() -> None:
    env = Env()
    dbus_service = RecordingDbusService()
    register_paths(dbus_service, env.service)
    callback = dbus_service.paths["/Heartbeat"][2]
    assert [path for path, (_, _, cb) in dbus_service.paths.items() if cb] == ["/Heartbeat"]
    for t in (0, 10, 20, 30):
        env.now = t
        assert callback("/Heartbeat", t + 1) is True
        env.service.tick()
    assert env.published[-1] == ("/State", 1)
    assert callback("/Heartbeat", "x") is False


def test_tick_survives_a_failing_save(caplog: pytest.LogCaptureFixture) -> None:
    rig = Rig(fail_save=True)
    for t in (0, 10, 20, 30):
        assert rig.at(t, beat=1) is True
    assert rig.published[-1] == ("/State", 1)
    assert rig.at(121) is True
    assert ("/State", 2) in rig.published and ("/LastTripFailures", 0) in rig.published
    assert "Zustandsdatei nicht geschrieben: [Errno 28] No space left on device" in caplog.text


def test_tick_survives_a_failing_publish() -> None:
    rig = Rig(fail_publish=True)
    rig.arm()
    assert rig.at(121) is True
    assert rig.saved[-1] == StoredState(armed=True, trip_count=1, last_trip_time=1791600000)
    values = rig.service.values()
    assert (values["/State"], values["/TripCount"], values["/LastTripFailures"]) == (2, 1, 0)


def test_tripped_is_stored_as_armed() -> None:
    rig = Rig()
    rig.arm()
    rig.at(121)  # ohne Heartbeat: Auslösung
    assert rig.service.values()["/State"] == 2
    assert rig.saved[-1].armed is True


def test_state_is_stored_as_armed_from_arming_to_the_release() -> None:
    rig = Rig()
    rig.arm()
    rig.at(121)  # ausgelöst
    for t in (130, 140, 150, 160):  # der Heartbeat kommt zurück: wieder scharf
        rig.at(t, beat=1)
    assert rig.service.values()["/State"] == 1
    assert [state.armed for state in rig.saved] == [True, True, True]  # scharf, ausgelöst, scharf
    rig.at(161, beat=0)  # erst die Freigabe mit 0 macht ihn wartend
    assert rig.saved[-1] == StoredState(armed=False, trip_count=1, last_trip_time=1791600000)


def test_a_tripped_watchdog_restarts_armed_and_trips_again(tmp_path: Path) -> None:
    # Der Cerbo startet neu, während der Watchdog ausgelöst ist. Die Zustandsdatei hält ihn für
    # scharf, und bleibt der Heartbeat aus, löst er nach dem Timeout erneut aus. Die Aktion liest
    # zuerst und schreibt nichts, was schon stimmt.
    access = FakeAccess({MIN_SOC_ITEM: 30})
    first = Rig(access=access)
    first.arm()
    first.at(121)
    assert access.writes == [(*MIN_SOC_ITEM, 20)]
    state_file = str(tmp_path / "state.json")
    write_state(state_file, first.saved[-1])
    stored, problem = read_state(state_file)  # wie beim Start des Dienstes
    assert problem is None
    restarted = Rig(stored=stored, access=access)
    assert restarted.service.values()["/State"] == 1  # scharf, nicht wartend
    restarted.at(60)
    assert ("/TripCount", 2) not in restarted.published  # das EMS hat noch bis zum Timeout Zeit
    restarted.at(91)  # weiter kein Heartbeat
    assert ("/State", 2) in restarted.published and ("/TripCount", 2) in restarted.published
    assert restarted.service.values()["/LastTripFailures"] == 0
    assert access.writes == [(*MIN_SOC_ITEM, 20)]  # kein zweites Schreiben
    assert restarted.saved[-1] == StoredState(armed=True, trip_count=2, last_trip_time=1791600000)


def test_tick_survives_an_unexpected_error(caplog: pytest.LogCaptureFixture) -> None:
    rig = Rig()
    rig.arm()
    rig.clock_broken = True
    assert rig.at(60) is True
    assert "Fehler im Takt; der Watchdog läuft weiter." in caplog.text
    rig.clock_broken = False
    rig.at(121)
    assert ("/TripCount", 1) in rig.published


def test_failed_save_is_retried_every_tick_until_it_works() -> None:
    rig = Rig(fail_save=True)
    rig.arm()  # scharf bei 30, die Zustandsdatei lässt sich nicht schreiben
    rig.at(31)
    assert rig.saved == []
    rig.fail_save = False
    rig.at(32)
    assert rig.saved == [StoredState(armed=True, trip_count=0, last_trip_time=0)]
    rig.at(33)
    assert len(rig.saved) == 1


def test_save_retries_log_once_and_keep_the_newest_state(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    rig = Rig(fail_save=True)
    rig.arm()
    for t in range(31, 40):
        rig.at(t, beat=1)
    rig.at(40, beat=0)  # Freigabe, solange die Datei noch nicht geschrieben ist
    rig.fail_save = False
    rig.at(41)
    assert rig.saved == [StoredState(armed=False, trip_count=0, last_trip_time=0)]
    assert caplog.text.count("Zustandsdatei nicht geschrieben") == 1
    assert "Zustandsdatei wieder geschrieben." in caplog.text


def test_release_ends_the_run_without_further_writes(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    access = FakeAccess({MIN_SOC_ITEM: 30}, failing_sets=1000)
    rig = Rig(
        settings=replace(SETTINGS, retries=6, actions=(EVCS, *SETTINGS.actions)), access=access
    )
    rig.arm()
    rig.at(121)  # Auslösung; beide Aktionen scheitern und wären in 5 s wieder dran
    writes = list(access.writes)
    rig.at(122, beat=0)
    for t in range(123, 200):
        rig.at(t)
    assert access.writes == writes
    # Beide sind schon einmal gescheitert: Mit dem Abbruch zählen sie als fehlgeschlagen.
    assert [value for path, value in rig.published if path == "/LastTripFailures"] == [0, 2]
    records = [(r.levelname, r.getMessage()) for r in caplog.records]
    assert (
        "WARNING",
        "Aktionen abgebrochen (Zustand wartet): evcs_evcs fehlgeschlagen, min_soc fehlgeschlagen",
    ) in records


def test_rearming_in_the_tick_of_the_last_attempt_counts_the_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Wie am Cerbo: rearm_s (30 s) = retries × retry_s (6 × 5 s). Der Heartbeat kommt gleich nach
    # der Auslösung zurück, und der Watchdog wird in dem Takt wieder scharf, in dem der letzte
    # Versuch für die EVCS fällig ist. Der Takt prüft „wieder scharf“ vor den Aktionen: Der Lauf
    # bricht ab, ohne dass dieser Versuch stattfindet.
    caplog.set_level(logging.INFO)
    rig = Rig(settings=replace(SETTINGS, retries=6, actions=(EVCS, *SETTINGS.actions)))
    rig.arm()
    rig.at(121)  # Auslösung: Die EVCS fehlt (keine Instanz 40), Min-SOC gelingt.
    rig.service.on_heartbeat("/Heartbeat", 1)  # noch in derselben Sekunde
    for t in range(122, 152):
        rig.at(t, beat=1 if t in (131, 141, 151) else None)
    assert rig.service.values()["/State"] == 1
    assert [value for path, value in rig.published if path == "/LastTripFailures"] == [0, 1]
    records = [(r.levelname, r.getMessage()) for r in caplog.records]
    assert ("ERROR", "Aktion evcs_evcs fehlgeschlagen.") in records
    assert (
        "WARNING",
        "Aktionen abgebrochen (Zustand scharf): evcs_evcs fehlgeschlagen, min_soc ok",
    ) in records


def test_rearming_ends_the_run_without_further_writes() -> None:
    access = FakeAccess({MIN_SOC_ITEM: 30}, failing_sets=1000)
    rig = Rig(settings=replace(SETTINGS, retries=100), access=access)
    rig.arm()
    rig.at(121)
    for t in (122, 132, 142, 152):  # scharf bei 152, der Lauf versucht es bis dahin weiter
        rig.at(t, beat=1)
    assert rig.service.values()["/State"] == 1
    writes = len(access.writes)
    for t in range(153, 240):
        rig.at(t, beat=1)
    assert len(access.writes) == writes


def test_a_new_trip_resets_the_failures_at_once() -> None:
    rig = Rig(settings=replace(SETTINGS, retries=1, actions=(EVCS,)))
    rig.arm()
    rig.at(121)
    rig.at(126)  # der zweite Versuch scheitert: fehlgeschlagen
    assert rig.service.values()["/LastTripFailures"] == 1
    for t in (130, 140, 150, 160):
        rig.at(t, beat=1)
    rig.published.clear()
    rig.at(251)  # zweite Auslösung; der Lauf ist noch offen
    assert ("/LastTripFailures", 0) in rig.published
    assert rig.service.values()["/LastTripFailures"] == 0


def test_heartbeat_keeps_the_last_accepted_value() -> None:
    service = Env().service
    assert service.values()["/Heartbeat"] is None
    assert service.on_heartbeat("/Heartbeat", 7)
    assert not any(service.on_heartbeat("/Heartbeat", v) for v in (None, [], 2.0, "7"))
    assert service.values()["/Heartbeat"] == 7


def test_rejected_heartbeats_do_not_keep_it_armed() -> None:
    rig = Rig()
    rig.arm()
    for t in range(40, 140, 10):
        rig.at(t, beat=1.5)
    assert ("/TripCount", 1) in rig.published


def test_first_release_after_a_restart_armed_is_accepted() -> None:
    rig = Rig(stored=StoredState(armed=True, trip_count=2, last_trip_time=5))
    assert rig.service.values()["/Heartbeat"] is None  # die 0 ist für velib eine Änderung
    rig.at(1, beat=0)
    assert rig.published == [("/State", 0)]
    assert rig.saved == [StoredState(armed=False, trip_count=2, last_trip_time=5)]
    rig.at(200)
    assert rig.published == [("/State", 0)]


def test_failures_are_published_when_the_actions_end() -> None:
    rig = Rig(settings=replace(SETTINGS, retries=2, actions=(EVCS,)))
    rig.arm()
    rig.at(121)
    failures = [value for path, value in rig.published if path == "/LastTripFailures"]
    assert failures == [0]  # nur der Neubeginn; das Ergebnis folgt am Ende des Laufs
    rig.at(126)
    rig.at(131)
    assert rig.published[-1] == ("/LastTripFailures", 1)
    assert rig.service.values()["/LastTripFailures"] == 1


def test_disabled_watchdog_stays_quiet() -> None:
    rig = Rig(settings=replace(SETTINGS, enabled=False), stored=StoredState(armed=True))
    assert rig.service.values()["/State"] == 3
    for t in range(0, 300, 10):
        rig.at(t, beat=1 if t < 50 else None)
    assert (rig.published, rig.saved) == ([], [])


def test_states_trip_and_failed_actions_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    rig = Rig(settings=replace(SETTINGS, actions=(EVCS, *SETTINGS.actions)))
    rig.arm()
    rig.at(121)
    records = [(r.levelname, r.getMessage()) for r in caplog.records]
    assert ("INFO", "Zustand: wartet → scharf") in records
    assert ("INFO", "Zustand: scharf → ausgelöst") in records
    assert (
        "WARNING",
        "Ausgelöst: seit über 90 s kein Heartbeat (Auslösung 1). Die Aktionen laufen.",
    ) in records
    assert ("ERROR", "Aktion evcs_evcs fehlgeschlagen.") in records
    assert ("INFO", "Aktionen abgeschlossen: evcs_evcs fehlgeschlagen, min_soc ok") in records


def test_main_needs_the_config_path(caplog: pytest.LogCaptureFixture) -> None:
    sleeps: list[float] = []
    assert main([], sleep=sleeps.append) == 2
    assert "Aufruf: bindaems-watchdog.py <config.ini>" in caplog.text
    assert sleeps == []  # ein falscher Aufruf endet sofort


def test_main_waits_60_s_after_a_config_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "dbus", None)  # jeder Zugriff auf D-Bus wirft ImportError
    config = tmp_path / "config.ini"
    config.write_text("[watchdog]\ntimeout_s = 0\n", encoding="utf-8")
    sleeps: list[float] = []
    assert main([str(config)], sleep=sleeps.append) == 2
    assert sleeps == [60]
    assert "config.ini: timeout_s muss eine positive Zahl sein." in caplog.text
    assert "Der Watchdog läuft nicht; nächster Versuch in 60 s." in caplog.text


class FakeBus:
    """Ersatz für eine Verbindung von dbus-python: Werte je (Dienst, Pfad)."""

    def __init__(self, items: dict[tuple[str, str], object]) -> None:
        self.items = dict(items)
        self.set_result = 0
        self.calls: list[tuple[str, dict[str, object]]] = []

    def list_names(self) -> list[str]:
        return [":1.7", "org.freedesktop.DBus", *sorted({service for service, _ in self.items})]

    def get_object(self, service: str, path: str, introspect: bool = True) -> "FakeBusItem":
        return FakeBusItem(self, (service, path))


class FakeBusItem:
    """Ein Pfad auf dem Ersatzbus; die Methoden heißen wie die von ``com.victronenergy.BusItem``."""

    def __init__(self, bus: FakeBus, key: tuple[str, str]) -> None:
        self.bus, self.key = bus, key

    def GetValue(self, **options: object) -> object:
        self.bus.calls.append(("GetValue", options))
        value = self.bus.items[self.key]
        if isinstance(value, Exception):
            raise value
        return value

    def SetValue(self, value: object, **options: object) -> int:
        self.bus.calls.append(("SetValue", options))
        self.bus.items[self.key] = value
        return self.bus.set_result


FAKE_DBUS = SimpleNamespace(Array=lambda items, signature: ("Array", tuple(items), signature))


def test_dbus_access_finds_the_service_by_device_instance() -> None:
    bus = FakeBus(
        {
            ("com.victronenergy.evcharger.a", "/DeviceInstance"): RuntimeError("NoReply"),
            ("com.victronenergy.evcharger.b", "/DeviceInstance"): 41,
            ("com.victronenergy.evcharger.c", "/DeviceInstance"): 40,
            ("com.victronenergy.evchargerx.d", "/DeviceInstance"): 42,
        }
    )
    access = DbusItemAccess(bus, FAKE_DBUS)
    assert access.find_service("com.victronenergy.evcharger", 40) == "com.victronenergy.evcharger.c"
    assert access.find_service("com.victronenergy.evcharger", 42) is None
    assert access.find_service("com.victronenergy.settings", None) == "com.victronenergy.settings"


def test_dbus_access_reads_and_writes_bus_items() -> None:
    bus = FakeBus({MIN_SOC_ITEM: 30.0})
    access = DbusItemAccess(bus, FAKE_DBUS)
    assert access.get_value(*MIN_SOC_ITEM) == 30.0
    assert access.set_value(*MIN_SOC_ITEM, 20) is True
    assert access.set_value(*MIN_SOC_ITEM, None) is True
    assert bus.items[MIN_SOC_ITEM] == ("Array", (), "i")  # ungültig: leeres Array
    bus.set_result = 2
    assert access.set_value(*MIN_SOC_ITEM, 20) is False
    interfaces = {(method, options["dbus_interface"]) for method, options in bus.calls}
    assert interfaces == {
        ("GetValue", "com.victronenergy.BusItem"),
        ("SetValue", "com.victronenergy.BusItem"),
    }
    assert {options["signature"] for method, options in bus.calls if method == "SetValue"} == {"v"}


OVERRIDE_ITEM = ("com.victronenergy.hub4", "/Overrides/Setpoint")


def test_override_is_cleared_with_the_invalid_value_and_then_left_alone() -> None:
    # null durchgehend: Der Wert der Aktion ist None, auf D-Bus geht er als leeres Array (ungültig)
    # hinaus, und ein Override, der schon ungültig ist, bleibt unberührt.
    bus = FakeBus({OVERRIDE_ITEM: 1500.0})
    access = DbusItemAccess(bus, SimpleNamespace(Array=DbusArray))
    action = Action("override_setpoint", *OVERRIDE_ITEM, None)
    runner = ActionRunner(access, [action], retries=0, retry_s=5.0)
    runner.start(0.0)
    runner.tick(0.0)
    assert runner.results == {"override_setpoint": "ok"}
    written = bus.items[OVERRIDE_ITEM]
    assert isinstance(written, DbusArray) and written == [] and written.signature == "i"
    runner.start(10.0)
    runner.tick(10.0)
    assert runner.results == {"override_setpoint": "unverändert"}
    assert [method for method, _ in bus.calls].count("SetValue") == 1  # kein zweites Schreiben
