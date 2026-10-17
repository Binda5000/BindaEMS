"""Bericht eines Szenario-Laufs als Markdown (Plan 3b, Task 16).

Eine Zeile je Tag mit den Energiekosten (der Leistungspreis gilt je Monat), darunter „Gesamt“
einschließlich Leistungspreis, die um den End-SOC bereinigten Kosten des Optimierers, der
Leistungspreis, die Informationszahl „Netzladen vor tatsächlicher Einspeisung“, unter der Tabelle
die beim Export übersprungenen Tage und der Takt.
„Bestanden“ heißt: bereinigte Kosten höchstens so hoch wie „Victron allein“ und kein Verstoß
gegen Regel R (Präzisierung 19).
"""

from __future__ import annotations

from typing import Final

from bindaems.tools.scenario.data import ScenarioFile
from bindaems.tools.scenario.runner import ScenarioResult

MIN_DAYS: Final = 30  # für die Abnahme der Phase 3 zählen mindestens so viele Tage
SLOT_MINUTES: Final = 15
_EPS_CT: Final = 1e-6
_NONE: Final = "–"
_COLUMNS: Final = (
    "Tag",
    "Victron allein (€)",
    "Optimierer (€)",
    "Differenz (€)",
    "Netzladen (kWh)",
    "Halten (Slots)",
    "Regel R",
    "Ersatzpläne",
)


def passed(result: ScenarioResult) -> bool:
    """Bereinigte Kosten des Optimierers höchstens die von „Victron allein“, kein Verstoß gegen
    Regel R."""
    return (
        result.adjusted_optimizer_ct <= result.baseline_ct + _EPS_CT and result.r1_violations == 0
    )


def _number(value: float, digits: int) -> str:
    """Zahl mit Dezimalkomma; ``-0,00`` wird zu ``0,00``."""
    text = f"{value:.{digits}f}"
    if float(text) == 0:
        text = f"{0.0:.{digits}f}"
    return text.replace(".", ",")


def _eur(ct: float) -> str:
    return _number(ct / 100, 2)


def _row(*cells: str) -> str:
    return "| " + " | ".join(cells) + " |"


def _money_row(label: str, baseline_ct: float, optimizer_ct: float) -> list[str]:
    return [label, _eur(baseline_ct), _eur(optimizer_ct), _eur(optimizer_ct - baseline_ct)]


def _skipped_lines(data: ScenarioFile) -> list[str]:
    """Zeile „Übersprungene Tage: …“ mit den Daten aus ``skipped_days`` („Datum: Grund“)."""
    if not data.skipped_days:
        return []
    days = ", ".join(entry.split(":", 1)[0].strip() for entry in data.skipped_days)
    return ["", f"Übersprungene Tage: {days}"]


def render_report(result: ScenarioResult, data: ScenarioFile) -> str:
    """Der Bericht; ``data`` liefert Herkunft und Zeitraum."""
    days = result.days
    rows = [
        _row(
            *_money_row(day.day.isoformat(), day.baseline_ct, day.optimizer_ct),
            _number(day.grid_charge_kwh, 1),
            str(day.hold_slots),
            str(day.r1_violations),
            str(day.fallbacks),
        )
        for day in days
    ]
    rows.append(
        _row(
            *_money_row("Gesamt", result.baseline_ct, result.optimizer_ct),
            _number(sum(day.grid_charge_kwh for day in days), 1),
            str(sum(day.hold_slots for day in days)),
            str(result.r1_violations),
            str(result.fallbacks),
        )
    )
    rows.append(
        _row(
            "Optimierer bereinigt um den End-SOC",
            _NONE,
            _eur(result.adjusted_optimizer_ct),
            _eur(result.adjusted_optimizer_ct - result.baseline_ct),
            *[_NONE] * 4,
        )
    )
    rows.append(
        _row(
            *_money_row("Leistungspreis", result.baseline_power_ct, result.optimizer_power_ct),
            *[_NONE] * 4,
        )
    )
    rows.append(
        _row(
            "Netzladen vor tatsächlicher Einspeisung",
            _NONE,
            _NONE,
            _NONE,
            _number(result.charge_before_export_kwh, 1),
            *[_NONE] * 3,
        )
    )
    source = "Anlage" if data.source == "plant" else "erzeugte Tage"
    lines = [
        "# Szenario-Vergleich mit Victron allein",
        "",
        f"{result.name} ({source})",
        "",
        _row(*_COLUMNS),
        _row(*["---"] * len(_COLUMNS)),
        *rows,
        *_skipped_lines(data),
        "",
        "Differenz = Optimierer − Victron allein, negativ heißt günstiger. Die Tageswerte enthalten"
        " nur Energiekosten, „Gesamt“ auch den Leistungspreis.",
        "",
        f"Takt: {result.step_slots * SLOT_MINUTES} min",
    ]
    if len(days) < MIN_DAYS:
        lines += [
            "",
            f"Weniger als {MIN_DAYS} Tage: "
            f"Für die Abnahme der Phase 3 zählen mindestens {MIN_DAYS}.",
        ]
    ok = passed(result)
    lines += ["", f"Ergebnis: {'Bestanden' if ok else 'Nicht bestanden'}"]
    if not ok:
        if result.adjusted_optimizer_ct > result.baseline_ct + _EPS_CT:
            lines.append("- Bereinigte Kosten über Victron allein")
        if result.r1_violations:
            lines.append(f"- Regel R verletzt ({result.r1_violations})")
    return "\n".join(lines) + "\n"
