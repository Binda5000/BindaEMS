import re
from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs

from bindaems.app.history.influx import InfluxQueryError, Series
from bindaems.app.ledger.estimate import (
    MAX_GAP_SLOTS,
    GapEstimator,
    allocate_wh,
    estimate_gap,
    gaps,
)
from bindaems.shared.domain import SlotFlows
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.timeutil import SLOT

T0 = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)  # Beginn der Lücke, 4 Slots bis 07:00
NOW = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)  # die Uhr der Tests (T_APP)
IMP, EXP = "grid.energy_import_kwh", "grid.energy_export_kwh"
PV = "pv.huawei.energy_kwh"
EVCS = "wallbox.evcs.total_kwh"
ORDER = ["evcs", "twc"]


class EdgeInflux:
    """Ersatz für den ``InfluxReader``: liefert die Zählerstände und den Ladezustand an den Rändern
    einer Lücke, je Signal und Zeitpunkt (``last`` am Zeitpunkt davor, ``first`` danach)."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, datetime], float] = {}
        self.queries: list[str] = []
        self.error: Exception | None = None

    def edges(self, signal: str, before: float | None, after: float | None, *, begin, end) -> None:
        for instant, value in ((begin, before), (end, after)):
            if value is not None:
                self.values[(signal, instant)] = value

    async def query(self, q: str, *, database: str | None = None) -> list[Series]:
        self.queries.append(q)
        if self.error is not None:
            raise self.error
        window = re.search(r"time >= (\d+)ms AND time <= (\d+)ms", q)
        assert window is not None
        low, high = (datetime.fromtimestamp(int(g) / 1000, UTC) for g in window.groups())
        instant = high if "last(" in q else low
        if 'FROM "raw"."soc"' in q:
            signal = "soc"
        else:
            source, ident, direction = re.findall(r"'([^']*)'", q)
            signal = {
                "grid": f"grid.energy_{direction}_kwh",
                "pv": f"pv.{ident}.energy_kwh",
                "wallbox": f"wallbox.{ident}.total_kwh",
            }[source]
        value = self.values.get((signal, instant))
        return (
            [] if value is None else [Series("m", {}, ["time", "v"], [[epoch_ms(instant), value]])]
        )


@pytest.fixture
def edge() -> EdgeInflux:
    return EdgeInflux()


@pytest.fixture
def estimator(ledger, edge, clock, cfg) -> GapEstimator:
    return GapEstimator(ledger, edge, clock, cfg)  # type: ignore[arg-type]


def real_slots(
    ledger, first: datetime, last: datetime, *, skip: set[datetime] = frozenset()
) -> None:  # type: ignore[assignment]
    """Echte Slots von ``first`` bis vor ``last``, ohne die in ``skip``."""
    at = first
    while at < last:
        if at not in skip:
            ledger.ingest(SlotFlows(at, 900.0, {"grid>house": 100.0}, {}), "stream")
        at += SLOT


def gap_slots(n: int = 4, start: datetime = T0) -> set[datetime]:
    return {start + SLOT * i for i in range(n)}


def counters(edge: EdgeInflux, *, n: int = 4, start: datetime = T0, **values) -> None:
    """Zählerstände vor/nach der Lücke: ``signal=(vorher, nachher)``."""
    for signal, (before, after) in values.items():
        edge.edges(signal, before, after, begin=start, end=start + SLOT * n)


NAMES = {"imp": IMP, "exp": EXP, "pv": PV, "evcs": EVCS}


def set_counters(edge: EdgeInflux, n: int = 4, **values: tuple[float | None, float | None]) -> None:
    counters(edge, n=n, **{NAMES[key]: bounds for key, bounds in values.items()})


# --- gaps ---------------------------------------------------------------------------------


def test_gaps_returns_runs_of_missing_slots() -> None:
    present = [T0 + SLOT * i for i in (0, 1, 4, 7, 8)]
    assert gaps(present, T0, T0 + SLOT * 9) == [(T0 + SLOT * 2, 2), (T0 + SLOT * 5, 2)]


def test_gaps_counts_only_completely_past_slots() -> None:
    # das Ende liegt mitten im Slot 3: nur die Slots 0 bis 2 sind vollständig vergangen
    assert gaps([], T0, T0 + SLOT * 3 + timedelta(minutes=7)) == [(T0, 3)]
    assert gaps([], T0, T0 + timedelta(minutes=14)) == []


def test_gaps_at_the_edges_of_the_range() -> None:
    assert gaps([T0 + SLOT], T0, T0 + SLOT * 3) == [(T0, 1), (T0 + SLOT * 2, 1)]
    assert gaps([], T0, T0) == []


def test_gaps_ignores_slots_outside_and_accepts_other_zones() -> None:
    from zoneinfo import ZoneInfo

    local = (T0 + SLOT).astimezone(ZoneInfo("Europe/Vienna"))
    assert gaps([local, T0 - SLOT, T0 + SLOT * 9], T0, T0 + SLOT * 3) == [
        (T0, 1),
        (T0 + SLOT * 2, 1),
    ]


# --- allocate_wh --------------------------------------------------------------------------


def test_allocate_wh_waterfall() -> None:
    flows = allocate_wh(1000.0, 200.0, 300.0, {"evcs": 400.0, "twc": 500.0})
    # PV: Haus 300, evcs 400, twc 300 (von 500); Akku bekommt nichts aus PV
    assert flows == {
        "pv>house": 300.0,
        "pv>wb:evcs": 400.0,
        "pv>wb:twc": 300.0,
        "grid>wb:twc": 200.0,
        "grid>battery": 200.0,
    }


def test_allocate_wh_discharge_covers_house_before_the_grid() -> None:
    flows = allocate_wh(0.0, -500.0, 300.0, {"evcs": 100.0})
    assert flows == {"battery>house": 300.0, "battery>wb:evcs": 100.0, "battery>grid": 100.0}


def test_allocate_wh_surplus_goes_to_the_grid_and_negative_inputs_count_zero() -> None:
    assert allocate_wh(500.0, 0.0, 100.0, {}) == {"pv>house": 100.0, "pv>grid": 400.0}
    assert allocate_wh(-5.0, 0.0, -3.0, {"evcs": -1.0}) == {}


# --- estimate_gap -------------------------------------------------------------------------


def test_gap_energy_is_spread_evenly() -> None:
    slots = estimate_gap(T0, 4, {"import": 4.0, "export": 0.0, "pv": 0.0}, 0.0, ORDER)
    assert [s.slot_start for s in slots] == [T0 + SLOT * i for i in range(4)]
    for slot in slots:
        assert slot.flows_wh == {"grid>house": pytest.approx(1000.0)}
        assert slot.covered_s == 0.0 and slot.counters == {}


def test_gap_with_pv_wallbox_and_export() -> None:
    energies = {"import": 0.0, "export": 2.0, "pv": 6.0, "wb:evcs": 2.0}  # Haus = 0 − 2 + 6 − 2 = 2
    [slot, _] = estimate_gap(T0, 2, energies, 0.0, ORDER)
    assert slot.flows_wh == {
        "pv>house": pytest.approx(1000.0),
        "pv>wb:evcs": pytest.approx(1000.0),
        "pv>grid": pytest.approx(1000.0),
    }


def test_negative_house_becomes_dc_pv() -> None:
    # Haus = 0 − 0 + 2 − 3 = −1 kWh: der Fehlbetrag zählt als zusätzliche PV, das Haus als 0
    [slot] = estimate_gap(T0, 1, {"pv": 2.0, "wb:evcs": 3.0}, 0.0, ORDER)
    assert slot.flows_wh == {"pv>wb:evcs": pytest.approx(3000.0)}


def test_rounding_residue_of_the_house_balance_adds_no_flows() -> None:
    # Haus = 0,3 − 0,1 − 0,2 = −2,8e-17 kWh: ein Rundungsrest, kein DC-PV und kein Hausfluss
    [slot] = estimate_gap(T0, 1, {"import": 0.3, "wb:evcs": 0.1}, 0.2, ORDER)
    assert slot.flows_wh == {
        "grid>wb:evcs": pytest.approx(100.0),
        "grid>battery": pytest.approx(200.0),
    }


def test_battery_charge_comes_from_the_grid_or_the_pv() -> None:
    # 1 kWh Ladung über 4 Slots: 250 Wh je Slot
    [grid, *_] = estimate_gap(T0, 4, {"import": 1.0}, 1.0, ORDER)
    assert grid.flows_wh == {"grid>battery": pytest.approx(250.0)}
    [pv, *_] = estimate_gap(T0, 4, {"pv": 1.0, "export": 0.0}, 1.0, ORDER)
    assert pv.flows_wh == {"pv>battery": pytest.approx(250.0)}


def test_battery_discharge_feeds_the_house() -> None:
    [slot] = estimate_gap(T0, 1, {}, -1.0, ORDER)  # Haus = 0 − 0 + 0 − 0 + 1 = 1 kWh
    assert slot.flows_wh == {"battery>house": pytest.approx(1000.0)}


def test_estimate_gap_without_slots_is_empty() -> None:
    assert estimate_gap(T0, 0, {"import": 1.0}, 0.0, ORDER) == []


def test_wallbox_order_decides_who_gets_the_pv() -> None:
    energies = {"pv": 1.0, "wb:evcs": 1.0, "wb:twc": 1.0, "import": 1.0}
    [first] = estimate_gap(T0, 1, energies, 0.0, ["twc", "evcs"])
    assert first.flows_wh["pv>wb:twc"] == pytest.approx(1000.0)
    assert first.flows_wh["grid>wb:evcs"] == pytest.approx(1000.0)


# --- GapEstimator -------------------------------------------------------------------------


def window_end(clock) -> datetime:
    return clock.now()


async def test_estimator_fills_a_gap_from_the_counter_readings(
    estimator, ledger, edge, clock
) -> None:
    real_slots(ledger, T0 - SLOT * 2, T0 + SLOT * 6, skip=gap_slots())
    set_counters(edge, imp=(100.0, 104.0), exp=(50.0, 50.0), pv=(10.0, 10.0))
    assert await estimator.run(T0 - SLOT * 2, window_end(clock)) == 4
    stored = {s.slot_start: s for s in ledger.slots(T0, T0 + SLOT * 4)}
    assert sorted(stored) == sorted(gap_slots())
    for slot in stored.values():
        assert slot.origin == "estimate" and slot.covered_s == 0.0
        assert slot.flows_wh == {"grid>house": pytest.approx(1000.0)}
        assert slot.import_wh == pytest.approx(1000.0) and slot.export_wh == 0.0


async def test_gap_over_the_repeated_hour_of_the_dst_change(estimator, ledger, edge, clock) -> None:
    # 25.10.2026: 01:30 MESZ bis 03:30 MEZ sind 3 Stunden (02:00–03:00 doppelt), also 12 Slots
    from datetime import date

    from bindaems.shared.timeutil import LOCAL_TZ

    clock.advance(datetime(2026, 10, 27, 8, 0, tzinfo=UTC) - clock.now())
    begin = datetime(2026, 10, 25, 1, 30, tzinfo=LOCAL_TZ).astimezone(UTC)
    assert begin == datetime(2026, 10, 24, 23, 30, tzinfo=UTC)
    real_slots(ledger, begin - SLOT * 2, begin + SLOT * 14, skip=gap_slots(12, begin))
    counters(edge, n=12, start=begin, **{IMP: (100.0, 112.0), EXP: (50.0, 50.0)})
    assert await estimator.run(begin - SLOT * 2, begin + SLOT * 14) == 12
    stored = [s for s in ledger.slots(begin, begin + SLOT * 12) if s.origin == "estimate"]
    assert [s.slot_start for s in stored] == sorted(gap_slots(12, begin))
    assert stored[-1].slot_start == datetime(2026, 10, 25, 2, 15, tzinfo=UTC)  # 03:15 MEZ
    assert all(s.flows_wh == {"grid>house": pytest.approx(1000.0)} for s in stored)
    [day] = ledger.days(date(2026, 10, 25), date(2026, 10, 25))
    assert day.estimated_slots == 12 and day.expected_slots == 100


async def test_battery_from_soc_difference(estimator, ledger, edge, clock, cfg) -> None:
    real_slots(ledger, T0 - SLOT, NOW, skip=gap_slots())
    set_counters(edge, imp=(100.0, 103.0), exp=(50.0, 50.0))
    edge.edges("soc", 50.0, 60.0, begin=T0, end=T0 + SLOT * 4)
    await estimator.run(T0 - SLOT, window_end(clock))
    # SOC 50 → 60 % bei 30 kWh nutzbar: 3 kWh Ladung aus dem Netz, nichts fürs Haus
    flows = [s.flows_wh for s in ledger.slots(T0, T0 + SLOT * 4)]
    assert cfg.battery.usable_kwh == 30
    assert all(f == {"grid>battery": pytest.approx(750.0)} for f in flows)


async def test_missing_soc_means_no_battery_change(estimator, ledger, edge, clock) -> None:
    real_slots(ledger, T0 - SLOT, NOW, skip=gap_slots())
    set_counters(edge, imp=(100.0, 104.0), exp=(50.0, 50.0))
    edge.edges("soc", 50.0, None, begin=T0, end=T0 + SLOT * 4)  # nur ein Rand
    await estimator.run(T0 - SLOT, window_end(clock))
    assert {tuple(s.flows_wh) for s in ledger.slots(T0, T0 + SLOT * 4)} == {("grid>house",)}


async def test_missing_pv_and_wallbox_counters_count_zero(estimator, ledger, edge, clock) -> None:
    real_slots(ledger, T0 - SLOT, NOW, skip=gap_slots())
    # PV ohne Wert am Ende, Wallbox ohne beide Ränder, PV-Zähler zurückgesetzt wäre ebenso 0
    set_counters(edge, imp=(100.0, 104.0), exp=(50.0, 50.0), pv=(10.0, None))
    await estimator.run(T0 - SLOT, window_end(clock))
    assert {tuple(s.flows_wh) for s in ledger.slots(T0, T0 + SLOT * 4)} == {("grid>house",)}


async def test_reset_pv_counter_counts_zero(estimator, ledger, edge, clock) -> None:
    real_slots(ledger, T0 - SLOT, NOW, skip=gap_slots())
    set_counters(edge, imp=(100.0, 104.0), exp=(50.0, 50.0), pv=(9000.0, 3.0))
    await estimator.run(T0 - SLOT, window_end(clock))
    assert {tuple(s.flows_wh) for s in ledger.slots(T0, T0 + SLOT * 4)} == {("grid>house",)}


async def test_wallbox_and_pv_energy_are_allocated(estimator, ledger, edge, clock) -> None:
    real_slots(ledger, T0 - SLOT, NOW, skip=gap_slots())
    # 4 kWh PV, 2 kWh in die Wallbox, 1 kWh Haus (aus der Bilanz), 1 kWh Einspeisung
    set_counters(edge, imp=(100.0, 100.0), exp=(50.0, 51.0), pv=(10.0, 14.0), evcs=(7.0, 9.0))
    await estimator.run(T0 - SLOT, window_end(clock))
    slot = ledger.slots(T0, T0 + SLOT)[0]
    assert slot.flows_wh == {
        "pv>house": pytest.approx(250.0),
        "pv>wb:evcs": pytest.approx(500.0),
        "pv>grid": pytest.approx(250.0),
    }
    assert slot.export_wh == pytest.approx(250.0)


async def test_gap_without_reading_stays_empty(estimator, ledger, edge, clock) -> None:
    real_slots(ledger, T0 - SLOT, NOW, skip=gap_slots())
    # keine Zählerstände: weder Bezug noch Einspeisung
    with capture_logs() as logs:
        assert await estimator.run(T0 - SLOT, window_end(clock)) == 0
    assert ledger.slots(T0, T0 + SLOT * 4) == []
    [entry] = [e for e in logs if e["event"] == "Lücke bleibt leer"]
    assert entry["reason"] == "import: Zählerstand fehlt"


@pytest.mark.parametrize("missing", ["imp", "exp"])
@pytest.mark.parametrize("edge_index", [0, 1])
async def test_one_missing_grid_edge_is_enough_to_skip(
    estimator, ledger, edge, clock, missing, edge_index
) -> None:
    real_slots(ledger, T0 - SLOT, NOW, skip=gap_slots())
    values: dict[str, tuple[float | None, float | None]] = {
        "imp": (100.0, 104.0),
        "exp": (50.0, 50.0),
    }
    bounds = list(values[missing])
    bounds[edge_index] = None
    values[missing] = (bounds[0], bounds[1])
    set_counters(edge, **values)
    assert await estimator.run(T0 - SLOT, window_end(clock)) == 0
    assert ledger.slots(T0, T0 + SLOT * 4) == []


async def test_reset_grid_counter_leaves_the_gap_empty(estimator, ledger, edge, clock) -> None:
    real_slots(ledger, T0 - SLOT, NOW, skip=gap_slots())
    set_counters(edge, imp=(900.0, 4.0), exp=(50.0, 50.0))
    with capture_logs() as logs:
        assert await estimator.run(T0 - SLOT, window_end(clock)) == 0
    assert [e["reason"] for e in logs if e["event"] == "Lücke bleibt leer"] == [
        "import: Zähler zurückgesetzt"
    ]


async def test_gap_longer_than_7_days_is_skipped(estimator, ledger, edge, clock) -> None:
    n = MAX_GAP_SLOTS + 1
    start = T0 - SLOT * (n + 4)
    real_slots(ledger, start - SLOT, NOW, skip={start + SLOT * i for i in range(n)})
    counters(edge, n=n, start=start, **{IMP: (1.0, 5.0), EXP: (1.0, 1.0)})
    with capture_logs() as logs:
        assert await estimator.run(start - SLOT, clock.now()) == 0
    assert any(e["event"] == "Lücke zu lang für eine Nachrechnung" for e in logs)
    assert edge.queries == []  # gar nicht erst gelesen


async def test_gap_of_exactly_7_days_is_estimated(estimator, ledger, edge, clock) -> None:
    n = MAX_GAP_SLOTS
    start = clock.now() - SLOT * n - SLOT
    counters(edge, n=n, start=start, **{IMP: (1.0, 5.0), EXP: (1.0, 1.0)})
    assert await estimator.run(start, start + SLOT * n) == n


async def test_real_slot_replaces_estimate(estimator, ledger, edge, clock) -> None:
    real_slots(ledger, T0 - SLOT, NOW, skip=gap_slots())
    set_counters(edge, imp=(100.0, 104.0), exp=(50.0, 50.0))
    await estimator.run(T0 - SLOT, window_end(clock))
    assert {s.origin for s in ledger.slots(T0, T0 + SLOT * 4)} == {"estimate"}
    assert ledger.ingest(SlotFlows(T0, 600.0, {"grid>house": 1234.0}, {}), "influx") is True
    first = ledger.slots(T0, T0 + SLOT)[0]
    assert first.origin == "influx" and first.covered_s == 600.0
    assert first.flows_wh == {"grid>house": 1234.0}


async def test_estimate_never_replaces_a_stored_slot(estimator, ledger, edge, clock) -> None:
    real_slots(ledger, T0 - SLOT, NOW, skip=gap_slots())
    before = ledger.slots(T0 - SLOT, NOW)
    set_counters(edge, imp=(100.0, 104.0), exp=(50.0, 50.0))
    await estimator.run(T0 - SLOT, window_end(clock))
    after = {s.slot_start: s for s in ledger.slots(T0 - SLOT, NOW)}
    for slot in before:
        assert after[slot.slot_start] == slot  # unverändert
    # auch ein direkter Versuch über ``ingest`` ändert einen echten Slot ohne Abdeckung nicht
    ledger.ingest(SlotFlows(T0 + SLOT * 10, 0.0, {"grid>house": 5.0}, {}), "stream")
    assert (
        ledger.ingest(SlotFlows(T0 + SLOT * 10, 0.0, {"grid>house": 9.0}, {}), "estimate") is False
    )


async def test_second_run_changes_nothing(estimator, ledger, edge, clock) -> None:
    real_slots(ledger, T0 - SLOT, NOW, skip=gap_slots())
    set_counters(edge, imp=(100.0, 104.0), exp=(50.0, 50.0))
    assert await estimator.run(T0 - SLOT, window_end(clock)) == 4
    stored = ledger.slots(T0 - SLOT, NOW)
    edge.queries.clear()
    assert await estimator.run(T0 - SLOT, window_end(clock)) == 0
    assert ledger.slots(T0 - SLOT, NOW) == stored
    assert edge.queries == []  # keine Lücke, keine Abfrage


async def test_estimates_are_marked(estimator, ledger, edge, clock) -> None:
    real_slots(ledger, T0 - SLOT, NOW, skip=gap_slots())
    set_counters(edge, imp=(100.0, 104.0), exp=(50.0, 50.0))
    await estimator.run(T0 - SLOT, window_end(clock))
    day = ledger.days(T0.date(), T0.date())[0]
    assert day.estimated_slots == 4
    assert all(s.origin == "estimate" and s.covered_s == 0 for s in ledger.slots(T0, T0 + SLOT * 4))


async def test_estimated_slots_notify_the_listeners(estimator, ledger, edge, clock) -> None:
    changed: list[datetime] = []
    ledger.on_slots_changed(changed.append)  # der Einstandspreis des Akkus hört ebenso zu
    real_slots(ledger, T0 - SLOT, NOW, skip=gap_slots())
    changed.clear()
    set_counters(edge, imp=(100.0, 104.0), exp=(50.0, 50.0))
    await estimator.run(T0 - SLOT, window_end(clock))
    assert min(changed) == T0 and len(changed) == 4


async def test_two_gaps_are_read_separately(estimator, ledger, edge, clock) -> None:
    second = T0 + SLOT * 6
    real_slots(ledger, T0 - SLOT, NOW, skip=gap_slots() | gap_slots(2, second))
    set_counters(edge, imp=(100.0, 104.0), exp=(50.0, 50.0))
    counters(edge, n=2, start=second, **{IMP: (200.0, 201.0), EXP: (70.0, 70.0)})
    assert await estimator.run(T0 - SLOT, clock.now()) == 6
    [slot] = ledger.slots(second, second + SLOT)[:1]
    assert slot.flows_wh == {"grid>house": pytest.approx(500.0)}


async def test_only_slots_that_are_over_get_estimated(estimator, ledger, edge, clock) -> None:
    # der laufende Slot (08:00) und alles danach bleibt unberührt, auch bei längerer Spanne
    now = clock.now()
    real_slots(ledger, now - SLOT * 3, now - SLOT)
    counters(edge, n=1, start=now - SLOT, **{IMP: (1.0, 2.0), EXP: (1.0, 1.0)})
    assert await estimator.run(now - SLOT * 3, now + timedelta(days=1)) == 1
    assert [s.slot_start for s in ledger.slots(now - SLOT, now + SLOT * 4)] == [now - SLOT]


async def test_influx_failure_goes_to_the_caller(estimator, ledger, edge, clock) -> None:
    edge.error = InfluxQueryError("InfluxDB nicht erreichbar")
    with pytest.raises(InfluxQueryError):
        await estimator.run(T0 - SLOT, window_end(clock))
    assert ledger.slots(T0 - SLOT, clock.now()) == []


async def test_reads_edges_per_gap_with_one_query_per_signal_and_edge(
    estimator, ledger, edge, clock, cfg
) -> None:
    real_slots(ledger, T0 - SLOT, NOW, skip=gap_slots())
    set_counters(edge, imp=(100.0, 104.0), exp=(50.0, 50.0))
    await estimator.run(T0 - SLOT, window_end(clock))
    signals = 2 + len(cfg.victron.instances.pv) + len(cfg.wallboxes)
    assert len(edge.queries) == 2 * signals + 2  # zwei Ränder je Zähler, dazu zweimal der SOC


async def test_empty_or_reversed_range_does_nothing(estimator, edge, clock) -> None:
    assert await estimator.run(clock.now(), clock.now()) == 0
    assert await estimator.run(clock.now(), clock.now() - timedelta(hours=1)) == 0
    assert (
        await estimator.run(clock.now() + SLOT, clock.now() + SLOT * 3) == 0
    )  # alles in der Zukunft
    assert edge.queries == []
