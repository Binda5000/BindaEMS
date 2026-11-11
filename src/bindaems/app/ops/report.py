"""Text des Tagesberichts (Betrieb im Blick).

Rein: kein I/O. Der Bericht fasst gestern in wenigen Sätzen zusammen: Netzbezug und Netzladen
gegen den Plan, Kosten und Ersparnis, Monatsspitze, die wichtigsten Auffälligkeiten und den
Betriebscheck von jetzt. Fehlende Zahlen entfallen samt ihrem Satzteil.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

from bindaems.app.ops.check import OpsCheck
from bindaems.app.ops.findings import Finding

MAX_FINDINGS: Final = 3
WEEKDAYS: Final = (
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
)


@dataclass(frozen=True)
class DaySummary:
    """Zahlen des Tages; ``None``, wo sie fehlen. Netzbezug und Netzladen geplant/Ist über
    dieselben Viertelstunden (mit Plan und Abrechnung), Kosten und Ersparnis wie die Statistik."""

    import_kwh: float | None
    planned_import_kwh: float | None
    grid_charge_kwh: float | None
    planned_grid_charge_kwh: float | None
    net_eur: float | None
    savings_eur: float | None
    month_peak_kw: float | None


def _num(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _grid(summary: DaySummary) -> str | None:
    if summary.import_kwh is None:
        return None
    text = f"Netzbezug {_num(summary.import_kwh)} kWh"
    if summary.planned_import_kwh is not None:
        text += f" (geplant {_num(summary.planned_import_kwh)})"
    if summary.grid_charge_kwh is not None:
        text += f", davon Netzladen {_num(summary.grid_charge_kwh)} kWh"
        if summary.planned_grid_charge_kwh is not None:
            text += f" (geplant {_num(summary.planned_grid_charge_kwh)})"
    return text + "."


def _money(summary: DaySummary) -> str | None:
    parts = []
    if summary.net_eur is not None:
        parts.append(f"Kosten {_num(summary.net_eur, 2)} €")
    if summary.savings_eur is not None:
        parts.append(f"Ersparnis {_num(summary.savings_eur, 2)} €")
    return ", ".join(parts) + "." if parts else None


def _findings(findings: Sequence[Finding] | None) -> str:
    if findings is None:
        return "Auffälligkeiten noch nicht gerechnet."
    if not findings:
        return "Keine Auffälligkeiten."
    ranked = sorted(findings, key=lambda item: item.level != "warn")  # stabil: warn vor info
    shown = ranked[:MAX_FINDINGS]
    count = len(findings)
    head = "1 Auffälligkeit" if count == 1 else f"{count} Auffälligkeiten"
    text = f"{head}: " + " ".join(item.text for item in shown)
    if count > len(shown):
        text += f" und {count - len(shown)} weitere unter Betrieb."
    return text


def _check(check: OpsCheck) -> str:
    bad = sum(1 for item in check.items if item.level in ("warn", "error"))
    if bad:
        return f"Betriebscheck jetzt: {bad} Befund{'e' if bad > 1 else ''}."
    if check.level == "ok":
        return "Betriebscheck jetzt: alles in Ordnung."
    return "Betriebscheck jetzt: noch nicht beurteilt."


def report_text(
    day: date,
    summary: DaySummary,
    findings: Sequence[Finding] | None,
    check: OpsCheck | None,
) -> str:
    """Der Bericht über den Ortstag ``day``.

    Höchstens drei Auffälligkeiten (``warn`` vor ``info``), der Rest als „und N weitere unter
    Betrieb“. ``findings`` ``None`` heißt: der Tag ist noch nicht gerechnet.
    """
    sentences = [
        _grid(summary),
        _money(summary),
        None
        if summary.month_peak_kw is None
        else f"Monatsspitze {_num(summary.month_peak_kw)} kW.",
        _findings(findings),
        None if check is None else _check(check),
    ]
    return f"{WEEKDAYS[day.weekday()]} {day.day}.{day.month}.: " + " ".join(
        sentence for sentence in sentences if sentence
    )
