from dataclasses import replace
from datetime import timedelta

from tests.helpers import T0, snap
from tests.unit.core.control.fakes import FakeActuator
from tests.unit.core.safety.conftest import make_ctx

from bindaems.core.control.actuator import Capabilities, Confirmation
from bindaems.core.control.executor import CommandExecutor, WriteResult
from bindaems.core.safety.budget import PersistentWriteBudget
from bindaems.core.safety.layer import SafetyLayer
from bindaems.core.safety.types import Outcome
from bindaems.shared.control import VICTRON, Command, CommandKind, OperatingState
from bindaems.shared.decisions import Decision, DecisionResult

EVCS = "wallbox.evcs"
S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731


def setup(tmp_path, limits, **actuator) -> tuple[CommandExecutor, FakeActuator, list[Decision]]:
    fake = FakeActuator(EVCS, **actuator)
    decisions: list[Decision] = []
    layer = SafetyLayer(limits, PersistentWriteBudget(12, 100, tmp_path / "b.json", T0))
    ids = iter(f"d{i}" for i in range(100))
    executor = CommandExecutor(
        layer, {EVCS: fake}, decisions.append, {EVCS: "EVCS „evcs“"}, new_id=lambda: next(ids)
    )
    return executor, fake, decisions


SAFE = Command(EVCS, CommandKind.CURRENT, 6.0, urgent=True, reasons=("safe_state",))
LIVE_CTX = make_ctx(present={(EVCS, CommandKind.CURRENT): 16.0})


def test_dry_run_verdict_logs_without_sending(tmp_path, limits) -> None:
    executor, fake, decisions = setup(tmp_path, limits)
    ctx = make_ctx(modes={}, present={(EVCS, CommandKind.CURRENT): 16.0})
    executor.execute(SAFE, ctx, snap({}), T0, {})
    assert fake.sent == [] and [d.result for d in decisions] == [DecisionResult.DRY_RUN]


def test_live_write_is_confirmed_and_logged_once(tmp_path, limits_live) -> None:
    executor, fake, decisions = setup(tmp_path, limits_live)
    assert executor.execute(SAFE, LIVE_CTX, snap({}), T0, {}).send
    assert fake.sent == [(CommandKind.CURRENT, 6.0)] and decisions == []
    fake.echo[CommandKind.CURRENT] = 6
    results = executor.poll(snap({}), T0 + timedelta(seconds=2), T0)
    assert [r.result for r in results] == [DecisionResult.OK]
    assert decisions[0].text.startswith(
        "EVCS „evcs“ (sicherer Zustand): Strom 6 A – gesendet und bestätigt."
    )


def test_unconfirmed_write_times_out_once(tmp_path, limits_live) -> None:
    executor, _, decisions = setup(tmp_path, limits_live)
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    assert executor.poll(snap({}), T0 + timedelta(seconds=9), T0) == []
    assert [r.result for r in executor.poll(snap({}), T0 + timedelta(seconds=10), T0)] == [
        DecisionResult.TIMEOUT
    ]
    assert executor.poll(snap({}), T0 + timedelta(seconds=20), T0) == []
    assert [d.result for d in decisions] == [DecisionResult.TIMEOUT]
    assert [f.result for f in executor.take_failures()] == [DecisionResult.TIMEOUT]
    assert executor.take_failures() == []


def test_pending_write_is_not_reconsidered(tmp_path, limits_live) -> None:
    executor, fake, _ = setup(tmp_path, limits_live)
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    assert executor.execute(SAFE, LIVE_CTX, snap({}), T0, {}) is None
    assert executor.pending() == {(EVCS, CommandKind.CURRENT)} and len(fake.sent) == 1


def test_not_controllable_is_not_sent(tmp_path, limits_live) -> None:
    executor, fake, decisions = setup(tmp_path, limits_live, controllable=False)
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    assert fake.sent == []
    assert decisions[0].result == DecisionResult.NOT_SENT
    assert "nicht gesendet: nicht steuerbar: Cerbo nicht verbunden." in decisions[0].text


def test_refused_send_is_not_sent(tmp_path, limits_live) -> None:
    executor, _, decisions = setup(
        tmp_path, limits_live, refuse="Cerbo-Verbindung: Auftrag nicht angenommen."
    )
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    assert decisions[0].result == DecisionResult.NOT_SENT and executor.pending() == frozenset()


def test_same_rejection_is_logged_once_a_minute(tmp_path, limits_live) -> None:
    executor, _, decisions = setup(tmp_path, limits_live)
    locked = make_ctx(locks={EVCS: ("EVCS nicht im manuellen Modus.",)})
    for seconds in (0, 30, 61):
        executor.execute(
            SAFE, replace(locked, now=T0 + timedelta(seconds=seconds)), snap({}), T0, {}
        )
    assert [d.result for d in decisions] == [DecisionResult.REJECTED, DecisionResult.REJECTED]


# --- Ab hier: weitere Tests zu Entscheidungen, Fristen, Fehlern und Urteilen ---

LOCKED = ("EVCS nicht im manuellen Modus.",)
STOP = Command(EVCS, CommandKind.CHARGING, False, urgent=True)
RELEASE = Command(VICTRON, CommandKind.GRID_SETPOINT, None, urgent=True)
TESLA_SAFE = Command("vehicle.tesla", CommandKind.CURRENT, 5.0, urgent=True)


def build(tmp_path, limits, *actuators: FakeActuator) -> tuple[CommandExecutor, list[Decision]]:
    """Ausführer für mehrere Aktoren; ``setup`` kennt nur die EVCS."""
    decisions: list[Decision] = []
    layer = SafetyLayer(limits, PersistentWriteBudget(12, 100, tmp_path / "b.json", T0))
    ids = iter(f"d{i}" for i in range(100))
    labels = {EVCS: "EVCS „evcs“", VICTRON: "Victron (ESS)"}
    executor = CommandExecutor(
        layer,
        {actuator.id: actuator for actuator in actuators},
        decisions.append,
        labels,
        new_id=lambda: next(ids),
    )
    return executor, decisions


class CountingLayer(SafetyLayer):
    """Zählt die Urteile: ``authorize`` verändert Zustand und darf je Befehl nur einmal laufen."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.calls = 0

    def authorize(self, command, ctx):
        self.calls += 1
        return super().authorize(command, ctx)


def test_each_considered_command_gets_exactly_one_verdict(tmp_path, limits_live) -> None:
    layer = CountingLayer(limits_live, PersistentWriteBudget(12, 100, tmp_path / "b.json", T0))
    fake = FakeActuator(EVCS)
    decisions: list[Decision] = []
    executor = CommandExecutor(layer, {EVCS: fake}, decisions.append, {EVCS: "EVCS „evcs“"})
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    assert layer.calls == 1
    for _ in range(3):  # offen: nicht betrachtet, die Safety-Schicht läuft nicht
        assert executor.execute(SAFE, LIVE_CTX, snap({}), T0, {}) is None
    assert layer.calls == 1
    fake.echo[CommandKind.CURRENT] = 6.0
    executor.poll(snap({}), S(1), T0)
    executor.execute(SAFE, replace(LIVE_CTX, now=S(6)), snap({}), T0, {})  # frei: wieder betrachtet
    assert layer.calls == 2


def test_decision_carries_all_fields(tmp_path, limits_live) -> None:
    executor, fake, decisions = setup(tmp_path, limits_live)
    inputs = {"battery.soc_pct": 46.0, "grid.l1.current_a": None}
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, inputs)
    inputs["battery.soc_pct"] = 99.0  # der Aufrufer darf seine Tabelle weiterverwenden
    fake.echo[CommandKind.CURRENT] = 6.0
    results = executor.poll(snap({}), S(2), S(3))
    assert results == [WriteResult(EVCS, CommandKind.CURRENT, 6.0, DecisionResult.OK, "")]
    assert decisions == [
        Decision(
            id="d0",
            ts=S(3),
            actuator=EVCS,
            kind=CommandKind.CURRENT,
            requested=6.0,
            applied=6.0,
            dry_run=False,
            outcome="accepted",
            result=DecisionResult.OK,
            codes=("safe_state",),
            text="EVCS „evcs“ (sicherer Zustand): Strom 6 A – gesendet und bestätigt.",
            inputs={"battery.soc_pct": 46.0, "grid.l1.current_a": None},
        )
    ]
    assert executor.take_failures() == []


def test_clamped_value_is_sent_confirmed_and_logged_as_applied(tmp_path, limits_live) -> None:
    executor, fake, decisions = setup(tmp_path, limits_live)
    ctx = make_ctx(present={(EVCS, CommandKind.CURRENT): 10.0})
    verdict = executor.execute(Command(EVCS, CommandKind.CURRENT, 20.0), ctx, snap({}), T0, {})
    assert verdict.outcome == Outcome.CLAMPED
    assert fake.sent == [(CommandKind.CURRENT, 16.0)]
    # Bestätigt wird der gesendete Wert, nicht der angeforderte.
    fake.echo[CommandKind.CURRENT] = 16.0
    executor.poll(snap({}), S(1), T0)
    (decision,) = decisions
    assert (decision.requested, decision.applied, decision.outcome, decision.codes) == (
        20.0,
        16.0,
        "clamped",
        ("range.max",),
    )
    assert decision.text == (
        "EVCS „evcs“: Strom 16 A – gesendet und bestätigt. Auf den Höchstwert 16 A begrenzt."
    )


def test_urgent_bypass_text_matches_the_plan_example(tmp_path, limits_live) -> None:
    executor, fake, decisions = setup(tmp_path, limits_live)
    executor.execute(Command(EVCS, CommandKind.CURRENT, 10.0), LIVE_CTX, snap({}), T0, {})
    fake.echo[CommandKind.CURRENT] = 10.0
    executor.poll(snap({}), S(1), T0)
    soon = make_ctx(now=S(2), present={(EVCS, CommandKind.CURRENT): 10.0})
    executor.execute(SAFE, soon, snap({}), S(2), {})
    fake.echo[CommandKind.CURRENT] = 6.0
    executor.poll(snap({}), S(3), S(3))
    assert decisions[-1].codes == ("safe_state", "urgent.bypass")
    assert decisions[-1].text == (
        "EVCS „evcs“ (sicherer Zustand): Strom 6 A – gesendet und bestätigt. "
        "Dringend (Richtung sicherer Zustand): ohne Mindestabstand und Limits."
    )


def test_dry_run_decision_is_marked_and_opens_no_write(tmp_path, limits) -> None:
    executor, _, decisions = setup(tmp_path, limits)
    ctx = make_ctx(modes={}, present={(EVCS, CommandKind.CURRENT): 16.0})
    inputs = {"battery.soc_pct": 46.0}
    verdict = executor.execute(SAFE, ctx, snap({}), S(5), inputs)
    inputs["battery.soc_pct"] = 99.0  # die Entscheidung behält ihre eigenen Eingangsgrößen
    assert verdict.sendable and not verdict.send
    (decision,) = decisions
    assert (decision.result, decision.dry_run, decision.outcome) == (
        DecisionResult.DRY_RUN,
        True,
        "accepted",
    )
    assert (decision.requested, decision.applied, decision.ts) == (6.0, 6.0, S(5))
    assert decision.inputs == {"battery.soc_pct": 46.0}
    assert decision.text == "EVCS „evcs“ (sicherer Zustand): Strom 6 A – Dry-Run, nicht gesendet."
    assert executor.pending() == frozenset() and executor.take_failures() == []


def test_dry_run_is_logged_even_if_the_actuator_is_not_controllable(tmp_path, limits) -> None:
    executor, fake, decisions = setup(tmp_path, limits, controllable=False)
    ctx = make_ctx(modes={}, present={(EVCS, CommandKind.CURRENT): 16.0})
    executor.execute(SAFE, ctx, snap({}), T0, {})
    assert fake.sent == [] and [d.result for d in decisions] == [DecisionResult.DRY_RUN]
    assert executor.take_failures() == []


def test_rejected_decision_carries_findings_and_reasons(tmp_path, limits_live) -> None:
    executor, fake, decisions = setup(tmp_path, limits_live)
    verdict = executor.execute(SAFE, make_ctx(locks={EVCS: LOCKED}), snap({}), S(7), {"x": 1.0})
    assert verdict.outcome == Outcome.REJECTED
    (decision,) = decisions
    assert (decision.requested, decision.applied, decision.dry_run) == (6.0, None, False)
    assert (decision.outcome, decision.result, decision.ts) == (
        "rejected",
        DecisionResult.REJECTED,
        S(7),
    )
    assert decision.codes == ("safe_state", "lock")
    assert decision.inputs == {"x": 1.0}
    assert decision.text == (
        "EVCS „evcs“ (sicherer Zustand): Strom 6 A – abgelehnt. "
        "Gesperrt: EVCS nicht im manuellen Modus."
    )
    assert fake.sent == [] and executor.pending() == frozenset()
    assert executor.take_failures() == []  # eine Ablehnung ist kein Fehler beim Schreiben


def test_rejection_of_an_unknown_actuator_is_logged_under_its_id(tmp_path, limits_live) -> None:
    executor, _, decisions = setup(tmp_path, limits_live)
    executor.execute(Command("wallbox.twc", CommandKind.CURRENT, 10.0), LIVE_CTX, snap({}), T0, {})
    assert decisions[0].text == (
        "wallbox.twc: Strom 10 A – abgelehnt. Unbekannter Aktor „wallbox.twc“."
    )


def test_rejections_differing_in_actuator_kind_or_codes_are_logged_separately(
    tmp_path, limits_live
) -> None:
    executor, _, decisions = setup(tmp_path, limits_live)
    locks = {EVCS: LOCKED, VICTRON: ("Mitregelndes System.",), "vehicle.tesla": ("Gesperrt.",)}
    locked = make_ctx(locks=locks)
    observing = replace(locked, operating=OperatingState.OBSERVE)
    series = [
        (0, SAFE, locked),  # protokolliert
        (1, STOP, locked),  # andere Art
        (2, SAFE, observing),  # andere Befund-Codes (state.inactive statt lock)
        (3, RELEASE, locked),  # anderer Aktor und andere Art
        (3, TESLA_SAFE, locked),  # anderer Aktor bei gleicher Art und gleichen Codes
        (4, SAFE, locked),  # wie um 0 s: noch keine 60 s
        (59, SAFE, locked),
        (60, SAFE, locked),  # genau 60 s nach der letzten gleichen Ablehnung
    ]
    for seconds, command, ctx in series:
        executor.execute(command, replace(ctx, now=S(seconds)), snap({}), T0, {})
    assert [(d.actuator, d.kind, d.codes) for d in decisions] == [
        (EVCS, CommandKind.CURRENT, ("safe_state", "lock")),
        (EVCS, CommandKind.CHARGING, ("lock",)),
        (EVCS, CommandKind.CURRENT, ("safe_state", "state.inactive")),
        (VICTRON, CommandKind.GRID_SETPOINT, ("lock",)),
        ("vehicle.tesla", CommandKind.CURRENT, ("lock",)),
        (EVCS, CommandKind.CURRENT, ("safe_state", "lock")),
    ]


def test_rejection_interval_runs_on_the_safety_clock(tmp_path, limits_live) -> None:
    executor, _, decisions = setup(tmp_path, limits_live)
    locked = make_ctx(locks={EVCS: LOCKED})
    for seconds in (0, 10, 20):  # die Wanduhr springt, die Safety-Uhr nicht
        wall = T0 + timedelta(hours=seconds)
        executor.execute(SAFE, replace(locked, now=S(seconds)), snap({}), wall, {})
    assert len(decisions) == 1


def test_unchanged_and_deferred_verdicts_are_not_logged(tmp_path, limits_live) -> None:
    executor, fake, decisions = setup(tmp_path, limits_live)
    unchanged = executor.execute(
        Command(EVCS, CommandKind.CURRENT, 16.0), LIVE_CTX, snap({}), T0, {}
    )
    assert unchanged.outcome == Outcome.UNCHANGED
    first = executor.execute(Command(EVCS, CommandKind.CURRENT, 12.0), LIVE_CTX, snap({}), T0, {})
    assert first.send
    fake.echo[CommandKind.CURRENT] = 12.0
    executor.poll(snap({}), S(1), T0)
    soon = make_ctx(now=S(2), present={(EVCS, CommandKind.CURRENT): 12.0})
    deferred = executor.execute(Command(EVCS, CommandKind.CURRENT, 8.0), soon, snap({}), T0, {})
    assert deferred.outcome == Outcome.DEFERRED
    assert [d.result for d in decisions] == [DecisionResult.OK]  # nur der erste Befehl
    assert fake.sent == [(CommandKind.CURRENT, 12.0)] and executor.pending() == frozenset()
    assert executor.take_failures() == []


def test_pending_writes_are_independent_per_actuator_and_kind(tmp_path, limits_live) -> None:
    evcs, victron = FakeActuator(EVCS), FakeActuator(VICTRON)
    executor, _ = build(tmp_path, limits_live, evcs, victron)
    ctx = make_ctx(
        present={(EVCS, CommandKind.CURRENT): 16.0, (VICTRON, CommandKind.GRID_SETPOINT): 500.0}
    )
    for command in (SAFE, STOP, RELEASE):
        assert executor.execute(command, ctx, snap({}), T0, {}) is not None
    assert executor.pending() == {
        (EVCS, CommandKind.CURRENT),
        (EVCS, CommandKind.CHARGING),
        (VICTRON, CommandKind.GRID_SETPOINT),
    }
    assert evcs.sent == [(CommandKind.CURRENT, 6.0), (CommandKind.CHARGING, False)]
    assert victron.sent == [(CommandKind.GRID_SETPOINT, None)]


def test_poll_completes_only_confirmed_writes_and_frees_their_key(tmp_path, limits_live) -> None:
    evcs, victron = FakeActuator(EVCS), FakeActuator(VICTRON)
    executor, decisions = build(tmp_path, limits_live, evcs, victron)
    ctx = make_ctx(
        present={(EVCS, CommandKind.CURRENT): 16.0, (VICTRON, CommandKind.GRID_SETPOINT): 500.0}
    )
    executor.execute(SAFE, ctx, snap({}), T0, {})
    executor.execute(RELEASE, ctx, snap({}), T0, {})
    victron.echo[CommandKind.GRID_SETPOINT] = None  # kein Override: bestätigt
    results = executor.poll(snap({}), S(1), T0)
    assert [(r.actuator, r.result) for r in results] == [(VICTRON, DecisionResult.OK)]
    assert executor.pending() == {(EVCS, CommandKind.CURRENT)}
    assert (
        decisions[0].text
        == "Victron (ESS): Netz-Sollwert-Override aufheben – gesendet und bestätigt."
    )
    # Der Schlüssel ist frei: derselbe Aktor und dieselbe Art werden wieder betrachtet.
    again = replace(ctx, now=S(3))
    assert executor.execute(RELEASE, again, snap({}), S(3), {}) is not None
    assert len(victron.sent) == 2


def test_each_write_has_its_own_deadline(tmp_path, limits_live) -> None:
    executor, _, _ = setup(tmp_path, limits_live)
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})  # Frist 10 s
    executor.execute(STOP, replace(LIVE_CTX, now=S(4)), snap({}), T0, {})  # Frist 14 s
    assert [r.kind for r in executor.poll(snap({}), S(10), T0)] == [CommandKind.CURRENT]
    assert executor.pending() == {(EVCS, CommandKind.CHARGING)}
    assert executor.poll(snap({}), S(13), T0) == []
    assert [r.kind for r in executor.poll(snap({}), S(14), T0)] == [CommandKind.CHARGING]
    assert executor.pending() == frozenset()


def test_deadline_follows_the_confirm_timeout_of_the_actuator(tmp_path, limits_live) -> None:
    executor, _, _ = setup(tmp_path, limits_live, confirm_timeout_s=75.0)
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    assert executor.poll(snap({}), S(74), T0) == []
    assert [r.result for r in executor.poll(snap({}), S(75), T0)] == [DecisionResult.TIMEOUT]


def test_deadline_runs_on_the_safety_clock_and_the_decision_on_the_wall_clock(
    tmp_path, limits_live
) -> None:
    executor, _, decisions = setup(tmp_path, limits_live)
    wall = T0 + timedelta(hours=5)  # Wanduhr und Safety-Uhr stehen weit auseinander
    executor.execute(SAFE, LIVE_CTX, snap({}), wall, {})
    assert executor.poll(snap({}), S(9), wall + timedelta(seconds=9)) == []
    results = executor.poll(snap({}), S(10), wall + timedelta(seconds=10))
    assert [r.result for r in results] == [DecisionResult.TIMEOUT]
    assert decisions[0].ts == wall + timedelta(seconds=10)


def test_value_arriving_exactly_at_the_deadline_counts_as_confirmed(tmp_path, limits_live) -> None:
    executor, fake, decisions = setup(tmp_path, limits_live)
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    fake.echo[CommandKind.CURRENT] = 6.0
    results = executor.poll(snap({}), S(10), T0)  # genau die Frist: erst fragen, dann Zeit prüfen
    assert [r.result for r in results] == [DecisionResult.OK]
    assert [d.result for d in decisions] == [DecisionResult.OK]


def test_timeout_names_the_last_pending_text_but_not_in_the_decision(
    tmp_path, limits_live, monkeypatch
) -> None:
    executor, fake, decisions = setup(tmp_path, limits_live)
    monkeypatch.setattr(
        fake, "confirm", lambda kind, value, snapshot: (Confirmation.PENDING, "Zurückgelesen: 16 A")
    )
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    (result,) = executor.poll(snap({}), S(10), T0)
    assert (result.result, result.detail) == (DecisionResult.TIMEOUT, "Zurückgelesen: 16 A")
    assert decisions[0].text == (
        "EVCS „evcs“ (sicherer Zustand): Strom 6 A – nicht bestätigt (Zeitüberschreitung)."
    )


def test_failed_confirmation_is_an_error_with_the_actuator_text(
    tmp_path, limits_live, monkeypatch
) -> None:
    executor, fake, decisions = setup(tmp_path, limits_live)
    monkeypatch.setattr(
        fake,
        "confirm",
        lambda kind, value, snapshot: (Confirmation.FAILED, "Wallbox meldet Fehler 7."),
    )
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    results = executor.poll(snap({}), S(1), T0)  # die Frist läuft noch: gescheitert zählt sofort
    assert results == [
        WriteResult(
            EVCS, CommandKind.CURRENT, 6.0, DecisionResult.ERROR, "Wallbox meldet Fehler 7."
        )
    ]
    assert decisions[0].text == (
        "EVCS „evcs“ (sicherer Zustand): Strom 6 A – Fehler: Wallbox meldet Fehler 7."
    )
    assert executor.pending() == frozenset()
    assert executor.poll(snap({}), S(20), T0) == [] and len(decisions) == 1
    assert executor.take_failures() == results


def test_a_value_the_device_did_not_take_is_an_error(tmp_path, limits_live, monkeypatch) -> None:
    # Tesla über Tessie: bestätigt, aber nicht übernommen. Ein neuer Versuch ist ein neuer Befehl
    # durch die Safety-Schicht, etwa der nächste Versuch für den sicheren Zustand.
    executor, fake, decisions = setup(tmp_path, limits_live)
    text = "Gerät übernahm 6 A nicht."
    monkeypatch.setattr(
        fake, "confirm", lambda kind, value, snapshot: (Confirmation.NOT_TAKEN, text)
    )
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    results = executor.poll(snap({}), S(1), T0)
    assert results == [WriteResult(EVCS, CommandKind.CURRENT, 6.0, DecisionResult.ERROR, text)]
    assert decisions[0].text == f"EVCS „evcs“ (sicherer Zustand): Strom 6 A – Fehler: {text}"
    assert executor.pending() == frozenset()
    assert executor.take_failures() == results


def test_confirm_gets_the_sent_value_and_the_current_snapshot(
    tmp_path, limits_live, monkeypatch
) -> None:
    executor, fake, _ = setup(tmp_path, limits_live)
    asked = []

    def confirm(kind, value, snapshot):
        asked.append((kind, value, snapshot))
        return Confirmation.PENDING, ""

    monkeypatch.setattr(fake, "confirm", confirm)
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    later = snap({"evcs.set_current_a": 6.0})
    executor.poll(later, S(1), T0)
    assert len(asked) == 1 and asked[0][:2] == (CommandKind.CURRENT, 6.0) and asked[0][2] is later


def test_capabilities_are_asked_with_snapshot_and_safety_time(
    tmp_path, limits_live, monkeypatch
) -> None:
    executor, fake, _ = setup(tmp_path, limits_live)
    asked = []

    def capabilities(snapshot, now):
        asked.append((snapshot, now))
        return Capabilities(True)

    monkeypatch.setattr(fake, "capabilities", capabilities)
    current = snap({"cerbo.connected": True})
    executor.execute(SAFE, replace(LIVE_CTX, now=S(3)), current, S(100), {})
    assert len(asked) == 1 and asked[0][0] is current and asked[0][1] == S(3)


def test_failures_come_in_order_and_only_failures(tmp_path, limits_live) -> None:
    executor, fake, _ = setup(tmp_path, limits_live)
    fake.controllable = False
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})  # nicht gesendet
    fake.controllable = True
    executor.execute(STOP, LIVE_CTX, snap({}), T0, {})
    fake.echo[CommandKind.CHARGING] = False
    executor.poll(snap({}), S(1), T0)  # bestätigt: kein Fehler
    executor.execute(SAFE, replace(LIVE_CTX, now=S(6)), snap({}), S(6), {})
    executor.poll(snap({}), S(16), S(16))  # Strom nie zurückgelesen: Zeitüberschreitung
    failures = executor.take_failures()
    assert [(f.kind, f.result) for f in failures] == [
        (CommandKind.CURRENT, DecisionResult.NOT_SENT),
        (CommandKind.CURRENT, DecisionResult.TIMEOUT),
    ]
    assert executor.take_failures() == []


def test_not_sent_results_name_the_reason(tmp_path, limits_live) -> None:
    executor, _, decisions = setup(tmp_path, limits_live, controllable=False)
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    (failure,) = executor.take_failures()
    assert failure == WriteResult(
        EVCS,
        CommandKind.CURRENT,
        6.0,
        DecisionResult.NOT_SENT,
        "nicht steuerbar: Cerbo nicht verbunden.",
    )
    assert decisions[0].text == (
        "EVCS „evcs“ (sicherer Zustand): Strom 6 A – "
        "nicht gesendet: nicht steuerbar: Cerbo nicht verbunden."
    )


def test_refusal_text_is_the_not_sent_reason(tmp_path, limits_live) -> None:
    reason = "Cerbo-Verbindung: Auftrag nicht angenommen."
    executor, _, decisions = setup(tmp_path, limits_live, refuse=reason)
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    assert [f.detail for f in executor.take_failures()] == [reason]
    assert decisions[0].text == (
        f"EVCS „evcs“ (sicherer Zustand): Strom 6 A – nicht gesendet: {reason}"
    )


def test_not_controllable_without_reasons_has_no_dangling_colon(
    tmp_path, limits_live, monkeypatch
) -> None:
    executor, fake, decisions = setup(tmp_path, limits_live)
    monkeypatch.setattr(fake, "capabilities", lambda snapshot, now: Capabilities(False))
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    assert decisions[0].text.endswith("– nicht gesendet: nicht steuerbar.")


def test_actuator_without_driver_is_not_sent(tmp_path, limits_live) -> None:
    executor, _, decisions = setup(tmp_path, limits_live)
    command = Command("vehicle.tesla", CommandKind.CHARGING, False, urgent=True)
    verdict = executor.execute(command, LIVE_CTX, snap({}), T0, {})
    assert verdict.send  # die Safety-Schicht hätte es erlaubt, der Ausführer kennt den Aktor nicht
    (decision,) = decisions
    assert decision.result == DecisionResult.NOT_SENT
    assert decision.text == (
        "vehicle.tesla: Laden aus – nicht gesendet: nicht steuerbar: Aktor nicht eingerichtet."
    )
    assert executor.pending() == frozenset()
    assert [f.result for f in executor.take_failures()] == [DecisionResult.NOT_SENT]
