"""Tests des EVCS-Aktors: Schreiben über den GX-Dienst, Rücklesen aus dem Modbus-Abbild."""

import pytest
from tests.helpers import T0

from bindaems.core.actuators.evcs import EvcsActuator
from bindaems.core.control.actuator import Capabilities, Confirmation
from bindaems.shared.control import CommandKind
from bindaems.shared.domain import Quality, Reading, SignalKind, Snapshot, Value

NO_INSTANCE = "Keine evcharger-Instanz in victron.instances.evcharger."
NOT_CONNECTED = "Cerbo nicht verbunden."
NOT_MANUAL = "EVCS nicht im manuellen Modus."
NOT_ACCEPTED = "Cerbo-Verbindung: Auftrag nicht angenommen."
MANUAL = {"wallbox__evcs__gx_mode": (0, Quality.OK)}


def readings(**signals: tuple[Value, Quality]) -> Snapshot:
    """Snapshot mit Qualität je Signal; Punkte im Namen als „__“ schreiben."""
    return Snapshot(
        T0,
        {
            name.replace("__", "."): Reading(value, T0, quality, "evcs", SignalKind.STATE)
            for name, (value, quality) in signals.items()
        },
    )


def actuator(cfg, sent: list, *, instance: int | None = 40, connected: bool = True) -> EvcsActuator:
    def send(path: str, value: float | int | None) -> bool:
        sent.append((path, value))
        return True

    return EvcsActuator("evcs", cfg.wallboxes["evcs"], instance, send, lambda: connected)


# Vorgabe des Plans


def test_writes_go_to_the_gx_service(cfg) -> None:
    sent: list = []
    evcs = actuator(cfg, sent)
    assert evcs.send(CommandKind.CURRENT, 6.0) is None
    assert evcs.send(CommandKind.CHARGING, False) is None
    assert sent == [("evcharger/40/SetCurrent", 6), ("evcharger/40/StartStop", 0)]


def test_present_prefers_modbus_and_falls_back_to_gx(cfg) -> None:
    evcs = actuator(cfg, [])
    both = readings(
        wallbox__evcs__set_current_a=(12, Quality.OK),
        wallbox__evcs__gx_set_current_a=(10, Quality.OK),
    )
    stale = readings(
        wallbox__evcs__set_current_a=(12, Quality.STALE),
        wallbox__evcs__gx_set_current_a=(10, Quality.OK),
    )
    assert evcs.present(both)[CommandKind.CURRENT] == 12
    assert evcs.present(stale)[CommandKind.CURRENT] == 10


def test_capabilities_name_every_reason(cfg) -> None:
    evcs = actuator(cfg, [], instance=None, connected=False)
    caps = evcs.capabilities(readings(wallbox__evcs__gx_mode=(1, Quality.OK)), T0)
    assert caps == Capabilities(
        False,
        (
            "Keine evcharger-Instanz in victron.instances.evcharger.",
            "Cerbo nicht verbunden.",
            "EVCS nicht im manuellen Modus.",
        ),
    )


def test_refused_write_names_the_reason(cfg) -> None:
    evcs = EvcsActuator("evcs", cfg.wallboxes["evcs"], 40, lambda p, v: False, lambda: True)
    assert evcs.send(CommandKind.CURRENT, 6.0) == "Cerbo-Verbindung: Auftrag nicht angenommen."


def test_confirm_waits_for_the_register(cfg) -> None:
    evcs = actuator(cfg, [])
    old = readings(wallbox__evcs__set_current_a=(16, Quality.OK))
    new = readings(wallbox__evcs__set_current_a=(6, Quality.OK))
    assert evcs.confirm(CommandKind.CURRENT, 6.0, old)[0] == Confirmation.PENDING
    assert evcs.confirm(CommandKind.CURRENT, 6.0, new)[0] == Confirmation.CONFIRMED


# Kennung und Frist


def test_identity_and_deadline(cfg) -> None:
    evcs = actuator(cfg, [])
    assert (evcs.id, evcs.confirm_timeout_s) == ("wallbox.evcs", 10.0)
    garage = EvcsActuator("garage", cfg.wallboxes["evcs"], 41, lambda p, v: True, lambda: True)
    assert garage.id == "wallbox.garage"


def test_no_alarms_of_its_own(cfg) -> None:
    assert actuator(cfg, []).alarms(T0) == []


# Schreiben


def test_current_is_written_in_whole_amps(cfg) -> None:
    sent: list = []
    evcs = actuator(cfg, sent)
    for amps in (6.0, 6.4, 15.6, 16):
        assert evcs.send(CommandKind.CURRENT, amps) is None
    path = "evcharger/40/SetCurrent"
    assert sent == [(path, 6), (path, 6), (path, 16), (path, 16)]
    assert all(type(value) is int for _, value in sent)  # ganze Ampere, kein 6.0


@pytest.mark.parametrize(("wanted", "written"), [(True, 1), (False, 0)])
def test_charging_is_written_as_one_or_zero(cfg, wanted, written) -> None:
    sent: list = []
    assert actuator(cfg, sent).send(CommandKind.CHARGING, wanted) is None
    assert sent == [("evcharger/40/StartStop", written)]
    assert type(sent[0][1]) is int  # 1/0, nicht True/False


@pytest.mark.parametrize("instance", [0, 41])  # auch Instanz 0 ist eine Instanz
def test_the_configured_instance_names_the_path(cfg, instance) -> None:
    sent: list = []
    evcs = actuator(cfg, sent, instance=instance)
    assert evcs.send(CommandKind.CURRENT, 8.0) is None
    assert evcs.send(CommandKind.CHARGING, True) is None
    assert sent == [(f"evcharger/{instance}/SetCurrent", 8), (f"evcharger/{instance}/StartStop", 1)]


@pytest.mark.parametrize(
    ("kind", "value"), [(CommandKind.CURRENT, 6.0), (CommandKind.CHARGING, True)]
)
def test_without_instance_nothing_is_written(cfg, kind, value) -> None:
    # Der Adapter wirft bei einem Pfad ohne Instanz; der Aktor baut gar keinen.
    sent: list = []
    assert actuator(cfg, sent, instance=None).send(kind, value) == NO_INSTANCE
    assert sent == []


def test_refused_start_stop_names_the_reason(cfg) -> None:
    evcs = EvcsActuator("evcs", cfg.wallboxes["evcs"], 40, lambda p, v: False, lambda: True)
    assert evcs.send(CommandKind.CHARGING, False) == NOT_ACCEPTED


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        (CommandKind.GRID_SETPOINT, "grid_setpoint"),
        (CommandKind.MAX_DISCHARGE, "max_discharge"),
        (CommandKind.MIN_SOC, "min_soc"),
    ],
)
def test_other_kinds_are_not_written(cfg, kind, name) -> None:
    sent: list = []
    assert actuator(cfg, sent).send(kind, 100.0) == f"Die EVCS kennt die Befehlsart „{name}“ nicht."
    assert sent == []


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf")])
def test_unusable_current_is_not_written(cfg, value) -> None:
    sent: list = []
    assert actuator(cfg, sent).send(CommandKind.CURRENT, value) == "Ungültiger Wert für „current“."
    assert sent == []


# Fähigkeiten


def test_controllable_in_manual_mode(cfg) -> None:
    caps = actuator(cfg, []).capabilities(readings(**MANUAL), T0)
    assert caps == Capabilities(True) and caps.reasons == ()


@pytest.mark.parametrize(
    ("instance", "connected", "state", "reasons"),
    [
        (None, True, MANUAL, (NO_INSTANCE,)),
        (40, False, MANUAL, (NOT_CONNECTED,)),
        (40, True, {"wallbox__evcs__gx_mode": (1, Quality.OK)}, (NOT_MANUAL,)),
        (40, True, {"wallbox__evcs__gx_mode": (0, Quality.STALE)}, (NOT_MANUAL,)),
        (40, True, {}, (NOT_MANUAL,)),  # ein unbekannter Modus zählt nicht als manuell
        (None, False, MANUAL, (NO_INSTANCE, NOT_CONNECTED)),
    ],
)
def test_reasons_come_in_this_order(cfg, instance, connected, state, reasons) -> None:
    evcs = actuator(cfg, [], instance=instance, connected=connected)
    assert evcs.capabilities(readings(**state), T0) == Capabilities(False, reasons)


def test_an_empty_register_block_is_not_manual(cfg) -> None:
    # Prüfprotokoll 10.10.2026: Ohne bekannte Produkt-ID sind alle Register 0 und lesen sich als
    # „manuell“. Der Modus kommt vom Cerbo oder aus einem gültigen Registerabbild.
    evcs = actuator(cfg, [])
    empty = readings(wallbox__evcs__product_id=(0, Quality.OK), wallbox__evcs__mode=(0, Quality.OK))
    valid = readings(
        wallbox__evcs__product_id=(0xC026, Quality.OK), wallbox__evcs__mode=(0, Quality.OK)
    )
    assert evcs.capabilities(empty, T0) == Capabilities(False, (NOT_MANUAL,))
    assert evcs.capabilities(valid, T0) == Capabilities(True)


# Zurückgelesene Werte

UNUSABLE = [
    pytest.param((12, Quality.STALE), id="stale"),
    pytest.param((12, Quality.INVALID), id="invalid"),
    pytest.param((None, Quality.INVALID), id="no-value"),
    pytest.param((float("nan"), Quality.OK), id="nan"),
    pytest.param((float("inf"), Quality.OK), id="inf"),
    pytest.param((True, Quality.OK), id="bool"),
    pytest.param(("12", Quality.OK), id="text"),
]


@pytest.mark.parametrize("modbus", UNUSABLE)
def test_current_falls_back_to_gx_when_modbus_is_unusable(cfg, modbus) -> None:
    evcs = actuator(cfg, [])
    state = readings(
        wallbox__evcs__set_current_a=modbus, wallbox__evcs__gx_set_current_a=(10, Quality.OK)
    )
    assert evcs.present(state) == {CommandKind.CURRENT: 10}


@pytest.mark.parametrize("gx", UNUSABLE)
def test_current_is_unknown_when_no_source_is_usable(cfg, gx) -> None:
    evcs = actuator(cfg, [])
    state = readings(
        wallbox__evcs__set_current_a=(12, Quality.STALE), wallbox__evcs__gx_set_current_a=gx
    )
    assert evcs.present(state) == {}


def test_nothing_is_known_without_readings(cfg) -> None:
    assert actuator(cfg, []).present(readings()) == {}


def test_a_set_current_of_zero_is_a_value(cfg) -> None:
    state = readings(
        wallbox__evcs__set_current_a=(0, Quality.OK),
        wallbox__evcs__gx_set_current_a=(10, Quality.OK),
    )
    assert actuator(cfg, []).present(state) == {CommandKind.CURRENT: 0}


def test_charging_is_read_as_a_boolean(cfg) -> None:
    evcs = actuator(cfg, [])
    on = evcs.present(readings(wallbox__evcs__start_stop=(1, Quality.OK)))
    off = evcs.present(readings(wallbox__evcs__start_stop=(0, Quality.OK)))
    assert on == {CommandKind.CHARGING: True} and on[CommandKind.CHARGING] is True
    assert off == {CommandKind.CHARGING: False} and off[CommandKind.CHARGING] is False
    # eine Zahl vom Gerät, kein Wahrheitswert: 1.0 liest sich wie 1
    assert evcs.present(readings(wallbox__evcs__start_stop=(1.0, Quality.OK))) == {
        CommandKind.CHARGING: True
    }


# Wie UNUSABLE, aber jeder Wert würde als „Laden an“ gelten, wenn er fälschlich zählte.
UNUSABLE_FLAG = [
    pytest.param((1, Quality.STALE), id="stale"),
    pytest.param((1, Quality.INVALID), id="invalid"),
    pytest.param((None, Quality.INVALID), id="no-value"),
    pytest.param((float("nan"), Quality.OK), id="nan"),
    pytest.param((True, Quality.OK), id="bool"),
    pytest.param(("1", Quality.OK), id="text"),
]


@pytest.mark.parametrize("modbus", UNUSABLE_FLAG)
def test_charging_falls_back_to_gx_when_modbus_is_unusable(cfg, modbus) -> None:
    evcs = actuator(cfg, [])
    state = readings(wallbox__evcs__start_stop=modbus, wallbox__evcs__gx_start_stop=(0, Quality.OK))
    assert evcs.present(state) == {CommandKind.CHARGING: False}


@pytest.mark.parametrize("gx", UNUSABLE_FLAG)
def test_charging_is_unknown_when_no_source_is_usable(cfg, gx) -> None:
    evcs = actuator(cfg, [])
    state = readings(wallbox__evcs__start_stop=(1, Quality.STALE), wallbox__evcs__gx_start_stop=gx)
    assert evcs.present(state) == {}


def test_charging_prefers_modbus_over_gx(cfg) -> None:
    state = readings(
        wallbox__evcs__start_stop=(0, Quality.OK), wallbox__evcs__gx_start_stop=(1, Quality.OK)
    )
    assert actuator(cfg, []).present(state) == {CommandKind.CHARGING: False}


@pytest.mark.parametrize(
    ("modbus", "gx", "expected"),
    [
        # nur 0 und 1 sind Werte; jeder andere fällt zur nächsten Quelle und sonst heraus
        ((2, Quality.OK), (1, Quality.OK), {CommandKind.CHARGING: True}),
        ((2, Quality.OK), (3, Quality.OK), {}),
        ((0.5, Quality.OK), (-1, Quality.OK), {}),
        ((1, Quality.STALE), (2, Quality.OK), {}),
    ],
)
def test_only_zero_and_one_are_charging_values(cfg, modbus, gx, expected) -> None:
    state = readings(wallbox__evcs__start_stop=modbus, wallbox__evcs__gx_start_stop=gx)
    assert actuator(cfg, []).present(state) == expected


def test_present_reports_each_kind_on_its_own(cfg) -> None:
    state = readings(
        wallbox__evcs__set_current_a=(12, Quality.OK),
        wallbox__evcs__gx_start_stop=(1, Quality.OK),
        wallbox__evcs__max_current_a=(16, Quality.OK),  # der Maximalstrom ist kein Sollstrom
    )
    assert actuator(cfg, []).present(state) == {
        CommandKind.CURRENT: 12,
        CommandKind.CHARGING: True,
    }


# Bestätigung


def test_confirm_charging_compares_booleans(cfg) -> None:
    evcs = actuator(cfg, [])
    on = readings(wallbox__evcs__start_stop=(1, Quality.OK))
    off = readings(wallbox__evcs__start_stop=(0, Quality.OK))
    assert evcs.confirm(CommandKind.CHARGING, True, on) == (Confirmation.CONFIRMED, "")
    assert evcs.confirm(CommandKind.CHARGING, False, off) == (Confirmation.CONFIRMED, "")
    assert evcs.confirm(CommandKind.CHARGING, True, off) == (Confirmation.PENDING, "")
    assert evcs.confirm(CommandKind.CHARGING, False, on) == (Confirmation.PENDING, "")


def test_confirm_stays_open_while_nothing_is_read_back(cfg) -> None:
    evcs = actuator(cfg, [])
    stale = readings(
        wallbox__evcs__set_current_a=(6, Quality.STALE),
        wallbox__evcs__start_stop=(1, Quality.STALE),
    )
    for kind, value in ((CommandKind.CURRENT, 6.0), (CommandKind.CHARGING, True)):
        assert evcs.confirm(kind, value, readings()) == (Confirmation.PENDING, "")
        assert evcs.confirm(kind, value, stale) == (Confirmation.PENDING, "")


def test_confirm_reads_back_from_gx_when_modbus_is_missing(cfg) -> None:
    evcs = actuator(cfg, [])
    gx = readings(wallbox__evcs__gx_set_current_a=(6, Quality.OK))
    assert evcs.confirm(CommandKind.CURRENT, 6.0, gx) == (Confirmation.CONFIRMED, "")
    assert evcs.confirm(CommandKind.CURRENT, 8.0, gx) == (Confirmation.PENDING, "")


def test_confirm_uses_the_current_tolerance(cfg) -> None:
    evcs = actuator(cfg, [])
    near = readings(wallbox__evcs__set_current_a=(6.4, Quality.OK))
    far = readings(wallbox__evcs__set_current_a=(7, Quality.OK))
    assert evcs.confirm(CommandKind.CURRENT, 6.0, near)[0] == Confirmation.CONFIRMED
    assert evcs.confirm(CommandKind.CURRENT, 6.0, far)[0] == Confirmation.PENDING
