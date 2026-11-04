from collections.abc import Callable
from typing import Any

import pytest
from tests.helpers import FakeInflux, FakeSink

from bindaems.app.charging.planned import PlannedCharges
from bindaems.app.control.decisions import DecisionStore
from bindaems.app.ledger.service import LedgerService
from bindaems.app.ops.service import FindingsService
from bindaems.app.planning.store import PlanStore
from bindaems.app.prices.service import PriceService
from bindaems.app.prices.store import PriceStore


@pytest.fixture
def ledger(engine, clock, settings_service, audit) -> LedgerService:
    prices = PriceService(PriceStore(engine, clock), settings_service)
    return LedgerService(engine, clock, prices, settings_service, audit, FakeSink())


@pytest.fixture
def plan_store(engine) -> PlanStore:
    return PlanStore(engine)


@pytest.fixture
def decision_store(engine, clock) -> DecisionStore:
    return DecisionStore(engine, clock)


@pytest.fixture
def influx() -> FakeInflux:
    return FakeInflux()


@pytest.fixture
def make_findings(
    engine, clock, cfg, ledger, plan_store, decision_store, influx
) -> Callable[..., FindingsService]:
    def build(**changes: Any) -> FindingsService:
        values: dict[str, Any] = {
            "reader": influx,
            "labels": lambda: {"victron": "Victron"},
        }
        values.update(changes)
        sleep = values.pop("sleep", None)
        extra = {"sleep": sleep} if sleep is not None else {}
        return FindingsService(
            engine,
            clock,
            cfg,
            ledger,
            plan_store,
            decision_store,
            PlannedCharges(engine, clock, cfg),
            values["reader"],
            values["labels"],
            **extra,
        )

    return build


@pytest.fixture
def findings(make_findings) -> FindingsService:
    return make_findings()
