from datetime import UTC, datetime, timedelta

from bindaems.app.history.series import build_catalog, build_query, choose_rp_and_step

T0 = datetime(2026, 10, 9, tzinfo=UTC)  # 1791504000000 ms


def test_catalog_from_config(cfg) -> None:
    catalog = build_catalog(cfg)
    assert list(catalog) == [
        "grid",
        "pv",
        "battery",
        "house",
        "consumption",
        "wallbox.evcs",
        "wallbox.twc",
        "soc.battery",
        "soc.tesla",
        "soc.egolf",
        "price",
        "forecast.pv",
    ]
    assert catalog["soc.egolf"].label == "SOC e-Golf"
    assert dict(catalog["wallbox.twc"].tags) == {"source": "wallbox", "id": "twc", "phase": "total"}
    assert (catalog["forecast.pv"].raw, catalog["forecast.pv"].scale) == (False, 1000.0)


def test_query_text_for_one_hour(cfg) -> None:
    spec = build_catalog(cfg)["grid"]
    assert build_query(spec, "raw", T0, T0 + timedelta(hours=1), 10) == (
        'SELECT mean("p_w") AS "v" FROM "raw"."power" WHERE "id"=\'grid\' AND "phase"=\'total\' '
        "AND \"source\"='grid' AND time >= 1791504000000ms AND time < 1791507600000ms "
        "GROUP BY time(10s) fill(none)"
    )


def test_choose_rp_and_step(cfg) -> None:
    catalog, now = build_catalog(cfg), T0 + timedelta(days=1)
    grid, price = catalog["grid"], catalog["price"]
    assert choose_rp_and_step(grid, T0, T0 + timedelta(hours=1), now) == ("raw", 10)
    assert choose_rp_and_step(grid, T0, T0 + timedelta(days=1), now) == ("raw", 300)
    assert choose_rp_and_step(grid, T0, T0 + timedelta(days=3), now) == ("long", 300)
    old = T0 - timedelta(days=100)
    assert choose_rp_and_step(grid, old, old + timedelta(hours=1), now) == ("long", 60)
    assert choose_rp_and_step(price, T0, T0 + timedelta(hours=1), now) == ("long", 60)
