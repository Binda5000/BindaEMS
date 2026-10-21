"""Lösen des Optimierers im eigenen Prozess mit äußerem Zeitlimit (Plan 3b, Präzisierung 13).

Je Lauf startet ein Kindprozess (``spawn``); die Eingangsdaten gehen als JSON-Text hinein, das
``OptimizerResult`` kommt über eine ``Pipe`` zurück. Antwortet das Kind nicht innerhalb des
Zeitlimits, wird es beendet und der Lauf meldet ``timeout``; endet es ohne Antwort, ``error``.
Ein hängender Solver kann so die app nicht blockieren, und kein Kind überlebt den Lauf.
"""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing
import time
from collections.abc import Awaitable, Callable
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Final

from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.optimizer import OptimizerResult, solve
from bindaems.shared.numbers import de
from bindaems.shared.plan import SolverStatus

Solver = Callable[[PlanInputs], Awaitable[OptimizerResult]]

OUTER_TIMEOUT_S: Final = 30.0
JOIN_S: Final = 2.0  # so lange darf das Kind nach ``terminate`` brauchen, dann ``kill``


def child_main(conn: Connection, inputs_json: str) -> None:
    """Einstieg des Kindprozesses: Eingangsdaten lesen, lösen, Ergebnis schicken."""
    started = time.perf_counter()
    try:
        try:
            inputs = PlanInputs.model_validate_json(inputs_json)
        except ValueError as exc:
            conn.send(_failure("error", started, f"Eingangsdaten ungültig: {exc}"))
            return
        try:
            result = solve(inputs)
        except Exception as exc:  # der Elternprozess meldet „Solver fehlgeschlagen: …“
            result = _failure("error", started, str(exc) or type(exc).__name__)
        conn.send(result)
    finally:
        conn.close()


def _failure(status: SolverStatus, started: float, message: str) -> OptimizerResult:
    return OptimizerResult(
        status=status,
        flows=(),
        peaks_kw={},
        objective_ct=None,
        solve_ms=round((time.perf_counter() - started) * 1000),
        message=message,
    )


def _stop(process: BaseProcess) -> None:
    """Beendet das Kind (erst ``terminate``, dann ``kill``) und räumt es ab."""
    if process.pid is None:  # nie gestartet
        return
    if process.is_alive():
        process.terminate()
        process.join(JOIN_S)
        if process.is_alive():
            process.kill()
    process.join()
    process.close()


async def solve_in_subprocess(
    inputs: PlanInputs,
    *,
    timeout_s: float = OUTER_TIMEOUT_S,
    target: Callable[[Connection, str], None] = child_main,
) -> OptimizerResult:
    """Löst ``inputs`` in einem Kindprozess; ``timeout`` nach ``timeout_s``, ``error``, wenn das
    Kind ohne Ergebnis endet. Das Kind ist danach in jedem Fall beendet."""
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=target,
        args=(sender, inputs.model_dump_json()),
        name="bindaems-optimizer",
        daemon=True,
    )
    started = time.perf_counter()
    # Auch bei Abbruch des Laufs (``CancelledError``) wird der Start abgewartet und das Kind
    # beendet: ``shield`` lässt Start und Aufräumen zu Ende laufen.
    start = asyncio.ensure_future(asyncio.to_thread(process.start))
    try:
        await asyncio.shield(start)
        sender.close()  # sonst sähe der Elternprozess das Ende des Kindes nicht (EOF)
        if not await asyncio.to_thread(receiver.poll, timeout_s):
            return _failure("timeout", started, f"keine Antwort nach {de(timeout_s)} s")
        try:
            result = await asyncio.to_thread(receiver.recv)
        except EOFError:
            code = await asyncio.to_thread(_exit_code, process)
            return _failure("error", started, f"Kindprozess ohne Ergebnis beendet (Code {code})")
        if not isinstance(result, OptimizerResult):
            return _failure("error", started, "Kindprozess lieferte kein Ergebnis des Optimierers")
        return result
    finally:
        try:
            await asyncio.shield(_cleanup(start, sender, process))
        finally:
            receiver.close()


async def _cleanup(start: asyncio.Future[None], sender: Connection, process: BaseProcess) -> None:
    with contextlib.suppress(Exception):  # ein gescheiterter Start kommt beim Aufrufer an
        await start
    sender.close()
    await asyncio.to_thread(_stop, process)


def _exit_code(process: BaseProcess) -> int | None:
    process.join(JOIN_S)
    return process.exitcode


async def solve_inline(inputs: PlanInputs) -> OptimizerResult:
    """Löst im selben Prozess in einem Worker-Thread (Tests, Demo, Szenarien)."""
    return await asyncio.to_thread(solve, inputs)
