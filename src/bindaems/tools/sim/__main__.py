"""Aufruf des Simulators von Hand (Design 13.4).

``python -m bindaems.tools.sim --szenario NAME --out PATH [--config PFAD] [--stunden H]`` lässt
ein Szenario aus ``SCENARIOS`` laufen, ``--stunden`` statt seiner Dauer, aber nie länger: Seine
Ereignisse und Prüfungen enden mit ihr. Die Konfiguration ist ohne Angabe die
Beispielkonfiguration; die Szenarien brauchen ihre Wallboxen ``evcs`` und ``twc`` und ihre
Fahrzeuge ``tesla`` und ``egolf``.

Jeder Lauf bekommt ein frisches Verzeichnis für die Ablage des core: In einem gebrauchten gälten
Absicht, Rate-Limits und Schreibbudget eines früheren Laufs weiter, und seine Entscheidungen kämen
zu denen des neuen. Auf die Standardausgabe kommt je Kennzahl eine Zeile, dann je Prüfung des
Szenarios eine, mit ihren Verstößen als Befund, wenn sie fehlschlägt; der Bericht
(``report.render_report``) geht nach ``--out``. Geloggt wird erst ab WARNING.

Rückgabe: 0, wenn alle Kennzahlen und Prüfungen bestehen, sonst 1; 2 bei unbekanntem Szenario,
einer Konfiguration, die fehlt, ungültig ist oder die Geräte der Szenarien nicht hat, und einem
Bericht, der sich nicht schreiben lässt.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
import tempfile
from pathlib import Path
from typing import Final

from bindaems.shared.config import Config, ConfigError, EvcsConfig, TwcConfig, load_config
from bindaems.shared.logging import configure_logging
from bindaems.tools.sim.report import FAILED, OK, check_lines, render_report
from bindaems.tools.sim.runner import run_scenario
from bindaems.tools.sim.scenarios import (
    EGOLF,
    EVCS_BOX,
    HOUR_S,
    SCENARIOS,
    TESLA,
    WALL_CONNECTOR,
)

DEFAULT_CONFIG: Final = Path("deploy/config.example.yaml")
DEVICES_MISSING: Final = (
    "Die Szenarien brauchen die Wallboxen „evcs“ (EVCS) und „twc“ (Wall Connector) und die "
    "Fahrzeuge „tesla“ (mit Tessie) und „egolf“."
)


def _hours(text: str) -> float:
    """Eine endliche Dauer in Stunden über 0 (für ``--stunden``)."""
    try:
        hours = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"keine Zahl: {text}") from None
    if not (math.isfinite(hours) and hours > 0):
        raise argparse.ArgumentTypeError(f"muss endlich sein und über 0 liegen: {text}")
    return hours


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bindaems.tools.sim",
        description="Simulator der Laderegelung: ein Szenario laufen lassen und berichten",
    )
    parser.add_argument("--szenario", required=True, help=f"eines von: {', '.join(SCENARIOS)}")
    parser.add_argument("--out", type=Path, required=True, help="Bericht (Markdown)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--stunden",
        type=_hours,
        help="Dauer in Stunden statt der des Szenarios, höchstens diese; mehr lässt es ganz laufen",
    )
    args = parser.parse_args(argv)
    scenario = SCENARIOS.get(args.szenario)
    if scenario is None:
        known = ", ".join(SCENARIOS)
        print(f"Unbekanntes Szenario „{args.szenario}“; bekannt sind {known}.", file=sys.stderr)
        return 2
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not _has_the_devices(cfg):
        print(DEVICES_MISSING, file=sys.stderr)
        return 2
    out: Path = args.out
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"Bericht nicht geschrieben: {exc}", file=sys.stderr)
        return 2
    duration_s = None  # ohne --stunden die Dauer des Szenarios
    if args.stunden is not None:
        duration_s = min(scenario.duration_s, max(1, round(args.stunden * HOUR_S)))
    configure_logging("WARNING")
    with tempfile.TemporaryDirectory(prefix="bindaems-sim-") as data_dir:
        result = asyncio.run(run_scenario(scenario, cfg, Path(data_dir), duration_s=duration_s))
    for figure in result.metrics.figures(cfg):
        rating = OK if figure.ok else FAILED
        print(f"{figure.name}: {figure.value} (Grenze {figure.limit}) – {rating}")
    for line in check_lines(result):
        finding = "" if line.ok else f" Befund: {line.finding}"
        print(f"Szenario-Prüfung: {line.rating} – {line.text}{finding}")
    try:
        out.write_text(render_report(result, str(args.config)), encoding="utf-8")
    except OSError as exc:
        print(f"Bericht nicht geschrieben: {exc}", file=sys.stderr)
        return 2
    print(f"Bericht: {out}")
    return 0 if not result.failures else 1


def _has_the_devices(cfg: Config) -> bool:
    """Hat die Konfiguration die Wallboxen und Fahrzeuge, die die Szenarien nennen?"""
    wallboxes, vehicles = cfg.wallboxes, cfg.vehicles
    tesla = vehicles.get(TESLA)
    return (
        isinstance(wallboxes.get(EVCS_BOX), EvcsConfig)
        and isinstance(wallboxes.get(WALL_CONNECTOR), TwcConfig)
        and tesla is not None
        and tesla.tessie is not None
        and EGOLF in vehicles
    )


if __name__ == "__main__":
    sys.exit(main())
