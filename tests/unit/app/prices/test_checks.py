from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta

import pytest

from bindaems.app.prices.checks import (
    VatDetection,
    by_local_day,
    detect_vat,
    duplicate_starts,
    is_hourly,
    range_findings,
    structure_findings,
    to_slots,
)
from bindaems.app.prices.sources import PriceInterval
from bindaems.shared.timeutil import local_day_slots


def iv(start: str, minutes: int, ct: float) -> PriceInterval:
    begin = datetime.fromisoformat(start).replace(tzinfo=UTC)
    return PriceInterval(begin, begin + timedelta(minutes=minutes), ct)


def day_intervals(day: date, value: Callable[[int], float]) -> list[PriceInterval]:
    return [
        PriceInterval(slot, slot + timedelta(minutes=15), value(index))
        for index, slot in enumerate(local_day_slots(day))
    ]


def hourly_means(slots: Mapping[datetime, float]) -> dict[datetime, float]:
    """Je vollständige Stunde das Mittel der vier Slots auf alle vier Slots."""
    hours: dict[datetime, list[datetime]] = {}
    for slot in slots:
        hours.setdefault(slot.replace(minute=0), []).append(slot)
    result: dict[datetime, float] = {}
    for members in hours.values():
        if len(members) == 4:
            mean = sum(slots[m] for m in members) / 4
            result.update(dict.fromkeys(members, mean))
    return result


def test_to_slots_hour_interval_fills_four_slots() -> None:
    slots = to_slots([iv("2026-10-09T08:00", 60, 12.0)])
    assert list(slots.values()) == [12.0] * 4 and min(slots) == datetime(2026, 10, 9, 8, tzinfo=UTC)


def test_to_slots_time_weighted_mean_of_short_intervals() -> None:
    slots = to_slots([iv("2026-10-09T08:00", 5, 10.0), iv("2026-10-09T08:05", 10, 16.0)])
    assert slots == {datetime(2026, 10, 9, 8, tzinfo=UTC): pytest.approx(14.0)}


def test_to_slots_drops_partially_covered_slot() -> None:
    assert to_slots([iv("2026-10-09T08:00", 10, 10.0)]) == {}
    # zwei Teilstücke, die sich überlappen, decken den Slot trotzdem nicht ganz ab
    assert to_slots([iv("2026-10-09T08:00", 10, 10.0), iv("2026-10-09T08:00", 5, 10.0)]) == {}


def test_to_slots_keeps_exact_values() -> None:
    slots = to_slots([iv("2026-10-09T08:00", 60, 17.824), iv("2026-10-09T08:00", 15, 17.824)])
    assert slots[datetime(2026, 10, 9, 8, tzinfo=UTC)] == 17.824


def test_structure_ok_for_recorded_day(smartenergy_intervals) -> None:
    assert structure_findings(date(2026, 10, 9), smartenergy_intervals) == []
    assert list(by_local_day(smartenergy_intervals)) == [date(2026, 10, 9)]


def test_autumn_dst_day_needs_100_slots() -> None:
    day = date(2026, 10, 25)
    full = day_intervals(day, lambda i: 10.0 + i % 7)
    assert len(full) == 100 and structure_findings(day, full) == []
    assert structure_findings(day, full[:96]) == ["4 Slots fehlen"]


def test_spring_dst_day_needs_92_slots() -> None:
    day = date(2027, 3, 28)
    full = day_intervals(day, lambda i: 10.0 + i % 7)
    assert len(full) == 92 and structure_findings(day, full) == []


def test_all_equal_day_is_suspicious() -> None:  # Ausfall wie im September 2025
    day = date(2026, 10, 9)
    findings = structure_findings(day, day_intervals(day, lambda i: 0.0))
    assert findings == ["alle Werte gleich (0.000 ct)"]


def test_duplicate_slot_is_suspicious() -> None:
    day = date(2026, 10, 9)
    intervals = day_intervals(day, lambda i: 10.0 + i % 7)
    assert duplicate_starts([*intervals, intervals[40]]) == [intervals[40].start]
    assert "Slot doppelt: 10:00" in structure_findings(day, [*intervals, intervals[40]])


def test_range_check_uses_net_values() -> None:
    t = datetime(2026, 10, 9, 8, tzinfo=UTC)
    assert range_findings({t: 110.0 / 1.2}) == []
    assert range_findings({t: 130.0 / 1.2}) == ["Wert außerhalb −50…100 ct/kWh: 108.33 ct um 10:00"]


def test_range_check_lists_at_most_three() -> None:
    t = datetime(2026, 10, 9, 8, tzinfo=UTC)
    net = {t + timedelta(minutes=15 * i): -60.0 for i in range(5)}
    findings = range_findings(net)
    assert len(findings) == 4 and findings[0].endswith("-60.00 ct um 10:00")
    assert findings[-1] == "…"


def test_detect_vat_recorded_day_is_gross(smartenergy_slots, energy_charts_slots) -> None:
    detection = detect_vat(smartenergy_slots, energy_charts_slots, 1.2)
    assert (detection.result, detection.slots) == ("gross", 96)
    assert detection.ratio == pytest.approx(1.2, abs=0.001)


def test_detect_vat_with_hourly_reference(smartenergy_slots, awattar_slots) -> None:
    assert detect_vat(smartenergy_slots, awattar_slots, 1.2).result == "gross"


def test_detect_vat_net(energy_charts_slots) -> None:
    hourly = hourly_means(energy_charts_slots)
    assert detect_vat(hourly, energy_charts_slots, 1.2).result == "net"


def test_detect_vat_ignores_small_reference_and_needs_24_slots(energy_charts_slots) -> None:
    small = dict.fromkeys(energy_charts_slots, 1.0)
    assert detect_vat(small, small, 1.2) == VatDetection(None, None, 0)
    five_hours = dict(list(energy_charts_slots.items())[:20])
    assert detect_vat(five_hours, five_hours, 1.2) == VatDetection(None, None, 20)


def test_detect_vat_undetermined_ratio(energy_charts_slots) -> None:
    odd = {t: v * 1.1 for t, v in energy_charts_slots.items()}
    detection = detect_vat(odd, energy_charts_slots, 1.2)
    assert detection.result is None and detection.ratio == pytest.approx(1.1)


def test_is_hourly(smartenergy_slots, energy_charts_slots) -> None:
    assert is_hourly(smartenergy_slots) and not is_hourly(energy_charts_slots)
    assert not is_hourly({})
