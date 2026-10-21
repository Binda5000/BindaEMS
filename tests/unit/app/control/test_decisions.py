import asyncio
from datetime import timedelta

import pytest
from structlog.testing import capture_logs
from tests.app_helpers import T_APP
from tests.helpers import FakeSink
from tests.unit.app.control.conftest import FakeCoreClient
from tests.unit.shared.test_decisions import decision

from bindaems.app.control import decisions as decisions_module
from bindaems.app.control.decisions import (
    BUFFER_MAX,
    CATCH_UP_OVERLAP,
    PAGE_LIMIT,
    DecisionCollector,
    DecisionQuery,
    DecisionStore,
)
from bindaems.app.core_link import CoreUnavailableError
from bindaems.shared.control import CommandKind
from bindaems.shared.decisions import DecisionResult
from bindaems.shared.influx.lineprotocol import Point
from bindaems.shared.timeutil import ManualClock

SAMPLE = decision(
    id="s1",
    ts=T_APP,
    actuator="wallbox.evcs",
    dry_run=True,
    result=DecisionResult.DRY_RUN,
    codes=("mode_pv", "pv_surplus"),
    text="EVCS „evcs“ (PV-Überschuss): Strom 8 A – Dry-Run, nicht gesendet.",
)


def at(minutes: float):
    return T_APP + timedelta(minutes=minutes)


# DecisionStore


def test_add_ignores_duplicate_ids(store: DecisionStore) -> None:
    assert store.add([SAMPLE]) == [SAMPLE]
    assert store.add([SAMPLE]) == []
    assert store.add([SAMPLE, decision(id="s2", ts=at(1))]) == [decision(id="s2", ts=at(1))]


def test_add_ignores_duplicates_within_a_batch(store: DecisionStore) -> None:
    assert store.add([SAMPLE, SAMPLE]) == [SAMPLE]


def test_add_nothing(store: DecisionStore) -> None:
    assert store.add([]) == []


def test_query_returns_the_stored_decision_unchanged(store: DecisionStore) -> None:
    odd = decision(
        id="x",
        kind=CommandKind.CHARGING,
        requested=True,
        applied=None,
        outcome="none",
        result=DecisionResult.FOREIGN,
        codes=(),
        inputs={"soc_pct": 46.5, "grid_l1_a": None},
    )
    store.add([SAMPLE, odd])
    found, more = store.query(DecisionQuery())
    assert not more
    assert {item.decision.id: item.decision for item in found} == {"s1": SAMPLE, "x": odd}


def _many(count: int) -> list:
    """250 Entscheidungen, jede Minute eine, abwechselnd Dry-Run; die EVCS-Texte alle 5 Minuten."""
    result = []
    for i in range(count):
        tesla = i % 10 == 0
        result.append(
            decision(
                id=f"d{i}",
                ts=at(i),
                actuator="vehicle.tesla" if tesla else "wallbox.evcs",
                dry_run=i % 2 == 0,
                result=DecisionResult.DRY_RUN if i % 2 == 0 else DecisionResult.OK,
                text=f"Strom {8 if i % 5 == 0 else 6} A – Nr. {i}",
            )
        )
    return result


def test_query_filters_and_pages(store: DecisionStore) -> None:
    store.add(_many(250))

    first, more = store.query(DecisionQuery(dry_run=False, limit=100))
    assert more
    assert [item.decision.id for item in first[:2]] == ["d249", "d247"]
    assert len(first) == 100
    assert all(not item.decision.dry_run for item in first)

    second, more = store.query(DecisionQuery(dry_run=False, limit=100, before=first[-1].cursor))
    assert (len(second), more) == (25, False)
    assert second[0].decision.id == "d49"
    assert second[-1].decision.id == "d1"

    found, _ = store.query(DecisionQuery(text="strom 8", limit=1000))
    assert len(found) == 50
    assert all("Strom 8 A" in item.decision.text for item in found)

    found, _ = store.query(DecisionQuery(actuator="vehicle.tesla", limit=1000))
    assert {item.decision.actuator for item in found} == {"vehicle.tesla"}
    assert len(found) == 25

    found, _ = store.query(DecisionQuery(result=DecisionResult.OK, limit=1000))
    assert len(found) == 125


def test_query_with_dry_run_shown_includes_everything(store: DecisionStore) -> None:
    store.add(_many(10))
    found, _ = store.query(DecisionQuery())
    assert len(found) == 10


def test_query_limits_the_period(store: DecisionStore) -> None:
    store.add(_many(10))
    found, _ = store.query(DecisionQuery(since=at(3), until=at(6)))
    assert [item.decision.id for item in found] == ["d5", "d4", "d3"]


@pytest.mark.parametrize("needle", ["änderung", "ÄNDERUNG", "Änderung", "ä"])
def test_text_search_ignores_case_of_umlauts(store: DecisionStore, needle: str) -> None:
    store.add([decision(id="a", text="Änderung von außen"), decision(id="b", text="Strom 8 A")])
    found, _ = store.query(DecisionQuery(text=needle))
    assert [item.decision.id for item in found] == ["a"]


def test_text_search_finds_uppercase_umlaut_with_lowercase_needle(store: DecisionStore) -> None:
    store.add([decision(id="a", text="EVCS „evcs“ (Übersteuerung): Strom 8 A")])
    found, _ = store.query(DecisionQuery(text="übersteuerung"))
    assert [item.decision.id for item in found] == ["a"]


def test_text_search_takes_wildcards_literally(store: DecisionStore) -> None:
    store.add([decision(id="a", text="Strom 50% mehr"), decision(id="b", text="Strom 500 mehr")])
    found, _ = store.query(DecisionQuery(text="50%"))
    assert [item.decision.id for item in found] == ["a"]
    found, _ = store.query(DecisionQuery(text="strom_50"))
    assert found == []


def test_paging_follows_ts_not_arrival_when_the_backlog_delivers_older_rows(
    store: DecisionStore,
) -> None:
    store.add([decision(id="new", ts=at(10))])
    store.add([decision(id="old", ts=at(5))])  # kommt später an: höheres seq, älterer ts
    first, more = store.query(DecisionQuery(limit=1))
    assert [item.decision.id for item in first] == ["new"]
    assert more
    second, more = store.query(DecisionQuery(limit=1, before=first[0].cursor))
    assert [item.decision.id for item in second] == ["old"]
    assert not more


def test_before_cursor_on_a_cleaned_row_gives_an_empty_page(
    store: DecisionStore, clock: ManualClock
) -> None:
    """Das Aufräumen löscht nach ``ts``: fehlt der Anker, fehlt auch alles Ältere. Die leere Seite
    ist richtig; ein Rückfall auf ``seq < before`` wiederholte nur, was die Seite schon zeigte."""
    now = clock.now()
    store.add(
        [
            decision(id="young", ts=now - timedelta(days=1)),
            decision(id="old1", ts=now - timedelta(days=181)),
            decision(id="old2", ts=now - timedelta(days=182)),
        ]
    )
    first, more = store.query(DecisionQuery(limit=2))
    assert [item.decision.id for item in first] == ["young", "old1"]
    assert more
    assert store.cleanup(180) == 2  # entfernt den Anker (old1) und alles Ältere
    older, more = store.query(DecisionQuery(limit=2, before=first[-1].cursor))
    assert (older, more) == ([], False)


def test_same_ts_is_ordered_by_arrival(store: DecisionStore) -> None:
    store.add([decision(id="a", ts=T_APP), decision(id="b", ts=T_APP)])
    found, _ = store.query(DecisionQuery())
    assert [item.decision.id for item in found] == ["b", "a"]
    older, _ = store.query(DecisionQuery(before=found[0].cursor))
    assert [item.decision.id for item in older] == ["a"]


def test_latest_per_actuator_in_the_last_24_h(store: DecisionStore, clock: ManualClock) -> None:
    now = clock.now()
    store.add(
        [
            decision(id="e1", ts=now - timedelta(hours=3), actuator="wallbox.evcs"),
            decision(id="e2", ts=now - timedelta(hours=1), actuator="wallbox.evcs"),
            decision(id="t1", ts=now - timedelta(hours=2), actuator="vehicle.tesla"),
            decision(id="v1", ts=now - timedelta(hours=25), actuator="battery.victron"),
        ]
    )
    latest = store.latest_per_actuator(now - timedelta(hours=24))
    assert [(item.decision.actuator, item.decision.id) for item in latest] == [
        ("wallbox.evcs", "e2"),
        ("vehicle.tesla", "t1"),
    ]


def test_latest_per_actuator_prefers_the_later_arrival_at_equal_ts(store: DecisionStore) -> None:
    store.add([decision(id="a", ts=T_APP), decision(id="b", ts=T_APP)])
    latest = store.latest_per_actuator(T_APP - timedelta(hours=1))
    assert [item.decision.id for item in latest] == ["b"]


def test_cleanup_removes_old_rows(store: DecisionStore, clock: ManualClock) -> None:
    now = clock.now()
    store.add(
        [
            decision(id="old", ts=now - timedelta(days=181)),
            decision(id="young", ts=now - timedelta(days=179)),
        ]
    )
    assert store.cleanup(180) == 1
    found, _ = store.query(DecisionQuery())
    assert [item.decision.id for item in found] == ["young"]


def test_newest_ts(store: DecisionStore) -> None:
    assert store.newest_ts() is None
    store.add([decision(id="a", ts=at(5)), decision(id="b", ts=at(2))])
    assert store.newest_ts() == at(5)


# DecisionCollector


def _ids(store: DecisionStore) -> list[str]:
    found, _ = store.query(DecisionQuery(limit=10000))
    return sorted(item.decision.id for item in found)


async def test_catch_up_after_reconnect_has_no_gap_and_no_duplicate(
    collector: DecisionCollector, fake_client: FakeCoreClient, store: DecisionStore
) -> None:
    t0 = T_APP
    store.add([decision(id="known", ts=t0)])
    first = [decision(id="known", ts=t0), decision(id="a", ts=t0 + timedelta(minutes=1))]
    second = [decision(id="b", ts=t0 + timedelta(minutes=30)), decision(id="c", ts=at(60))]
    # der core liefert ab T0−5 min; die erste Seite reicht bis T0+1 min
    fake_client.pages = [
        ([decision(id="before", ts=t0 - timedelta(minutes=5)), *first], True),
        (second, False),
    ]

    await collector.catch_up()

    assert fake_client.calls == [
        (t0 - CATCH_UP_OVERLAP, PAGE_LIMIT),
        (t0 + timedelta(minutes=1), PAGE_LIMIT),
    ]
    assert _ids(store) == ["a", "b", "before", "c", "known"]


async def test_clock_jump_back_within_the_overlap_is_caught(
    collector: DecisionCollector, fake_client: FakeCoreClient, store: DecisionStore
) -> None:
    store.add([decision(id="known", ts=T_APP)])
    fake_client.pages = [([decision(id="jumped", ts=T_APP - timedelta(minutes=3))], False)]

    await collector.catch_up()

    assert fake_client.calls[0][0] == T_APP - timedelta(minutes=10)
    assert _ids(store) == ["jumped", "known"]


async def test_empty_table_catches_up_7_days(
    collector: DecisionCollector, fake_client: FakeCoreClient, clock: ManualClock
) -> None:
    fake_client.pages = [([], False)]
    await collector.catch_up()
    assert fake_client.calls == [(clock.now() - timedelta(days=7), PAGE_LIMIT)]


async def test_catch_up_ends_with_a_log_line_when_the_core_is_unavailable(
    collector: DecisionCollector, fake_client: FakeCoreClient
) -> None:
    fake_client.error = CoreUnavailableError("keine Verbindung")
    with capture_logs() as logs:
        await collector.catch_up()  # kein Fehler: der nächste Verbindungsaufbau holt nach
    assert [entry["event"] for entry in logs] == ["Rückstand der Entscheidungen nicht lesbar"]


async def test_catch_up_stops_when_pages_do_not_advance(
    collector: DecisionCollector, fake_client: FakeCoreClient, store: DecisionStore
) -> None:
    stuck = decision(id="a", ts=at(-60 * 24))
    fake_client.pages = [([stuck], True), ([stuck], True), ([stuck], True)]
    # erste Seite liegt nach „since“ (jetzt − 7 Tage), die zweite wiederholt denselben ts
    await collector.catch_up()
    assert len(fake_client.calls) == 2
    assert _ids(store) == ["a"]


async def test_catch_up_stops_on_an_empty_page_that_claims_more(
    collector: DecisionCollector, fake_client: FakeCoreClient
) -> None:
    fake_client.pages = [([], True)]
    await collector.catch_up()
    assert len(fake_client.calls) == 1


async def test_new_decisions_become_grafana_points(
    collector: DecisionCollector, fake_client: FakeCoreClient, sink: FakeSink
) -> None:
    await collector.on_decision(SAMPLE)
    assert await collector.flush() == 1
    assert sink.points == [
        Point(
            "decision",
            {
                "actuator": "wallbox.evcs",
                "kind": str(SAMPLE.kind),
                "result": "dry_run",
                "dry_run": "true",
            },
            {"text": SAMPLE.text, "codes": "mode_pv,pv_surplus"},
            SAMPLE.ts,
            rp="raw",
        )
    ]
    # dieselbe Entscheidung im Rückstand: kein zweiter Punkt
    fake_client.pages = [([SAMPLE], False)]
    await collector.catch_up()
    assert len(sink.points) == 1


async def test_a_decision_in_the_backlog_and_the_buffer_gets_one_point(
    collector: DecisionCollector, fake_client: FakeCoreClient, sink: FakeSink
) -> None:
    await collector.on_decision(SAMPLE)
    fake_client.pages = [([SAMPLE], False)]
    await collector.catch_up()
    await collector.flush()
    assert len(sink.points) == 1


async def test_decisions_of_one_cycle_do_not_overwrite_each_other_in_influx(
    collector: DecisionCollector, sink: FakeSink
) -> None:
    # Startfolge eines Zyklus: Strom, dann Laden an, für denselben Aktor mit gleichem Ergebnis
    for id_, kind in (("s1", CommandKind.CURRENT), ("s2", CommandKind.CHARGING)):
        await collector.on_decision(decision(id=id_, kind=kind, result=DecisionResult.DRY_RUN))
    assert await collector.flush() == 2
    # InfluxDB behält je Serie (Messung und Tags) und Zeitpunkt nur einen Punkt
    keys = {(p.measurement, tuple(sorted(p.tags.items())), p.ts) for p in sink.points}
    assert len(keys) == 2
    assert [p.tags["kind"] for p in sink.points] == ["current", "charging"]


async def test_live_decision_tags(collector: DecisionCollector, sink: FakeSink) -> None:
    await collector.on_decision(decision(id="l1", dry_run=False, codes=()))
    await collector.flush()
    assert sink.points[0].tags["dry_run"] == "false"
    assert sink.points[0].fields["codes"] == ""


async def test_run_writes_the_buffer_every_second(
    collector: DecisionCollector,
    store: DecisionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(decisions_module.asyncio, "sleep", fake_sleep)
    await collector.on_decision(SAMPLE)
    task = asyncio.create_task(collector.run())
    for _ in range(5):
        await real_sleep(0.01)
    task.cancel()
    assert sleeps[0] == 1.0
    assert _ids(store) == ["s1"]


async def test_cancelled_write_keeps_the_buffer(
    collector: DecisionCollector, store: DecisionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cancelled(items):
        raise asyncio.CancelledError

    monkeypatch.setattr(store, "add", cancelled)
    await collector.on_decision(SAMPLE)
    with pytest.raises(asyncio.CancelledError):
        await collector.flush()
    monkeypatch.undo()
    assert await collector.flush() == 1


async def test_failed_write_keeps_the_buffer(
    collector: DecisionCollector, store: DecisionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_add = store.add
    calls = 0

    def flaky(items):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Datenbank gesperrt")
        return real_add(items)

    monkeypatch.setattr(store, "add", flaky)
    await collector.on_decision(SAMPLE)
    with pytest.raises(RuntimeError):
        await collector.flush()
    await collector.on_decision(decision(id="s2", ts=at(1)))
    assert await collector.flush() == 2
    assert _ids(store) == ["s1", "s2"]


async def test_buffer_is_capped_while_the_database_is_down(
    collector: DecisionCollector, store: DecisionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_add = store.add

    def broken(items):
        raise RuntimeError("Datenbank gesperrt")

    monkeypatch.setattr(store, "add", broken)
    with capture_logs() as logs:
        for i in range(12_000):
            await collector.on_decision(decision(id=f"d{i}", ts=at(i / 60)))
            if i == 6_000:  # ein gescheitertes Schreiben mitten in der Ausfallphase
                with pytest.raises(RuntimeError):
                    await collector.flush()
    assert len(collector._buffer) == BUFFER_MAX == 10_000
    dropped = [entry for entry in logs if entry.get("code") == "decisions_dropped"]
    assert len(dropped) == 1  # je Ausfallphase, nicht je Eintrag
    assert dropped[0]["log_level"] == "warning"

    monkeypatch.setattr(store, "add", real_add)
    assert await collector.flush() == 10_000
    ids = _ids(store)
    assert "d1999" not in ids and "d2000" in ids and "d11999" in ids  # die ältesten fielen weg

    # nach dem Schreiben beginnt eine neue Ausfallphase mit eigener Warnung
    monkeypatch.setattr(store, "add", broken)
    with capture_logs() as logs:
        for i in range(10_001):
            await collector.on_decision(decision(id=f"e{i}", ts=at(1000 + i / 60)))
    assert len([entry for entry in logs if entry.get("code") == "decisions_dropped"]) == 1


async def test_cleanup(
    collector: DecisionCollector, store: DecisionStore, clock: ManualClock
) -> None:
    store.add([decision(id="old", ts=clock.now() - timedelta(days=200)), SAMPLE])
    assert await collector.cleanup(180) == 1
    assert _ids(store) == ["s1"]
