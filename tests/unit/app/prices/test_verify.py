import httpx
from tests.app_helpers import T_APP, mock_all_sources

from bindaems.app.prices.verify import check_prices
from bindaems.shared.timeutil import ManualClock


async def test_verify_writes_protocol_with_detection(respx_mock, tmp_path) -> None:
    mock_all_sources(respx_mock)  # die drei Aufnahmen
    async with httpx.AsyncClient() as http:
        assert await check_prices(http, ManualClock(T_APP), tmp_path) == 0
    text = (tmp_path / "pruefprotokoll-preise-2026-10-09.md").read_text()
    assert "Abruf: 09.10.2026 10:00 Uhr" in text
    assert "smartENERGY: HTTP 200, tariff EPEXSPOTAT, unit ct/kWh, interval 15" in text
    assert "smartENERGY Slots je Tag: 09.10.: 96" in text
    assert "Raster: Stundenwerte im 15-min-Raster" in text
    assert "Befunde smartENERGY: keine" in text
    assert "energy_charts: HTTP 200, 96 Slots" in text
    assert "Brutto/Netto (energy_charts): brutto, Verhältnis 1.200 aus 96 Slots" in text
    assert "Brutto/Netto (awattar): brutto" in text
    assert (
        "Ergebnis: bestanden" in text and (tmp_path / "preise-smartenergy-2026-10-09.json").exists()
    )
    assert (tmp_path / "preise-awattar-2026-10-09.json").exists()


async def test_verify_fails_without_reference(respx_mock, tmp_path) -> None:
    mock_all_sources(respx_mock, energy_charts=503, awattar=503)
    async with httpx.AsyncClient() as http:
        assert await check_prices(http, ManualClock(T_APP), tmp_path) == 1
    text = (tmp_path / "pruefprotokoll-preise-2026-10-09.md").read_text()
    assert "energy_charts: nicht verfügbar (HTTP 503)" in text
    assert "Ergebnis: nicht bestanden" in text


async def test_verify_fails_when_primary_unreachable(respx_mock, tmp_path) -> None:
    mock_all_sources(respx_mock)
    respx_mock.get("https://apis.smartenergy.at/market/v1/price").mock(
        side_effect=httpx.ConnectError("weg")
    )
    async with httpx.AsyncClient() as http:
        assert await check_prices(http, ManualClock(T_APP), tmp_path) == 1
    text = (tmp_path / "pruefprotokoll-preise-2026-10-09.md").read_text()
    assert "smartENERGY: nicht verfügbar (smartenergy: ConnectError: weg)" in text
