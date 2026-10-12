"""Prüfprotokoll Teil 2 (Spec 17.2): schreibt mit Rückfrage auf die Geräte und erstellt den Bericht.

Aufruf::

    python -m bindaems.tools.verify_part2 --config PATH --punkt evcs|tesla|victron
        [--wallbox NAME] [--fahrzeug NAME] [--out BERICHT.md] [--yes] [--schlafend]

Das Werkzeug läuft gemeinsam mit dem Betreiber an der Anlage, der core bleibt dabei im Dry-Run.
Es nutzt dieselben Adapter und Aktoren wie der core (``part2.session``) und prüft jeden Wert mit
den Regeln 1–3 der Safety-Schicht. Vor dem Start sagt es, was es schreibt, und fragt nach; vor
jedem weiteren schreibenden Schritt fragt es erneut. Nach jedem Schritt, bei Abbruch (auch Strg+C)
und am Ende stellt es die vorgefundenen Werte wieder her; dann stoppt es die Adapter und schreibt
den Bericht (Standard: ``docs/verification/<Datum>-pruefprotokoll-teil2-<punkt>.md``, mit
``--schlafend`` ``…-tesla-schlafend.md``, damit er den Bericht des Punkts nicht überschreibt).

``--yes`` beantwortet alle Rückfragen mit Ja. Schritte, die einen Handgriff des Betreibers
brauchen (Strom von Hand ändern, Kabel ziehen, den Cerbo neu starten, Last einschalten), entfallen
dann als INFO „übersprungen (--yes)“; der Bericht nennt ``--yes`` unter „Rückfragen“.

Punkte: ``evcs`` (17.2-6 und der sichere Strom über den Watchdog, 17.2-5) mit ``--wallbox``
(Standard: die erste EVCS). ``tesla`` (17.2-7) mit ``--fahrzeug`` (Standard: das erste Fahrzeug
mit Tessie): Die Befehle gehen über Tessie, die Wirkung misst das Werkzeug am Wall Connector, und
das Fahrzeug wird nie geweckt. ``--schlafend`` prüft stattdessen, ohne etwas zu senden, dass ein
schlafendes Fahrzeug nicht geweckt wird. ``tesla`` braucht ``BINDAEMS_TESSIE_TOKEN``.
``victron`` (17.2-1 bis 17.2-4 und 17.2-8): Override des Netz-Sollwerts, Entladesperre, Netzladen,
„kein Akku ins Auto“ (mit der Leistung aller EVCS) und Importlimit. Es verweigert, solange Dynamic
ESS aktiv ist, denn DESS nullt fremde Overrides: Der Betreiber schaltet DESS vorher ab, vor der
Live-Schaltung von Victron; geht es mitten im Lauf an, schreibt es nichts mehr. Es verweigert auch,
solange ein Netz-Sollwert-Override steht, denn dann regelt ein anderes System, und bei einer
Entladegrenze von 0 W, die ein abgebrochener Lauf hinterlassen haben kann. Die Entladegrenze ist
eine gespeicherte Einstellung; das Werkzeug schreibt sie je Lauf höchstens viermal.

Exitcode 0: kein Befund ist FEHLER. 1: ein Befund ist FEHLER, der Betreiber hat die Rückfrage vor
dem Start verneint (dann ohne Secrets und ohne Verbindung) oder der Bericht ließ sich nicht
schreiben. 2: Konfiguration, Secrets oder Aufruf ungültig. 130: Strg+C; kam es nach dem Start,
sind die vorgefundenen Werte dann wiederhergestellt, soweit es ging, und der Bericht ist
geschrieben.

Die Rückfragen lesen von der Konsole, während der Event-Loop steht; die Adapter pausieren solange.
Nach einer langen Pause (etwa beim Abziehen des Netzwerkkabels) verbinden sie bei Bedarf von selbst
neu.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from bindaems.core.adapters.victron_mqtt import AiomqttTransport, VictronMqttAdapter
from bindaems.shared.config import (
    Config,
    ConfigError,
    EvcsConfig,
    Secrets,
    load_config,
    load_secrets,
)
from bindaems.shared.control import VICTRON, vehicle_actuator, wallbox_actuator
from bindaems.shared.timeutil import LOCAL_TZ
from bindaems.tools.part2.evcs import DEFAULT_TIMINGS as EVCS_TIMINGS
from bindaems.tools.part2.evcs import run_evcs
from bindaems.tools.part2.session import MAX_PERSISTENT_WRITES, Part2Session, build_session, minutes
from bindaems.tools.part2.session import RESTORE_S as SESSION_RESTORE_S
from bindaems.tools.part2.tesla import DEFAULT_TIMINGS as TESLA_TIMINGS
from bindaems.tools.part2.tesla import NO_VEHICLE, run_tesla
from bindaems.tools.part2.victron import DEFAULT_TIMINGS as VICTRON_TIMINGS
from bindaems.tools.part2.victron import run_victron
from bindaems.tools.verify_checks import Finding, render_report
from bindaems.tools.watchdog_probe import run_probe, timings_from_config

PARTS: Final = ("evcs", "tesla", "victron")
TITLES: Final = {"evcs": "EVCS", "tesla": "Tesla", "victron": "Victron"}
ITEMS: Final = {"evcs": "17.2-6", "tesla": "17.2-7", "victron": "17.2-1"}  # Zeilen des Werkzeugs
PROBE_ITEM: Final = "17.2-5"
WARNINGS: Final = {
    "evcs": (
        "Das Werkzeug stellt an der EVCS „{device}“ den Ladestrom um und fragt vor jedem weiteren "
        "schreibenden Schritt nach. Ein angestecktes Fahrzeug lädt dabei mit wechselndem Strom. "
        "Nach jedem Schritt und am Ende, auch nach Strg+C, stellt es die vorgefundenen Werte "
        "wieder her."
    ),
    "tesla": (
        "Das Werkzeug stellt am Fahrzeug „{device}“ über Tessie den Ladestrom um und fragt vor dem "
        "kurzen Ausschalten des Ladens nach. Das Fahrzeug lädt dabei mit wechselndem Strom. Es "
        "sendet nur an ein waches Fahrzeug, das zu Hause angesteckt ist, und weckt es nie. Nach "
        "jedem Schritt und am Ende, auch nach Strg+C, stellt es die vorgefundenen Werte wieder her."
    ),
    "victron": (
        "Das Werkzeug setzt am Cerbo kurz den Netz-Sollwert-Override (der Akku lädt dabei aus dem "
        "Netz) und stellt die Entladegrenze auf 0 W, eine gespeicherte Einstellung (höchstens "
        f"{MAX_PERSISTENT_WRITES} persistente Schreibzugriffe je Lauf). Es fragt vor jedem "
        "weiteren schreibenden Schritt nach und verlangt ausgeschaltetes Dynamic ESS und einen "
        "Cerbo ohne Netz-Sollwert-Override, dessen Entladegrenze nicht auf 0 W steht; sonst endet "
        "es ohne zu schreiben. Nach jedem Schritt und am Ende, auch nach Strg+C, stellt es die "
        "vorgefundenen Werte wieder her."
    ),
    "tesla-schlafend": (
        "Das Werkzeug prüft am Fahrzeug „{device}“, dass ein schlafendes Auto nicht geweckt wird. "
        "Es sendet nichts und liest nur den Zustand aus dem Tessie-Cache, "
        f"{minutes(TESLA_TIMINGS.sleep_total_s)} min lang. Das Fahrzeug muss dafür schon schlafen."
    ),
}
# Frist je Wert beim letzten Wiederherstellen: ``confirm_s`` des Teils. Ein Befehl an Tessie kann
# bis zu 60 s dauern, die 15 s der Sitzung wären dafür zu kurz.
PART_RESTORE_S: Final = {
    "evcs": EVCS_TIMINGS.confirm_s,
    "tesla": TESLA_TIMINGS.confirm_s,
    "victron": VICTRON_TIMINGS.confirm_s,
}
ABORTED: Final = "mit Strg+C abgebrochen; die übrigen Schritte fehlen"
RESTORE_ABORTED: Final = "mit Strg+C abgebrochen; die Werte bitte von Hand prüfen"
NO_PORTAL: Final = "Portal-ID konnte nicht ermittelt werden"
NO_TESSIE: Final = "Tessie-Token fehlt: BINDAEMS_TESSIE_TOKEN setzen."
UNATTENDED_META: Final = "mit --yes beantwortet; Schritte mit Handgriff übersprungen"
VENUS_OS_PATH: Final = "platform/0/Firmware/Installed/Version"
VERSION_WAIT_S = 5.0  # so lange wartet die Leseanfrage nach der Version von Venus OS


@dataclass
class _Outcome:
    """Ergebnis eines Laufs: Befunde, Zusatzangaben für den Bericht und ob Strg+C kam."""

    findings: list[Finding] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)
    interrupted: bool = False


def _ask_operator(question: str) -> bool:
    """Fragt den Betreiber. Nur „j“ gilt als Ja, ein Eingabeende als Nein."""
    try:
        answer = input(f"{question} [j/N] ")
    except EOFError:
        print()  # die Eingabezeile abschließen
        return False
    return answer.strip() == "j"


def _auto_yes(question: str) -> bool:
    """Mit ``--yes``: zeigt die Frage und beantwortet sie mit Ja."""
    print(f"{question} j (--yes)")
    return True


def _confirmed(warning: str) -> bool:
    print(warning)
    return _ask_operator("Fortfahren?")


def _secrets_error(exc: ValidationError) -> str:
    """Fehlertext ohne Eingabewerte – diese könnten Secrets enthalten."""
    details = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors(include_input=False, include_url=False)
    )
    return f"Secrets ungültig: {details}"


def _wallbox(cfg: Config, name: str | None) -> str:
    """Die EVCS für den Punkt ``evcs``: ``name`` oder die erste EVCS in ``wallboxes``."""
    evcs = [key for key, wallbox in cfg.wallboxes.items() if isinstance(wallbox, EvcsConfig)]
    if name is None:
        if not evcs:
            raise ValueError("Keine EVCS in wallboxes.")
        return evcs[0]
    if name not in evcs:
        raise ValueError(f"Keine EVCS „{name}“ in wallboxes.")
    return name


def _vehicle(cfg: Config, name: str | None) -> str:
    """Das Fahrzeug für den Punkt ``tesla``: ``name`` oder das erste Fahrzeug mit Tessie."""
    tessie = [key for key, vehicle in cfg.vehicles.items() if vehicle.tessie is not None]
    if name is None:
        if not tessie:
            raise ValueError("Kein Fahrzeug mit Tessie in vehicles.")
        return tessie[0]
    if name not in tessie:
        raise ValueError(NO_VEHICLE.format(name))
    return name


def _device(cfg: Config, args: argparse.Namespace) -> str:
    """Das Gerät des Punkts: das Fahrzeug für ``tesla``, die Anlage für ``victron``, sonst die EVCS.

    Die Anlage ist der Aktor ``victron``; ``--wallbox`` und ``--fahrzeug`` gelten für sie nicht.
    """
    if args.punkt == "victron":
        return VICTRON
    return _vehicle(cfg, args.fahrzeug) if args.punkt == "tesla" else _wallbox(cfg, args.wallbox)


def _actuator_id(punkt: str, device: str) -> str:
    if punkt == "victron":
        return device
    return vehicle_actuator(device) if punkt == "tesla" else wallbox_actuator(device)


def _portal(cfg: Config, session: Part2Session) -> str | None:
    """Die Portal-ID aus der Konfiguration, sonst die, die der Victron-Adapter ermittelt hat."""
    if cfg.victron.mqtt.portal_id is not None:
        return cfg.victron.mqtt.portal_id
    for adapter in session.adapters:
        if isinstance(adapter, VictronMqttAdapter):
            return adapter.portal_id
    return None


def _watchdog_probe(
    cfg: Config, secrets: Secrets, session: Part2Session
) -> Callable[[], Awaitable[list[Finding]]]:
    """Die Watchdog-Probe (``run_probe``) mit eigener MQTT-Verbindung, wie das Werkzeug der Probe.

    Kommt keine Verbindung zustande, ist das ein Befund statt eines Abbruchs.
    """

    async def probe() -> list[Finding]:
        portal = _portal(cfg, session)
        if portal is None:
            return [Finding(PROBE_ITEM, "Verbindung zum Cerbo", "fail", NO_PORTAL)]
        findings: list[Finding] = []
        try:
            async with AiomqttTransport(cfg.victron.mqtt, secrets.mqtt_password) as transport:
                findings = await run_probe(transport, portal, timings_from_config(cfg))
        except Exception as exc:  # ein Befund statt eines Abbruchs
            if not findings:
                detail = f"{type(exc).__name__}: {exc}"
                return [Finding(PROBE_ITEM, "Verbindung zum Cerbo", "fail", detail)]
            # die Probe ist durch; nur das Trennen schlug fehl
            print(f"Verbindung nicht sauber getrennt: {type(exc).__name__}: {exc}", file=sys.stderr)
        return findings

    return probe


def _version(payload: bytes) -> str | None:
    """Der Text einer N/-Meldung ``{"value": "v3.80"}``; sonst ``None``."""
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    value = data.get("value") if isinstance(data, dict) else None
    return value if isinstance(value, str) and value else None


async def _venus_os(cfg: Config, secrets: Secrets, portal: str) -> str | None:
    """Die Version von Venus OS über eine Leseanfrage ``R/…``; bei jedem Fehler ``None``.

    Eine eigene, kurze Verbindung: Der Victron-Adapter liest diesen Pfad nicht. Sie wartet
    höchstens ``VERSION_WAIT_S`` und schreibt nichts.
    """
    topic = f"N/{portal}/{VENUS_OS_PATH}"
    try:
        async with asyncio.timeout(VERSION_WAIT_S):
            async with AiomqttTransport(cfg.victron.mqtt, secrets.mqtt_password) as transport:
                await transport.subscribe(topic)
                await transport.publish(f"R/{portal}/{VENUS_OS_PATH}", b"")
                async for message in transport.messages():
                    if message.topic == topic:
                        return _version(message.payload)
    except Exception:  # Zeitüberschreitung, keine Verbindung, …: die Version bleibt unbekannt
        return None
    return None


async def _part(
    session: Part2Session,
    cfg: Config,
    secrets: Secrets,
    punkt: str,
    device: str,
    asleep_check: bool,
) -> list[Finding]:
    """Startet die Sitzung und führt den Teil aus."""
    await session.start()
    if punkt == "evcs":
        return await run_evcs(session, cfg, device, probe=_watchdog_probe(cfg, secrets, session))
    if punkt == "tesla":
        return await run_tesla(session, cfg, device, asleep_check=asleep_check)
    if punkt == "victron":
        return await run_victron(session, cfg)
    raise ValueError(f"Punkt „{punkt}“ ist unbekannt.")


def _execute(
    session: Part2Session,
    cfg: Config,
    secrets: Secrets,
    punkt: str,
    device: str,
    asleep_check: bool,
) -> _Outcome:
    """Teil ausführen, Werte wiederherstellen, Angaben für den Bericht lesen, Sitzung stoppen.

    Alles läuft im selben Event-Loop; die Adapter laufen weiter, bis die Werte wiederhergestellt
    sind. Strg+C im Teil oder ein Fehler darin beendet nur den Teil: Die Befunde bis dahin bleiben,
    eine Zeile nennt den Abbruch, und das Wiederherstellen läuft trotzdem. Strg+C beim
    Wiederherstellen bricht es ab (eine FEHLER-Zeile sagt das); gestoppt wird in jedem Fall.
    """
    item = ITEMS[punkt]
    outcome = _Outcome()
    with asyncio.Runner() as runner:
        try:
            outcome.findings = runner.run(_part(session, cfg, secrets, punkt, device, asleep_check))
        except KeyboardInterrupt:
            print(
                "\nAbgebrochen (Strg+C). Die vorgefundenen Werte werden wiederhergestellt, "
                "bitte warten; ein weiteres Strg+C bricht das Wiederherstellen ab."
            )
            outcome.interrupted = True
            outcome.findings = [*session.findings, Finding(item, "Abbruch", "warn", ABORTED)]
        except Exception as exc:  # ein Fehler des Werkzeugs: Befund, dann wiederherstellen
            error = Finding(
                item, "Interner Fehler des Werkzeugs", "fail", f"{type(exc).__name__}: {exc}"
            )
            outcome.findings = [*session.findings, error]
        finally:
            _finish(runner, session, cfg, secrets, punkt, device, outcome)
    return outcome


def _finish(
    runner: asyncio.Runner,
    session: Part2Session,
    cfg: Config,
    secrets: Secrets,
    punkt: str,
    device: str,
    outcome: _Outcome,
) -> None:
    """Nach dem Teil: wiederherstellen, Angaben für den Bericht lesen, dann die Sitzung stoppen.

    Das Wiederherstellen wartet je Wert so lange, wie der Teil auf eine Bestätigung wartet
    (``PART_RESTORE_S``).
    """
    item = ITEMS[punkt]
    try:
        outcome.findings += runner.run(
            session.restore(PART_RESTORE_S.get(punkt, SESSION_RESTORE_S))
        )
    except KeyboardInterrupt:
        print(f"\nAchtung: Wiederherstellen {RESTORE_ABORTED}.", file=sys.stderr)
        outcome.interrupted = True
        outcome.findings.append(Finding(item, "Wiederherstellen", "fail", RESTORE_ABORTED))
    outcome.meta = _firmware(session, punkt, device)
    portal = _portal(cfg, session)
    if not outcome.interrupted and portal is not None:
        try:
            version = runner.run(_venus_os(cfg, secrets, portal))
        except KeyboardInterrupt:
            outcome.interrupted = True
        else:
            if version is not None:
                outcome.meta = {"Venus OS": version, **outcome.meta}
    try:
        runner.run(session.stop())
    except KeyboardInterrupt:  # die übrigen Tasks bricht das Ende des Runners ab
        outcome.interrupted = True
    except Exception as exc:
        print(f"Sitzung nicht sauber beendet: {type(exc).__name__}: {exc}", file=sys.stderr)


def _firmware(session: Part2Session, punkt: str, device: str) -> dict[str, str]:
    """Die Firmware des Geräts, soweit es sie meldet (EVCS: aus ihrem Modbus-Abbild)."""
    if punkt != "evcs":
        return {}
    firmware = session.snapshot().ok(f"wallbox.{device}.firmware")
    return {"EVCS-Firmware": firmware} if isinstance(firmware, str) and firmware else {}


def _write(out: Path, report: str) -> bool:
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
    except OSError as exc:
        # Das Ergebnis darf nicht verloren gehen: Der Lauf hat an der Anlage geschrieben.
        print(f"Bericht nicht geschrieben: {exc}", file=sys.stderr)
        print(report)
        return False
    print(f"Bericht: {out}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_part2",
        description="Prüfprotokoll Teil 2 (Spec 17.2): schreibend, mit Rückfrage beim Betreiber",
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--punkt", choices=PARTS, required=True)
    parser.add_argument("--wallbox", help="EVCS für --punkt evcs (Standard: die erste EVCS)")
    parser.add_argument(
        "--fahrzeug", help="Fahrzeug für --punkt tesla (Standard: das erste mit Tessie)"
    )
    parser.add_argument(
        "--out",
        type=Path,
        help=(
            "Bericht (Standard: docs/verification/<Datum>-pruefprotokoll-teil2-<punkt>.md, "
            "mit --schlafend …-tesla-schlafend.md)"
        ),
    )
    parser.add_argument("--yes", action="store_true", help="alle Rückfragen mit Ja beantworten")
    parser.add_argument(
        "--schlafend",
        action="store_true",
        help="nur --punkt tesla: prüfen, dass ein schlafendes Fahrzeug nicht geweckt wird",
    )
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except KeyboardInterrupt:  # Strg+C vor dem Start der Sitzung, etwa an der Rückfrage
        print("\nAbgebrochen.")
        return 130


def _run(args: argparse.Namespace) -> int:
    punkt: str = args.punkt
    if args.schlafend and punkt != "tesla":
        print("--schlafend gilt nur für --punkt tesla.", file=sys.stderr)
        return 2
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        device = _device(cfg, args)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    warning = WARNINGS["tesla-schlafend" if args.schlafend else punkt]
    if not args.yes and not _confirmed(warning.format(device=device)):
        print("Abgebrochen.")
        return 1
    try:
        secrets = load_secrets()  # erst nach der Bestätigung: ein Abbruch braucht keine Secrets
    except ValidationError as exc:
        print(_secrets_error(exc), file=sys.stderr)
        return 2
    if punkt == "tesla" and secrets.tessie_token is None:
        print(NO_TESSIE, file=sys.stderr)  # ohne Token wäre Tessie nie erreichbar
        return 2
    day = datetime.now(LOCAL_TZ).date().isoformat()
    stem = f"{day}-pruefprotokoll-teil2-{punkt}" + ("-schlafend" if args.schlafend else "")
    out = args.out or Path("docs/verification") / f"{stem}.md"
    ask = _auto_yes if args.yes else _ask_operator
    session = build_session(cfg, secrets, ask=ask, unattended=args.yes)
    outcome = _execute(session, cfg, secrets, punkt, device, args.schlafend)
    label = session.limits.actuators[_actuator_id(punkt, device)].label
    meta = {"Werkzeug": "verify_part2", "Datum": day, "Punkt": punkt, "Gerät": label}
    if args.yes:
        meta["Rückfragen"] = UNATTENDED_META
    report = render_report(
        outcome.findings, meta | outcome.meta, title=f"Prüfprotokoll Teil 2 – {TITLES[punkt]}"
    )
    written = _write(out, report)
    if outcome.interrupted:
        return 130
    if not written:
        return 1
    return 0 if all(f.status != "fail" for f in outcome.findings) else 1


if __name__ == "__main__":
    sys.exit(main())
