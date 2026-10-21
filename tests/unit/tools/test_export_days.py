"""Tests von ``export_days`` (Plan 3b, Task 16): echte Dienste der app auf einer Datenbank in
``tmp_path``, nur erfundene Werte."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
import structlog
import yaml
from sqlalchemy import Engine, Table, func, select
from sqlalchemy.exc import OperationalError
from tests.app_helpers import ingest, price_row

from bindaems.app.audit import AuditLog
from bindaems.app.db.engine import DB_FILENAME, migrate, open_database, open_database_readonly
from bindaems.app.db.schema import audit_log, settings_version
from bindaems.app.forecast.archive import ForecastArchive, RecordValue
from bindaems.app.ledger.service import LedgerService
from bindaems.app.prices.service import PriceService
from bindaems.app.prices.store import PriceStore
from bindaems.app.settings.service import SettingsService
from bindaems.shared.config import Config
from bindaems.shared.timeutil import SLOT, ManualClock, local_day_slots
from bindaems.tools.export_days import MAX_FILLED, NoDaysError, export_days, main
from bindaems.tools.scenario.data import ScenarioFile

CLOCK = ManualClock(datetime.fromisoformat("2026-10-16T10:00:00+00:00"))
DAY = date(2026, 10, 14)


@dataclass
class AppDb:
    engine: Engine
    store: PriceStore
    ledger: LedgerService
    archive: ForecastArchive
    settings: SettingsService


@pytest.fixture
def app_db(tmp_path: Path) -> Iterator[AppDb]:
    engine = open_database(tmp_path / DB_FILENAME)
    migrate(engine)
    audit = AuditLog(engine, CLOCK)
    settings = SettingsService(engine, CLOCK, audit)
    store = PriceStore(engine, CLOCK)
    ledger = LedgerService(engine, CLOCK, PriceService(store, settings), settings, audit)
    yield AppDb(engine, store, ledger, ForecastArchive(engine), settings)
    engine.dispose()


def flows_for(pv_w: float, load_w: float) -> dict[str, float]:
    """Flüsse in Wh einer Viertelstunde: PV deckt die Last, der Rest geht ins Netz bzw. kommt
    von dort."""
    pv_wh, load_wh = pv_w / 4, load_w / 4
    flows = {
        "pv>house": min(pv_wh, load_wh),
        "pv>grid": max(0.0, pv_wh - load_wh),
        "grid>house": max(0.0, load_wh - pv_wh),
    }
    return {name: wh for name, wh in flows.items() if wh > 0}


def fill_day(
    db: AppDb,
    day: date,
    *,
    pv_w: float,
    load_w: float,
    price_ct: float,
    forecast: bool = False,
    incomplete: int = 0,
    prices: bool = True,
    override: dict[int, tuple[dict[str, float], float]] | None = None,
) -> None:
    """Ein lokaler Tag: Preise, Abrechnung (``incomplete`` Slots mit zu wenig Abdeckung) und
    auf Wunsch Prognosen, ausgegeben 3 h vor dem Slot (PV +100 W, Last +50 W). ``override`` ersetzt
    Flüsse und Abdeckung einzelner Slots (nach Index)."""
    slots = local_day_slots(day)
    if prices:
        db.store.save([price_row(slot, price_ct, "primary") for slot in slots])
    gaps = set(range(10, 10 + 10 * incomplete, 10))
    for index, slot in enumerate(slots):
        flows, covered = (override or {}).get(
            index, (flows_for(pv_w, load_w), 100.0 if index in gaps else 900.0)
        )
        ingest(db.ledger, slot, flows, covered_s=covered)
        if forecast:
            issued = slot - timedelta(hours=3)
            db.archive.record("pv", issued, {slot: RecordValue(pv_w + 100, pv_w + 100)})
            db.archive.record("load", issued, {slot: RecordValue(load_w + 50, load_w + 50)})


def _count(engine: Engine, table: Table) -> int:
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


def run(
    db: AppDb, cfg: Config, first: date = DAY, last: date = DAY, **kwargs: object
) -> ScenarioFile:
    options: dict[str, object] = {"start_soc_pct": 50.0, "with_forecast": False, "clock": CLOCK}
    options.update(kwargs)
    return export_days(db.engine, cfg, first, last, **options)  # type: ignore[arg-type]


def test_export_days_from_app_database(app_db: AppDb, cfg: Config) -> None:
    fill_day(app_db, DAY, pv_w=2000, load_w=600, price_ct=10.0, forecast=True)
    data = run(app_db, cfg, with_forecast=True)
    assert data.source == "plant" and len(data.slots) == 96
    assert data.slots[40].pv_w == 2000 and data.slots[40].load_w == 600
    assert data.slots[40].pv_forecast_w == {"h0_6": 2100.0}
    assert data.slots[40].load_forecast_w == {"h0_6": 650.0}
    assert data.created_at == CLOCK.now() and data.battery.start_soc_pct == 50.0


def test_prices_follow_current_settings(app_db: AppDb, cfg: Config) -> None:
    fill_day(app_db, DAY, pv_w=0, load_w=600, price_ct=10.0)
    data = run(app_db, cfg)
    view = PriceService(app_db.store, app_db.settings).slots(*_bounds(DAY))[0]
    assert data.slots[0].import_ct == view.import_gross_ct > 10.0
    assert data.planning == app_db.settings.current().settings.planning
    assert data.tariff == app_db.settings.current().settings.tariff


def _bounds(day: date) -> tuple[datetime, datetime]:
    slots = local_day_slots(day)
    return slots[0], slots[-1] + SLOT


def test_battery_comes_from_config(app_db: AppDb, cfg: Config) -> None:
    fill_day(app_db, DAY, pv_w=0, load_w=600, price_ct=10.0)
    battery = run(app_db, cfg, start_soc_pct=35.0).battery
    assert battery.usable_kwh == cfg.battery.usable_kwh
    assert battery.max_grid_charge_setpoint_w == cfg.victron.max_grid_charge_setpoint_w
    assert battery.grid_max_import_w > 0 and battery.start_soc_pct == 35.0


def test_day_with_too_many_gaps_is_skipped(app_db: AppDb, cfg: Config) -> None:
    fill_day(app_db, DAY, pv_w=0, load_w=600, price_ct=10.0, incomplete=MAX_FILLED + 1)
    with pytest.raises(NoDaysError) as error:
        run(app_db, cfg)
    assert error.value.lines == ["2026-10-14: übersprungen (unvollständig)"]


def test_up_to_four_gaps_are_filled(app_db: AppDb, cfg: Config) -> None:
    fill_day(app_db, DAY, pv_w=0, load_w=600, price_ct=10.0, incomplete=4)
    data = run(app_db, cfg)
    assert len(data.slots) == 96 and data.slots[0].pv_forecast_w == {"h0_6": 0.0}
    assert all(item.load_w == 600 for item in data.slots)  # Mittel der Nachbarn


def test_gap_is_filled_with_mean_of_neighbours(app_db: AppDb, cfg: Config) -> None:
    override = {
        19: (flows_for(200, 600), 900.0),
        20: (flows_for(0, 1000), 100.0),
        21: (flows_for(600, 800), 900.0),
    }
    fill_day(app_db, DAY, pv_w=400, load_w=600, price_ct=10.0, override=override)
    data = run(app_db, cfg)
    assert (data.slots[20].pv_w, data.slots[20].load_w) == (400, 700)


def test_day_without_prices_is_skipped(app_db: AppDb, cfg: Config) -> None:
    fill_day(app_db, DAY, pv_w=0, load_w=600, price_ct=10.0, prices=False)
    with pytest.raises(NoDaysError) as error:
        run(app_db, cfg)
    assert error.value.lines == ["2026-10-14: übersprungen (keine Preise)"]


def test_skipped_day_is_listed_in_file(app_db: AppDb, cfg: Config) -> None:
    fill_day(app_db, DAY, pv_w=0, load_w=600, price_ct=10.0)
    fill_day(app_db, date(2026, 10, 15), pv_w=0, load_w=600, price_ct=10.0, prices=False)
    data = run(app_db, cfg, last=date(2026, 10, 15))
    assert len(data.slots) == 96 and data.skipped_days == ["2026-10-15: keine Preise"]


def test_short_day_has_92_slots(app_db: AppDb, cfg: Config) -> None:
    day = date(2026, 3, 29)
    fill_day(app_db, day, pv_w=0, load_w=600, price_ct=10.0)
    assert len(run(app_db, cfg, first=day, last=day).slots) == 92


def test_month_peak_counts_only_slots_before_first_day(app_db: AppDb, cfg: Config) -> None:
    earlier = date(2026, 10, 12)
    peak = {30: ({"grid>house": 1200.0}, 900.0)}  # 4,8 kW
    fill_day(app_db, earlier, pv_w=0, load_w=600, price_ct=10.0, override=peak)
    fill_day(app_db, DAY, pv_w=0, load_w=2000, price_ct=10.0)  # 2,0 kW
    assert run(app_db, cfg).month_peak_kw == pytest.approx(4.8)
    fill_day(app_db, date(2026, 10, 1), pv_w=0, load_w=600, price_ct=10.0)
    assert run(app_db, cfg, first=date(2026, 10, 1), last=date(2026, 10, 1)).month_peak_kw is None


def test_month_peak_ignores_the_previous_month(app_db: AppDb, cfg: Config) -> None:
    fill_day(app_db, date(2026, 9, 30), pv_w=0, load_w=5000, price_ct=10.0)
    fill_day(app_db, date(2026, 10, 1), pv_w=0, load_w=600, price_ct=10.0)
    assert run(app_db, cfg, first=date(2026, 10, 1), last=date(2026, 10, 1)).month_peak_kw is None


def test_without_forecast_uses_actual_values(app_db: AppDb, cfg: Config) -> None:
    fill_day(app_db, DAY, pv_w=1500, load_w=600, price_ct=10.0, forecast=True)
    item = run(app_db, cfg, with_forecast=False).slots[50]
    assert item.pv_forecast_w == {"h0_6": 1500.0} and item.load_forecast_w == {"h0_6": 600.0}


def test_missing_forecast_stays_empty(app_db: AppDb, cfg: Config) -> None:
    fill_day(app_db, DAY, pv_w=1500, load_w=600, price_ct=10.0)
    item = run(app_db, cfg, with_forecast=True).slots[50]
    assert item.pv_forecast_w == {} and item.load_forecast_w == {}


# --- Befehlszeile --------------------------------------------------------------------------


@pytest.fixture
def config_file(app_db: AppDb, cfg: Config, tmp_path: Path) -> Path:
    raw = yaml.safe_load(Path("deploy/config.example.yaml").read_text(encoding="utf-8"))
    raw["app"] = {**(raw.get("app") or {}), "data_dir": str(tmp_path)}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_main_writes_file_and_prints_line_per_day(
    app_db: AppDb, config_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fill_day(app_db, DAY, pv_w=2000, load_w=600, price_ct=10.0, forecast=True, incomplete=2)
    fill_day(app_db, date(2026, 10, 15), pv_w=0, load_w=600, price_ct=10.0, prices=False)
    out = tmp_path / "tage.json"
    code = main(
        [
            "--config",
            str(config_file),
            "--von",
            "2026-10-14",
            "--bis",
            "2026-10-15",
            "--out",
            str(out),
        ]
    )
    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert lines == [
        "2026-10-14: 96 Slots, 2 ergänzt, Prognose 100 %",
        "2026-10-15: übersprungen (keine Preise)",
    ]
    data = ScenarioFile.model_validate_json(out.read_text(encoding="utf-8"))
    assert data.source == "plant" and len(data.slots) == 96 and data.battery.start_soc_pct == 50.0


def test_main_options(
    app_db: AppDb, config_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fill_day(app_db, DAY, pv_w=2000, load_w=600, price_ct=10.0, forecast=True)
    out = tmp_path / "tage.json"
    code = main(
        ["--config", str(config_file), "--von", "2026-10-14", "--bis", "2026-10-14",
         "--out", str(out), "--start-soc", "80", "--ohne-prognose"]
    )  # fmt: skip
    assert code == 0 and "Prognose 0 %" in capsys.readouterr().out
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["battery"]["start_soc_pct"] == 80
    assert raw["slots"][40]["pv_forecast_w"] == {"h0_6": 2000.0}


def test_main_fails_without_exportable_day(
    app_db: AppDb, config_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "tage.json"
    code = main(
        [
            "--config",
            str(config_file),
            "--von",
            "2026-10-14",
            "--bis",
            "2026-10-14",
            "--out",
            str(out),
        ]
    )
    captured = capsys.readouterr()
    assert code == 2 and not out.exists()
    assert "2026-10-14: übersprungen (keine Preise)" in captured.out
    assert "Kein Tag exportierbar" in captured.err


def test_main_fails_on_bad_config_database_and_dates(
    config_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = str(tmp_path / "tage.json")
    days = ["--von", "2026-10-14", "--bis", "2026-10-14", "--out", out]
    assert main(["--config", str(tmp_path / "fehlt.yaml"), *days]) == 2
    assert "Datei nicht gefunden" in capsys.readouterr().err
    empty = tmp_path / "leer"
    empty.mkdir()
    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    raw["app"]["data_dir"] = str(empty)
    other = tmp_path / "andere.yaml"
    other.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert main(["--config", str(other), *days]) == 2
    assert "Datenbank nicht gefunden" in capsys.readouterr().err
    assert main(["--config", str(config_file), "--von", "2026-10-15", "--bis", "2026-10-14",
                 "--out", out]) == 2  # fmt: skip
    assert main(["--config", str(config_file), "--von", "gestern", "--bis", "2026-10-14",
                 "--out", out]) == 2  # fmt: skip
    assert main(["--config", str(config_file), *days, "--start-soc", "130"]) == 2


def _main(config: Path, out: Path) -> int:
    return main(
        ["--config", str(config), "--von", "2026-10-14", "--bis", "2026-10-14", "--out", str(out)]
    )


def test_export_days_does_not_write(app_db: AppDb, config_file: Path, tmp_path: Path) -> None:
    fill_day(app_db, DAY, pv_w=0, load_w=600, price_ct=10.0)
    with app_db.engine.begin() as conn:  # eine Datenbank, die noch keine Einstellungen hat
        conn.execute(settings_version.delete())
    audit_before = _count(app_db.engine, audit_log)
    assert _main(config_file, tmp_path / "tage.json") == 0
    assert _count(app_db.engine, settings_version) == 0  # keine Version 1 angelegt
    assert _count(app_db.engine, audit_log) == audit_before
    # die Datenbank ist nur lesend geöffnet: Schreiben scheitert
    engine = open_database_readonly(tmp_path / DB_FILENAME)
    try:
        with pytest.raises(OperationalError, match="readonly"), engine.begin() as conn:
            conn.exec_driver_sql("DELETE FROM price_raw")
    finally:
        engine.dispose()


def test_relative_data_dir_follows_the_config(
    app_db: AppDb, config_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill_day(app_db, DAY, pv_w=0, load_w=600, price_ct=10.0)
    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    raw["app"]["data_dir"] = ".."  # die Datenbank liegt eine Ebene über der Konfiguration
    (tmp_path / "konfig").mkdir()
    config = tmp_path / "konfig" / "config.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    elsewhere = tmp_path / "anderswo" / "tief"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)  # nicht das Arbeitsverzeichnis, die Konfigurationsdatei zählt
    assert _main(config, tmp_path / "tage.json") == 0


def test_main_fails_when_output_directory_is_missing(
    app_db: AppDb, config_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fill_day(app_db, DAY, pv_w=0, load_w=600, price_ct=10.0)
    out = tmp_path / "fehlt" / "tage.json"
    code = main(
        [
            "--config",
            str(config_file),
            "--von",
            "2026-10-14",
            "--bis",
            "2026-10-14",
            "--out",
            str(out),
        ]
    )
    assert code == 2 and "nicht geschrieben" in capsys.readouterr().err


def test_main_leaves_the_logging_configuration_alone(config_file: Path, tmp_path: Path) -> None:
    # Nur der Aufruf als Programm stellt die Logs ein (siehe ``scenario_days``).
    handlers = list(logging.getLogger().handlers)
    configured = structlog.is_configured()
    out = str(tmp_path / "tage.json")
    args = ["--config", str(config_file), "--von", "2026-10-15", "--bis", "2026-10-14"]
    assert main([*args, "--out", out]) == 2
    assert logging.getLogger().handlers == handlers
    assert structlog.is_configured() is configured
