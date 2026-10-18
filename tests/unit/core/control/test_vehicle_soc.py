from datetime import timedelta

import pytest
from tests.helpers import T0

from bindaems.core.adapters.tessie import FRESHNESS_S
from bindaems.core.control.vehicle_soc import (
    SOC_ANCHOR_BEFORE,
    TESSIE_FRESHNESS_S,
    SocReading,
    SocTracker,
)

T = T0
H = timedelta(hours=1)
MIN = timedelta(minutes=1)
EFF = 0.9
USABLE = 36.0


@pytest.fixture
def tracker() -> SocTracker:
    return SocTracker()


def update(tracker: SocTracker, key: str = "egolf", **kw: object) -> SocReading:
    args: dict[str, object] = {
        "wall_now": T,
        "measured": None,
        "measured_at": None,
        "counter_kwh": None,
        "session_start": T - H,
        "efficiency": EFF,
        "usable_kwh": USABLE,
    }
    args.update(kw)
    return tracker.update(key, **args)  # type: ignore[arg-type]


# Vorgaben des Plans


def test_measured_value_wins(tracker) -> None:
    r = tracker.update(
        "egolf",
        wall_now=T,
        measured=55.0,
        measured_at=T,
        counter_kwh=100.0,
        session_start=T - H,
        efficiency=0.9,
        usable_kwh=36.0,
    )
    assert r == SocReading(55.0, "measured")


def test_estimate_adds_counter_energy(tracker) -> None:
    update(tracker, measured=55.0, measured_at=T, counter_kwh=100.0)
    # später ohne Messung: 55 + 4 · 0,9 / 36 · 100 = 65
    r = update(tracker, wall_now=T + H, counter_kwh=104.0)
    assert r.source == "estimated"
    assert r.pct == pytest.approx(65.0)


def test_anchor_older_than_plug_in_is_ignored(tracker) -> None:
    start = T
    old = start - SOC_ANCHOR_BEFORE - MIN  # 61 min vor dem Anstecken: Das Auto kann gefahren sein
    r = update(tracker, measured=55.0, measured_at=old, counter_kwh=100.0, session_start=start)
    assert r == SocReading(0.0, "assumed")
    # auch später ohne Messung bleibt er unbrauchbar
    r = update(tracker, wall_now=T + H, counter_kwh=104.0, session_start=start)
    assert r == SocReading(0.0, "assumed")


def test_counter_reset_drops_the_anchor(tracker) -> None:
    update(tracker, measured=55.0, measured_at=T, counter_kwh=100.0)
    assert update(tracker, wall_now=T + H, counter_kwh=3.0) == SocReading(0.0, "assumed")
    # der Anker ist weg: auch ein Zähler über dem alten Stand schätzt nicht mehr
    assert update(tracker, wall_now=T + 2 * H, counter_kwh=104.0) == SocReading(0.0, "assumed")


def test_after_a_counter_reset_the_same_reading_anchors_anew(tracker) -> None:
    update(tracker, measured=55.0, measured_at=T, counter_kwh=100.0)
    # derselbe Messwert nach dem Tausch: neuer Anker beim neuen Zählerstand, nie negativ geschätzt
    r = update(tracker, wall_now=T + H, measured=55.0, measured_at=T, counter_kwh=3.0)
    assert r == SocReading(55.0, "measured")
    r = update(tracker, wall_now=T + 2 * H, counter_kwh=7.0)
    assert r.pct == pytest.approx(65.0)


# Weitere Fälle


def test_anchor_constant() -> None:
    assert timedelta(minutes=60) == SOC_ANCHOR_BEFORE


def test_without_any_value_it_is_assumed_zero(tracker) -> None:
    assert update(tracker, counter_kwh=100.0) == SocReading(0.0, "assumed")


def test_anchor_exactly_at_the_limit_counts(tracker) -> None:
    start = T
    at = start - SOC_ANCHOR_BEFORE
    r = update(tracker, measured=40.0, measured_at=at, counter_kwh=10.0, session_start=start)
    assert r == SocReading(40.0, "measured")


def test_anchor_from_a_previous_session_is_not_used_in_a_new_one(tracker) -> None:
    update(tracker, measured=55.0, measured_at=T, counter_kwh=100.0, session_start=T - H)
    later = T + 5 * H  # abgesteckt, gefahren, wieder angesteckt
    r = update(tracker, wall_now=later, counter_kwh=101.0, session_start=later)
    assert r == SocReading(0.0, "assumed")


def test_energy_counted_before_plug_in_is_not_added(tracker) -> None:
    # gemessen, als das Auto noch nicht steckte; die Wallbox lädt bis zum Anstecken ein anderes
    update(tracker, measured=55.0, measured_at=T - 30 * MIN, counter_kwh=100.0, session_start=None)
    assert update(tracker, counter_kwh=110.0, session_start=T) == SocReading(55.0, "estimated")
    r = update(tracker, wall_now=T + H, counter_kwh=114.0, session_start=T)
    assert r.pct == pytest.approx(65.0)


def test_a_reading_before_plug_in_counts_from_the_counter_of_the_new_wallbox(tracker) -> None:
    update(tracker, measured=55.0, measured_at=T - 30 * MIN, counter_kwh=100.0, session_start=None)
    # an einer anderen Wallbox angesteckt: deren Zähler steht tiefer, das ist kein Tausch
    assert update(tracker, counter_kwh=20.0, session_start=T) == SocReading(55.0, "estimated")
    r = update(tracker, wall_now=T + H, counter_kwh=24.0, session_start=T)
    assert r.pct == pytest.approx(65.0)


def test_an_unchanged_reading_before_plug_in_takes_the_counter_once(tracker) -> None:
    before = T - 30 * MIN
    update(tracker, measured=55.0, measured_at=before, counter_kwh=100.0, session_start=None)
    update(tracker, measured=55.0, measured_at=before, counter_kwh=110.0, session_start=T)
    r = update(tracker, measured=55.0, measured_at=before, counter_kwh=114.0, session_start=T)
    assert r.source == "estimated"
    assert r.pct == pytest.approx(65.0)


def test_an_unchanged_reading_with_counted_energy_is_estimated(tracker) -> None:
    # Home Assistant meldet den Ladestand nur beim Parken: derselbe Wert mit demselben
    # Zeitstempel ist keine neue Messung, die Energie seither kommt dazu
    update(tracker, measured=55.0, measured_at=T, counter_kwh=100.0)
    r = update(tracker, wall_now=T + H, measured=55.0, measured_at=T, counter_kwh=102.0)
    assert r.source == "estimated"
    assert r.pct == pytest.approx(60.0)


def test_a_new_reading_moves_the_anchor(tracker) -> None:
    update(tracker, measured=55.0, measured_at=T, counter_kwh=100.0)
    later = T + H
    assert update(
        tracker, wall_now=later, measured=70.0, measured_at=later, counter_kwh=104.0
    ) == SocReading(70.0, "measured")
    r = update(tracker, wall_now=later + H, counter_kwh=108.0)
    assert r.pct == pytest.approx(80.0)


def test_an_anchor_without_new_energy_is_estimated_at_its_value(tracker) -> None:
    update(tracker, measured=55.0, measured_at=T, counter_kwh=100.0)
    assert update(tracker, wall_now=T + H, counter_kwh=100.0) == SocReading(55.0, "estimated")


def test_estimate_never_exceeds_100(tracker) -> None:
    update(tracker, measured=95.0, measured_at=T, counter_kwh=100.0)
    assert update(tracker, wall_now=T + H, counter_kwh=120.0) == SocReading(100.0, "estimated")


def test_without_counter_there_is_no_estimate(tracker) -> None:
    update(tracker, measured=55.0, measured_at=T, counter_kwh=100.0)
    assert update(tracker, wall_now=T + H) == SocReading(0.0, "assumed")
    # der Anker bleibt: kommt der Zähler zurück, wird wieder geschätzt
    r = update(tracker, wall_now=T + 2 * H, counter_kwh=104.0)
    assert r.pct == pytest.approx(65.0)


def test_a_reading_without_counter_takes_the_first_known_counter(tracker) -> None:
    # der Wert aus Home Assistant kommt vor dem ersten Lesen des Zählers
    assert update(tracker, measured=55.0, measured_at=T) == SocReading(55.0, "measured")
    r = update(tracker, wall_now=T + MIN, measured=55.0, measured_at=T, counter_kwh=100.0)
    assert r == SocReading(55.0, "measured")
    r = update(tracker, wall_now=T + H, measured=55.0, measured_at=T, counter_kwh=104.0)
    assert r.source == "estimated"
    assert r.pct == pytest.approx(65.0)


def test_a_measurement_counts_without_a_session(tracker) -> None:
    r = update(tracker, measured=55.0, measured_at=T, counter_kwh=100.0, session_start=None)
    assert r == SocReading(55.0, "measured")


def test_without_a_session_nothing_is_estimated(tracker) -> None:
    update(tracker, measured=55.0, measured_at=T, counter_kwh=100.0)
    r = update(tracker, wall_now=T + H, counter_kwh=104.0, session_start=None)
    assert r == SocReading(0.0, "assumed")


def test_a_measurement_without_timestamp_is_taken_at_wall_now(tracker) -> None:
    start = T
    r = update(tracker, wall_now=start + MIN, measured=55.0, counter_kwh=100.0, session_start=start)
    assert r == SocReading(55.0, "measured")


def test_vehicles_are_tracked_separately(tracker) -> None:
    update(tracker, "egolf", measured=55.0, measured_at=T, counter_kwh=100.0)
    assert update(tracker, "tesla", counter_kwh=104.0) == SocReading(0.0, "assumed")
    assert update(tracker, "egolf", counter_kwh=104.0).pct == pytest.approx(65.0)


def test_reading_is_frozen() -> None:
    reading = SocReading(55.0, "measured")
    with pytest.raises(AttributeError):
        reading.pct = 60.0  # type: ignore[misc]


# Fix-Runde 1: Tessie frisch gemessen, Anker ohne Zähler, Schätzung über Ansteckvorgänge


def test_tessie_freshness_matches_the_adapter() -> None:
    assert TESSIE_FRESHNESS_S == FRESHNESS_S


def test_a_fresh_polled_value_stays_measured_while_the_counter_runs(tracker) -> None:
    # Tessie fragt alle 30 s ab, die Regelung läuft im Sekundentakt
    counter = 100.0
    for second in range(31):
        counter += 0.003
        now = T + timedelta(seconds=second)
        r = update(
            tracker,
            wall_now=now,
            measured=55.0,
            measured_at=T,
            counter_kwh=counter,
            polled=True,
        )
        assert r == SocReading(55.0, "measured")


def test_a_polled_value_older_than_the_freshness_is_estimated(tracker) -> None:
    update(tracker, measured=55.0, measured_at=T, counter_kwh=100.0, polled=True)
    at_limit = T + timedelta(seconds=TESSIE_FRESHNESS_S)
    r = update(
        tracker, wall_now=at_limit, measured=55.0, measured_at=T, counter_kwh=102.0, polled=True
    )
    assert r == SocReading(55.0, "measured")
    later = at_limit + timedelta(seconds=1)
    r = update(
        tracker, wall_now=later, measured=55.0, measured_at=T, counter_kwh=104.0, polled=True
    )
    # Anker beim Zählerstand der letzten frischen Messung (102): 55 + 2 · 0,9 / 36 · 100
    assert r.source == "estimated"
    assert r.pct == pytest.approx(60.0)


def test_a_new_session_continues_from_the_last_estimate_of_the_previous_one(tracker) -> None:
    first = T - H
    update(tracker, measured=55.0, measured_at=T, counter_kwh=100.0, session_start=first)
    r = update(tracker, wall_now=T + 10 * MIN, counter_kwh=104.0, session_start=first)
    assert r.pct == pytest.approx(65.0)
    # kurz ab- und wieder angesteckt; dazwischen hat die Wallbox ein anderes Auto geladen
    second = T + 20 * MIN
    r = update(tracker, wall_now=second, counter_kwh=110.0, session_start=second)
    assert r == SocReading(pytest.approx(65.0), "estimated")
    r = update(tracker, wall_now=second + H, counter_kwh=114.0, session_start=second)
    assert r.pct == pytest.approx(75.0)
