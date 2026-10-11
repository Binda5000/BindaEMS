"""Erzeugt die ``config.ini`` des Cerbo-Watchdogs aus ``config.yaml`` (Spec 8.6).

Aufruf::

    python -m bindaems.tools.watchdog_config --config /config/config.yaml > config.ini

Heartbeat-Takt, Timeout, USV-Reserve und sicherer EVCS-Strom stehen so nur in der ``config.yaml``.
Die Datei liest ``cerbo-watchdog/bindaems_watchdog/config.py`` auf dem Cerbo; das Format zeigt
``cerbo-watchdog/config.ini.example``. Der Watchdog schreibt nur, was in dieser Datei steht – das
EMS kann sie zur Laufzeit nicht ändern. Ohne ``--out`` geht der Text auf die Standardausgabe.

Exitcode 2 mit der Meldung auf stderr: ungültige ``config.yaml``, ein Heartbeat-Takt, zu dem der
Watchdog die Datei ablehnen würde, oder eine Zieldatei, die sich nicht schreiben lässt. Eine EVCS
ohne GX-Instanz ergibt eine Warnung auf stderr (und einen Kommentar in der Datei).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bindaems.core.safety.types import de
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
    Instanz. Ist 2,5 × ``heartbeat_s`` nicht kleiner als ``timeout_s``, wirft die Funktion einen
    ``ConfigError``: Der Watchdog lehnt eine solche Datei beim Start ab.
    """
    watchdog = cfg.victron.watchdog
    timeout = f"{watchdog.timeout_s:g}"
    gap = f"{GAP_FACTOR * watchdog.heartbeat_s:g}"
    if float(gap) >= float(timeout):  # verglichen wird, was in der Datei steht
        raise ConfigError(
            f"Konfiguration ungültig: victron.watchdog: {de(GAP_FACTOR)} × heartbeat_s "
            f"({de(float(gap))} s) muss kleiner sein als timeout_s ({de(float(timeout))} s), "
            "sonst lehnt der Watchdog die config.ini ab"
        )
    blocks = [
        _HEADER,
        [
            "[watchdog]",
            "enabled = true",
            f"timeout_s = {timeout}",
            f"rearm_s = {REARM_S:g}",
            f"heartbeat_gap_s = {gap}",
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


def _evcs(cfg: Config) -> list[tuple[str, EvcsConfig, int | None]]:
    """Je EVCS-Wallbox Schlüssel, Konfiguration und evcharger-Instanz (``None``: keine)."""
    names = cfg.victron.instances.evcharger
    return [
        (key, wallbox, next((i for i, name in names.items() if name == key), None))
        for key, wallbox in cfg.wallboxes.items()
        if isinstance(wallbox, EvcsConfig)
    ]


def _no_instance(key: str) -> str:
    return (
        f"EVCS „{key}“: keine evcharger-Instanz in victron.instances.evcharger – "
        "der Watchdog setzt keinen sicheren Strom"
    )


def _evcs_blocks(cfg: Config) -> list[list[str]]:
    blocks = []
    for key, wallbox, instance in _evcs(cfg):
        if instance is None:
            blocks.append([f"; {_no_instance(key)}"])
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
        text = render_config(cfg)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    for key, _, instance in _evcs(cfg):
        if instance is None:
            print(f"Warnung: {_no_instance(key)}", file=sys.stderr)
    if args.out is None:
        sys.stdout.write(text)
        return 0
    try:
        args.out.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"config.ini nicht geschrieben: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
