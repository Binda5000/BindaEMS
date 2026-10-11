"""Watchdog-Probe (Prüfprotokoll Teil 2, Spec 17.2 Punkt 5): löst den Watchdog absichtlich aus.

Aufruf::

    python -m bindaems.tools.watchdog_probe --config PATH [--out BERICHT.md] [--yes]

Die Probe spricht MQTT mit dem Cerbo. Sie schreibt dort nur den Heartbeat des Watchdogs
(``W/<portal>/bindaems/0/Heartbeat``) und sendet den Keepalive. Sie

1. fragt den Watchdog nach Version und Zustand,
2. macht ihn mit Heartbeats scharf,
3. lässt die Heartbeats ausbleiben, bis er auslöst,
4. wartet ohne Heartbeat, bis seine Aktionen mit allen Wiederholungen durch sein müssen, liest
   die Zahl der fehlgeschlagenen Aktionen und nimmt die Heartbeats wieder auf, bis er wieder
   scharf ist,
5. und gibt ihn mit der 0 frei.

**Die Probe löst den Watchdog aus:** Er setzt die EVCS auf den sicheren Strom und schreibt Min-SOC
und Entladegrenze, wenn sie abweichen. Darum fragt das Werkzeug vorher nach; ``--yes`` überspringt
die Rückfrage. Das Ergebnis ist ein Bericht im Format des Prüfprotokolls Teil 2. Läuft das EMS
währenddessen mit einem live angeforderten Aktor, sendet es eigene Heartbeats, und der Watchdog
löst nicht aus.

Die Pause in Schritt 4 ist nötig: Wird der Watchdog wieder scharf, bricht sein Aktionslauf ab, und
eine Aktion, die noch auf ihre nächste Wiederholung wartet, kommt nicht mehr zum Zug.

Bricht die Probe ab, nachdem die Heartbeats liefen (Strg+C, Verbindungsverlust, Fehler), versucht
sie, den Watchdog mit dem Heartbeat 0 freizugeben: Sonst löste er später unbeaufsichtigt aus oder
bliebe ausgelöst. Gelingt das nicht, steht es im Bericht und auf der Konsole, dazu der Weg zurück.
Befunde, die bis dahin vorlagen, bleiben im Bericht.

Exitcode 0: kein Schritt ist ``fail``. 1: ein Schritt ist ``fail``, der Bericht ließ sich nicht
schreiben oder der Betreiber hat die Rückfrage verneint (dann ohne Verbindung und ohne Secrets).
2: Konfiguration oder Secrets ungültig. 130: Strg+C.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from aiomqtt import MqttError
from pydantic import ValidationError

from bindaems.core.adapters.victron_mqtt import (
    DISCOVERY_TIMEOUT_S,
    HEARTBEAT_PATH,
    KEEPALIVE_SUPPRESS,
    AiomqttTransport,
    MqttMessage,
    MqttTransport,
)
from bindaems.core.safety.types import de, finite_number
from bindaems.core.watchdog_client.heartbeat import RELEASE
from bindaems.core.watchdog_client.status import WATCHDOG_VERSION
from bindaems.shared.config import Config, ConfigError, Secrets, load_config, load_secrets
from bindaems.shared.timeutil import LOCAL_TZ
from bindaems.tools.verify_checks import Finding, Status, render_report
from bindaems.tools.watchdog_config import RETRIES, RETRY_S

ITEM = "17.2-5"  # Punkt im Prüfprotokoll Teil 2 (Spec 17.2)
KEEPALIVE_S = 30.0  # Abstand der Keepalives; der Cerbo schickt Änderungen nur auf Anfrage
ACTIONS_SLACK_S = 10.0  # Luft nach dem letzten möglichen Versuch des Aktionslaufs
WAITING, ARMED, TRIPPED, DISABLED = 0, 1, 2, 3  # Werte von ``/State``
STATE_NAMES = {WAITING: "wartet", ARMED: "scharf", TRIPPED: "ausgelöst", DISABLED: "deaktiviert"}
NO_ANSWER = "Keine Antwort von com.victronenergy.bindaems."
RELEASE_ATTEMPT_S = 2.0  # so lange versucht die Probe nach einem Abbruch, den Watchdog freizugeben
UNRELEASED = (
    "Der Watchdog konnte nicht freigegeben werden: Er kann scharf bleiben und später auslösen "
    "oder ausgelöst bleiben. Zurücksetzen: die Probe erneut starten (sie endet mit der Freigabe) "
    "oder den Heartbeat 0 senden (am Cerbo: dbus -y com.victronenergy.bindaems /Heartbeat "
    "SetValue 0)."
)
WARNING = (
    "Die Probe löst den Watchdog aus. Er setzt die EVCS auf den sicheren Strom und schreibt "
    "Min-SOC und Entladegrenze, wenn sie abweichen."
)


@dataclass(frozen=True)
class ProbeTimings:
    """Takt und Wartezeiten der Probe in Sekunden.

    ``heartbeat_s`` ist der Takt der Heartbeats, ``timeout_s`` das Timeout des Watchdogs. Die
    ``*_wait_s`` sind die längsten Wartezeiten der Schritte; ``trip_slack_s`` kommt zum Timeout
    hinzu, bis die Auslösung da sein muss. ``actions_wait_s`` ist die Pause ohne Heartbeat nach der
    Auslösung, bis der Aktionslauf samt Wiederholungen zu Ende sein muss.
    """

    heartbeat_s: float
    answer_wait_s: float  # Antwort auf den Keepalive
    arm_wait_s: float  # scharf nach den ersten Heartbeats
    timeout_s: float
    trip_slack_s: float
    actions_wait_s: float  # Aktionslauf nach der Auslösung, ohne Heartbeat
    rearm_wait_s: float  # wieder scharf nach der Auslösung
    release_wait_s: float  # Zustand „wartet“ nach der 0


def timings_from_config(cfg: Config) -> ProbeTimings:
    """Heartbeat und Timeout aus ``victron.watchdog``; die Wartezeiten lassen Luft nach oben.

    Der Watchdog wird nach ``rearm_s`` = 30 s lückenloser Heartbeats scharf und prüft im Takt von
    einer Sekunde. Sein Aktionslauf dauert mit allen Wiederholungen ``RETRIES`` × ``RETRY_S`` aus
    der erzeugten ``config.ini``; die Pause danach ist (``RETRIES`` + 1) × ``RETRY_S`` + 10 s.
    """
    watchdog = cfg.victron.watchdog
    return ProbeTimings(
        heartbeat_s=watchdog.heartbeat_s,
        answer_wait_s=10.0,
        arm_wait_s=45.0,
        timeout_s=watchdog.timeout_s,
        trip_slack_s=30.0,
        actions_wait_s=(RETRIES + 1) * RETRY_S + ACTIONS_SLACK_S,
        rearm_wait_s=45.0,
        release_wait_s=15.0,
    )


def _as_int(value: object) -> int | None:
    """Der Wert als ganze Zahl; Texte, ``bool``, Brüche, NaN und ``None`` ergeben ``None``."""
    number = finite_number(value)
    return int(number) if number is not None and number.is_integer() else None


def _decode(payload: bytes) -> object:
    """Der ``value`` einer N/-Meldung; ``None`` bei Unlesbarem, leerer Meldung und ``null``."""
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    return data.get("value") if isinstance(data, dict) else None


def _count(value: object) -> str:
    number = _as_int(value)
    return str(number) if number is not None else "?"


def _error_finding(exc: Exception, *, unreleased: bool = False) -> Finding:
    """Der Befund zu einem Fehler: Verbindungsprobleme getrennt von Fehlern der Probe selbst.

    ``unreleased``: Der Watchdog ließ sich nach dem Fehler nicht freigeben (Hinweis ``UNRELEASED``).
    """
    detail = f"{type(exc).__name__}: {exc}"
    if unreleased:
        detail += f". {UNRELEASED}"
    connection = isinstance(exc, OSError | MqttError)
    title = "Verbindung zum Cerbo" if connection else "Interner Fehler der Probe"
    return Finding(ITEM, title, "fail", detail)


class _Probe:
    """Ein Lauf der Probe: liest mit, was der Watchdog meldet, und sendet die Heartbeats."""

    def __init__(self, transport: MqttTransport, portal: str, timings: ProbeTimings) -> None:
        self._transport = transport
        self._portal = portal
        self._timings = timings
        self._values: dict[str, object] = {}  # zuletzt gesehen: N/<portal>/bindaems/0/<Pfad>
        self._changed = asyncio.Event()  # gesetzt, wenn ein Wert eintrifft oder ein Task endet
        self._tasks: list[asyncio.Task[None]] = []
        self._findings: list[Finding] = []  # in der Reihenfolge der Tabelle, mit den Schritten
        self._counter = 0  # zuletzt gesendeter Heartbeat über 0
        self._last_beat = 0.0  # Zeit des Event-Loops, zu der er ging
        self._released = False  # die 0 ist gesendet
        self._release_tried = False  # die Freigabe nach einem Abbruch wurde versucht
        self._release_failed = False  # … und ist gescheitert

    async def run(self) -> list[Finding]:
        """Führt die Schritte aus; ein Fehler beendet den Lauf, die bisherigen Befunde bleiben.

        Nach einem Fehler gibt die Probe den Watchdog frei, wenn die Heartbeats schon liefen, und
        hängt eine Fehlerzeile an, die Verbindungsverlust und Fehler der Probe unterscheidet.
        """
        try:
            await self._steps()
        except Exception as exc:
            await self._release_after_abort()
            self._findings.append(_error_finding(exc, unreleased=self._release_failed))
        return self._findings

    async def close(self) -> None:
        """Gibt den Watchdog nach einem Abbruch frei und beendet die Hilfstasks."""
        try:
            await self._release_after_abort()
        finally:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _steps(self) -> None:
        t = self._timings
        loop = asyncio.get_running_loop()
        self._spawn(self._read(self._transport.messages()))
        await self._transport.subscribe(f"N/{self._portal}/bindaems/0/#")
        await self._transport.publish(f"R/{self._portal}/keepalive", b"")  # alles neu senden
        self._spawn(self._keepalive())
        if not await self._until(self._answered, loop.time() + t.answer_wait_s):
            self._findings.append(Finding(ITEM, "Watchdog erreichbar", "fail", NO_ANSWER))
            return
        version = str(self._values["Version"])
        self._findings.append(
            Finding(
                ITEM,
                "Watchdog erreichbar",
                "ok",
                f"Version {version}, Zustand {self._state_name()}",
            )
        )
        if version == WATCHDOG_VERSION:
            self._findings.append(Finding(ITEM, "Watchdog-Version", "ok", version))
        else:
            detail = f"{version}, erwartet {WATCHDOG_VERSION}"
            self._findings.append(Finding(ITEM, "Watchdog-Version", "warn", detail))

        self._findings.append(await self._arm("Scharf nach Heartbeat", t.arm_wait_s))
        trips_before = self._values.get("TripCount")
        elapsed = await self._trip()
        if elapsed is not None:
            try:
                await self._pause(t.actions_wait_s)
            except BaseException:
                # Abbruch vor dem Ende des Aktionslaufs: Der Befund zur Auslösung bleibt erhalten.
                self._findings.append(self._trip_finding(elapsed, trips_before, final=False))
                raise
        # Mit der Auslösung steht /LastTripFailures auf 0; den Endstand bekommt der Pfad, wenn der
        # Aktionslauf endet. Die Pause ohne Heartbeat lässt ihn mit allen Wiederholungen zu Ende
        # laufen: Würde der Watchdog vorher wieder scharf, bräche der Lauf ab.
        self._findings.append(self._trip_finding(elapsed, trips_before, final=True))
        self._findings.append(await self._arm("Wieder scharf", t.rearm_wait_s))
        self._findings.append(await self._release())

    async def _release_after_abort(self) -> None:
        """Gibt den Watchdog nach einem Abbruch frei, falls die Heartbeats schon liefen.

        Ohne die 0 bliebe er scharf und löste ``timeout_s`` später aus, bliebe ausgelöst, oder ein
        angefangener Lauf machte ihn nachträglich scharf. Es bleibt bei einem Versuch, auch wenn
        er selbst abgebrochen wird; er dauert höchstens ``RELEASE_ATTEMPT_S``. Scheitert er, sagen
        die Konsole und der Befund es.
        """
        if self._counter == 0 or self._released or self._release_tried:
            return
        self._release_tried = True
        try:
            async with asyncio.timeout(RELEASE_ATTEMPT_S):
                await self._send(RELEASE)
        except Exception:  # Zeitüberschreitung, Verbindung weg, …
            self._release_failed = True
            print(f"Achtung: {UNRELEASED}", file=sys.stderr)
        else:
            print("Der Watchdog ist mit dem Heartbeat 0 freigegeben.")

    async def _arm(self, title: str, wait_s: float) -> Finding:
        """Sendet Heartbeats, bis der Watchdog scharf ist, höchstens ``wait_s`` lang."""
        elapsed = await self._beat_until(ARMED, wait_s)
        if elapsed is None:
            detail = f"nach {de(round(wait_s))} s nicht scharf (Zustand {self._state_name()})"
            return Finding(ITEM, title, "fail", detail)
        return Finding(ITEM, title, "ok", f"scharf nach {de(round(elapsed))} s")

    async def _trip(self) -> float | None:
        """Lässt die Heartbeats ausbleiben; Sekunden seit dem letzten bis zur Auslösung."""
        t = self._timings
        loop = asyncio.get_running_loop()
        deadline = loop.time() + t.timeout_s + t.trip_slack_s
        if not await self._until(lambda: self._state() == TRIPPED, deadline):
            return None
        return loop.time() - self._last_beat

    async def _pause(self, wait_s: float) -> None:
        """Wartet ``wait_s`` ohne Heartbeat; endet die Verbindung vorher, mit deren Fehler."""
        await self._until(lambda: False, asyncio.get_running_loop().time() + wait_s)

    def _trip_finding(self, elapsed: float | None, trips_before: object, *, final: bool) -> Finding:
        title = "Auslösung ohne Heartbeat"
        if elapsed is None:
            t = self._timings
            return Finding(
                ITEM,
                title,
                "fail",
                f"nach {de(round(t.timeout_s + t.trip_slack_s))} s nicht ausgelöst",
            )
        failures = _as_int(self._values.get("LastTripFailures"))
        detail = (
            f"ausgelöst nach {de(round(elapsed))} s, "
            f"Auslösungen {_count(trips_before)} → {_count(self._values.get('TripCount'))}, "
            f"fehlgeschlagene Aktionen {_count(failures)}"
        )
        status: Status = "ok" if failures == 0 else "warn"  # unbekannt gilt nicht als fehlerfrei
        if not final:  # die Fehlerzahl ist erst am Ende des Aktionslaufs verbindlich
            detail += " (Abbruch vor dem Ende des Aktionslaufs)"
            status = "warn"
        return Finding(ITEM, title, status, detail)

    async def _release(self) -> Finding:
        title = "Freigabe ohne Auslösung"
        loop = asyncio.get_running_loop()
        await self._send(RELEASE)
        if await self._until(
            lambda: self._state() == WAITING, loop.time() + self._timings.release_wait_s
        ):
            return Finding(ITEM, title, "ok", "Heartbeat 0: Zustand wartet")
        return Finding(ITEM, title, "fail", f"Zustand {self._state_name()} statt wartet")

    async def _beat_until(self, state: int, wait_s: float) -> float | None:
        """Sendet Heartbeats im Takt, bis der Watchdog ``state`` meldet.

        Liefert die Sekunden bis dahin oder ``None``, wenn er in ``wait_s`` nicht dort ankommt.
        """
        loop = asyncio.get_running_loop()
        start = loop.time()
        deadline = start + wait_s
        beat_at = start
        while True:
            self._counter += 1
            await self._send(self._counter)
            beat_at += self._timings.heartbeat_s
            if await self._until(lambda: self._state() == state, min(beat_at, deadline)):
                return loop.time() - start
            if loop.time() >= deadline:
                return None

    async def _send(self, value: int) -> None:
        payload = json.dumps({"value": value}).encode()
        await self._transport.publish(f"W/{self._portal}/{HEARTBEAT_PATH}", payload)
        if value == RELEASE:
            self._released = True
        else:
            self._last_beat = asyncio.get_running_loop().time()

    async def _until(self, predicate: Callable[[], bool], deadline: float) -> bool:
        """Wartet, bis ``predicate`` gilt (``True``) oder ``deadline`` (Loop-Zeit) um ist.

        Bricht mit dem Fehler ab, wenn die Verbindung endet: Sonst stünde im Bericht
        „nicht ausgelöst“ für etwas, das nur an der Verbindung lag.
        """
        loop = asyncio.get_running_loop()
        while not predicate():
            for task in self._tasks:
                if task.done():
                    task.result()  # wirft den Fehler des Tasks
                    raise ConnectionError("MQTT-Verbindung beendet")
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            self._changed.clear()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(remaining):
                    await self._changed.wait()
        return True

    def _answered(self) -> bool:
        return self._values.get("State") is not None and self._values.get("Version") is not None

    def _state(self) -> int | None:
        return _as_int(self._values.get("State"))

    def _state_name(self) -> str:
        state = self._state()
        return STATE_NAMES.get(state, "unbekannt") if state is not None else "unbekannt"

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        task.add_done_callback(lambda _: self._changed.set())  # weckt ``_until``
        self._tasks.append(task)

    async def _read(self, messages: AsyncIterator[MqttMessage]) -> None:
        prefix = f"N/{self._portal}/bindaems/0/"
        async for message in messages:
            if message.topic.startswith(prefix):
                self._values[message.topic[len(prefix) :]] = _decode(message.payload)
                self._changed.set()

    async def _keepalive(self) -> None:
        while True:
            await asyncio.sleep(KEEPALIVE_S)
            await self._transport.publish(f"R/{self._portal}/keepalive", KEEPALIVE_SUPPRESS)


async def run_probe(transport: MqttTransport, portal: str, timings: ProbeTimings) -> list[Finding]:
    """Führt die Probe aus (Schritte siehe Modulbeschreibung); je Schritt ein ``Finding``.

    ``transport`` ist eine geöffnete Verbindung. Antwortet der Watchdog nicht, endet die Probe nach
    dem ersten Schritt. Ein Fehler (Verbindung weg, Fehler der Probe) beendet den Lauf: Die
    bisherigen Befunde bleiben, eine Fehlerzeile kommt dazu. Endet die Probe vorzeitig, nachdem die
    Heartbeats liefen, auch durch Abbruch der Task, gibt sie den Watchdog mit dem Heartbeat 0 frei.
    """
    probe = _Probe(transport, portal, timings)
    try:
        return await probe.run()
    finally:
        await probe.close()


async def _discover_portal(transport: MqttTransport) -> str:
    """Die Portal-ID aus ``N/<portal>/system/0/Serial``, wie der Victron-Adapter sie ermittelt."""
    await transport.subscribe("N/+/system/0/Serial")
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(DISCOVERY_TIMEOUT_S):
            async for message in transport.messages():
                parts = message.topic.split("/")
                if len(parts) == 5 and parts[0] == "N" and parts[2:] == ["system", "0", "Serial"]:
                    return parts[1]
    raise ConnectionError("Portal-ID konnte nicht ermittelt werden")


async def _probe(cfg: Config, secrets: Secrets) -> list[Finding]:
    mqtt = cfg.victron.mqtt
    findings: list[Finding] = []
    try:
        async with AiomqttTransport(mqtt, secrets.mqtt_password) as transport:
            portal = mqtt.portal_id or await _discover_portal(transport)
            findings = await run_probe(transport, portal, timings_from_config(cfg))
    except Exception as exc:  # ein Befund statt eines Abbruchs
        if findings:  # die Probe ist durch; nur das Trennen schlug fehl
            print(f"Verbindung nicht sauber getrennt: {type(exc).__name__}: {exc}", file=sys.stderr)
        else:
            findings.append(_error_finding(exc))
    return findings


def _confirmed() -> bool:
    """Zeigt die Warnung und fragt nach. Nur ``j`` gilt als Ja, ein Eingabeende als Nein."""
    print(WARNING)
    try:
        answer = input("Fortfahren? [j/N] ")
    except EOFError:
        print()  # die Eingabezeile abschließen
        return False
    return answer.strip() == "j"


def _secrets_error(exc: ValidationError) -> str:
    """Fehlertext ohne Eingabewerte – diese könnten Secrets enthalten."""
    details = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors(include_input=False, include_url=False)
    )
    return f"Secrets ungültig: {details}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="watchdog_probe",
        description="Prüfprotokoll Teil 2, Punkt 5: löst den Watchdog am Cerbo absichtlich aus",
    )
    parser.add_argument("--config", type=Path, default=Path("/config/config.yaml"))
    parser.add_argument(
        "--out", type=Path, help="Bericht (Standard: docs/verification/<Datum>-watchdog-probe.md)"
    )
    parser.add_argument("--yes", action="store_true", help="ohne Rückfrage starten")
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except KeyboardInterrupt:  # Strg+C, auch an der Rückfrage; eine laufende Probe hat aufgeräumt
        print("\nAbgebrochen.")
        return 130


def _run(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not args.yes and not _confirmed():
        print("Abgebrochen.")
        return 1
    try:
        secrets = load_secrets()  # erst nach der Bestätigung: ein Abbruch braucht keine Secrets
    except ValidationError as exc:
        print(_secrets_error(exc), file=sys.stderr)
        return 2
    day = datetime.now(LOCAL_TZ).date().isoformat()
    out = args.out or Path("docs/verification") / f"{day}-watchdog-probe.md"
    print("Die Probe läuft und dauert einige Minuten.")
    findings = asyncio.run(_probe(cfg, secrets))
    meta = {"Werkzeug": "watchdog_probe", "Datum": day}
    report = render_report(findings, meta, title="Prüfprotokoll Teil 2 – Watchdog-Probe")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
    except OSError as exc:
        # Das Ergebnis darf nicht verloren gehen: Die Probe ist nur durch eine neue Auslösung
        # zu wiederholen.
        print(f"Bericht nicht geschrieben: {exc}", file=sys.stderr)
        print(report)
        return 1
    print(f"Bericht: {out}")
    return 0 if all(f.status != "fail" for f in findings) else 1


if __name__ == "__main__":
    sys.exit(main())
