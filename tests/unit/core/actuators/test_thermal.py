"""Tests des Aktors der Klimaanlage: Sollwertversatz über den CoE-Adapter (Plan 6a, Task 5)."""

from collections.abc import Mapping
from datetime import timedelta

import pytest
from tests.helpers import T0, snap

from bindaems.core.actuators.thermal import (
    ECHO_MISMATCH_S,
    REASON_NO_VALUES,
    REASON_NOT_ACCEPTED,
    ThermalActuator,
)
from bindaems.core.control.actuator import Capabilities, Confirmation
from bindaems.shared.config import CoeOffsetConfig, Config
from bindaems.shared.control import CommandKind
from bindaems.shared.domain import Quality, Severity, Snapshot, Value

OFFSET = CommandKind.SETPOINT_OFFSET
ECHO = "thermal.klima.offset_active_k"
SENT = "thermal.klima.offset_sent_k"


class Device:
    """Was der Aktor vom Adapter sieht: ``set_offset`` und ob die Quelle verbunden ist."""

    def __init__(self, *, accept: bool = True, connected: bool = True) -> None:
        self.accept = accept
        self.connected = connected
        self.offsets: list[float] = []

    def set_offset(self, value: float) -> bool:
        self.offsets.append(value)
        return self.accept


@pytest.fixture
def klima(cfg: Config) -> CoeOffsetConfig:
    device = cfg.thermal.devices["klima"]
    assert isinstance(device, CoeOffsetConfig)
    return device


def actuator(klima: CoeOffsetConfig, device: Device) -> ThermalActuator:
    return ThermalActuator("klima", klima, device.set_offset, lambda: device.connected)


def values(echo: Value = None, sent: Value = None) -> Snapshot:
    signals: dict[str, Value] = {}
    if echo is not None:
        signals[ECHO] = echo
    if sent is not None:
        signals[SENT] = sent
    return snap(signals, source="klima")


def test_identity(klima: CoeOffsetConfig) -> None:
    thermal = actuator(klima, Device())
    assert thermal.id == "thermal.klima"
    assert thermal.confirm_timeout_s == 60.0


def test_present_from_echo(klima: CoeOffsetConfig) -> None:
    thermal = actuator(klima, Device())
    assert thermal.present(values(echo=-1.0)) == {OFFSET: -1.0}
    assert thermal.present(values(echo=0.0)) == {OFFSET: 0.0}


@pytest.mark.parametrize("echo", [None, True, "1.0", float("nan")])
def test_present_without_usable_echo(klima: CoeOffsetConfig, echo: Value) -> None:
    signals: Mapping[str, Value] = {ECHO: echo}
    assert actuator(klima, Device()).present(snap(signals)) == {}


def test_present_ignores_stale_echo(klima: CoeOffsetConfig) -> None:
    stale = snap({ECHO: 1.0}, quality=Quality.STALE)
    assert actuator(klima, Device()).present(stale) == {}


def test_capabilities_follow_the_source(klima: CoeOffsetConfig) -> None:
    device = Device()
    thermal = actuator(klima, device)
    assert thermal.capabilities(values(), T0) == Capabilities(True)
    device.connected = False
    assert thermal.capabilities(values(), T0) == Capabilities(False, (REASON_NO_VALUES,))
    assert REASON_NO_VALUES == "Keine Werte der Klimaanlage."


def test_send_sets_the_offset(klima: CoeOffsetConfig) -> None:
    device = Device()
    thermal = actuator(klima, device)
    assert thermal.send(OFFSET, -1.5) is None
    assert thermal.send(OFFSET, 0) is None
    assert device.offsets == [-1.5, 0.0]


def test_send_returns_reason_without_session(klima: CoeOffsetConfig) -> None:
    device = Device(accept=False)
    assert actuator(klima, device).send(OFFSET, 0.5) == REASON_NOT_ACCEPTED
    assert device.offsets == [0.5]


@pytest.mark.parametrize("value", [None, True, float("inf"), "0.5"])
def test_send_refuses_invalid_values(klima: CoeOffsetConfig, value: Value) -> None:
    device = Device()
    reason = actuator(klima, device).send(OFFSET, value)
    assert reason == "Ungültiger Wert für „setpoint_offset“."
    assert device.offsets == []


def test_send_refuses_other_kinds(klima: CoeOffsetConfig) -> None:
    device = Device()
    reason = actuator(klima, device).send(CommandKind.CURRENT, 6)
    assert reason == "Die Klimaanlage kennt die Befehlsart „current“ nicht."
    assert device.offsets == []


def test_confirm_within_tolerance(klima: CoeOffsetConfig) -> None:
    thermal = actuator(klima, Device())
    assert thermal.confirm(OFFSET, -1.0, values(echo=-1.04)) == (Confirmation.CONFIRMED, "")
    assert thermal.confirm(OFFSET, -1.0, values(echo=-0.95)) == (Confirmation.CONFIRMED, "")
    assert thermal.confirm(OFFSET, -1.0, values(echo=-0.9)) == (Confirmation.PENDING, "")
    assert thermal.confirm(OFFSET, -1.0, values()) == (Confirmation.PENDING, "")


def mismatch_after(thermal: ThermalActuator, snapshot: Snapshot, seconds: float) -> list:
    """Beobachtet ``snapshot`` von T0 bis ``seconds`` danach, je Sekunde, wie die Laufzeit."""
    for second in range(int(seconds) + 1):
        thermal.capabilities(snapshot, T0 + timedelta(seconds=second))
    return thermal.alarms(T0 + timedelta(seconds=seconds))


def test_echo_mismatch_alarm_after_120s(klima: CoeOffsetConfig) -> None:
    thermal = actuator(klima, Device())
    assert thermal.send(OFFSET, 1.0) is None
    wrong = values(echo=0.0, sent=1.0)
    assert ECHO_MISMATCH_S == 120.0
    assert mismatch_after(thermal, wrong, 119) == []
    thermal.capabilities(wrong, T0 + timedelta(seconds=120))
    [alarm] = thermal.alarms(T0 + timedelta(seconds=120))
    assert alarm.id == "thermal.echo_mismatch.klima"
    assert alarm.severity is Severity.WARNING
    assert alarm.since == T0 + timedelta(seconds=120)
    assert alarm.message == (
        "Klimaanlage: Das Echo der UVR zeigt seit mehr als 120 s nicht den gesendeten Versatz "
        "(gesendet +1,0 K, Echo 0,0 K)."
    )
    # stimmt das Echo wieder, endet der Alarm sofort
    thermal.capabilities(values(echo=1.0, sent=1.0), T0 + timedelta(seconds=121))
    assert thermal.alarms(T0 + timedelta(seconds=121)) == []


def test_missing_echo_counts_as_mismatch(klima: CoeOffsetConfig) -> None:
    # Pakete kommen, aber der UVR-Ausgang für das Echo fehlt (Review Focus 4)
    thermal = actuator(klima, Device())
    assert thermal.send(OFFSET, 0.5) is None
    [alarm] = mismatch_after(thermal, values(sent=0.5), 120)
    assert alarm.message.endswith("(gesendet +0,5 K, kein Echo).")


def test_echo_compares_with_what_the_adapter_sends(klima: CoeOffsetConfig) -> None:
    # Nimmt die Laufzeit die Freigabe zurück, sendet der Adapter 0: Ein Echo 0 ist dann richtig.
    thermal = actuator(klima, Device())
    assert thermal.send(OFFSET, 1.0) is None
    assert mismatch_after(thermal, values(echo=0.0, sent=0.0), 300) == []
    # Ohne Wert des Adapters gilt der zuletzt gesendete
    assert mismatch_after(thermal, values(echo=0.0), 300) != []


def test_no_echo_alarm_without_live_send(klima: CoeOffsetConfig) -> None:
    # Im Dry-Run ruft der Ausführer ``send`` nie: kein Live-Betrieb, kein Alarm
    thermal = actuator(klima, Device())
    assert mismatch_after(thermal, values(sent=0.0), 300) == []
    # ein abgelehnter Auftrag zählt nicht als gesendet
    refused = actuator(klima, Device(accept=False))
    assert refused.send(OFFSET, 0.5) == REASON_NOT_ACCEPTED
    assert mismatch_after(refused, values(echo=0.0, sent=0.0), 300) == []


def test_no_echo_alarm_without_source(klima: CoeOffsetConfig) -> None:
    device = Device()
    thermal = actuator(klima, device)
    assert thermal.send(OFFSET, 1.0) is None
    mismatch_after(thermal, values(echo=0.0, sent=1.0), 100)
    device.connected = False  # ohne Pakete gibt es kein Echo zu beurteilen; die Zeit beginnt neu
    thermal.capabilities(values(echo=0.0, sent=1.0), T0 + timedelta(seconds=101))
    assert thermal.alarms(T0 + timedelta(seconds=200)) == []
    device.connected = True
    assert mismatch_after(thermal, values(echo=0.0, sent=1.0), 119) == []
