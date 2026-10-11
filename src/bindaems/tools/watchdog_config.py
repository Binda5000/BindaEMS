"""Erzeugt die ``config.ini`` des Cerbo-Watchdogs aus ``config.yaml`` (Spec 8.6).

Aufruf::

    python -m bindaems.tools.watchdog_config --config /config/config.yaml > config.ini

Heartbeat-Takt, Timeout, USV-Reserve und sicherer EVCS-Strom stehen so nur in der ``config.yaml``.
Die Datei liest ``cerbo-watchdog/bindaems_watchdog/config.py`` auf dem Cerbo; das Format zeigt
``cerbo-watchdog/config.ini.example``. Der Watchdog schreibt nur, was in dieser Datei steht – das
EMS kann sie zur Laufzeit nicht ändern. Ohne ``--out`` geht der Text auf die Standardausgabe; eine
ungültige ``config.yaml`` ergibt die Meldung auf stderr und den Exitcode 2.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bindaems.shared.config import Config, ConfigError, EvcsConfig, load_config

REARM_S = 30  # so lange müssen die Heartbeats lückenlos laufen, bis der Watchdog wieder scharf ist
GAP_FACTOR = 2.5  # größter Abstand zweier Heartbeats, der noch als lückenlos gilt: 2,5 × Takt
RETRIES = 6
RETRY_S = 5
STATE_FILE = "/data/bindaems-watchdog/state.json"

_HEADER = [
    "; BindaEMS-Watchdog – erzeugt mit „python -m bindaems.tools.watchdog_config“ aus config.yaml.",
    "; Nicht von Hand ändern: config.yaml anpassen, neu erzeugen und mit install.sh übertragen.",
]


def render_config(cfg: Config) -> str:
    """Der Text der ``config.ini``: Zeiten, je EVCS ein Abschnitt, Min-SOC und Entladegrenze.

    Eine EVCS ohne passende ``evcharger``-Instanz in ``victron.instances.evcharger`` bekommt keinen
    Abschnitt, sondern eine Kommentarzeile: Der Watchdog findet den GX-Dienst nur über die
    Instanz.
    """
    watchdog = cfg.victron.watchdog
    blocks = [
        _HEADER,
        [
            "[watchdog]",
            "enabled = true",
            f"timeout_s = {watchdog.timeout_s:g}",
            f"rearm_s = {REARM_S:g}",
            f"heartbeat_gap_s = {GAP_FACTOR * watchdog.heartbeat_s:g}",
            f"retries = {RETRIES}",
            f"retry_s = {RETRY_S:g}",
            f"state_file = {STATE_FILE}",
        ],
        *_evcs_blocks(cfg),
        [
            "; Min-SOC auf die USV-Reserve (battery.reserve_soc_pct)",
            "[action:min_soc]",
            "service = com.victronenergy.settings",
            "path = /Settings/CGwacs/BatteryLife/MinimumSocLimit",
            f"value = {cfg.battery.reserve_soc_pct:g}",
        ],
        [
            "; Entladegrenze unbegrenzt",
            "[action:max_discharge]",
            "service = com.victronenergy.settings",
            "path = /Settings/CGwacs/MaxDischargePower",
            "value = -1",
        ],
    ]
    return "\n\n".join("\n".join(block) for block in blocks) + "\n"


def _evcs_blocks(cfg: Config) -> list[list[str]]:
    blocks = []
    for key, wallbox in cfg.wallboxes.items():
        if not isinstance(wallbox, EvcsConfig):
            continue
        instance = next(
            (i for i, name in cfg.victron.instances.evcharger.items() if name == key), None
        )
        if instance is None:
            blocks.append(
                [
                    f"; EVCS „{key}“: keine evcharger-Instanz in victron.instances.evcharger – "
                    "der Watchdog setzt keinen sicheren Strom"
                ]
            )
            continue
        blocks.append(
            [
                f"; EVCS „{key}“ auf den sicheren Strom (wallboxes.{key}.safe_a)",
                f"[action:evcs_{key}]",
                "service = com.victronenergy.evcharger",
                f"instance = {instance}",
                "path = /SetCurrent",
                f"value = {wallbox.safe_a:g}",
            ]
        )
    return blocks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="watchdog_config",
        description="config.ini des Cerbo-Watchdogs aus config.yaml erzeugen",
    )
    parser.add_argument("--config", type=Path, default=Path("/config/config.yaml"))
    parser.add_argument("--out", type=Path, help="Zieldatei (Standard: Standardausgabe)")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    text = render_config(cfg)
    if args.out is None:
        sys.stdout.write(text)
    else:
        args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
