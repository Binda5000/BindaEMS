from datetime import UTC, datetime, timedelta

import pytest
from tests.helpers import snap

from bindaems.core.accounting.flows import SlotAccumulator, allocate, wb_key


def test_wb_key() -> None:
    assert wb_key("pv", "evcs") == "pv>wb:evcs"


def test_allocate_sunny_day() -> None:
    assert allocate(5000, -500, 1500, 1000, {"evcs": 2000, "twc": 0}) == {
        "pv>house": 1000,
        "pv>wb:evcs": 2000,
        "pv>battery": 1500,
        "pv>grid": 500,
    }


def test_allocate_night() -> None:
    assert allocate(0, 0, -800, 800, {}) == {"battery>house": 800}


def test_allocate_battery_assist_ev() -> None:
    assert allocate(2000, 1000, -1500, 500, {"evcs": 4000}) == {
        "pv>house": 500,
        "pv>wb:evcs": 1500,
        "battery>wb:evcs": 1500,
        "grid>wb:evcs": 1000,
    }


def test_allocate_grid_charging() -> None:
    assert allocate(0, 3500, 3000, 500, {}) == {"grid>house": 500, "grid>battery": 3000}


def test_allocate_priority_order() -> None:
    assert allocate(4000, 2000, 0, 0, {"twc": 3000, "evcs": 3000}) == {
        "pv>wb:twc": 3000,
        "pv>wb:evcs": 1000,
        "grid>wb:evcs": 2000,
    }


def test_allocate_reports_battery_to_grid() -> None:
    assert allocate(0, -1000, -1500, 500, {}) == {"battery>house": 500, "battery>grid": 1000}


def test_allocate_pv_prefers_wallboxes_over_battery() -> None:
    assert allocate(3000, 1000, 2000, 0, {"evcs": 2000}) == {
        "pv>wb:evcs": 2000,
        "pv>battery": 1000,
        "grid>battery": 1000,
    }


def test_allocate_battery_serves_house_before_wallboxes() -> None:
    assert allocate(0, 600, -1000, 600, {"evcs": 1000}) == {
        "battery>house": 600,
        "battery>wb:evcs": 400,
        "grid>wb:evcs": 600,
    }


def test_allocate_clamps_negative_inputs() -> None:
    assert allocate(-20, 500, 0, -5, {"evcs": -3}) == {}  # Messrauschen, kein Bedarf


def test_accumulator_integrates_and_closes_slot() -> None:
    acc = SlotAccumulator(counter_signals=["grid.energy_import_kwh"])
    t = datetime(2026, 10, 8, 9, 14, 58, tzinfo=UTC)
    assert acc.add(t, {"pv>house": 3600.0}, snap({"grid.energy_import_kwh": 100.0})) is None
    second = snap({"grid.energy_import_kwh": 100.5})
    assert acc.add(t + timedelta(seconds=1), {"pv>house": 3600.0}, second) is None
    closed = acc.add(
        t + timedelta(seconds=2), {"pv>house": 3600.0}, snap({"grid.energy_import_kwh": 100.6})
    )
    assert closed.slot_start == datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
    assert closed.flows_wh["pv>house"] == pytest.approx(2.0)
    assert closed.covered_s == pytest.approx(2.0)
    assert closed.counters["grid.energy_import_kwh"] == (100.0, 100.6)
    running = acc.flush()  # Grenzwert ist zugleich Start des Folgeslots
    assert running.counters["grid.energy_import_kwh"] == (100.6, 100.6)


def test_accumulator_holds_previous_power() -> None:
    acc = SlotAccumulator(counter_signals=[])
    t = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
    acc.add(t, {"pv>house": 3600.0}, snap({}))
    acc.add(t + timedelta(seconds=2), {"grid>house": 7200.0}, snap({}))
    acc.add(t + timedelta(seconds=3), {}, snap({}))
    flows = acc.flush().flows_wh
    assert flows["pv>house"] == pytest.approx(2.0)  # 2 s mit der Leistung des ersten Punkts
    assert flows["grid>house"] == pytest.approx(2.0)


def test_accumulator_skips_gaps() -> None:
    acc = SlotAccumulator(counter_signals=[])
    t = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
    acc.add(t, {"pv>house": 1000.0}, snap({}))
    acc.add(t + timedelta(seconds=10), {"pv>house": 1000.0}, snap({}))
    assert acc.flush().covered_s == 0.0


def test_accumulator_slots_across_dst_change() -> None:
    acc = SlotAccumulator(counter_signals=[])
    t = datetime(2026, 10, 25, 0, 59, 59, tzinfo=UTC)  # 02:59:59 MESZ, Umstellung um 01:00 UTC
    acc.add(t, {"grid>house": 3600.0}, snap({}))
    closed = acc.add(t + timedelta(seconds=1), {"grid>house": 3600.0}, snap({}))
    assert closed.slot_start == datetime(2026, 10, 25, 0, 45, tzinfo=UTC)
    assert acc.flush().slot_start == datetime(2026, 10, 25, 1, 0, tzinfo=UTC)


def test_counter_start_is_first_ok_value_and_flush_uses_last_seen() -> None:
    acc = SlotAccumulator(counter_signals=["grid.energy_import_kwh"])
    t = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
    acc.add(t, {}, snap({}))
    acc.add(t + timedelta(seconds=1), {}, snap({"grid.energy_import_kwh": 50.0}))
    acc.add(t + timedelta(seconds=2), {}, snap({"grid.energy_import_kwh": 50.1}))
    assert acc.flush().counters["grid.energy_import_kwh"] == (50.0, 50.1)


def test_missing_boundary_counter_gives_unknown_end() -> None:
    acc = SlotAccumulator(counter_signals=["grid.energy_import_kwh"])
    t = datetime(2026, 10, 8, 9, 14, 59, tzinfo=UTC)
    acc.add(t, {}, snap({"grid.energy_import_kwh": 50.0}))
    closed = acc.add(t + timedelta(seconds=1), {}, snap({}))
    assert closed.counters["grid.energy_import_kwh"] == (50.0, None)


def test_flush_without_samples_returns_none() -> None:
    acc = SlotAccumulator(counter_signals=[])
    assert acc.flush() is None
    acc.add(datetime(2026, 10, 8, 9, 0, tzinfo=UTC), {}, snap({}))
    assert acc.flush() is not None
    assert acc.flush() is None  # der laufende Slot wird nur einmal ausgegeben


def test_unknown_flows_do_not_count_as_covered() -> None:
    acc = SlotAccumulator(counter_signals=["grid.energy_import_kwh"])
    t = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
    acc.add(t, None, snap({"grid.energy_import_kwh": 1.0}))  # Bilanz unbekannt
    acc.add(t + timedelta(seconds=1), {"pv>house": 3600.0}, snap({}))
    acc.add(t + timedelta(seconds=2), None, snap({"grid.energy_import_kwh": 1.2}))
    slot = acc.flush()
    assert slot.covered_s == pytest.approx(1.0)  # nur die Sekunde mit bekannten Flüssen
    assert slot.flows_wh == {"pv>house": pytest.approx(1.0)}
    assert slot.counters["grid.energy_import_kwh"] == (1.0, 1.2)
