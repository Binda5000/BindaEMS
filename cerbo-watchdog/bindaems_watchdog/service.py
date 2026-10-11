"""Der D-Bus-Dienst ``com.victronenergy.bindaems`` auf dem Cerbo (Spec 8.6).

Dünne Hülle um Logik, Aktionen und Zustandsdatei: ``WatchdogService`` nimmt die Heartbeats an,
prüft jede Sekunde die Zeiten und veröffentlicht den Stand. ``dbus``, ``GLib`` und ``vedbus`` gibt
es nur auf dem Gerät; ``main`` importiert sie erst beim Start, damit der Rest überall läuft.
"""

from __future__ import annotations

import functools
import logging
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from bindaems_watchdog import VERSION
from bindaems_watchdog.actions import FAILED, ActionRunner, ItemAccess
from bindaems_watchdog.config import ConfigError, Settings, load_settings
from bindaems_watchdog.logic import ARMED, DISABLED, TRIPPED, WAITING, WatchdogLogic
from bindaems_watchdog.state_file import StoredState, read_state, write_state

SERVICE_NAME = "com.victronenergy.bindaems"
VELIB_PATH = "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python"
BUS_ITEM = "com.victronenergy.BusItem"
HEARTBEAT = "/Heartbeat"
TICK_MS = 1000
# Höchstdauer eines D-Bus-Aufrufs: Solange er läuft, steht die Hauptschleife.
CALL_TIMEOUT_S = 5.0
# Wartezeit vor dem Ende nach einem Konfigfehler: daemontools startet den Dienst sonst jede Sekunde
# neu, und jeder Start kostet den Cerbo Rechenzeit.
CONFIG_ERROR_DELAY_S = 60

STATE_NAMES = {WAITING: "wartet", ARMED: "scharf", TRIPPED: "ausgelöst", DISABLED: "deaktiviert"}

_log = logging.getLogger(__name__)


class WatchdogService:
    """Verbindet Logik, Aktionen, Zustandsdatei und die Pfade des D-Bus-Dienstes.

    ``publish(pfad, wert)`` setzt einen Pfad, ``save`` schreibt die Zustandsdatei. ``wall`` ist
    die Uhrzeit (für ``/LastTripTime``), ``monotonic`` die Uhr der Logik.
    """

    def __init__(
        self,
        settings: Settings,
        logic: WatchdogLogic,
        runner: ActionRunner,
        stored: StoredState,
        publish: Callable[[str, object], None],
        save: Callable[[StoredState], None],
        wall: Callable[[], float],
        monotonic: Callable[[], float],
    ) -> None:
        self._settings = settings
        self._logic = logic
        self._runner = runner
        self._publish = publish
        self._save = save
        self._wall = wall
        self._monotonic = monotonic
        self._heartbeat: Optional[int] = None
        self._state = logic.state
        self._trip_count = stored.trip_count
        self._last_trip_time = stored.last_trip_time
        self._last_trip_failures = 0
        self._run_pending = False  # Aktionen laufen, ihr Ergebnis ist noch nicht veröffentlicht

    def values(self) -> Dict[str, object]:
        """Die aktuellen Werte aller Pfade.

        ``/Heartbeat`` startet mit ``None`` (ungültig): velib ruft ``on_heartbeat`` für einen
        unveränderten Wert nicht auf. Startete er mit 0, ginge nach einem Neustart im Zustand
        „scharf“ eine erste Freigabe 0 verloren, und der Watchdog löste fälschlich aus.
        """
        return {
            HEARTBEAT: self._heartbeat,
            "/State": self._logic.state,
            "/TripCount": self._trip_count,
            "/LastTripTime": self._last_trip_time,
            "/LastTripFailures": self._last_trip_failures,
            "/TimeoutSeconds": int(self._settings.timeout_s),
            "/Version": VERSION,
        }

    def on_heartbeat(self, path: str, value: object) -> bool:
        """Callback von ``VeDbusService`` für ``/Heartbeat``; ``False`` lehnt den Wert ab.

        Angenommen werden nur ganze Zahlen ab 0 (kein ``bool``): Werte über 0 sind
        Lebenszeichen, die 0 gibt den Watchdog frei.
        """
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            _log.warning("Heartbeat abgelehnt: %r", value)
            return False
        self._heartbeat = value
        self._logic.heartbeat(value, self._monotonic())
        return True

    def tick(self) -> bool:
        """Der Takt (GLib-Timer, jede Sekunde); liefert immer ``True``.

        Eine Ausnahme im Callback würde GLib den Timer entfernen lassen, und der Watchdog stünde
        still. Darum fängt ``tick`` jeden Fehler, protokolliert ihn und macht weiter.
        """
        try:
            self._step(self._monotonic())
        except Exception:
            _log.exception("Fehler im Takt; der Watchdog läuft weiter.")
        return True

    def _step(self, now: float) -> None:
        if self._logic.tick(now):
            self._trip_count += 1
            self._last_trip_time = int(self._wall())
            self._runner.start(now)
            self._run_pending = True
            _log.warning(
                "Ausgelöst: seit über %g s kein Heartbeat (Auslösung %d). Die Aktionen laufen.",
                self._settings.timeout_s,
                self._trip_count,
            )
            self._send("/TripCount", self._trip_count)
            self._send("/LastTripTime", self._last_trip_time)
        try:
            self._runner.tick(now)
        except Exception:
            _log.exception("Fehler bei den Aktionen.")
        if self._run_pending and not self._runner.busy:
            self._run_pending = False
            self._finish_run()
        state = self._logic.state
        if state != self._state:
            _log.info("Zustand: %s → %s", STATE_NAMES[self._state], STATE_NAMES[state])
            self._state = state
            self._send("/State", state)
            self._store(StoredState(state == ARMED, self._trip_count, self._last_trip_time))

    def _finish_run(self) -> None:
        results = self._runner.results
        for name, result in results.items():
            if result == FAILED:
                _log.error("Aktion %s fehlgeschlagen.", name)
        summary = ", ".join(f"{name} {result}" for name, result in results.items())
        _log.info("Aktionen abgeschlossen: %s", summary or "keine")
        self._last_trip_failures = self._runner.failures
        self._send("/LastTripFailures", self._last_trip_failures)

    def _send(self, path: str, value: object) -> None:
        try:
            self._publish(path, value)
        except Exception as exc:
            _log.error("%s nicht veröffentlicht: %s", path, exc)

    def _store(self, state: StoredState) -> None:
        try:
            self._save(state)
        except Exception as exc:
            _log.error("Zustandsdatei nicht geschrieben: %s", exc)


def register_paths(dbus_service: Any, service: WatchdogService) -> None:
    """Legt die Pfade am ``VeDbusService`` an; nur ``/Heartbeat`` ist beschreibbar."""
    for path, value in service.values().items():
        if path == HEARTBEAT:
            dbus_service.add_path(
                path, value, writeable=True, onchangecallback=service.on_heartbeat
            )
        else:
            dbus_service.add_path(path, value)


class DbusItemAccess(ItemAccess):
    """``ItemAccess`` über D-Bus mit ``GetValue`` und ``SetValue`` an ``com.victronenergy.BusItem``.

    ``bus`` ist eine Verbindung von dbus-python, ``dbus`` das Modul selbst; beides übergibt
    ``main``, weil es nur auf dem Gerät vorhanden ist.
    """

    def __init__(self, bus: Any, dbus: Any) -> None:
        self._bus = bus
        self._dbus = dbus

    def find_service(self, service: str, instance: Optional[int]) -> Optional[str]:
        """Sucht unter den Diensten ``<service>.*`` den mit dieser ``/DeviceInstance``."""
        if instance is None:
            return service
        prefix = service + "."
        for name in self._bus.list_names():
            if not name.startswith(prefix):
                continue
            try:
                found = self.get_value(name, "/DeviceInstance")
            except Exception as exc:  # ein Dienst ohne Antwort verdeckt nicht die übrigen
                _log.warning("%s /DeviceInstance nicht lesbar: %s", name, exc)
                continue
            if found == instance:
                return str(name)
        return None

    def get_value(self, service: str, path: str) -> object:
        return self._item(service, path).GetValue(dbus_interface=BUS_ITEM, timeout=CALL_TIMEOUT_S)

    def set_value(self, service: str, path: str, value: object) -> bool:
        """Schreibt ``value`` als Variant; ``None`` wird zum leeren Array (ungültig)."""
        if value is None:
            value = self._dbus.Array([], signature="i")
        result = self._item(service, path).SetValue(
            value, dbus_interface=BUS_ITEM, signature="v", timeout=CALL_TIMEOUT_S
        )
        return bool(result == 0)

    def _item(self, service: str, path: str) -> Any:
        # Ohne Introspektion: ein Aufruf weniger je Zugriff; die Signatur steht im Aufruf.
        return self._bus.get_object(service, path, introspect=False)


def main(argv: Optional[List[str]] = None, *, sleep: Callable[[float], None] = time.sleep) -> int:
    """Startet den Dienst mit ``argv = [<config.ini>]``; 2 bei falschem Aufruf oder Konfigfehler.

    Nach einem Konfigfehler wartet ``main`` erst ``CONFIG_ERROR_DELAY_S`` (mit ``sleep``). Sonst
    kehrt es nicht zurück: Die Hauptschleife von GLib läuft, bis der Prozess beendet wird.
    """
    args = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(args) != 1:
        _log.error("Aufruf: bindaems-watchdog.py <config.ini>")
        return 2
    try:
        settings = load_settings(args[0])
    except ConfigError as exc:
        _log.error("%s", exc)
        _log.warning("Der Watchdog läuft nicht; nächster Versuch in %g s.", CONFIG_ERROR_DELAY_S)
        sleep(CONFIG_ERROR_DELAY_S)
        return 2
    stored, problem = read_state(settings.state_file)
    if problem is not None:
        _log.warning("%s", problem)

    sys.path.insert(1, VELIB_PATH)
    import dbus  # type: ignore[import-not-found]
    import dbus.mainloop.glib  # type: ignore[import-not-found]
    from gi.repository import GLib  # type: ignore[import-not-found]
    from vedbus import VeDbusService  # type: ignore[import-not-found]

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    try:
        dbus_service = VeDbusService(SERVICE_NAME, bus, register=False)
    except TypeError:  # ältere velib: ohne ``register``, meldet den Namen sofort an
        dbus_service = VeDbusService(SERVICE_NAME, bus)

    def publish(path: str, value: object) -> None:
        dbus_service[path] = value

    logic = WatchdogLogic(settings, stored.armed, time.monotonic())
    runner = ActionRunner(
        DbusItemAccess(bus, dbus), settings.actions, settings.retries, settings.retry_s
    )
    service = WatchdogService(
        settings,
        logic,
        runner,
        stored,
        publish=publish,
        save=functools.partial(write_state, settings.state_file),
        wall=time.time,
        monotonic=time.monotonic,
    )
    dbus_service.add_mandatory_paths(
        __file__, VERSION, "local", 0, 0xFFFF, "BindaEMS Watchdog", VERSION, "", 1
    )
    register_paths(dbus_service, service)
    if hasattr(dbus_service, "register"):
        dbus_service.register()

    names = ", ".join(action.name for action in settings.actions)
    _log.info("BindaEMS-Watchdog %s, Timeout %g s.", VERSION, settings.timeout_s)
    _log.info("%d Aktionen: %s", len(settings.actions), names or "keine")
    if not settings.actions:
        _log.warning("Keine Aktionen konfiguriert: Eine Auslösung ändert nichts.")
    _log.info("Start im Zustand %s.", STATE_NAMES[logic.state])

    GLib.timeout_add(TICK_MS, service.tick)
    GLib.MainLoop().run()
    return 0
