from datetime import datetime
from typing import Any

from tests.helpers import T0
from tests.unit.core.control.charge_helpers import inputs

from bindaems.core.control.charging import ChargeController
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.regulation import Regulation, RegulationInputs
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import Outcome, Verdict
from bindaems.shared.control import Command, CommandKind
from bindaems.shared.decisions import DecisionResult


def test_regulation_passes_the_charging_through(cfg) -> None:
    limits = HardLimits.from_config(cfg)
    regulation = Regulation(cfg, limits, started=T0)
    charger = ChargeController(cfg, limits, started=T0)
    found = regulation.step(RegulationInputs(inputs(cfg)))
    assert found  # „Laden aus“ für den e-Golf in PV-Überschuss
    assert found == charger.step(inputs(cfg))
    assert regulation.health()["vehicles"] == charger.health()
    assert regulation.vehicle_wallbox() == charger.vehicle_wallbox()


class SpyCharger:
    """Merkt sich jeden Aufruf, den der Kern an die Laderegelung weitergibt."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def note_verdict(self, command: Command, verdict: Verdict | None, *, not_sent: bool) -> None:
        self.calls.append(("note_verdict", command, verdict, not_sent))

    def note_result(self, result: WriteResult) -> None:
        self.calls.append(("note_result", result))

    def forget(self, actuators: object) -> None:
        self.calls.append(("forget", actuators))

    def idle(self, now: datetime) -> None:
        self.calls.append(("idle", now))


def test_regulation_hands_every_call_to_the_charging(cfg) -> None:
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    spy = SpyCharger()
    regulation.charger = spy  # type: ignore[assignment]
    command = Command("wallbox.evcs", CommandKind.CURRENT, 10.0)
    verdict = Verdict(command, Outcome.ACCEPTED, 10.0, (), True)
    result = WriteResult("wallbox.evcs", CommandKind.CURRENT, 10.0, DecisionResult.TIMEOUT, "")
    regulation.note_verdict(command, verdict, not_sent=True)
    regulation.note_verdict(command, None)
    regulation.note_result(result)
    regulation.forget({"wallbox.evcs"})
    regulation.idle(T0)
    assert spy.calls == [
        ("note_verdict", command, verdict, True),
        ("note_verdict", command, None, False),
        ("note_result", result),
        ("forget", {"wallbox.evcs"}),
        ("idle", T0),
    ]


def test_regulation_has_no_alarms_and_no_budget_yet(cfg) -> None:
    regulation = Regulation(cfg, HardLimits.from_config(cfg), started=T0)
    assert regulation.alarms(T0) == []
    assert regulation.grid_budget_w() is None
