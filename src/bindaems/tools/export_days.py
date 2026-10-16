"""Exportiert echte Tage der Anlage aus der Datenbank der app als Szenario-Datei (Plan 3b, Task 16).

Aufruf an der Anlage (im Container der app, die Datei liegt unter ``/data`` und wird nie
eingecheckt)::

    python -m bindaems.tools.export_days --config /config/config.yaml \
        --von 2026-10-01 --bis 2026-10-31 --out /data/tage.json

Danach rechnet ``scenario_days --tage /data/tage.json --out /data/bericht.md`` den Vergleich mit
„Victron allein“. Je lokalem Tag (92, 96 oder 100 Slots) gehen in die Datei:

- **Ist-Werte** (PV, Hauslast) aus der Abrechnung (``actual_from_ledger``). Ein unvollständiger
  Slot wird als Mittel seiner nächsten vollständigen Nachbarn ergänzt, höchstens ``MAX_FILLED``
  je Tag; bei mehr Lücken entfällt der Tag („unvollständig“).
- **Preise** mit den aktuellen Einstellungen über ``PriceService``; fehlt ein Preis, entfällt der
  Tag („keine Preise“).
- **Prognosen** je Horizontklasse aus dem Archiv. Fehlt eine Klasse, nimmt der Lauf die nächste
  vorhandene, sonst den Ist-Wert. ``--ohne-prognose`` setzt den Ist-Wert als Prognose (``h0_6``).
- **Monatsspitze** vor dem ersten exportierten Tag: höchster Netzbezug der Slots des Monats vor
  diesem Tag (die exportierten Tage selbst zählen nicht, sie rechnet der Lauf).

Je Tag steht eine Zeile auf der Standardausgabe. Exitcode 2 mit der Meldung auf stderr bei
ungültiger Konfiguration oder Eingabe, fehlender Datenbank, einer nicht schreibbaren Zieldatei und
wenn kein Tag exportierbar ist. Die Datenbank wird nur gelesen (Schema und Einstellungen müssen
schon da sein).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Final

from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from bindaems.app.audit import AuditLog
from bindaems.app.db.engine import DB_FILENAME, open_database
from bindaems.app.forecast.archive import (
    Actual,
    ForecastArchive,
    HorizonClass,
    Kind,
    actual_from_ledger,
)
from bindaems.app.ledger.service import LedgerService
from bindaems.app.prices.service import PriceService, PriceView
from bindaems.app.prices.store import PriceStore
from bindaems.app.settings.service import SettingsService
from bindaems.shared.config import Config, ConfigError, load_config
from bindaems.shared.logging import configure_logging
from bindaems.shared.timeutil import (
    LOCAL_TZ,
    SLOT,
    Clock,
    SystemClock,
    ensure_utc,
    local_day_slots,
)
from bindaems.tools.scenario.data import ScenarioBattery, ScenarioFile, ScenarioSlot

MAX_FILLED: Final = 4  # je Tag höchstens so viele unvollständige Slots (Mittel der Nachbarn)
_WH_TO_KW: Final = 4 / 1000  # Wh je Viertelstunde → kW


class NoDaysError(Exception):
    """Kein Tag war exportierbar; ``lines`` sind die Zeilen je Tag."""

    def __init__(self, lines: list[str]) -> None:
        super().__init__("Kein Tag exportierbar")
        self.lines = lines


@dataclass(frozen=True)
class _Services:
    prices: PriceService
    ledger: LedgerService
    archive: ForecastArchive
    settings: SettingsService


def _services(engine: Engine, clock: Clock) -> _Services:
    audit = AuditLog(engine, clock)
    settings = SettingsService(engine, clock, audit)
    prices = PriceService(PriceStore(engine, clock), settings)
    return _Services(
        prices,
        LedgerService(engine, clock, prices, settings, audit),
        ForecastArchive(engine),
        settings,
    )


def _fill(actuals: list[Actual | None]) -> list[Actual]:
    """Unvollständige Slots als Mittel der nächsten vollständigen Nachbarn davor und danach."""
    complete = [i for i, actual in enumerate(actuals) if actual is not None]
    result: list[Actual] = []
    for index, actual in enumerate(actuals):
        if actual is not None:
            result.append(actual)
            continue
        before = [actuals[i] for i in complete if i < index][-1:]
        after = [actuals[i] for i in complete if i > index][:1]
        neighbours = [item for item in (*before, *after) if item is not None]
        result.append(
            Actual(
                pv_w=sum(item.pv_w for item in neighbours) / len(neighbours),
                house_w=sum(item.house_w for item in neighbours) / len(neighbours),
            )
        )
    return result


def _forecasts(
    archive: ForecastArchive, kind: Kind, start: datetime, end: datetime
) -> dict[datetime, dict[HorizonClass, float]]:
    result: dict[datetime, dict[HorizonClass, float]] = {}
    for record in archive.records(kind, start, end):
        result.setdefault(ensure_utc(record.slot_start), {})[record.horizon] = max(
            0.0, record.p50_w
        )
    return result


def _day(
    services: _Services, day: date, *, with_forecast: bool
) -> tuple[list[ScenarioSlot], int, int] | str:
    """Slots, Zahl ergänzter Slots und Anteil der Slots mit Prognose in % oder der Grund, warum
    der Tag entfällt."""
    starts = local_day_slots(day)
    end = starts[-1] + SLOT
    views: dict[datetime, PriceView] = {
        ensure_utc(view.start): view for view in services.prices.slots(starts[0], end)
    }
    if any(start not in views for start in starts):
        return "keine Preise"
    stored = {ensure_utc(slot.slot_start): slot for slot in services.ledger.slots(starts[0], end)}
    measured = [actual_from_ledger(stored[start]) if start in stored else None for start in starts]
    gaps = sum(1 for actual in measured if actual is None)
    if gaps > MAX_FILLED:
        return "unvollständig"
    actuals = _fill(measured)
    pv = _forecasts(services.archive, "pv", starts[0], end) if with_forecast else {}
    load = _forecasts(services.archive, "load", starts[0], end) if with_forecast else {}
    slots: list[ScenarioSlot] = []
    forecast_slots = 0
    for start, actual in zip(starts, actuals, strict=True):
        view = views[start]
        if with_forecast:
            pv_forecast, load_forecast = pv.get(start, {}), load.get(start, {})
            forecast_slots += 1 if pv_forecast and load_forecast else 0
        else:
            pv_forecast, load_forecast = {"h0_6": actual.pv_w}, {"h0_6": actual.house_w}
        slots.append(
            ScenarioSlot(
                start=start,
                pv_w=actual.pv_w,
                load_w=actual.house_w,
                import_ct=view.import_gross_ct,
                feed_in_ct=view.feed_in_ct,
                pv_forecast_w=pv_forecast,
                load_forecast_w=load_forecast,
            )
        )
    return slots, gaps, round(100 * forecast_slots / len(starts))


def _month_peak_kw(ledger: LedgerService, first_day: date) -> float | None:
    """Höchster Netzbezug der Slots des lokalen Monats vor ``first_day`` in kW."""
    month_start = datetime.combine(first_day.replace(day=1), time(0), tzinfo=LOCAL_TZ)
    before = ledger.slots(month_start, local_day_slots(first_day)[0])
    return max((slot.import_wh * _WH_TO_KW for slot in before), default=None)


def _battery(cfg: Config, start_soc_pct: float) -> ScenarioBattery:
    grid = cfg.grid
    if grid.max_import_kw is not None:
        grid_max_import_w = grid.max_import_kw * 1000
    else:
        grid_max_import_w = (grid.fuse_a - grid.fuse_margin_a) * grid.voltage_nominal_v * 3
    return ScenarioBattery(
        **cfg.battery.model_dump(),
        max_grid_charge_setpoint_w=cfg.victron.max_grid_charge_setpoint_w,
        grid_max_import_w=grid_max_import_w,
        start_soc_pct=start_soc_pct,
    )


def export_days_with_lines(
    engine: Engine,
    cfg: Config,
    first: date,
    last: date,
    *,
    start_soc_pct: float,
    with_forecast: bool,
    clock: Clock,
) -> tuple[ScenarioFile, list[str]]:
    """Wie ``export_days``, dazu die Zeile je Tag für die Standardausgabe."""
    services = _services(engine, clock)
    slots: list[ScenarioSlot] = []
    lines: list[str] = []
    skipped: list[str] = []
    exported: list[date] = []
    day = first
    while day <= last:
        outcome = _day(services, day, with_forecast=with_forecast)
        if isinstance(outcome, str):
            lines.append(f"{day.isoformat()}: übersprungen ({outcome})")
            skipped.append(f"{day.isoformat()}: {outcome}")
        else:
            day_slots, filled, percent = outcome
            slots += day_slots
            exported.append(day)
            lines.append(
                f"{day.isoformat()}: {len(day_slots)} Slots, {filled} ergänzt, Prognose {percent} %"
            )
        day += timedelta(days=1)
    if not exported:
        raise NoDaysError(lines)
    settings = services.settings.current().settings
    count = len(exported)
    data = ScenarioFile(
        version=1,
        created_at=clock.now(),
        source="plant",
        name=f"Anlage {exported[0].isoformat()} bis {exported[-1].isoformat()}, "
        f"{count} Tag{'' if count == 1 else 'e'}",
        battery=_battery(cfg, start_soc_pct),
        planning=settings.planning,
        tariff=settings.tariff,
        month_peak_kw=_month_peak_kw(services.ledger, exported[0]),
        skipped_days=skipped,
        slots=slots,
    )
    return data, lines


def export_days(
    engine: Engine,
    cfg: Config,
    first: date,
    last: date,
    *,
    start_soc_pct: float,
    with_forecast: bool,
    clock: Clock,
) -> ScenarioFile:
    """Szenario-Datei der lokalen Tage ``first`` bis ``last`` (einschließlich); ``NoDaysError``,
    wenn kein Tag exportierbar ist."""
    data, _ = export_days_with_lines(
        engine,
        cfg,
        first,
        last,
        start_soc_pct=start_soc_pct,
        with_forecast=with_forecast,
        clock=clock,
    )
    return data


def _date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"kein Datum JJJJ-MM-TT: {text}") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="export_days", description="Tage der Anlage als Szenario-Datei exportieren"
    )
    parser.add_argument("--config", type=Path, default=Path("/config/config.yaml"))
    parser.add_argument("--von", type=_date, required=True, help="erster lokaler Tag JJJJ-MM-TT")
    parser.add_argument("--bis", type=_date, required=True, help="letzter lokaler Tag JJJJ-MM-TT")
    parser.add_argument("--out", type=Path, required=True, help="Zieldatei (JSON)")
    parser.add_argument("--start-soc", type=float, default=50.0, help="SOC zu Beginn in %%")
    parser.add_argument(
        "--ohne-prognose", action="store_true", help="Ist-Werte als Prognose (h0_6) eintragen"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse beendet sich bei Fehlern mit 2 (und bei --help mit 0)
        return exc.code if isinstance(exc.code, int) else 2
    configure_logging("WARNING")
    if args.von > args.bis:
        print("--von liegt nach --bis", file=sys.stderr)
        return 2
    if not 0 <= args.start_soc <= 100:
        print("--start-soc muss zwischen 0 und 100 liegen", file=sys.stderr)
        return 2
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    path = cfg.app.data_dir / DB_FILENAME
    if not path.is_file():
        print(f"Datenbank nicht gefunden: {path}", file=sys.stderr)
        return 2
    engine = open_database(path)
    try:
        data, lines = export_days_with_lines(
            engine,
            cfg,
            args.von,
            args.bis,
            start_soc_pct=args.start_soc,
            with_forecast=not args.ohne_prognose,
            clock=SystemClock(),
        )
    except NoDaysError as exc:
        print("\n".join(exc.lines))
        print(f"{exc}: {args.von} bis {args.bis}", file=sys.stderr)
        return 2
    except (SQLAlchemyError, ValidationError) as exc:
        print(f"Datenbank nicht lesbar: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    try:
        args.out.write_text(data.model_dump_json(indent=1), encoding="utf-8")
    except OSError as exc:
        print(f"Datei nicht geschrieben: {exc}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
