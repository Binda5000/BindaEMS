from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import insert
from tests.app_helpers import ingest, price_row
from tests.unit.app.charging.charging_helpers import T0

from bindaems.app.charging.sessions import (
    GUEST,
    UNKNOWN,
    FlowSplit,
    attribute,
    slot_sessions,
    vehicle_name,
    wallbox_name,
)
from bindaems.app.charging.tracker import StoredSession
from bindaems.app.db.schema import charging_session
from bindaems.app.ledger.costing import SlotCost
from bindaems.app.ledger.service import LedgerSlot
from bindaems.shared.timeutil import SLOT

NOW = T0 + timedelta(hours=6)


def stored(
    session_id: int,
    start: datetime,
    end: datetime | None,
    vehicle: str | None = "tesla",
    wallbox: str = "twc",
) -> StoredSession:
    return StoredSession(session_id, wallbox, vehicle, start, end, end or start)


def led(slot: datetime, flows: dict[str, float], price: float | None = None) -> LedgerSlot:
    return LedgerSlot(
        slot_start=slot,
        covered_s=900.0,
        flows_wh=flows,
        counters={},
        import_wh=sum(wh for name, wh in flows.items() if name.startswith("grid>")),
        export_wh=0.0,
        import_price_ct=price,
        origin="stream",
    )


def cost(slot: datetime, discharge_ct: float = 12.0) -> SlotCost:
    return SlotCost(slot, 1.0, 10.0, discharge_ct)


def test_split_and_cost_of_a_session() -> None:
    slot = led(
        T0,
        {"pv>wb:twc": 1000.0, "battery>wb:twc": 500.0, "grid>wb:twc": 500.0},
        price=30.0,
    )
    session = stored(1, T0 - SLOT, T0 + SLOT)
    by_session, by_vehicle = attribute([slot], {T0: cost(T0, 12.0)}, [session], NOW)
    split = by_session[1]
    assert (split.kwh, split.pv_kwh, split.battery_kwh, split.grid_kwh) == (2.0, 1.0, 0.5, 0.5)
    assert split.cost_eur == pytest.approx(0.21)  # 0 + 0,5 · 12 ct + 0,5 · 30 ct
    assert by_vehicle == {"tesla": split}


def test_slot_belongs_to_the_session_with_most_overlap() -> None:
    first = stored(1, T0 - SLOT, T0 + timedelta(minutes=5), "tesla")
    second = stored(2, T0 + timedelta(minutes=5), T0 + SLOT, "polestar")
    assert slot_sessions([first, second], "twc", T0, NOW) == second
    # Gleichstand: die spätere Session
    tied_a = stored(3, T0 - SLOT, T0 + timedelta(minutes=7, seconds=30))
    tied_b = stored(4, T0 + timedelta(minutes=7, seconds=30), T0 + SLOT * 2)
    assert slot_sessions([tied_a, tied_b], "twc", T0, NOW) == tied_b
    assert slot_sessions([first, second], "evcs", T0, NOW) is None  # andere Wallbox
    assert slot_sessions([first], "twc", T0 + SLOT, NOW) is None  # keine Überdeckung


def test_energy_without_session_is_unknown() -> None:
    slot = led(T0, {"pv>wb:twc": 1000.0})
    by_session, by_vehicle = attribute([slot], {T0: cost(T0)}, [], NOW)
    assert by_session == {}
    assert by_vehicle[UNKNOWN] == FlowSplit(1.0, 1.0, 0.0, 0.0, 0.0)
    # eine Session ohne Fahrzeug zählt ebenfalls als „Unbekannt“, ein Gast als „Gast“
    anonymous = stored(1, T0, T0 + SLOT, None)
    assert set(attribute([slot], {T0: cost(T0)}, [anonymous], NOW)[1]) == {UNKNOWN}
    guest = stored(2, T0, T0 + SLOT, GUEST)
    assert set(attribute([slot], {T0: cost(T0)}, [guest], NOW)[1]) == {GUEST}


def test_vehicle_cost_unknown_without_price() -> None:
    session = stored(1, T0, T0 + 2 * SLOT)
    priced = led(T0, {"grid>wb:twc": 1000.0}, price=20.0)
    unpriced = led(T0 + SLOT, {"grid>wb:twc": 1000.0, "pv>wb:twc": 500.0}, price=None)
    costs = {T0: cost(T0), T0 + SLOT: cost(T0 + SLOT)}
    by_session, by_vehicle = attribute([priced, unpriced], costs, [session], NOW)
    assert by_session[1].kwh == 2.5 and by_session[1].cost_eur is None
    assert by_vehicle["tesla"].cost_eur is None
    # PV und Akku ohne Netzanteil bleiben auch ohne Bezugspreis bekannt
    pv_only = led(T0 + SLOT, {"pv>wb:twc": 500.0, "battery>wb:twc": 1000.0}, price=None)
    known = attribute([pv_only], costs, [session], NOW)[0][1]
    assert known.cost_eur == pytest.approx(0.12)


def test_open_session_counts_up_to_now() -> None:
    open_session = stored(1, T0, None)
    inside = led(T0 + SLOT, {"pv>wb:twc": 1000.0})
    after = led(NOW + SLOT, {"pv>wb:twc": 1000.0})
    costs = {T0 + SLOT: cost(T0 + SLOT), NOW + SLOT: cost(NOW + SLOT)}
    by_session, by_vehicle = attribute([inside, after], costs, [open_session], NOW)
    assert by_session[1].kwh == 1.0  # der Slot nach „jetzt“ gehört nicht mehr dazu
    assert by_vehicle[UNKNOWN].kwh == 1.0


def test_battery_without_cost_row_makes_the_cost_unknown() -> None:
    slot = led(T0, {"battery>wb:twc": 500.0})
    assert attribute([slot], {}, [stored(1, T0, T0 + SLOT)], NOW)[0][1].cost_eur is None


def test_battery_share_of_unknown_cost_makes_the_session_cost_unknown() -> None:
    # A1: Akku-Inhalt aus einer Netzladung ohne Bezugspreis hat keinen Einstandspreis
    slot = led(T0, {"battery>wb:twc": 500.0, "pv>wb:twc": 500.0})
    unknown = {T0: SlotCost(T0, 3.0, None, None)}
    by_session, by_vehicle = attribute([slot], unknown, [stored(1, T0, T0 + SLOT)], NOW)
    assert by_session[1].kwh == 1.0 and by_session[1].cost_eur is None
    assert by_vehicle["tesla"].cost_eur is None


def test_service_session_cost_unknown_until_the_battery_price_is_known(
    service, engine, ledger, clock
) -> None:
    from bindaems.app.prices.store import PriceStore

    clock.advance(timedelta(hours=1))
    add_session(engine, T0 + SLOT, T0 + SLOT * 2)
    ingest(ledger, T0, {"grid>battery": 1000.0})  # Netzladung ohne Bezugspreis
    ingest(ledger, T0 + SLOT, {"battery>wb:twc": 500.0})
    day = date(2026, 10, 9)
    [session] = service.sessions(day, day, None)
    assert session.split.battery_kwh == 0.5 and session.split.cost_eur is None
    assert service.by_vehicle(T0, T0 + SLOT * 2)["tesla"].cost_eur is None
    PriceStore(engine, clock).save([price_row(T0, 10.0, "primary")])
    assert ledger.fill_missing_prices() == 1
    [session] = service.sessions(day, day, None)
    assert session.split.cost_eur == pytest.approx(0.5 * (13.44 + 2.0) / 100)


def test_session_without_energy_has_an_empty_split() -> None:
    session = stored(1, T0, T0 + SLOT)
    by_session, by_vehicle = attribute([], {}, [session], NOW)
    assert by_session[1] == FlowSplit(0.0, 0.0, 0.0, 0.0, 0.0)
    assert by_vehicle["tesla"] == by_session[1]


def test_two_wallboxes_in_one_slot_go_to_their_sessions() -> None:
    slot = led(T0, {"pv>wb:twc": 1000.0, "grid>wb:evcs": 2000.0}, price=10.0)
    twc = stored(1, T0, T0 + SLOT, "tesla", "twc")
    evcs = stored(2, T0, T0 + SLOT, "polestar", "evcs")
    by_session, _ = attribute([slot], {T0: cost(T0)}, [twc, evcs], NOW)
    assert by_session[1].pv_kwh == 1.0 and by_session[1].cost_eur == 0.0
    assert by_session[2].grid_kwh == 2.0 and by_session[2].cost_eur == pytest.approx(0.2)


def test_names(cfg) -> None:
    assert vehicle_name(cfg, GUEST) == "Gast" and vehicle_name(cfg, UNKNOWN) == "Unbekannt"
    key = next(iter(cfg.vehicles))
    assert vehicle_name(cfg, key) == cfg.vehicles[key].name
    assert vehicle_name(cfg, "gone") == "gone"
    wallbox = next(iter(cfg.wallboxes))
    assert wallbox_name(cfg, {wallbox: "Garage"}, wallbox) == "Garage"
    assert wallbox_name(cfg, {}, wallbox) in {"EVCS", "Wall Connector"}
    assert wallbox_name(cfg, {}, "gone") == "gone"


def add_session(engine, start, end, vehicle="tesla", wallbox="twc", last_seen=None) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(charging_session).values(
                wallbox=wallbox,
                vehicle=vehicle,
                start=start,
                end=end,
                last_seen=last_seen or end or start,
            )
        )


def test_service_sessions_newest_first_with_split(service, engine, ledger, costing, clock) -> None:
    clock.advance(timedelta(hours=2))
    add_session(engine, T0, T0 + SLOT * 2)
    add_session(engine, T0 + timedelta(hours=1), T0 + timedelta(hours=1, minutes=15), "polestar")
    ingest(ledger, T0, {"pv>wb:twc": 1000.0})
    ingest(ledger, T0 + timedelta(hours=1), {"pv>wb:twc": 500.0})
    day = date(2026, 10, 9)
    found = service.sessions(day, day, None)
    assert [s.vehicle for s in found] == ["polestar", "tesla"]
    assert found[0].split.kwh == 0.5 and found[1].split.kwh == 1.0
    assert found[1].end == T0 + SLOT * 2 and found[1].split.cost_eur == 0.0
    only = service.sessions(day, day, "tesla")
    assert [s.id for s in only] == [found[1].id]
    assert service.sessions(day, day, "unknown") == []


def test_service_open_session_and_by_vehicle(service, engine, ledger, clock) -> None:
    clock.advance(SLOT * 2)
    add_session(engine, T0, None)
    ingest(ledger, T0, {"pv>wb:twc": 1000.0})
    ingest(ledger, T0 + SLOT, {"grid>wb:twc": 1000.0, "battery>wb:twc": 1000.0})
    ingest(ledger, T0 + SLOT * 3, {"pv>wb:twc": 4000.0})  # nach „jetzt“
    [session] = service.sessions(date(2026, 10, 9), date(2026, 10, 9), None)
    assert session.end is None and session.split.kwh == 3.0
    totals = service.by_vehicle(T0, T0 + timedelta(hours=1))
    assert totals["tesla"].kwh == 3.0 and totals["tesla"].pv_kwh == 1.0
    assert totals["tesla"].cost_eur is None  # Netzbezug ohne Bezugspreis


def test_open_session_is_found_per_vehicle_however_old(service, engine, ledger, clock) -> None:
    clock.advance(timedelta(days=100))
    old = T0 - timedelta(days=10)
    add_session(engine, old, None)  # tesla, seit 10 Tagen offen
    for index in range(8):  # acht neuere, beendete Sessions
        start = T0 + timedelta(hours=index)
        add_session(engine, start, start + timedelta(minutes=30), "tesla", "evcs")
    add_session(engine, T0, T0 + SLOT, "polestar", "evcs")
    ingest(ledger, old, {"pv>wb:twc": 2000.0})
    found = service.open_session("tesla")
    assert found is not None and found.end is None and found.start == old
    assert found.split.kwh == 2.0
    assert service.open_session("polestar") is None  # beendet: keine offene
    assert service.open_session("guest") is None


def test_open_session_of_an_unknown_vehicle(service, engine, clock) -> None:
    clock.advance(timedelta(hours=1))
    add_session(engine, T0, None, vehicle="")
    found = service.open_session(UNKNOWN)
    assert found is not None and found.vehicle is None


def test_service_prices_the_grid_share(service, engine, ledger, clock) -> None:
    from bindaems.app.prices.store import PriceStore

    PriceStore(engine, clock).save([price_row(T0, 10.0, "primary")])
    clock.advance(timedelta(hours=1))
    add_session(engine, T0, T0 + SLOT)
    ingest(ledger, T0, {"grid>wb:twc": 1000.0})
    [price] = [slot.import_price_ct for slot in ledger.slots(T0, T0 + SLOT)]
    assert price is not None
    [session] = service.sessions(date(2026, 10, 9), date(2026, 10, 9), None)
    assert session.split.cost_eur == pytest.approx(price / 100)


def test_by_vehicle_of_an_empty_range(service) -> None:
    assert service.by_vehicle(T0, T0) == {}


def test_filtered_out_neighbour_still_wins_the_shared_slot(service, engine, ledger, clock) -> None:
    # tesla und polestar teilen sich den Slot T0; polestar überdeckt ihn länger (10 von 15 min).
    clock.advance(timedelta(hours=2))
    add_session(engine, T0 - SLOT, T0 + timedelta(minutes=5), "tesla")
    add_session(engine, T0 + timedelta(minutes=5), T0 + SLOT * 2, "polestar")
    ingest(ledger, T0 - SLOT, {"pv>wb:twc": 500.0})  # gehört tesla
    ingest(ledger, T0, {"pv>wb:twc": 1000.0})  # gehört polestar
    day = date(2026, 10, 9)
    [only] = service.sessions(day, day, "tesla")
    assert only.split.kwh == 0.5  # nicht die 1,0 kWh des gemeinsamen Slots
    [other] = service.sessions(day, day, "polestar")
    assert other.split.kwh == 1.0


def test_session_beyond_the_limit_still_wins_the_shared_slot(
    service, engine, ledger, clock
) -> None:
    # Die ältere tesla-Session überdeckt den gemeinsamen Slot länger; ``limit=1`` zeigt polestar.
    clock.advance(timedelta(hours=2))
    add_session(engine, T0 - SLOT, T0 + timedelta(minutes=10), "tesla")
    add_session(engine, T0 + timedelta(minutes=10), T0 + SLOT * 2, "polestar")
    ingest(ledger, T0, {"pv>wb:twc": 1000.0})  # gehört tesla
    ingest(ledger, T0 + SLOT, {"pv>wb:twc": 250.0})  # gehört polestar
    day = date(2026, 10, 9)
    [newest] = service.sessions(day, day, None, limit=1)
    assert newest.vehicle == "polestar" and newest.split.kwh == 0.25


class _CountedSession(StoredSession):
    """Zählt jeden Zugriff auf ``start``: ein Maß für die geprüften Überdeckungen."""

    reads = 0

    def __getattribute__(self, name: str):  # type: ignore[no-untyped-def]
        if name == "start":
            type(self).reads += 1
        return super().__getattribute__(name)


def test_attribution_does_not_scan_every_session_per_slot() -> None:
    # 200 Tage mit je einer Session und Ladeenergie in jeder Viertelstunde
    days = 200
    sessions = [
        _CountedSession(
            day + 1,
            "twc",
            "tesla",
            T0 + timedelta(days=day),
            T0 + timedelta(days=day, hours=10),
            T0 + timedelta(days=day, hours=10),
        )
        for day in range(days)
    ]
    slots = [led(T0 + SLOT * index, {"pv>wb:twc": 100.0}) for index in range(days * 96)]
    _CountedSession.reads = 0
    by_session, by_vehicle = attribute(slots, {}, sessions, T0 + timedelta(days=days))
    # früher: jede Session je Slot (200 × 19 200); jetzt wenige je Slot
    assert _CountedSession.reads <= 4 * (len(slots) + len(sessions))
    assert by_vehicle["tesla"].kwh == pytest.approx(days * 40 * 0.1)
    assert by_vehicle[UNKNOWN].kwh == pytest.approx(days * 56 * 0.1)
    assert all(split.kwh == pytest.approx(4.0) for split in by_session.values())


def test_count_by_vehicle_counts_without_attribution(
    service, engine, ledger, clock, monkeypatch
) -> None:
    clock.advance(timedelta(hours=2))
    add_session(engine, T0, T0 + SLOT)
    add_session(engine, T0 + SLOT, T0 + SLOT * 2)
    add_session(engine, T0 + SLOT * 2, T0 + SLOT * 3, "guest")
    add_session(engine, T0 + SLOT * 3, None, "")  # unbekannt
    add_session(engine, T0 - timedelta(days=2), T0 - timedelta(days=2) + SLOT)  # davor
    ingest(ledger, T0, {"pv>wb:twc": 1000.0})

    def no_attribution(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("Zählen braucht keine Zuordnung")

    monkeypatch.setattr("bindaems.app.charging.sessions.attribute", no_attribution)
    day = date(2026, 10, 9)
    assert service.count_by_vehicle(day, day) == {"tesla": 2, GUEST: 1, UNKNOWN: 1}
