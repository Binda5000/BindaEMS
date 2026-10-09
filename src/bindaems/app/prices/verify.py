"""Prüfprotokoll „Preise“ (Spec 17.1-13): Format und Intervall von smartENERGY,
Verfügbarkeit der Referenzquellen und Ergebnis der Brutto/Netto-Erkennung.

Braucht keine Konfiguration; geschrieben werden nur Dateien in ``out_dir``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx

from bindaems.app.fetch import FetchError, RawResponse
from bindaems.app.prices.checks import (
    by_local_day,
    detect_vat,
    is_hourly,
    structure_findings,
    to_slots,
)
from bindaems.app.prices.sources import (
    AwattarSource,
    EnergyChartsSource,
    PriceParseError,
    ReferenceSource,
    SmartEnergySource,
)
from bindaems.shared.timeutil import LOCAL_TZ, Clock, local_day_slots

GROSS_FACTOR = 1.2  # USt 20 %, ohne Laufzeit-Einstellungen
VAT_WORDS = {"gross": "brutto", "net": "netto", None: "unbestimmt"}

Outcome = RawResponse | FetchError


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=LOCAL_TZ)


def _head(body: str) -> dict[str, Any]:
    """``tariff``, ``unit`` und ``interval`` der smartENERGY-Antwort (``?``, wenn sie fehlen)."""
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    data = data if isinstance(data, dict) else {}
    return {key: data.get(key, "?") for key in ("tariff", "unit", "interval")}


async def _outcome(request: Awaitable[RawResponse]) -> Outcome:
    try:
        return await request
    except FetchError as exc:
        return exc


async def check_prices(http: httpx.AsyncClient, clock: Clock, out_dir: Path) -> int:
    """Schreibt Rohantworten und Protokoll; 0 = bestanden, 1 = nicht bestanden."""
    now = clock.now().astimezone(LOCAL_TZ)
    today = now.date()
    primary = SmartEnergySource(http, clock)
    references: list[ReferenceSource] = [
        EnergyChartsSource(http, clock),
        AwattarSource(http, clock),
    ]
    primary_outcome = await _outcome(primary.fetch())
    window = (_midnight(today), _midnight(today + timedelta(days=2)))
    reference_outcomes = [await _outcome(source.fetch(*window)) for source in references]
    return await asyncio.to_thread(
        _write_protocol, now, out_dir, primary, primary_outcome, references, reference_outcomes
    )


def _write_protocol(
    now: datetime,
    out_dir: Path,
    primary: SmartEnergySource,
    primary_outcome: Outcome,
    references: Sequence[ReferenceSource],
    reference_outcomes: Sequence[Outcome],
) -> int:
    stamp = now.date().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"Abruf: {now:%d.%m.%Y %H:%M} Uhr"]

    def archive(name: str, raw: RawResponse) -> None:
        (out_dir / f"preise-{name}-{stamp}.json").write_text(raw.body, encoding="utf-8")

    primary_slots: dict[datetime, float] = {}
    primary_ok = False
    if isinstance(primary_outcome, FetchError):
        lines.append(f"smartENERGY: nicht verfügbar ({primary_outcome})")
    else:
        raw = primary_outcome
        archive(primary.name, raw)
        head = _head(raw.body)
        lines.append(
            f"smartENERGY: HTTP {raw.status}, tariff {head['tariff']}, unit {head['unit']}, "
            f"interval {head['interval']}"
        )
        try:
            intervals = primary.parse(raw.body) if raw.ok else None
        except PriceParseError as exc:
            lines.append(f"Befunde smartENERGY: {exc}")
        else:
            if intervals is None:
                lines.append(f"Befunde smartENERGY: keine Daten (HTTP {raw.status})")
            else:
                primary_slots = to_slots(intervals)
                days = by_local_day(intervals)
                counts = ", ".join(
                    f"{day:%d.%m.}: {sum(slot in primary_slots for slot in local_day_slots(day))}"
                    for day in sorted(days)
                )
                lines.append(f"smartENERGY Slots je Tag: {counts or 'keine'}")
                lines.append(
                    "Raster: Stundenwerte im 15-min-Raster"
                    if is_hourly(primary_slots)
                    else "Raster: 15-min-Werte"
                )
                findings = [
                    f"{day:%d.%m.}: {finding}"
                    for day in sorted(days)
                    for finding in structure_findings(day, days[day])
                ]
                lines.append(f"Befunde smartENERGY: {'; '.join(findings) or 'keine'}")
                primary_ok = bool(intervals) and not findings

    available = detected = False
    for reference, outcome in zip(references, reference_outcomes, strict=True):
        if isinstance(outcome, FetchError):
            lines.append(f"{reference.name}: nicht verfügbar ({outcome})")
            continue
        archive(reference.name, outcome)
        if not outcome.ok:
            lines.append(f"{reference.name}: nicht verfügbar (HTTP {outcome.status})")
            continue
        try:
            reference_slots = to_slots(reference.parse(outcome.body))
        except PriceParseError as exc:
            lines.append(f"{reference.name}: nicht verfügbar ({exc})")
            continue
        available = True
        lines.append(f"{reference.name}: HTTP {outcome.status}, {len(reference_slots)} Slots")
        detection = detect_vat(primary_slots, reference_slots, GROSS_FACTOR)
        ratio = f"{detection.ratio:.3f}" if detection.ratio is not None else "–"
        lines.append(
            f"Brutto/Netto ({reference.name}): {VAT_WORDS[detection.result]}, "
            f"Verhältnis {ratio} aus {detection.slots} Slots"
        )
        detected = detected or detection.result is not None

    passed = primary_ok and available and detected
    text = "\n".join(
        [
            f"# Prüfprotokoll Preise vom {now:%d.%m.%Y}",
            "",
            *(f"- {line}" for line in lines),
            "",
            f"Ergebnis: {'bestanden' if passed else 'nicht bestanden'}",
            "",
        ]
    )
    path = out_dir / f"pruefprotokoll-preise-{stamp}.md"
    path.write_text(text, encoding="utf-8")
    print(f"Prüfprotokoll geschrieben: {path}")
    return 0 if passed else 1
