"""respx liefert die Aufnahmen; clock = T_APP (09.10. 10:00 Ortszeit)."""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from tests.helpers import FakeSink

from bindaems.app.prices.pipeline import DayResult, PricePipeline
from bindaems.app.prices.service import PriceService
from bindaems.app.prices.sources import EnergyChartsSource, SmartEnergySource, make_reference
from bindaems.app.prices.store import PriceStore
from bindaems.app.settings.service import SettingsService
from bindaems.shared.timeutil import SLOT

FIX_SE = Path("tests/fixtures/smartenergy_2026-10-09.json")
FIX_EC = Path("tests/fixtures/energycharts_at_2026-10-09.json")
T_FIRST = datetime(2026, 10, 8, 22, tzinfo=UTC)
T_END = datetime(2026, 10, 9, 22, tzinfo=UTC)

Response = Path | str | int  # Aufnahme, geänderter Körper oder HTTP-Status


def _changed(fixture: Path, value: Callable[[float], float]) -> str:
    data = json.loads(fixture.read_text())
    for entry in data["data"]:
        entry["value"] = value(entry["value"])
    return json.dumps(data)


def all_zero(fixture: Path) -> str:
    return _changed(fixture, lambda _: 0.0)


def scaled(fixture: Path, factor: float) -> str:
    return _changed(fixture, lambda v: v * factor)


def two_days_primary(tomorrow_factor: float) -> str:
    """Aufnahme plus derselbe Tag einen Tag später, Werte mal ``tomorrow_factor``."""
    data = json.loads(FIX_SE.read_text())
    tomorrow = [
        {
            "date": (datetime.fromisoformat(entry["date"]) + timedelta(days=1)).isoformat(),
            "value": entry["value"] * tomorrow_factor,
        }
        for entry in data["data"]
    ]
    data["data"] += tomorrow
    return json.dumps(data)


def two_days_reference(tiny_hours_tomorrow: int = 0) -> str:
    """Referenz für beide Tage; morgen haben die ersten Stunden auf Wunsch fast 0 ct."""
    data = json.loads(FIX_EC.read_text())
    tomorrow = list(data["price"])
    for index in range(4 * tiny_hours_tomorrow):
        tomorrow[index] = 1.0  # 0,1 ct – zählt nicht für die Erkennung
    data["unix_seconds"] = data["unix_seconds"] + [t + 86400 for t in data["unix_seconds"]]
    data["price"] = data["price"] + tomorrow
    return json.dumps(data)


def set_vat_mode(service: SettingsService, mode: str) -> None:
    current = service.current()
    prices = current.settings.prices.model_copy(update={"vat_mode": mode})
    settings = current.settings.model_copy(update={"prices": prices})
    service.update(settings, base_version=current.version, actor="test", source="ui")


def _route(respx_mock, url: str, response: Response) -> None:
    if isinstance(response, int):
        respx_mock.get(url).respond(response, text="Wartung")
    else:
        body = response.read_text() if isinstance(response, Path) else response
        respx_mock.get(url).respond(200, text=body)


@pytest.fixture
def pipeline_env(engine, clock, settings_service, respx_mock):
    def build(
        primary: Response, reference: Response, store: PriceStore | None = None
    ) -> tuple[PricePipeline, PriceStore, FakeSink]:
        store = store or PriceStore(engine, clock)
        _route(respx_mock, SmartEnergySource.URL, primary)
        _route(respx_mock, EnergyChartsSource.URL, reference)
        http = httpx.AsyncClient()
        sink = FakeSink()
        pipeline = PricePipeline(
            store,
            SmartEnergySource(http, clock),
            lambda name: make_reference(name, http, clock),
            settings_service,
            PriceService(store, settings_service),
            clock,
            sink,
        )
        return pipeline, store, sink

    return build


async def test_refresh_stores_net_prices_from_recorded_day(pipeline_env) -> None:
    pipeline, store, _ = pipeline_env(primary=FIX_SE, reference=FIX_EC)
    status = await pipeline.refresh()
    assert status.vat_mode == "gross" and status.vat_detection.result == "gross"
    stored = store.get(T_FIRST, T_END)
    first = stored[0]
    assert len(stored) == 96 and (first.spot_raw_ct, first.origin) == (17.824, "primary")
    assert first.spot_net_ct == pytest.approx(14.8533, abs=1e-4)
    assert first.reference_ct == pytest.approx(15.45) and first.raw_id is not None
    assert status.days == [DayResult(date(2026, 10, 9), "primary", [])] and status.errors == []
    assert status.last_success == status.last_attempt is not None
    component = pipeline.component_status()
    assert (component.name, component.ok, component.message) == (
        "prices",
        True,
        "Preise bis 09.10. 23:45",
    )


async def test_all_zero_day_falls_back_to_reference(pipeline_env) -> None:
    pipeline, store, _ = pipeline_env(primary=all_zero(FIX_SE), reference=FIX_EC)
    status = await pipeline.refresh()
    stored = store.get(T_FIRST, T_END)
    assert {s.origin for s in stored} == {"fallback"} and stored[0].spot_net_ct == pytest.approx(
        15.45
    )
    assert stored[0].spot_raw_ct is None
    assert status.errors == [
        "09.10.: smartENERGY verdächtig (alle Werte gleich (0.000 ct)) – Ersatzquelle energy_charts"
    ]


async def test_missing_primary_falls_back_to_reference(pipeline_env) -> None:
    pipeline, store, _ = pipeline_env(primary=503, reference=FIX_EC)
    status = await pipeline.refresh()
    assert {s.origin for s in store.get(T_FIRST, T_END)} == {"fallback"}
    assert status.errors == [
        "smartenergy: HTTP 503",
        "09.10.: keine Daten von smartENERGY – Ersatzquelle energy_charts",
    ]
    assert status.days[0].origin == "fallback"


async def test_suspicious_day_without_reference_stores_nothing(pipeline_env) -> None:
    pipeline, store, _ = pipeline_env(primary=all_zero(FIX_SE), reference=503)
    status = await pipeline.refresh()
    assert store.get(datetime(2026, 10, 8, tzinfo=UTC), datetime(2026, 10, 10, tzinfo=UTC)) == []
    assert status.days[0].findings == ["alle Werte gleich (0.000 ct)"]
    assert status.days[0].origin is None and status.last_success is None
    assert not pipeline.component_status().ok


async def test_undetermined_vat_uses_fallback_setting_with_warning(pipeline_env) -> None:
    pipeline, _, _ = pipeline_env(primary=scaled(FIX_SE, 1.1 / 1.2), reference=FIX_EC)
    status = await pipeline.refresh()
    assert status.vat_mode == "gross"
    assert (
        "Brutto/Netto nicht erkennbar (Verhältnis 1.100) – Ersatzeinstellung „brutto“ aktiv"
        in status.errors
    )


async def test_manual_vat_mode_overrides_detection(pipeline_env, settings_service) -> None:
    set_vat_mode(settings_service, "net")
    pipeline, store, _ = pipeline_env(primary=FIX_SE, reference=FIX_EC)
    status = await pipeline.refresh()
    assert status.vat_mode == "net" and status.vat_detection.result == "gross"
    assert store.get(T_FIRST, T_FIRST + SLOT)[0].spot_net_ct == 17.824
    assert "Brutto/Netto: eingestellt „netto“, erkannt „brutto“ (Verhältnis 1.200)" in status.errors


async def test_restore_redetects_vat_from_stored_slots(pipeline_env) -> None:
    pipeline, store, _ = pipeline_env(primary=FIX_SE, reference=FIX_EC)
    await pipeline.refresh()
    restored, _, _ = pipeline_env(primary=503, reference=503, store=store)
    restored.restore()
    assert (await restored.refresh()).vat_mode == "gross"


async def test_points_written_to_long_rp(pipeline_env) -> None:
    pipeline, _, sink = pipeline_env(primary=FIX_SE, reference=FIX_EC)
    await pipeline.refresh()
    kinds = {p.tags["kind"] for p in sink.points if p.measurement == "price"}
    assert kinds == {"spot_raw", "reference", "import_gross"} and {p.rp for p in sink.points} == {
        "long"
    }
    gross = [p for p in sink.points if p.tags["kind"] == "import_gross"]
    assert len(gross) == 96 and gross[0].ts == T_FIRST
    assert gross[0].fields["ct_kwh"] == pytest.approx((17.824 / 1.2 + 1.2) * 1.2)


async def test_settings_change_republishes_import_prices(pipeline_env, settings_service) -> None:
    pipeline, _, sink = pipeline_env(primary=FIX_SE, reference=FIX_EC)
    await pipeline.refresh()
    sink.points.clear()
    pipeline.republish()
    assert {p.tags["kind"] for p in sink.points} == {"import_gross"}
    assert min(p.ts for p in sink.points) == datetime(2026, 10, 9, 8, tzinfo=UTC)  # ab jetzt


async def test_switch_to_net_after_earlier_detection_goes_to_fallback(pipeline_env) -> None:
    pipeline, store, _ = pipeline_env(primary=FIX_SE, reference=FIX_EC)
    await pipeline.refresh()  # erkennt „brutto“
    pipeline_env(primary=two_days_primary(1 / 1.2), reference=two_days_reference(), store=store)
    status = await pipeline.refresh()
    assert {d.day: d.origin for d in status.days} == {
        date(2026, 10, 9): "primary",
        date(2026, 10, 10): "fallback",
    }
    assert any(
        e.startswith("Brutto/Netto nicht erkennbar (Verhältnis 1.1") and "zuletzt erkannt" in e
        for e in status.errors
    )
    assert any(
        e.startswith("10.10.: smartENERGY verdächtig (Brutto/Netto passt nicht")
        for e in status.errors
    )
    tomorrow = store.get(T_END, T_END + timedelta(days=1))
    assert len(tomorrow) == 96 and {s.origin for s in tomorrow} == {"fallback"}


async def test_net_day_is_caught_even_when_overall_detection_says_gross(pipeline_env) -> None:
    pipeline, store, _ = pipeline_env(
        primary=two_days_primary(1 / 1.2), reference=two_days_reference(tiny_hours_tomorrow=4)
    )
    status = await pipeline.refresh()
    assert status.vat_detection.result == "gross"  # 24 Stunden brutto gegen 20 Stunden netto
    assert {d.day: d.origin for d in status.days} == {
        date(2026, 10, 9): "primary",
        date(2026, 10, 10): "fallback",
    }
    assert {s.origin for s in store.get(T_END, T_END + timedelta(days=1))} == {"fallback"}
