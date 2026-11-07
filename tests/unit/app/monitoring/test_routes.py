"""``GET /api/backup`` (Plan „Lebenszeichen und USV-Reichweite“, Aufgabe 5)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from tests.app_helpers import login_as

from bindaems.app.monitoring.backup_range import BackupRange, LoadBasis
from bindaems.app.monitoring.heartbeat import PingInfo
from bindaems.app.monitoring.outages import Outage
from bindaems.app.monitoring.routes import backup_router
from bindaems.shared.timeutil import ManualClock

START = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
URL = "https://hc.example/geheim-1234"


class Fakes:
    def __init__(self) -> None:
        self.range = BackupRange(31.2, 14.1, 712.0, "tag", 40.3)
        self.basis: LoadBasis | None = LoadBasis(
            712.0, 551.0, 672, 140, date(2026, 10, 28), date(2026, 11, 3)
        )
        self.open: Outage | None = None
        self.history: list[Outage] = []
        self.ping = PingInfo(True, True, START, "in Ordnung · CONTROL")

    def backup_range(self) -> BackupRange:
        return self.range

    def load_basis(self) -> LoadBasis | None:
        return self.basis

    def current(self) -> Outage | None:
        return self.open

    def recent(self, limit: int = 20) -> list[Outage]:
        return self.history

    def ping_info(self) -> PingInfo:
        return self.ping


@pytest.fixture
def fakes() -> Fakes:
    return Fakes()


@pytest.fixture
def client(make_client, fakes: Fakes, clock: ManualClock, guard) -> TestClient:
    return make_client(
        backup_router(
            range_now=fakes.backup_range,
            basis=fakes.load_basis,
            outages=fakes,  # type: ignore[arg-type]
            heartbeat=fakes,  # type: ignore[arg-type]
            floor_pct=5.0,
            clock=clock,
            guard=guard,
        )
    )


def _outage(**over: object) -> Outage:
    args: dict[str, object] = {
        "id": 1,
        "start": START,
        "end": START + timedelta(seconds=840),
        "end_estimated": False,
        "soc_start": 62.0,
        "soc_end": 60.5,
        "soc_min": 60.5,
        "load_wh": 280.0,
        "covered_s": 900.0,
    }
    args.update(over)
    return Outage(**args)  # type: ignore[arg-type]


def test_backup_requires_login(client: TestClient) -> None:
    assert client.get("/api/backup").status_code == 401


def test_backup_payload(client: TestClient, auth, fakes: Fakes, clock: ManualClock) -> None:
    clock.advance(START - clock.now() + timedelta(seconds=1000))
    fakes.open = _outage(end=None, load_wh=1180.0 * 1000 / 3600, covered_s=1000.0)
    fakes.history = [fakes.open, _outage(id=0)]
    login_as(client, auth, "viewer")
    body = client.get("/api/backup").json()
    assert set(body) == {
        "now_h", "reserve_h", "night_h", "load_w", "load_source", "floor_soc_pct",
        "basis", "outage", "outages", "ping",
    }  # fmt: skip
    assert (body["now_h"], body["reserve_h"], body["night_h"]) == (31.2, 14.1, 40.3)
    assert (body["load_w"], body["load_source"], body["floor_soc_pct"]) == (712.0, "tag", 5.0)
    assert body["basis"] == {
        "first_day": "2026-10-28",
        "last_day": "2026-11-03",
        "day_slots": 672,
        "night_slots": 140,
        "night_w": 551.0,
    }
    assert body["outage"] == {
        "start": START.isoformat(),
        "soc_start": 62.0,
        "mean_w": pytest.approx(1180.0),
    }
    done = body["outages"][1]
    assert done == {
        "start": START.isoformat(),
        "end": (START + timedelta(seconds=840)).isoformat(),
        "end_estimated": False,
        "duration_s": 840,
        "soc_start": 62.0,
        "soc_end": 60.5,
        "soc_min": 60.5,
        "mean_w": pytest.approx(1120.0),
        "load_kwh": pytest.approx(0.28),
    }
    assert body["outages"][0]["end"] is None and body["outages"][0]["duration_s"] == 1000
    assert body["ping"] == {
        "configured": True,
        "ok": True,
        "last_sent": START.isoformat(),
        "text": "in Ordnung · CONTROL",
    }


def test_backup_without_load_assumption(client: TestClient, auth, fakes: Fakes) -> None:
    fakes.range = BackupRange(None, None, None, None, None)
    fakes.basis = None
    fakes.ping = PingInfo(False, True, None, None)
    login_as(client, auth, "viewer")
    body = client.get("/api/backup").json()
    assert body["now_h"] is None and body["reserve_h"] is None and body["load_w"] is None
    assert body["night_h"] is None and body["basis"] is None and body["outage"] is None
    assert body["outages"] == []
    assert body["ping"] == {"configured": False, "ok": True, "last_sent": None, "text": None}


def test_backup_has_no_url(client: TestClient, auth, fakes: Fakes) -> None:
    login_as(client, auth, "viewer")
    assert URL not in client.get("/api/backup").text
