"""Laufzeit mit dem Akku-Sollwert im Regelzyklus (Plan 2c-2a, Design 5): der Override im Dry-Run
und live, die Sperre durch Dynamic ESS, das Auffrischen ohne Eintrag im Log, der gewollte Verfall
des Overrides ohne Alarm „fremde Änderung“ und Health. Dazu der 15-min-Wächter (Design 6): sein
Budget für Akku-Sollwert und Safety-Schicht (Regel 2) und sein Alarm in Health."""

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from tests.helpers import T0
from tests.integration.conftest import GOOD_STATE, allow_victron_live, run
from tests.integration.plant import Plant
from tests.integration.test_runtime_charging import alarm_ids

from bindaems.core.control.peak_guard import PEAK_EXCEEDED
from bindaems.shared.config import Config
from bindaems.shared.control import (
    VICTRON,
    ActuatorMode,
    ChargeMode,
    CommandKind,
    Intent,
    VehicleIntent,
)
from bindaems.shared.decisions import Decision, DecisionResult

OVERRIDE_PATH = "hub4/0/Overrides/Setpoint"
# Abends ohne PV: Der e-Golf lädt an der EVCS mit 3680 W (8 A auf zwei Phasen), das Haus braucht
# 1720 W. Der Akku (SOC 60 %) gibt 2400 W ab, 3000 W kommen aus dem Netz.
EVENING = {
    "pv.huawei.power_w": 0.0,
    **{f"pv.huawei.l{n}.power_w": 0.0 for n in (1, 2, 3)},
    **{f"vebus.l{n}.ac_out_power_w": 1300.0 for n in (1, 2, 3)},
    "battery.power_w": -2400.0,
    "battery.soc_pct": 60.0,
    "wallbox.evcs.power_w": 3680.0,
    "wallbox.evcs.current_a": 8.0,
}
# Die EVCS meldet „lädt“, evcc hält 8 A und „Laden an“; der Basis-Sollwert ist 0 W, kein Override
EGOLF_CHARGING = GOOD_STATE | {
    "wallbox.evcs.status": 2,
    "wallbox.evcs.gx_set_current_a": 8,
    "wallbox.evcs.gx_start_stop": 1,
    "wallbox.evcs.gx_current_a": 8,
    "ess.grid_setpoint_w": 0,
    "ess.override.setpoint": None,
}
EGOLF_NOW = {"egolf": VehicleIntent(mode=ChargeMode.NOW)}
FOREIGN = "competitor.foreign.victron"
# Der e-Golf in Sofort mit 7360 W (16 A auf zwei Phasen), das Haus braucht wie oben 1720 W. Der Akku
# gibt 2400 W ab, 6680 W kommen aus dem Netz.
FULL = EVENING | {
    "wallbox.evcs.power_w": 7360.0,
    "wallbox.evcs.current_a": 16.0,
    "grid.power_w": 6680.0,
    **{f"grid.l{n}.power_w": 6680.0 / 3 for n in (1, 2, 3)},
}
# evcc hält 16 A und „Laden an“; der Basis-Sollwert ist 1000 W, kein Override
EGOLF_FULL = EGOLF_CHARGING | {
    "wallbox.evcs.gx_set_current_a": 16,
    "wallbox.evcs.gx_current_a": 16,
    "ess.grid_setpoint_w": 1000,
}
# Die erste ganze Viertelstunde: Die Regelung beginnt erst um 10:00:30 (CONTROL ab 30 s), die
# Viertelstunde davor ist lückenhaft.
QUARTER = datetime(2026, 10, 8, 10, 15, tzinfo=UTC)


def victron_decisions(rt) -> list[Decision]:
    """Die Entscheidungen an Victron, aufsteigend."""
    decisions, _ = rt.decisions(T0 - timedelta(days=1), 1000)
    return [d for d in decisions if d.actuator == VICTRON]


def override_writes(plant: Plant) -> list[float | int | None]:
    """Die Werte aller Schreibaufträge an den Override, in ihrer Reihenfolge."""
    return [value for path, value in plant.writes if path == OVERRIDE_PATH]


def peak_target(cfg: Config) -> Config:
    """Die Konfiguration mit dem Ziel 8 kW für den 15-min-Wächter."""
    control = cfg.control.model_copy(update={"peak_target_kw": 8.0})
    return cfg.model_copy(update={"control": control})


def dry_run(runtime_env, state: dict[str, Any]):
    """Laufzeit mit der Absicht „Regelbetrieb an, e-Golf in Sofort“, alle Aktoren im Dry-Run."""
    rt, _, clock = runtime_env(state=state, extra=EVENING)
    rt.submit_intent(Intent(version=1, control=True, vehicles=EGOLF_NOW))
    return rt, clock


def live_victron(runtime_env, plant: Plant, state: dict[str, Any]):
    """Laufzeit an der Anlage ``plant`` wie ``dry_run``, Victron aber live."""
    rt, _, clock = runtime_env(
        state=state,
        extra=EVENING,
        configure=allow_victron_live,
        mqtt_write=plant.write,
        before_cycle=plant.before_cycle,
    )
    plant.attach(rt.store)
    live = {VICTRON: ActuatorMode.LIVE}
    rt.submit_intent(Intent(version=1, control=True, actuators=live, vehicles=EGOLF_NOW))
    return rt, clock


async def test_dry_run_override_for_a_car_in_now(runtime_env) -> None:
    rt, clock = dry_run(runtime_env, dict(EGOLF_CHARGING))
    run(rt, clock, 31)  # CONTROL ab 30 s: der erste Regelzyklus
    [decision] = victron_decisions(rt)
    assert (decision.kind, decision.requested, decision.applied) == (
        CommandKind.GRID_SETPOINT,
        3680.0,
        3680.0,
    )
    assert (decision.result, decision.dry_run, decision.codes) == (
        DecisionResult.DRY_RUN,
        True,
        ("battery_comp",),
    )
    assert decision.text == (
        "Victron (ESS) (Akku nicht ins Auto): Netz-Sollwert 3680 W – Dry-Run, nicht gesendet."
    )
    own = {name: decision.inputs[name] for name in ("pv_w", "house_w", "cars_w", "target_w")}
    assert own == {"pv_w": 0.0, "house_w": 1720.0, "cars_w": 3680.0, "target_w": 3680.0}
    assert (decision.inputs["deficit_w"], decision.inputs["allowed_w"]) == (3680.0, 0.0)
    assert decision.inputs["actual_setpoint_w"] is None  # kein Override am Cerbo
    run(rt, clock, 300)  # dasselbe Ziel: keine weitere Entscheidung, auch nicht beim Auffrischen
    assert len(victron_decisions(rt)) == 1


async def test_dess_lock_sends_nothing(runtime_env) -> None:  # Review Focus 2
    state = EGOLF_CHARGING | {"dess.mode": 1}
    rt, clock = dry_run(runtime_env, state)
    run(rt, clock, 31 + 120)
    assert rt.health().mode == "DEGRADED"  # Regelbetrieb mit gesperrtem Victron
    assert victron_decisions(rt) == []  # kein Befehl, also auch keine Ablehnung im Log
    battery = rt.health().battery
    assert (battery["state"], battery["target_w"]) == ("locked", 3680.0)
    state["dess.mode"] = 0  # Dynamic ESS aus: Die Sperre endet 60 s nach der letzten Ursache
    run(rt, clock, 90)
    [decision] = victron_decisions(rt)  # ohne eigenen Wert beginnt der Override neu
    assert (decision.requested, decision.result) == (3680.0, DecisionResult.DRY_RUN)
    assert rt.health().battery["state"] == "ok"


async def test_live_override_is_refreshed_without_a_decision(runtime_env) -> None:
    plant = Plant(watchdog_state=1)  # scharf für diesen core, sobald er sein Echo zeigt
    rt, clock = live_victron(runtime_env, plant, dict(EGOLF_CHARGING))
    run(rt, clock, 33)  # CONTROL ab 31 s: Der Override geht hinaus, bei 32 s ist er bestätigt
    [decision] = victron_decisions(rt)
    assert (decision.requested, decision.result) == (3680.0, DecisionResult.OK)
    assert decision.text == (
        "Victron (ESS) (Akku nicht ins Auto): Netz-Sollwert 3680 W – gesendet und bestätigt."
    )
    assert override_writes(plant) == [3680.0]
    run(rt, clock, 238)  # bis 270 s: 239 s nach dem Urteil, noch nicht fällig
    assert override_writes(plant) == [3680.0]
    run(rt, clock, 2)  # bei 271 s das Auffrischen, bei 272 s bestätigt
    assert override_writes(plant) == [3680.0, 3680.0]
    assert victron_decisions(rt) == [decision]  # kein Eintrag im Log
    assert rt.health().battery["refresh_age_s"] == 1.0


@pytest.mark.parametrize(("after_s", "alarm"), [(300, False), (100, True)])
async def test_override_that_is_no_longer_refreshed_lapses_without_alarm(
    runtime_env, after_s, alarm
) -> None:  # Review Focus 1
    plant = Plant(watchdog_state=1)
    state = dict(EGOLF_CHARGING)
    rt, clock = live_victron(runtime_env, plant, state)
    run(rt, clock, 33)  # der Override, bei 32 s bestätigt
    [confirmed] = victron_decisions(rt)
    state["pv.huawei.power_w"] = None  # die PV fehlt: Das Ziel ist unbekannt, kein Auffrischen
    lapsed = confirmed.ts + timedelta(seconds=after_s)
    run(rt, clock, round((lapsed - clock.now()).total_seconds()))
    assert rt.health().battery["state"] == "unknown"
    plant.follow = False  # ab jetzt meldet der Cerbo „kein Override“ (aus ``state``)
    run(rt, clock, 40)  # drei Prüfungen im Abstand von 10 s fänden eine fremde Änderung
    assert override_writes(plant) == [3680.0]
    assert (FOREIGN in alarm_ids(rt)) is alarm


async def test_health_shows_the_battery(runtime_env) -> None:
    rt, clock = dry_run(runtime_env, dict(EGOLF_CHARGING))
    assert rt.health().battery["state"] == "idle"
    run(rt, clock, 31)  # der erste Override bei 30 s
    assert rt.health().battery == {
        "state": "ok",
        "target_w": 3680.0,
        "override_w": 3680.0,
        "support": "soc",  # der Tesla am Wall Connector in PV-Überschuss; SOC unter 70 %
        "bridging_s": None,
        "refresh_age_s": 0.0,
    }


async def test_peak_budget_reaches_the_safety_layer(runtime_env) -> None:  # Präzisierung 12
    rt, _, clock = runtime_env(state=dict(EGOLF_FULL), extra=FULL, configure=peak_target)
    rt.submit_intent(Intent(version=1, control=True, vehicles=EGOLF_NOW))  # Victron im Dry-Run
    budgets: dict[datetime, float | None] = {}
    states: dict[datetime, str] = {}
    for _ in range(17 * 60):  # bis 10:16:59
        now = clock.now()
        rt.cycle_once()
        budgets[now], states[now] = rt.regulation.grid_budget_w(), rt.health().peak["state"]
        clock.advance(1)
    # Review Focus 4: Die Regelung beginnt mitten in der Viertelstunde; bis zur nächsten ist sie
    # lückenhaft, ohne Budget.
    assert {(states[t], budgets[t]) for t in budgets if t < QUARTER} == {("gap", None)}
    after = [t for t in budgets if t >= QUARTER]
    assert {states[t] for t in after} == {"active"}  # die Hochrechnung bleibt bei 6680 W
    assert all(isinstance(budgets[t], float) and math.isfinite(budgets[t]) for t in after)
    # Der Akku-Sollwert will 1000 + 7360 W. Ab 10:15 senkt ihn das Budget P_zul (dann 7700 W, es
    # steigt mit der Viertelstunde) mit dem Grund ``peak_guard``; der Override jeder Entscheidung
    # liegt höchstens bei P_zul ihres Zyklus.
    decisions = [d for d in victron_decisions(rt) if d.ts >= QUARTER]
    assert [(d.ts, d.requested) for d in decisions] == [
        (QUARTER, 7700.0),
        (QUARTER + timedelta(seconds=116), 7900.0),
    ]
    assert decisions[0].codes == ("battery_comp", "peak_guard")
    assert all(d.applied <= budgets[d.ts] for d in decisions)
    # Um 10:16:56 ist P_zul 7895,3 W, das Ziel auf 10 W gerundet 7900 W: Regel 2 kappt es am Budget.
    assert decisions[1].applied == pytest.approx(7895.31, abs=0.01)
    assert decisions[1].codes == ("battery_comp", "peak_guard", "range.ceiling")


async def test_peak_exceeded_is_an_alarm_in_health(runtime_env) -> None:  # Präzisierung 14
    # Dynamic ESS hält Victron (gesperrt), der Akku hilft nicht: e-Golf und Haus ziehen 9080 W aus
    # dem Netz, mehr als das Ziel 8 kW.
    drawn = FULL | {
        **{f"vebus.l{n}.ac_in_power_w": 1300.0 for n in (1, 2, 3)},
        "battery.power_w": 0.0,
        "grid.power_w": 9080.0,
        **{f"grid.l{n}.power_w": 9080.0 / 3 for n in (1, 2, 3)},
    }
    state = EGOLF_FULL | {"dess.mode": 1}
    rt, _, clock = runtime_env(state=state, extra=drawn, configure=peak_target)
    rt.submit_intent(Intent(version=1, control=True, vehicles=EGOLF_NOW))
    run(rt, clock, 16 * 60)  # bis 10:15:59: 59 s über dem Ziel
    assert rt.health().mode == "DEGRADED"  # Regelbetrieb mit gesperrtem Victron
    assert rt.health().peak["state"] == "active"
    assert PEAK_EXCEEDED not in alarm_ids(rt)
    run(rt, clock, 1)  # 10:16:00: 60 s
    assert rt.health().peak["state"] == "exceeded"
    [alarm] = [alarm for alarm in rt.health().alarms if alarm["id"] == PEAK_EXCEEDED]
    assert alarm == {
        "id": PEAK_EXCEEDED,
        "severity": "warning",
        "message": "15-min-Ziel überschritten: 9,1 kW in der Viertelstunde bis 12:30.",
        "since": "2026-10-08T10:16:00+00:00",
    }
