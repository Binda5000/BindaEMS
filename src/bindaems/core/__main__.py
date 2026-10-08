"""Einstiegspunkt: ``python -m bindaems.core --config /config/config.yaml``."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

import structlog
from pydantic import ValidationError

from bindaems.core.logging import configure_logging
from bindaems.core.runtime import CoreRuntime
from bindaems.shared.config import Config, ConfigError, Secrets, load_config, load_secrets

log = structlog.get_logger("bindaems.core")


def _secrets_error(exc: ValidationError) -> str:
    """Fehlertext ohne Eingabewerte – diese könnten Secrets enthalten."""
    details = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors(include_input=False, include_url=False)
    )
    return f"Secrets ungültig: {details}"


async def _run(cfg: Config, secrets: Secrets) -> int:
    runtime = CoreRuntime(cfg, secrets)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    run_task = asyncio.create_task(runtime.run(), name="core")
    stop_task = asyncio.create_task(stop.wait(), name="signal")
    log.info("core gestartet", mode="OBSERVE")
    await asyncio.wait({run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    if not stop_task.done():
        stop_task.cancel()
        error = run_task.exception() if not run_task.cancelled() else None
        log.error("core unerwartet beendet", error=repr(error))
        await runtime.shutdown()
        return 1
    log.info("Signal empfangen, core fährt geordnet herunter")
    await runtime.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bindaems-core", description="BindaEMS core (Phase 1a: nur beobachten)"
    )
    parser.add_argument("--config", type=Path, default=Path("/config/config.yaml"))
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        secrets = load_secrets()
    except ValidationError as exc:
        print(_secrets_error(exc), file=sys.stderr)
        return 2
    configure_logging(args.log_level)
    return asyncio.run(_run(cfg, secrets))


if __name__ == "__main__":
    sys.exit(main())
