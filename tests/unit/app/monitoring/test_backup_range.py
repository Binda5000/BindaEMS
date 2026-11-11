"""Reichweite des Akkus bei Netzausfall (Plan „Lebenszeichen und USV-Reichweite“, Aufgabe 2)."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from bindaems.app.history.influx import InfluxQueryError, Series
from bindaems.app.monitoring.backup_range import (
    BackupRangeService,
    LoadBasis,
    ac_out_query,
    backup_range,
    hours_text,
    load_basis,
    slot_loads,
)
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.timeutil import LOCAL_TZ, ManualClock, local_day_slots

NBSP = chr(0xA0)  # geschütztes Leerzeichen vor der Einheit
TODAY = date(2026, 11, 4)


def _series(phase: str, rows: list[tuple[datetime, float | None]]) -> Series:
    return Series(
        name="power",
        tags={"phase": phase},
        columns=["time", "mean"],
        values=[[epoch_ms(t), v] for t, v in rows],
    )


def _loads(days: int = 7, day_w: float = 800.0, night_w: float = 400.0) -> dict[datetime, float]:
    loads: dict[datetime, float] = {}
    for offset in range(1, days + 1):
        for index, slot in enumerate(local_day_slots(TODAY - timedelta(days=offset))):
            loads[slot] = night_w if index < 20 else day_w
    return loads


def _basis(day_w: float | None = 700.0, night_w: float | None = 400.0) -> LoadBasis:
    return LoadBasis(day_w, night_w, 672, 140, date(2026, 10, 28), date(2026, 11, 3))


def _range(**kw: Any) -> Any:
    args: dict[str, Any] = {
        "soc_pct": 60.0,
        "reserve_pct": 20.0,
        "floor_pct": 5.0,
        "usable_kwh": 96.0,
        "roundtrip_pct": 88.0,
        "basis": _basis(),
        "outage_load_w": None,
    }
    args.update(kw)
    return backup_range(**args)


def test_query_targets_ac_out() -> None:
    query = ac_out_query(datetime(2026, 10, 28, tzinfo=UTC), datetime(2026, 11, 4, tzinfo=UTC))
    assert "'consumption_output'" in query
    assert '"long"."power"' in query
    assert "GROUP BY time(15m)" in query
    assert "house_load" not in query


def test_slot_needs_three_phases() -> None:
    t = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    two = [_series("L1", [(t, 200.0)]), _series("L2", [(t, 200.0)])]
    assert slot_loads(two) == {}
    three = [*two, _series("L3", [(t, 100.0)])]
    assert slot_loads(three) == {t: 500.0}


def test_day_and_night_mean() -> None:
    basis = load_basis(_loads(), TODAY)
    assert basis.night_w == 400
    assert basis.day_w == pytest.approx((20 * 400 + 76 * 800) / 96)
    assert (basis.first_day, basis.last_day) == (date(2026, 10, 28), date(2026, 11, 3))
    assert basis.night_slots == 140
    assert basis.day_slots == 672


def test_too_few_slots_gives_none() -> None:
    loads = dict(list(_loads(days=1).items())[:191])
    basis = load_basis(loads, TODAY)
    assert basis.day_w is None
    assert basis.night_w is None  # nur 20 Nachtslots


def test_today_is_excluded() -> None:
    base = _loads()
    with_today = {**base, **{s: 5000.0 for s in local_day_slots(TODAY)}}
    assert load_basis(with_today, TODAY) == load_basis(base, TODAY)


def test_formula() -> None:
    result = _range()
    eta = math.sqrt(0.88)
    assert result.now_h == pytest.approx(0.55 * 96 * eta * 1000 / 700)
    assert result.reserve_h == pytest.approx(0.15 * 96 * eta * 1000 / 700)
    assert result.load_w == 700
    assert result.load_source == "tag"


def test_soc_below_floor_is_zero() -> None:
    assert _range(soc_pct=3.0).now_h == 0.0


def test_soc_unknown() -> None:
    result = _range(soc_pct=None)
    assert result.now_h is None
    assert result.reserve_h is not None
    assert result.night_h is None


def test_implausible_load() -> None:
    result = _range(basis=_basis(day_w=30.0))
    assert result.now_h is None
    assert result.reserve_h is None


def test_no_basis() -> None:
    result = _range(basis=None)
    assert (result.now_h, result.reserve_h, result.night_h, result.load_w) == (
        None,
        None,
        None,
        None,
    )


def test_outage_load_wins() -> None:
    result = _range(outage_load_w=1200.0)
    assert result.load_w == 1200
    assert result.load_source == "ausfall"
    assert result.now_h == pytest.approx(0.55 * 96 * math.sqrt(0.88) * 1000 / 1200)
    assert _range(basis=None, outage_load_w=1200.0).now_h is not None


def test_night_hours() -> None:
    result = _range()
    assert result.night_h == pytest.approx(0.55 * 96 * math.sqrt(0.88) * 1000 / 400)
    assert _range(basis=_basis(night_w=None)).night_h is None


def test_hours_text() -> None:
    assert hours_text(7.46) == f"≈ 7,5{NBSP}h"
    assert hours_text(31.4) == f"≈ 31{NBSP}h"
    assert hours_text(0) == f"≈ 0{NBSP}h"
    assert hours_text(9.99) == f"≈ 10{NBSP}h"
    assert hours_text(None) == "zu wenig Daten"


def test_hours_text_boundaries_round_half_up() -> None:
    # gleiche Erwartungen wie hoursText in ui/src/lib/system/backup.test.ts
    assert hours_text(0.04) == f"≈ 0{NBSP}h"
    assert hours_text(9.96) == f"≈ 10{NBSP}h"
    assert hours_text(10.5) == f"≈ 11{NBSP}h"
    assert hours_text(12.5) == f"≈ 13{NBSP}h"


class FakeReader:
    def __init__(self) -> None:
        self.results: list[list[Series] | Exception] = []
        self.queries: list[str] = []

    async def query(self, q: str, *, database: str | None = None) -> list[Series]:
        self.queries.append(q)
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _phase_series(loads: dict[datetime, float]) -> list[Series]:
    rows = sorted(loads.items())
    return [_series(p, [(t, w / 3) for t, w in rows]) for p in ("L1", "L2", "L3")]


async def test_influx_error_keeps_last_basis() -> None:
    clock = ManualClock(datetime(2026, 11, 4, 8, 0, tzinfo=UTC))
    reader = FakeReader()
    reader.results = [_phase_series(_loads()), InfluxQueryError("weg")]
    service = BackupRangeService(reader, clock)  # type: ignore[arg-type]
    assert service.basis() is None
    await service.refresh()
    first = service.basis()
    assert first is not None
    assert first.night_w == pytest.approx(400)
    clock.advance(timedelta(hours=24))
    await service.refresh()
    assert service.basis() == first
    clock.advance(timedelta(hours=25))
    assert service.basis() is None


async def test_refresh_queries_seven_local_days() -> None:
    clock = ManualClock(datetime(2026, 11, 4, 8, 0, tzinfo=UTC))
    reader = FakeReader()
    reader.results = [[]]
    service = BackupRangeService(reader, clock)  # type: ignore[arg-type]
    await service.refresh()
    start = datetime(2026, 10, 28, tzinfo=LOCAL_TZ)
    end = datetime(2026, 11, 4, tzinfo=LOCAL_TZ)
    assert f"time >= {epoch_ms(start)}ms" in reader.queries[0]
    assert f"time < {epoch_ms(end)}ms" in reader.queries[0]
