from datetime import UTC, date, datetime, timedelta

import pytest
from tests.unit.app.ledger.conftest import FakeInflux

from bindaems.app.history.influx import InfluxQueryError
from bindaems.app.ledger.checks import NOTE_MISSING, DayCheck, check_day
from bindaems.app.ledger.service import LedgerSlot
from bindaems.shared.timeutil import SLOT, local_day_slots

DAY = date(2026, 10, 9)
PV = ["pv.huawei.energy_kwh"]
WALLBOXES = ["evcs", "twc"]
IMP, EXP = "grid.energy_import_kwh", "grid.energy_export_kwh"


def slot(
    n: int = 0,
    *,
    import_wh: float = 0.0,
    export_wh: float = 0.0,
    flows: dict[str, float] | None = None,
) -> LedgerSlot:
    return LedgerSlot(
        slot_start=local_day_slots(DAY)[n],
        covered_s=900.0,
        flows_wh=flows or {},
        counters={},
        import_wh=import_wh,
        export_wh=export_wh,
        import_price_ct=None,
        origin="stream",
    )


def by_signal(checks: list[DayCheck]) -> dict[str, DayCheck]:
    return {check.signal: check for check in checks}


def run(
    slots: list[LedgerSlot],
    start: dict[str, float | None],
    end: dict[str, float | None],
) -> dict[str, DayCheck]:
    return by_signal(check_day(DAY, slots, start, end, WALLBOXES, PV))


def test_signals_in_order() -> None:
    checks = check_day(DAY, [], {}, {}, WALLBOXES, PV)
    assert [c.signal for c in checks] == ["import", "export", "pv", "wb:evcs", "wb:twc"]
    assert all(c.day == DAY for c in checks)


def test_flagged_above_one_percent_and_100_wh() -> None:
    # Abrechnung 10,2 kWh, Zähler 10,0 kWh → 2 %, 0,2 kWh: markiert
    result = run([slot(import_wh=10200.0)], {IMP: 100.0}, {IMP: 110.0})["import"]
    assert result.ledger_kwh == pytest.approx(10.2) and result.counter_kwh == pytest.approx(10.0)
    assert result.deviation_pct == pytest.approx(2.0)
    assert result.flagged is True and result.note is None


def test_negative_deviation_is_flagged_too() -> None:
    result = run([slot(import_wh=9700.0)], {IMP: 100.0}, {IMP: 110.0})["import"]
    assert result.deviation_pct == pytest.approx(-3.0) and result.flagged is True


def test_small_absolute_difference_is_not_flagged() -> None:
    # Einspeisung 0,50 gegen 0,45 kWh → 11 %, aber nur 0,05 kWh
    result = run([slot(export_wh=500.0)], {EXP: 50.0}, {EXP: 50.45})["export"]
    assert result.deviation_pct == pytest.approx(11.11, abs=0.01)
    assert result.flagged is False and result.note is None


def test_small_relative_difference_is_not_flagged() -> None:
    result = run([slot(import_wh=100500.0)], {IMP: 0.0}, {IMP: 100.0})["import"]
    assert result.deviation_pct == pytest.approx(0.5) and result.flagged is False


def test_wallbox_sums_flows_into_that_wallbox() -> None:
    slots = [
        slot(0, flows={"pv>wb:evcs": 3000.0, "grid>wb:evcs": 1000.0, "pv>wb:twc": 500.0}),
        slot(1, flows={"battery>wb:evcs": 1000.0}),
    ]
    start = {"wallbox.evcs.total_kwh": 10.0, "wallbox.twc.total_kwh": 3.0}
    end = {"wallbox.evcs.total_kwh": 15.0, "wallbox.twc.total_kwh": 3.5}
    result = run(slots, start, end)
    assert result["wb:evcs"].ledger_kwh == pytest.approx(5.0) and not result["wb:evcs"].flagged
    assert result["wb:twc"].ledger_kwh == pytest.approx(0.5) and not result["wb:twc"].flagged


def test_pv_is_never_flagged() -> None:
    slots = [slot(flows={"pv>house": 5000.0, "pv>battery": 1000.0, "pv>grid": 2000.0})]
    result = run(slots, {PV[0]: 0.0}, {PV[0]: 20.0})["pv"]
    assert result.ledger_kwh == pytest.approx(8.0) and result.counter_kwh == pytest.approx(20.0)
    assert result.deviation_pct == pytest.approx(-60.0)
    assert result.flagged is False


def test_pv_counters_are_summed() -> None:
    check_pv = ["pv.a.energy_kwh", "pv.b.energy_kwh"]
    start = {"pv.a.energy_kwh": 10.0, "pv.b.energy_kwh": 20.0}
    end = {"pv.a.energy_kwh": 12.0, "pv.b.energy_kwh": 23.0}
    [pv] = [
        c
        for c in check_day(DAY, [slot(flows={"pv>house": 5000.0})], start, end, [], check_pv)
        if c.signal == "pv"
    ]
    assert pv.counter_kwh == pytest.approx(5.0) and pv.deviation_pct == pytest.approx(0.0)


def test_pv_without_counter_is_noted() -> None:
    [pv] = [c for c in check_day(DAY, [], {}, {}, [], []) if c.signal == "pv"]
    assert pv.counter_kwh is None and pv.note == "Zählerstand fehlt"


def test_counter_reset_is_not_flagged() -> None:
    result = run([slot(import_wh=10000.0)], {IMP: 5000.0}, {IMP: 12.0})["import"]
    assert result.counter_kwh is None and result.deviation_pct is None
    assert result.flagged is False and result.note == "Zähler zurückgesetzt"


def test_one_reset_pv_counter_resets_the_sum() -> None:
    check_pv = ["pv.a.energy_kwh", "pv.b.energy_kwh"]
    start = {"pv.a.energy_kwh": 10.0, "pv.b.energy_kwh": 20.0}
    end = {"pv.a.energy_kwh": 12.0, "pv.b.energy_kwh": 1.0}
    [pv] = [c for c in check_day(DAY, [], start, end, [], check_pv) if c.signal == "pv"]
    assert pv.counter_kwh is None and pv.note == "Zähler zurückgesetzt"


@pytest.mark.parametrize(
    ("start", "end"),
    [({}, {IMP: 1.0}), ({IMP: 1.0}, {}), ({IMP: None}, {IMP: 1.0}), ({IMP: 1.0}, {IMP: None})],
)
def test_missing_reading_is_noted(start, end) -> None:
    result = run([slot(import_wh=1000.0)], start, end)["import"]
    assert result.counter_kwh is None and result.deviation_pct is None
    assert result.flagged is False and result.note == "Zählerstand fehlt"
    assert result.ledger_kwh == pytest.approx(1.0)


def test_reset_and_missing_are_noted_per_signal() -> None:
    result = run([], {IMP: 9.0, EXP: None}, {IMP: 1.0, EXP: 1.0})
    assert result["import"].note == "Zähler zurückgesetzt"
    assert result["export"].note == "Zählerstand fehlt"


def test_zero_counter_has_no_deviation() -> None:
    result = run([slot(import_wh=1000.0)], {IMP: 7.0}, {IMP: 7.0})["import"]
    assert result.counter_kwh == 0.0 and result.deviation_pct is None
    assert result.flagged is False and result.note == "Zähler zeigt 0 kWh"


def test_day_check_is_frozen() -> None:
    result = run([], {}, {})["import"]
    with pytest.raises(AttributeError):
        result.flagged = True  # type: ignore[misc]


# --- LedgerChecks ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("import_wh", "start", "end", "flagged"),
    [
        (30300.0, 100.0, 130.0, False),  # genau 1 % (roh 1,0000000000000024 %), 0,3 kWh
        (30310.0, 100.0, 130.0, True),  # knapp über 1 %
        (2100.0, 100.3, 102.3, False),  # genau 0,1 kWh (roh 0,10000000000000009), 5 %
        (2101.0, 100.3, 102.3, True),  # knapp über 0,1 kWh
        (29700.0, 100.0, 130.0, False),  # genau −1 %
        (1900.0, 100.3, 102.3, False),  # genau −0,1 kWh
        (1899.0, 100.3, 102.3, True),  # knapp darunter
    ],
)
def test_thresholds_are_compared_after_rounding(
    import_wh: float, start: float, end: float, flagged: bool
) -> None:
    # B9: Rundungsreste dürfen einen Tag genau an der Grenze nicht markieren
    result = run([slot(import_wh=import_wh)], {IMP: start}, {IMP: end})["import"]
    assert result.flagged is flagged


def midnight(day: date) -> datetime:
    return local_day_slots(day)[0]


def counters(per_day: dict[date, dict[str, float]]) -> dict[str, dict[datetime, float]]:
    result: dict[str, dict[datetime, float]] = {}
    for day, values in per_day.items():
        for signal, value in values.items():
            result.setdefault(signal, {})[midnight(day)] = value
    return result


async def test_run_stores_checks_and_replaces_them(checks, ledger, influx: FakeInflux) -> None:
    from tests.app_helpers import SLOT_FLOWS_MSG

    from bindaems.app.ledger.service import parse_slot_flows

    ledger.ingest(parse_slot_flows(SLOT_FLOWS_MSG), "stream")  # 09.10.2026, 0,4 kWh Bezug
    influx.set_counters(
        counters(
            {
                DAY: {IMP: 100.0, EXP: 50.0},
                DAY + timedelta(days=1): {IMP: 100.4, EXP: 51.0},
            }
        )
    )
    assert await checks.run(DAY, DAY) == 1
    assert len(influx.queries) == 1
    stored = by_signal(checks.checks(DAY, DAY))
    assert stored["import"].counter_kwh == pytest.approx(0.4) and not stored["import"].flagged
    assert stored["export"].counter_kwh == pytest.approx(1.0) and stored["export"].flagged
    assert stored["pv"].note == "Zählerstand fehlt"
    assert set(stored) == {"import", "export", "pv", "wb:evcs", "wb:twc"}
    # ein zweiter Lauf ersetzt die Zeilen der Tage, er hängt nichts an
    influx.set_counters(counters({DAY: {IMP: 0.0}, DAY + timedelta(days=1): {IMP: 0.4}}))
    await checks.run(DAY, DAY)
    again = by_signal(checks.checks(DAY, DAY))
    assert len(checks.checks(DAY, DAY)) == 5
    assert again["export"].note == "Zählerstand fehlt" and again["export"].counter_kwh is None


async def test_run_reads_all_midnights_in_one_query(checks, influx: FakeInflux) -> None:
    influx.set_counters({})
    assert await checks.run(date(2026, 9, 1), date(2026, 10, 5)) == 35
    assert len(influx.queries) == 1
    assert len(checks.checks(date(2026, 9, 1), date(2026, 10, 5))) == 35 * 5


async def test_run_on_dst_change_uses_the_right_midnights(checks, influx: FakeInflux) -> None:
    day = date(2026, 10, 25)  # 25 Stunden
    start, end = (
        datetime(2026, 10, 24, 22, 0, tzinfo=UTC),
        datetime(2026, 10, 25, 23, 0, tzinfo=UTC),
    )
    influx.set_counters({IMP: {start: 500.0, end: 530.0}})
    await checks.run(day, day)
    assert by_signal(checks.checks(day, day))["import"].counter_kwh == pytest.approx(30.0)


async def test_run_on_spring_dst_change_uses_the_right_midnights(
    checks, influx: FakeInflux
) -> None:
    day = date(2026, 3, 29)  # 23 Stunden
    start, end = (
        datetime(2026, 3, 28, 23, 0, tzinfo=UTC),
        datetime(2026, 3, 29, 22, 0, tzinfo=UTC),
    )
    influx.set_counters({IMP: {start: 500.0, end: 523.0}})
    await checks.run(day, day)
    assert by_signal(checks.checks(day, day))["import"].counter_kwh == pytest.approx(23.0)


async def test_run_interpolates_the_midnight_reading(checks, ledger, influx: FakeInflux) -> None:
    # B1: der core schreibt die Zähler nur jede Minute. Hohe Last über Mitternacht (12 kW, 0,2 kWh
    # je Minute), letzter Wert 40 s vor Mitternacht: der Stand um Mitternacht liegt dazwischen.
    from bindaems.shared.domain import SlotFlows

    start, end = midnight(DAY), midnight(DAY + timedelta(days=1))
    for slot_start in local_day_slots(DAY)[:5]:
        ledger.ingest(SlotFlows(slot_start, 900.0, {"grid>house": 1000.0}, {}), "stream")
    second = timedelta(seconds=1)
    influx.set_counters(
        {
            IMP: {
                start - 100 * second: 99.8,
                start - 40 * second: 100.0,
                start + 20 * second: 100.2,  # 100,1333 um Mitternacht
                end - 40 * second: 105.1333,
                end + 20 * second: 105.1333,
            }
        }
    )
    await checks.run(DAY, DAY)
    result = by_signal(checks.checks(DAY, DAY))["import"]
    assert result.ledger_kwh == pytest.approx(5.0)
    assert result.counter_kwh == pytest.approx(5.0, abs=1e-3)
    assert not result.flagged  # vorher: 5,13 kWh am Zähler, 2,7 % und 0,13 kWh, markiert


@pytest.mark.parametrize("phase_s", [5, 40, 55])
async def test_midnight_reading_is_exact_with_minute_readings_at_a_phase(
    checks, influx: FakeInflux, phase_s: int
) -> None:
    # N1: der core liest die Zähler jede Minute zu einer beliebigen Sekunde φ. In ``long`` stempelt
    # die CQ jeden Wert mit dem Anfang seiner Minute, dort trifft die Interpolation den Stand um
    # Mitternacht nicht; aus ``raw`` (Lesezeitpunkte) ist sie bei linearer Last exakt. Um die erste
    # Mitternacht laufen 12 kW (100,0 kWh genau um Mitternacht), um die zweite steht der Zähler.
    start, end = midnight(DAY), midnight(DAY + timedelta(days=1))
    kwh_per_s = 12.0 / 3600
    readings: dict[datetime, float] = {}
    for minute in range(-5, 5):
        read = start + timedelta(minutes=minute, seconds=phase_s)
        readings[read] = 100.0 + (read - start).total_seconds() * kwh_per_s
        readings[end + timedelta(minutes=minute, seconds=phase_s)] = 150.0
    influx.set_counters({IMP: readings})
    await checks.run(DAY, DAY)
    assert by_signal(checks.checks(DAY, DAY))["import"].counter_kwh == pytest.approx(50.0, abs=1e-9)


async def test_run_on_the_100_slot_day_counts_every_slot(
    checks, ledger, influx: FakeInflux
) -> None:
    # 25.10.2026 hat 25 Stunden: 100 Viertelstunden zählen zum Tag, keine zum Nachbartag
    from bindaems.shared.domain import SlotFlows

    day = date(2026, 10, 25)
    day_slots = local_day_slots(day)
    assert len(day_slots) == 100
    for at in [day_slots[0] - SLOT, *day_slots, day_slots[-1] + SLOT]:
        ledger.ingest(
            SlotFlows(slot_start=at, covered_s=900.0, flows_wh={"grid>house": 100.0}, counters={}),
            "stream",
        )
    influx.set_counters({IMP: {midnight(day): 500.0, midnight(day + timedelta(days=1)): 510.0}})
    assert await checks.run(day, day) == 1
    result = by_signal(checks.checks(day, day))["import"]
    assert result.ledger_kwh == pytest.approx(10.0) and result.counter_kwh == pytest.approx(10.0)
    assert result.deviation_pct == pytest.approx(0.0) and not result.flagged


async def test_run_on_the_92_slot_day_counts_every_slot(checks, ledger, influx: FakeInflux) -> None:
    # 29.03.2026 hat 23 Stunden: 92 Viertelstunden zählen zum Tag, keine zum Nachbartag
    from bindaems.shared.domain import SlotFlows

    day = date(2026, 3, 29)
    day_slots = local_day_slots(day)
    assert len(day_slots) == 92
    for at in [day_slots[0] - SLOT, *day_slots, day_slots[-1] + SLOT]:
        ledger.ingest(
            SlotFlows(slot_start=at, covered_s=900.0, flows_wh={"grid>house": 100.0}, counters={}),
            "stream",
        )
    influx.set_counters({IMP: {midnight(day): 500.0, midnight(day + timedelta(days=1)): 509.2}})
    assert await checks.run(day, day) == 1
    result = by_signal(checks.checks(day, day))["import"]
    assert result.ledger_kwh == pytest.approx(9.2) and result.counter_kwh == pytest.approx(9.2)
    assert result.deviation_pct == pytest.approx(0.0) and not result.flagged


async def test_run_leaves_the_neighbouring_days_alone(checks, influx: FakeInflux) -> None:
    before, after = DAY - timedelta(days=1), DAY + timedelta(days=1)
    influx.set_counters(
        counters(
            {
                before: {IMP: 1.0},
                DAY: {IMP: 2.0},
                after: {IMP: 3.0},
                after + timedelta(days=1): {IMP: 4.0},
            }
        )
    )
    assert await checks.run(before, after) == 3
    stored = {(c.day, c.signal): c for c in checks.checks(before, after)}
    influx.set_counters(counters({DAY: {IMP: 2.0}, after: {IMP: 9.0}}))
    assert await checks.run(DAY, DAY) == 1
    again = {(c.day, c.signal): c for c in checks.checks(before, after)}
    assert again[(DAY, "import")].counter_kwh == pytest.approx(7.0)
    for day in (before, after):
        for signal in ("import", "export", "pv", "wb:evcs", "wb:twc"):
            assert again[(day, signal)] == stored[(day, signal)]
    assert stored[(after, "import")].counter_kwh == pytest.approx(1.0)


async def test_run_with_first_after_last_does_nothing(checks, influx: FakeInflux) -> None:
    assert await checks.run(DAY, DAY - timedelta(days=1)) == 0
    assert influx.queries == []
    assert checks.checks(DAY - timedelta(days=1), DAY) == []


async def test_run_failure_keeps_old_rows(checks, influx: FakeInflux) -> None:
    influx.set_counters(counters({DAY: {IMP: 1.0}, DAY + timedelta(days=1): {IMP: 2.0}}))
    await checks.run(DAY, DAY)
    influx.error = InfluxQueryError("InfluxDB nicht erreichbar")
    with pytest.raises(InfluxQueryError):
        await checks.run(DAY, DAY)
    assert by_signal(checks.checks(DAY, DAY))["import"].counter_kwh == pytest.approx(1.0)


async def test_run_covers_estimated_slots(checks, ledger, influx: FakeInflux) -> None:
    from bindaems.shared.domain import SlotFlows

    start = local_day_slots(DAY)[0]
    ledger.ingest(SlotFlows(start, 0.0, {"grid>house": 2000.0}, {}), "estimate")
    influx.set_counters(counters({DAY: {IMP: 10.0}, DAY + timedelta(days=1): {IMP: 12.0}}))
    await checks.run(DAY, DAY)
    result = by_signal(checks.checks(DAY, DAY))["import"]
    assert result.ledger_kwh == pytest.approx(2.0) and not result.flagged


def test_checks_are_empty_before_the_first_run(checks) -> None:
    assert checks.checks(DAY, DAY) == []
    assert checks.totals([]) == []


def make(day: date, signal: str, ledger_kwh: float, counter_kwh: float | None, **kw) -> DayCheck:
    deviation = (ledger_kwh - counter_kwh) / counter_kwh * 100 if counter_kwh else None
    return DayCheck(
        day, signal, ledger_kwh, counter_kwh, deviation, kw.get("flagged", False), kw.get("note")
    )


def test_totals_skip_unchecked_days(checks) -> None:
    d1, d2, d3 = DAY, DAY + timedelta(days=1), DAY + timedelta(days=2)
    rows = [
        make(d1, "import", 10.2, 10.0),
        make(d2, "import", 99.0, None, note="Zählerstand fehlt"),
        make(d3, "import", 5.0, 5.0),
        make(d1, "export", 1.0, None, note="Zähler zurückgesetzt"),
    ]
    totals = checks.totals(rows)
    assert [t["signal"] for t in totals] == ["import", "export"]
    imp = totals[0]
    assert imp["ledger_kwh"] == pytest.approx(15.2) and imp["counter_kwh"] == pytest.approx(15.0)
    assert imp["deviation_pct"] == pytest.approx(0.2 / 15.0 * 100)
    exp = totals[1]
    assert exp["ledger_kwh"] == 0.0 and exp["counter_kwh"] is None and exp["deviation_pct"] is None


def test_totals_without_any_counter_day_keep_one_entry_per_signal(checks) -> None:
    # Diese Form wertet das UI aus (Karte „Abgleich“): Ein Signal ohne Tag mit Zählerwert steht
    # mit ``ledger_kwh 0.0`` und ``counter_kwh None`` da, ``totals`` ist also nie leer.
    signals = ["import", "export", "pv", "wb:evcs"]
    rows = [
        make(DAY + timedelta(days=n), signal, 3.0, None, note=NOTE_MISSING)
        for n in range(3)
        for signal in signals
    ]
    assert checks.totals(rows) == [
        {"signal": signal, "ledger_kwh": 0.0, "counter_kwh": None, "deviation_pct": None}
        for signal in signals
    ]


def test_totals_with_zero_counter_have_no_deviation(checks) -> None:
    [total] = checks.totals([make(DAY, "wb:evcs", 0.0, 0.0, note="Zähler zeigt 0 kWh")])
    assert total["counter_kwh"] == 0.0 and total["deviation_pct"] is None
