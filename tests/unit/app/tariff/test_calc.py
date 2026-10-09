from datetime import date, datetime, time

import pytest

from bindaems.app.tariff.calc import component_net_ct, feed_in_price, import_price
from bindaems.shared.settings import FeedInSettings, PriceComponent, TariffSettings, TimeWindow

VALUES = {"grid_usage": 8.0, "grid_loss": 0.6, "electricity_tax": 1.5, "renewables_levy": 0.5}


def tariff() -> TariffSettings:
    """Standardbestandteile mit Werten: grid_usage 8.0, grid_loss 0.6, electricity_tax 1.5,
    renewables_levy 0.5."""
    default = TariffSettings()
    components = [
        component.model_copy(update={"value_ct": VALUES[component.id]})
        if component.id in VALUES
        else component
        for component in default.components
    ]
    return default.model_copy(update={"components": components})


def tariff_with_extra(component: PriceComponent) -> TariffSettings:
    """tariff() plus ein zusätzlicher Bestandteil."""
    base = tariff()
    return base.model_copy(update={"components": [*base.components, component]})


def at(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def test_import_price_sums_components_with_vat() -> None:
    price = import_price(at("2026-10-09T08:00:00+00:00"), 10.0, tariff())
    assert price.net_ct == pytest.approx(21.8) and price.gross_ct == pytest.approx(26.16)
    assert price.parts_net["grid_usage"] == 8.0 and price.missing == ()
    assert price.parts_net["spot"] == 10.0


def test_snap_factor_in_summer_window() -> None:  # 10:00 MESZ
    price = import_price(at("2026-07-15T08:00:00+00:00"), 10.0, tariff())
    assert price.net_ct == pytest.approx(20.2)


def test_snap_window_end_is_exclusive_in_local_time() -> None:  # 16:00 MESZ
    price = import_price(at("2026-07-15T14:00:00+00:00"), 10.0, tariff())
    assert price.net_ct == pytest.approx(21.8)
    # 15:45 MESZ liegt noch im Fenster
    price = import_price(at("2026-07-15T13:45:00+00:00"), 10.0, tariff())
    assert price.net_ct == pytest.approx(20.2)


def test_snap_starts_on_first_of_april() -> None:
    price = import_price(at("2026-04-01T08:00:00+00:00"), 10.0, tariff())
    assert price.net_ct == pytest.approx(20.2)


def test_snap_not_in_october() -> None:
    price = import_price(at("2026-10-01T08:00:00+00:00"), 10.0, tariff())
    assert price.net_ct == pytest.approx(21.8)


def test_validity_dates_use_local_date() -> None:
    t = tariff_with_extra(
        PriceComponent(id="alt", name="Alt", value_ct=1.0, valid_until=date(2026, 12, 31))
    )
    # 23:45 Ortszeit am 31. 12.
    assert import_price(at("2026-12-31T22:45:00+00:00"), 10.0, t).parts_net.get("alt") == 1.0
    # 00:00 Ortszeit am 1. 1.
    assert "alt" not in import_price(at("2026-12-31T23:00:00+00:00"), 10.0, t).parts_net


def test_component_without_vat() -> None:
    t = tariff_with_extra(PriceComponent(id="frei", name="Frei", value_ct=1.0, vat=False))
    price = import_price(at("2026-10-09T08:00:00+00:00"), 10.0, t)
    assert price.gross_ct == pytest.approx(27.16)


def test_missing_values_are_reported() -> None:
    price = import_price(at("2026-10-09T08:00:00+00:00"), 10.0, TariffSettings())
    assert price.missing == ("grid_usage", "grid_loss", "electricity_tax", "renewables_levy")
    assert price.net_ct == pytest.approx(11.2)


def test_negative_spot_passes_through() -> None:
    price = import_price(at("2026-10-09T08:00:00+00:00"), -5.0, tariff())
    assert price.net_ct == pytest.approx(6.8)


def test_window_with_absolute_value() -> None:
    window = TimeWindow(start=time(0), end=time(6), value_ct=4.0)
    nacht = PriceComponent(id="nacht", name="Nacht", value_ct=2.0, windows=[window])
    t = tariff_with_extra(nacht)
    # 04:00 Ortszeit
    assert import_price(at("2026-10-09T02:00:00+00:00"), 10.0, t).parts_net["nacht"] == 4.0
    # 10:00 Ortszeit
    assert import_price(at("2026-10-09T08:00:00+00:00"), 10.0, t).parts_net["nacht"] == 2.0
    # ein Fenster mit value_ct deckt einen fehlenden Grundwert
    empty = PriceComponent(id="leer", name="Leer", windows=[window])
    assert component_net_ct(empty, at("2026-10-09T02:00:00+00:00"), 10.0) == 4.0
    missing = import_price(at("2026-10-09T02:00:00+00:00"), 10.0, tariff_with_extra(empty))
    assert missing.missing == ()


def test_feed_in_uses_latest_month_not_after_slot() -> None:
    feed_in = FeedInSettings(monthly_ct={"2026-08": 6.1, "2026-09": 7.3})
    assert feed_in_price(at("2026-10-09T08:00:00+00:00"), feed_in) == 7.3
    # 23:45 Ortszeit am 31. 8.
    assert feed_in_price(at("2026-08-31T21:45:00+00:00"), feed_in) == 6.1
    assert feed_in_price(at("2026-07-10T08:00:00+00:00"), feed_in) is None


def test_feed_in_month_boundary_in_local_time() -> None:
    feed_in = FeedInSettings(monthly_ct={"2026-08": 6.1, "2026-09": 7.3})
    # 00:00 Ortszeit am 1. 9.
    assert feed_in_price(at("2026-08-31T22:00:00+00:00"), feed_in) == 7.3
