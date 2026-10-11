"""Tests des Victron-Aktors: Netz-Sollwert-Override, Entladegrenze und Min-SOC über den Cerbo."""

import json
from collections.abc import Mapping

import pytest
from tests.helpers import T0, snap

from bindaems.core.actuators.victron import VictronActuator
from bindaems.core.adapters.victron_mqtt import (
    FRESHNESS_S,
    MAX_DISCHARGE_PATH,
    MIN_SOC_PATH,
    SETPOINT_OVERRIDE_PATH,
    SOURCE,
    writable_paths,
)
from bindaems.core.adapters.victron_topics import InstanceResolver, parse_message
from bindaems.core.control.actuator import Capabilities, Confirmation
from bindaems.core.state.store import StateStore
from bindaems.shared.config import Config
from bindaems.shared.control import CommandKind
from bindaems.shared.domain import Quality, Reading, SignalKind, Snapshot, Value
from bindaems.shared.timeutil import ManualClock

NOT_CONNECTED = "Cerbo nicht verbunden."
NOT_ACCEPTED = "Cerbo-Verbindung: Auftrag nicht angenommen."
OVERRIDE = "ess.override.setpoint"
MAX_DISCHARGE = "ess.max_discharge_w"
MIN_SOC = "ess.min_soc_pct"
PORTAL = "c0619ab1234"


def readings(signals: Mapping[str, tuple[Value, Quality]]) -> Snapshot:
    """Snapshot mit Qualität je Signal."""
    return Snapshot(
        T0,
        {
            name: Reading(value, T0, quality, "victron", SignalKind.STATE)
            for name, (value, quality) in signals.items()
        },
    )


def actuator(
    sent: list | None = None, *, accept: bool = True, connected: bool = True
) -> VictronActuator:
    def send(path: str, value: float | int | None) -> bool:
        if sent is not None:
            sent.append((path, value))
        return accept

    return VictronActuator(send, lambda: connected)


class CerboFeed:
    """Der Weg vom Cerbo in den Zustandsspeicher, so wie ``VictronMqttAdapter`` ihn geht.

    Eine Nachricht ``N/<portal>/<pfad>`` mit ``{"value": …}`` läuft durch ``parse_message`` in einen
    echten ``StateStore``, der wie im Adapter eingerichtet ist.
    """

    def __init__(self, cfg: Config) -> None:
        self.store = StateStore(ManualClock(T0))
        self.store.register_source(
            SOURCE, FRESHNESS_S, activity_based=True, invalidate_on_reconnect=True
        )
        self.store.set_connected(SOURCE, True)
        self._resolver = InstanceResolver(cfg.victron.instances)

    def publish(self, path: str, value: object) -> None:
        topic, payload = f"N/{PORTAL}/{path}", json.dumps({"value": value}).encode()
        self.store.touch(SOURCE)
        for update in parse_message(topic, payload, PORTAL, self._resolver):
            self.store.update(update.signal, update.value, source=SOURCE, kind=update.kind)

    def connected(self) -> bool:
        return bool(self.store.connected(SOURCE))


# Vorgabe des Plans


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        (CommandKind.GRID_SETPOINT, 1500.0, ("hub4/0/Overrides/Setpoint", 1500.0)),
        (CommandKind.GRID_SETPOINT, None, ("hub4/0/Overrides/Setpoint", None)),
        (CommandKind.MAX_DISCHARGE, -1.0, ("settings/0/Settings/CGwacs/MaxDischargePower", -1.0)),
        (
            CommandKind.MIN_SOC,
            20.0,
            ("settings/0/Settings/CGwacs/BatteryLife/MinimumSocLimit", 20.0),
        ),
    ],
)
def test_paths_and_values(kind, value, expected) -> None:
    sent: list = []
    victron = VictronActuator(lambda p, v: sent.append((p, v)) or True, lambda: True)
    assert victron.send(kind, value) is None and sent == [expected]


def test_no_override_differs_from_unknown() -> None:
    victron = VictronActuator(lambda p, v: True, lambda: True)
    assert victron.present(snap({"ess.override.setpoint": None})) == {
        CommandKind.GRID_SETPOINT: None
    }
    assert CommandKind.GRID_SETPOINT not in victron.present(snap({}))


@pytest.mark.parametrize(
    ("kind", "wanted", "actual", "state"),
    [
        (CommandKind.GRID_SETPOINT, 1000.0, 1000.6, Confirmation.CONFIRMED),
        (CommandKind.GRID_SETPOINT, None, None, Confirmation.CONFIRMED),
        (CommandKind.MIN_SOC, 20.0, 20.4, Confirmation.CONFIRMED),
        (CommandKind.MIN_SOC, 20.0, 21.0, Confirmation.PENDING),
    ],
)
def test_confirm_tolerances(kind, wanted, actual, state) -> None:
    signal = {
        CommandKind.GRID_SETPOINT: "ess.override.setpoint",
        CommandKind.MIN_SOC: "ess.min_soc_pct",
    }[kind]
    victron = VictronActuator(lambda p, v: True, lambda: True)
    assert victron.confirm(kind, wanted, snap({signal: actual}))[0] == state


# Kennung und Frist


def test_identity_and_deadline() -> None:
    victron = actuator()
    assert (victron.id, victron.confirm_timeout_s) == ("victron", 10.0)


def test_no_alarms_of_its_own() -> None:
    assert actuator().alarms(T0) == []


# Fähigkeiten


def test_controllable_while_the_cerbo_is_connected() -> None:
    caps = actuator().capabilities(snap({}), T0)
    assert caps == Capabilities(True) and caps.reasons == ()


def test_not_controllable_without_the_cerbo() -> None:
    caps = actuator(connected=False).capabilities(snap({}), T0)
    assert caps == Capabilities(False, (NOT_CONNECTED,))


def test_capabilities_follow_the_connection() -> None:
    connected = [True]
    victron = VictronActuator(lambda p, v: True, lambda: connected[0])
    assert victron.capabilities(snap({}), T0) == Capabilities(True)
    connected[0] = False  # die Verbindung wird bei jeder Frage neu geprüft, nicht gemerkt
    assert victron.capabilities(snap({}), T0) == Capabilities(False, (NOT_CONNECTED,))
    connected[0] = True
    assert victron.capabilities(snap({}), T0) == Capabilities(True)


# Schreiben


def test_every_path_is_writable_for_the_adapter(cfg) -> None:
    # Der Adapter wirft bei einem Pfad außerhalb von ``writable_paths``; der Aktor baut keinen.
    sent: list = []
    victron = actuator(sent)
    for kind, value in (
        (CommandKind.GRID_SETPOINT, 1.0),
        (CommandKind.GRID_SETPOINT, None),
        (CommandKind.MAX_DISCHARGE, -1.0),
        (CommandKind.MIN_SOC, 20.0),
    ):
        assert victron.send(kind, value) is None
    assert len(sent) == 4
    assert {path for path, _ in sent} <= writable_paths(cfg.victron)


def test_values_are_written_as_floats() -> None:
    sent: list = []
    victron = actuator(sent)
    for kind in (CommandKind.GRID_SETPOINT, CommandKind.MAX_DISCHARGE, CommandKind.MIN_SOC):
        assert victron.send(kind, 20) is None  # eine ganze Zahl kommt als Gleitkommazahl an
    assert [value for _, value in sent] == [20.0, 20.0, 20.0]
    assert all(type(value) is float for _, value in sent)


@pytest.mark.parametrize("value", [0.0, -500.0, 3000.0])
def test_zero_and_signed_setpoints_are_numbers(value) -> None:
    # 0 W ist ein Override (Netzbezug null), nicht „Override aufheben“.
    sent: list = []
    assert actuator(sent).send(CommandKind.GRID_SETPOINT, value) is None
    assert sent == [("hub4/0/Overrides/Setpoint", value)]


@pytest.mark.parametrize(
    "kind", [CommandKind.GRID_SETPOINT, CommandKind.MAX_DISCHARGE, CommandKind.MIN_SOC]
)
def test_refused_write_names_the_reason(kind) -> None:
    victron = actuator(accept=False)
    assert victron.send(kind, 10.0) == NOT_ACCEPTED


def test_refused_lifting_of_the_override_names_the_reason() -> None:
    assert actuator(accept=False).send(CommandKind.GRID_SETPOINT, None) == NOT_ACCEPTED


@pytest.mark.parametrize(
    ("kind", "name"),
    [(CommandKind.CURRENT, "current"), (CommandKind.CHARGING, "charging")],
)
def test_other_kinds_are_not_written(kind, name) -> None:
    sent: list = []
    assert actuator(sent).send(kind, 6.0) == f"Victron kennt die Befehlsart „{name}“ nicht."
    assert sent == []


def test_an_unknown_kind_is_not_written() -> None:
    sent: list = []
    assert actuator(sent).send("bogus", 1.0) == "Victron kennt die Befehlsart „bogus“ nicht."
    assert sent == []


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        (CommandKind.GRID_SETPOINT, "grid_setpoint"),
        (CommandKind.MAX_DISCHARGE, "max_discharge"),
        (CommandKind.MIN_SOC, "min_soc"),
    ],
)
@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), float("-inf"), "1500"])
def test_unusable_value_is_not_written(kind, name, value) -> None:
    sent: list = []
    assert actuator(sent).send(kind, value) == f"Ungültiger Wert für „{name}“."
    assert sent == []


@pytest.mark.parametrize(
    ("kind", "name"),
    [(CommandKind.MAX_DISCHARGE, "max_discharge"), (CommandKind.MIN_SOC, "min_soc")],
)
def test_only_the_override_can_be_lifted_with_none(kind, name) -> None:
    sent: list = []
    assert actuator(sent).send(kind, None) == f"Ungültiger Wert für „{name}“."
    assert sent == []


# Zurückgelesene Werte

SETTINGS = [
    pytest.param(CommandKind.MAX_DISCHARGE, MAX_DISCHARGE, id="max_discharge"),
    pytest.param(CommandKind.MIN_SOC, MIN_SOC, id="min_soc"),
]

# Je Signal kein brauchbarer Wert: nicht OK, nicht vorhanden oder keine endliche Zahl
UNUSABLE = [
    pytest.param((20, Quality.STALE), id="stale"),
    pytest.param((20, Quality.INVALID), id="invalid"),
    pytest.param((None, Quality.INVALID), id="no-value"),
    pytest.param((None, Quality.OK), id="none"),
    pytest.param((float("nan"), Quality.OK), id="nan"),
    pytest.param((float("inf"), Quality.OK), id="inf"),
    pytest.param((True, Quality.OK), id="bool"),
    pytest.param(("20", Quality.OK), id="text"),
]


@pytest.mark.parametrize(("kind", "signal"), SETTINGS)
def test_settings_are_read_as_numbers(kind, signal) -> None:
    assert actuator().present(snap({signal: 20})) == {kind: 20.0}


@pytest.mark.parametrize(
    ("kind", "signal", "value"),
    [
        (CommandKind.MAX_DISCHARGE, MAX_DISCHARGE, 0),  # Entladen gesperrt
        (CommandKind.MAX_DISCHARGE, MAX_DISCHARGE, -1),  # unbegrenzt
        (CommandKind.MAX_DISCHARGE, MAX_DISCHARGE, 450.5),
        (CommandKind.MIN_SOC, MIN_SOC, 0),
        (CommandKind.MIN_SOC, MIN_SOC, 20.5),
    ],
)
def test_every_number_is_a_value_including_zero_and_unlimited(kind, signal, value) -> None:
    assert actuator().present(snap({signal: value})) == {kind: float(value)}


@pytest.mark.parametrize(("kind", "signal"), SETTINGS)
@pytest.mark.parametrize("reading", UNUSABLE)
def test_unusable_setting_is_unknown(kind, signal, reading) -> None:
    assert actuator().present(readings({signal: reading})) == {}


def test_nothing_is_known_without_readings() -> None:
    assert actuator().present(snap({})) == {}


@pytest.mark.parametrize("value", [1500.0, 1500, 0.0, 0, -300.5])
def test_override_is_its_number(value) -> None:
    # 0 W ist ein Override, nicht „kein Override“; beides unterscheidet sich im Ergebnis.
    assert actuator().present(snap({OVERRIDE: value})) == {CommandKind.GRID_SETPOINT: float(value)}


@pytest.mark.parametrize("quality", [Quality.OK, Quality.INVALID])
def test_no_override_is_none_while_the_cerbo_is_connected(quality) -> None:
    # Ein null des Cerbo (D-Bus-Wert ungültig) erreicht den Zustandsspeicher als ungültig (R23).
    state = snap({OVERRIDE: None}, quality=quality)
    assert actuator().present(state) == {CommandKind.GRID_SETPOINT: None}


@pytest.mark.parametrize("quality", [Quality.OK, Quality.INVALID])
def test_no_override_is_unknown_while_the_cerbo_is_not_connected(quality) -> None:
    state = snap({OVERRIDE: None}, quality=quality)
    assert actuator(connected=False).present(state) == {}


@pytest.mark.parametrize(
    ("value", "quality"),
    [
        (None, Quality.STALE),
        (1500.0, Quality.STALE),
        (1500.0, Quality.INVALID),
    ],
)
def test_override_is_unknown_without_a_usable_reading(value, quality) -> None:
    # Eine Zahl gilt nur mit Qualität OK; ein veralteter Wert sagt auch über „kein Override“ nichts.
    assert actuator().present(snap({OVERRIDE: value}, quality=quality)) == {}


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), float("-inf"), True, False, "1500", ""]
)
def test_override_that_is_no_number_is_unknown(value) -> None:
    assert actuator().present(snap({OVERRIDE: value})) == {}


def test_only_the_setpoint_override_is_the_grid_setpoint() -> None:
    # Weder der persistente Sollwert (AcPowerSetPoint) noch ein anderer Override ist der Override.
    other = snap({"ess.grid_setpoint_w": 50.0, "ess.override.maxdischargepower": 0.0})
    assert actuator().present(other) == {}


def test_present_reports_each_kind_on_its_own() -> None:
    state = readings(
        {
            OVERRIDE: (None, Quality.INVALID),
            MAX_DISCHARGE: (500, Quality.STALE),
            MIN_SOC: (20, Quality.OK),
        }
    )
    assert actuator().present(state) == {
        CommandKind.GRID_SETPOINT: None,
        CommandKind.MIN_SOC: 20.0,
    }


def test_all_kinds_are_read_back_together() -> None:
    state = snap({OVERRIDE: 1000, MAX_DISCHARGE: -1, MIN_SOC: 20})
    assert actuator().present(state) == {
        CommandKind.GRID_SETPOINT: 1000.0,
        CommandKind.MAX_DISCHARGE: -1.0,
        CommandKind.MIN_SOC: 20.0,
    }


# Bestätigung

SIGNAL_OF = {
    CommandKind.GRID_SETPOINT: OVERRIDE,
    CommandKind.MAX_DISCHARGE: MAX_DISCHARGE,
    CommandKind.MIN_SOC: MIN_SOC,
}


@pytest.mark.parametrize(
    ("kind", "wanted", "actual", "state"),
    [
        # Netz-Sollwert: ±1 W, der Rand gehört dazu
        (CommandKind.GRID_SETPOINT, 1000.0, 1001.0, Confirmation.CONFIRMED),
        (CommandKind.GRID_SETPOINT, 1000.0, 999.0, Confirmation.CONFIRMED),
        (CommandKind.GRID_SETPOINT, 1000.0, 1001.2, Confirmation.PENDING),
        (CommandKind.GRID_SETPOINT, 0.0, 0.0, Confirmation.CONFIRMED),
        # „kein Override“ und 0 W sind verschieden
        (CommandKind.GRID_SETPOINT, None, 0.0, Confirmation.PENDING),
        (CommandKind.GRID_SETPOINT, 0.0, None, Confirmation.PENDING),
        (CommandKind.GRID_SETPOINT, None, 500.0, Confirmation.PENDING),
        (CommandKind.GRID_SETPOINT, 500.0, None, Confirmation.PENDING),
        # Entladegrenze: ±1 W; −1 (unbegrenzt) passt nur zu −1
        (CommandKind.MAX_DISCHARGE, 500.0, 500.8, Confirmation.CONFIRMED),
        (CommandKind.MAX_DISCHARGE, 500.0, 502.0, Confirmation.PENDING),
        (CommandKind.MAX_DISCHARGE, -1.0, -1.0, Confirmation.CONFIRMED),
        (CommandKind.MAX_DISCHARGE, -1.0, 0.0, Confirmation.PENDING),
        (CommandKind.MAX_DISCHARGE, 0.0, -1.0, Confirmation.PENDING),
        # Min-SOC: ±0,5 %, der Rand gehört dazu
        (CommandKind.MIN_SOC, 20.0, 20.5, Confirmation.CONFIRMED),
        (CommandKind.MIN_SOC, 20.0, 19.5, Confirmation.CONFIRMED),
        (CommandKind.MIN_SOC, 20.0, 20.6, Confirmation.PENDING),
    ],
)
def test_confirm_follows_the_tolerance_of_the_kind(kind, wanted, actual, state) -> None:
    result = actuator().confirm(kind, wanted, snap({SIGNAL_OF[kind]: actual}))
    assert result == (state, "")


@pytest.mark.parametrize("kind", list(SIGNAL_OF))
def test_confirm_stays_open_while_nothing_is_read_back(kind) -> None:
    value = None if kind == CommandKind.GRID_SETPOINT else 20.0
    victron = actuator()
    stale = snap({SIGNAL_OF[kind]: value}, quality=Quality.STALE)
    assert victron.confirm(kind, value, snap({})) == (Confirmation.PENDING, "")
    assert victron.confirm(kind, value, stale) == (Confirmation.PENDING, "")


@pytest.mark.parametrize("quality", [Quality.OK, Quality.INVALID])
def test_lifting_the_override_is_confirmed_by_no_override(quality) -> None:
    # Der Cerbo meldet „kein Override“ als ungültigen Wert; das bestätigt das Aufheben (R23).
    victron = actuator()
    state = snap({OVERRIDE: None}, quality=quality)
    assert victron.confirm(CommandKind.GRID_SETPOINT, None, state) == (Confirmation.CONFIRMED, "")
    assert victron.confirm(CommandKind.GRID_SETPOINT, 1000.0, state) == (Confirmation.PENDING, "")


def test_lifting_the_override_is_not_confirmed_without_the_cerbo() -> None:
    state = snap({OVERRIDE: None}, quality=Quality.INVALID)
    victron = actuator(connected=False)
    assert victron.confirm(CommandKind.GRID_SETPOINT, None, state) == (Confirmation.PENDING, "")


def test_confirm_never_fails_on_its_own() -> None:
    # Die Frist überwacht der Ausführer; ein falscher Wert bleibt offen.
    victron = actuator()
    for kind, wanted in ((CommandKind.GRID_SETPOINT, 1000.0), (CommandKind.MIN_SOC, 20.0)):
        far = snap({SIGNAL_OF[kind]: 5000.0})
        assert victron.confirm(kind, wanted, far) == (Confirmation.PENDING, "")


def test_reading_back_never_writes() -> None:
    sent: list = []
    victron = actuator(sent)
    state = snap({OVERRIDE: 1000.0, MAX_DISCHARGE: -1.0, MIN_SOC: 20.0})
    victron.capabilities(state, T0)
    victron.present(state)
    victron.confirm(CommandKind.GRID_SETPOINT, 1000.0, state)
    victron.alarms(T0)
    assert sent == []


# Durch die echte Pipeline: Nachricht des Cerbo → parse_message → StateStore → Aktor


def test_the_override_and_its_removal_come_back_through_the_real_pipeline(cfg) -> None:
    cerbo = CerboFeed(cfg)
    victron = VictronActuator(lambda p, v: True, cerbo.connected)

    cerbo.publish(SETPOINT_OVERRIDE_PATH, 1500)
    active = cerbo.store.snapshot()
    assert victron.present(active) == {CommandKind.GRID_SETPOINT: 1500.0}
    assert victron.confirm(CommandKind.GRID_SETPOINT, 1500.0, active) == (
        Confirmation.CONFIRMED,
        "",
    )
    assert victron.confirm(CommandKind.GRID_SETPOINT, None, active) == (Confirmation.PENDING, "")

    cerbo.publish(SETPOINT_OVERRIDE_PATH, None)  # der D-Bus-Wert ist ungültig: kein Override
    lifted = cerbo.store.snapshot()
    reading = lifted.get(OVERRIDE)
    assert reading is not None  # die Annahme hinter R23: das null kommt als ungültiger Wert an
    assert (reading.value, reading.quality) == (None, Quality.INVALID)
    assert victron.present(lifted) == {CommandKind.GRID_SETPOINT: None}
    assert victron.confirm(CommandKind.GRID_SETPOINT, None, lifted) == (Confirmation.CONFIRMED, "")
    assert victron.confirm(CommandKind.GRID_SETPOINT, 1500.0, lifted) == (Confirmation.PENDING, "")


def test_nothing_is_known_through_the_real_pipeline_once_the_cerbo_is_gone(cfg) -> None:
    cerbo = CerboFeed(cfg)
    victron = VictronActuator(lambda p, v: True, cerbo.connected)
    cerbo.publish(SETPOINT_OVERRIDE_PATH, None)
    cerbo.publish(MAX_DISCHARGE_PATH, -1)
    cerbo.publish(MIN_SOC_PATH, 20)
    assert victron.present(cerbo.store.snapshot()) == {
        CommandKind.GRID_SETPOINT: None,
        CommandKind.MAX_DISCHARGE: -1.0,
        CommandKind.MIN_SOC: 20.0,
    }

    cerbo.store.set_connected(SOURCE, False)  # der Speicher hält das null weiter für ungültig
    gone = cerbo.store.snapshot()
    assert victron.present(gone) == {}
    assert victron.confirm(CommandKind.GRID_SETPOINT, None, gone) == (Confirmation.PENDING, "")


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        (CommandKind.GRID_SETPOINT, 1500.0),
        (CommandKind.GRID_SETPOINT, None),
        (CommandKind.MAX_DISCHARGE, -1.0),
        (CommandKind.MIN_SOC, 20.0),
    ],
)
def test_the_echo_of_the_cerbo_confirms_the_write(cfg, kind, value) -> None:
    # Der Pfad, den der Aktor schreibt, und das Signal, das er zurückliest, gehören zusammen.
    cerbo = CerboFeed(cfg)
    written: list = []
    victron = VictronActuator(lambda p, v: written.append((p, v)) or True, cerbo.connected)
    assert victron.send(kind, value) is None
    assert victron.confirm(kind, value, cerbo.store.snapshot()) == (Confirmation.PENDING, "")

    for path, payload in written:
        cerbo.publish(path, payload)  # der Cerbo meldet den geschriebenen Wert zurück
    assert victron.confirm(kind, value, cerbo.store.snapshot()) == (Confirmation.CONFIRMED, "")
