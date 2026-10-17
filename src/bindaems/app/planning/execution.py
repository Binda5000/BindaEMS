"""Stand der Ausführung des Plans (Plan 3c, Entscheidung 10, Präzisierung 12).

Zeitplan und Home Assistant zeigen, was der core mit dem Plan macht, statt einer „Vorschau“. Die
Antwort liefert allein die app aus dem, was sie vom core weiß (``LiveState.health``); sie schreibt
nichts und stellt nichts um.

``core_has_plan`` ist der eine Vergleich, ob der core den Plan hat: ``PlanSender`` fragt damit, ob
er senden muss, und der Stand ``pending`` heißt genau, dass er es muss.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Literal

from bindaems.shared.plan import Plan

ExecutionState = Literal[
    "none", "unreachable", "pending", "off", "idle", "locked", "dry_run", "live"
]

# Gewöhnlich regelt der core in diesen Betriebszuständen (Spec 8: CONTROL, eingeschränkt DEGRADED)
REGULATING_MODES: Final = frozenset({"CONTROL", "DEGRADED"})
ACTUATOR: Final = "victron"
UNKNOWN_MODE: Final = "unbekannt"

# Texte je Stand, ``{mode}`` ist der Betriebszustand des core; ``none`` zeigt nichts.
EXECUTION_TEXTS: Final[Mapping[str, str]] = {
    "unreachable": "Der core ist nicht erreichbar; er führt den Plan nicht aus.",
    "pending": "Der Plan ist noch nicht beim core.",
    "off": "Regelbetrieb ist aus: Der core führt den Plan nicht aus.",
    "idle": "Der core regelt gerade nicht ({mode}).",
    "locked": "Victron ist gesperrt: Der core führt den Plan nicht aus.",
    "dry_run": "Dry-Run: Der core rechnet den Plan mit und sendet nichts an Victron.",
    "live": "Der core führt den Plan aus.",
}


def _intent(health: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    intent = health.get("intent") if health is not None else None
    return intent if isinstance(intent, dict) else None


def core_has_plan(
    health: Mapping[str, Any] | None, plan_id: int, grid_charge_allowed: bool
) -> bool:
    """Ob der Plan der Absicht im Health des core dieselbe ``id`` und dasselbe
    ``grid_charge_allowed`` trägt wie der gewollte (Präzisierung 11). Ohne Health ist es unbekannt
    und gilt als nein."""
    intent = _intent(health)
    held = intent.get("plan") if intent is not None else None
    if not isinstance(held, dict):
        return False
    return (held.get("id"), held.get("grid_charge_allowed")) == (plan_id, grid_charge_allowed)


def _victron(health: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    actuators = health.get("actuators") if health is not None else None
    if not isinstance(actuators, list):
        return None
    return next((a for a in actuators if isinstance(a, dict) and a.get("id") == ACTUATOR), None)


def execution_state(
    plan: Plan | None,
    *,
    valid: bool,
    connected: bool,
    health: Mapping[str, Any] | None,
    grid_charge_allowed: bool,
) -> ExecutionState:
    """Der erste zutreffende Stand: ``none`` (kein gültiger Plan), ``unreachable`` (nicht
    verbunden), ``pending`` (der core hat diesen Plan nicht), ``off`` (``intent.control`` falsch),
    ``idle`` (``mode`` weder ``CONTROL`` noch ``DEGRADED``), ``locked`` (``victron`` mit
    Sperrgründen), ``dry_run`` (wirksame Betriebsart von ``victron`` nicht ``live``), sonst
    ``live``. ``plan`` ist der gespeicherte Plan, ``valid`` ob er jetzt gilt."""
    if plan is None or not valid:
        return "none"
    if not connected:
        return "unreachable"
    if plan.id is None or not core_has_plan(health, plan.id, grid_charge_allowed):
        return "pending"
    intent = _intent(health)
    if intent is None or intent.get("control") is not True:
        return "off"
    if health is None or health.get("mode") not in REGULATING_MODES:
        return "idle"
    victron = _victron(health)
    if victron is not None and victron.get("locked"):
        return "locked"
    if victron is None or victron.get("effective") != "live":
        return "dry_run"
    return "live"


def execution_text(state: ExecutionState, health: Mapping[str, Any] | None) -> str | None:
    """Der Satz zum Stand; ``None`` bei ``none``. ``idle`` nennt den Betriebszustand aus
    ``health``."""
    template = EXECUTION_TEXTS.get(state)
    if template is None:
        return None
    mode = health.get("mode") if health is not None else None
    return template.format(mode=mode if isinstance(mode, str) and mode else UNKNOWN_MODE)
