"""Entscheidungen: Typ, JSON-Form und deutscher Text (Spec 7.10).

Jeder Befehl an einen Aktor endet in einer Entscheidung: gesendet, nicht gesendet, im Dry-Run nur
geprüft, abgelehnt oder wegen eines Stundenlimits zurückgestellt. Der core schreibt sie in sein Log
und sendet sie als Stream; die app liest sie später. Der Text ist deutsch und steht fest
(``decision_text``), damit Bedienung und Log dasselbe sagen.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from bindaems.shared.control import CommandKind, CommandValue
from bindaems.shared.numbers import de, finite_number
from bindaems.shared.timeutil import ensure_utc

_SHOWN_MAX: Final = 60  # Zeichen, die ein ungültiger Wert in einem Text einnimmt

# ``outcome`` einer Entscheidung, die kein Urteil der Safety-Schicht ist (eine fremde Änderung)
NO_VERDICT: Final = "none"

_FIELDS: Final = frozenset(
    {
        "id",
        "ts",
        "actuator",
        "kind",
        "requested",
        "applied",
        "dry_run",
        "outcome",
        "result",
        "codes",
        "text",
        "inputs",
    }
)

# Gründe, die der Text deutsch nennt; andere Codes erscheinen unverändert. Zuerst die des sicheren
# Zustands und der Übergabe, dann die der Laderegelung (Plan 2c-1, „Texte“), dann die des
# Akku-Sollwerts und des 15-min-Wächters (Plan 2c-2a, „Texte“), zuletzt der der Zuordnungserkennung
# (Plan 2c-2b, „Texte“), dazu die der Bedienung (Plan 2d, „Texte“) und die der Klimaanlage
# (Plan 6a, Präzisierung 5).
_REASON_TEXT: Final = {
    "safe_state": "sicherer Zustand",
    "handback": "Übergabe",
    "grid_outage": "Netzausfall",
    "mode_pv": "PV-Überschuss",
    "mode_min_pv": "Min+PV",
    "mode_now": "Sofort",
    "mode_off": "Aus",
    "mode_planned": "Geplant",
    "boost": "Boost",
    "pv_surplus": "Überschuss",
    "phase_limit": "Phasengrenze",
    "import_limit": "Importlimit",
    "reserve_guard": "Schutz der USV-Reserve",
    "overload": "Überlast",
    "wake": "Wecken",
    "not_taken_retry": "erneut nach „nicht übernommen“",
    "assignment_unclear": "Zuordnung unklar",
    "resume_after_outage": "Wiederaufnahme nach Ausfall",
    "battery_comp": "Akku nicht ins Auto",
    "battery_support": "Akku-Unterstützung",
    "peak_guard": "15-min-Wächter",
    "plan_hold": "Plan: Halten",
    "plan_grid_charge": "Plan: Netzladen",
    "assignment_detected": "Zuordnung erkannt",
    "self_start": "Selbststart",
    "foreign_change": "Änderung von außen",
    "plan_power": "Plan: Laden",
    "latest_start": "Späteste Startzeit",
    "target_reached": "Ziel-SOC erreicht",
    "charge_limit": "Ladelimit",
    "now_limit": "Sofort-Limit",
    "climate": "Vorklimatisieren",  # Plan Vorklimatisieren, Entscheidung 11
    "thermal_more": "PV-Überschuss in die Klimaanlage",
    "thermal_back": "Netzbezug, Klimaanlage eine Stufe zurück",
    "thermal_cars": "Autos haben Vorrang",
    "thermal_battery": "Akku hat Vorrang",
    "thermal_less": "Teurer Strom, Klimaanlage zurückhaltend",
    "thermal_peak": "Spitze droht, Klimaanlage nicht mehr",
    "thermal_mode": "Betriebsart gewechselt, Versatz 0",
    "thermal_stale": "Keine Werte der Klimaanlage, Versatz 0",
    "thermal_no_echo": "Klimaanlage nicht steuerbar (kein Echo oder gesperrt), Versatz 0",
    "thermal_off": "Klimaanlage aus oder lüftet, Versatz 0",
    # Plan 5c, Task 8: ``{k}`` ist der Versatz der Entscheidung (``_reason_text``)
    "thermal_plan": "Klimaanlage folgt dem Plan ({k} K)",
}
_K: Final = "{k}"
_WITHOUT_K: Final = " ({k} K)"


class DecisionResult(StrEnum):
    """Was aus dem Befehl geworden ist."""

    OK = "ok"  # gesendet und zurückgelesen
    ERROR = "error"  # beim Senden oder Rücklesen ein Fehler
    TIMEOUT = "timeout"  # gesendet, aber nicht rechtzeitig bestätigt
    NOT_SENT = "not_sent"  # hätte gesendet werden sollen, ging aber nicht (nicht steuerbar)
    DRY_RUN = "dry_run"  # der Aktor ist nicht live: geprüft, nicht gesendet
    REJECTED = "rejected"  # die Safety-Schicht hat den Befehl abgelehnt
    DEFERRED = "deferred"  # die Safety-Schicht hat ihn wegen eines Stundenlimits zurückgestellt
    FOREIGN = "foreign"  # ein fremdes System hat den Wert am Gerät geändert (kein Befehl des EMS)


_RESULT_TEXT: Final = {
    DecisionResult.OK: "gesendet und bestätigt",
    DecisionResult.TIMEOUT: "nicht bestätigt (Zeitüberschreitung)",
    DecisionResult.DRY_RUN: "Dry-Run, nicht gesendet",
    DecisionResult.REJECTED: "abgelehnt",
    DecisionResult.DEFERRED: "zurückgestellt",
}
# Diese Ergebnisse nennen ein Detail, falls es eins gibt: „Fehler: …“, „nicht gesendet: …“,
# „von außen geändert: …“.
_RESULT_WITH_DETAIL: Final = {
    DecisionResult.ERROR: "Fehler",
    DecisionResult.NOT_SENT: "nicht gesendet",
    DecisionResult.FOREIGN: "von außen geändert",
}


@dataclass(frozen=True)
class Decision:
    """Eine Entscheidung zu einem Befehl.

    - ``id``: Kennung; ``ts``: Zeitpunkt (Wanduhr, zeitzonenbewusst)
    - ``actuator`` und ``kind``: wohin und was
    - ``requested``: der Wert, den die Regelung anfordert; ``applied``: der Wert nach der
      Safety-Schicht (bei einer Ablehnung ``None``)
    - ``dry_run``: der Aktor war nicht live
    - ``outcome``: Urteil der Safety-Schicht (der Wert von ``Outcome``, bei einer fremden Änderung
      ``NO_VERDICT``); ``result``: Ergebnis
    - ``codes``: Gründe des Befehls, dann Befunde der Safety-Schicht
    - ``text``: die Entscheidung auf Deutsch (``decision_text``)
    - ``inputs``: Eingangsgrößen zum Zeitpunkt; ``None`` heißt unbekannt
    """

    id: str
    ts: datetime
    actuator: str
    kind: CommandKind
    requested: CommandValue
    applied: CommandValue
    dry_run: bool
    outcome: str
    result: DecisionResult
    codes: tuple[str, ...]
    text: str
    inputs: Mapping[str, float | None]


def _shown(value: object) -> str:
    """Die ``repr`` des Werts für einen Text, höchstens 60 Zeichen lang (sonst mit „…“)."""
    shown = repr(value)
    return shown if len(shown) <= _SHOWN_MAX else f"{shown[: _SHOWN_MAX - 1]}…"


def _json_value(value: object) -> float | bool | None:
    """Ein Befehlswert für JSON: Zahl, Wahrheitswert oder ``None``.

    Was JSON nicht halten kann (NaN, ±∞, ein zu großer ``int``) oder was keine Zahl ist, wird
    ``None``. Der Text der Entscheidung nennt es dann trotzdem.
    """
    if value is None or isinstance(value, bool):
        return value
    return finite_number(value)


def decision_to_json(decision: Decision) -> dict[str, Any]:
    """Die JSON-Form einer Entscheidung: ``ts`` als ISO-Zeit in UTC, Arten und Ergebnis als Text.

    Das Ergebnis ist reines JSON ohne NaN und ∞: Eine Zahl, die JSON nicht halten kann, wird
    ``None``, bei ``requested`` und ``applied`` ebenso wie in ``inputs`` (dort heißt ``None``
    unbekannt). Ein ``ts`` ohne Zeitzone ist ein Programmierfehler (``ValueError``).
    """
    return {
        "id": decision.id,
        "ts": ensure_utc(decision.ts).isoformat(),
        "actuator": decision.actuator,
        "kind": str(decision.kind),
        "requested": _json_value(decision.requested),
        "applied": _json_value(decision.applied),
        "dry_run": decision.dry_run,
        "outcome": decision.outcome,
        "result": str(decision.result),
        "codes": list(decision.codes),
        "text": decision.text,
        "inputs": {name: finite_number(value) for name, value in decision.inputs.items()},
    }


def _invalid(field: str, problem: str) -> ValueError:
    return ValueError(f"Entscheidung ungültig: „{field}“ {problem}")


def _string(data: dict[str, Any], field: str) -> str:
    value = data[field]
    if not isinstance(value, str):
        raise _invalid(field, f"ist keine Zeichenkette: {_shown(value)}")
    return value


def _timestamp(data: dict[str, Any]) -> datetime:
    """Die ISO-Zeit mit Zeitzone aus ``ts``, in UTC."""
    value = _string(data, "ts")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise _invalid("ts", f"ist keine ISO-Zeit: {_shown(value)}") from None
    if moment.utcoffset() is None:
        raise _invalid("ts", f"hat keine Zeitzone: {_shown(value)}")
    try:
        return moment.astimezone(UTC)
    except OverflowError:  # etwa Jahr 1 mit positivem Versatz: in UTC vor dem Jahr 1
        raise _invalid("ts", f"liegt außerhalb des Zeitbereichs: {_shown(value)}") from None


def _member[E: StrEnum](data: dict[str, Any], field: str, enum: type[E], what: str) -> E:
    """Das Mitglied von ``enum`` zum Text im Feld; ``what`` sagt, was es ist („eine …“)."""
    value = data[field]
    try:
        if isinstance(value, str):
            return enum(value)
    except ValueError:
        pass
    raise _invalid(field, f"ist {what}: {_shown(value)}")


def _command_value(data: dict[str, Any], field: str) -> CommandValue:
    value = data[field]
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    number = finite_number(value)
    if number is None:
        raise _invalid(field, f"ist weder Zahl noch Wahrheitswert noch null: {_shown(value)}")
    return number


def _flag(data: dict[str, Any], field: str) -> bool:
    value = data[field]
    if not isinstance(value, bool):
        raise _invalid(field, f"ist kein Wahrheitswert: {_shown(value)}")
    return value


def _codes(data: dict[str, Any]) -> tuple[str, ...]:
    value = data["codes"]
    if not isinstance(value, list) or not all(isinstance(code, str) for code in value):
        raise _invalid("codes", f"ist keine Liste von Zeichenketten: {_shown(value)}")
    return tuple(value)


def _inputs(data: dict[str, Any]) -> dict[str, float | None]:
    value = data["inputs"]
    if not isinstance(value, dict):
        raise _invalid("inputs", f"ist kein Objekt: {_shown(value)}")
    inputs: dict[str, float | None] = {}
    for name, item in value.items():
        if not isinstance(name, str):
            raise _invalid("inputs", f"hat einen Namen, der keine Zeichenkette ist: {_shown(name)}")
        number = finite_number(item)
        if item is not None and number is None:
            raise _invalid("inputs", f"enthält für „{name}“ keine Zahl: {_shown(item)}")
        inputs[name] = number
    return inputs


def decision_from_json(data: Any) -> Decision:
    """Liest eine Entscheidung aus ihrer JSON-Form (``decision_to_json``).

    Ein ``ValueError`` mit deutschem Text, der das Feld nennt, wenn die Form abweicht: kein Objekt,
    fehlende oder fremde Felder, ein Wert vom falschen Typ, eine unbekannte Art oder ein
    unbekanntes Ergebnis, eine Zeit ohne Zeitzone oder keine ISO-Zeit. Zahlen werden ``float``,
    ``ts`` ist in UTC.
    """
    if not isinstance(data, dict):
        raise ValueError(f"Entscheidung ungültig: kein Objekt, sondern {_shown(data)}")
    if data.keys() != _FIELDS:
        missing = sorted(_FIELDS - data.keys())
        foreign = sorted(str(key) for key in data.keys() - _FIELDS)
        raise ValueError(
            f"Entscheidung ungültig: Felder weichen ab (fehlend: {missing}, fremd: {foreign})"
        )
    return Decision(
        id=_string(data, "id"),
        ts=_timestamp(data),
        actuator=_string(data, "actuator"),
        kind=_member(data, "kind", CommandKind, "keine bekannte Befehlsart"),
        requested=_command_value(data, "requested"),
        applied=_command_value(data, "applied"),
        dry_run=_flag(data, "dry_run"),
        outcome=_string(data, "outcome"),
        result=_member(data, "result", DecisionResult, "kein bekanntes Ergebnis"),
        codes=_codes(data),
        text=_string(data, "text"),
        inputs=_inputs(data),
    )


def _command_text(kind: CommandKind, value: CommandValue) -> str:
    """Der Befehl als Text. Ein Wert, der nicht zur Art passt, erscheint, wie er ist (gekürzt)."""
    number = finite_number(value)
    shown = de(number) if number is not None else _shown(value)
    if kind == CommandKind.CURRENT:
        return f"Strom {shown} A"
    if kind == CommandKind.CHARGING:
        if isinstance(value, bool):
            return "Laden an" if value else "Laden aus"
        return f"Laden {shown}"
    if kind == CommandKind.GRID_SETPOINT:
        return "Netz-Sollwert-Override aufheben" if value is None else f"Netz-Sollwert {shown} W"
    if kind == CommandKind.MAX_DISCHARGE:
        return "Entladegrenze unbegrenzt" if number == -1 else f"Entladegrenze {shown} W"
    if kind == CommandKind.MIN_SOC:
        return f"Min-SOC {shown} %"
    if kind == CommandKind.CHARGE_LIMIT:
        return f"Ladelimit {shown} %"
    if kind == CommandKind.SETPOINT_OFFSET:
        return f"Sollwertversatz {_offset(number) if number is not None else shown} K"
    return f"{kind} {shown}"


def _offset(value: float) -> str:
    """Ein Versatz mit Vorzeichen und einer Nachkommastelle: „+0,5“, „−1,0“, „0,0“."""
    if round(value, 1) == 0:
        return "0,0"
    return f"{value:+.1f}".replace(".", ",").replace("-", "\N{MINUS SIGN}")


def _reason_text(code: str, kind: CommandKind, value: CommandValue) -> str:
    """Der Text des Grunds ``code``; ``{k}`` darin ist der Versatz der Entscheidung wie in
    ``_offset`` (ohne endliche Zahl oder bei einer anderen Art entfällt die Klammer mit ihm)."""
    text = _REASON_TEXT.get(code, code)
    if _K not in text:
        return text
    number = finite_number(value)
    if kind != CommandKind.SETPOINT_OFFSET or number is None:
        return text.replace(_WITHOUT_K, "").replace(_K, "")
    return text.replace(_K, _offset(number))


def _result_text(result: DecisionResult, detail: str) -> str:
    """Das Ergebnis als Text; bei Fehler, „nicht gesendet“ und fremder Änderung mit dem Detail."""
    if result in _RESULT_WITH_DETAIL:
        text = _RESULT_WITH_DETAIL[result]
        return f"{text}: {detail}" if detail else text
    return _RESULT_TEXT.get(result, str(result))


def decision_text(
    label: str,
    kind: CommandKind,
    value: CommandValue,
    result: DecisionResult,
    *,
    detail: str = "",
    reasons: Sequence[str] = (),
    findings: Sequence[str] = (),
) -> str:
    """Der Text einer Entscheidung: ``{label}{ (Gründe)}: {Befehl} – {Ergebnis}.``

    Danach folgen, mit Leerzeichen verbunden, die Befundtexte der Safety-Schicht. ``value`` ist der
    angewendete Wert, sonst der angeforderte. ``reasons`` sind Codes: die des sicheren Zustands,
    der Übergabe, der Laderegelung, des Akku-Sollwerts, des 15-min-Wächters, der
    Zuordnungserkennung, der Bedienung und der Klimaanlage (``_REASON_TEXT``) erscheinen deutsch,
    andere unverändert; mehrere stehen mit „, “ getrennt. ``detail`` gehört zu „Fehler“, „nicht
    gesendet“ und „von außen geändert“. Der Satz endet auf einen Punkt; hat das Detail schon
    einen, kommt kein zweiter dazu. Ein Wert, der nicht zur Art passt (etwa NaN), erscheint, wie
    er ist: Auch eine abgelehnte, ungültige Anfrage lässt sich protokollieren.
    """
    head = label
    if reasons:
        head += f" ({', '.join(_reason_text(code, kind, value) for code in reasons)})"
    sentence = f"{head}: {_command_text(kind, value)} – {_result_text(result, detail)}"
    if not sentence.endswith("."):
        sentence += "."
    return " ".join([sentence, *(finding for finding in findings if finding)])
