"""Betriebscheck: eine Ampel aus Health des core, Bausteinen der app und Entscheidungsraten.

Rein: kein I/O, keine Uhr außer ``now``. Die Prüfungen laufen in dieser Reihenfolge, IDs fest:

- ``core``: verbunden (ok) oder nicht erreichbar (error); ohne core sind alle Prüfungen aus Health
  und die Raten ``unknown``, die Aktoren fehlen dann (ihre IDs kennt nur Health)
- ``mode``: CONTROL ok; OBSERVE, DEGRADED warn; SAFE error; START unknown
- ``actuator.<id>``: wirksam wie gewünscht ok; live gewünscht, aber nicht wirksam, gesperrt error
- ``watchdog``: nicht nötig oder scharf ok; Version abweichend warn; nötig und nicht scharf oder
  ausgelöst error
- ``alarms``: keine ok; Schwere warning warn; Schwere error error
- ``assignment``: unklare Zuordnung an einer Wallbox warn
- ``tesla_commands``, ``evcs_rate``, ``victron_rate``: Befehle bzw. Entscheidungen je Stunde über
  der Grenze warn
- ``plan``: valid ok; expired, none warn
- ``battery``: ok, idle ok; unknown warn; locked error
- ``inputs``, ``adapters``, ``selfcheck``, ``app``: Probleme warn, ``selfcheck`` mit ``fail`` error

Gesamtstufe: error vor warn vor ok. ``unknown`` senkt sie nicht, außer der core ist verbunden und
Health fehlt noch ganz: dann ist sie ``unknown``. Fehlt ein Feld in Health (älterer core), ist die
Prüfung ``unknown`` mit „vom core nicht gemeldet“.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from bindaems.app.status import ComponentStatus

Level = Literal["ok", "warn", "error", "unknown"]

# Grenzen der Befehle und Entscheidungen je Stunde. Die Reserve der Tessie-Befehle gehört zu B2,
# die Grenzen der Entscheidungsraten (Hysterese der Wallbox, Totband des Victron) zu B6.
TESLA_COMMANDS_WARN = 16
EVCS_PER_HOUR_WARN = 20
VICTRON_PER_HOUR_WARN = 60

_CORE_DOWN = "unbekannt, core nicht erreichbar"
_NO_DATA = "noch keine Daten vom core"
_NOT_REPORTED = "vom core nicht gemeldet"
_EVCS = "wallbox.evcs"
_VICTRON = "victron"


@dataclass(frozen=True)
class CheckItem:
    id: str
    label: str
    level: Level
    text: str
    link: str | None = None


@dataclass(frozen=True)
class OpsCheck:
    level: Level
    items: tuple[CheckItem, ...]
    checked_at: datetime


@dataclass(frozen=True)
class DecisionRates:
    """Entscheidungen der letzten 60 min je Aktor, Dry-Run nicht gefiltert."""

    per_actuator_hour: Mapping[str, int]


def _unknown(id_: str, label: str, text: str = _NOT_REPORTED) -> CheckItem:
    return CheckItem(id_, label, "unknown", text)


def _join(parts: Sequence[str]) -> str:
    return " ".join(part for part in parts if part)


def _core(connected: bool) -> CheckItem:
    if connected:
        return CheckItem("core", "core", "ok", "core verbunden")
    return CheckItem("core", "core", "error", "core nicht erreichbar")


def _mode(health: Mapping[str, Any]) -> CheckItem:
    mode = health.get("mode")
    if mode is None:
        return _unknown("mode", "Betriebszustand")
    reasons = " ".join(str(r) for r in health.get("mode_reasons") or [])
    if mode == "CONTROL":
        return CheckItem("mode", "Betriebszustand", "ok", "CONTROL")
    if mode == "OBSERVE":
        return CheckItem(
            "mode", "Betriebszustand", "warn", _join(["OBSERVE: Regelbetrieb ist aus.", reasons])
        )
    if mode == "DEGRADED":
        return CheckItem("mode", "Betriebszustand", "warn", _join(["DEGRADED:", reasons]))
    if mode == "SAFE":
        return CheckItem("mode", "Betriebszustand", "error", _join(["SAFE:", reasons]))
    return _unknown("mode", "Betriebszustand", "START: der core fährt noch hoch")


def _actuator(actuator: Mapping[str, Any]) -> CheckItem:
    id_ = f"actuator.{actuator.get('id')}"
    label = str(actuator.get("label") or actuator.get("id"))
    requested, effective = actuator.get("requested"), actuator.get("effective")
    locked = [str(r) for r in actuator.get("locked") or []]
    if locked:
        return CheckItem(id_, label, "error", _join(["gesperrt:", *locked]))
    if requested == "live" and effective != "live":
        reasons = [str(r) for r in actuator.get("reasons") or []]
        return CheckItem(
            id_, label, "error", _join(["live gewünscht, läuft aber im Dry-Run.", *reasons])
        )
    if requested is None or effective is None:
        return _unknown(id_, label)
    if effective == "live":
        return CheckItem(id_, label, "ok", "live")
    return CheckItem(id_, label, "ok", "Dry-Run (so gewünscht)")


def _watchdog(health: Mapping[str, Any]) -> CheckItem:
    dog = health.get("watchdog")
    if not isinstance(dog, Mapping):
        return _unknown("watchdog", "Watchdog")
    required, armed, state = dog.get("required"), dog.get("armed"), dog.get("state")
    if state == "tripped":
        return CheckItem("watchdog", "Watchdog", "error", "Watchdog hat ausgelöst")
    if required and not (armed and state == "armed"):
        return CheckItem("watchdog", "Watchdog", "error", "Watchdog nötig, aber nicht scharf")
    version, expected = dog.get("version"), dog.get("expected_version")
    if version is not None and expected is not None and version != expected:
        text = f"Version {version} statt {expected}"
        return CheckItem("watchdog", "Watchdog", "warn", text)
    return CheckItem("watchdog", "Watchdog", "ok", "scharf" if required else "nicht nötig")


def _alarms(health: Mapping[str, Any]) -> CheckItem:
    alarms = health.get("alarms")
    if alarms is None:
        return _unknown("alarms", "Alarme")
    for severity, level in (("error", "error"), ("warning", "warn")):
        hits = [a for a in alarms if a.get("severity") == severity]
        if hits:
            count = f"{len(hits)} Alarm{'e' if len(hits) > 1 else ''}"
            text = f"{count}, zuerst: {hits[0].get('message')}"
            return CheckItem("alarms", "Alarme", level, text)  # type: ignore[arg-type]
    return CheckItem("alarms", "Alarme", "ok", "keine Alarme")


def _assignment(health: Mapping[str, Any]) -> CheckItem:
    wallboxes = health.get("wallboxes")
    if wallboxes is None:
        return _unknown("assignment", "Zuordnung")
    unclear = [str(w.get("id")) for w in wallboxes if w.get("assignment") == "unclear"]
    if unclear:
        text = f"Zuordnung unklar an {', '.join(unclear)}"
        return CheckItem("assignment", "Zuordnung", "warn", text, "/laden")
    return CheckItem("assignment", "Zuordnung", "ok", "alle Zuordnungen klar")


def _tesla(health: Mapping[str, Any]) -> CheckItem:
    vehicles = health.get("vehicles")
    if vehicles is None:
        return _unknown("tesla_commands", "Tesla-Befehle")
    over = [
        f"{v.get('name')} ({int(v['commands_hour'])})"
        for v in vehicles
        if v.get("commands_hour") is not None and v["commands_hour"] > TESLA_COMMANDS_WARN
    ]
    if over:
        text = f"über {TESLA_COMMANDS_WARN} Befehle je Stunde: {', '.join(over)}; Reserve greift"
        return CheckItem("tesla_commands", "Tesla-Befehle", "warn", text)
    return CheckItem(
        "tesla_commands", "Tesla-Befehle", "ok", f"höchstens {TESLA_COMMANDS_WARN} je Stunde"
    )


def _rate(
    rates: DecisionRates | None, *, id_: str, label: str, actuator: str, limit: int, hint: str
) -> CheckItem:
    if rates is None:
        return _unknown(id_, label, "Entscheidungen nicht verfügbar")
    count = rates.per_actuator_hour.get(actuator, 0)
    if count > limit:
        link = f"/entscheidungen?range=hour&actuator={actuator}"
        return CheckItem(id_, label, "warn", f"{count} Entscheidungen je Stunde, {hint}", link)
    return CheckItem(id_, label, "ok", f"{count} Entscheidungen je Stunde")


def _plan(health: Mapping[str, Any]) -> CheckItem:
    plan = health.get("plan")
    state = plan.get("state") if isinstance(plan, Mapping) else None
    if state is None:
        return _unknown("plan", "Plan")
    if state == "valid":
        return CheckItem("plan", "Plan", "ok", "Plan gilt")
    text = "Plan abgelaufen" if state == "expired" else "kein Plan"
    return CheckItem("plan", "Plan", "warn", text)


def _battery(health: Mapping[str, Any]) -> CheckItem:
    battery = health.get("battery")
    state = battery.get("state") if isinstance(battery, Mapping) else None
    if state is None:
        return _unknown("battery", "Akku-Sollwert")
    if state == "locked":
        return CheckItem("battery", "Akku-Sollwert", "error", "Akku-Sollwert gesperrt")
    if state == "unknown":
        return CheckItem("battery", "Akku-Sollwert", "warn", "Akku-Stand unbekannt")
    return CheckItem("battery", "Akku-Sollwert", "ok", "in Ordnung" if state == "ok" else "ruht")


def _inputs(health: Mapping[str, Any]) -> CheckItem:
    inputs = health.get("inputs")
    if not isinstance(inputs, Mapping) or "ok" not in inputs:
        return _unknown("inputs", "Eingänge")
    if inputs["ok"]:
        return CheckItem("inputs", "Eingänge", "ok", "alle Eingänge in Ordnung")
    problems = [str(p) for p in inputs.get("problems") or []][:2]
    return CheckItem("inputs", "Eingänge", "warn", "; ".join(problems) or "Eingänge gestört")


def _adapters(health: Mapping[str, Any]) -> CheckItem:
    adapters = health.get("adapters")
    if adapters is None:
        return _unknown("adapters", "Adapter")
    down = [str(a.get("name")) for a in adapters if not a.get("connected")]
    if down:
        return CheckItem("adapters", "Adapter", "warn", f"getrennt: {', '.join(down)}")
    return CheckItem("adapters", "Adapter", "ok", "alle verbunden")


def _selfcheck(health: Mapping[str, Any]) -> CheckItem:
    results = health.get("selfcheck")
    if results is None:
        return _unknown("selfcheck", "Selbsttest")
    for status, level in (("fail", "error"), ("warn", "warn")):
        hits = [r for r in results if r.get("status") == status]
        if hits:
            text = "; ".join(f"{r.get('id')}: {r.get('message')}" for r in hits[:2])
            return CheckItem("selfcheck", "Selbsttest", level, text)  # type: ignore[arg-type]
    return CheckItem("selfcheck", "Selbsttest", "ok", "bestanden")


def _app(components: Sequence[ComponentStatus]) -> CheckItem:
    bad = [c.name for c in components if not c.ok]
    if bad:
        return CheckItem("app", "app-Bausteine", "warn", f"gestört: {', '.join(bad)}")
    return CheckItem("app", "app-Bausteine", "ok", "alle in Ordnung")


_HEALTH_CHECKS: tuple[Callable[[Mapping[str, Any]], CheckItem], ...] = (
    _watchdog,
    _alarms,
    _assignment,
    _tesla,
)
_LATER_CHECKS: tuple[Callable[[Mapping[str, Any]], CheckItem], ...] = (
    _plan,
    _battery,
    _inputs,
    _adapters,
    _selfcheck,
)
_PLACEHOLDERS: tuple[tuple[str, str], ...] = (
    ("mode", "Betriebszustand"),
    ("watchdog", "Watchdog"),
    ("alarms", "Alarme"),
    ("assignment", "Zuordnung"),
    ("tesla_commands", "Tesla-Befehle"),
    ("evcs_rate", "Wallbox-Entscheidungen"),
    ("victron_rate", "Victron-Entscheidungen"),
    ("plan", "Plan"),
    ("battery", "Akku-Sollwert"),
    ("inputs", "Eingänge"),
    ("adapters", "Adapter"),
    ("selfcheck", "Selbsttest"),
)


def _rates(rates: DecisionRates | None) -> tuple[CheckItem, CheckItem]:
    evcs = _rate(
        rates,
        id_="evcs_rate",
        label="Wallbox-Entscheidungen",
        actuator=_EVCS,
        limit=EVCS_PER_HOUR_WARN,
        hint="Hysterese prüfen",
    )
    victron = _rate(
        rates,
        id_="victron_rate",
        label="Victron-Entscheidungen",
        actuator=_VICTRON,
        limit=VICTRON_PER_HOUR_WARN,
        hint="Totband prüfen",
    )
    return evcs, victron


def _overall(items: Sequence[CheckItem], *, no_data: bool) -> Level:
    if no_data:
        return "unknown"
    levels = {item.level for item in items}
    if "error" in levels:
        return "error"
    return "warn" if "warn" in levels else "ok"


def operations_check(
    *,
    connected: bool,
    health: Mapping[str, Any] | None,
    components: Sequence[ComponentStatus],
    rates: DecisionRates | None,
    now: datetime,
) -> OpsCheck:
    """Der Betriebscheck. ``health`` zählt nur bei verbundenem core."""
    items: list[CheckItem] = [_core(connected)]
    if not connected or health is None:
        text = _CORE_DOWN if not connected else _NO_DATA
        items.extend(_unknown(id_, label, text) for id_, label in _PLACEHOLDERS)
    else:
        items.append(_mode(health))
        actuators = health.get("actuators")
        if actuators is None:
            items.append(_unknown("actuators", "Aktoren"))
        else:
            items.extend(_actuator(a) for a in actuators)
        items.extend(check(health) for check in _HEALTH_CHECKS)
        items.extend(_rates(rates))
        items.extend(check(health) for check in _LATER_CHECKS)
    items.append(_app(components))
    no_data = connected and health is None
    return OpsCheck(_overall(items, no_data=no_data), tuple(items), now)


def check_json(check: OpsCheck) -> dict[str, Any]:
    return {
        "level": check.level,
        "checked_at": check.checked_at.isoformat(),
        "items": [
            {
                "id": item.id,
                "label": item.label,
                "level": item.level,
                "text": item.text,
                "link": item.link,
            }
            for item in check.items
        ],
    }
