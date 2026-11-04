from datetime import UTC, datetime, timedelta

import httpx
import pytest
from tests.app_helpers import StopScheduler, record_then_stop
from tests.plan_helpers import set_planning

from bindaems.app.storm.geosphere import WarningsUnavailable
from bindaems.app.storm.service import POLL, STALE_AFTER, StormService
from bindaems.shared.plan import StormWarning
from bindaems.shared.settings import PlanningSettings, StormSettings

# T_APP ist 10:00 Ortszeit (08:00 UTC); LOCAL_MIDNIGHT ist 0:00 Ortszeit desselben Tages
LOCAL_MIDNIGHT = datetime(2026, 10, 8, 22, 0, tzinfo=UTC)


def _w(
    begin_h: int,
    end_h: int,
    *,
    warn_id: int = 1,
    version: int = 1,
    kind: str = "gewitter",
    level: int = 2,
) -> StormWarning:
    return StormWarning(
        warn_id=warn_id,
        version=version,
        kind=kind,  # type: ignore[arg-type]
        level=level,  # type: ignore[arg-type]
        begin=LOCAL_MIDNIGHT + timedelta(hours=begin_h),
        end=LOCAL_MIDNIGHT + timedelta(hours=end_h),
        text="Gewitter",
    )


class FakeFetch:
    def __init__(self, *results: list[StormWarning] | Exception) -> None:
        self.results = list(results)
        self.calls = 0

    async def __call__(self, http, clock, location) -> list[StormWarning]:
        self.calls += 1
        result = self.results[min(self.calls, len(self.results)) - 1]
        if isinstance(result, Exception):
            raise result
        return result


def _enable(settings_service, enabled: bool = True) -> None:
    set_planning(settings_service, PlanningSettings(storm=StormSettings(enabled=enabled)))


def _service(cfg, settings_service, clock, fetch, **kw) -> StormService:
    return StormService(cfg, settings_service, httpx.AsyncClient(), clock, fetch, **kw)


async def test_switch_off_fetches_nothing(cfg, settings_service, clock) -> None:
    fetch = FakeFetch([_w(14, 16)])
    service = _service(cfg, settings_service, clock, fetch)
    await service.refresh()
    status = service.component_status()
    assert fetch.calls == 0 and service.warnings() == []
    assert (status.name, status.ok, status.message) == ("storm", True, "aus")


async def test_known_warning_survives_failed_fetch(cfg, settings_service, clock) -> None:
    _enable(settings_service)
    fetch = FakeFetch([_w(14, 16)], WarningsUnavailable("down"))
    service = _service(cfg, settings_service, clock, fetch)
    await service.refresh()
    clock.advance(timedelta(minutes=15))
    await service.refresh()
    assert len(service.warnings()) == 1 and service.component_status().ok
    clock.advance(STALE_AFTER)  # 61 min nach dem Erfolg bei 10:00 sind es 76 min
    await service.refresh()
    status = service.component_status()
    assert not status.ok and status.message == "Warnungen seit 10:00 nicht abrufbar"
    assert len(service.warnings()) == 1


async def test_warning_expires_at_end(cfg, settings_service, clock) -> None:
    _enable(settings_service)
    service = _service(cfg, settings_service, clock, FakeFetch([_w(14, 16)]))
    await service.refresh()
    before = service.fingerprint()
    clock.advance(timedelta(hours=6))  # 16:00
    assert service.warnings() == [] and service.fingerprint() != before


async def test_withdrawn_warning_is_dropped(cfg, settings_service, clock) -> None:
    _enable(settings_service)
    service = _service(cfg, settings_service, clock, FakeFetch([_w(14, 16)], []))
    await service.refresh()
    assert len(service.warnings()) == 1
    await service.refresh()
    assert service.warnings() == [] and service.fingerprint() == ()


async def test_new_version_replaces_old(cfg, settings_service, clock) -> None:
    _enable(settings_service)
    fetch = FakeFetch([_w(14, 16, version=2)], [_w(14, 15, version=3)])
    service = _service(cfg, settings_service, clock, fetch)
    await service.refresh()
    await service.refresh()
    (only,) = service.warnings()
    assert only.version == 3 and only.end == _w(14, 15).end


async def test_warnings_follow_current_settings(cfg, settings_service, clock) -> None:
    _enable(settings_service)
    service = _service(cfg, settings_service, clock, FakeFetch([_w(14, 16, level=1)]))
    await service.refresh()
    assert service.warnings() == []  # gelb liegt unter Orange
    _enable(settings_service, enabled=False)
    assert service.warnings() == []


async def test_status_texts(cfg, settings_service, clock) -> None:
    _enable(settings_service)
    service = _service(cfg, settings_service, clock, FakeFetch([], [_w(14, 16)]))
    await service.refresh()
    status = service.component_status()
    assert status.ok and status.message == "keine Warnung"
    await service.refresh()
    status = service.component_status()
    assert status.ok and status.message == "Gewitter (orange) bis 16:00"


async def test_run_refreshes_then_every_poll(cfg, settings_service, clock) -> None:
    _enable(settings_service)
    fetch = FakeFetch([])
    sleeps: list[float] = []
    service = _service(cfg, settings_service, clock, fetch, sleep=record_then_stop(sleeps, 2))
    with pytest.raises(StopScheduler):
        await service.run()
    assert fetch.calls == 2
    assert sleeps == [POLL.total_seconds()] * 2


async def test_run_survives_unexpected_error(cfg, settings_service, clock) -> None:
    _enable(settings_service)
    sleeps: list[float] = []
    service = _service(
        cfg,
        settings_service,
        clock,
        FakeFetch(RuntimeError("x")),
        sleep=record_then_stop(sleeps, 1),
    )
    with pytest.raises(StopScheduler):
        await service.run()
    assert len(sleeps) == 1
