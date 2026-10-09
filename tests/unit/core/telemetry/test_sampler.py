from dataclasses import replace
from datetime import timedelta

from tests.helpers import EMPTY_DERIVED, T0, FakeSink, snap_at

from bindaems.core.telemetry.sampler import TelemetrySampler, cadence_for, signal_to_point
from bindaems.shared.domain import Quality, Reading, SignalKind, SlotFlows
from bindaems.shared.influx.lineprotocol import Point
from bindaems.shared.timeutil import ManualClock

S = SignalKind.STATE
M = SignalKind.MEASUREMENT


def r(v, kind=M) -> Reading:
    return Reading(v, T0, Quality.OK, "victron", kind)


def test_grid_phase_power_point() -> None:
    assert signal_to_point("grid.l2.power_w", r(812.5)) == Point(
        "power", {"source": "grid", "id": "grid", "phase": "L2"}, {"p_w": 812.5}, T0
    )


def test_wallbox_current_point() -> None:
    assert signal_to_point("wallbox.twc.l3.current_a", r(16.0)) == Point(
        "power", {"source": "wallbox", "id": "twc", "phase": "L3"}, {"i_a": 16.0}, T0
    )


def test_energy_counter_point() -> None:
    assert signal_to_point("grid.energy_import_kwh", r(1234.5)) == Point(
        "energy", {"source": "grid", "id": "grid", "direction": "import"}, {"kwh": 1234.5}, T0
    )


def test_energy_directions_and_ids() -> None:
    def tags(signal: str) -> dict:
        point = signal_to_point(signal, r(1.0))
        assert point is not None
        return dict(point.tags)

    assert tags("pv.huawei.energy_kwh") == {"source": "pv", "id": "huawei", "direction": "forward"}
    assert tags("wallbox.evcs.total_kwh")["direction"] == "total"
    assert tags("wallbox.twc.session_kwh") == {
        "source": "wallbox",
        "id": "twc",
        "direction": "session",
    }


def test_soc_points() -> None:
    assert signal_to_point("battery.soc_pct", r(63.0)) == Point(
        "soc", {"id": "battery"}, {"pct": 63.0}, T0
    )
    assert signal_to_point("vehicle.tesla.soc_pct", r(55.0)) == Point(
        "soc", {"id": "tesla"}, {"pct": 55.0}, T0
    )


def test_other_power_mappings() -> None:
    def point(signal: str, value: float = 2.0) -> tuple:
        p = signal_to_point(signal, r(value))
        assert p is not None
        return p.measurement, dict(p.tags), dict(p.fields)

    assert point("vebus.l1.ac_out_power_w") == (
        "power",
        {"source": "vebus", "id": "ac_out", "phase": "L1"},
        {"p_w": 2.0},
    )
    assert point("consumption_input.l3.power_w")[1] == {
        "source": "load",
        "id": "consumption_input",
        "phase": "L3",
    }
    assert point("load.obergeschoss.power_w")[1]["phase"] == "total"
    assert point("pv_dc.power_w")[1] == {"source": "pv", "id": "dc", "phase": "total"}
    assert point("wallbox.evcs.l2.power_w")[1] == {"source": "wallbox", "id": "evcs", "phase": "L2"}
    assert point("vehicle.tesla.charger_power_kw", 11.0)[2] == {"p_w": 11000.0}


def test_numbers_are_always_floats() -> None:
    power = signal_to_point("grid.l1.power_w", r(500))
    status = signal_to_point("ess.hub4_mode", r(1, S))
    assert power is not None and isinstance(power.fields["p_w"], float)
    assert status is not None and isinstance(status.fields["value_num"], float)


def test_state_point_types() -> None:
    assert signal_to_point("ess.hub4_mode", r(1, S)).fields == {"value_num": 1}
    assert signal_to_point("vehicle.tesla.charging_state", r("Charging", S)).fields == {
        "value_str": "Charging"
    }
    assert signal_to_point("wallbox.twc.vehicle_connected", r(True, S)).fields == {
        "value_bool": True
    }


def test_measurement_without_rule() -> None:
    # Tessie liefert Zustände als Messwerte (Frische); Text und Wahrheitswerte gehen als status
    charging = signal_to_point("vehicle.tesla.charging_state", r("Charging"))
    assert charging is not None and charging.tags == {"id": "vehicle.tesla.charging_state"}
    assert signal_to_point("wallbox.evcs.gx_current_a", r(16.0)) is None  # Zahl ohne Regel


def test_cadence_for() -> None:
    assert cadence_for("grid.l1.power_w", M) == 2.0
    assert cadence_for("grid.l1.voltage_v", M) == 10.0
    assert cadence_for("grid.energy_import_kwh", M) == 60.0
    assert cadence_for("battery.soc_pct", M) == 30.0
    assert cadence_for("vehicle.egolf.soc_pct", S) is None  # HA-Zustand: bei Änderung
    assert cadence_for("ess.hub4_mode", S) is None


def test_cadence_power_2s_voltage_10s() -> None:
    clock = ManualClock(T0)
    sink = FakeSink()
    smp = TelemetrySampler(sink, clock)
    for _ in range(11):
        smp.on_cycle(
            snap_at(clock.now(), {"grid.l1.power_w": 1.0, "grid.l1.voltage_v": 230.0}),
            EMPTY_DERIVED,
        )
        clock.advance(1)
    assert sum(1 for p in sink.points if "p_w" in p.fields and p.tags.get("source") == "grid") == 6
    assert sum(1 for p in sink.points if "u_v" in p.fields) == 2


def test_state_written_only_on_change() -> None:
    clock = ManualClock(T0)
    sink = FakeSink()
    smp = TelemetrySampler(sink, clock)
    for v in (1, 1, 2):
        smp.on_cycle(snap_at(clock.now(), {"ess.hub4_mode": v}, kind=S), EMPTY_DERIVED)
        clock.advance(1)
    assert [p.fields["value_num"] for p in sink.points if p.measurement == "status"] == [1, 2]


def test_stale_readings_not_written() -> None:
    clock = ManualClock(T0)
    sink = FakeSink()
    smp = TelemetrySampler(sink, clock)
    smp.on_cycle(snap_at(T0, {"grid.l1.power_w": 1.0}, quality=Quality.STALE), EMPTY_DERIVED)
    assert sink.points == []


def test_derived_values_every_2s() -> None:
    clock = ManualClock(T0)
    sink = FakeSink()
    smp = TelemetrySampler(sink, clock)
    derived = replace(EMPTY_DERIVED, house_load_w=1500.0, pv_total_w=3000.0)
    for _ in range(4):
        smp.on_cycle(snap_at(clock.now(), {}), derived)
        clock.advance(1)
    house = [p for p in sink.points if p.tags.get("id") == "house_load"]
    assert len(house) == 2
    assert house[0] == Point(
        "power", {"source": "derived", "id": "house_load", "phase": "total"}, {"p_w": 1500.0}, T0
    )
    assert not [p for p in sink.points if p.tags.get("id") == "consumption"]  # None entfällt


def test_slot_flows_points() -> None:
    sink = FakeSink()
    smp = TelemetrySampler(sink, ManualClock(T0))
    smp.write_slot_flows(SlotFlows(T0, 900.0, {"pv>house": 250.0}, {}))
    assert Point("flows", {"flow": "pv>house"}, {"wh": 250.0}, T0) in sink.points
    assert Point("flows", {"flow": "_coverage"}, {"covered_s": 900.0}, T0) in sink.points


def test_due_tolerance_of_50_ms() -> None:
    clock = ManualClock(T0)
    sink = FakeSink()
    smp = TelemetrySampler(sink, clock)
    smp.on_cycle(snap_at(clock.now(), {"grid.power_w": 1.0}), EMPTY_DERIVED)
    clock.advance(timedelta(seconds=1.96))  # 2 s − 0,04 s: fällig
    smp.on_cycle(snap_at(clock.now(), {"grid.power_w": 1.0}), EMPTY_DERIVED)
    clock.advance(timedelta(seconds=1.9))  # 2 s − 0,1 s: noch nicht fällig
    smp.on_cycle(snap_at(clock.now(), {"grid.power_w": 1.0}), EMPTY_DERIVED)
    assert [p.ts for p in sink.points] == [T0, T0 + timedelta(seconds=1.96)]


def test_unchanged_values_get_the_sample_time() -> None:
    # Der Cerbo sendet konstante Werte nicht erneut: der Wert bleibt gültig, sein Zeitstempel alt.
    # Jede Ausgabe braucht trotzdem ihren eigenen Zeitpunkt, sonst überschreibt InfluxDB sie.
    from types import MappingProxyType

    from bindaems.shared.domain import Snapshot

    clock = ManualClock(T0)
    sink = FakeSink()
    smp = TelemetrySampler(sink, clock)
    unchanged = Reading(0.0, T0, Quality.OK, "victron", M)  # PV nachts: seit T0 konstant 0 W
    for _ in range(3):
        snap = Snapshot(clock.now(), MappingProxyType({"pv.huawei.power_w": unchanged}))
        smp.on_cycle(snap, EMPTY_DERIVED)
        clock.advance(2)
    assert [p.ts for p in sink.points] == [T0 + timedelta(seconds=s) for s in (0, 2, 4)]
