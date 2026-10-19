"""CoE-Probe (Plan 6a, Entscheidung 14): hört am CoE-Port mit und zeigt jedes Paket.

Aufruf::

    python -m bindaems.tools.coe_probe listen [--sekunden N] [--port P]

``listen`` bindet den UDP-Port (Standard 5442, wie ``listen_port`` des core) und zeigt je Wert
eines Pakets eine Zeile: Zeit, Absender, Knoten, Art mit Ausgang, Einheit und Rohwert, etwa
„14:05:09 von 192.168.81.92: Knoten 56, analog 4, Einheit 10, Rohwert 120“. Damit lassen sich die
Empfangstabelle und die Skalierung (``unit``, ``scale``) in ``config.yaml`` prüfen. Ein Paket, das
kein gültiges CoE-V2-Paket ist, ergibt eine Zeile „ungültiges Paket“ mit dem Grund.

Die Probe sendet nie, sie hört nur mit. Sie läuft nur mit gestopptem core, denn der core bindet
denselben Port; ist er belegt, sagt sie das und endet mit Exitcode 2. Ohne ``--sekunden`` hört sie
bis Strg+C. Exitcode 0 nach dem Ende, 2 bei belegtem Port.
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import math
import socket
import sys
from collections.abc import Callable
from datetime import datetime
from typing import Final

from bindaems.core.adapters.coe import PortFactory, udp_port
from bindaems.shared.coe import CoeError, decode
from bindaems.shared.timeutil import LOCAL_TZ

DEFAULT_PORT: Final = 5442
PORT_BUSY: Final = "Port {port} ist belegt. Läuft der core noch?"


def address_in_use(exc: OSError) -> bool:
    """Ist der Fehler „Adresse schon in Gebrauch“ (ein anderer Prozess hält den Port)?"""
    return exc.errno == errno.EADDRINUSE


def port_busy(port: int) -> bool:
    """Hält ein anderer Prozess den UDP-Port auf allen Schnittstellen, etwa der core?

    Bindet den Port kurz und gibt ihn sofort wieder frei; gesendet wird nichts. Jeder andere
    Fehler als „Adresse in Gebrauch“ geht an den Aufrufer.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.bind(("0.0.0.0", port))  # noqa: S104 - wie der core: alle Schnittstellen
        except OSError as exc:
            if address_in_use(exc):
                return True
            raise
    return False


def packet_lines(data: bytes, addr: tuple[str, int], at: datetime) -> list[str]:
    """Je Wert des Pakets eine Zeile; ein ungültiges Paket ergibt eine Zeile mit dem Grund."""
    head = f"{at.astimezone(LOCAL_TZ):%H:%M:%S} von {addr[0]}"
    try:
        values = decode(data)
    except CoeError as exc:
        return [f"{head}: ungültiges Paket ({exc})"]
    return [
        f"{head}: Knoten {value.node}, {'analog' if value.analog else 'digital'} "
        f"{value.output}, Einheit {value.unit}, Rohwert {value.raw}"
        for value in values
    ]


async def listen(
    port: int,
    seconds: float | None,
    *,
    out: Callable[[str], None] = print,
    port_factory: PortFactory = udp_port,
) -> int:
    """Hört ``seconds`` lang (``None``: bis zum Abbruch) am Port mit; die Zahl der Pakete.

    Jedes Paket geht zeilenweise an ``out`` (``packet_lines``). Ist der Port belegt, wirft das
    Öffnen ``OSError`` (``address_in_use``). Der Port wird am Ende geschlossen, gesendet wird nie.
    """
    count = 0

    def received(data: bytes, addr: tuple[str, int]) -> None:
        nonlocal count
        count += 1
        for line in packet_lines(data, addr, datetime.now(LOCAL_TZ)):
            out(line)

    opened = await port_factory(received, port)
    try:
        if seconds is None:
            await asyncio.Event().wait()  # bis Strg+C
        else:
            await asyncio.sleep(seconds)
    finally:
        opened.close()
    return count


def _seconds(text: str) -> float:
    """Eine endliche Dauer in Sekunden über 0 (für ``--sekunden``)."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"keine Zahl: {text}") from None
    if not (math.isfinite(value) and value > 0):
        raise argparse.ArgumentTypeError(f"muss endlich sein und über 0 liegen: {text}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="coe_probe",
        description="CoE-Probe: am CoE-Port mithören (nur mit gestopptem core), sendet nie",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    listen_cmd = commands.add_parser("listen", help="jedes CoE-Paket zeigen")
    listen_cmd.add_argument(
        "--sekunden", type=_seconds, help="so lange mithören (Standard: bis Strg+C)"
    )
    listen_cmd.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"UDP-Port (Standard {DEFAULT_PORT})"
    )
    args = parser.parse_args(argv)
    print(f"Höre auf UDP-Port {args.port} mit; die Probe sendet nichts.")
    try:
        count = asyncio.run(listen(args.port, args.sekunden))
    except KeyboardInterrupt:
        print("\nBeendet.")
        return 0
    except OSError as exc:
        if address_in_use(exc):
            print(PORT_BUSY.format(port=args.port), file=sys.stderr)
            return 2
        raise
    print(f"{count} Pakete empfangen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
