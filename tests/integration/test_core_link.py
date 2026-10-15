"""Vertrag zwischen app und core: der Client der app gegen die echte interne API des core."""

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import pytest
import uvicorn
from pydantic import SecretStr
from tests.helpers import T0
from tests.unit.shared.test_decisions import decision

from bindaems.app.core_link import CoreClient, CoreReply, CoreUnavailableError
from bindaems.core.api.app import AckReply, HealthReport, create_api
from bindaems.core.control.intent import IntentReply
from bindaems.shared.control import Intent
from bindaems.shared.decisions import Decision
from bindaems.shared.timeutil import LOCAL_TZ

TOKEN = "x" * 32


class FakeView:
    def __init__(self) -> None:
        self.current: Intent | None = None
        self.intent_reply = IntentReply("accepted", 5)
        self.submitted: list[Intent] = []
        self.decision_list: list[Decision] = []
        self.decision_more = False
        self.decision_calls: list[tuple[datetime, int]] = []
        self.ack_reply = AckReply("accepted", "Quittiert.")
        self.acked: list[str] = []

    def intent(self) -> Intent | None:
        return self.current

    def submit_intent(self, intent: Intent) -> IntentReply:
        self.submitted.append(intent)
        if self.intent_reply.result != "rejected":
            self.current = intent
        return self.intent_reply

    def decisions(self, since: datetime, limit: int) -> tuple[list[Decision], bool]:
        self.decision_calls.append((since, limit))
        return self.decision_list, self.decision_more

    def acknowledge(self, actuator: str) -> AckReply:
        self.acked.append(actuator)
        return self.ack_reply

    def health(self) -> HealthReport:
        return HealthReport(
            mode="OBSERVE", version="0.1.0", adapters=[], selfcheck=[], alarms=[], cycle_ms_p95=None
        )

    def state(self) -> dict[str, Any]:
        return {"ts": "2026-10-09T08:00:00+00:00", "signals": {}, "derived": {}}

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        async def messages() -> AsyncIterator[dict[str, Any]]:
            yield {"type": "state", "data": self.state()}

        yield messages()


@dataclass
class RunningApi:
    url: str
    view: FakeView


@pytest.fixture
async def core_api() -> AsyncIterator[RunningApi]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    view = FakeView()
    config = uvicorn.Config(
        create_api(view, SecretStr(TOKEN)),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="off",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(5):
            while not server.started:  # noqa: ASYNC110 - uvicorn bietet dafür kein Event
                await asyncio.sleep(0.01)
        yield RunningApi(f"http://127.0.0.1:{port}", view)
    finally:
        server.should_exit = True
        await task


async def test_client_reads_health_and_stream_from_real_core_api(core_api) -> None:
    async with httpx.AsyncClient(trust_env=False) as http:
        client = CoreClient(core_api.url, SecretStr(TOKEN), http)
        assert (await client.health())["mode"] == "OBSERVE"
        async with client.stream() as messages:
            assert (await anext(messages))["type"] == "state"


async def test_wrong_token_is_rejected_by_real_core_api(core_api) -> None:
    async with httpx.AsyncClient(trust_env=False) as http:
        client = CoreClient(core_api.url, SecretStr("y" * 32), http)
        with pytest.raises(CoreUnavailableError):
            await client.health()
        with pytest.raises(CoreUnavailableError):
            async with client.stream() as messages:
                await anext(messages)


def make_client(http: httpx.AsyncClient, api: RunningApi, token: str = TOKEN) -> CoreClient:
    return CoreClient(api.url, SecretStr(token), http)


async def test_client_submits_and_reads_the_intent(core_api) -> None:
    async with httpx.AsyncClient(trust_env=False) as http:
        client = make_client(http, core_api)
        assert await client.intent() is None
        reply = await client.submit_intent(Intent(version=5, control=True))
        assert (reply.status, reply.body["result"]) == (200, "accepted")
        assert (await client.intent()) == Intent(version=5, control=True)


async def test_client_returns_rejections_as_replies(core_api) -> None:
    core_api.view.intent_reply = IntentReply("rejected", 7, ("Version veraltet.",), "stale")
    async with httpx.AsyncClient(trust_env=False) as http:
        reply = await make_client(http, core_api).submit_intent(Intent(version=1))
    assert reply == CoreReply(
        409, {"result": "rejected", "version": 7, "details": ["Version veraltet."]}
    )
    core_api.view.intent_reply = IntentReply("rejected", 7, ("Unbekannter Aktor.",), "invalid")
    async with httpx.AsyncClient(trust_env=False) as http:
        reply = await make_client(http, core_api).submit_intent(Intent(version=9))
    assert reply.status == 422 and reply.body["details"] == ["Unbekannter Aktor."]


async def test_client_reads_decisions_with_timezone(core_api) -> None:
    core_api.view.decision_list = [decision()]
    async with httpx.AsyncClient(trust_env=False) as http:
        decisions, more = await make_client(http, core_api).decisions(T0.astimezone(LOCAL_TZ), 10)
    assert core_api.view.decision_calls == [(T0, 10)] and more is False
    assert decisions == [decision()]


async def test_client_reads_foreign_decisions_and_more(core_api) -> None:
    from bindaems.shared.decisions import DecisionResult

    foreign = decision(result=DecisionResult.FOREIGN, outcome="none", codes=("foreign_change",))
    core_api.view.decision_list = [foreign]
    core_api.view.decision_more = True
    async with httpx.AsyncClient(trust_env=False) as http:
        decisions, more = await make_client(http, core_api).decisions(T0, 1)
    assert decisions == [foreign] and more is True


async def test_client_acknowledges_with_status_and_detail(core_api) -> None:
    cases = [
        (AckReply("accepted", "Quittiert."), 202),
        (AckReply("not_locked", "nicht gesperrt."), 409),
        (AckReply("cause_present", "Ursache besteht weiter: x"), 409),
        (AckReply("unknown", "Unbekannter Aktor."), 404),
    ]
    async with httpx.AsyncClient(trust_env=False) as http:
        client = make_client(http, core_api)
        for ack, code in cases:
            core_api.view.ack_reply = ack
            assert await client.acknowledge("wallbox.evcs") == CoreReply(
                code, {"detail": ack.detail}
            )
    assert core_api.view.acked == ["wallbox.evcs"] * 4


async def test_new_calls_reject_a_wrong_token_as_unavailable(core_api) -> None:
    async with httpx.AsyncClient(trust_env=False) as http:
        client = make_client(http, core_api, "y" * 32)
        with pytest.raises(CoreUnavailableError):
            await client.intent()
        with pytest.raises(CoreUnavailableError):
            await client.submit_intent(Intent(version=1))
        with pytest.raises(CoreUnavailableError):
            await client.decisions(T0, 1)
        with pytest.raises(CoreUnavailableError):
            await client.acknowledge("wallbox.evcs")
