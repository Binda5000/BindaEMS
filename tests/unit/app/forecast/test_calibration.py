import pytest

from bindaems.app.forecast.calibration import (
    DEFAULT_BAND,
    NEUTRAL,
    CalPair,
    ErrPair,
    apply_band,
    calibrate,
    clearness_class,
    fit_bands,
    pv_threshold_w,
)


def test_clearness_classes() -> None:
    assert clearness_class(800, 100) == "clear"
    assert clearness_class(400, 200) == "mixed"
    assert clearness_class(300, 270) == "overcast"
    assert clearness_class(30, 30) is None
    assert clearness_class(None, None) is None


@pytest.mark.parametrize(
    ("ghi", "diffuse", "expected"),
    [
        (400, 160, "clear"),  # k = 0,6 genau
        (400, 280, "mixed"),  # k = 0,3 genau
        (400, 281, "overcast"),
        (50, 10, "clear"),  # 50 W/m² gilt noch
        (49.9, 10, None),
        (500, None, None),
        (None, 100, None),
        (200, 250, "overcast"),  # diffus über global: k negativ
    ],
)
def test_clearness_boundaries(ghi, diffuse, expected) -> None:
    assert clearness_class(ghi, diffuse) == expected


def test_factor_is_median_ratio_per_hour_and_class():
    pairs = [CalPair(12, "clear", 5000, 4000)] * 10
    assert calibrate(pairs, 200).factor(12, "clear") == pytest.approx(0.8)


def test_factor_is_clamped():
    assert calibrate([CalPair(12, "clear", 5000, 500)] * 10, 200).factor(12, "clear") == 0.5
    assert calibrate([CalPair(12, "clear", 1000, 9000)] * 10, 200).factor(12, "clear") == 1.5


def test_factor_falls_back_to_hour_then_neutral():
    pairs = [CalPair(12, "clear", 5000, 4500)] * 9 + [CalPair(12, "mixed", 3000, 3000)] * 2
    cal = calibrate(pairs, 200)
    assert cal.factor(12, "mixed") == pytest.approx(0.9)  # Median der Stunde über 11 Paare
    assert cal.factor(9, "clear") == 1.0
    assert cal.samples == 11


def test_factor_needs_eight_pairs():
    assert calibrate([CalPair(12, "clear", 5000, 4000)] * 7, 200).factor(12, "clear") == 1.0
    assert calibrate([CalPair(12, "clear", 5000, 4000)] * 8, 200).factor(12, "clear") == 0.8


def test_factor_is_median_not_mean():
    pairs = [CalPair(12, "clear", 1000, 800)] * 5 + [CalPair(12, "clear", 1000, 50)] * 4
    assert calibrate(pairs, 200).factor(12, "clear") == pytest.approx(0.8)


def test_pairs_below_threshold_are_ignored():
    cal = calibrate([CalPair(7, "clear", 150, 50)] * 10, 200)
    assert cal.factor(7, "clear") == 1.0 and cal.samples == 0


def test_neutral_calibration_changes_nothing():
    assert NEUTRAL.factor(12, "clear") == 1.0 and NEUTRAL.samples == 0
    assert calibrate([], 200) == NEUTRAL


def test_bands_from_error_quartiles():
    ratios = [0.6 + 0.04 * i for i in range(21)]  # 0,60 … 1,40
    pairs = [ErrPair("h0_6", "clear", 1000, 1000 * r) for r in ratios]
    bands = fit_bands(pairs, 200)
    assert bands.band("h0_6", "clear") == pytest.approx((0.8, 1.2))
    assert bands.band("h0_6", "mixed") == pytest.approx((0.8, 1.2))  # Horizontklasse allein
    assert bands.band("h24", "mixed") == DEFAULT_BAND


def test_bands_prefer_the_class_over_the_horizon():
    clear = [ErrPair("h0_6", "clear", 1000, 1000 * (0.9 + 0.005 * i)) for i in range(21)]
    mixed = [ErrPair("h0_6", "mixed", 1000, 1000 * (0.5 + 0.02 * i)) for i in range(20)]
    bands = fit_bands(clear + mixed, 200)
    assert bands.band("h0_6", "clear") == pytest.approx((0.925, 0.975))
    assert bands.band("h0_6", "mixed") == pytest.approx((0.595, 0.785))
    assert bands.band("h0_6", "overcast") == bands.by_horizon["h0_6"]  # nur die Horizontklasse


def test_bands_need_twenty_pairs_and_ignore_small_forecasts():
    assert fit_bands([ErrPair("h0_6", "clear", 1000, 500)] * 19, 200).band("h0_6", "clear") == (
        DEFAULT_BAND
    )
    assert fit_bands([ErrPair("h0_6", "clear", 100, 50)] * 30, 200).band("h0_6", "clear") == (
        DEFAULT_BAND
    )


def test_bands_keep_order_and_inverter_limit():
    assert apply_band(9000.0, (0.9, 1.3), 10000.0) == (8100.0, 9000.0, 10000.0)
    assert apply_band(9000.0, (1.1, 1.3), 10000.0) == (9000.0, 9000.0, 10000.0)
    assert apply_band(9000.0, (0.9, 0.95), 10000.0) == (8100.0, 9000.0, 9000.0)


def test_threshold_is_two_percent_of_kwp_but_at_least_100_w(cfg):
    kwp = sum(plane.kwp for plane in cfg.pv.planes)
    assert pv_threshold_w(cfg) == max(100.0, 0.02 * kwp * 1000)
    assert pv_threshold_w(cfg) == pytest.approx(200.0)  # Beispiel-cfg: 10 kWp
    small = cfg.model_copy(
        update={
            "pv": cfg.pv.model_copy(
                update={"planes": [cfg.pv.planes[0].model_copy(update={"kwp": 1.0})]}
            )
        }
    )
    assert pv_threshold_w(small) == 100.0
