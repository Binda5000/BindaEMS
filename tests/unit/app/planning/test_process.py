"""Tests des Lösens im Kindprozess mit äußerem Zeitlimit (Plan 3b, Task 9)."""

from __future__ import annotations

import asyncio
import multiprocessing
import time
from multiprocessing.connection import Connection

import pytest
from tests.plan_helpers import scenario

from bindaems.app.planning import process
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.process import child_main, solve_in_subprocess, solve_inline


def small_inputs() -> PlanInputs:
    return scenario(prices=[10.0, 20.0, 30.0, 40.0], pv=[0.0] * 4, load=[500.0] * 4)


def sleep_forever(conn: Connection, text: str) -> None:
    """Kind, das nie antwortet (Modulebene, damit ``spawn`` es importieren kann)."""
    time.sleep(3600)


def exit_silently(conn: Connection, text: str) -> None:
    """Kind, das ohne Antwort endet."""
    conn.close()


def send_garbage(conn: Connection, text: str) -> None:
    """Kind, das kein ``OptimizerResult`` schickt."""
    conn.send("kein Ergebnis")
    conn.close()


def broken_solve(inputs: PlanInputs) -> None:
    raise RuntimeError("HiGHS abgestürzt")


def solve_raises(conn: Connection, text: str) -> None:
    """Kind, dessen Solver eine Ausnahme wirft (der Patch gilt nur im Kindprozess)."""
    process.solve = broken_solve  # type: ignore[assignment]
    child_main(conn, text)


def no_live_children() -> bool:
    return not [p for p in multiprocessing.active_children() if p.is_alive()]


async def test_subprocess_solves() -> None:
    result = await solve_in_subprocess(small_inputs())
    assert result.status == "optimal" and len(result.flows) == 4
    assert no_live_children()


async def test_subprocess_matches_inline() -> None:
    inputs = small_inputs()
    child = await solve_in_subprocess(inputs)
    inline = await solve_inline(inputs)
    assert child.status == inline.status
    assert child.objective_ct == inline.objective_ct


async def test_timeout_kills_child() -> None:
    started = time.monotonic()
    result = await solve_in_subprocess(small_inputs(), timeout_s=0.5, target=sleep_forever)
    assert result.status == "timeout" and time.monotonic() - started < 5
    assert result.flows == () and result.message is not None
    assert no_live_children()


async def test_cancelled_run_kills_child() -> None:
    task = asyncio.create_task(solve_in_subprocess(small_inputs(), target=sleep_forever))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(50):  # das Aufräumen läuft abgeschirmt weiter
        if no_live_children():
            break
        await asyncio.sleep(0.1)
    assert no_live_children()


async def test_next_run_after_timeout_works() -> None:
    await solve_in_subprocess(small_inputs(), timeout_s=0.5, target=sleep_forever)
    assert (await solve_in_subprocess(small_inputs())).status == "optimal"


async def test_child_without_answer_is_error() -> None:
    result = await solve_in_subprocess(small_inputs(), target=exit_silently)
    assert result.status == "error" and result.flows == ()
    assert result.message is not None and "ohne Ergebnis" in result.message
    assert no_live_children()


async def test_child_with_wrong_answer_is_error() -> None:
    result = await solve_in_subprocess(small_inputs(), target=send_garbage)
    assert result.status == "error" and result.flows == ()
    assert no_live_children()


async def test_inline_solves() -> None:
    result = await solve_inline(small_inputs())
    assert result.status == "optimal" and len(result.flows) == 4


def test_child_main_sends_result() -> None:
    parent, child = multiprocessing.Pipe(duplex=False)
    child_main(child, small_inputs().model_dump_json())
    result = parent.recv()
    parent.close()
    assert result.status == "optimal" and len(result.flows) == 4


def test_child_main_reports_invalid_inputs() -> None:
    parent, child = multiprocessing.Pipe(duplex=False)
    child_main(child, "{}")
    result = parent.recv()
    parent.close()
    assert result.status == "error" and result.flows == () and result.message


def test_child_main_reports_solver_exception(monkeypatch) -> None:
    monkeypatch.setattr(process, "solve", broken_solve)
    parent, child = multiprocessing.Pipe(duplex=False)
    child_main(child, small_inputs().model_dump_json())
    result = parent.recv()
    parent.close()
    assert (result.status, result.message, result.flows) == ("error", "HiGHS abgestürzt", ())


async def test_solver_exception_in_child_gives_its_message() -> None:
    result = await solve_in_subprocess(small_inputs(), target=solve_raises)
    assert (result.status, result.message) == ("error", "HiGHS abgestürzt")
    assert no_live_children()
