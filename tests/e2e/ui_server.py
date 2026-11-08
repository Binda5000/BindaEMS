"""Demo-Backend für das UI: die app mit der Demo-Welt, ohne Geräte und ohne Internet.

Start aus dem Repository-Wurzelverzeichnis (sonst fehlt ``tests`` im Importpfad):

    uv run python -m tests.e2e.ui_server [--host 127.0.0.1] [--port 8099] [--ui-dir ui/build]
        [--data-dir <Verzeichnis>]

Ohne ``--data-dir`` beginnt jeder Start mit einem neuen Temp-Verzeichnis (frischer Stand).
Die Uhr steht auf dem 09.10.2026, 10:00 Ortszeit; die Messwerte schwanken jede Sekunde leicht.
Die Unwetter-Reserve ist an, mit einer Gewitterwarnung (orange) von 18:00 bis 21:00 Uhr.
PV-Anlagen und Strings, die der Browser anlegt, bekommen nach wenigen Sekunden Werte (Leistung,
Energie heute und die letzten Tage).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import tempfile
import traceback
from pathlib import Path

import uvicorn
from tests.ui_world import (
    DEMO_PASSWORD,
    World,
    build_world,
    demo_storm,
    demo_thermal_stage,
    refresh_pv,
)

from bindaems.shared.config import load_config

CONFIG = Path("deploy/config.example.yaml")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Demo-Backend für das BindaEMS-UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--ui-dir", type=Path, default=None, help="gebautes UI, z. B. ui/build")
    parser.add_argument("--data-dir", type=Path, default=None)
    return parser.parse_args(argv)


async def _tick(world: World) -> None:
    tick = 0
    while True:
        await asyncio.sleep(1.0)
        tick += 1
        world.step_live(tick)


async def _pv(world: World) -> None:
    """Wie die Aufgaben der app: HA-Werte, Energie und Tage der PV-Einträge, die der Browser
    anlegt, alle 5 s nachführen."""
    while True:
        await asyncio.sleep(5.0)
        try:
            await refresh_pv(world.app)
        except Exception:  # die Demo läuft weiter; der Fehler steht im Log
            traceback.print_exc()


async def serve(args: argparse.Namespace) -> None:
    data_dir = args.data_dir or Path(tempfile.mkdtemp(prefix="bindaems-demo-"))
    world = await build_world(load_config(CONFIG), data_dir, ui_dir=args.ui_dir)
    print(
        f"Demo-Backend auf http://{args.host}:{args.port} – Benutzer admin, sicher (TOTP) "
        f"und gast, Passwort {DEMO_PASSWORD}",
        flush=True,
    )
    # vor dem Planungsdienst: schon sein erster Lauf kennt die Warnung
    await demo_storm(world.app)
    ticker = asyncio.create_task(_tick(world))
    pv = asyncio.create_task(_pv(world))
    # Der Planungsdienst läuft wie in der app (lifespan ist aus): „Neu planen“ geht durch ihn.
    # Seine Pläne zeigen wie der erste die Vorschau von Vorheizen und Vorkühlen.
    with demo_thermal_stage():
        planner = asyncio.create_task(world.app.planning.run())
        try:
            config = uvicorn.Config(
                world.app.app, host=args.host, port=args.port, log_level="warning", lifespan="off"
            )
            await uvicorn.Server(config).serve()
        finally:
            for task in (planner, ticker, pv):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            world.close()


def main() -> None:
    asyncio.run(serve(parse_args()))


if __name__ == "__main__":
    main()
