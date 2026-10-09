"""Befehlszeile der app: ``python -m bindaems.app <befehl>``."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

import httpx

from bindaems.app.audit import AuditLog
from bindaems.app.auth.service import AuthError, AuthService, UserNotFoundError
from bindaems.app.db.engine import DB_FILENAME, migrate, open_database
from bindaems.app.prices.verify import check_prices
from bindaems.shared.config import Config, ConfigError, load_config
from bindaems.shared.timeutil import SystemClock


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=Path("/config/config.yaml"))
    parser = argparse.ArgumentParser(prog="bindaems-app", description="BindaEMS app")
    commands = parser.add_subparsers(dest="command", required=True)
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


async def _verify_prices(out_dir: Path) -> int:
    async with httpx.AsyncClient() as http:
        return await check_prices(http, SystemClock(), out_dir)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "verify-prices":
        return asyncio.run(_verify_prices(args.out))
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    return _accounts(args, cfg)
