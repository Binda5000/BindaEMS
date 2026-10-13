"""Gemeinsame Hilfen für die Tests der Laderegelung (nur für Tests, keine Testfunktionen).

Die Vorgaben sind die Annahmen aller Tests der Laderegelung; ein Test ändert nur, was er prüft:

- ``evcs``: die EVCS mit einem Fahrzeug, ``twc``: der Wall Connector ohne Fahrzeug
- ``tesla``: der Tesla mit Tessie, abgesteckt; ``NONE``: ein Fahrzeug ohne Tessie (der e-Golf)
- ``grid``: das Netz mit 2 A je Phase und Überschuss
- ``inputs``: die Eingaben eines Zyklus aus diesen Vorgaben, ``PRESENT`` und ``CONTROLLABLE``
- ``accept`` und ``run``: Urteile für die Befehle eines Schritts und Läufe über viele Sekunden
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from tests.helpers import T0

from bindaems.core.control.charge_inputs import (
    NO_VEHICLE_DATA,
    ChargeInputs,
    GridObs,
    VehicleObs,
    WallboxObs,
)
from bindaems.core.safety.types import Outcome, Verdict
from bindaems.core.state.derived import PHASES
from bindaems.shared.config import Config
from bindaems.shared.control import CommandKind, CommandValue, Intent

if TYPE_CHECKING:  # nur für die Typen: Die Tests der Zuordnung brauchen die Regelung nicht
    from bindaems.core.control.charging import ChargeCommand, ChargeController

NONE = NO_VEHICLE_DATA

# Ist-Werte der Aktoren: EVCS 6 A und Laden aus, Tesla 16 A und Laden aus
PRESENT: dict[tuple[str, CommandKind], CommandValue] = {
    ("wallbox.evcs", CommandKind.CURRENT): 6.0,
    ("wallbox.evcs", CommandKind.CHARGING): False,
    ("vehicle.tesla", CommandKind.CURRENT): 16.0,
    ("vehicle.tesla", CommandKind.CHARGING): False,
}
CONTROLLABLE: dict[str, bool] = {"wallbox.evcs": True, "vehicle.tesla": False}


def evcs(**kw: Any) -> WallboxObs:
    """Die EVCS: erreichbar und verbunden, lädt nicht, nicht geladen, 0 A je Phase, 0 W."""
    base = WallboxObs(True, True, False, False, dict.fromkeys(PHASES, 0.0), 0.0)
    return replace(base, **kw)


def twc(**kw: Any) -> WallboxObs:
    """Der Wall Connector: erreichbar, kein Fahrzeug, lädt nicht, ``complete`` unbekannt.

    Er misst 0 A je Phase und 0 W.
    """
    base = WallboxObs(True, False, False, None, dict.fromkeys(PHASES, 0.0), 0.0)
    return replace(base, **kw)


def tesla(**kw: Any) -> VehicleObs:
    """Der Tesla: erreichbar, nicht angesteckt, lädt nicht, nicht geladen, wach, zu Hause.

    Er zieht 0 A und hat 3 Phasen.
    """
    return replace(VehicleObs(True, False, False, False, False, True, 0.0, 3), **kw)


def grid(
    l1: float | None = 2.0,
    l2: float | None = 2.0,
    l3: float | None = 2.0,
    *,
    feed: tuple[float | None, float | None, float | None] = (0.0, 0.0, 0.0),
    grid_w: float | None = -4000.0,
    battery_w: float | None = 0.0,
    soc: float | None = 60.0,
) -> GridObs:
    """Das Netz: Netzstrom je Phase (``l1`` bis ``l3``), Akku-Einspeisung ``feed``, Überschuss."""
    return GridObs(
        phase_import_a=dict(zip(PHASES, (l1, l2, l3), strict=True)),
        battery_feed_a=dict(zip(PHASES, feed, strict=True)),
        grid_w=grid_w,
        battery_w=battery_w,
        soc_pct=soc,
    )


def inputs(
    cfg: Config,
    *,
    s: int = 0,
    grid_obs: GridObs | None = None,
    evcs_obs: WallboxObs | None = None,
    twc_obs: WallboxObs | None = None,
    tesla_obs: VehicleObs | None = None,
    present: Mapping[tuple[str, CommandKind], CommandValue] | None = None,
    controllable: Mapping[str, bool] | None = None,
    wakeable: Mapping[str, bool] | None = None,
    blocked: frozenset[tuple[str, CommandKind]] = frozenset(),
    intent: Intent | None = None,
    accepted_at: datetime | None = T0,
) -> ChargeInputs:
    """Die Eingaben eines Zyklus ``s`` Sekunden nach ``T0``, auf beiden Uhren.

    Ohne Angabe gelten ``grid()``, ``evcs()``, ``twc()``, ``tesla()`` und für den e-Golf ``NONE``,
    dazu ``PRESENT``, ``CONTROLLABLE``, keine Weckerlaubnis und ``Intent(version=1, control=True)``.
    ``cfg`` bleibt ungenutzt; es steht für die Form, die ``run`` von ``make`` erwartet.
    """
    when = T0 + timedelta(seconds=s)
    return ChargeInputs(
        now=when,
        wall_now=when,
        grid=grid() if grid_obs is None else grid_obs,
        wallboxes={
            "evcs": evcs() if evcs_obs is None else evcs_obs,
            "twc": twc() if twc_obs is None else twc_obs,
        },
        vehicles={"tesla": tesla() if tesla_obs is None else tesla_obs, "egolf": NONE},
        present=dict(PRESENT if present is None else present),
        controllable=dict(CONTROLLABLE if controllable is None else controllable),
        wakeable={} if wakeable is None else dict(wakeable),
        blocked=frozenset(blocked),
        intent=Intent(version=1, control=True) if intent is None else intent,
        accepted_at=accepted_at,
    )


def accept(
    controller: ChargeController,
    commands: Sequence[ChargeCommand],
    outcome: Outcome = Outcome.ACCEPTED,
) -> None:
    """Gibt jedem Befehl ein Dry-Run-Urteil ``outcome`` mit seinem Wert."""
    for found in commands:
        command = found.command
        controller.note_verdict(command, Verdict(command, outcome, command.value, (), True))


def run(
    controller: ChargeController,
    cfg: Config,
    seconds: int,
    *,
    make: Callable[..., ChargeInputs] = inputs,
    **kw: Any,
) -> list[tuple[int, ChargeCommand]]:
    """Ein Schritt je Sekunde ``0 … seconds − 1``; zurück kommen ``(Sekunde, Befehl)``.

    Jedes ``kw`` ist eine feste Eingabe oder eine Funktion der Sekunde; ``make(cfg, s=s, …)`` baut
    daraus die Eingaben. Alle Befehle werden angenommen (``accept``).
    """
    found: list[tuple[int, ChargeCommand]] = []
    for s in range(seconds):
        resolved = {key: value(s) if callable(value) else value for key, value in kw.items()}
        commands = controller.step(make(cfg, s=s, **resolved))
        accept(controller, commands)
        found.extend((s, command) for command in commands)
    return found
