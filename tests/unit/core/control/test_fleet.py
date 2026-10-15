from bindaems.core.control.fleet import GUEST, fleet, guest_key, guest_wallbox

# Vorgaben des Plans


def test_fleet_is_the_vehicles_then_one_guest_per_wallbox(cfg) -> None:
    assert fleet(cfg) == ["tesla", "egolf", "guest@evcs", "guest@twc"]
    assert (guest_key("evcs"), guest_wallbox("guest@twc"), guest_wallbox("tesla")) == (
        "guest@evcs",
        "twc",
        None,
    )


# Ergänzungen


def test_the_guests_follow_the_wallboxes_of_the_configuration(cfg) -> None:
    # die Reihenfolge von ``config.yaml``, nicht ``wallbox_priority``
    turned = cfg.model_copy(update={"wallbox_priority": ["twc", "evcs"]})
    assert fleet(turned)[2:] == ["guest@evcs", "guest@twc"]
    assert all(guest_wallbox(guest_key(key)) == key for key in cfg.wallboxes)
    assert guest_key("evcs").startswith(f"{GUEST}@")
