"""Geplante Ladungen für die Abnahme „Ziel-SOC“ (Plan 4b, Entscheidung 15, Präzisierung 16).

Je Session in „Geplant“ und Abfahrt eine Zeile in ``planned_charge``. ``record`` läuft nach jeder
Antwort von Health mit den offenen Sessions je Wallbox. Gelöscht wird keine Zeile: Was nicht bis zur
Abfahrt geplant lief, bleibt mit eigenem Ergebnis sichtbar.

- **Beginn:** beim ersten Health, das ein Fahrzeug angesteckt in „Geplant“ an einer Wallbox mit
  offener Session nennt und für die Abfahrt (``planned.departure_at`` nach jetzt) einen Bedarf hat
  (``planned.state`` nicht ``reached`` und ``planned.need_kwh`` über 0). Ohne Bedarf (über dem
  Ziel angesteckt, über die Abfahrt hinaus am Ziel) zählt nichts. Ziel ist das der Absicht in
  Health (``intent``), ohne sie das wirksame aus ``planned``. Ladestand und Quelle zu Beginn kommen
  aus Health; ohne bekannten Wert gilt 0 % „angenommen“. Den ersetzt der erste bekannte Ladestand
  (``measured``, ``estimated``); Beginn (``started_at``) und Machbarkeit gelten dann ab da.
- **Machbar** (``feasible``): Volle Leistung bis zur Abfahrt reichte für den Bedarf, und das
  Ladelimit im Auto liegt nicht unter dem Ziel (nur ohne ``charge_limit_sync``; mit gleicht der
  core es an). Sinkt das Limit später unter das Ziel, bevor es erreicht ist, ist die Ladung nicht
  mehr machbar; eine erreichte bleibt machbar.
- **Erreicht** (``reached_at``): der erste bekannte Ladestand mindestens am Ziel. Ein angenommener
  Ladestand erreicht nie etwas.
- **Ende:**
  - an der Abfahrt: ``reached`` mit ``reached_at`` oder einem bekannten Ladestand des letzten
    Health vor der Abfahrt von mindestens Ziel − 1 Prozentpunkt; ``unknown``, wenn dieser
    Ladestand oder der zu Beginn angenommen ist oder er mehr als ``SOC_STALE`` vor der Abfahrt
    gelesen wurde; sonst ``missed``;
  - endet die Session vorher (Ende in ``charging_session``): ``reached`` mit ``reached_at``, sonst
    ``early``; endet sie danach (die app war aus), wie an der Abfahrt;
  - hat der Tracker die Session nach einer Lücke ohne Health geschlossen (``SESSION_GAP``) und für
    dasselbe Fahrzeug an derselben Wallbox eine neue begonnen, zieht die Zeile in die neue um und
    behält Beginn und Machbarkeit; lag die Abfahrt in der Lücke, ist sie ``unknown``. Hat er sie
    wegen einer Lücke geschlossen (ohne Health oder mit unbekanntem Steckzustand) und keine neue
    begonnen, bleibt die Zeile offen, bis eine neue beginnt oder die Abfahrt vorbei ist
    (``unknown``). Ein vorher erreichtes Ziel bleibt in allen Fällen ``reached``;
  - ``changed``: Abfahrt oder Ziel ändern sich vor der Abfahrt (dann kann eine neue beginnen), oder
    die Absicht wechselt auf PV, Min+PV oder Aus. Sofort und Boost lassen die Zeile offen, sie wird
    an der Abfahrt beurteilt; ein unbekannter Modus (core startet neu) ändert nichts.
- **Übernahme** (Plan 5b, Präzisierung 9): Ändern sich Ziel oder Abfahrt einer Zeile, deren Beginn
  (``started_at``) höchstens ``AUTO_ADOPT`` zurückliegt, und hat die Automatik das Auto in den
  letzten 2 min gesetzt (``recently_set``), übernimmt die Zeile Ziel und Abfahrt und rechnet die
  Machbarkeit neu wie eine neue Zeile, statt mit ``changed`` zu enden. Ein erreichtes Ziel gilt
  nach einem höheren nicht mehr als erreicht. So entsteht keine „geänderte“ Ladung, wenn nach einer
  Abfahrt, zu der das Auto stecken blieb, sofort die Zeile für die nächste beginnt.

``end_soc_pct``, ``end_soc_source`` und ``end_soc_at`` halten den letzten Ladestand vor der Abfahrt
mit seiner Zeit; gespeichert wird er höchstens jede Minute, so übersteht er einen Neustart.

Die Abnahme zählt die geschlossenen Zeilen nach dem Tag der Abfahrt (``summary``): erreicht von
den machbaren; nicht machbare, vor der Abfahrt abgesteckte, solche ohne Ladestand und geänderte je
für sich.

``PlannedCharges`` liest und schreibt blockierend: aus einem Thread aufrufen.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final

import structlog
from sqlalchemy import Connection, Engine, func, insert, select, update

from bindaems.app.charging.tracker import SESSION_GAP, StoredSession
from bindaems.app.control.intent import _health_vehicles
from bindaems.app.db.schema import charging_session, planned_charge
from bindaems.app.planning.vehicles import full_power_w, need_kwh
from bindaems.shared.config import Config
from bindaems.shared.control import ChargeMode
from bindaems.shared.numbers import finite_number
from bindaems.shared.timeutil import SLOT, Clock, ensure_utc, local_day_slots

log = structlog.get_logger(__name__)

OPEN: Final = "open"
REACHED: Final = "reached"
MISSED: Final = "missed"
EARLY: Final = "early"
UNKNOWN: Final = "unknown"
CHANGED: Final = "changed"

PLANNED: Final = ChargeMode.PLANNED.value
# ein Wechsel auf diese Modi beendet die geplante Ladung; Sofort und Boost laden zur Abfahrt weiter
LEAVING_MODES: Final = frozenset(
    {ChargeMode.PV.value, ChargeMode.MIN_PV.value, ChargeMode.OFF.value}
)
REACHED_STATE: Final = "reached"  # ``planned.state`` am Ziel
ASSUMED: Final = "assumed"
KNOWN_SOURCES: Final = frozenset({"measured", "estimated"})
REACHED_TOLERANCE_PP: Final = 1.0  # an der Abfahrt zählt Ziel − 1 Prozentpunkt als erreicht
SAVE_EVERY: Final = timedelta(minutes=1)  # höchstens so oft den letzten Ladestand speichern
# ein Ladestand, der so lange vor der Abfahrt gelesen wurde, gilt an der Abfahrt als unbekannt
SOC_STALE: Final = timedelta(minutes=15)
# so lange nach ihrem Beginn übernimmt eine Zeile Ziel und Abfahrt der Automatik (Präzisierung 9)
AUTO_ADOPT: Final = timedelta(minutes=10)

RecentlySet = Callable[[str, datetime], bool]


def _never(key: str, now: datetime) -> bool:
    """Ohne Automatik hat sie nie etwas gesetzt."""
    return False


@dataclass(frozen=True)
class PlannedSummary:
    """Geplante Ladungen eines Zeitraums nach dem Tag der Abfahrt.

    ``feasible`` sind die machbaren mit bekanntem Ergebnis, davon ``reached`` erreicht und
    ``missed`` verfehlt; ``infeasible`` die nicht machbaren mit bekanntem Ergebnis; ``early`` vor
    der Abfahrt abgesteckt, ``unknown`` ohne Ladestand, ``changed`` vor der Abfahrt geändert oder
    verlassen. Offene zählen nicht.
    """

    reached: int
    feasible: int
    missed: int
    infeasible: int
    early: int
    unknown: int
    changed: int


@dataclass(frozen=True)
class OpenCharge:
    """Beginn und Abfahrt der offenen Zeile eines Fahrzeugs."""

    started_at: datetime
    departure_at: datetime


def open_charges(engine: Engine) -> dict[str, OpenCharge]:
    """Die offenen Zeilen je Fahrzeug aus der Datenbank (blockiert: aus einem Thread aufrufen)."""
    query = (
        select(planned_charge.c.vehicle, planned_charge.c.started_at, planned_charge.c.departure_at)
        .where(planned_charge.c.outcome == OPEN)
        .order_by(planned_charge.c.id)
    )
    with engine.connect() as conn:
        return {
            row.vehicle: OpenCharge(ensure_utc(row.started_at), ensure_utc(row.departure_at))
            for row in conn.execute(query)
        }


def feasible(
    cfg: Config,
    *,
    vehicle: str,
    wallbox: str,
    soc_pct: float,
    target_pct: float,
    hours: float,
    limit_pct: float | None,
) -> bool:
    """Ob volle Leistung in ``hours`` Stunden für den Bedarf bis ``target_pct`` reicht und das
    Ladelimit ``limit_pct`` (``None``: unbekannt oder angeglichen) nicht unter dem Ziel liegt.

    Bedarf und volle Leistung wie im Planer (``need_kwh``, ``full_power_w``)."""
    car, point = cfg.vehicles[vehicle], cfg.wallboxes[wallbox]
    need = need_kwh(soc_pct, target_pct, car.usable_kwh, car.charge_efficiency_pct / 100)
    full_kw = full_power_w(car, point, cfg.grid.voltage_nominal_v) / 1000
    if limit_pct is not None and limit_pct < target_pct:
        return False
    return need <= full_kw * max(0.0, hours)


@dataclass(frozen=True)
class _Seen:
    """Was Health über ein Fahrzeug sagt; ``None`` heißt unbekannt."""

    mode: str | None
    plugged: bool | None
    wallbox: str | None
    soc_pct: float | None
    soc_source: str | None
    limit_pct: float | None
    departure_at: datetime | None  # nur mit dem Teil ``planned``
    target_pct: float | None
    need: bool  # ``planned`` nennt einen Bedarf: Zustand nicht ``reached``, ``need_kwh`` über 0


@dataclass
class _Row:
    """Eine offene Zeile im Speicher."""

    id: int
    session_id: int
    vehicle: str
    wallbox: str
    target_pct: float
    departure_at: datetime
    started_at: datetime
    start_soc_pct: float
    start_soc_source: str
    feasible: bool
    reached_at: datetime | None
    end_soc_pct: float | None
    end_soc_source: str | None
    end_soc_at: datetime | None  # wann ``end_soc_*`` gelesen wurde; ``None`` unbekannt
    saved: datetime  # wann ``end_soc_*`` zuletzt gespeichert wurde
    dirty: bool = False


def _moment(value: Any) -> datetime | None:
    """Zeitpunkt aus ISO-Text mit Zeitzone (Health); sonst ``None``."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ensure_utc(moment) if moment.tzinfo is not None else None


def _seen(health: Mapping[str, Any], key: str) -> _Seen | None:
    entry = _health_vehicles(health).get(key)
    if entry is None:
        return None
    planned = entry.get("planned")
    departure_at = target = None
    need = False
    if isinstance(planned, Mapping):
        departure_at = _moment(planned.get("departure_at"))
        target = finite_number(planned.get("target_soc_pct"))
        need_kwh = finite_number(planned.get("need_kwh"))
        need = planned.get("state") != REACHED_STATE and need_kwh is not None and need_kwh > 0
    # Modus und Ziel der Absicht: Boost über „Geplant“ beendet die Ladung nicht, und ein Ladelimit
    # unter dem Ziel ändert das Ziel nicht (das wirksame Ziel in ``planned`` sinkt dann)
    intent = health.get("intent")
    intents = intent.get("vehicles") if isinstance(intent, Mapping) else None
    wanted = intents.get(key) if isinstance(intents, Mapping) else None
    mode = entry.get("mode")
    if isinstance(wanted, Mapping):
        mode = wanted.get("mode", mode)
        wanted_target = finite_number(wanted.get("target_soc_pct"))
        if target is not None and wanted_target is not None:
            target = wanted_target
    plugged, wallbox, source = entry.get("plugged"), entry.get("wallbox"), entry.get("soc_source")
    return _Seen(
        mode=mode if isinstance(mode, str) else None,
        plugged=plugged if isinstance(plugged, bool) else None,
        wallbox=wallbox if isinstance(wallbox, str) else None,
        soc_pct=finite_number(entry.get("soc_pct")),
        soc_source=source if isinstance(source, str) else None,
        limit_pct=finite_number(entry.get("charge_limit_pct")),
        departure_at=departure_at,
        target_pct=target,
        need=need,
    )


class PlannedCharges:
    """Zeichnet die geplanten Ladungen auf und zählt sie für die Statistik."""

    def __init__(
        self,
        engine: Engine,
        clock: Clock,
        cfg: Config,
        *,
        recently_set: RecentlySet = _never,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._cfg = cfg
        self._recently_set = recently_set  # ``DrivingAutomation.recently_set`` (Plan 5b)
        self._open: dict[str, _Row] = {}  # je Fahrzeug höchstens eine offene Zeile
        self._loaded = False

    def record(self, health: Mapping[str, Any], sessions: Mapping[str, StoredSession]) -> None:
        """Verarbeitet ein Health; ``sessions`` sind die offenen Sessions je Wallbox."""
        now = self._clock.now()
        open_ids = {session.id for session in sessions.values()}
        try:
            with self._engine.begin() as conn:
                if not self._loaded:
                    self._load(conn)
                for key in self._cfg.vehicles:
                    self._observe(conn, now, key, _seen(health, key), sessions, open_ids)
        except Exception:
            # zurückgerollt: der nächste Lauf liest die offenen Zeilen neu
            self._loaded = False
            self._open.clear()
            raise

    def summary(self, first: date, last: date) -> PlannedSummary:
        """Die geschlossenen Zeilen mit Abfahrt an den Ortstagen ``first … last``."""
        start, end = local_day_slots(first)[0], local_day_slots(last)[-1] + SLOT
        table = planned_charge
        query = (
            select(table.c.outcome, table.c.feasible, func.count())
            .where(
                table.c.departure_at >= start,
                table.c.departure_at < end,
                table.c.outcome != OPEN,
            )
            .group_by(table.c.outcome, table.c.feasible)
        )
        with self._engine.connect() as conn:
            counts = {(row[0], bool(row[1])): int(row[2]) for row in conn.execute(query)}

        def count(outcome: str, ok: bool | None = None) -> int:
            return sum(n for (o, f), n in counts.items() if o == outcome and ok in (None, f))

        reached, missed = count(REACHED, True), count(MISSED, True)
        return PlannedSummary(
            reached=reached,
            feasible=reached + missed,
            missed=missed,
            infeasible=count(REACHED, False) + count(MISSED, False),
            early=count(EARLY),
            unknown=count(UNKNOWN),
            changed=count(CHANGED),
        )

    # --- Zeilen -----------------------------------------------------------------------------

    def _load(self, conn: Connection) -> None:
        rows = conn.execute(
            select(planned_charge, charging_session.c.wallbox)
            .join(charging_session, charging_session.c.id == planned_charge.c.session_id)
            .where(planned_charge.c.outcome == OPEN)
            .order_by(planned_charge.c.id)
        ).all()
        now = self._clock.now()
        for row in rows:
            earlier = self._open.get(row.vehicle)
            if earlier is not None:  # Rest eines Fehlers: die ältere ist nicht mehr beurteilbar
                self._finish(conn, earlier, UNKNOWN)
            self._open[row.vehicle] = _Row(
                id=row.id,
                session_id=row.session_id,
                vehicle=row.vehicle,
                wallbox=row.wallbox,
                target_pct=row.target_soc_pct,
                departure_at=ensure_utc(row.departure_at),
                started_at=ensure_utc(row.started_at),
                start_soc_pct=row.start_soc_pct,
                start_soc_source=row.start_soc_source,
                feasible=bool(row.feasible),
                reached_at=None if row.reached_at is None else ensure_utc(row.reached_at),
                end_soc_pct=row.end_soc_pct,
                end_soc_source=row.end_soc_source,
                end_soc_at=None if row.end_soc_at is None else ensure_utc(row.end_soc_at),
                saved=now,
            )
        self._loaded = True

    def _observe(
        self,
        conn: Connection,
        now: datetime,
        key: str,
        seen: _Seen | None,
        sessions: Mapping[str, StoredSession],
        open_ids: set[int],
    ) -> None:
        row = self._open.get(key)
        if row is not None and row.session_id not in open_ids:
            self._session_ended(conn, now, row, sessions)
        row = self._open.get(key)
        if row is not None:
            if now >= row.departure_at:
                self._close(conn, row, before_departure=False)
            elif seen is not None and seen.mode in LEAVING_MODES:
                self._finish(conn, row, CHANGED)
            elif (
                seen is not None
                and seen.departure_at is not None
                and (seen.departure_at != row.departure_at or seen.target_pct != row.target_pct)
            ):
                target = seen.target_pct
                if target is not None and self._adopts(now, row, seen.departure_at):
                    self._adopt(conn, now, row, seen, target, seen.departure_at)
                    return
                self._finish(conn, row, CHANGED)
            else:
                if seen is not None:
                    self._update(conn, now, row, seen)
                return
        if seen is None or seen.mode != PLANNED or seen.plugged is not True or not seen.need:
            return
        wallbox, departure_at, target = seen.wallbox, seen.departure_at, seen.target_pct
        if wallbox is None or wallbox not in self._cfg.wallboxes or wallbox not in sessions:
            return
        if departure_at is None or target is None or departure_at <= now:
            return
        self._begin(conn, now, key, sessions[wallbox], departure_at, target, seen)

    def _session_ended(
        self, conn: Connection, now: datetime, row: _Row, sessions: Mapping[str, StoredSession]
    ) -> None:
        """Die Session der Zeile ist nicht mehr offen: Umzug nach einer Lücke oder Ende.

        Hat der Tracker nach einer Lücke für dasselbe Auto eine neue Session begonnen, zieht die
        Zeile um, auch eine schon erreichte (Abfahrt in der Lücke: ``unknown``). Hat er die Session
        wegen einer Lücke geschlossen (Ende mehr als ``SESSION_GAP`` vor jetzt: Health fehlte oder
        der Steckzustand war unbekannt) und keine neue begonnen, bleibt die Zeile offen, bis eine
        neue beginnt (Umzug) oder die Abfahrt vorbei ist (``unknown``). Ein vorher erreichtes Ziel
        bleibt in beiden Fällen ``reached``; so bleibt es eine Zeile je Abfahrt, auch wenn der
        Bedarf nach der Lücke zurückkommt.
        """
        end = conn.execute(
            select(charging_session.c.end).where(charging_session.c.id == row.session_id)
        ).scalar()
        ended = now if end is None else ensure_utc(end)
        gap_outcome = REACHED if row.reached_at is not None else UNKNOWN
        newer = sessions.get(row.wallbox)
        if newer is not None and newer.vehicle == row.vehicle and newer.start >= ended:
            # der Tracker hat nach einer Lücke ohne Health neu begonnen: dasselbe Auto steckt noch
            if ended <= row.departure_at < newer.start:
                self._finish(conn, row, gap_outcome)  # die Abfahrt lag in der Lücke
                return
            row.session_id = newer.id
            conn.execute(
                update(planned_charge)
                .where(planned_charge.c.id == row.id)
                .values(session_id=newer.id)
            )
            return
        if ended < row.departure_at and now - ended > SESSION_GAP:
            # Ende wegen einer Lücke: ob das Auto vor der Abfahrt abgesteckt wurde, ist unbekannt
            if now >= row.departure_at:
                self._finish(conn, row, gap_outcome)
            return
        self._close(conn, row, before_departure=ended < row.departure_at)

    def _begin(
        self,
        conn: Connection,
        now: datetime,
        key: str,
        session: StoredSession,
        departure_at: datetime,
        target: float,
        seen: _Seen,
    ) -> None:
        soc = _known_soc(seen)
        source = ASSUMED if soc is None else str(seen.soc_source)
        start_soc = 0.0 if soc is None else soc
        ok = self._feasible(key, session.wallbox, start_soc, target, departure_at, seen, now)
        reached_at = now if soc is not None and soc >= target else None
        read_at = now if seen.soc_pct is not None and seen.soc_source is not None else None
        row_id = conn.execute(
            insert(planned_charge)
            .returning(planned_charge.c.id)
            .values(
                session_id=session.id,
                vehicle=key,
                target_soc_pct=target,
                departure_at=departure_at,
                started_at=now,
                start_soc_pct=start_soc,
                start_soc_source=source,
                feasible=ok,
                reached_at=reached_at,
                end_soc_pct=start_soc,
                end_soc_source=source,
                end_soc_at=read_at,
                outcome=OPEN,
            )
        ).scalar_one()
        self._open[key] = _Row(
            id=row_id,
            session_id=session.id,
            vehicle=key,
            wallbox=session.wallbox,
            target_pct=target,
            departure_at=departure_at,
            started_at=now,
            start_soc_pct=start_soc,
            start_soc_source=source,
            feasible=ok,
            reached_at=reached_at,
            end_soc_pct=start_soc,
            end_soc_source=source,
            end_soc_at=read_at,
            saved=now,
        )

    def _feasible(
        self,
        key: str,
        wallbox: str,
        soc: float,
        target: float,
        departure_at: datetime,
        seen: _Seen,
        now: datetime,
    ) -> bool:
        synced = self._cfg.vehicles[key].charge_limit_sync
        return feasible(
            self._cfg,
            vehicle=key,
            wallbox=wallbox,
            soc_pct=soc,
            target_pct=target,
            hours=(departure_at - now).total_seconds() / 3600,
            limit_pct=None if synced else seen.limit_pct,
        )

    def _adopts(self, now: datetime, row: _Row, departure_at: datetime) -> bool:
        """Ob die Zeile geänderte Werte als die der Automatik übernimmt (Präzisierung 9)."""
        if departure_at <= now or now - row.started_at > AUTO_ADOPT:
            return False
        return self._recently_set(row.vehicle, now)

    def _adopt(
        self,
        conn: Connection,
        now: datetime,
        row: _Row,
        seen: _Seen,
        target: float,
        departure_at: datetime,
    ) -> None:
        """Übernimmt Ziel und Abfahrt der Automatik; die Machbarkeit gilt neu wie bei einer neuen
        Zeile (mit dem bekannten Ladestand, sonst dem zu Beginn), ein erreichtes Ziel nur, wenn
        das neue nicht höher liegt."""
        raised = target > row.target_pct
        row.target_pct, row.departure_at = target, departure_at
        soc = _known_soc(seen)
        start = row.start_soc_pct if soc is None else soc
        row.feasible = self._feasible(
            row.vehicle, row.wallbox, start, row.target_pct, row.departure_at, seen, now
        )
        if raised:
            row.reached_at = None
        conn.execute(
            update(planned_charge)
            .where(planned_charge.c.id == row.id)
            .values(
                target_soc_pct=row.target_pct,
                departure_at=row.departure_at,
                feasible=row.feasible,
                reached_at=row.reached_at,
            )
        )
        log.info(
            "Geplante Ladung übernimmt Ziel und Abfahrt der Automatik",
            vehicle=row.vehicle,
            target_soc_pct=row.target_pct,
            departure_at=row.departure_at.isoformat(),
        )
        self._update(conn, now, row, seen)

    def _update(self, conn: Connection, now: datetime, row: _Row, seen: _Seen) -> None:
        """Merkt den Ladestand vor der Abfahrt mit seiner Zeit, ``reached_at``, einen bekannten
        Start und ein Ladelimit unter dem Ziel."""
        values: dict[str, Any] = {}
        if seen.soc_source is not None and seen.soc_pct is not None:
            row.end_soc_pct, row.end_soc_source = seen.soc_pct, seen.soc_source
            row.end_soc_at, row.dirty = now, True
        soc = _known_soc(seen)
        if soc is not None:
            if row.start_soc_source == ASSUMED:
                row.start_soc_source = str(seen.soc_source)
                row.started_at, row.start_soc_pct = now, soc
                row.feasible = self._feasible(
                    row.vehicle, row.wallbox, soc, row.target_pct, row.departure_at, seen, now
                )
                values |= {
                    "started_at": now,
                    "start_soc_pct": soc,
                    "start_soc_source": row.start_soc_source,
                    "feasible": row.feasible,
                }
            if row.reached_at is None and soc >= row.target_pct:
                row.reached_at = now
                values["reached_at"] = now
        synced = self._cfg.vehicles[row.vehicle].charge_limit_sync
        if (
            row.feasible
            and row.reached_at is None
            and not synced
            and seen.limit_pct is not None
            and seen.limit_pct < row.target_pct
        ):
            row.feasible = False
            values["feasible"] = False
        if row.dirty and (values or now - row.saved >= SAVE_EVERY):
            values |= _end_values(row)
        if values:
            conn.execute(update(planned_charge).where(planned_charge.c.id == row.id).values(values))
            if "end_soc_pct" in values:
                row.saved, row.dirty = now, False

    def _close(self, conn: Connection, row: _Row, *, before_departure: bool) -> None:
        self._finish(conn, row, _outcome(row, before_departure=before_departure))

    def _finish(self, conn: Connection, row: _Row, outcome: str) -> None:
        """Schließt die Zeile mit ``outcome`` und dem letzten Ladestand."""
        if self._open.get(row.vehicle) is row:
            del self._open[row.vehicle]
        conn.execute(
            update(planned_charge)
            .where(planned_charge.c.id == row.id)
            .values(outcome=outcome, **_end_values(row))
        )


def _end_values(row: _Row) -> dict[str, Any]:
    return {
        "end_soc_pct": row.end_soc_pct,
        "end_soc_source": row.end_soc_source,
        "end_soc_at": row.end_soc_at,
    }


def _known_soc(seen: _Seen) -> float | None:
    """Der Ladestand, wenn er bekannt ist (``measured``, ``estimated``); sonst ``None``."""
    return seen.soc_pct if seen.soc_source in KNOWN_SOURCES else None


def _outcome(row: _Row, *, before_departure: bool) -> str:
    """Das Ergebnis einer Zeile, die jetzt schließt (siehe Moduldoku)."""
    if row.reached_at is not None:
        return REACHED
    if before_departure:
        return EARLY
    if row.start_soc_source not in KNOWN_SOURCES or row.end_soc_source not in KNOWN_SOURCES:
        return UNKNOWN
    if row.end_soc_at is None or row.departure_at - row.end_soc_at > SOC_STALE:
        return UNKNOWN
    if row.end_soc_pct is not None and row.end_soc_pct >= row.target_pct - REACHED_TOLERANCE_PP:
        return REACHED
    return MISSED
