import pytest

from bindaems.core.safety.ranges import check_range
from bindaems.shared.control import Command, CommandKind

K = CommandKind
CASES = [
    (Command("wallbox.evcs", K.CURRENT, 20.0), 16.0, ["range.max"], False),
    (Command("wallbox.evcs", K.CURRENT, 10.7), 10.0, ["range.integer"], False),
    (Command("wallbox.evcs", K.CURRENT, 5.9), None, ["range.integer", "range.min"], True),
    (Command("vehicle.tesla", K.CURRENT, 4.0), None, ["range.min"], True),
    (Command("vehicle.tesla", K.CURRENT, 5.0), 5.0, [], False),
    (Command("wallbox.evcs", K.CHARGING, True), True, [], False),
    (Command("victron", K.GRID_SETPOINT, -300.0), 0.0, ["range.negative"], False),
    (Command("victron", K.GRID_SETPOINT, 9000.0), 8000.0, ["range.ceiling"], False),
    (Command("victron", K.GRID_SETPOINT, None), None, [], False),
    (Command("victron", K.MAX_DISCHARGE, -1.0), -1.0, [], False),
    (Command("victron", K.MAX_DISCHARGE, -5.0), None, ["range.invalid"], True),
    (Command("victron", K.MAX_DISCHARGE, 15000.0), 12000.0, ["range.max"], False),
    (Command("victron", K.MIN_SOC, 15.0), None, ["range.reserve"], True),
    (Command("victron", K.MIN_SOC, 101.0), None, ["range.invalid"], True),
    (Command("wallbox.evcs", K.CURRENT, True), None, ["value.invalid"], True),
    (Command("wallbox.evcs", K.CURRENT, float("nan")), None, ["value.invalid"], True),
    (Command("wallbox.evcs", K.CHARGING, 1.0), None, ["value.invalid"], True),
    (Command("victron", K.MIN_SOC, None), None, ["value.invalid"], True),
]


@pytest.mark.parametrize(("command", "value", "codes", "reject"), CASES)
def test_ranges(limits, command, value, codes, reject) -> None:
    step = check_range(command, limits, None)
    assert (step.value, [f.code for f in step.findings], step.reject) == (value, codes, reject)


def test_budget_lowers_ceiling(limits) -> None:
    step = check_range(Command("victron", K.GRID_SETPOINT, 6000.0), limits, 5000.0)
    assert step.value == 5000.0
    assert step.findings[0].text == "Netz-Sollwert auf 5000 W begrenzt (Leistungsbudget)."


def test_texts(limits) -> None:
    def text(command):
        return check_range(command, limits, None).findings[0].text

    assert text(Command("wallbox.evcs", K.CURRENT, 20.0)) == "Auf den Höchstwert 16 A begrenzt."
    assert (
        text(Command("wallbox.evcs", K.CURRENT, 10.7))
        == "Auf ganze Ampere abgerundet: 10,7 A → 10 A."
    )
    assert text(Command("wallbox.evcs", K.CURRENT, 5.0)) == (
        "Unter dem Mindeststrom 6 A; zum Abschalten das Laden beenden."
    )
    assert text(Command("victron", K.GRID_SETPOINT, -300.0)) == (
        "Netz-Sollwert nie negativ (kein Akku ins Netz): −300 W → 0 W."
    )
    assert (
        text(Command("victron", K.MIN_SOC, 15.0))
        == "Min-SOC 15 % liegt unter der USV-Reserve 20 %."
    )


# Weitere Randfälle der Regeltabelle, die CASES offenlassen: Grenzen selbst, Typen, Reihenfolge.
EDGES = [
    # die Grenzen selbst sind erlaubt
    (Command("wallbox.evcs", K.CURRENT, 16.0), 16.0, [], False),
    (Command("victron", K.GRID_SETPOINT, 0.0), 0.0, [], False),
    (Command("victron", K.GRID_SETPOINT, 8000.0), 8000.0, [], False),
    (Command("victron", K.MAX_DISCHARGE, 0.0), 0.0, [], False),
    (Command("victron", K.MAX_DISCHARGE, 12000.0), 12000.0, [], False),
    (Command("victron", K.MIN_SOC, 20.0), 20.0, [], False),
    (Command("victron", K.MIN_SOC, 100.0), 100.0, [], False),
    (Command("wallbox.evcs", K.CHARGING, False), False, [], False),
    # int zählt als Zahl; heraus kommt ein float
    (Command("wallbox.evcs", K.CURRENT, 10), 10.0, [], False),
    # Strom: jede Regel arbeitet mit dem Ergebnis der vorigen
    (Command("wallbox.evcs", K.CURRENT, 16.5), 16.0, ["range.integer"], False),
    (Command("wallbox.evcs", K.CURRENT, 20.5), 16.0, ["range.integer", "range.max"], False),
    (Command("wallbox.evcs", K.CURRENT, 0.0), None, ["range.min"], True),
    (Command("wallbox.evcs", K.CURRENT, -2.5), None, ["range.integer", "range.min"], True),
    # −1 ist die einzige erlaubte negative Entladegrenze
    (Command("victron", K.MAX_DISCHARGE, -0.5), None, ["range.invalid"], True),
    # falscher Typ oder nicht endlich: ablehnen statt klemmen
    (Command("victron", K.GRID_SETPOINT, True), None, ["value.invalid"], True),
    (Command("victron", K.MAX_DISCHARGE, False), None, ["value.invalid"], True),
    (Command("victron", K.GRID_SETPOINT, float("inf")), None, ["value.invalid"], True),
    (Command("victron", K.MAX_DISCHARGE, float("-inf")), None, ["value.invalid"], True),
    (Command("wallbox.evcs", K.CURRENT, None), None, ["value.invalid"], True),
    (Command("wallbox.evcs", K.CHARGING, None), None, ["value.invalid"], True),
    (Command("wallbox.evcs", K.CHARGING, 1), None, ["value.invalid"], True),
    (Command("wallbox.evcs", K.CURRENT, "10"), None, ["value.invalid"], True),
    (Command("wallbox.evcs", K.CURRENT, 10**400), None, ["value.invalid"], True),  # kein float
    # unbekannte Art (die SafetyLayer prüft sie vorher): im Zweifel ablehnen
    (Command("victron", "bogus", 1.0), None, ["value.invalid"], True),
]


@pytest.mark.parametrize(("command", "value", "codes", "reject"), EDGES)
def test_range_edges(limits, command, value, codes, reject) -> None:
    step = check_range(command, limits, None)
    assert (step.value, [f.code for f in step.findings], step.reject) == (value, codes, reject)
    assert type(step.value) is type(value)  # int wird float, bool bleibt bool


def test_used_up_budget_stops_at_zero(limits) -> None:
    # Ein aufgebrauchtes (negatives) Budget senkt den Sollwert höchstens auf 0 W: nie Akku ins Netz.
    step = check_range(Command("victron", K.GRID_SETPOINT, 300.0), limits, -100.0)
    assert (step.value, [f.code for f in step.findings]) == (0.0, ["range.ceiling"])
    assert step.findings[0].text == "Netz-Sollwert auf 0 W begrenzt (Leistungsbudget)."
    step = check_range(Command("victron", K.GRID_SETPOINT, -300.0), limits, -100.0)
    assert (step.value, [f.code for f in step.findings]) == (0.0, ["range.negative"])


def test_more_texts(limits) -> None:
    def text(command):
        return check_range(command, limits, None).findings[0].text

    assert text(Command("wallbox.evcs", K.CURRENT, True)) == "Ungültiger Wert für „current“: True."
    assert text(Command("victron", K.MIN_SOC, None)) == "Ungültiger Wert für „min_soc“: None."
    assert text(Command("wallbox.evcs", K.CURRENT, "10")) == "Ungültiger Wert für „current“: '10'."
    assert text(Command("wallbox.evcs", K.CURRENT, -2.5)) == (
        "Auf ganze Ampere abgerundet: −2,5 A → −3 A."
    )
    assert text(Command("victron", K.MAX_DISCHARGE, 15000.0)) == (
        "Auf den Höchstwert 12000 W begrenzt."
    )
    assert text(Command("victron", K.MAX_DISCHARGE, -5.0)) == (
        "Wert −5 W liegt außerhalb des zulässigen Bereichs."
    )
    assert text(Command("victron", K.MIN_SOC, 101.0)) == (
        "Wert 101 % liegt außerhalb des zulässigen Bereichs."
    )
    assert text(Command("victron", K.GRID_SETPOINT, 9000.0)) == (
        "Netz-Sollwert auf 8000 W begrenzt (maximaler Netzladesetpoint)."
    )


# Ladelimit des Tesla (Plan 4b, Präzisierung 9): ganze Prozent von 50 bis 100
CHARGE_LIMIT_CASES = [
    (Command("vehicle.tesla", K.CHARGE_LIMIT, 49.0), None, ["range.invalid"], True),
    (Command("vehicle.tesla", K.CHARGE_LIMIT, 101.0), 100.0, ["range.max"], False),
    (Command("vehicle.tesla", K.CHARGE_LIMIT, 80.0), 80.0, [], False),
    (Command("vehicle.tesla", K.CHARGE_LIMIT, 50), 50.0, [], False),
    (Command("vehicle.tesla", K.CHARGE_LIMIT, 100.0), 100.0, [], False),
    (Command("vehicle.tesla", K.CHARGE_LIMIT, 80.6), 80.0, ["range.integer"], False),
    (Command("vehicle.tesla", K.CHARGE_LIMIT, 50.5), 50.0, ["range.integer"], False),
    (
        Command("vehicle.tesla", K.CHARGE_LIMIT, 49.9),
        None,
        ["range.integer", "range.invalid"],
        True,
    ),
    (Command("vehicle.tesla", K.CHARGE_LIMIT, 120.5), 100.0, ["range.integer", "range.max"], False),
    (Command("vehicle.tesla", K.CHARGE_LIMIT, True), None, ["value.invalid"], True),
    (Command("vehicle.tesla", K.CHARGE_LIMIT, None), None, ["value.invalid"], True),
    (Command("vehicle.tesla", K.CHARGE_LIMIT, float("nan")), None, ["value.invalid"], True),
]


@pytest.mark.parametrize(("command", "value", "codes", "reject"), CHARGE_LIMIT_CASES)
def test_charge_limit_range(limits, command, value, codes, reject) -> None:
    step = check_range(command, limits, None)
    assert (step.value, [f.code for f in step.findings], step.reject) == (value, codes, reject)
    assert type(step.value) is type(value)


def test_charge_limit_texts(limits) -> None:
    def texts(value):
        return [
            f.text
            for f in check_range(
                Command("vehicle.tesla", K.CHARGE_LIMIT, value), limits, None
            ).findings
        ]

    assert texts(49.0) == ["Wert 49 % liegt außerhalb des zulässigen Bereichs."]
    assert texts(101.0) == ["Auf den Höchstwert 100 % begrenzt."]
    assert texts(80.6) == ["Auf ganze Prozent abgerundet: 80,6 % → 80 %."]
