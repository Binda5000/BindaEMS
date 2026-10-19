from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import insert, select, update
from tests.unit.app.charging.charging_helpers import T0

from bindaems.app.charging.planned import PlannedCharges, PlannedSummary, feasible
from bindaems.app.charging.tracker import StoredSession
from bindaems.app.db.schema import charging_session, planned_charge

DEPARTURE = T0 + timedelta(hours=8)  # 18:00 Ortszeit


def health(
    *,
    soc: float | None = 40.0,
    source: str | None = "measured",
    target: int = 80,
    departure: datetime = DEPARTURE,
    mode: str | None = "planned",
    plugged: bool | None = True,
    wallbox: str = "twc",
    limit: float | None = None,
    planned: bool = True,
    intent_mode: str = "planned",
) -> dict[str, Any]:
    """Health mit dem Tesla in „Geplant“; das wirksame Ziel ist ``min(target, limit)``, ``mode``
    der geregelte Modus und ``intent_mode`` der der Absicht."""
    effective = target if limit is None else min(target, limit)
    return {
        "intent": {
            "version": 1,
            "vehicles": {"tesla": {"mode": intent_mode, "target_soc_pct": target}},
        },
        "intent_version": 1,
        "vehicles": [
            {
                "id": "tesla",
                "mode": mode,
                "wallbox": wallbox,
                "plugged": plugged,
                "soc_pct": soc,
                "soc_source": source,
                "charge_limit_pct": limit,
                "planned": {
                    "state": "surplus",
                    "target_soc_pct": effective,
                    "departure_at": departure.isoformat(),
                    "need_kwh": 10.0,
                    "latest_start": None,
                    "plan_w": None,
                }
                if planned
                else None,
            }
        ],
    }


@pytest.fixture
def charges(engine, clock, cfg) -> PlannedCharges:
    return PlannedCharges(engine, clock, cfg)


@pytest.fixture
def session(engine) -> StoredSession:
    """Eine offene Session des Tesla am Wall Connector seit ``T0``."""
    with engine.begin() as conn:
        session_id = conn.execute(
            insert(charging_session)
            .returning(charging_session.c.id)
            .values(wallbox="twc", vehicle="tesla", start=T0, end=None, last_seen=T0)
        ).scalar_one()
    return StoredSession(session_id, "twc", "tesla", T0, None, T0)


def end_session(engine, session: StoredSession, end: datetime) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(charging_session)
            .where(charging_session.c.id == session.id)
            .values(end=end, last_seen=end)
        )


def rows(engine) -> list[Any]:
    with engine.connect() as conn:
        return conn.execute(select(planned_charge).order_by(planned_charge.c.id)).all()


def at(clock, moment: datetime) -> None:
    clock.advance(moment - clock.now())


def summary(charges: PlannedCharges, day: date = date(2026, 10, 9)) -> PlannedSummary:
    return charges.summary(day, day)


def test_first_planned_health_opens_a_row(charges, session, engine) -> None:
    charges.record(health(soc=40.0), {"twc": session})
    [row] = rows(engine)
    assert row.session_id == session.id
    assert row.vehicle == "tesla"
    assert row.target_soc_pct == 80
    assert row.departure_at == DEPARTURE
    assert row.started_at == T0
    assert (row.start_soc_pct, row.start_soc_source) == (40.0, "measured")
    assert row.feasible is True
    assert (row.reached_at, row.outcome) == (None, "open")
    charges.record(health(soc=41.0), {"twc": session})
    assert len(rows(engine)) == 1  # dieselbe Abfahrt: keine zweite Zeile


def test_no_row_without_planned_session_or_plug(charges, session, engine) -> None:
    charges.record(health(planned=False, mode="pv"), {"twc": session})
    charges.record(health(), {})  # keine offene Session an der Wallbox
    charges.record(health(plugged=False), {"twc": session})
    charges.record(health(departure=T0 - timedelta(seconds=5)), {"twc": session})  # vorbei
    assert rows(engine) == []


def test_reached_before_departure_counts(charges, session, engine, clock) -> None:
    charges.record(health(soc=40.0), {"twc": session})
    at(clock, T0 + timedelta(hours=4))
    charges.record(health(soc=80.0), {"twc": session})
    assert rows(engine)[0].reached_at == T0 + timedelta(hours=4)
    at(clock, DEPARTURE - timedelta(seconds=10))
    # an der Abfahrt ist der Ladestand unbekannt: das Ziel war vorher erreicht
    charges.record(health(soc=0.0, source="assumed"), {"twc": session})
    at(clock, DEPARTURE + timedelta(seconds=1))
    charges.record(health(departure=DEPARTURE + timedelta(days=1)), {"twc": session})
    first = rows(engine)[0]
    assert first.outcome == "reached"
    assert (first.end_soc_pct, first.end_soc_source) == (0.0, "assumed")
    assert summary(charges) == PlannedSummary(
        reached=1, feasible=1, missed=0, infeasible=0, early=0, unknown=0
    )


def test_one_point_below_the_target_at_departure_counts_as_reached(
    charges, session, engine, clock
) -> None:
    charges.record(health(soc=40.0), {"twc": session})
    at(clock, DEPARTURE - timedelta(seconds=10))
    charges.record(health(soc=79.0, source="estimated"), {"twc": session})
    at(clock, DEPARTURE)
    charges.record(health(soc=79.0), {"twc": session})  # Abfahrt erreicht: schließt
    [row] = rows(engine)
    assert (row.outcome, row.end_soc_pct, row.end_soc_source) == ("reached", 79.0, "estimated")
    assert row.reached_at is None


def test_feasible_charge_below_the_target_is_missed(charges, session, engine, clock) -> None:
    charges.record(health(soc=40.0), {"twc": session})
    at(clock, DEPARTURE - timedelta(seconds=10))
    charges.record(health(soc=78.9), {"twc": session})
    at(clock, DEPARTURE + timedelta(minutes=5))
    charges.record(health(soc=78.9), {"twc": session})
    assert rows(engine)[0].outcome == "missed"
    assert summary(charges) == PlannedSummary(
        reached=0, feasible=1, missed=1, infeasible=0, early=0, unknown=0
    )


def test_late_plug_in_is_not_feasible(charges, session, engine, clock, cfg) -> None:
    # Bedarf 30 kWh (40 → 76 % von 75 kWh bei 90 %), 1 h bis zur Abfahrt bei 11 kW
    departure = T0 + timedelta(hours=1)
    charges.record(health(soc=40.0, target=76, departure=departure), {"twc": session})
    assert rows(engine)[0].feasible is False
    at(clock, departure - timedelta(seconds=10))
    charges.record(health(soc=50.0, target=76, departure=departure), {"twc": session})
    at(clock, departure + timedelta(seconds=10))
    charges.record(health(soc=50.0, target=76, departure=departure), {"twc": session})
    assert rows(engine)[0].outcome == "missed"
    assert summary(charges) == PlannedSummary(
        reached=0, feasible=0, missed=0, infeasible=1, early=0, unknown=0
    )


def test_feasible_compares_the_need_with_full_power_until_departure(cfg) -> None:
    # Tesla am Wall Connector: 16 A · 230 V · 3 = 11,04 kW; 30 kWh brauchen 2,72 h
    args = {"vehicle": "tesla", "wallbox": "twc", "soc_pct": 40.0, "target_pct": 76.0}
    assert feasible(cfg, **args, hours=2.8, limit_pct=None) is True
    assert feasible(cfg, **args, hours=2.7, limit_pct=None) is False
    assert feasible(cfg, **args, hours=8.0, limit_pct=70.0) is False  # Ladelimit unter dem Ziel
    assert feasible(cfg, **args, hours=8.0, limit_pct=76.0) is True
    assert feasible(cfg, **{**args, "soc_pct": 90.0}, hours=0.0, limit_pct=None) is True


def test_charge_limit_below_the_target_is_not_feasible(charges, session, engine, clock) -> None:
    charges.record(health(soc=40.0, limit=None), {"twc": session})
    assert rows(engine)[0].feasible is True
    at(clock, T0 + timedelta(hours=1))
    charges.record(health(soc=50.0, limit=70.0), {"twc": session})  # in der Tesla-App gesenkt
    [row] = rows(engine)
    assert row.feasible is False
    assert row.target_soc_pct == 80  # das Ziel der Absicht, nicht das wirksame


def test_unplugged_before_departure_is_early(charges, session, engine, clock) -> None:
    charges.record(health(soc=40.0), {"twc": session})
    at(clock, T0 + timedelta(hours=2))
    charges.record(health(soc=60.0), {"twc": session})
    end_session(engine, session, T0 + timedelta(hours=2, minutes=1))
    at(clock, T0 + timedelta(hours=2, minutes=1))
    charges.record(health(soc=60.0, plugged=False), {})
    [row] = rows(engine)
    assert (row.outcome, row.end_soc_pct) == ("early", 60.0)
    assert summary(charges) == PlannedSummary(
        reached=0, feasible=0, missed=0, infeasible=0, early=1, unknown=0
    )


def test_unplugged_after_reaching_the_target_counts_as_reached(
    charges, session, engine, clock
) -> None:
    charges.record(health(soc=40.0), {"twc": session})
    at(clock, T0 + timedelta(hours=5))
    charges.record(health(soc=80.0), {"twc": session})
    end_session(engine, session, T0 + timedelta(hours=6))
    at(clock, T0 + timedelta(hours=6))
    charges.record(health(soc=80.0, plugged=False), {})
    assert rows(engine)[0].outcome == "reached"


def test_session_ended_after_departure_is_judged_at_departure(
    charges, session, engine, clock
) -> None:
    # die app war über die Abfahrt aus: die Session endete erst danach, der Tracker schloss sie
    charges.record(health(soc=40.0), {"twc": session})
    at(clock, DEPARTURE - timedelta(minutes=1))
    charges.record(health(soc=80.0), {"twc": session})
    end_session(engine, session, DEPARTURE + timedelta(minutes=20))
    at(clock, DEPARTURE + timedelta(hours=2))
    charges.record(health(soc=None, source=None, plugged=False, planned=False), {})
    assert rows(engine)[0].outcome == "reached"


def test_assumed_soc_is_unknown(charges, session, engine, clock) -> None:
    charges.record(health(soc=0.0, source="assumed"), {"twc": session})
    [row] = rows(engine)
    assert (row.start_soc_pct, row.start_soc_source) == (0.0, "assumed")
    at(clock, DEPARTURE - timedelta(seconds=10))
    charges.record(health(soc=0.0, source="assumed"), {"twc": session})
    at(clock, DEPARTURE + timedelta(seconds=10))
    charges.record(health(soc=0.0, source="assumed"), {"twc": session})
    [row] = rows(engine)
    assert (row.outcome, row.reached_at) == ("unknown", None)
    assert summary(charges) == PlannedSummary(
        reached=0, feasible=0, missed=0, infeasible=0, early=0, unknown=1
    )


def test_soc_unknown_at_departure_is_unknown(charges, session, engine, clock) -> None:
    charges.record(health(soc=40.0), {"twc": session})
    at(clock, DEPARTURE - timedelta(seconds=10))
    charges.record(health(soc=0.0, source="assumed"), {"twc": session})  # Tessie veraltet
    at(clock, DEPARTURE + timedelta(seconds=10))
    charges.record(health(), {"twc": session})
    assert rows(engine)[0].outcome == "unknown"


def test_first_known_soc_replaces_an_assumed_start(charges, session, engine, clock) -> None:
    charges.record(health(soc=0.0, source="assumed"), {"twc": session})
    at(clock, T0 + timedelta(minutes=5))
    charges.record(health(soc=40.0, source="measured"), {"twc": session})
    [row] = rows(engine)
    assert (row.start_soc_pct, row.start_soc_source, row.feasible) == (40.0, "measured", True)
    assert row.started_at == T0


def test_assumed_soc_never_sets_reached(charges, session, engine, clock) -> None:
    charges.record(health(soc=40.0), {"twc": session})
    at(clock, T0 + timedelta(hours=1))
    charges.record(health(soc=90.0, source="assumed"), {"twc": session})
    assert rows(engine)[0].reached_at is None


def test_new_departure_opens_a_new_row(charges, session, engine, clock) -> None:
    charges.record(health(soc=40.0), {"twc": session})
    at(clock, DEPARTURE - timedelta(seconds=10))
    charges.record(health(soc=80.0), {"twc": session})
    # das Auto steckt über die Abfahrt hinaus: der core rollt auf den nächsten Tag
    at(clock, DEPARTURE + timedelta(seconds=10))
    following = DEPARTURE + timedelta(days=1)
    charges.record(health(soc=80.0, departure=following), {"twc": session})
    first, second = rows(engine)
    assert (first.departure_at, first.outcome) == (DEPARTURE, "reached")
    assert (second.departure_at, second.outcome, second.session_id) == (
        following,
        "open",
        session.id,
    )
    assert (second.started_at, second.start_soc_pct) == (DEPARTURE + timedelta(seconds=10), 80.0)


def test_changed_departure_or_target_replaces_the_open_row(charges, session, engine, clock) -> None:
    charges.record(health(soc=40.0), {"twc": session})
    at(clock, T0 + timedelta(minutes=10))
    later = DEPARTURE + timedelta(hours=1)
    charges.record(health(soc=42.0, departure=later), {"twc": session})
    [row] = rows(engine)  # die alte Abfahrt kam nie: keine Zeile für sie
    assert (row.departure_at, row.start_soc_pct) == (later, 42.0)
    at(clock, T0 + timedelta(minutes=20))
    charges.record(health(soc=43.0, departure=later, target=90), {"twc": session})
    [row] = rows(engine)
    assert (row.target_soc_pct, row.start_soc_pct, row.outcome) == (90, 43.0, "open")


def test_leaving_planned_drops_the_open_row(charges, session, engine, clock) -> None:
    charges.record(health(soc=40.0), {"twc": session})
    at(clock, T0 + timedelta(minutes=10))
    charges.record(health(mode=None, planned=False, plugged=None), {"twc": session})  # unbekannt
    assert len(rows(engine)) == 1
    charges.record(health(mode="boost", planned=False), {"twc": session})  # Boost über „Geplant“
    assert len(rows(engine)) == 1
    charges.record(health(mode="now", planned=False, intent_mode="now"), {"twc": session})
    assert rows(engine) == []


def test_open_row_and_last_soc_survive_a_restart(charges, session, engine, clock, cfg) -> None:
    charges.record(health(soc=40.0), {"twc": session})
    at(clock, DEPARTURE - timedelta(minutes=5))
    charges.record(health(soc=79.5), {"twc": session})
    restarted = PlannedCharges(engine, clock, cfg)
    at(clock, DEPARTURE + timedelta(minutes=10))
    restarted.record(health(departure=DEPARTURE + timedelta(days=1)), {"twc": session})
    first = rows(engine)[0]
    assert (first.outcome, first.end_soc_pct) == ("reached", 79.5)


def test_summary_counts_closed_rows_by_departure_day(charges, engine) -> None:
    with engine.begin() as conn:
        session_id = conn.execute(
            insert(charging_session)
            .returning(charging_session.c.id)
            .values(wallbox="twc", vehicle="tesla", start=T0, end=None, last_seen=T0)
        ).scalar_one()
        base = {
            "session_id": session_id,
            "vehicle": "tesla",
            "target_soc_pct": 80,
            "started_at": T0,
            "start_soc_pct": 40.0,
            "start_soc_source": "measured",
        }
        cases = [
            # 9. Oktober, Ortszeit
            (datetime(2026, 10, 8, 22, 0, tzinfo=UTC), True, "reached"),  # 00:00 am 9.
            (datetime(2026, 10, 9, 5, 0, tzinfo=UTC), True, "reached"),
            (datetime(2026, 10, 9, 6, 0, tzinfo=UTC), True, "missed"),
            (datetime(2026, 10, 9, 6, 0, tzinfo=UTC), False, "reached"),
            (datetime(2026, 10, 9, 6, 0, tzinfo=UTC), False, "missed"),
            (datetime(2026, 10, 9, 7, 0, tzinfo=UTC), True, "early"),
            (datetime(2026, 10, 9, 7, 0, tzinfo=UTC), False, "unknown"),
            (datetime(2026, 10, 9, 21, 0, tzinfo=UTC), True, "open"),  # zählt nicht
            # andere Tage
            (datetime(2026, 10, 8, 21, 59, tzinfo=UTC), True, "reached"),
            (datetime(2026, 10, 9, 22, 0, tzinfo=UTC), True, "reached"),
        ]
        for departure, ok, outcome in cases:
            conn.execute(
                insert(planned_charge).values(
                    **base, departure_at=departure, feasible=ok, outcome=outcome
                )
            )
    assert summary(charges) == PlannedSummary(
        reached=2, feasible=3, missed=1, infeasible=2, early=1, unknown=1
    )
    assert charges.summary(date(2026, 10, 8), date(2026, 10, 10)).reached == 4
    assert charges.summary(date(2026, 10, 11), date(2026, 10, 11)) == PlannedSummary(
        reached=0, feasible=0, missed=0, infeasible=0, early=0, unknown=0
    )


def test_known_soc_during_boost_replaces_an_assumed_start(charges, session, engine, clock) -> None:
    # Boost über „Geplant“: Health nennt keinen Teil ``planned``, die Abfahrt der Zeile gilt
    charges.record(health(soc=0.0, source="assumed"), {"twc": session})
    at(clock, T0 + timedelta(minutes=5))
    charges.record(health(mode="boost", planned=False, soc=50.0), {"twc": session})
    [row] = rows(engine)
    assert (row.start_soc_pct, row.start_soc_source, row.feasible) == (50.0, "measured", True)
