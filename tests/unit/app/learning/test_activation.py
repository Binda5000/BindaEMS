from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from bindaems.app.forecast.archive import Actual, ForecastRecord, HorizonClass
from bindaems.app.learning.activation import (
    ACTIVATION_DAYS,
    MIN_SHADOW_DAYS,
    Activation,
    decide,
    shadow_sums,
)
from bindaems.app.learning.backtest import DaySums
from bindaems.shared.timeutil import LOCAL_TZ

TODAY = date(2026, 10, 20)


def _days(count: int, model_w: float, profile_w: float, *, n: int = 100) -> dict[date, DaySums]:
    """``count`` volle Tage vor heute (gestern zuerst) mit dem gegebenen MAE."""
    return {
        TODAY - timedelta(days=k): DaySums(n, model_w * n, profile_w * n)
        for k in range(1, count + 1)
    }


def _decide(
    shadow: dict[date, DaySums],
    backtest: dict[date, DaySums] | None = None,
    *,
    enabled: bool = True,
    has_model: bool = True,
) -> Activation:
    return decide(shadow, backtest, enabled=enabled, has_model=has_model, today=TODAY)


def test_exactly_ten_percent_activates() -> None:
    result = _decide(_days(14, 225.0, 250.0))
    assert result.active is True
    assert result.reason == "better"
    assert result.basis == "shadow"
    assert result.days == 14
    assert result.model_mae_w == pytest.approx(225.0)
    assert result.profile_mae_w == pytest.approx(250.0)
    assert result.improvement_pct == pytest.approx(10.0)


def test_just_below_ten_percent_keeps_profile() -> None:
    result = _decide(_days(14, 225.1, 250.0))
    assert result.active is False
    assert result.reason == "not_better"
    assert result.improvement_pct is not None
    assert result.improvement_pct < 10.0


def test_shadow_wins_from_ten_days() -> None:
    shadow = _days(MIN_SHADOW_DAYS, 260.0, 250.0)  # Modell im Betrieb schlechter
    backtest = _days(14, 100.0, 250.0)  # im Backtest deutlich besser
    result = _decide(shadow, backtest)
    assert result.basis == "shadow"
    assert result.days == MIN_SHADOW_DAYS
    assert result.active is False
    assert result.reason == "not_better"


def test_backtest_decides_before_ten_shadow_days() -> None:
    shadow = _days(MIN_SHADOW_DAYS - 1, 260.0, 250.0)
    backtest = _days(14, 200.0, 250.0)
    result = _decide(shadow, backtest)
    assert result.basis == "backtest"
    assert result.days == 14
    assert result.active is True
    assert result.reason == "better"
    assert result.improvement_pct == pytest.approx(20.0)


def test_backtest_needs_ten_days_too() -> None:
    result = _decide(_days(4, 200.0, 250.0), _days(MIN_SHADOW_DAYS - 1, 100.0, 250.0))
    assert result.basis == "none"
    assert result.days == 4
    assert result.reason == "too_few_days"
    assert result.active is False


def test_too_few_days_without_backtest() -> None:
    result = _decide(_days(4, 200.0, 250.0), None)
    assert result.active is False
    assert result.basis == "none"
    assert result.days == 4
    assert result.reason == "too_few_days"
    assert result.model_mae_w is None
    assert result.profile_mae_w is None
    assert result.improvement_pct is None
    assert result.text() == "Profil aktiv: Für den Vergleich fehlen noch Tage (4 von 10)."


def test_days_without_values_do_not_count() -> None:
    shadow = _days(14, 200.0, 250.0)
    for k in range(1, 6):
        shadow[TODAY - timedelta(days=k)] = DaySums(0, 0.0, 0.0)
    result = _decide(shadow)
    assert result.basis == "none"
    assert result.days == 9
    assert result.reason == "too_few_days"


def test_disabled_and_no_model() -> None:
    shadow = _days(14, 200.0, 250.0)
    disabled = _decide(shadow, enabled=False)
    assert disabled.active is False
    assert disabled.reason == "disabled"
    assert disabled.basis == "shadow"
    assert disabled.days == 14
    assert disabled.model_mae_w == pytest.approx(200.0)
    assert disabled.improvement_pct == pytest.approx(20.0)
    assert disabled.text() == "Profil aktiv: Das Lastmodell ist in den Einstellungen ausgeschaltet."

    no_model = _decide(shadow, has_model=False)
    assert no_model.active is False
    assert no_model.reason == "no_model"
    assert no_model.text() == "Profil aktiv: Es gibt noch kein brauchbares Modell."

    both = _decide(shadow, enabled=False, has_model=False)
    assert both.reason == "disabled"  # disabled vor no_model

    empty = _decide({}, enabled=False)
    assert empty.reason == "disabled"
    assert empty.basis == "none"
    assert empty.days == 0
    assert empty.model_mae_w is None


def test_window_excludes_today() -> None:
    shadow = _days(14, 200.0, 250.0)
    shadow[TODAY] = DaySums(100, 100.0 * 9000.0, 100.0 * 10.0)  # würde das Ergebnis kippen
    shadow[TODAY - timedelta(days=15)] = DaySums(100, 100.0 * 9000.0, 100.0 * 10.0)
    result = _decide(shadow)
    assert result.days == ACTIVATION_DAYS
    assert result.model_mae_w == pytest.approx(200.0)
    assert result.profile_mae_w == pytest.approx(250.0)
    assert result.active is True


def test_mae_weights_days_by_slot_count() -> None:
    shadow = _days(9, 100.0, 200.0, n=100)
    shadow[TODAY - timedelta(days=10)] = DaySums(300, 300.0 * 400.0, 300.0 * 400.0)
    result = _decide(shadow)
    assert result.model_mae_w == pytest.approx((9 * 100 * 100.0 + 300 * 400.0) / 1200)
    assert result.profile_mae_w == pytest.approx((9 * 100 * 200.0 + 300 * 400.0) / 1200)


def test_zero_profile_error_is_not_better() -> None:
    result = _decide(_days(14, 0.0, 0.0))
    assert result.active is False
    assert result.reason == "not_better"
    assert result.improvement_pct is None
    assert result.profile_mae_w == 0.0


def _record(
    slot: datetime, p50_w: float, *, horizon: HorizonClass = "h0_6", issued: datetime | None = None
) -> ForecastRecord:
    return ForecastRecord(
        kind="load",
        slot_start=slot,
        horizon=horizon,
        issued_at=issued or slot - timedelta(hours=1),
        p50_w=p50_w,
        raw_w=p50_w,
        clearness=None,
    )


def test_shadow_sums_pair_same_slot_and_horizon() -> None:
    day = datetime(2026, 10, 10, 12, 0, tzinfo=LOCAL_TZ)
    other = day + timedelta(minutes=15)
    third = day + timedelta(minutes=30)
    actual = {
        day: Actual(0.0, 300.0),
        other: Actual(0.0, 100.0),
    }
    model = [
        _record(day, 250.0),  # Fehler 50
        _record(day, 400.0, horizon="h6_24"),  # kein Partner mit gleichem Vorlauf
        _record(other, 130.0),  # Fehler 30
        _record(third, 10.0),  # kein Ist
    ]
    profile = [
        _record(day, 200.0),  # Fehler 100
        _record(day, 0.0, horizon="h24"),
        _record(other, 90.0),  # Fehler 10
        _record(third, 10.0),
    ]
    sums = shadow_sums(model, profile, actual)
    assert set(sums) == {date(2026, 10, 10)}
    assert sums[date(2026, 10, 10)].n == 2
    assert sums[date(2026, 10, 10)].model_abs_w == pytest.approx(80.0)
    assert sums[date(2026, 10, 10)].profile_abs_w == pytest.approx(110.0)


def test_shadow_sums_use_latest_issue_and_local_day() -> None:
    # 23:30 UTC ist in Wien schon der nächste Tag.
    slot = datetime(2026, 10, 10, 23, 30, tzinfo=UTC)
    early = slot - timedelta(hours=5)
    late = slot - timedelta(hours=1)
    actual = {slot: Actual(0.0, 100.0)}
    model = [_record(slot, 500.0, issued=early), _record(slot, 120.0, issued=late)]
    profile = [_record(slot, 90.0, issued=late), _record(slot, 900.0, issued=early)]
    sums = shadow_sums(model, profile, actual)
    assert set(sums) == {date(2026, 10, 11)}
    assert sums[date(2026, 10, 11)].n == 1
    assert sums[date(2026, 10, 11)].model_abs_w == pytest.approx(20.0)
    assert sums[date(2026, 10, 11)].profile_abs_w == pytest.approx(10.0)


def test_shadow_sums_empty() -> None:
    assert shadow_sums([], [], {}) == {}


def test_texts_for_better_and_worse() -> None:
    better = _decide(_days(14, 212.5, 250.0))
    assert better.text() == (
        "Modell aktiv: in den letzten 14 Tagen 15 % genauer als das Profil (im Schattenbetrieb)."
    )
    worse = _decide(_days(14, 260.0, 250.0))
    assert worse.text() == "Profil aktiv: Das Modell ist 4 % ungenauer als das Profil."


def test_text_for_backtest_basis() -> None:
    result = _decide(_days(2, 200.0, 250.0), _days(14, 200.0, 250.0))
    assert result.text() == (
        "Modell aktiv: in den letzten 14 Tagen 20 % genauer als das Profil (im Backtest)."
    )


def test_text_for_not_better_and_zero() -> None:
    assert _decide(_days(14, 230.0, 250.0)).text() == (
        "Profil aktiv: Das Modell ist nur 8 % genauer, nötig sind 10 %."
    )
    # Knapp verfehlt wird abgerundet, nie auf die nötigen 10 % aufgerundet.
    assert _decide(_days(14, 226.0, 250.0)).text() == (
        "Profil aktiv: Das Modell ist nur 9 % genauer, nötig sind 10 %."
    )
    # Auf 0 gerundet bleibt es bei „nur 0 %“, nie „−0 %“.
    assert _decide(_days(14, 250.4, 250.0)).text() == (
        "Profil aktiv: Das Modell ist nur 0 % genauer, nötig sind 10 %."
    )
    assert _decide(_days(14, 250.0, 250.0)).text() == (
        "Profil aktiv: Das Modell ist nur 0 % genauer, nötig sind 10 %."
    )
    assert _decide(_days(14, 0.0, 0.0)).text() == (
        "Profil aktiv: Das Modell ist nur 0 % genauer, nötig sind 10 %."
    )
    assert _decide(_days(14, 275.0, 250.0)).text() == (
        "Profil aktiv: Das Modell ist 10 % ungenauer als das Profil."
    )


def test_as_json_has_documented_keys() -> None:
    result = _decide(_days(14, 225.0, 250.0))
    data = result.as_json()
    assert list(data) == [
        "active",
        "basis",
        "days",
        "model_mae_w",
        "profile_mae_w",
        "improvement_pct",
        "reason",
        "text",
    ]
    assert data["active"] is True
    assert data["text"] == result.text()
    empty = _decide({}).as_json()
    assert empty["model_mae_w"] is None
    assert empty["improvement_pct"] is None
