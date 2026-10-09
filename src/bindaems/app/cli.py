"""Befehlszeile der app: ``python -m bindaems.app <befehl>`` (ohne Befehl: ``serve``)."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import signal
import sys
from pathlib import Path

import httpx
import structlog
from pydantic import ValidationError

from bindaems.app.audit import AuditLog
from bindaems.app.auth.service import AuthError, AuthService, UserNotFoundError
from bindaems.app.db.engine import DB_FILENAME, migrate, open_database
from bindaems.app.prices.verify import check_prices
from bindaems.app.runtime import AppRuntime
from bindaems.shared.config import Config, ConfigError, Secrets, load_config, load_secrets
from bindaems.shared.logging import configure_logging
from bindaems.shared.timeutil import SystemClock

log = structlog.get_logger("bindaems.app")

COMMANDS = ("serve", "create-admin", "set-password", "verify-prices")


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=Path("/config/config.yaml"))
    parser = argparse.ArgumentParser(prog="bindaems-app", description="BindaEMS app")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", parents=[common], help="app starten (Standard)")
    serve.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    for name, text in (
        ("create-admin", "einen Admin anlegen (erster Zugang)"),
        ("set-password", "das Passwort eines Benutzers setzen"),
    ):
        command = commands.add_parser(name, parents=[common], help=text)
        command.add_argument("--username", required=True)
        command.add_argument(
            "--password-stdin", action="store_true", help="Passwort aus der ersten Zeile von stdin"
        )
    verify = commands.add_parser(
        "verify-prices", help="Prüfprotokoll „Preise“ schreiben (ohne Konfiguration)"
    )
    verify.add_argument("--out", type=Path, required=True, help="Zielverzeichnis")
    return parser


def _read_password(from_stdin: bool) -> str | None:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("Passwort: ")
    if getpass.getpass("Passwort wiederholen: ") != first:
        print("Passwörter stimmen nicht überein", file=sys.stderr)
        return None
    return first


def _auth_service(cfg: Config) -> AuthService:
    engine = open_database(cfg.app.data_dir / DB_FILENAME)
    migrate(engine)
    clock = SystemClock()
    return AuthService(engine, clock, AuditLog(engine, clock))


def _accounts(args: argparse.Namespace, cfg: Config) -> int:
    password = _read_password(args.password_stdin)
    if password is None:
        return 1
    auth = _auth_service(cfg)
    try:
        if args.command == "create-admin":
            user = auth.create_user(args.username, password, "admin", actor="cli", source="cli")
            print(f"Admin „{user.username}“ angelegt.")
            return 0
        name = args.username.strip().lower()
        match = [user for user in auth.list_users() if user.username == name]
        if not match:
            raise UserNotFoundError()
        auth.set_password(match[0].id, password, actor="cli", source="cli")
        print(f"Passwort für „{name}“ geändert.")
        return 0
    except AuthError as exc:
        print(exc, file=sys.stderr)
        return 1


def _secrets_error(exc: ValidationError) -> str:
    """Fehlertext ohne Eingabewerte – diese könnten Secrets enthalten."""
    details = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors(include_input=False, include_url=False)
    )
    return f"Secrets ungültig: {details}"


async def _serve(cfg: Config, secrets: Secrets) -> int:
    runtime = AppRuntime(cfg, secrets)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    run_task = asyncio.create_task(runtime.run(), name="app")
    stop_task = asyncio.create_task(stop.wait(), name="signal")
    log.info("app gestartet", host=cfg.app.host, port=cfg.app.port)
    await asyncio.wait({run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    if not stop_task.done():
        stop_task.cancel()
        error = run_task.exception() if not run_task.cancelled() else None
        log.error("app unerwartet beendet", error=repr(error))
        await runtime.shutdown()
        return 1
    log.info("Signal empfangen, app fährt geordnet herunter")
    await runtime.shutdown()
    await run_task
    return 0


async def _verify_prices(out_dir: Path) -> int:
    async with httpx.AsyncClient() as http:
        return await check_prices(http, SystemClock(), out_dir)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in (*COMMANDS, "-h", "--help"):
        arguments.insert(0, "serve")
    args = _parser().parse_args(arguments)
    if args.command == "verify-prices":
        return asyncio.run(_verify_prices(args.out))
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.command != "serve":
        return _accounts(args, cfg)
    try:
        secrets = load_secrets()
    except ValidationError as exc:
        print(_secrets_error(exc), file=sys.stderr)
        return 2
    configure_logging(args.log_level)
    return asyncio.run(_serve(cfg, secrets))
