"""Laufzeit mit dem Regelungs-Kern (Plan 2c-2a, Präzisierung 15): Eine Ausnahme in der Regelung
setzt nur die Regelung aus. Keeper, Alarme und Entscheidungslog laufen weiter, der Heartbeat bleibt
aus, und hört die Ausnahme auf, regelt der nächste Zyklus wieder."""

from datetime import datetime, timedelta

from tests.integration.conftest import live_evcs, run
from tests.integration.plant import Plant
from tests.integration.test_runtime_charging import (
    EGOLF_PLUGGED,
    SURPLUS,
    actuator_health,
    alarm_ids,
    charging_decisions,
    evcs_writes,
)

from bindaems.core.control.regulation import RegulationInputs
from bindaems.core.regulation_link import CONTROL_EXCEPTION
from bindaems.shared.control import ChargeMode, CommandKind, Intent, VehicleIntent
from bindaems.shared.decisions import DecisionResult

SAFE_CURRENT = ("evcharger/40/SetCurrent", 6)  # der Sicherwert der EVCS bei der Übergabe


def throwing(rt, monkeypatch, cycles: int | None = None) -> list[datetime]:
    """Der Kern wirft in ``step``, in den ersten ``cycles`` Schritten oder (``None``) immer.

    Zurück kommen die Zeiten aller Schritte; danach regelt der Kern wie sonst.
    """
    regulation = rt.regulation.regulation
    original = regulation.step
    steps: list[datetime] = []

    def step(inputs: RegulationInputs):
        steps.append(inputs.charge.now)
        if cycles is None or len(steps) <= cycles:
            raise RuntimeError("Testfehler in der Regelung")
        return original(inputs)

    monkeypatch.setattr(regulation, "step", step)
    return steps


async def test_exception_in_the_regulation_keeps_the_keeper_running(
    runtime_env, monkeypatch
) -> None:  # Review Focus 5
    plant = Plant(watchdog_state=1)
    state = dict(EGOLF_PLUGGED)
    rt, clock = live_evcs(runtime_env, plant=plant, state=state, extra=SURPLUS)
    run(rt, clock, 32 + 121)  # Start bei 151 s, bestätigt bei 152 s: Die EVCS hält Werte des EMS
    assert actuator_health(rt, "wallbox.evcs")["owned"] is True
    # Die EVCS bleibt bei 10 A und übernimmt den Sicherwert nicht: Die Übergabe bleibt offen.
    state |= {"wallbox.evcs.gx_set_current_a": 10, "wallbox.evcs.gx_start_stop": 1}
    plant.follow = False
    since = clock.now() - timedelta(microseconds=1)
    beats = plant.heartbeats()  # der letzte bei 150 s, der nächste wäre bei 160 s fällig
    steps = throwing(rt, monkeypatch)
    off = {"egolf": VehicleIntent(mode=ChargeMode.OFF)}
    rt.submit_intent(Intent(version=2, control=True, vehicles=off))  # EVCS im Dry-Run
    mark = len(plant.writes)
    run(rt, clock, 1)  # der erste Zyklus mit Ausnahme: Der Keeper sendet den Sicherwert trotzdem
    assert evcs_writes(plant, mark) == [SAFE_CURRENT]
    run(rt, clock, 14)  # kein Zyklus bricht ab; ohne Bestätigung folgt nach 10 s der zweite Versuch
    assert len(steps) == 15  # die Regelung warf in jedem Zyklus
    assert evcs_writes(plant, mark) == [SAFE_CURRENT] * 2
    during = rt.decisions(since, 1000)[0]
    assert [(d.kind, d.result, d.codes[0]) for d in during] == [
        (CommandKind.CURRENT, DecisionResult.TIMEOUT, "handback"),
    ]
    assert CONTROL_EXCEPTION in alarm_ids(rt)
    assert plant.heartbeats() == beats  # kein Heartbeat, solange die Regelung wirft
    monkeypatch.undo()
    run(rt, clock, 1)  # die Regelung geht wieder: Der fällige Zähler geht hinaus
    assert CONTROL_EXCEPTION not in alarm_ids(rt)
    assert plant.heartbeats() == [*beats, beats[-1] + 1]


async def test_after_an_exception_no_further_command_of_the_regulation_runs(
    runtime_env, monkeypatch
) -> None:
    rt, _, clock = runtime_env(state=dict(EGOLF_PLUGGED), extra=SURPLUS)
    rt.submit_intent(Intent(version=1, control=True))
    regulation = rt.regulation.regulation
    original = regulation.note_verdict

    def note_verdict(command, verdict, *, not_sent=False) -> None:
        if command.kind is CommandKind.CURRENT:
            raise RuntimeError("Testfehler in der Regelung")
        original(command, verdict, not_sent=not_sent)

    monkeypatch.setattr(regulation, "note_verdict", note_verdict)
    run(rt, clock, 32 + 125)  # Start nach 120 s: erst der Strom, dann „Laden an“
    found = [(d.kind, d.requested) for d in charging_decisions(rt)]
    assert found == [(CommandKind.CURRENT, 10.0)]  # „Laden an“ kam nicht mehr an die Reihe


async def test_regulation_recovers_after_the_exception(runtime_env, monkeypatch) -> None:
    rt, _, clock = runtime_env(state=dict(EGOLF_PLUGGED), extra=SURPLUS)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 30)  # OBSERVE; CONTROL ab 30 s
    steps = throwing(rt, monkeypatch, cycles=3)
    run(rt, clock, 3)
    assert len(steps) == 3
    assert CONTROL_EXCEPTION in alarm_ids(rt)
    assert [v["mode"] for v in rt.health().vehicles] == [None, None]  # noch kein guter Zyklus
    run(rt, clock, 1)
    assert CONTROL_EXCEPTION not in alarm_ids(rt)
    assert rt.health().vehicles[1]["mode"] == "pv"
    run(rt, clock, 125)  # Start nach 120 s mit genug Überschuss
    found = [(d.actuator, d.kind, d.requested, d.result, d.codes) for d in charging_decisions(rt)]
    # dieselben Entscheidungen wie in test_dry_run_decides_only_its_own_changes
    assert found == [
        (
            "wallbox.evcs",
            CommandKind.CURRENT,
            10.0,
            DecisionResult.DRY_RUN,
            ("mode_pv", "pv_surplus"),
        ),
        (
            "wallbox.evcs",
            CommandKind.CHARGING,
            True,
            DecisionResult.DRY_RUN,
            ("mode_pv", "pv_surplus"),
        ),
    ]
