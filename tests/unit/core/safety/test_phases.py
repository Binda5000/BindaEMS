from dataclasses import replace

import pytest
from tests.unit.core.safety.conftest import PHASES, make_ctx

from bindaems.core.safety.limits import CurrentRange
from bindaems.core.safety.phases import ACTIVE_PHASE_A, check_phases
from bindaems.core.safety.types import Finding
from bindaems.shared.control import Command, CommandKind

K = CommandKind
IMP = {"L1": 30.0, "L2": 20.0, "L3": 10.0}
ALL = lambda a: {p: a for p in PHASES}  # noqa: E731


def test_evcs_increase_reduced_to_headroom(limits) -> None:
    ctx = make_ctx(
        phase_import_a=IMP,
        wallbox_phase_a={"evcs": ALL(6.0)},
        present={("wallbox.evcs", K.CURRENT): 6.0},
    )
    step = check_phases(Command("wallbox.evcs", K.CURRENT, 16.0), 16.0, ctx, limits)
    assert step.value == 9.0
    assert step.findings == (
        Finding("phase.reduced", "Phase L1 würde über 33 A steigen; reduziert auf 9 A."),
    )


def test_evcs_start_without_headroom_rejected(limits) -> None:
    step = check_phases(
        Command("wallbox.evcs", K.CURRENT, 16.0), 16.0, make_ctx(phase_import_a=IMP), limits
    )
    assert step.reject and step.findings[0].code == "phase.rejected"


def test_decrease_is_never_rejected(limits) -> None:
    ctx = make_ctx(
        phase_import_a={"L1": 45.0, "L2": 20.0, "L3": 10.0},
        wallbox_phase_a={"evcs": ALL(16.0)},
        present={("wallbox.evcs", K.CURRENT): 16.0},
    )
    step = check_phases(Command("wallbox.evcs", K.CURRENT, 10.0), 10.0, ctx, limits)
    assert (step.value, step.reject) == (6.0, False)
    assert (
        step.findings[0].text
        == "Phase L1 würde über 33 A steigen; nur noch Mindeststrom 6 A möglich."
    )


def test_two_phase_car_ignores_unused_phase(limits) -> None:
    ctx = make_ctx(
        phase_import_a={"L1": 20.0, "L2": 20.0, "L3": 32.5},
        wallbox_phase_a={"evcs": {"L1": 10.0, "L2": 10.0, "L3": 0.0}},
        present={("wallbox.evcs", K.CURRENT): 10.0},
    )
    assert check_phases(Command("wallbox.evcs", K.CURRENT, 16.0), 16.0, ctx, limits).value == 16.0


def test_unknown_phase_currents_allow_only_decreases(limits) -> None:
    ctx = make_ctx(
        phase_import_a={"L1": 30.0, "L2": None, "L3": 10.0},
        present={("wallbox.evcs", K.CURRENT): 10.0},
    )
    up = check_phases(Command("wallbox.evcs", K.CURRENT, 12.0), 12.0, ctx, limits)
    assert (
        up.reject and up.findings[0].text == "Phasenströme unbekannt; nur Verringerungen erlaubt."
    )
    assert not check_phases(Command("wallbox.evcs", K.CURRENT, 8.0), 8.0, ctx, limits).reject


def test_tesla_at_known_wallbox(limits) -> None:
    ctx = make_ctx(
        phase_import_a=IMP,
        wallbox_phase_a={"twc": ALL(8.0)},
        present={("vehicle.tesla", K.CURRENT): 8.0},
    )
    assert check_phases(Command("vehicle.tesla", K.CURRENT, 16.0), 16.0, ctx, limits).value == 11.0


def test_tesla_at_unknown_wallbox_uses_all_phases(limits) -> None:
    ctx = make_ctx(phase_import_a=IMP, vehicle_wallbox={"vehicle.tesla": None})
    assert check_phases(Command("vehicle.tesla", K.CURRENT, 16.0), 16.0, ctx, limits).reject


def test_start_checks_set_current(limits) -> None:
    present = {("wallbox.evcs", K.CURRENT): 10.0, ("wallbox.evcs", K.CHARGING): False}
    start, stop = (
        Command("wallbox.evcs", K.CHARGING, True),
        Command("wallbox.evcs", K.CHARGING, False),
    )
    assert check_phases(start, True, make_ctx(phase_import_a=IMP, present=present), limits).reject
    assert not check_phases(
        start, True, make_ctx(phase_import_a=ALL(20.0), present=present), limits
    ).reject
    assert not check_phases(
        stop, False, make_ctx(phase_import_a=IMP, present=present), limits
    ).reject


def test_setpoint_increase_reduced(limits) -> None:
    ctx = make_ctx(phase_import_a=IMP, present={("victron", K.GRID_SETPOINT): None})
    step = check_phases(Command("victron", K.GRID_SETPOINT, 6000.0), 6000.0, ctx, limits)
    assert step.value == 2070.0
    assert step.findings[0].text == "Phase L1 würde über 33 A steigen; reduziert auf 2070 W."
    assert (
        check_phases(Command("victron", K.GRID_SETPOINT, 1000.0), 1000.0, ctx, limits).value
        == 1000.0
    )


def test_setpoint_on_overloaded_phase(limits) -> None:
    over = {"L1": 36.0, "L2": 20.0, "L3": 10.0}
    from_override = make_ctx(phase_import_a=over, present={("victron", K.GRID_SETPOINT): 500.0})
    assert (
        check_phases(
            Command("victron", K.GRID_SETPOINT, 3000.0), 3000.0, from_override, limits
        ).value
        == 0.0
    )
    from_feed_in = make_ctx(
        phase_import_a=over,
        base_grid_setpoint_w=-200.0,
        present={("victron", K.GRID_SETPOINT): None},
    )
    assert check_phases(Command("victron", K.GRID_SETPOINT, 0.0), 0.0, from_feed_in, limits).reject


def test_setpoint_unknown_present_assumes_lowest(limits) -> None:
    ctx = make_ctx(phase_import_a=IMP, base_grid_setpoint_w=-200.0)
    step = check_phases(Command("victron", K.GRID_SETPOINT, 6000.0), 6000.0, ctx, limits)
    assert step.value == 1870.0
    assert step.findings[0].text == "Phase L1 würde über 33 A steigen; reduziert auf 1870 W."


def test_discharge_lock_raised_to_protect_phases(limits) -> None:
    ctx = make_ctx(
        phase_import_a=IMP, battery_discharge_w=9000.0, present={("victron", K.MAX_DISCHARGE): -1.0}
    )
    step = check_phases(Command("victron", K.MAX_DISCHARGE, 0.0), 0.0, ctx, limits)
    assert step.value == 6930.0
    assert (
        step.findings[0].text
        == "Phase L1 würde über 33 A steigen; Entladegrenze auf 6930 W angehoben."
    )


def test_min_soc_above_soc_needs_headroom(limits) -> None:
    ctx = make_ctx(phase_import_a=IMP, battery_discharge_w=9000.0, soc_pct=50.0)
    assert check_phases(Command("victron", K.MIN_SOC, 60.0), 60.0, ctx, limits).reject
    assert not check_phases(Command("victron", K.MIN_SOC, 40.0), 40.0, ctx, limits).reject
    unknown = check_phases(
        Command("victron", K.MIN_SOC, 60.0), 60.0, make_ctx(soc_pct=None), limits
    )
    assert unknown.findings[0].text == "Akku-SOC unbekannt."


# Weitere Randfälle der Regeltabelle, die die Tests oben offenlassen: Grenzen, Erhöhung gegen
# Verringerung, unbekannte und unbrauchbare Eingaben, bindende Phase, Texte.
EVCS, TESLA, VICTRON = "wallbox.evcs", "vehicle.tesla", "victron"
NAN, INF = float("nan"), float("inf")
OVER = {"L1": 36.0, "L2": 20.0, "L3": 10.0}  # L1 liegt 3 A über der Grenze
NO_ROOM = {"L1": 45.0, "L2": 20.0, "L3": 10.0}  # L1 liegt 12 A über der Grenze
UNKNOWN = {"L1": 10.0, "L2": None, "L3": 10.0}  # der Netzbezug von L2 ist unbekannt


def run(limits, actuator, kind, value, **changes):
    """``check_phases`` mit einem Kontext aus ``make_ctx``; prüft die Form einer Ablehnung."""
    step = check_phases(Command(actuator, kind, value), value, make_ctx(**changes), limits)
    if step.reject:
        assert step.value is None
        assert step.findings
    return step


def codes(step) -> list[str]:
    return [finding.code for finding in step.findings]


def wallbox_of(actuator) -> str:
    return "twc" if actuator == TESLA else "evcs"


def change_current(limits, value, imports=IMP, measured=None, actual=None, actuator=EVCS, **more):
    """Strombefehl mit Netzbezug, Strom der Wallbox des Aktors je Phase und Ist-Strom."""
    return run(
        limits,
        actuator,
        K.CURRENT,
        value,
        phase_import_a=imports,
        wallbox_phase_a={} if measured is None else {wallbox_of(actuator): measured},
        present={} if actual is None else {(actuator, K.CURRENT): actual},
        **more,
    )


def start_charging(limits, imports=IMP, measured=None, set_current=None, actuator=EVCS, **more):
    """Start einer Wallbox, die nicht lädt; ``set_current`` ist ihr Ist-Strom, falls bekannt."""
    present = {(actuator, K.CHARGING): False}
    if set_current is not None:
        present[(actuator, K.CURRENT)] = set_current
    return run(
        limits,
        actuator,
        K.CHARGING,
        True,
        phase_import_a=imports,
        wallbox_phase_a={} if measured is None else {wallbox_of(actuator): measured},
        present=present,
        **more,
    )


def set_setpoint(limits, value, imports=IMP, **more):
    return run(limits, VICTRON, K.GRID_SETPOINT, value, phase_import_a=imports, **more)


def set_discharge_limit(limits, value, discharge, imports=IMP, **more):
    return run(
        limits,
        VICTRON,
        K.MAX_DISCHARGE,
        value,
        phase_import_a=imports,
        battery_discharge_w=discharge,
        **more,
    )


def set_min_soc(limits, value, imports=IMP, **more):
    return run(limits, VICTRON, K.MIN_SOC, value, phase_import_a=imports, **more)


# --- Betroffene und bindende Phasen


def test_phase_is_active_from_half_an_ampere(limits) -> None:
    assert ACTIVE_PHASE_A == 0.5

    def third_phase(l3_measured):
        measured = {"L1": 10.0, "L2": 10.0, "L3": l3_measured}
        return change_current(limits, 16.0, {"L1": 20.0, "L2": 20.0, "L3": 32.5}, measured, 10.0)

    active = third_phase(ACTIVE_PHASE_A)  # Spielraum 0,5 A + 0,5 A Eigenstrom: nur 1 A
    assert active.reject and active.findings[0].text == "Phase L3 würde über 33 A steigen."
    assert third_phase(0.49).value == 16.0


def test_current_binding_phase_has_the_least_room_plus_measured(limits) -> None:
    # Bindend ist L2, nicht L1: Dort ist Spielraum plus Strom der Wallbox am kleinsten.
    def with_l2_import(l2_import):
        imports = {"L1": 30.0, "L2": l2_import, "L3": 10.0}
        return change_current(limits, 16.0, imports, {"L1": 10.0, "L2": 2.0, "L3": 0.0}, 10.0)

    reduced = with_l2_import(29.0)  # Spielraum je Phase: L1 13 A, L2 6 A
    assert reduced.value == 6.0
    assert reduced.findings[0].text == "Phase L2 würde über 33 A steigen; reduziert auf 6 A."
    rejected = with_l2_import(31.0)  # L2: nur 4 A
    assert rejected.reject and rejected.findings[0].text == "Phase L2 würde über 33 A steigen."


def test_ties_go_to_the_first_phase(limits) -> None:
    def text(imports, hard_limits=limits):
        return change_current(hard_limits, 16.0, imports).findings[0].text

    assert text(ALL(30.0)) == "Phase L1 würde über 33 A steigen."
    assert text({"L1": 10.0, "L2": 30.0, "L3": 30.0}) == "Phase L2 würde über 33 A steigen."
    mixed_up = {**limits.wallbox_phases, "evcs": ("L3", "L2", "L1")}
    assert text(ALL(30.0), replace(limits, wallbox_phases=mixed_up)).startswith("Phase L1 ")


def test_only_affected_phases_need_a_known_current(limits) -> None:
    def with_l2_measured(l2_measured):
        measured = {"L1": 10.0, "L2": l2_measured, "L3": 0.0}
        return change_current(limits, 16.0, {"L1": 20.0, "L2": None, "L3": 10.0}, measured, 10.0)

    assert with_l2_measured(0.0).value == 16.0  # L2 ungenutzt: sein unbekannter Strom egal
    assert codes(with_l2_measured(10.0)) == ["phase.unknown"]  # L2 aktiv: Erhöhung abgelehnt


@pytest.mark.parametrize(
    "imports",
    [
        {"L1": 30.0, "L3": 10.0},  # L2 fehlt
        {"L1": 30.0, "L2": NAN, "L3": 10.0},  # nicht endlich
        {"L1": 30.0, "L2": INF, "L3": 10.0},
        {"L1": 30.0, "L2": 10**400, "L3": 10.0},  # kein float
    ],
)
def test_missing_or_unusable_phase_current_is_unknown(limits, imports) -> None:
    step = change_current(limits, 12.0, imports, actual=10.0)
    assert codes(step) == ["phase.unknown"]
    assert not change_current(limits, 8.0, imports, actual=10.0).reject


def test_limit_in_texts_has_a_decimal_comma(limits) -> None:
    step = change_current(replace(limits, phase_limit_a=33.5), 16.0, ALL(30.0))
    assert step.findings[0].text == "Phase L1 würde über 33,5 A steigen."


# --- Strom (current)


def test_current_allowed_is_the_floored_room(limits) -> None:
    def with_l1_import(l1_import, value=16.0):
        return change_current(limits, value, {**IMP, "L1": l1_import})

    at_minimum = with_l1_import(26.1)  # Spielraum 6,9 A: erlaubt 6 A, genau der Mindeststrom
    assert (at_minimum.value, codes(at_minimum)) == (6.0, ["phase.reduced"])
    assert with_l1_import(26.1, 6.0).findings == ()  # der erlaubte Strom selbst bleibt
    assert with_l1_import(27.1).reject  # Spielraum 5,9 A: erlaubt 5 A, weniger als der Mindeststrom


def test_current_decrease_goes_down_to_allowed(limits) -> None:
    imports, measured = {"L1": 33.0, "L2": 20.0, "L3": 10.0}, ALL(10.0)  # erlaubt: 10 A
    step = change_current(limits, 14.0, imports, measured, 16.0)
    assert (step.value, codes(step)) == (10.0, ["phase.reduced"])
    assert step.findings[0].text == "Phase L1 würde über 33 A steigen; reduziert auf 10 A."
    assert change_current(limits, 10.0, imports, measured, 16.0).findings == ()


@pytest.mark.parametrize(
    ("value", "actual", "expected", "expected_codes"),
    [
        (10.0, 16.0, 6.0, ["phase.minimum"]),  # Verringerung: auf den Mindeststrom
        (6.0, 16.0, 6.0, ["phase.minimum"]),  # schon der Mindeststrom: der Befund bleibt
        (10.0, 10.0, 6.0, ["phase.minimum"]),  # gleicher Wert ist keine Erhöhung
        (11.0, 10.0, None, ["phase.rejected"]),  # Erhöhung: abgelehnt
    ],
)
def test_current_without_room(limits, value, actual, expected, expected_codes) -> None:
    step = change_current(limits, value, NO_ROOM, ALL(16.0), actual)  # erlaubt: 4 A
    assert (step.value, codes(step)) == (expected, expected_codes)


@pytest.mark.parametrize("actual", [None, NAN, True])
def test_current_above_safe_with_unknown_actual_counts_as_increase(limits, actual) -> None:
    step = change_current(limits, 16.0, actual=actual)  # erlaubt: 3 A
    assert step.reject and codes(step) == ["phase.rejected"]


@pytest.mark.parametrize("actual", [None, NAN, True])
@pytest.mark.parametrize("actuator", [EVCS, TESLA])
def test_safe_current_with_unknown_actual_is_no_increase(limits, actuator, actual) -> None:
    safe_a, min_a = float(limits.currents[actuator].safe_a), limits.currents[actuator].min_a
    # Phasenströme unbekannt (veraltete Daten): der sichere Strom bleibt, wie jede Verringerung
    stale = change_current(limits, safe_a, dict.fromkeys(PHASES), actual=actual, actuator=actuator)
    assert (stale.value, stale.findings) == (safe_a, ())
    # kein Spielraum: auf den Mindeststrom statt einer Ablehnung, mit dem Befund dazu
    crowded = change_current(limits, safe_a, NO_ROOM, actual=actual, actuator=actuator)
    assert (crowded.value, codes(crowded)) == (float(min_a), ["phase.minimum"])
    assert crowded.findings[0].text == (
        f"Phase L1 würde über 33 A steigen; nur noch Mindeststrom {min_a} A möglich."
    )


@pytest.mark.parametrize("actuator", [EVCS, TESLA])
def test_current_just_above_safe_with_unknown_actual_is_an_increase(limits, actuator) -> None:
    above_safe = limits.currents[actuator].safe_a + 1.0
    stale = change_current(limits, above_safe, dict.fromkeys(PHASES), actuator=actuator)
    assert stale.reject and codes(stale) == ["phase.unknown"]
    crowded = change_current(limits, above_safe, NO_ROOM, actuator=actuator)
    assert crowded.reject and codes(crowded) == ["phase.rejected"]


@pytest.mark.parametrize("actuator", [EVCS, TESLA])
def test_safe_current_above_a_known_actual_is_an_increase(limits, actuator) -> None:
    # Nur ein unbekannter Ist-Strom nimmt dem sicheren Strom das Gewicht: Bei bekannten 0 A
    # ist er ein Anstieg.
    safe_a = float(limits.currents[actuator].safe_a)
    step = change_current(limits, safe_a, dict.fromkeys(PHASES), actual=0.0, actuator=actuator)
    assert step.reject and codes(step) == ["phase.unknown"]


def test_safe_current_threshold_is_safe_a_not_min_a(limits) -> None:
    # Mindeststrom 6 A, sicherer Strom 10 A; der Ist-Strom ist unbekannt
    safe_above_minimum = replace(
        limits, currents={**limits.currents, EVCS: CurrentRange(6, 16, 10)}
    )

    def unknown_actual(value, imports):
        return change_current(safe_above_minimum, value, imports)

    stale = dict.fromkeys(PHASES)
    for value in (8.0, 10.0):  # nicht über dem sicheren Strom, 8 A aber über dem Mindeststrom
        step = unknown_actual(value, stale)
        assert (step.value, step.findings) == (value, ())
    assert codes(unknown_actual(11.0, stale)) == ["phase.unknown"]

    crowded = unknown_actual(10.0, NO_ROOM)  # kein Spielraum: wirklich auf den Mindeststrom
    assert (crowded.value, codes(crowded)) == (6.0, ["phase.minimum"])
    some_room = unknown_actual(10.0, {"L1": 25.0, "L2": 20.0, "L3": 10.0})  # erlaubt: 8 A
    assert (some_room.value, codes(some_room)) == (8.0, ["phase.reduced"])
    assert some_room.findings[0].text == "Phase L1 würde über 33 A steigen; reduziert auf 8 A."


def test_current_decrease_stops_at_the_minimum_not_the_safe_current(limits) -> None:
    safe_above_minimum = {**limits.currents, EVCS: CurrentRange(6, 16, 10)}
    step = change_current(
        replace(limits, currents=safe_above_minimum), 12.0, NO_ROOM, ALL(16.0), 16.0
    )
    assert step.value == 6.0


def test_tesla_decrease_stops_at_its_own_minimum(limits) -> None:
    step = change_current(limits, 10.0, NO_ROOM, ALL(16.0), 16.0, actuator=TESLA)
    assert step.value == 5.0
    assert (
        step.findings[0].text
        == "Phase L1 würde über 33 A steigen; nur noch Mindeststrom 5 A möglich."
    )


@pytest.mark.parametrize(
    ("vehicle_wallbox", "expected"),
    [
        ({TESLA: "twc"}, 11.0),  # seine Wallbox: 8 A Eigenstrom, Spielraum 3 A
        ({TESLA: "evcs"}, 5.0),  # eine andere Wallbox: dort sind es 2 A Eigenstrom
        ({TESLA: None}, None),  # unbekannt: alle Phasen ohne Eigenstrom, Spielraum 3 A
        ({}, None),  # nicht eingetragen: ebenso
        ({TESLA: "garage"}, None),  # nicht konfiguriert: ebenso, die Messung zählt nicht
    ],
)
def test_tesla_uses_the_current_of_its_own_wallbox(limits, vehicle_wallbox, expected) -> None:
    step = run(
        limits,
        TESLA,
        K.CURRENT,
        16.0,
        phase_import_a=IMP,
        vehicle_wallbox=vehicle_wallbox,
        wallbox_phase_a={"twc": ALL(8.0), "evcs": ALL(2.0), "garage": ALL(8.0)},
        present={(TESLA, K.CURRENT): 8.0},
    )
    assert step.value == expected


# --- Laden an/aus (charging)


@pytest.mark.parametrize(
    ("present", "expected", "expected_codes"),
    [
        ({(EVCS, K.CHARGING): True, (EVCS, K.CURRENT): 10.0}, True, []),  # läuft schon
        ({(EVCS, K.CHARGING): False, (EVCS, K.CURRENT): 10.0}, None, ["phase.rejected"]),
        ({(EVCS, K.CURRENT): 10.0}, None, ["phase.rejected"]),  # Zustand unbekannt: wie ein Start
    ],
)
def test_start_is_only_an_increase_if_not_running(
    limits, present, expected, expected_codes
) -> None:
    step = run(limits, EVCS, K.CHARGING, True, phase_import_a=IMP, present=present)
    assert (step.value, codes(step)) == (expected, expected_codes)


@pytest.mark.parametrize("actuator", [EVCS, TESLA])
@pytest.mark.parametrize(
    ("set_current", "reject"),
    [(None, True), (13.0, False), (14.0, True), (6.0, False), (True, True), (NAN, True)],
)
def test_start_assumes_set_current_or_maximum(limits, actuator, set_current, reject) -> None:
    # Spielraum 13 A auf jeder Phase; ohne Ist-Strom gilt der Höchststrom von 16 A
    step = start_charging(limits, ALL(20.0), set_current=set_current, actuator=actuator)
    assert step.reject is reject


def test_start_checks_all_three_phases(limits) -> None:
    # Auch eine Phase, auf der die Wallbox gerade nichts zieht, kann beim Start Strom bekommen.
    imports = {"L1": 20.0, "L2": 20.0, "L3": 32.5}
    step = start_charging(limits, imports, {"L1": 10.0, "L2": 10.0, "L3": 0.0}, 10.0)
    assert step.reject and step.findings[0].text == "Phase L3 würde über 33 A steigen."


def test_start_counts_the_measured_current_as_its_own(limits) -> None:
    measured = {"L1": 4.0, "L2": 0.0, "L3": 0.0}
    assert start_charging(limits, IMP, measured, 7.0).value is True  # L1: 3 A + 4 A gemessen
    assert start_charging(limits, IMP, measured, 8.0).reject


def test_start_names_the_phase_that_is_over(limits) -> None:
    # L1 hat den kleinsten Spielraum, mit 10 A Eigenstrom reicht er aber; über ist nur L2.
    imports = {"L1": 30.0, "L2": 29.0, "L3": 10.0}
    step = start_charging(limits, imports, {"L1": 10.0, "L2": 2.0, "L3": 0.0}, 10.0)
    assert step.reject and step.findings[0].text == "Phase L2 würde über 33 A steigen."


def test_start_with_unknown_phase_currents(limits) -> None:
    start = start_charging(limits, UNKNOWN, set_current=6.0)
    assert start.reject and start.findings[0].text == (
        "Phasenströme unbekannt; nur Verringerungen erlaubt."
    )
    stop = run(limits, EVCS, K.CHARGING, False, phase_import_a=UNKNOWN)
    assert stop.value is False


def test_tesla_start_uses_all_phases_at_an_unknown_wallbox(limits) -> None:
    at_twc = start_charging(limits, IMP, ALL(8.0), 8.0, actuator=TESLA)
    assert at_twc.value is True  # Spielraum 3 A + 8 A Eigenstrom: reicht
    unknown = {"vehicle_wallbox": {TESLA: None}}  # ohne Eigenstrom reichen 3 A nicht
    assert start_charging(limits, IMP, ALL(8.0), 8.0, actuator=TESLA, **unknown).reject
    assert start_charging(limits, ALL(20.0), ALL(8.0), 8.0, actuator=TESLA, **unknown).value


# --- Netz-Sollwert (grid_setpoint)


def test_setpoint_none_changes_nothing(limits) -> None:
    for imports in (IMP, OVER, UNKNOWN):
        step = set_setpoint(limits, None, imports)
        assert (step.value, step.findings) == (None, ())


@pytest.mark.parametrize("imports", [OVER, UNKNOWN])
@pytest.mark.parametrize("value", [400.0, 500.0])
@pytest.mark.parametrize(
    "ctx",
    [
        {"present": {(VICTRON, K.GRID_SETPOINT): 500.0}},  # Override
        {"present": {(VICTRON, K.GRID_SETPOINT): None}, "base_grid_setpoint_w": 500.0},  # Basis
    ],
)
def test_setpoint_not_above_actual_is_unchanged(limits, imports, value, ctx) -> None:
    step = set_setpoint(limits, value, imports, **ctx)
    assert (step.value, step.findings) == (value, ())


@pytest.mark.parametrize(
    ("present", "expected"),
    [
        ({(VICTRON, K.GRID_SETPOINT): None}, 2570.0),  # kein Override: der Basis-Sollwert 500 W
        ({(VICTRON, K.GRID_SETPOINT): 1000.0}, 3000.0),  # Override 1000 W: Grenze 3070 W
        ({}, 2070.0),  # unbekannt: min(500, 0) = 0 W
        ({(VICTRON, K.GRID_SETPOINT): True}, 2070.0),  # unbrauchbar: wie unbekannt
        ({(VICTRON, K.GRID_SETPOINT): NAN}, 2070.0),
    ],
)
def test_setpoint_effective_actual_decides_the_limit(limits, present, expected) -> None:
    # engster Spielraum 3 A, das sind 2070 W über dem Ist-Sollwert
    step = set_setpoint(limits, 3000.0, base_grid_setpoint_w=500.0, present=present)
    assert step.value == expected


def test_setpoint_is_floored_and_may_reach_the_limit(limits) -> None:
    override = {(VICTRON, K.GRID_SETPOINT): 100.75}  # Grenze: 100,75 W + 2070 W
    step = set_setpoint(limits, 5000.0, present=override)
    assert step.value == 2170.0
    assert step.findings[0].text == "Phase L1 würde über 33 A steigen; reduziert auf 2170 W."
    at_limit = set_setpoint(limits, 2170.75, present=override)
    assert (at_limit.value, at_limit.findings) == (2170.75, ())


def test_setpoint_without_room(limits) -> None:
    on_limit = {"L1": 33.0, "L2": 20.0, "L3": 10.0}  # Spielraum 0: noch nicht negativ
    step = set_setpoint(limits, 100.0, on_limit)
    assert (step.value, codes(step)) == (0.0, ["phase.reduced"])

    zero_override = {(VICTRON, K.GRID_SETPOINT): 0.0}
    step = set_setpoint(limits, 3000.0, OVER, present=zero_override)
    assert step.value == 0.0
    assert step.findings[0].text == "Phase L1 würde über 33 A steigen; reduziert auf 0 W."

    feed_in = {"base_grid_setpoint_w": -200.0, "present": {(VICTRON, K.GRID_SETPOINT): None}}
    step = set_setpoint(limits, 0.0, OVER, **feed_in)
    assert step.reject and step.findings[0].text == "Phase L1 würde über 33 A steigen."

    # Grenze genau 0 W (−690 W + 1 A · 690 V): noch kein Fall für eine Ablehnung
    to_zero = {"base_grid_setpoint_w": -690.0, "present": {(VICTRON, K.GRID_SETPOINT): None}}
    step = set_setpoint(limits, 100.0, {"L1": 32.0, "L2": 20.0, "L3": 10.0}, **to_zero)
    assert (step.value, codes(step)) == (0.0, ["phase.reduced"])


def test_setpoint_names_the_phase_with_the_least_room(limits) -> None:
    step = set_setpoint(
        limits,
        6000.0,
        {"L1": 10.0, "L2": 30.0, "L3": 20.0},
        wallbox_phase_a={"evcs": ALL(10.0)},  # zählt nicht: das ESS verteilt gleichmäßig
        present={(VICTRON, K.GRID_SETPOINT): None},
    )
    assert step.findings[0].text == "Phase L2 würde über 33 A steigen; reduziert auf 2070 W."


def test_setpoint_increase_with_unknown_phase_currents_is_rejected(limits) -> None:
    step = set_setpoint(limits, 1000.0, UNKNOWN, present={(VICTRON, K.GRID_SETPOINT): None})
    assert step.reject and codes(step) == ["phase.unknown"]


# --- Entladegrenze (max_discharge)


@pytest.mark.parametrize("imports", [OVER, UNKNOWN])
@pytest.mark.parametrize(
    ("value", "discharge"),
    [
        (-1.0, 9000.0),  # unbegrenzt
        (-1.0, None),
        (9000.0, 9000.0),  # nicht unter der jetzigen Entladeleistung
        (12000.0, 9000.0),
    ],
)
def test_discharge_limit_that_costs_no_import_is_unchanged(
    limits, imports, value, discharge
) -> None:
    step = set_discharge_limit(limits, value, discharge, imports)
    assert (step.value, step.findings) == (value, ())


@pytest.mark.parametrize(
    ("value", "discharge", "expected"),
    [
        (6930.0, 9000.0, 6930.0),  # Untergrenze 9000 W − 2070 W: genau erreicht
        (6929.0, 9000.0, 6930.0),
        (0.0, 9000.4, 6931.0),  # 6930,4 W wird aufgerundet
        (0.0, 100.0, 0.0),  # Untergrenze negativ: nichts zu schützen
        (0.0, 14070.0, 12000.0),  # Untergrenze genau die maximale Entladeleistung
        (0.0, 14071.0, -1.0),  # darüber: unbegrenzt
    ],
)
def test_discharge_limit_is_raised_to_the_lower_bound(limits, value, discharge, expected) -> None:
    step = set_discharge_limit(limits, value, discharge)
    assert step.value == expected
    assert codes(step) == ([] if expected == value else ["phase.discharge"])


def test_discharge_texts_and_binding_phase(limits) -> None:
    over = set_discharge_limit(limits, 0.0, 4000.0, OVER)
    assert over.value == 6070.0  # 3 A über der Grenze: 2070 W mehr als die jetzigen 4000 W
    assert over.findings[0].text == (
        "Phase L1 würde über 33 A steigen; Entladegrenze auf 6070 W angehoben."
    )
    unlimited = set_discharge_limit(limits, 0.0, 14000.0, {"L1": 32.0, "L2": 20.0, "L3": 10.0})
    assert unlimited.value == -1.0
    assert unlimited.findings[0].text == (
        "Phase L1 würde über 33 A steigen; Entladegrenze auf −1 W angehoben."
    )
    named = set_discharge_limit(limits, 0.0, 9000.0, {"L1": 10.0, "L2": 30.0, "L3": 20.0})
    assert named.findings[0].text == (
        "Phase L2 würde über 33 A steigen; Entladegrenze auf 6930 W angehoben."
    )


@pytest.mark.parametrize(
    ("value", "actual", "reject"),
    [
        (0.0, None, True),  # bisherige Grenze unbekannt
        (0.0, -1.0, True),  # bisher unbegrenzt
        (500.0, 1000.0, True),  # strenger als bisher
        (500.0, 500.0, False),  # gleich
        (500.0, 400.0, False),  # lockerer
        (0.0, 0.0, False),
        (-1.0, None, False),  # unbegrenzt
    ],
)
def test_discharge_limit_with_unknown_battery_power(limits, value, actual, reject) -> None:
    present = {} if actual is None else {(VICTRON, K.MAX_DISCHARGE): actual}
    step = set_discharge_limit(limits, value, None, present=present)
    assert (step.value, codes(step)) == (
        (None, ["phase.battery_unknown"]) if reject else (value, [])
    )
    if reject:
        assert step.findings[0].text == "Akkuleistung unbekannt."


def test_discharge_limit_with_unknown_phase_currents(limits) -> None:
    step = set_discharge_limit(limits, 0.0, 9000.0, UNKNOWN)
    assert step.reject and codes(step) == ["phase.unknown"]
    both = set_discharge_limit(limits, 0.0, None, UNKNOWN)
    assert codes(both) == ["phase.battery_unknown"]  # die Akkuleistung wird zuerst gebraucht


# --- Min-SOC (min_soc)


@pytest.mark.parametrize("imports", [OVER, UNKNOWN])
@pytest.mark.parametrize(
    ("value", "soc", "discharge", "expected_codes"),
    [
        (20.0, None, None, []),  # nicht über der Reserve von 20 %: SOC und Leistung egal
        (20.0, 10.0, None, []),
        (20.0, 50.0, 1000.0, []),  # die Reserve selbst: ob die Phasenströme bekannt sind, egal
        (50.0, 50.0, None, []),  # nicht über dem SOC
        (40.0, 50.0, None, []),
        (40.0, 50.0, 1000.0, []),
        (20.5, None, None, ["phase.soc_unknown"]),  # knapp über der Reserve
        (50.5, 50.0, None, ["phase.battery_unknown"]),  # knapp über dem SOC
        (60.0, None, None, ["phase.soc_unknown"]),  # beides unbekannt: der SOC zuerst
        (60.0, NAN, 1000.0, ["phase.soc_unknown"]),
        (60.0, 50.0, None, ["phase.battery_unknown"]),
        (60.0, 50.0, NAN, ["phase.battery_unknown"]),
    ],
)
def test_min_soc_needs_soc_and_battery_power_to_be_known(
    limits, imports, value, soc, discharge, expected_codes
) -> None:
    # L1 liegt über der Grenze oder L2 ist unbekannt; nur wo es eine Erhöhung ist, kommt es
    # darauf überhaupt an, und selbst dann prüfen SOC und Leistung vor den Phasen.
    step = set_min_soc(limits, value, imports, soc_pct=soc, battery_discharge_w=discharge)
    assert codes(step) == expected_codes
    assert step.reject == bool(expected_codes)
    if not expected_codes:
        assert step.value == value


def test_min_soc_with_unknown_phase_currents_is_rejected(limits) -> None:
    step = set_min_soc(limits, 60.0, UNKNOWN, battery_discharge_w=1000.0)
    assert codes(step) == ["phase.unknown"]


def test_min_soc_checks_the_phase_after_the_discharge_stops(limits) -> None:
    on_limit = set_min_soc(limits, 60.0, battery_discharge_w=2070.0)  # 30 A + 2070 W / 690 V = 33 A
    assert (on_limit.value, on_limit.findings) == (60.0, ())
    over = set_min_soc(limits, 60.0, battery_discharge_w=2071.0)
    assert over.reject and over.findings[0].text == "Phase L1 würde über 33 A steigen."
    # eine Phase über der Grenze bleibt auch ohne Entladung darüber
    assert set_min_soc(limits, 60.0, OVER, battery_discharge_w=0.0).reject
    named = set_min_soc(
        limits, 60.0, {"L1": 10.0, "L2": 30.0, "L3": 20.0}, battery_discharge_w=9000.0
    )
    assert named.findings[0].text == "Phase L2 würde über 33 A steigen."


# --- Sicherer Zustand


@pytest.mark.parametrize("actuator", [VICTRON, EVCS, TESLA])
@pytest.mark.parametrize(
    ("imports", "untouched"),
    [
        (dict.fromkeys(PHASES), True),  # veraltete Messwerte (Spec 8.4): nichts ist bekannt
        (UNKNOWN, True),
        (NO_ROOM, False),  # überlastete Phase: der Strom geht höchstens auf den Mindeststrom
    ],
)
def test_safe_state_commands_are_never_rejected(limits, actuator, imports, untouched) -> None:
    ctx = make_ctx(phase_import_a=imports, soc_pct=None, battery_discharge_w=None, present={})
    commands = limits.safe_commands(actuator)
    assert commands
    for command in commands:
        step = check_phases(command, command.value, ctx, limits)
        assert not step.reject, command
        if untouched:
            assert (step.value, step.findings) == (command.value, ())


# --- Falsche Werte, unbrauchbare Zahlen, Typ des Ergebnisses


@pytest.mark.parametrize(
    ("actuator", "kind", "value"),
    [
        (EVCS, K.CURRENT, True),
        (EVCS, K.CURRENT, None),
        (EVCS, K.CURRENT, NAN),
        (EVCS, K.CURRENT, "10"),
        (EVCS, K.CURRENT, 10**400),  # kein float
        (EVCS, K.CHARGING, 1.0),
        (EVCS, K.CHARGING, None),
        (VICTRON, K.GRID_SETPOINT, True),
        (VICTRON, K.MAX_DISCHARGE, None),
        (VICTRON, K.MIN_SOC, INF),
        (VICTRON, "bogus", 1.0),
    ],
)
def test_wrong_value_or_unknown_kind_is_rejected(limits, actuator, kind, value) -> None:
    step = run(limits, actuator, kind, value, phase_import_a=ALL(0.0))
    assert (step.value, codes(step)) == (None, ["value.invalid"])


def test_invalid_value_text(limits) -> None:
    def text(value):
        return change_current(limits, value, ALL(0.0)).findings[0].text

    assert text(True) == "Ungültiger Wert für „current“: True."
    assert text("10") == "Ungültiger Wert für „current“: '10'."


def test_unusable_numbers_count_as_unknown(limits) -> None:
    nan_discharge = set_discharge_limit(limits, 0.0, NAN)
    assert codes(nan_discharge) == ["phase.battery_unknown"]
    assert codes(set_min_soc(limits, 60.0, soc_pct=NAN)) == ["phase.soc_unknown"]
    assert codes(set_min_soc(limits, 60.0, battery_discharge_w=NAN)) == ["phase.battery_unknown"]

    def with_measured(measured):
        return change_current(limits, 16.0, IMP, measured, 10.0)

    # ein nicht endlicher Messwert gilt wie ein fehlender: kein Strom, also nicht aktiv
    assert with_measured({"L1": NAN, "L2": 10.0, "L3": 10.0}) == with_measured(
        {"L2": 10.0, "L3": 10.0}
    )
    assert with_measured({"L1": NAN, "L2": 0.0, "L3": 0.0}) == with_measured({})


def test_numbers_come_out_as_float(limits) -> None:
    # Die Eingabe ist ein int; heraus kommt immer ein float.
    steps = [
        change_current(limits, 8, ALL(10.0)),  # unverändert
        change_current(limits, 16, IMP, ALL(6.0), 6.0),  # reduziert auf 9 A
        set_setpoint(limits, 1000),
        set_setpoint(limits, 6000),  # reduziert auf 2070 W
        set_discharge_limit(limits, 6930, 9000.0),
        set_discharge_limit(limits, 0, 9000.0),  # angehoben auf 6930 W
        set_min_soc(limits, 40),
    ]
    assert [step.value for step in steps] == [8.0, 9.0, 1000.0, 2070.0, 6930.0, 6930.0, 40.0]
    assert all(type(step.value) is float for step in steps)
