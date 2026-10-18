"""Gemeinsame Konstanten und Hilfen der Statistik-Tests (kein conftest-Import)."""

from datetime import UTC, date, datetime

from sqlalchemy import insert

from bindaems.app.consumers.service import ConsumerInput, ConsumerService
from bindaems.app.db.schema import charging_session

T0 = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)  # 08:00 Ortszeit, vor "jetzt" (10:00 Ortszeit)
TODAY = date(2026, 10, 9)


def add_session(engine, start, end, vehicle="tesla", wallbox="twc") -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(charging_session).values(
                wallbox=wallbox, vehicle=vehicle, start=start, end=end, last_seen=end or start
            )
        )


def add_consumer(consumers: ConsumerService, name: str) -> int:
    return consumers.create(
        ConsumerInput(
            name=name, color="#336699", source_kind="core", power_ref=f"load.{name}.power_w"
        ),
        actor="chris",
        source="ui",
    ).id
