import httpx
from tests.app_helpers import k, tree_input

from bindaems.app.consumers.service import Consumer
from bindaems.app.consumers.values import (
    HaRef,
    HaValueCache,
    build_tree,
    candidates,
    consumer_note,
)

URL = "http://influx.lan:8086/query"


def _results(*series: dict[str, object]) -> dict[str, object]:
    return {"results": [{"statement_id": 0, "series": list(series)}]}


def _last(name: str, object_id: str, value: float | None, unit: str | None) -> dict[str, object]:
    """Antwort auf die Abfrage der letzten Werte: eine Reihe je Messung und Entität."""
    return {
        "name": name,
        "tags": {"domain": "sensor", "entity_id": object_id},
        "columns": ["time", "v", "u"],
        "values": [[1791504000000, value, unit]],
    }


def _ha_consumer(power_ref: str, unit: str = "W") -> Consumer:
    return Consumer(1, "Serverschrank", None, None, "#000000", "ha", power_ref, unit, 0)


async def test_ha_values_single_query_and_kw_scaling(respx_mock, reader) -> None:
    route = respx_mock.get(URL).respond(
        json=_results(_last("W", "kueche", 120.0, None), _last("kW", "wp", 1.5, None))
    )
    cache = HaValueCache(reader, "homeassistant")
    kueche, wp = HaRef("sensor", "kueche", "W"), HaRef("sensor", "wp", "kW")
    await cache.refresh([kueche, wp])
    assert (cache.power_w(kueche), cache.power_w(wp)) == (120.0, 1500.0)
    assert cache.power_w(HaRef("sensor", "kueche", "kW")) is None  # andere Einheit
    request = route.calls.last.request
    assert request.url.params["db"] == "homeassistant"
    # HA schreibt je nach measurement_attr unter der Einheit oder unter der entity_id
    assert request.url.params["q"] == (
        'SELECT last("value") AS "v", last("unit_of_measurement_str") AS "u" '
        'FROM "W","kW","sensor.kueche","sensor.wp" WHERE time > now() - 24h AND '
        "((\"domain\"='sensor' AND \"entity_id\"='kueche') OR "
        "(\"domain\"='sensor' AND \"entity_id\"='wp')) "
        'GROUP BY "domain","entity_id"'
    )


async def test_ha_values_from_measurement_per_entity(respx_mock, reader) -> None:
    # measurement_attr: entity_id – die Einheit steht im Feld unit_of_measurement_str
    respx_mock.get(URL).respond(
        json=_results(
            _last("sensor.serverschrank_power", "serverschrank_power", 351.0, "W"),
            _last("sensor.wp_power", "wp_power", 1.5, "kW"),
            _last("sensor.alt_power", "alt_power", 7.0, None),  # Einheit nicht mitgeschrieben
        )
    )
    cache = HaValueCache(reader, "homeassistant")
    server = HaRef("sensor", "serverschrank_power", "W")
    wp = HaRef("sensor", "wp_power", "kW")
    old = HaRef("sensor", "alt_power", "W")
    await cache.refresh([server, wp, old])
    assert (cache.power_w(server), cache.power_w(wp), cache.power_w(old)) == (351.0, 1500.0, 7.0)
    assert (cache.note(server), cache.note(wp), cache.note(old)) == (None, None, None)


async def test_ha_values_explain_why_a_value_is_missing(respx_mock, reader) -> None:
    respx_mock.get(URL).respond(
        json=_results(
            _last("sensor.zaehler", "zaehler", 12345.6, "kWh"),  # Energie, keine Leistung
            _last("kW", "herd", 2.2, None),
            _last("sensor.text", "text", None, "W"),  # kein Zahlenwert
        )
    )
    cache = HaValueCache(reader, "homeassistant")
    counter = HaRef("sensor", "zaehler", "W")
    stove = HaRef("sensor", "herd", "W")
    text = HaRef("sensor", "text", "W")
    quiet = HaRef("sensor", "ruhig", "W")
    await cache.refresh([counter, stove, text, quiet])
    assert [cache.power_w(ref) for ref in (counter, stove, text, quiet)] == [None] * 4
    assert cache.note(counter) == "HA meldet kWh statt W"
    assert cache.note(stove) == "HA meldet kW statt W"
    assert cache.note(text) == "kein Wert in den letzten 24 h"
    assert cache.note(quiet) == "kein Wert in den letzten 24 h"
    assert cache.note(HaRef("sensor", "neu", "W")) is None  # noch nicht gelesen


async def test_ha_values_unreadable_database(respx_mock, reader) -> None:
    respx_mock.get(URL).respond(403, text='{"error":"error authorizing query"}')
    cache = HaValueCache(reader, "homeassistant")
    ref = HaRef("sensor", "kueche", "W")
    await cache.refresh([ref])
    assert (cache.power_w(ref), cache.note(ref)) == (None, "HA-Datenbank nicht lesbar")


def test_consumer_note_without_ha_database() -> None:
    consumer = _ha_consumer("sensor.serverschrank_power")
    assert consumer_note(consumer, None) == "HA-Datenbank nicht eingerichtet (influxdb.ha_database)"


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
    def influx(request: httpx.Request) -> httpx.Response:
        if request.url.params["q"].startswith("SHOW SERIES"):
            keys = [["kW,domain=sensor,entity_id=wp"], ["W,domain=sensor,entity_id=kueche"]]
            return httpx.Response(200, json=_results({"columns": ["key"], "values": keys}))
        # Messung je Entität: letzte Einheit je Reihe
        rows = [
            ("sensor.serverschrank_power", "serverschrank_power", "W"),
            ("sensor.zaehler", "zaehler", "kWh"),
            ("sensor.temperatur", "temperatur", "°C"),
            ("sensor.fremd", "anders", "W"),  # Messung passt nicht zur Entität
        ]
        series = [
            {
                "name": name,
                "tags": {"domain": "sensor", "entity_id": object_id},
                "columns": ["time", "u"],
                "values": [[1791504000000, unit]],
            }
            for name, object_id, unit in rows
        ]
        return httpx.Response(200, json=_results(*series))

    route = respx_mock.get(URL).mock(side_effect=influx)
    result = await candidates(cfg, live, reader)
    assert result["core"] == [{"ref": "load.obergeschoss.power_w"}]
    assert result["ha"] == [
        {"entity_id": "sensor.kueche", "unit": "W"},
        {"entity_id": "sensor.serverschrank_power", "unit": "W"},
        {"entity_id": "sensor.wp", "unit": "kW"},
    ]
    assert result["ha_error"] is None
    queries = [(c.request.url.params["db"], c.request.url.params["q"]) for c in route.calls]
    assert queries == [
        ("homeassistant", 'SHOW SERIES FROM "W","kW"'),
        (
            "homeassistant",
            'SELECT "unit_of_measurement_str" AS "u" FROM /^[a-z_]+\\.[a-z0-9_]+$/ '
            'WHERE time > now() - 30d GROUP BY "domain","entity_id" ORDER BY time DESC LIMIT 1',
        ),
    ]


async def test_candidates_say_why_ha_sources_are_missing(cfg, live, respx_mock, reader) -> None:
    respx_mock.get(URL).respond(403, text='{"error":"error authorizing query"}')
    result = await candidates(cfg, live, reader)
    assert result["ha"] == []
    assert result["ha_error"] == (
        "HA-Datenbank „homeassistant“ nicht lesbar: "
        'InfluxDB: HTTP 403 {"error":"error authorizing query"}'
    )
    influx = cfg.influxdb.model_copy(update={"ha_database": None})
    result = await candidates(cfg.model_copy(update={"influxdb": influx}), live, reader)
    assert result["ha_error"] == "HA-Datenbank nicht eingerichtet (influxdb.ha_database)"


def test_tree_computes_energy_and_other_energy_per_level() -> None:
    consumers, power = tree_input([k(1, 800.0), k(2, 300.0, parent=1), k(3, 500.0)])
    energy = {1: (4.0, None), 2: (1.5, None), 3: (None, "kein Verlauf seit Mitternacht")}
    root = build_tree(consumers, power, 2000.0, energy=lambda c: energy[c.id], house_kwh=12.0)
    assert (root.energy_kwh, root.other_kwh) == (12.0, None)  # Energie von V3 fehlt
    og = root.children[0]
    assert (og.energy_kwh, og.other_kwh, og.energy_note) == (4.0, 2.5, None)
    assert root.children[1].energy_note == "kein Verlauf seit Mitternacht"


def test_tree_negative_other_energy_is_clamped() -> None:
    consumers, power = tree_input([k(1, 800.0)])
    root = build_tree(consumers, power, 2000.0, energy=lambda _: (5.0, None), house_kwh=4.0)
    assert root.other_kwh == 0.0
