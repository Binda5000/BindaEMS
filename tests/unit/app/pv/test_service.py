from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import insert, select

from bindaems.app.consumers.source import PowerSource
from bindaems.app.db.schema import pv_source_day
from bindaems.app.pv.service import PvSource, PvSourceError, PvSourceInput, PvSourceService


def make(name: str = "Dach", **fields: Any) -> PvSourceInput:
    base: dict[str, Any] = {
        "name": name,
        "color": "#ffaa00",
        "source_kind": "core",
        "power_ref": "pv.huawei.power_w",
    }
    return PvSourceInput(**(base | fields))


def test_pv_source_satisfies_power_source() -> None:
    source: PowerSource = PvSource(
        id=1,
        name="Dach",
        parent_id=None,
        color="#ffaa00",
        source_kind="core",
        power_ref="pv_dc.power_w",
        power_unit=None,
        kwp=None,
        sort=0,
    )
    assert source.id == 1


def test_core_ref_must_be_pv_signal() -> None:
    with pytest.raises(ValidationError, match="PV-Signal"):
        make(power_ref="load.obergeschoss.power_w")
    make(power_ref="pv.huawei.power_w")
    make(power_ref="pv_dc.power_w")


def test_core_ref_has_no_unit() -> None:
    with pytest.raises(ValidationError, match="Einheit"):
        make(power_unit="W")


def test_ha_ref_needs_unit() -> None:
    with pytest.raises(ValidationError, match="Einheit"):
        make(source_kind="ha", power_ref="sensor.pv1_leistung")
    make(source_kind="ha", power_ref="sensor.pv1_leistung", power_unit="W")


def test_kwp_bounds() -> None:
    for bad in (0.05, 1001):
        with pytest.raises(ValidationError):
            make(kwp=bad)
    assert make(kwp=4.1).kwp == 4.1
    assert make(kwp=None).kwp is None


def test_string_cannot_have_children(service: PvSourceService) -> None:
    anlage = service.create(make("A"), actor="chris", source="ui")
    string = service.create(make("S", parent_id=anlage.id), actor="chris", source="ui")
    with pytest.raises(PvSourceError, match="Ein String kann keine Strings haben") as err:
        service.create(make("T", parent_id=string.id), actor="chris", source="ui")
    assert err.value.status == 422


def test_parent_must_exist(service: PvSourceService) -> None:
    with pytest.raises(PvSourceError) as err:
        service.create(make(parent_id=99), actor="chris", source="ui")
    assert err.value.status == 404


def test_cannot_hang_under_itself_or_move_system_with_strings(service: PvSourceService) -> None:
    a = service.create(make("A"), actor="chris", source="ui")
    b = service.create(make("B"), actor="chris", source="ui")
    service.create(make("S", parent_id=a.id), actor="chris", source="ui")
    with pytest.raises(PvSourceError) as err:
        service.update(a.id, make("A", parent_id=a.id), actor="chris", source="ui")
    assert err.value.status == 422
    with pytest.raises(PvSourceError) as err:
        service.update(a.id, make("A", parent_id=b.id), actor="chris", source="ui")
    assert err.value.status == 422
    # ohne Strings darf B unter A
    assert service.update(b.id, make("B", parent_id=a.id), actor="chris", source="ui").parent_id


def test_delete_system_with_strings_rejected(service: PvSourceService, engine) -> None:
    a = service.create(make("A"), actor="chris", source="ui")
    s = service.create(make("S", parent_id=a.id), actor="chris", source="ui")
    with pytest.raises(PvSourceError, match="zuerst die Strings löschen") as err:
        service.delete(a.id, actor="chris", source="ui")
    assert err.value.status == 409
    with engine.begin() as conn:
        conn.execute(
            insert(pv_source_day),
            [
                {"day": "2026-11-07", "source_id": s.id, "kwh": 1.0},
                {"day": "2026-11-07", "source_id": a.id, "kwh": 2.0},
                {"day": "2026-11-07", "source_id": 0, "kwh": 3.0},
            ],
        )
    service.delete(s.id, actor="chris", source="ui")
    service.delete(a.id, actor="chris", source="ui")
    with engine.connect() as conn:
        rows = conn.execute(select(pv_source_day.c.source_id)).scalars().all()
    assert rows == [0]


def test_ha_source_rejected_without_ha_database(engine, clock, audit) -> None:
    service = PvSourceService(engine, clock, audit, ha_enabled=False)
    with pytest.raises(PvSourceError) as err:
        service.create(
            make(source_kind="ha", power_ref="sensor.pv1", power_unit="kW"),
            actor="chris",
            source="ui",
        )
    assert err.value.status == 422
    assert "HA-Datenbank nicht eingerichtet (influxdb.ha_database)" in str(err.value)


def test_list_get_orders_by_sort_and_name(service: PvSourceService) -> None:
    b = service.create(make("b", sort=1), actor="chris", source="ui")
    service.create(make("A", sort=1), actor="chris", source="ui")
    service.create(make("z", sort=0), actor="chris", source="ui")
    assert [s.name for s in service.list()] == ["z", "A", "b"]
    assert service.get(b.id).name == "b"
    with pytest.raises(PvSourceError) as err:
        service.get(99)
    assert err.value.status == 404


def test_changes_are_audited(service: PvSourceService, audit) -> None:
    created = service.create(make("Dach", kwp=4.1), actor="chris", source="ui")
    service.update(created.id, make("Dach Süd", kwp=4.1), actor="chris", source="ui")
    service.delete(created.id, actor="chris", source="ui")
    entries = audit.recent()
    assert [e.action for e in entries] == [
        "pv_source.delete",
        "pv_source.update",
        "pv_source.create",
    ]
    assert {(e.actor, e.source) for e in entries} == {("chris", "ui")}
    assert entries[0].target == "Dach Süd"


def stored_days(engine) -> list[tuple[int, str]]:
    with engine.connect() as conn:
        rows = conn.execute(select(pv_source_day.c.source_id, pv_source_day.c.day)).all()
    return sorted((row[0], row[1]) for row in rows)


def add_days(engine, *source_ids: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(pv_source_day),
            [{"day": "2026-11-07", "source_id": sid, "kwh": 1.0} for sid in source_ids],
        )


def watched(engine, clock, audit, calls: list[None]) -> PvSourceService:
    """Service, der jeden Weckruf des Nachtrags in ``calls`` zählt."""
    return PvSourceService(
        engine, clock, audit, ha_enabled=True, on_change=lambda: calls.append(None)
    )


def test_create_wakes_the_fill(engine, clock, audit) -> None:
    calls: list[None] = []
    service = watched(engine, clock, audit, calls)
    service.create(make("A"), actor="chris", source="ui")
    assert len(calls) == 1


def test_name_colour_kwp_and_parent_keep_the_days(engine, clock, audit) -> None:
    calls: list[None] = []
    service = PvSourceService(engine, clock, audit, ha_enabled=True)
    a = service.create(make("A"), actor="chris", source="ui")
    s = service.create(make("S", parent_id=a.id), actor="chris", source="ui")
    service = watched(engine, clock, audit, calls)
    add_days(engine, 0, a.id, s.id)
    service.update(s.id, make("S2", color="#000000", kwp=4.1, sort=3), actor="c", source="ui")
    service.update(s.id, make("S2", color="#000000", kwp=4.1, sort=3), actor="c", source="ui")
    assert stored_days(engine) == [(0, "2026-11-07"), (a.id, "2026-11-07"), (s.id, "2026-11-07")]
    assert calls == []


@pytest.mark.parametrize(
    "signal",
    [
        {"power_ref": "pv.west.power_w"},
        {"source_kind": "ha", "power_ref": "sensor.pv1", "power_unit": "W"},
    ],
)
def test_signal_change_drops_the_days_and_wakes_the_fill(
    engine, clock, audit, signal: dict[str, Any]
) -> None:
    calls: list[None] = []
    service = PvSourceService(engine, clock, audit, ha_enabled=True)
    a = service.create(make("A"), actor="chris", source="ui")
    b = service.create(make("B"), actor="chris", source="ui")
    service = watched(engine, clock, audit, calls)
    add_days(engine, 0, a.id, b.id)
    service.update(a.id, make("A", **signal), actor="chris", source="ui")
    assert stored_days(engine) == [(0, "2026-11-07"), (b.id, "2026-11-07")]
    assert len(calls) == 1


def test_unit_change_drops_the_days(engine, clock, audit) -> None:
    service = PvSourceService(engine, clock, audit, ha_enabled=True)
    ha = {"source_kind": "ha", "power_ref": "sensor.pv1"}
    a = service.create(make("A", power_unit="W", **ha), actor="chris", source="ui")
    add_days(engine, a.id)
    service.update(a.id, make("A", power_unit="kW", **ha), actor="chris", source="ui")
    assert stored_days(engine) == []


def test_string_becomes_top_level_system(service: PvSourceService) -> None:
    a = service.create(make("A"), actor="chris", source="ui")
    s = service.create(make("S", parent_id=a.id), actor="chris", source="ui")
    moved = service.update(s.id, make("S", parent_id=None), actor="chris", source="ui")
    assert moved.parent_id is None
    assert service.get(s.id).parent_id is None
    service.delete(a.id, actor="chris", source="ui")  # A hat keine Strings mehr
