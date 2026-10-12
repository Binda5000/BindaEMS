"""Sicherer Zustand und Übergabe: Sicherwerte mit höchstens 3 Versuchen je Vorfall (Spec 8.4, 8.5).

Die Ziele (``SafeTarget``) nennt die Laufzeit (Plan 2b, Präzisierungen 3 und 4): in SAFE jeden
nicht gesperrten Aktor, in jedem Zustand, in dem der core schreibt, dazu jeden Aktor, der übergeben
wird. Die Sicherwerte kommen aus ``HardLimits.safe_commands``; bei Netzausfall (Inselbetrieb)
bekommen EVCS und Tesla zusätzlich „Laden aus“ (8.4).

``SafeStateKeeper.commands`` liefert je Zyklus die dringenden Befehle dafür:

- Ein wirksam live geschaltetes Ziel bekommt je Art einen Befehl, wenn der zurückgelesene Wert
  nicht der Sicherwert ist, kein Schreibvorgang offen ist und der letzte Versuch mindestens 10 s
  her ist. Je Vorfall gibt es höchstens 3 Versuche. Danach ist die Art gescheitert (``failed``,
  Alarm ``actuator.failed.<aktor>``), bis der Wert stimmt.
- Ein Dry-Run-Ziel bekommt je Art einmal je Vorfall einen Befehl; die Safety-Schicht macht daraus
  die Entscheidung „Dry-Run“. Das zählt nicht als Versuch.
- Ein Vorfall endet, sobald der Aktor kein Ziel mehr ist; der nächste beginnt mit neuen Versuchen.

Die Befehle gehen wie jeder andere durch die Safety-Schicht. Die Schicht ist rein: Zeit (die Uhr
der Safety-Schicht), Ist-Werte und offene Schreibvorgänge kommen als Argumente.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from bindaems.core.control.actuator import matches
from bindaems.core.safety.limits import HardLimits
from bindaems.shared.control import Command, CommandKind, CommandValue
from bindaems.shared.domain import Alarm, Severity

SAFE_RETRY_S: Final = 10.0  # Mindestabstand zweier Versuche je Aktor und Art
SAFE_ATTEMPTS: Final = 3  # höchstens so viele Live-Versuche je Vorfall, Aktor und Art

_RETRY: Final = timedelta(seconds=SAFE_RETRY_S)
_GRID_OUTAGE: Final = "grid_outage"  # Grund des Stopps bei Netzausfall
_STOP_FAMILIES: Final = frozenset({"evcs", "tesla"})  # sie stoppen das Laden bei Netzausfall
_WATCHDOG_TAKES_OVER: Final = " Der Watchdog am Cerbo übernimmt."

_Key = tuple[str, CommandKind]  # Aktor und Art
_Wanted = dict[CommandKind, tuple[CommandValue, tuple[str, ...]]]  # Sicherwert und Grund je Art


@dataclass(frozen=True)
class SafeTarget:
    """Ein Aktor, der seine Sicherwerte bekommen soll.

    ``reason`` ist der Grund der Befehle: ``safe_state`` im sicheren Zustand, ``handback`` bei der
    Übergabe. ``stop_charging`` steht für einen Netzausfall: EVCS und Tesla bekommen dann
    zusätzlich „Laden aus“ mit dem Grund ``grid_outage``.
    """

    actuator: str
    reason: Literal["safe_state", "handback"]
    stop_charging: bool = False


def safe_values(limits: HardLimits, target: SafeTarget) -> dict[CommandKind, CommandValue]:
    """Die Sicherwerte des Ziels je Art, in der Reihenfolge der Befehle.

    Das sind die Werte aus ``limits.safe_commands``; mit ``stop_charging`` kommt bei EVCS und Tesla
    ``CHARGING: False`` dazu. Für etwas, das kein Aktor ist, gibt es keine.
    """
    return {kind: value for kind, (value, _) in _wanted(limits, target).items()}


def safe_values_reached(
    limits: HardLimits, target: SafeTarget, present: Mapping[_Key, CommandValue]
) -> bool:
    """Hat das Gerät alle Sicherwerte des Ziels? Jede Art muss per ``matches`` passen.

    ``present`` sind die zurückgelesenen Werte je Aktor und Art. Ein unbekannter Ist-Wert (der
    Eintrag fehlt) passt nie; beim Netz-Sollwert ist ``None`` dagegen ein Wert („kein Override“).
    """
    return all(
        _reached(present, (target.actuator, kind), value)
        for kind, value in safe_values(limits, target).items()
    )


@dataclass
class _Attempts:
    """Stand einer Art im laufenden Vorfall."""

    live: int = 0  # gesendete Live-Versuche
    last: datetime | None = None  # Zeitpunkt des letzten Live-Versuchs
    dry_run: bool = False  # der Befehl im Dry-Run ist gegeben


class SafeStateKeeper:
    """Bringt die Ziele in den sicheren Zustand und merkt sich je Vorfall die Versuche.

    Die Laufzeit ruft ``commands`` einmal je Zyklus auf, auch ohne Ziele, denn das beendet die
    Vorfälle. Sie gibt jeden Befehl an den Ausführer. Nicht für mehrere Threads gedacht.
    """

    def __init__(self, limits: HardLimits) -> None:
        self._limits = limits
        self._attempts: dict[_Key, _Attempts] = {}
        self._failed: dict[str, tuple[CommandKind, ...]] = {}  # je Aktor, in Reihenfolge der Ziele
        self._failed_since: dict[str, datetime] = {}  # erste gescheiterte Art im Vorfall

    @property
    def failed(self) -> frozenset[_Key]:
        """Die gescheiterten Arten nach dem letzten ``commands``, als (Aktor, Art).

        Gescheitert ist eine Art eines wirksam live geschalteten Ziels, wenn die 3 Versuche
        verbraucht sind, der Wert nicht stimmt, kein Schreibvorgang offen ist und der letzte
        Versuch mindestens 10 s her ist.
        """
        return frozenset(
            (actuator, kind) for actuator, kinds in self._failed.items() for kind in kinds
        )

    def commands(
        self,
        now: datetime,
        targets: Sequence[SafeTarget],
        present: Mapping[_Key, CommandValue],
        pending: AbstractSet[_Key],
        live: AbstractSet[str],
    ) -> list[Command]:
        """Die dringenden Befehle für die Sicherwerte der ``targets`` zum Zeitpunkt ``now``.

        ``present`` sind die zurückgelesenen Werte, ``pending`` die offenen Schreibvorgänge des
        Ausführers und ``live`` die wirksam live geschalteten Aktoren. Je Ziel und Art gilt:

        - Stimmt der Wert oder ist ein Schreibvorgang offen, gibt es keinen Befehl.
        - Dry-Run: ein Befehl je Vorfall.
        - Live: ein Befehl, wenn der letzte Versuch mindestens 10 s her ist und noch nicht alle
          3 Versuche verbraucht sind. Ein Treffer setzt die Versuche nicht zurück. Ein Aktor, der
          im Vorfall live wird, beginnt bei 0, denn Dry-Run-Befehle zählen nicht.

        Die Befehle kommen in der Reihenfolge der Ziele und ihrer Sicherwerte, mit dem Grund des
        Ziels; der Stopp bei Netzausfall mit ``grid_outage``. Ein Aktor, der zweimal als Ziel
        vorkommt, bekommt je Art höchstens einen Befehl, mit dem Grund des ersten Ziels. Für einen
        Aktor, der kein Ziel mehr ist, endet der Vorfall: Versuche und Fehler sind vergessen.
        """
        current = {target.actuator for target in targets}
        self._attempts = {key: state for key, state in self._attempts.items() if key[0] in current}
        self._failed_since = {a: ts for a, ts in self._failed_since.items() if a in current}
        failed: dict[str, list[CommandKind]] = {}
        commands: list[Command] = []
        for target in targets:
            actuator = target.actuator
            for kind, (value, reasons) in _wanted(self._limits, target).items():
                key = (actuator, kind)
                state = self._attempts.setdefault(key, _Attempts())
                if _reached(present, key, value) or key in pending:
                    continue
                if actuator not in live:
                    if not state.dry_run:
                        state.dry_run = True
                        commands.append(_command(key, value, reasons))
                    continue
                if state.last is not None and now - state.last < _RETRY:
                    continue
                if state.live >= SAFE_ATTEMPTS:
                    kinds = failed.setdefault(actuator, [])
                    if kind not in kinds:
                        kinds.append(kind)
                    continue
                state.live += 1
                state.last = now
                commands.append(_command(key, value, reasons))
        self._failed = {actuator: tuple(kinds) for actuator, kinds in failed.items()}
        for actuator in self._failed:
            self._failed_since.setdefault(actuator, now)
        return commands

    def alarms(
        self, now: datetime, labels: Mapping[str, str], needs_watchdog: AbstractSet[str]
    ) -> list[Alarm]:
        """Je Aktor mit gescheiterten Arten der Alarm ``actuator.failed.<aktor>`` (Fehler).

        Der Stand ist der des letzten ``commands``; ``now`` nimmt die Methode wie die anderen
        Quellen von Alarmen, der Stand hängt aber nicht davon ab. ``since`` ist der Zeitpunkt, zu
        dem der Aktor in diesem Vorfall zum ersten Mal eine gescheiterte Art hatte.

        Der Text nennt die Arten in der Reihenfolge der Sicherwerte, mehrere als „„a“, „b““. Steht
        der Aktor in ``needs_watchdog`` (Victron, EVCS), folgt „Der Watchdog am Cerbo übernimmt.“
        Ohne Eintrag in ``labels`` steht die Aktor-ID im Text.
        """
        alarms: list[Alarm] = []
        for actuator, kinds in self._failed.items():
            names = ", ".join(f"„{kind}“" for kind in kinds)
            message = f"{labels.get(actuator, actuator)}: Sicherwert für {names} nicht bestätigt."
            if actuator in needs_watchdog:
                message += _WATCHDOG_TAKES_OVER
            since = self._failed_since[actuator]
            alarms.append(Alarm(f"actuator.failed.{actuator}", Severity.ERROR, message, since))
        return alarms


def _wanted(limits: HardLimits, target: SafeTarget) -> _Wanted:
    """Sicherwert und Grund je Art: ``safe_commands`` mit dem Grund des Ziels, dazu der Stopp."""
    reason = (target.reason,)
    wanted: _Wanted = {
        command.kind: (command.value, reason) for command in limits.safe_commands(target.actuator)
    }
    spec = limits.actuators.get(target.actuator)
    if target.stop_charging and spec is not None and spec.family in _STOP_FAMILIES:
        wanted[CommandKind.CHARGING] = (False, (_GRID_OUTAGE,))
    return wanted


def _reached(present: Mapping[_Key, CommandValue], key: _Key, value: CommandValue) -> bool:
    """Stimmt der zurückgelesene Wert mit dem Sicherwert überein? Fehlt er, ist er unbekannt."""
    return key in present and matches(key[1], value, present[key])


def _command(key: _Key, value: CommandValue, reasons: tuple[str, ...]) -> Command:
    """Ein dringender Befehl für den Sicherwert."""
    return Command(key[0], key[1], value, urgent=True, reasons=reasons)
