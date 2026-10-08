from datetime import timedelta

from tests.helpers import T0, snap
from tests.unit.core.state.conftest import BASE

from bindaems.core.state.derived import derive
from bindaems.core.state.plausibility import PlausibilityMonitor
from bindaems.shared.domain import Severity


def test_voltage_out_of_range_alarm(cfg) -> None:
    s = snap(BASE | {"grid.l2.voltage_v": 150.0})
    alarms = PlausibilityMonitor().evaluate(s, derive(s, cfg), T0)
    assert any(
        a.id == "plaus.voltage.L2" and a.message == "Netzspannung L2 unplausibel: 150.0 V"
        for a in alarms
    )


def test_grid_power_and_soc_alarms(cfg) -> None:
    s = snap(BASE | {"grid.l3.power_w": -31000.0, "battery.soc_pct": 104.0})
    alarms = {a.id: a for a in PlausibilityMonitor().evaluate(s, derive(s, cfg), T0)}
    assert alarms["plaus.grid_power.L3"].message == "Netzleistung L3 unplausibel: -31000 W"
    assert alarms["plaus.soc"].message == "Akku-SOC unplausibel: 104.0 %"
    assert {a.severity for a in alarms.values()} == {Severity.WARNING}


def test_no_alarm_for_plausible_values(cfg) -> None:
    s = snap(BASE | {"battery.soc_pct": 55.0})
    assert PlausibilityMonitor().evaluate(s, derive(s, cfg), T0) == []


def test_alarm_since_stays_at_first_occurrence(cfg) -> None:
    mon = PlausibilityMonitor()
    bad = snap(BASE | {"grid.l1.voltage_v": 300.0})
    good = snap(BASE)
    first = mon.evaluate(bad, derive(bad, cfg), T0)
    later = mon.evaluate(bad, derive(bad, cfg), T0 + timedelta(seconds=5))
    assert first[0].since == later[0].since == T0
    assert mon.evaluate(good, derive(good, cfg), T0 + timedelta(seconds=6)) == []
    again = mon.evaluate(bad, derive(bad, cfg), T0 + timedelta(seconds=7))
    assert again[0].since == T0 + timedelta(seconds=7)


def test_balance_alarm_after_full_window(cfg) -> None:
    mon = PlausibilityMonitor(window_s=60)
    # Residuum 3000 + 3000 + 2000 − 5400 = 2600 W > Grenze 810 W
    bad = derive(snap(BASE | {"battery.power_w": -2000.0}), cfg)
    for i in range(61):
        alarms = mon.evaluate(
            snap(BASE | {"battery.power_w": -2000.0}), bad, T0 + timedelta(seconds=i)
        )
    assert any(a.id == "plaus.balance" for a in alarms)
    balance = next(a for a in alarms if a.id == "plaus.balance")
    assert balance.message == "Energiebilanz unplausibel: Abweichung 2600 W (Grenze 810 W)"


def test_no_balance_alarm_before_window_full(cfg) -> None:
    mon = PlausibilityMonitor(window_s=60)
    bad = derive(snap(BASE | {"battery.power_w": -2000.0}), cfg)
    assert not [a for a in mon.evaluate(snap(BASE), bad, T0) if a.id == "plaus.balance"]
    for i in range(1, 60):
        alarms = mon.evaluate(snap(BASE), bad, T0 + timedelta(seconds=i))
    assert not [a for a in alarms if a.id == "plaus.balance"]  # 59 s: noch nicht voll


def test_gap_in_residual_restarts_window(cfg) -> None:
    mon = PlausibilityMonitor(window_s=60)
    values = BASE | {"battery.power_w": -2000.0}
    bad = derive(snap(values), cfg)
    gap = derive(snap({k: v for k, v in values.items() if not k.startswith("vebus")}), cfg)
    for i in range(61):
        derived = gap if i == 30 else bad
        alarms = mon.evaluate(snap(values), derived, T0 + timedelta(seconds=i))
    assert not [a for a in alarms if a.id == "plaus.balance"]


def test_balance_within_tolerance(cfg) -> None:
    mon = PlausibilityMonitor(window_s=60)
    ok = derive(snap(BASE | {"battery.power_w": 400.0}), cfg)  # Residuum ≈ 200 W
    for i in range(61):
        alarms = mon.evaluate(
            snap(BASE | {"battery.power_w": 400.0}), ok, T0 + timedelta(seconds=i)
        )
    assert not [a for a in alarms if a.id == "plaus.balance"]


def test_balance_limit_has_500_w_floor(cfg) -> None:
    mon = PlausibilityMonitor(window_s=60)
    # Verbrauch 1000 W, Akku 400 W → 15 % wären 150 W, die Grenze bleibt 500 W; Residuum 400 W
    small = (
        {f"grid.l{n}.power_w": 0.0 for n in (1, 2, 3)}
        | {f"vebus.l{n}.ac_in_power_w": 0.0 for n in (1, 2, 3)}
        | {f"vebus.l{n}.ac_out_power_w": 0.0 for n in (1, 2, 3)}
        | {f"pv.huawei.l{n}.power_w": 1000.0 / 3 for n in (1, 2, 3)}
        | {"grid.power_w": 0.0, "pv.huawei.power_w": 1000.0, "battery.power_w": -400.0}
    )
    d = derive(snap(small), cfg)
    assert d.balance_residual_w == 400.0
    for i in range(61):
        alarms = mon.evaluate(snap(small), d, T0 + timedelta(seconds=i))
    assert not [a for a in alarms if a.id == "plaus.balance"]
