from datetime import timedelta

from tests.helpers import T0, snap

from bindaems.core.checks.selfcheck import CRITICAL_SIGNALS, CheckResult, run_selfcheck
from bindaems.shared.domain import SignalKind

STATE = SignalKind.STATE
GOOD = {
    "vebus.phases": 3,
    "ess.hub4_mode": 1,
    "ess.batterylife_state": 10,
    "dess.mode": 0,
    "ess.schedule.0.day": -7,
    "ess.min_soc_pct": 20,
    "wallbox.evcs.mode": 0,
}


def fresh(_: str):
    return T0 - timedelta(seconds=31)


def by_id(results):
    return {r.id: r for r in results}


def test_all_ok(cfg) -> None:
    results = run_selfcheck(snap(GOOD, kind=STATE), cfg, fresh, T0)
    assert [(r.id, r.status) for r in results] == [
        ("phases", "ok"),
        ("ess_mode", "ok"),
        ("batterylife", "ok"),
        ("dess", "ok"),
        ("schedules", "ok"),
        ("min_soc", "ok"),
        ("evcs_mode.evcs", "ok"),
        ("fresh_data", "ok"),
    ]
    res = by_id(results)
    assert res["phases"].message == "3 Phasen erkannt."
    assert res["ess_mode"].message == "ESS-Modus 1 wie erwartet."
    assert res["batterylife"].message == "BatteryLife-Zustand 10 wie erwartet."
    assert res["dess"].message == "Dynamic ESS ist aus."
    assert res["schedules"].message == "Keine Victron-Ladefenster aktiv."
    assert res["min_soc"].message == "Victron-Min-SOC entspricht der USV-Reserve."
    assert res["evcs_mode.evcs"].message == "EVCS im manuellen Modus."
    assert res["fresh_data"].message == "Kritische Messwerte seit mindestens 30 s aktuell."


def test_failures_have_exact_texts(cfg) -> None:
    bad = GOOD | {
        "vebus.phases": 1,
        "ess.hub4_mode": 3,
        "ess.batterylife_state": 2,
        "dess.mode": 1.0,
        "ess.schedule.0.day": 7,
        "ess.schedule.3.day": 0,
        "wallbox.evcs.mode": 1,
    }
    res = by_id(run_selfcheck(snap(bad, kind=STATE), cfg, fresh, T0))
    assert res["phases"] == CheckResult("phases", "fail", "Erwartet 3 Phasen, gemeldet 1.")
    assert res["ess_mode"] == CheckResult("ess_mode", "fail", "ESS-Modus 3, erwartet 1.")
    assert res["batterylife"].message == "BatteryLife-Zustand 2, erwartet [10]."
    assert res["dess"] == CheckResult("dess", "fail", "Dynamic ESS ist aktiv (Modus 1).")
    assert res["schedules"].message == "Aktive Victron-Ladefenster: 0, 3."
    assert res["evcs_mode.evcs"] == CheckResult(
        "evcs_mode.evcs", "fail", "EVCS im Modus 1 statt manuell."
    )


def test_min_soc_below_reserve_fails(cfg) -> None:
    res = by_id(run_selfcheck(snap(GOOD | {"ess.min_soc_pct": 10}, kind=STATE), cfg, fresh, T0))
    assert res["min_soc"].status == "fail"
    assert res["min_soc"].message == "Victron-Min-SOC 10 % liegt unter der USV-Reserve 20 %."


def test_min_soc_above_reserve_warns(cfg) -> None:
    res = by_id(run_selfcheck(snap(GOOD | {"ess.min_soc_pct": 30}, kind=STATE), cfg, fresh, T0))
    assert res["min_soc"].status == "warn"
    assert res["min_soc"].message == "Victron-Min-SOC 30 % liegt über der USV-Reserve 20 %."


def test_missing_signal_is_unknown(cfg) -> None:
    values = {k: v for k, v in GOOD.items() if k != "vebus.phases"}
    res = by_id(run_selfcheck(snap(values, kind=STATE), cfg, lambda s: T0, T0))
    assert res["phases"] == CheckResult("phases", "unknown", "Phasenzahl nicht verfügbar.")


def test_all_unknown_without_signals(cfg) -> None:
    res = by_id(run_selfcheck(snap({}, kind=STATE), cfg, fresh, T0))
    assert {k: v.message for k, v in res.items() if v.status == "unknown"} == {
        "phases": "Phasenzahl nicht verfügbar.",
        "ess_mode": "ESS-Modus nicht verfügbar.",
        "batterylife": "BatteryLife-Zustand nicht verfügbar.",
        "dess": "DESS-Modus nicht verfügbar.",
        "schedules": "Ladefenster nicht verfügbar.",
        "min_soc": "Victron-Min-SOC nicht verfügbar.",
        "evcs_mode.evcs": "EVCS-Modus nicht verfügbar.",
    }


def test_fresh_data_requires_30s(cfg) -> None:
    res = by_id(
        run_selfcheck(snap(GOOD, kind=STATE), cfg, lambda s: T0 - timedelta(seconds=10), T0)
    )
    assert res["fresh_data"].status == "fail"
    assert res["fresh_data"].message.startswith("Nicht aktuell: grid.l1.power_w")


def test_fresh_data_lists_only_missing_signals(cfg) -> None:
    def since(signal: str):
        return None if signal in ("battery.soc_pct", "grid.l3.power_w") else fresh(signal)

    res = by_id(run_selfcheck(snap(GOOD, kind=STATE), cfg, since, T0))
    assert res["fresh_data"].message == "Nicht aktuell: grid.l3.power_w, battery.soc_pct."
    assert "battery.soc_pct" in CRITICAL_SIGNALS
