import pytest

from bindaems.app.forecast.calibration import clearness_class


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
