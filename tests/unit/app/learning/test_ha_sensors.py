from datetime import UTC, datetime, timedelta

from structlog.testing import capture_logs

from bindaems.app.consumers.values import NOT_CONFIGURED
from bindaems.app.history.influx import Series
from bindaems.app.learning.ha_sensors import HaSensorReader, SensorCandidate
from bindaems.shared.influx.lineprotocol import epoch_ms

FIRST = datetime(2026, 10, 16, 6, 0, tzinfo=UTC)
MINUTE = timedelta(minutes=1)
TEMP = "sensor.aussentemperatur"


def _last(
    name: str, object_id: str, value: float | None, unit: str | None, at: datetime = FIRST
) -> Series:
    """Letzte Werte einer Reihe: Messung ``name`` (Einheit oder Entität), Tags der Entität."""
    return Series(
        name,
        {"domain": "sensor", "entity_id": object_id},
        ["time", "v", "u"],
        [[epoch_ms(at), value, unit]],
    )


def _minutes(name: str, object_id: str, start: datetime, count: int, value: float) -> Series:
    rows = [[epoch_ms(start + i * MINUTE), value, None] for i in range(count)]
    return Series(name, {"domain": "sensor", "entity_id": object_id}, ["time", "w", "u"], rows)


def _seed(name: str, object_id: str, value: float) -> Series:
    row = [epoch_ms(FIRST - timedelta(days=2)), value, None]
    return Series(name, {"domain": "sensor", "entity_id": object_id}, ["time", "w", "u"], [row])


async def test_candidates_list_numeric_entities_of_both_schemas(fake_reader) -> None:
    fake_reader.answer(
        'last("value")',
        [
            _last(TEMP, "aussentemperatur", 7.5, "°C"),  # Messung je Entität, Einheit im Feld
            _last("%", "luftfeuchte", 55.0, None),  # Messung je Einheit
            _last("sensor.anwesend", "anwesend", 1.0, None),  # an/aus als Zahl, ohne Einheit
            _last("sensor.status", "status", None, None),  # kein Zahlenwert
            _last("sensor.alt", "alt", 3.0, "°C"),
            _last("%", "alt", 4.0, None, FIRST + timedelta(days=1)),  # jüngere Reihe gilt
        ],
    )
    result, error = await HaSensorReader(fake_reader, "homeassistant").candidates()
    assert error is None
    assert result == [
        SensorCandidate("sensor.alt", "%"),
        SensorCandidate("sensor.anwesend", None),
        SensorCandidate(TEMP, "°C"),
        SensorCandidate("sensor.luftfeuchte", "%"),
    ]
    (query,) = fake_reader.texts("FROM /.*/")
    assert "time > now() - 30d" in query and 'GROUP BY "domain","entity_id"' in query


async def test_candidates_without_database_name_the_reason(fake_reader) -> None:
    assert await HaSensorReader(fake_reader, None).candidates() == ([], NOT_CONFIGURED)
    assert await HaSensorReader(None, None).candidates() == ([], NOT_CONFIGURED)
    assert fake_reader.queries == []
    assert await HaSensorReader(None, "homeassistant").candidates() == ([], None)


async def test_candidates_say_why_ha_is_unreadable(fake_reader, unreadable) -> None:
    fake_reader.answer("SELECT", unreadable())
    assert await HaSensorReader(fake_reader, "homeassistant").candidates() == (
        [],
        "HA-Datenbank „homeassistant“ nicht lesbar: "
        'InfluxDB: HTTP 403 {"error":"error authorizing query"}',
    )


async def test_slot_values_average_minutes_and_seed_from_before(fake_reader) -> None:
    # der Wert 4,0 vor dem Bereich gilt bis zur ersten Minute mit Wert (06:05), danach 10,0
    fake_reader.answer(
        "fill(previous)", [_minutes(TEMP, "aussentemperatur", FIRST + 5 * MINUTE, 25, 10.0)]
    )
    fake_reader.answer("time <", [_seed(TEMP, "aussentemperatur", 4.0)])
    sensors = HaSensorReader(fake_reader, "homeassistant")
    values = await sensors.slot_values([TEMP], FIRST, FIRST + timedelta(minutes=30))
    assert values == {
        TEMP: {FIRST: (5 * 4.0 + 10 * 10.0) / 15, FIRST + timedelta(minutes=15): 10.0}
    }
    assert {database for _, database in fake_reader.queries} == {"homeassistant"}
    (series,) = fake_reader.texts("fill(previous)")
    # ohne gemerkte Einheiten nur die Messung je Entität
    assert 'FROM "sensor.aussentemperatur" WHERE' in series
    assert f"time >= {epoch_ms(FIRST)}ms AND time < {epoch_ms(FIRST) + 30 * 60_000}ms" in series
    assert 'GROUP BY time(60s),"domain","entity_id" fill(previous)' in series


async def test_slot_values_read_measurements_per_unit_found_by_candidates(fake_reader) -> None:
    fake_reader.answer('last("value")', [_last("%", "luftfeuchte", 55.0, None)])
    fake_reader.answer("fill(previous)", [_minutes("%", "luftfeuchte", FIRST, 15, 61.5)])
    sensors = HaSensorReader(fake_reader, "homeassistant")
    await sensors.candidates()
    values = await sensors.slot_values(["sensor.luftfeuchte"], FIRST, FIRST + timedelta(minutes=15))
    assert values == {"sensor.luftfeuchte": {FIRST: 61.5}}  # keine Umrechnung der Einheit
    (series,) = fake_reader.texts("fill(previous)")
    assert 'FROM "%","sensor.luftfeuchte" WHERE' in series


async def test_unknown_entity_yields_no_values(fake_reader) -> None:
    fake_reader.answer("fill(previous)", [_minutes(TEMP, "aussentemperatur", FIRST, 15, 10.0)])
    sensors = HaSensorReader(fake_reader, "homeassistant")
    values = await sensors.slot_values(
        ["sensor.unbekannt", TEMP], FIRST, FIRST + timedelta(hours=1)
    )
    assert set(values) == {TEMP}
    assert list(values[TEMP]) == [FIRST]  # nur der Slot mit Minuten
    assert await sensors.slot_values(["sensor.unbekannt"], FIRST, FIRST + timedelta(hours=1)) == {}
    assert await sensors.slot_values([], FIRST, FIRST + timedelta(hours=1)) == {}


async def test_query_error_yields_empty_result_and_one_warning(
    fake_reader, unreadable, caplog
) -> None:
    fake_reader.answer("SELECT", unreadable())
    sensors = HaSensorReader(fake_reader, "homeassistant")
    with capture_logs() as logs:
        values = await sensors.slot_values([TEMP], FIRST, FIRST + timedelta(hours=1))
    assert values == {}
    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert len(warnings) == 1 and "HA-Sensoren" in warnings[0]["event"]


async def test_slot_values_without_database_or_reader_are_empty(fake_reader) -> None:
    end = FIRST + timedelta(hours=1)
    assert await HaSensorReader(fake_reader, None).slot_values([TEMP], FIRST, end) == {}
    assert await HaSensorReader(None, "homeassistant").slot_values([TEMP], FIRST, end) == {}
    assert fake_reader.queries == []
