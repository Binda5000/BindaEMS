import pytest
from pydantic import ValidationError
from tests.app_helpers import core_input

from bindaems.app.consumers.service import ConsumerError, ConsumerInput, ConsumerService


def test_create_list_update_delete_with_audit(service, audit) -> None:
    parent = service.create(
        ConsumerInput(
            name="Obergeschoss",
            color="#336699",
            source_kind="core",
            power_ref="load.obergeschoss.power_w",
        ),
        actor="chris",
        source="ui",
    )
    child = service.create(
        ConsumerInput(
            name=" Küche ",
            color="#aa0000",
            source_kind="ha",
            parent_id=parent.id,
            power_ref="sensor.kueche",
            power_unit="W",
        ),
        actor="chris",
        source="ui",
    )
    assert child.name == "Küche"
    service.update(
        child.id,
        ConsumerInput(
            name="Küche OG",
            color="#aa0000",
            source_kind="ha",
            parent_id=parent.id,
            power_ref="sensor.kueche",
            power_unit="W",
        ),
        actor="chris",
        source="ui",
    )
    assert [c.name for c in service.list()] == ["Küche OG", "Obergeschoss"]
    assert service.get(child.id).parent_id == parent.id
    service.delete(child.id, actor="chris", source="ui")
    assert [e.action for e in audit.recent()] == [
        "consumer.delete",
        "consumer.update",
        "consumer.create",
        "consumer.create",
    ]
    assert audit.recent()[0].target == "Küche OG"


def test_parent_must_exist_and_no_cycles(service) -> None:
    with pytest.raises(ConsumerError, match="Elternelement 99 existiert nicht"):
        service.create(core_input("A", parent_id=99), actor="chris", source="ui")
    a = service.create(core_input("A"), actor="chris", source="ui")
    b = service.create(core_input("B", parent_id=a.id), actor="chris", source="ui")
    with pytest.raises(ConsumerError, match="unter sich selbst"):
        service.update(a.id, core_input("A", parent_id=b.id), actor="chris", source="ui")
    with pytest.raises(ConsumerError, match="unter sich selbst"):
        service.update(a.id, core_input("A", parent_id=a.id), actor="chris", source="ui")


def test_delete_with_children_is_rejected(service) -> None:
    a = service.create(core_input("A"), actor="chris", source="ui")
    service.create(core_input("B", parent_id=a.id), actor="chris", source="ui")
    with pytest.raises(ConsumerError) as error:
        service.delete(a.id, actor="chris", source="ui")
    assert (error.value.status, str(error.value)) == (409, "Verbraucher hat Unterverbraucher")


@pytest.mark.parametrize(
    "data",
    [
        {"source_kind": "core", "power_ref": "load.og.energy_kwh"},
        {"source_kind": "ha", "power_ref": "sensor.kueche"},
        {"source_kind": "core", "power_ref": "load.og.power_w", "power_unit": "W"},
    ],
)
def test_source_validation(data) -> None:
    with pytest.raises(ValidationError):
        ConsumerInput(name="X", color="#000000", **data)


def test_ha_consumer_needs_ha_database(engine, clock, audit) -> None:
    service = ConsumerService(engine, clock, audit, ha_enabled=False)
    data = ConsumerInput(
        name="X", color="#000000", source_kind="ha", power_ref="sensor.x", power_unit="W"
    )
    with pytest.raises(ConsumerError, match="ha_database"):
        service.create(data, actor="chris", source="ui")
