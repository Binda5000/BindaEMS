"""Auffälligkeiten eines lokalen Tages: Regeln über Abrechnung, Pläne, Akku-SOC und Befehle
(Betrieb im Blick).

Reine Rechnung ohne Datenzugriff; der Aufrufer liest die Eingaben (``DayInputs``) und ruft
``find``. Leistungen sind Mittelwerte der Viertelstunde (kW = Wh · 4 / 1000). Zeiten in den
Texten sind lokal (``HH:MM``), in ``Finding.start``/``end`` UTC.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Literal

from bindaems.app.ledger.service import LedgerSlot
from bindaems.app.planning.review import in_force
from bindaems.shared.plan import Plan
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, ensure_utc, local_day_slots

Rule = Literal[
    "grid_charge_unplanned",
    "below_reserve",
    "month_peak",
    "plan_deviation",
    "target_missed",
    "command_failures",
    "counter_check",
    "not_checked",
]

GRID_CHARGE_MIN_WH: Final = 250.0  # 1 kW im Mittel einer Viertelstunde
RESERVE_MARGIN_PCT: Final = 2.0  # Messrauschen: erst darunter gilt „unter der Reserve“
VICTRON_RECHARGE_HINT_PCT: Final = 3.0  # so weit unter der Reserve lädt Victron selbst nach
PEAK_RISE_MIN_KW: Final = 0.5
DEVIATION_MIN_KWH: Final = 3.0
DEVIATION_MIN_SHARE: Final = 0.25
MAX_GAP_SLOTS: Final = (
    1  # Slots derselben Regel mit höchstens so vielen Slots dazwischen: ein Treffer
)
COVERAGE_MIN_SHARE: Final = 0.9  # darunter ist die Abrechnung des Tages unvollständig

_PER_HOUR: Final = 4  # Wh je Viertelstunde → mittlere W
_SLOT_S: Final = SLOT.total_seconds()
_WH_PER_KWH: Final = 1000.0
_RULE_ORDER: Final[tuple[Rule, ...]] = (
    "grid_charge_unplanned",
    "below_reserve",
    "month_peak",
    "plan_deviation",
    "target_missed",
    "command_failures",
    "counter_check",
    "not_checked",
)


@dataclass(frozen=True)
class Finding:
    day: date
    rule: Rule
    level: Literal["info", "warn"]
    start: datetime | None
    end: datetime | None
    text: str
    link: str | None
    data: Mapping[str, Any]


@dataclass(frozen=True)
class MissedTarget:
    """Ein Auto, das sein Ladeziel zur Abfahrt nicht erreicht hat."""

    vehicle_name: str
    target_soc_pct: float
    end_soc_pct: float | None
    departure_at: datetime


@dataclass(frozen=True)
class DayInputs:
    """Alles, was die Regeln für einen Tag brauchen.

    ``soc_min`` ist je Slotbeginn der kleinste SOC (%); ``None`` heißt: InfluxDB nicht erreichbar.
    ``flagged_checks`` sind markierte Zählervergleiche: Signal (``import``, ``export``, ``pv``,
    ``wb:<key>``) und Abweichung in %. ``wallbox_labels`` nennt je Wallbox-Schlüssel den
    Anzeigenamen wie im UI (eigener Name, sonst die Art der Ladestation).
    """

    day: date
    slots: Sequence[LedgerSlot]
    plans: Sequence[Plan]
    soc_min: Mapping[datetime, float] | None
    reserve_soc_pct: float
    previous_peak_kw: float | None
    failures: Mapping[str, int]
    actuator_labels: Mapping[str, str]
    missed: Sequence[MissedTarget]
    flagged_checks: Sequence[tuple[str, float | None]]
    wallbox_labels: Mapping[str, str]


def _num(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _pct(value: float) -> str:
    """Prozent ohne überflüssige Nachkommastelle: 14 → „14“, 17,5 → „17,5“."""
    text = _num(value)
    return text.removesuffix(",0")


def _clock(moment: datetime) -> str:
    return ensure_utc(moment).astimezone(LOCAL_TZ).strftime("%H:%M")


def _span(start: datetime, end: datetime) -> str:
    return f"{_clock(start)}–{_clock(end)}"


def _runs(starts: Sequence[datetime]) -> list[tuple[datetime, datetime]]:
    """Folgen von Slotbeginnen als (Beginn, Ende); höchstens ``MAX_GAP_SLOTS`` Slots dazwischen
    gehören noch zur selben Folge."""
    runs: list[tuple[datetime, datetime]] = []
    for start in sorted(ensure_utc(item) for item in starts):
        if runs and start - runs[-1][1] <= MAX_GAP_SLOTS * SLOT:
            runs[-1] = (runs[-1][0], start + SLOT)
        else:
            runs.append((start, start + SLOT))
    return runs


def _within(values: Mapping[datetime, float], run: tuple[datetime, datetime]) -> list[float]:
    return [v for start, v in values.items() if run[0] <= ensure_utc(start) < run[1]]


def _decisions_link(day: date, actuator: str, *, error: bool = False) -> str:
    suffix = "&result=error" if error else ""
    return f"/entscheidungen?range={day.isoformat()}&actuator={actuator}{suffix}"


def _grid_charge(inputs: DayInputs) -> list[Finding]:
    day_has_plans = bool(in_force(inputs.plans, local_day_slots(inputs.day)))
    charging = {
        ensure_utc(s.slot_start): s.flows_wh["grid>battery"]
        for s in inputs.slots
        if s.flows_wh.get("grid>battery", 0.0) >= GRID_CHARGE_MIN_WH
    }
    plan_of = in_force(inputs.plans, list(charging))
    unplanned: dict[datetime, float] = {}
    for start, wh in charging.items():
        found = plan_of.get(start)
        if (found is None and day_has_plans) or (found is not None and found[1].charge_grid_w <= 0):
            unplanned[start] = wh
    findings: list[Finding] = []
    for run in _runs(list(unplanned)):
        wh_values = _within(unplanned, run)
        kwh = sum(wh_values) / _WH_PER_KWH
        max_kw = max(wh_values) * _PER_HOUR / _WH_PER_KWH
        soc_values = _within(inputs.soc_min or {}, run)
        soc_min = min(soc_values) if soc_values else None
        any_plan = any(start in plan_of for start in unplanned if run[0] <= start < run[1])
        reason = "der Plan sah kein Netzladen vor" if any_plan else "es galt kein Plan"
        text = (
            f"Akku lud {_span(*run)} mit bis zu {_num(max_kw)} kW aus dem Netz "
            f"({_num(kwh)} kWh), {reason}."
        )
        if soc_min is not None and soc_min <= inputs.reserve_soc_pct - VICTRON_RECHARGE_HINT_PCT:
            text += (
                f" Vermutlich lud Victron selbst nach (SOC {_pct(soc_min)} %, "
                f"Reserve {_pct(inputs.reserve_soc_pct)} %)."
            )
        findings.append(
            Finding(
                day=inputs.day,
                rule="grid_charge_unplanned",
                level="warn",
                start=run[0],
                end=run[1],
                text=text,
                link=_decisions_link(inputs.day, "victron"),
                data={"kwh": round(kwh, 3), "max_kw": round(max_kw, 3), "soc_min": soc_min},
            )
        )
    return findings


def _not_checked(inputs: DayInputs, text: str, data: Mapping[str, Any]) -> Finding:
    return Finding(
        day=inputs.day,
        rule="not_checked",
        level="info",
        start=None,
        end=None,
        text=text,
        link=None,
        data=data,
    )


def _below_reserve(inputs: DayInputs) -> list[Finding]:
    if inputs.soc_min is None:
        return [_not_checked(inputs, "Akku-SOC nicht geprüft: InfluxDB nicht erreichbar.", {})]
    if not inputs.soc_min:  # InfluxDB antwortet, hat aber keinen Wert: kein ruhiger Tag
        return [_not_checked(inputs, "Akku-SOC nicht geprüft: keine Werte in InfluxDB.", {})]
    limit = inputs.reserve_soc_pct - RESERVE_MARGIN_PCT
    low = {start: soc for start, soc in inputs.soc_min.items() if soc < limit}
    findings: list[Finding] = []
    for run in _runs(list(low)):
        lowest = min(_within(low, run))
        findings.append(
            Finding(
                day=inputs.day,
                rule="below_reserve",
                level="warn",
                start=run[0],
                end=run[1],
                text=(
                    f"Akku war {_span(*run)} unter der Reserve (tiefster Stand "
                    f"{_pct(lowest)} %, Reserve {_pct(inputs.reserve_soc_pct)} %)."
                ),
                link=None,
                data={"soc_min": lowest, "reserve_pct": inputs.reserve_soc_pct},
            )
        )
    return findings


def _kw(wh: float) -> float:
    return wh * _PER_HOUR / _WH_PER_KWH


def _wallbox(key: str, labels: Mapping[str, str]) -> str:
    return f"Wallbox {labels.get(key, key)}"


def _split(flows: Mapping[str, float], wallbox_labels: Mapping[str, str]) -> str:
    """„Akku 8,2 kW, Haus 0,9 kW“: Netzbezug nach Ziel; Ziele ohne Bezug entfallen."""
    parts: list[tuple[str, float]] = []
    for name, wh in sorted(flows.items()):
        source, _, target = name.partition(">")
        if source != "grid" or wh <= 0:
            continue
        if target == "battery":
            parts.append(("Akku", wh))
        elif target == "house":
            parts.append(("Haus", wh))
        elif target.startswith("wallbox."):
            parts.append((_wallbox(target.removeprefix("wallbox."), wallbox_labels), wh))
    parts.sort(key=lambda part: -part[1])
    return ", ".join(f"{label} {_num(_kw(wh))} kW" for label, wh in parts)


def _month_peak(inputs: DayInputs) -> list[Finding]:
    if inputs.previous_peak_kw is None or not inputs.slots:
        return []
    top = max(inputs.slots, key=lambda s: s.import_wh)
    peak_kw = _kw(top.import_wh)
    if peak_kw < inputs.previous_peak_kw + PEAK_RISE_MIN_KW:
        return []
    start = ensure_utc(top.slot_start)
    split = _split(top.flows_wh, inputs.wallbox_labels)
    text = (
        f"Neue Monatsspitze {_num(peak_kw)} kW um {_clock(start)} "
        f"(vorher {_num(inputs.previous_peak_kw)} kW)"
    )
    text += f": {split}." if split else "."
    return [
        Finding(
            day=inputs.day,
            rule="month_peak",
            level="warn",
            start=start,
            end=start + SLOT,
            text=text,
            link=None,
            data={"kw": round(peak_kw, 3), "previous_kw": inputs.previous_peak_kw},
        )
    ]


def _plan_deviation(inputs: DayInputs) -> list[Finding]:
    ledger = {ensure_utc(s.slot_start): s for s in inputs.slots}
    plan_of = in_force(inputs.plans, list(ledger))
    planned_wh = sum(found[1].import_w / _PER_HOUR for start, found in plan_of.items())
    actual_wh = sum(ledger[start].import_wh for start in plan_of)
    diff = actual_wh - planned_wh
    if abs(diff) < DEVIATION_MIN_KWH * _WH_PER_KWH or abs(diff) < DEVIATION_MIN_SHARE * planned_wh:
        return []
    return [
        Finding(
            day=inputs.day,
            rule="plan_deviation",
            level="info",
            start=None,
            end=None,
            text=(
                f"Netzbezug {_num(actual_wh / _WH_PER_KWH)} kWh statt geplant "
                f"{_num(planned_wh / _WH_PER_KWH)} kWh "
                f"({'+' if diff >= 0 else '-'}{_num(abs(diff) / _WH_PER_KWH)} kWh)."
            ),
            link="/zeitplan",
            data={"actual_kwh": actual_wh / _WH_PER_KWH, "planned_kwh": planned_wh / _WH_PER_KWH},
        )
    ]


def _target_missed(inputs: DayInputs) -> list[Finding]:
    findings: list[Finding] = []
    for item in inputs.missed:
        text = (
            f"{item.vehicle_name} hat das Ziel {_pct(item.target_soc_pct)} % um "
            f"{_clock(item.departure_at)} verfehlt"
        )
        if item.end_soc_pct is not None:
            text += f" (erreicht: {_pct(item.end_soc_pct)} %)"
        findings.append(
            Finding(
                day=inputs.day,
                rule="target_missed",
                level="warn",
                start=None,
                end=None,
                text=text + ".",
                link="/laden",
                data={
                    "vehicle": item.vehicle_name,
                    "target_soc_pct": item.target_soc_pct,
                    "end_soc_pct": item.end_soc_pct,
                },
            )
        )
    return findings


def _command_failures(inputs: DayInputs) -> list[Finding]:
    findings: list[Finding] = []
    for actuator, count in sorted(inputs.failures.items()):
        if count <= 0:
            continue
        label = inputs.actuator_labels.get(actuator, actuator)
        noun = "Befehl" if count == 1 else "Befehle"
        findings.append(
            Finding(
                day=inputs.day,
                rule="command_failures",
                level="warn",
                start=None,
                end=None,
                text=f"{count} {noun} an {label} gescheitert.",
                link=_decisions_link(inputs.day, actuator, error=True),
                data={"actuator": actuator, "count": count},
            )
        )
    return findings


_SIGNAL_LABELS: Final = {"import": "Netzbezug", "export": "Einspeisung", "pv": "PV"}


def _signal_label(signal: str, wallbox_labels: Mapping[str, str]) -> str:
    """Name eines Zählervergleichs wie in der Statistik: „Netzbezug“, „Wallbox Garage“."""
    if signal.startswith("wb:"):
        return _wallbox(signal.removeprefix("wb:"), wallbox_labels)
    return _SIGNAL_LABELS.get(signal, signal)


def _counter_check(inputs: DayInputs) -> list[Finding]:
    findings: list[Finding] = []
    for signal, deviation in inputs.flagged_checks:
        by = "" if deviation is None else f" um {_num(deviation)} %"
        label = _signal_label(signal, inputs.wallbox_labels)
        findings.append(
            Finding(
                day=inputs.day,
                rule="counter_check",
                level="info",
                start=None,
                end=None,
                text=f"Abrechnung und Zähler weichen bei {label}{by} ab.",
                link="/statistik",
                data={"signal": signal, "deviation_pct": deviation},
            )
        )
    return findings


def _coverage(inputs: DayInputs) -> list[Finding]:
    """Fehlen der Abrechnung mehr als 10 % der Viertelstunden des Tages (oder ihrer Zeit), sind
    die Regeln über sie nur teilweise geprüft; ein leerer Tag ist kein ruhiger Tag."""
    expected = len(local_day_slots(inputs.day))
    present = len(inputs.slots)
    covered_s = sum(item.covered_s for item in inputs.slots)
    least = COVERAGE_MIN_SHARE * expected
    if present >= least and covered_s >= least * _SLOT_S:
        return []
    quarters = min(present, round(covered_s / _SLOT_S))  # volle Viertelstunden
    text = (
        f"Abrechnung unvollständig ({quarters} von {expected} Viertelstunden), "
        "Auffälligkeiten nur teilweise geprüft."
    )
    return [_not_checked(inputs, text, {"slots": quarters, "expected": expected})]


_RULES: Final[tuple[Callable[[DayInputs], list[Finding]], ...]] = (
    _grid_charge,
    _below_reserve,
    _month_peak,
    _plan_deviation,
    _target_missed,
    _command_failures,
    _counter_check,
    _coverage,
)


def find(inputs: DayInputs) -> list[Finding]:
    """Alle Auffälligkeiten des Tages: nach Beginn, Tagesregeln ohne Zeit am Ende."""
    findings = [item for rule in _RULES for item in rule(inputs)]

    def order(item: Finding) -> tuple[bool, datetime | None, int]:
        return (item.start is None, item.start, _RULE_ORDER.index(item.rule))

    return sorted(findings, key=order)
