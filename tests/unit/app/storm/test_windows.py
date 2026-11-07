from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from bindaems.app.storm.windows import relevant, storm_floors, warning_at
from bindaems.shared.plan import StormKind, StormLevel, StormWarning
from bindaems.shared.settings import StormSettings

VIENNA = ZoneInfo("Europe/Vienna")


def _w(
    begin: datetime,
    end: datetime,
    *,
    kind: StormKind = "gewitter",
    level: StormLevel = 2,
    warn_id: int = 1,
) -> StormWarning:
    return StormWarning(
        warn_id=warn_id, version=1, kind=kind, level=level, begin=begin, end=end, text="t"
    )


def _slots(first: datetime, n: int) -> list[datetime]:
    return [first.astimezone(UTC) + timedelta(minutes=15 * i) for i in range(n)]


def _on(**kw: object) -> StormSettings:
    return StormSettings(enabled=True, **kw)  # type: ignore[arg-type]


def _floors(
    slots: list[datetime],
    warnings: list[StormWarning],
    settings: StormSettings | None = None,
    *,
    reserve: float = 10.0,
    soc_max: float = 100.0,
) -> list[float | None]:
    return storm_floors(
        slots, warnings, settings or _on(), reserve_pct=reserve, soc_max_pct=soc_max
    )


def test_floor_covers_lead_and_warning() -> None:
    slots = _slots(datetime(2026, 7, 1, 12, 0, tzinfo=VIENNA), 20)  # 12:00 bis 16:45
    w = _w(datetime(2026, 7, 1, 14, 0, tzinfo=VIENNA), datetime(2026, 7, 1, 16, 0, tzinfo=VIENNA))
    floors = _floors(slots, [w])
    assert floors[:4] == [None] * 4  # bis 12:45
    assert floors[4:16] == [40.0] * 12  # 13:00 bis 15:45
    assert floors[16:] == [None] * 4  # ab 16:00


def test_floor_window_across_dst_end() -> None:
    begin = datetime(2026, 10, 25, 1, 30, tzinfo=VIENNA)
    end = datetime(2026, 10, 25, 3, 30, tzinfo=VIENNA)
    w = _w(begin, end)
    slots = _slots(datetime(2026, 10, 24, 21, 0, tzinfo=UTC), 24)
    floors = _floors(slots, [w])
    got = [s for s, f in zip(slots, floors, strict=True) if f is not None]
    start = begin.astimezone(UTC) - timedelta(hours=1)
    expected = [s for s in slots if s + timedelta(minutes=15) > start and s < end.astimezone(UTC)]
    assert got == expected
    assert len(got) == 16
    assert got[0] == datetime(2026, 10, 24, 22, 30, tzinfo=UTC)
    assert got[-1] == datetime(2026, 10, 25, 2, 15, tzinfo=UTC)


def test_unaligned_begin_includes_slot() -> None:
    slots = _slots(datetime(2026, 7, 1, 12, 0, tzinfo=VIENNA), 8)
    w = _w(datetime(2026, 7, 1, 14, 10, tzinfo=VIENNA), datetime(2026, 7, 1, 15, 0, tzinfo=VIENNA))
    floors = _floors(slots, [w])
    first = next(i for i, f in enumerate(floors) if f is not None)
    assert slots[first] == datetime(2026, 7, 1, 13, 0, tzinfo=VIENNA).astimezone(UTC)


def test_filters_kind_and_level() -> None:
    slots = _slots(datetime(2026, 7, 1, 12, 0, tzinfo=VIENNA), 20)
    b = datetime(2026, 7, 1, 14, 0, tzinfo=VIENNA)
    e = datetime(2026, 7, 1, 16, 0, tzinfo=VIENNA)
    s = _on(min_level=2, kinds=["sturm", "gewitter"])
    no = [_w(b, e, kind="regen", level=2), _w(b, e, kind="gewitter", level=1, warn_id=2)]
    assert _floors(slots, no, s) == [None] * 20
    assert any(f is not None for f in _floors(slots, [_w(b, e, kind="gewitter", level=2)], s))


def test_switch_off_means_no_floor() -> None:
    slots = _slots(datetime(2026, 7, 1, 12, 0, tzinfo=VIENNA), 20)
    w = _w(datetime(2026, 7, 1, 14, 0, tzinfo=VIENNA), datetime(2026, 7, 1, 16, 0, tzinfo=VIENNA))
    off = StormSettings(enabled=False)
    assert _floors(slots, [w], off) == [None] * 20
    assert relevant([w], off) == []


def test_target_clamped_to_soc_max() -> None:
    slots = _slots(datetime(2026, 7, 1, 13, 0, tzinfo=VIENNA), 2)
    w = _w(datetime(2026, 7, 1, 14, 0, tzinfo=VIENNA), datetime(2026, 7, 1, 16, 0, tzinfo=VIENNA))
    assert _floors(slots, [w], _on(target_soc_pct=100), soc_max=95.0) == [95.0, 95.0]


def test_target_at_reserve_has_no_effect() -> None:
    slots = _slots(datetime(2026, 7, 1, 13, 0, tzinfo=VIENNA), 4)
    w = _w(datetime(2026, 7, 1, 14, 0, tzinfo=VIENNA), datetime(2026, 7, 1, 16, 0, tzinfo=VIENNA))
    assert _floors(slots, [w], _on(target_soc_pct=20), reserve=20.0) == [None] * 4


def test_overlapping_warnings_union() -> None:
    slots = _slots(datetime(2026, 7, 1, 12, 0, tzinfo=VIENNA), 24)  # bis 17:45
    a = _w(datetime(2026, 7, 1, 14, 0, tzinfo=VIENNA), datetime(2026, 7, 1, 16, 0, tzinfo=VIENNA))
    b = _w(
        datetime(2026, 7, 1, 15, 0, tzinfo=VIENNA),
        datetime(2026, 7, 1, 18, 0, tzinfo=VIENNA),
        warn_id=2,
    )
    floors = _floors(slots, [a, b])
    assert floors[:4] == [None] * 4
    assert floors[4:] == [40.0] * 20  # 13:00 bis 17:45


def test_relevant_sorted_by_begin() -> None:
    a = _w(datetime(2026, 7, 1, 15, 0, tzinfo=UTC), datetime(2026, 7, 1, 16, 0, tzinfo=UTC))
    b = _w(
        datetime(2026, 7, 1, 10, 0, tzinfo=UTC), datetime(2026, 7, 1, 11, 0, tzinfo=UTC), warn_id=2
    )
    assert relevant([a, b], _on()) == [b, a]


def test_warning_at_latest_end() -> None:
    a = _w(datetime(2026, 7, 1, 14, 0, tzinfo=UTC), datetime(2026, 7, 1, 16, 0, tzinfo=UTC))
    b = _w(
        datetime(2026, 7, 1, 15, 0, tzinfo=UTC), datetime(2026, 7, 1, 18, 0, tzinfo=UTC), warn_id=2
    )
    assert warning_at(datetime(2026, 7, 1, 15, 30, tzinfo=UTC), [a, b]) == b
    assert warning_at(datetime(2026, 7, 1, 13, 15, tzinfo=UTC), [a, b]) == a  # im Vorlauf
    assert warning_at(datetime(2026, 7, 1, 18, 0, tzinfo=UTC), [a, b]) is None
