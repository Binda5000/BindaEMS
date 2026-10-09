from tests.app_helpers import k, tree_input

from bindaems.app.consumers.values import HaRef, HaValueCache, build_tree, candidates

URL = "http://influx.lan:8086/query"


async def test_ha_values_single_query_and_kw_scaling(respx_mock, reader) -> None:
    route = respx_mock.get(URL).respond(
        json={
            "results": [
                {
                    "series": [
                        {
                            "name": "W",
                            "tags": {"domain": "sensor", "entity_id": "kueche"},
                            "columns": ["time", "v"],
                            "values": [[1791504000000, 120.0]],
                        },
                        {
                            "name": "kW",
                            "tags": {"domain": "sensor", "entity_id": "wp"},
                            "columns": ["time", "v"],
                            "values": [[1791504000000, 1.5]],
                        },
                    ]
                }
            ]
        }
    )
    cache = HaValueCache(reader, "homeassistant")
    kueche, wp = HaRef("sensor", "kueche", "W"), HaRef("sensor", "wp", "kW")
    await cache.refresh([kueche, wp])
    assert (cache.power_w(kueche), cache.power_w(wp)) == (120.0, 1500.0)
    assert cache.power_w(HaRef("sensor", "kueche", "kW")) is None  # andere Einheit
    request = route.calls.last.request
    assert request.url.params["db"] == "homeassistant"
    assert request.url.params["q"] == (
        'SELECT last("value") AS "v" FROM "W","kW" WHERE time > now() - 24h AND '
        "((\"domain\"='sensor' AND \"entity_id\"='kueche') OR "
        "(\"domain\"='sensor' AND \"entity_id\"='wp')) "
        'GROUP BY "domain","entity_id"'
    )


def test_tree_computes_other_per_level() -> None:
    consumers, power = tree_input(
        [k(1, 800.0), k(2, 300.0, parent=1), k(3, 200.0, parent=1), k(4, 500.0)]
    )
    root = build_tree(consumers, power, house_w=2000.0)
    assert (root.name, root.id, root.power_w, root.other_w) == ("Haus", None, 2000.0, 700.0)
    assert [child.id for child in root.children] == [1, 4]
    assert root.children[0].other_w == 300.0 and root.children[0].children[0].other_w is None


def test_tree_negative_other_is_clamped_and_flagged() -> None:
    consumers, power = tree_input([k(1, 1500.0), k(2, 900.0)])
    root = build_tree(consumers, power, house_w=2000.0)
    assert (root.other_w, root.mismatch) == (0.0, True)


def test_tree_unknown_value_makes_other_unknown() -> None:
    consumers, power = tree_input([k(1, None), k(2, 900.0)])
    assert build_tree(consumers, power, house_w=2000.0).other_w is None


async def test_candidates_from_core_signals_and_ha_series(cfg, live, respx_mock, reader) -> None:
    route = respx_mock.get(URL).respond(
        json={
            "results": [
                {
                    "series": [
                        {
                            "columns": ["key"],
                            "values": [
                                ["kW,domain=sensor,entity_id=wp"],
                                ["W,domain=sensor,entity_id=kueche"],
                            ],
                        }
                    ]
                }
            ]
        }
    )
    result = await candidates(cfg, live, reader)
    assert result["core"] == [{"ref": "load.obergeschoss.power_w"}]
    assert result["ha"] == [
        {"entity_id": "sensor.kueche", "unit": "W"},
        {"entity_id": "sensor.wp", "unit": "kW"},
    ]
    params = route.calls.last.request.url.params
    assert (params["db"], params["q"]) == ("homeassistant", 'SHOW SERIES FROM "W","kW"')
