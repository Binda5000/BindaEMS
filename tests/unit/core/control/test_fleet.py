from bindaems.core.control.fleet import (
    GUEST,
    GUEST_MIN_A,
    GUEST_NAME,
    CarSpec,
    car_spec,
    fleet,
    guest_key,
    guest_wallbox,
)

# Vorgaben des Plans


def test_fleet_is_the_vehicles_then_one_guest_per_wallbox(cfg) -> None:
    assert fleet(cfg) == ["tesla", "egolf", "guest@evcs", "guest@twc"]
    assert (guest_key("evcs"), guest_wallbox("guest@twc"), guest_wallbox("tesla")) == (
        "guest@evcs",
        "twc",
        None,
    )


def test_a_guest_has_all_phases_and_the_range_of_its_wallbox(cfg) -> None:
    assert car_spec(cfg, "guest@evcs") == CarSpec("Gast", 3, 6, 16, False)
    assert car_spec(cfg, "guest@twc") == CarSpec("Gast", 3, 6, 16, False)
    assert car_spec(cfg, "egolf") == CarSpec("e-Golf", 2, 6, 16, False)
    assert car_spec(cfg, "tesla") == CarSpec("Tesla Model 3", 3, 5, 16, True)


# Ergänzungen


def test_the_guests_follow_the_wallboxes_of_the_configuration(cfg) -> None:
    # die Reihenfolge von ``config.yaml``, nicht ``wallbox_priority``
    turned = cfg.model_copy(update={"wallbox_priority": ["twc", "evcs"]})
    assert fleet(turned)[2:] == ["guest@evcs", "guest@twc"]
    assert all(guest_wallbox(guest_key(key)) == key for key in cfg.wallboxes)
    assert guest_key("evcs").startswith(f"{GUEST}@")


def test_a_guest_takes_the_range_of_its_own_wallbox(cfg) -> None:
    # An der EVCS ihr Strombereich, am Wall Connector GUEST_MIN_A bis zu seinem max_a
    evcs = cfg.wallboxes["evcs"].model_copy(update={"min_a": 7, "safe_a": 7, "max_a": 12})
    twc = cfg.wallboxes["twc"].model_copy(update={"max_a": 10})
    narrow = cfg.model_copy(update={"wallboxes": {"evcs": evcs, "twc": twc}})
    assert car_spec(narrow, "guest@evcs") == CarSpec(GUEST_NAME, 3, 7, 12, False)
    assert car_spec(narrow, "guest@twc") == CarSpec(GUEST_NAME, 3, GUEST_MIN_A, 10, False)
    assert (GUEST_NAME, GUEST_MIN_A) == ("Gast", 6)
