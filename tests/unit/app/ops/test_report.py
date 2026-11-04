"""Tests des Berichtstexts (Betrieb im Blick, Task 5)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from bindaems.app.ops.check import CheckItem, OpsCheck
from bindaems.app.ops.findings import Finding
from bindaems.app.ops.report import DaySummary, report_text

DAY = date(2025, 11, 3)  # ein Montag
NOW = datetime(2026, 11, 4, 6, 30, tzinfo=UTC)

FULL = DaySummary(
    import_kwh=14.2,
    planned_import_kwh=9.8,
    grid_charge_kwh=4.4,
    planned_grid_charge_kwh=0.0,
    net_eur=3.12,
    savings_eur=1.85,
    month_peak_kw=9.1,
)


def finding(text: str, level: str = "warn", rule: str = "grid_charge_unplanned") -> Finding:
    return Finding(
        day=DAY, rule=rule, level=level, start=None, end=None, text=text, link=None, data={}
    )  # type: ignore[arg-type]


def check(*levels: str) -> OpsCheck:
    items = tuple(CheckItem(f"i{n}", f"Punkt {n}", level, "x") for n, level in enumerate(levels))  # type: ignore[arg-type]
    overall = "error" if "error" in levels else "warn" if "warn" in levels else "ok"
    return OpsCheck(level=overall, items=items, checked_at=NOW)  # type: ignore[arg-type]


def test_report_text_like_3_november() -> None:
    found = [
        finding("Akku lud 22:30–23:00 mit bis zu 9,1 kW aus dem Netz."),
        finding("Neue Monatsspitze 9,1 kW um 22:30.", "info", "new_peak"),
    ]
    assert report_text(DAY, FULL, found, check("ok", "ok")) == (
        "Montag 3.11.: Netzbezug 14,2 kWh (geplant 9,8), davon Netzladen 4,4 kWh (geplant 0,0). "
        "Kosten 3,12 €, Ersparnis 1,85 €. Monatsspitze 9,1 kW. "
        "2 Auffälligkeiten: Akku lud 22:30–23:00 mit bis zu 9,1 kW aus dem Netz. "
        "Neue Monatsspitze 9,1 kW um 22:30. "
        "Betriebscheck jetzt: alles in Ordnung."
    )


def test_report_text_quiet_day() -> None:
    text = report_text(DAY, FULL, [], check("ok"))
    assert "Keine Auffälligkeiten." in text
    assert text.endswith("Betriebscheck jetzt: alles in Ordnung.")


def test_report_text_not_computed() -> None:
    assert "Auffälligkeiten noch nicht gerechnet." in report_text(DAY, FULL, None, check("ok"))


def test_report_text_counts_check_findings() -> None:
    assert report_text(DAY, FULL, [], check("ok", "warn", "error")).endswith(
        "Betriebscheck jetzt: 2 Befunde."
    )
    assert report_text(DAY, FULL, [], check("warn")).endswith("Betriebscheck jetzt: 1 Befund.")


def test_report_text_caps_at_three() -> None:
    found = [finding(f"Info {n}.", "info", "new_peak") for n in range(2)] + [
        finding(f"Warnung {n}.") for n in range(3)
    ]
    text = report_text(DAY, FULL, found, None)
    assert (
        "5 Auffälligkeiten: Warnung 0. Warnung 1. Warnung 2. und 2 weitere unter Betrieb." in text
    )
    assert "Info" not in text
    assert "Betriebscheck" not in text


def test_report_text_leaves_out_unknown_numbers() -> None:
    summary = DaySummary(
        import_kwh=14.2,
        planned_import_kwh=None,
        grid_charge_kwh=None,
        planned_grid_charge_kwh=None,
        net_eur=None,
        savings_eur=1.85,
        month_peak_kw=None,
    )
    assert report_text(DAY, summary, [], None) == (
        "Montag 3.11.: Netzbezug 14,2 kWh. Ersparnis 1,85 €. Keine Auffälligkeiten."
    )
    empty = DaySummary(None, None, None, None, None, None, None)
    assert report_text(DAY, empty, [], None) == "Montag 3.11.: Keine Auffälligkeiten."
