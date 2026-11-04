"""Auffälligkeiten sammeln, ablegen und abfragen (Betrieb im Blick).

``FindingsService`` liest die Eingaben eines lokalen Tages (Abrechnung, Pläne, Akku-SOC aus
InfluxDB, gescheiterte Befehle, verfehlte Ladeziele, markierte Zählervergleiche), ruft ``find``
und ersetzt die Zeilen des Tages in ``ops_finding``; ``ops_day`` merkt sich die gerechneten Tage.
Beim Start holt er die fehlenden der letzten 7 Tage nach, dann rechnet er täglich um 03:05 Uhr
Ortszeit den Vortag. ``ReportSender`` schickt den Tagesbericht über Home Assistant.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Final

import structlog
from sqlalchemy import Engine, delete, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from bindaems.app.charging.planned import PlannedCharges
from bindaems.app.control.decisions import DecisionStore
from bindaems.app.db.schema import ledger_check, ops_day, ops_finding
from bindaems.app.ha.notify import HaNotifier
from bindaems.app.history.influx import InfluxQueryError, InfluxReader, quote_ident, quote_str
from bindaems.app.history.series import SeriesSpec, build_catalog, choose_rp_and_step
from bindaems.app.ledger.service import LedgerService
from bindaems.app.ops.check import OpsCheck
from bindaems.app.ops.findings import DayInputs, Finding, find
from bindaems.app.ops.report import DaySummary, report_text
from bindaems.app.planning.review import review
from bindaems.app.planning.store import PlanStore
from bindaems.app.schedule import next_local
from bindaems.app.settings.service import CurrentSettings
from bindaems.app.stats.service import StatsService
from bindaems.shared.config import Config
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, Clock, ensure_utc, local_day_slots

log = structlog.get_logger(__name__)

OPS_NIGHTLY_AT: Final = (3, 5)  # Ortszeit, nach dem Abgleich der Abrechnung um 02:45
START_DAYS: Final = 7  # beim Start: fehlende Tage der letzten 7 bis gestern
KEEP_DAYS: Final = 400
REPORT_INTERVAL_S: Final = 60.0  # so oft prüft ``ReportSender``, ob der Bericht fällig ist
REPORT_RETRY: Final = timedelta(minutes=15)  # nach einem gescheiterten Senden
SOC_SERIES: Final = "soc.battery"
SOC_STEP_S: Final = 900  # je Viertelstunde der kleinste SOC

Labels = Callable[[], Mapping[str, str]]


def actuator_labels(health: Mapping[str, Any] | None) -> dict[str, str]:
    """Aktor-ID → Bezeichnung aus ``health["actuators"]``; Einträge ohne beides fehlen."""
    actuators = health.get("actuators") if health is not None else None
    if not isinstance(actuators, list):
        return {}
    labels: dict[str, str] = {}
    for actuator in actuators:
        if not isinstance(actuator, Mapping):
            continue
        id_, label = actuator.get("id"), actuator.get("label")
        if isinstance(id_, str) and isinstance(label, str) and label:
            labels[id_] = label
    return labels


def soc_min_query(spec: SeriesSpec, rp: str, start: datetime, end: datetime) -> str:
    """Kleinster Wert der Reihe je Viertelstunde (nach dem Muster von ``build_query``)."""
    conditions = [
        f"{quote_ident(key)}={quote_str(value)}" for key, value in sorted(spec.tags.items())
    ]
    conditions += [f"time >= {epoch_ms(start)}ms", f"time < {epoch_ms(end)}ms"]
    # nur Katalogreihen; Bezeichner und Werte sind maskiert
    return (
        f'SELECT min({quote_ident(spec.field)}) AS "v" '  # noqa: S608
        f"FROM {quote_ident(rp)}.{quote_ident(spec.measurement)} "
        f"WHERE {' AND '.join(conditions)} GROUP BY time({SOC_STEP_S}s) fill(none)"
    )


def finding_json(item: Finding) -> dict[str, Any]:
    return {
        "rule": item.rule,
        "level": item.level,
        "start": None if item.start is None else ensure_utc(item.start).isoformat(),
        "end": None if item.end is None else ensure_utc(item.end).isoformat(),
        "text": item.text,
        "link": item.link,
        "data": dict(item.data),
    }


class FindingsService:
    def __init__(
        self,
        engine: Engine,
        clock: Clock,
        cfg: Config,
        ledger: LedgerService,
        plans: PlanStore,
        decisions: DecisionStore,
        planned: PlannedCharges,
        reader: InfluxReader | None,
        labels: Labels,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._cfg = cfg
        self._ledger = ledger
        self._plans = plans
        self._decisions = decisions
        self._planned = planned
        self._reader = reader
        self._labels = labels
        self._sleep = sleep
        self._soc_spec = build_catalog(cfg)[SOC_SERIES]

    # --- Rechnen ----------------------------------------------------------------------------

    async def run_day(self, day: date) -> list[Finding]:
        """Rechnet die Auffälligkeiten des Ortstags ``day`` und ersetzt seine Zeilen."""
        start, end = _bounds(day)
        soc_min = await self._soc_min(start, end)
        labels = dict(self._labels())  # in der Ereignisschleife: liest den letzten Health
        inputs = await asyncio.to_thread(self._inputs, day, start, end, soc_min, labels)
        findings = find(inputs)
        await asyncio.to_thread(self._store, day, findings)
        return findings

    def _inputs(
        self,
        day: date,
        start: datetime,
        end: datetime,
        soc_min: Mapping[datetime, float] | None,
        labels: Mapping[str, str],
    ) -> DayInputs:
        peak = self._ledger.month_peak_before(day)
        return DayInputs(
            day=day,
            slots=self._ledger.slots(start, end),
            plans=self._plans.plans_between(start, end),
            soc_min=soc_min,
            reserve_soc_pct=self._cfg.battery.reserve_soc_pct,
            previous_peak_kw=None if peak is None else peak.kw,
            failures=self._decisions.failures(start, end),
            actuator_labels=labels,
            missed=self._planned.missed(day),
            flagged_checks=self._flagged(day),
        )

    def _flagged(self, day: date) -> list[tuple[str, float | None]]:
        query = (
            select(ledger_check.c.signal, ledger_check.c.deviation_pct)
            .where(ledger_check.c.day == day.isoformat(), ledger_check.c.flagged.is_(True))
            .order_by(ledger_check.c.signal)
        )
        with self._engine.connect() as conn:
            return [(row.signal, row.deviation_pct) for row in conn.execute(query)]

    async def _soc_min(self, start: datetime, end: datetime) -> dict[datetime, float] | None:
        """Kleinster Akku-SOC je Slotbeginn; ``None`` ohne Reader oder bei einem Fehler."""
        if self._reader is None:
            return None
        rp, _ = choose_rp_and_step(self._soc_spec, start, end, self._clock.now())
        try:
            result = await self._reader.query(soc_min_query(self._soc_spec, rp, start, end))
        except InfluxQueryError as exc:
            log.warning("Akku-SOC für die Auffälligkeiten nicht lesbar", error=str(exc))
            return None
        values: dict[datetime, float] = {}
        for series in result:
            if "time" not in series.columns or "v" not in series.columns:
                continue
            time_index, value_index = series.columns.index("time"), series.columns.index("v")
            for row in series.values:
                stamp, value = row[time_index], row[value_index]
                if isinstance(stamp, int | float) and isinstance(value, int | float):
                    values[datetime.fromtimestamp(stamp / 1000, UTC)] = float(value)
        return values

    def _store(self, day: date, findings: list[Finding]) -> None:
        """Ersetzt die Zeilen des Tages in einer Transaktion; ``report_sent_at`` bleibt."""
        key = day.isoformat()
        now = self._clock.now()
        with self._engine.begin() as conn:
            conn.execute(delete(ops_finding).where(ops_finding.c.day == key))
            if findings:
                conn.execute(
                    insert(ops_finding),
                    [
                        {
                            "day": key,
                            "rule": item.rule,
                            "level": item.level,
                            "start": item.start,
                            "end": item.end,
                            "text": item.text,
                            "link": item.link,
                            "data": dict(item.data),
                        }
                        for item in findings
                    ],
                )
            statement = sqlite_insert(ops_day).values(day=key, computed_at=now)
            conn.execute(
                statement.on_conflict_do_update(
                    index_elements=[ops_day.c.day], set_={"computed_at": now}
                )
            )

    # --- Betrieb ----------------------------------------------------------------------------

    async def run(self) -> None:
        """Beim Start die fehlenden Tage der letzten 7 bis gestern, dann täglich um 03:05 Uhr
        Ortszeit den Vortag. Ein Fehler wird protokolliert, der Rhythmus läuft weiter."""
        today = self._today()
        first = today - timedelta(days=START_DAYS)
        computed = await asyncio.to_thread(self._computed, first, today - timedelta(days=1))
        for back in range(START_DAYS, 0, -1):
            day = today - timedelta(days=back)
            if day not in computed:
                await self._run_logged(day)
        while True:
            now = self._clock.now()
            due = next_local(now, lambda t: (t.hour, t.minute) == OPS_NIGHTLY_AT)
            await self._sleep((due - now).total_seconds())
            await self._run_logged(self._today() - timedelta(days=1))

    async def _run_logged(self, day: date) -> None:
        try:
            found = await self.run_day(day)
            log.info("Auffälligkeiten gerechnet", day=day.isoformat(), findings=len(found))
        except Exception:
            log.exception("Auffälligkeiten nicht gerechnet", day=day.isoformat())

    def _today(self) -> date:
        return self._clock.now().astimezone(LOCAL_TZ).date()

    # --- Abfragen ---------------------------------------------------------------------------

    def _computed(self, first: date, last: date) -> set[date]:
        query = select(ops_day.c.day).where(
            ops_day.c.day >= first.isoformat(), ops_day.c.day <= last.isoformat()
        )
        with self._engine.connect() as conn:
            return {date.fromisoformat(value) for value in conn.execute(query).scalars()}

    def days(self, first: date, last: date) -> dict[date, list[Finding]]:
        """Die gerechneten Tage in ``first … last`` mit ihren Auffälligkeiten (auch leeren)."""
        computed = self._computed(first, last)
        query = (
            select(ops_finding)
            .where(ops_finding.c.day >= first.isoformat(), ops_finding.c.day <= last.isoformat())
            .order_by(ops_finding.c.day, ops_finding.c.id)
        )
        result: dict[date, list[Finding]] = {day: [] for day in computed}
        with self._engine.connect() as conn:
            for row in conn.execute(query):
                result.setdefault(date.fromisoformat(row.day), []).append(_finding(row))
        return result

    def recent(self, days: int) -> list[Finding]:
        """Die Auffälligkeiten der letzten ``days`` Tage bis gestern, neueste Tage zuerst."""
        last = self._today() - timedelta(days=1)
        by_day = self.days(last - timedelta(days=days - 1), last)
        return [item for day in sorted(by_day, reverse=True) for item in by_day[day]]

    def day(self, day: date) -> list[Finding] | None:
        """Die Auffälligkeiten des Tages; ``None``, solange er nicht gerechnet ist."""
        return self.days(day, day).get(day)

    def cleanup(self, keep_days: int = KEEP_DAYS) -> int:
        """Löscht Tage, die älter als ``keep_days`` Tage sind; liefert die Zahl der gelöschten
        Auffälligkeiten."""
        cutoff = (self._today() - timedelta(days=keep_days)).isoformat()
        with self._engine.begin() as conn:
            removed = conn.execute(delete(ops_finding).where(ops_finding.c.day < cutoff))
            conn.execute(delete(ops_day).where(ops_day.c.day < cutoff))
        return int(removed.rowcount)


def _bounds(day: date) -> tuple[datetime, datetime]:
    slots = local_day_slots(day)
    return slots[0], slots[-1] + SLOT


def _finding(row: Any) -> Finding:
    return Finding(
        day=date.fromisoformat(row.day),
        rule=row.rule,
        level=row.level,
        start=row.start,
        end=row.end,
        text=row.text,
        link=row.link,
        data=row.data,
    )


def day_summary(
    day: date,
    *,
    stats: StatsService,
    ledger: LedgerService,
    plans: PlanStore,
    now: datetime,
) -> DaySummary:
    """Zahlen des Tagesberichts über den Ortstag ``day`` (blockierend: Datenbank).

    Kosten, Ersparnis und Netzbezug stehen in der Statistik des Tages. Gibt es Viertelstunden mit
    Plan und Abrechnung, vergleicht ``review`` Netzbezug und Netzladen über genau diese; sonst
    gelten der Netzbezug der Statistik und das Netzladen der Abrechnung, ohne Plan.
    """
    period = stats.period("day", day)
    start, end = _bounds(day)
    slots = ledger.slots(start, end)
    reviewed = review(day, plans.plans_between(start, end), slots, now)
    totals = reviewed["totals"]
    compared = any(
        row["planned"] is not None and row["actual"] is not None for row in reviewed["slots"]
    )
    peak = ledger.month_peak(day)
    if compared:
        actual, planned = totals["actual"], totals["planned"]
        import_kwh, grid_charge = actual["import_kwh"], actual["grid_charge_kwh"]
        planned_import, planned_grid = planned["import_kwh"], planned["grid_charge_kwh"]
    else:
        import_kwh = period["energy"]["import_kwh"]
        grid_charge = sum(slot.flows_wh.get("grid>battery", 0.0) for slot in slots) / 1000.0
        planned_import = planned_grid = None
    return DaySummary(
        import_kwh=import_kwh,
        planned_import_kwh=planned_import,
        grid_charge_kwh=grid_charge,
        planned_grid_charge_kwh=planned_grid,
        net_eur=period["money"]["net_eur"],
        savings_eur=period["money"]["savings_no_plant_eur"],
        month_peak_kw=None if peak is None else peak.kw,
    )


class ReportSender:
    """Schickt den Tagesbericht über gestern an Home Assistant (``HaNotifier.report``).

    Jede Minute: ist es lokal nach ``report_at``, steht ``report_sent_at`` von gestern in
    ``ops_day`` noch leer und ist der Bericht nicht ``off`` (bei ``findings`` nur mit mindestens
    einer Warnung), geht er hinaus und ``report_sent_at`` wird gesetzt: ein Neustart sendet nichts
    doppelt. Ein Fehler beim Senden (oder beim Rechnen des Berichts) wird nach ``REPORT_RETRY``
    wiederholt, längstens bis der Tag wechselt. Ist gestern noch nicht gerechnet, rechnet er ihn
    zuerst. Ohne ``notifier`` (kein Home Assistant) sendet er nichts.
    """

    def __init__(
        self,
        engine: Engine,
        clock: Clock,
        settings: CurrentSettings,
        findings: FindingsService,
        summary: Callable[[date], DaySummary],
        check: Callable[[], OpsCheck],
        notifier: HaNotifier | None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._settings = settings
        self._findings = findings
        self._summary = summary
        self._check = check
        self._notifier = notifier
        self._sleep = sleep
        self._retry_at: datetime | None = None
        # (Tag, Modus, Uhrzeit), für die ein ruhiger Tag im Modus ``findings`` schon feststeht
        self._quiet: tuple[date, str, str] | None = None

    def text(self, day: date) -> str:
        """Der Bericht über ``day`` mit dem Stand von jetzt (blockierend: Datenbank)."""
        return report_text(day, self._summary(day), self._findings.day(day), self._check())

    async def run(self) -> None:
        while True:
            await self.check()
            await self._sleep(REPORT_INTERVAL_S)

    async def check(self) -> None:
        """Ein Durchlauf: sendet den Bericht, wenn er fällig ist. Ein Fehler wird protokolliert
        und nach ``REPORT_RETRY`` wiederholt."""
        try:
            await self._send_if_due()
        except Exception:
            log.exception("Tagesbericht nicht gesendet")
            self._retry_at = self._clock.now() + REPORT_RETRY

    async def _send_if_due(self) -> None:
        mode = self._settings.current().settings.ops
        now = self._clock.now()
        local = now.astimezone(LOCAL_TZ)
        if self._notifier is None or mode.report == "off":
            return
        if local.time() < time.fromisoformat(mode.report_at):
            return
        if self._retry_at is not None and now < self._retry_at:
            return
        day = local.date() - timedelta(days=1)
        if self._quiet == (day, mode.report, mode.report_at):
            return
        if await asyncio.to_thread(self._sent, day):
            return
        found = self._findings.day(day)
        if found is None:
            found = await self._findings.run_day(day)
        if mode.report == "findings" and not any(item.level == "warn" for item in found):
            self._quiet = (day, mode.report, mode.report_at)  # nicht jede Minute neu lesen
            return
        text = await asyncio.to_thread(self.text, day)
        if await self._notifier.report(text):
            self._retry_at = None
            await asyncio.to_thread(self._mark_sent, day, now)
        else:
            self._retry_at = now + REPORT_RETRY

    def _sent(self, day: date) -> bool:
        query = select(ops_day.c.report_sent_at).where(ops_day.c.day == day.isoformat())
        with self._engine.connect() as conn:
            return conn.execute(query).scalar_one_or_none() is not None

    def _mark_sent(self, day: date, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(ops_day).where(ops_day.c.day == day.isoformat()).values(report_sent_at=at)
            )
