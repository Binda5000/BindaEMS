from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from bindaems.shared.config import Config
from bindaems.shared.influx.spool import DiskSpool
from bindaems.shared.influx.writer import InfluxWriter
from bindaems.shared.timeutil import ManualClock

TS = datetime(2026, 10, 8, 10, 0, 0, 123000, tzinfo=UTC)  # = 1791453600123 ms


@pytest.fixture
async def writer_env(
    cfg: Config, tmp_path: Path
) -> AsyncIterator[Callable[..., tuple[InfluxWriter, DiskSpool]]]:
    clients: list[httpx.AsyncClient] = []

    def build(spool_max_bytes: int = 10_000_000, **kwargs: Any) -> tuple[InfluxWriter, DiskSpool]:
        client = httpx.AsyncClient()
        clients.append(client)
        spool = DiskSpool(tmp_path / "spool", spool_max_bytes, timedelta(days=7), ManualClock(TS))
        writer = InfluxWriter(cfg.influxdb, SecretStr("geheim"), client, spool, **kwargs)
        return writer, spool

    yield build
    for client in clients:
        await client.aclose()
