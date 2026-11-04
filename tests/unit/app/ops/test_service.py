"""Tests des Dienstes für Auffälligkeiten (Betrieb im Blick, Task 4)."""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, insert, select, update
from tests.app_helpers import StopScheduler, ingest, record_then_stop
from tests.plan_helpers import at, sample_inputs, sample_plan
from tests.unit.shared.test_decisions import decision

from bindaems.app.db.schema import (
    charging_session,
    ledger_check,
    ops_day,
    ops_finding,
    planned_charge,
)
from bindaems.app.history.influx import InfluxQueryError, Series
from bindaems.app.ops.report import DaySummary
from bindaems.app.ops.service import OPS_NIGHTLY_AT, actuator_labels, day_summary
from bindaems.shared.decisions import DecisionResult
from bindaems.shared.influx.lineprotocol import epoch_ms

DAY = date(2026, 10, 8)  # Sommerzeit, lokal = UTC + 2; die Uhr steht am 9. 10. 10:00 lokal
SENT = datetime(2026, 10, 9, 5, 30, tzinfo=UTC)
SUMMARY = DaySummary(14.2, 9.8, 4.4, 0.0, 3.12, 1.85, 9.1)


def utc(clock: str, day: str = "2026-10-08") -> datetime:
    return at(f"{day}T{clock}:00+00:00")


def soc_series(values: dict[str, float]) -> Series:
    """Antwort von InfluxDB: kleinster SOC je Viertelstunde (``HH:MM`` UTC am 8. 10.)."""
    rows: list[list[object]] = [[epoch_ms(utc(clock)), v] for clock, v in values.items()]
    return Series("soc", {}, ["time", "v"], rows)


def grid_charge(ledger, plan_store) -> None:
    """Zwei Slots Netzladen um 21:30 und 21:45 lokal, der Plan sah kein Netzladen vor."""
    for clock in ("19:30", "19:45"):
        ingest(ledger, utc(clock), {"grid>battery": 2200.0})
    plan = sample_plan("2026-10-08T05:00:00+00:00", ["19:30", "19:45"])
    plan_store.save(
        plan.model_copy(update={"valid_until": utc("05:00", "2026-10-09")}), sample_inputs()
    )


def stored_count(engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(ops_finding)).scalar_one())


async def test_run_day_stores_and_replaces(
    findings, ledger, plan_store, influx, engine, clock
) -> None:
    grid_charge(ledger, plan_store)
    influx.series = [soc_series({"19:30": 30.0, "19:45": 31.0})]
    assert findings.day(DAY) is None  # noch nicht gerechnet
    first = await findings.run_day(DAY)
    assert "grid_charge_unplanned" in [item.rule for item in first]
    [query] = influx.queries
    assert 'SELECT min("pct") AS "v" FROM "raw"."soc"' in query
    assert "\"id\"='battery'" in query and "GROUP BY time(900s)" in query
    assert findings.day(DAY) == first
    count = stored_count(engine)
    assert count == len(first)
    with engine.begin() as conn:
        conn.execute(update(ops_day).values(report_sent_at=SENT))
    clock.advance(timedelta(hours=1))
    second = await findings.run_day(DAY)
    assert second == first
    assert stored_count(engine) == count  # ersetzt, nicht verdoppelt
    with engine.connect() as conn:
        row = conn.execute(select(ops_day)).mappings().one()
    assert (row["day"], row["computed_at"], row["report_sent_at"]) == (
        "2026-10-08",
        clock.now(),
        SENT,
    )


@pytest.mark.parametrize("broken", ["no_reader", "error"])
async def test_run_day_without_influx_gives_not_checked(make_findings, influx, broken: str) -> None:
    if broken == "no_reader":
        service = make_findings(reader=None)
    else:
        influx.error = InfluxQueryError("InfluxDB nicht erreichbar")
        service = make_findings()
    found = await service.run_day(DAY)
    assert [(item.rule, item.text) for item in found] == [
        ("not_checked", "Akku-SOC nicht geprüft: InfluxDB nicht erreichbar.")
    ]
    assert service.day(DAY) == found


async def test_run_day_collects_the_inputs_of_the_day(
    findings, ledger, decision_store, engine, settings_service
) -> None:
    # Monatsspitze: vorher 4 kW am 2. 10., am Tag 12 kW; der Slot am 9. 10. zählt nicht als vorher
    current = settings_service.current()
    settings_service.update(
        current.settings.model_copy(update={"wallbox_names": {"evcs": "Garage"}}),
        base_version=current.version,
        actor="test",
        source="ui",
    )
    ingest(ledger, utc("10:00", "2026-10-02"), {"grid>house": 1000.0})
    ingest(ledger, utc("10:00"), {"grid>house": 2000.0, "grid>wallbox.twc": 1000.0})
    ingest(ledger, utc("06:00", "2026-10-09"), {"grid>house": 4000.0})
    decision_store.add(
        [
            decision(id="f1", ts=utc("09:00"), actuator="victron", result=DecisionResult.ERROR),
            decision(id="f2", ts=utc("09:15"), actuator="victron", result=DecisionResult.TIMEOUT),
            decision(
                id="f3",
                ts=utc("09:00", "2026-10-09"),
                actuator="victron",
                result=DecisionResult.ERROR,
            ),
        ]
    )
    with engine.begin() as conn:
        session_id = conn.execute(
            insert(charging_session)
            .returning(charging_session.c.id)
            .values(wallbox="twc", vehicle="tesla", start=utc("00:00"), last_seen=utc("00:00"))
        ).scalar_one()
        conn.execute(
            insert(planned_charge).values(
                session_id=session_id,
                vehicle="tesla",
                target_soc_pct=80.0,
                departure_at=utc("05:00"),
                started_at=utc("00:00"),
                start_soc_pct=40.0,
                start_soc_source="measured",
                feasible=True,
                end_soc_pct=61.0,
                outcome="missed",
            )
        )
        for day, signal, flagged in (  # Signale wie in ``LedgerChecks``
            ("2026-10-08", "import", True),
            ("2026-10-08", "export", False),
            ("2026-10-08", "wb:evcs", True),
            ("2026-10-08", "wb:twc", False),
            ("2026-10-07", "pv", True),
        ):
            conn.execute(
                insert(ledger_check).values(
                    day=day,
                    signal=signal,
                    ledger_kwh=10.0,
                    counter_kwh=10.5,
                    deviation_pct=4.2,
                    flagged=flagged,
                    checked_at=utc("01:00", "2026-10-09"),
                )
            )
    found = await findings.run_day(DAY)
    assert [(item.rule, item.text) for item in found] == [
        (
            "month_peak",
            "Neue Monatsspitze 12,0 kW um 12:00 (vorher 4,0 kW): "
            "Haus 8,0 kW, Wallbox Wall Connector 4,0 kW.",
        ),
        ("target_missed", "Tesla Model 3 hat das Ziel 80 % um 07:00 verfehlt (erreicht: 61 %)."),
        ("command_failures", "2 Befehle an Victron gescheitert."),
        ("counter_check", "Abrechnung und Zähler weichen bei Netzbezug um 4,2 % ab."),
        ("counter_check", "Abrechnung und Zähler weichen bei Wallbox Garage um 4,2 % ab."),
    ]
    checks = [item.data["signal"] for item in found if item.rule == "counter_check"]
    assert checks == ["import", "wb:evcs"]  # der Schlüssel bleibt in den Daten


def test_actuator_labels_from_health() -> None:
    health = {
        "actuators": [
            {"id": "victron", "label": "Victron"},
            {"id": "wallbox.evcs", "label": None},
            {"label": "ohne id"},
            "kaputt",
        ]
    }
    assert actuator_labels(health) == {"victron": "Victron"}
    assert actuator_labels(None) == {}
    assert actuator_labels({"actuators": None}) == {}


def spy_days(service, monkeypatch, *, failing: date | None = None) -> list[date]:
    days: list[date] = []

    async def run_day(day: date) -> list:
        days.append(day)
        if day == failing:
            raise RuntimeError("Datenbank gesperrt")
        return []

    monkeypatch.setattr(service, "run_day", run_day)
    return days


async def test_run_at_start_fills_missing_days_only(make_findings, engine, monkeypatch) -> None:
    sleeps: list[float] = []
    service = make_findings(sleep=record_then_stop(sleeps, 1))
    with engine.begin() as conn:
        for day in ("2026-10-05", "2026-10-07", "2026-10-01"):
            conn.execute(insert(ops_day).values(day=day, computed_at=SENT))
    days = spy_days(service, monkeypatch, failing=date(2026, 10, 3))
    with pytest.raises(StopScheduler):
        await service.run()
    # die letzten 7 Tage bis gestern, ohne die schon gerechneten; ein Fehler hält nichts auf
    assert days == [
        date(2026, 10, 2),
        date(2026, 10, 3),
        date(2026, 10, 4),
        date(2026, 10, 6),
        date(2026, 10, 8),
    ]


async def test_nightly_runs_at_0305_local(make_findings, engine, clock, monkeypatch) -> None:
    assert OPS_NIGHTLY_AT == (3, 5)
    clock.advance(datetime(2026, 10, 24, 22, 0, tzinfo=UTC) - clock.now())  # 25. 10. 00:00 MESZ
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            raise StopScheduler
        clock.advance(seconds)

    service = make_findings(sleep=sleep)
    with engine.begin() as conn:
        for back in range(1, 8):
            day = date(2026, 10, 25) - timedelta(days=back)
            conn.execute(insert(ops_day).values(day=day.isoformat(), computed_at=SENT))
    days = spy_days(service, monkeypatch)
    with pytest.raises(StopScheduler):
        await service.run()
    # 03:05 MEZ am 25. 10. ist 02:05 UTC: 4 h 5 min nach dem Start (die Uhr wird zurückgestellt)
    assert sleeps == [4 * 3600 + 300.0, 86400.0, 86400.0]
    assert days == [date(2026, 10, 24), date(2026, 10, 25)]


def _finding_row(day: str) -> dict[str, object]:
    return {
        "day": day,
        "rule": "not_checked",
        "level": "info",
        "text": "Akku-SOC nicht geprüft: InfluxDB nicht erreichbar.",
        "data": {},
    }


def test_cleanup_keeps_400_days(findings, engine) -> None:
    # heute ist der 9. 10. 2026; 400 Tage davor ist der 4. 9. 2025
    with engine.begin() as conn:
        for day in ("2025-09-03", "2025-09-04", "2026-10-08"):
            conn.execute(insert(ops_day).values(day=day, computed_at=SENT))
            conn.execute(insert(ops_finding).values(**_finding_row(day)))
        conn.execute(insert(ops_finding).values(**_finding_row("2025-09-03")))
    assert findings.cleanup() == 2
    with engine.connect() as conn:
        days = conn.execute(select(ops_day.c.day).order_by(ops_day.c.day)).scalars().all()
        rows = conn.execute(select(ops_finding.c.day).order_by(ops_finding.c.day)).scalars().all()
    assert days == ["2025-09-04", "2026-10-08"]
    assert rows == ["2025-09-04", "2026-10-08"]
    assert findings.cleanup(keep_days=0) == 2


def test_recent_lists_newest_days_first(findings, engine) -> None:
    # heute ist der 9. 10. 2026: ``recent(2)`` sind der 8. und der 7. 10.
    with engine.begin() as conn:
        for day in ("2026-10-06", "2026-10-07", "2026-10-08"):
            conn.execute(insert(ops_day).values(day=day, computed_at=SENT))
            conn.execute(insert(ops_finding).values(**_finding_row(day)))
        conn.execute(insert(ops_day).values(day="2026-10-09", computed_at=SENT))
    assert [item.day for item in findings.recent(2)] == [date(2026, 10, 8), date(2026, 10, 7)]
    assert findings.day(date(2026, 10, 9)) == []  # gerechnet, ohne Auffälligkeiten


# --- Tagesbericht (Task 5) ----------------------------------------------------------------------


def set_ops(settings_service, **changes: Any) -> None:
    current = settings_service.current()
    ops = current.settings.ops.model_copy(update=changes)
    settings_service.update(
        current.settings.model_copy(update={"ops": ops}),
        base_version=current.version,
        actor="test",
        source="ui",
    )


def report_sent_at(engine) -> datetime | None:
    with engine.connect() as conn:
        return conn.execute(
            select(ops_day.c.report_sent_at).where(ops_day.c.day == DAY.isoformat())
        ).scalar_one_or_none()


def warn_day(engine) -> None:
    """Gestern hat eine Warnung."""
    with engine.begin() as conn:
        conn.execute(insert(ops_day).values(day=DAY.isoformat(), computed_at=SENT))
        conn.execute(
            insert(ops_finding).values(
                day=DAY.isoformat(),
                rule="grid_charge_unplanned",
                level="warn",
                text="Akku lud nachts.",
                data={},
            )
        )


async def test_report_sent_after_report_at_once(make_sender, ha_calls, engine, clock) -> None:
    sender = make_sender()
    clock.advance(datetime(2026, 10, 9, 5, 29, tzinfo=UTC) - clock.now())  # 07:29 lokal
    await sender.check()
    assert ha_calls.reports == []
    clock.advance(timedelta(minutes=1))  # 07:30
    await sender.check()
    assert len(ha_calls.reports) == 1
    assert ha_calls.reports[0].startswith("Donnerstag 8.10.: Netzbezug 14,2 kWh (geplant 9,8)")
    assert report_sent_at(engine) == clock.now()
    clock.advance(timedelta(minutes=5))
    await sender.check()
    assert len(ha_calls.reports) == 1


async def test_report_sent_once_per_day_across_restart(
    make_sender, ha_calls, engine, clock
) -> None:
    await make_sender().check()
    assert len(ha_calls.reports) == 1
    await make_sender().check()  # Neustart: gleiche Datenbank
    assert len(ha_calls.reports) == 1


async def test_report_computes_a_missing_day_first(make_sender, ha_calls, engine) -> None:
    await make_sender().check()
    assert ha_calls.reports[0] == (
        "Donnerstag 8.10.: Netzbezug 14,2 kWh (geplant 9,8), "
        "davon Netzladen 4,4 kWh (geplant 0,0). Kosten 3,12 €, Ersparnis 1,85 €. "
        "Monatsspitze 9,1 kW. Keine Auffälligkeiten. Betriebscheck jetzt: alles in Ordnung."
    )
    assert report_sent_at(engine) is not None


async def test_findings_mode_skips_quiet_day(
    make_sender, ha_calls, engine, settings_service
) -> None:
    set_ops(settings_service, report="findings")
    with engine.begin() as conn:
        conn.execute(insert(ops_day).values(day=DAY.isoformat(), computed_at=SENT))
    await make_sender().check()
    assert ha_calls.reports == []
    assert report_sent_at(engine) is None


async def test_findings_mode_sends_on_warning(
    make_sender, ha_calls, engine, settings_service
) -> None:
    set_ops(settings_service, report="findings")
    warn_day(engine)
    await make_sender().check()
    assert len(ha_calls.reports) == 1
    assert "Akku lud nachts." in ha_calls.reports[0]


async def test_off_sends_nothing(make_sender, ha_calls, engine, settings_service) -> None:
    set_ops(settings_service, report="off")
    warn_day(engine)
    await make_sender().check()
    assert ha_calls.calls == []
    assert report_sent_at(engine) is None


async def test_report_at_follows_the_setting(make_sender, ha_calls, settings_service) -> None:
    set_ops(settings_service, report_at="10:01")  # die Uhr steht auf 10:00
    await make_sender().check()
    assert ha_calls.reports == []


async def test_without_notifier_nothing_is_sent(make_sender, engine) -> None:
    warn_day(engine)
    await make_sender(notifier=None).check()
    assert report_sent_at(engine) is None


async def test_failed_send_retries_after_15_min(make_sender, ha_calls, engine, clock) -> None:
    warn_day(engine)
    sender = make_sender()
    ha_calls.failing = True
    await sender.check()
    assert report_sent_at(engine) is None
    ha_calls.failing = False
    clock.advance(timedelta(minutes=14))
    await sender.check()
    assert ha_calls.reports == []  # noch zu früh
    clock.advance(timedelta(minutes=1))
    await sender.check()
    assert len(ha_calls.reports) == 1
    assert report_sent_at(engine) == clock.now()


async def test_error_in_summary_is_retried(make_sender, ha_calls, engine, clock) -> None:
    warn_day(engine)
    fail = [True]

    def summary(day):
        if fail[0]:
            raise RuntimeError("Abrechnung weg")
        return SUMMARY

    sender = make_sender(summary=summary)
    await sender.check()
    assert report_sent_at(engine) is None
    fail[0] = False
    clock.advance(timedelta(minutes=15))
    await sender.check()
    assert len(ha_calls.reports) == 1


async def test_run_checks_every_minute(make_sender, engine, clock, ha_calls) -> None:
    warn_day(engine)
    sleeps: list[float] = []
    sender = make_sender()
    sender._sleep = record_then_stop(sleeps, 2)  # type: ignore[assignment]
    with pytest.raises(StopScheduler):
        await sender.run()
    assert sleeps == [60.0, 60.0]
    assert len(ha_calls.reports) == 1


def test_day_summary_compares_planned_and_actual(findings, ledger, plan_store, clock) -> None:
    grid_charge(ledger, plan_store)  # zwei Slots mit 2200 Wh Netzladen, Plan ohne Netzladen
    stats = StatsStub()
    summary = day_summary(DAY, stats=stats, ledger=ledger, plans=plan_store, now=clock.now())
    assert summary.grid_charge_kwh == pytest.approx(4.4)
    assert summary.planned_grid_charge_kwh == 0.0
    assert summary.planned_import_kwh is not None
    assert (summary.net_eur, summary.savings_eur) == (3.12, 1.85)
    assert summary.month_peak_kw == pytest.approx(8.8)  # 2200 Wh je Viertelstunde


def test_day_summary_without_plan_uses_statistics(ledger, plan_store, clock) -> None:
    ingest(ledger, utc("19:30"), {"grid>battery": 1000.0, "grid>house": 500.0})
    summary = day_summary(DAY, stats=StatsStub(), ledger=ledger, plans=plan_store, now=clock.now())
    assert summary.import_kwh == 14.2  # aus der Statistik
    assert summary.grid_charge_kwh == pytest.approx(1.0)
    assert (summary.planned_import_kwh, summary.planned_grid_charge_kwh) == (None, None)
    assert summary.month_peak_kw == pytest.approx(6.0)  # 1500 Wh je Viertelstunde


class StatsStub:
    def period(self, period: str, day: date) -> dict:
        return {
            "energy": {"import_kwh": 14.2},
            "money": {"net_eur": 3.12, "savings_no_plant_eur": 1.85},
        }


async def test_findings_mode_decides_a_quiet_day_once(
    make_sender, ha_calls, engine, settings_service, monkeypatch
) -> None:
    set_ops(settings_service, report="findings")
    with engine.begin() as conn:
        conn.execute(insert(ops_day).values(day=DAY.isoformat(), computed_at=SENT))
    sender = make_sender()
    reads = []
    original = sender._sent
    monkeypatch.setattr(sender, "_sent", lambda day: reads.append(day) or original(day))
    for _ in range(3):
        await sender.check()
    assert len(reads) == 1  # die anderen Minuten merken sich „ruhig“
    set_ops(settings_service, report="daily")  # anderer Modus: neu entscheiden
    await sender.check()
    assert len(ha_calls.reports) == 1


async def test_report_reads_the_findings_off_the_event_loop(
    make_sender, findings, ha_calls, engine, monkeypatch
) -> None:
    # die Datenbank blockiert: wie die übrigen Abfragen im Thread, nicht in der Ereignisschleife
    warn_day(engine)
    loop_thread = threading.current_thread()
    threads: list[threading.Thread] = []
    read = findings.day

    def day(value: date) -> Any:
        threads.append(threading.current_thread())
        return read(value)

    monkeypatch.setattr(findings, "day", day)
    await make_sender().check()
    assert len(ha_calls.reports) == 1
    assert threads and loop_thread not in threads
