from collections.abc import Callable
from typing import Any

import pytest
from tests.helpers import FakeInflux, FakeSink

from bindaems.app.charging.planned import PlannedCharges
from bindaems.app.control.decisions import DecisionStore
from bindaems.app.ha.notify import HaNotifier
from bindaems.app.ledger.service import LedgerService
from bindaems.app.ops.check import OpsCheck
from bindaems.app.ops.report import DaySummary
from bindaems.app.ops.service import FindingsService, ReportSender
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
    engine, clock, cfg, settings_service, ledger, plan_store, decision_store, influx
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
            settings_service,
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


class HaCalls:
    """Ersatz für den Dienstaufruf an Home Assistant: zeichnet auf, kann scheitern."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.failing = False

    async def __call__(self, domain: str, service: str, data: dict[str, Any]) -> None:
        if self.failing:
            raise ConnectionError("Home Assistant nicht erreichbar")
        self.calls.append((domain, service, data))

    @property
    def reports(self) -> list[str]:
        return [
            data["message"]
            for domain, service, data in self.calls
            if (domain, service) == ("persistent_notification", "create")
            and data["notification_id"] == "bindaems_tagesbericht"
        ]


@pytest.fixture
def ha_calls() -> HaCalls:
    return HaCalls()


@pytest.fixture
def make_sender(engine, clock, settings_service, findings, ha_calls) -> Callable[..., ReportSender]:
    def build(**changes: Any) -> ReportSender:
        values: dict[str, Any] = {
            "notifier": HaNotifier(ha_calls, None, clock),
            "summary": lambda day: DaySummary(14.2, 9.8, 4.4, 0.0, 3.12, 1.85, 9.1),
            "check": lambda: OpsCheck(level="ok", items=(), checked_at=clock.now()),
        }
        values.update(changes)
        return ReportSender(
            engine,
            clock,
            settings_service,
            findings,
            values["summary"],
            values["check"],
            values["notifier"],
        )

    return build
