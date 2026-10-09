"""Vertrag zwischen app und core: der Client der app gegen die echte interne API des core."""

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import uvicorn
from pydantic import SecretStr

from bindaems.app.core_link import CoreClient, CoreUnavailableError
from bindaems.core.api.app import HealthReport, create_api

TOKEN = "x" * 32


class FakeView:
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


@pytest.fixture
async def core_api() -> AsyncIterator[RunningApi]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(
        create_api(FakeView(), SecretStr(TOKEN)),
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
        yield RunningApi(f"http://127.0.0.1:{port}")
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
