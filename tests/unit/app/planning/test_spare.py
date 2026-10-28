"""Tests des übrigen Akkus für Autos in Sofort (Plan Akku ins Auto, Entscheidungen 2 und 5)."""

from __future__ import annotations

from tests.plan_helpers import scenario

from bindaems.app.planning.battery_model import SimStep
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.spare import FULL_TOL_KWH, now_battery_flags, spare_value_ct
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind

EIGEN = BatteryIntent()
HALTEN = BatteryIntent(kind=BatteryIntentKind.HALTEN)
NETZLADEN_2000 = BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2000.0)


def _inputs(
    *,
    import_ct: list[float] | None = None,
    feed_in: list[float | None] | None = None,
    end_value_ct: float = 0.0,
    car_battery_allowed: bool = True,
) -> PlanInputs:
    """Standardakku (30 kWh, Reserve 6 kWh, Obergrenze 30 kWh), SOC 50 %, ohne PV; Verschleiß
    und Marge je 2 ct, Wirkungsgrad je Richtung √0,88 ≈ 0,938."""
    count = len(import_ct or feed_in or [0.0] * 4)
    prices = import_ct or [20.0] * count
    inputs = scenario(
        prices=prices,
        pv=[0.0] * count,
        load=[500.0] * count,
        end_value_ct=end_value_ct,
        car_battery=car_battery_allowed,
    )
    if feed_in is not None:
        slots = [
            slot.model_copy(update={"feed_in_ct": ct})
            for slot, ct in zip(inputs.slots, feed_in, strict=True)
        ]
        inputs = inputs.model_copy(update={"slots": slots})
    return inputs


def _steps(*, soc_end: list[float], export_w: list[float], hi_kwh: float = 30.0) -> list[SimStep]:
    """Nachrechnung mit diesem SOC am Slotende (kWh) und dieser Einspeisung (W); ``hi_kwh`` ist
    die Obergrenze, die ``_inputs`` ergibt."""
    assert hi_kwh == 30.0  # Obergrenze des Standardakkus
    starts = [15.0, *soc_end[:-1]]
    return [
        SimStep(
            soc_start_kwh=start,
            soc_end_kwh=end,
            import_w=0.0,
            export_w=export,
            charge_pv_w=0.0,
            charge_grid_w=0.0,
            discharge_w=0.0,
            cost_ct=0.0,
        )
        for start, end, export in zip(starts, soc_end, export_w, strict=True)
    ]


def _steps_never_full() -> list[SimStep]:
    return _steps(soc_end=[14.0, 13.0, 12.0], export_w=[0.0] * 3)


def _steps_until_empty_at(index: int) -> list[SimStep]:
    """Drei Slots; der Akku ist am Ende des Slots ``index`` leer (Reserve 6 kWh), danach füllt
    ihn die PV ein wenig."""
    soc_end = [12.0, 9.0, 6.05]
    soc_end[index] = 6.05
    for later in range(index + 1, 3):
        soc_end[later] = 7.0 + later
    return _steps(soc_end=soc_end, export_w=[0.0] * 3)


def test_spare_value_is_feed_in_when_the_battery_fills_later() -> None:
    steps = _steps(soc_end=[18, 17, 30, 30], export_w=[0, 0, 0, 800], hi_kwh=30)
    assert spare_value_ct(_inputs(feed_in=[6, 6, 6, 5], end_value_ct=15), steps, 0) == 5


def test_spare_value_is_end_value_when_the_battery_never_fills() -> None:
    steps = _steps(soc_end=[18, 17, 16, 15], export_w=[0, 0, 0, 0], hi_kwh=30)
    assert spare_value_ct(_inputs(end_value_ct=15), steps, 0) == 15


def test_spare_value_counts_a_full_battery_within_the_tolerance_and_no_price_as_zero() -> None:
    steps = _steps(soc_end=[18, 30 - FULL_TOL_KWH, 30, 30], export_w=[0, 500, 0, 0], hi_kwh=30)
    assert spare_value_ct(_inputs(feed_in=[6, None, 6, 6], end_value_ct=15), steps, 0) == 0
    # nur spätere Slots zählen
    assert spare_value_ct(_inputs(feed_in=[6, 4, 6, 6], end_value_ct=15), steps, 1) == 15


def test_now_battery_needs_switch_price_and_eigenverbrauch() -> None:
    inputs = _inputs(
        import_ct=[25, 8, 25], end_value_ct=10, car_battery_allowed=True
    )  # wear 2, margin 2
    flags = now_battery_flags(inputs, [EIGEN, EIGEN, HALTEN], _steps_never_full())
    assert flags == [True, False, False]  # 25−4 ≥ 10/0,938; 8−4 < 10,66; Halten
    off = inputs.model_copy(
        update={"planning": inputs.planning.model_copy(update={"car_battery_allowed": False})}
    )
    assert now_battery_flags(off, [EIGEN] * 3, _steps_never_full()) == [False] * 3


def test_now_battery_off_while_grid_energy_is_in_the_battery() -> None:
    flags = now_battery_flags(
        _inputs(import_ct=[5, 30, 30]), [NETZLADEN_2000, EIGEN, EIGEN], _steps_until_empty_at(2)
    )
    assert flags == [False, False, False]
    # Ist der Akku nach dem Netzladen leer, gibt er danach wieder
    flags = now_battery_flags(
        _inputs(import_ct=[5, 30, 30]), [NETZLADEN_2000, EIGEN, EIGEN], _steps_until_empty_at(0)
    )
    assert flags == [False, True, True]


def test_now_battery_compares_with_the_feed_in_of_a_later_full_battery() -> None:
    # 12 − 4 = 8 < 10/0,938 (Restwert), aber ≥ 5/0,938 (Einspeisung bei vollem Akku im Slot 2)
    inputs = _inputs(import_ct=[12, 12, 12], feed_in=[5, 5, 5], end_value_ct=10)
    full_later = _steps(soc_end=[14.0, 13.0, 30.0], export_w=[0.0, 0.0, 800.0])
    assert now_battery_flags(inputs, [EIGEN] * 3, full_later)[:2] == [True, True]
    assert now_battery_flags(inputs, [EIGEN] * 3, _steps_never_full()) == [False] * 3


def test_now_battery_off_while_grid_energy_from_before_the_horizon_is_in_the_battery() -> None:
    # Fix-Welle F2: Netzladen vor dem Horizont (``grid_in_battery_at_start``) sperrt ab Slot 0, bis
    # der Akku in der Nachrechnung leer ist; der Slot, in dem er leer wird, zählt noch dazu.
    inputs = _inputs(import_ct=[30, 30, 30]).model_copy(update={"grid_in_battery_at_start": True})
    assert now_battery_flags(inputs, [EIGEN] * 3, _steps_until_empty_at(1)) == [
        False,
        False,
        True,
    ]
    assert now_battery_flags(inputs, [EIGEN] * 3, _steps_never_full()) == [False] * 3
