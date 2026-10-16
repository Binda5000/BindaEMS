import json
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from tests.app_helpers import T_APP, price_row, sample_inputs

from bindaems.app.ha.entities import (
    EntitySpec,
    HaInputs,
    build_entities,
    build_inputs,
    discovery,
    on_off,
)
from bindaems.app.prices.service import PriceService
from bindaems.shared.config import Config
from bindaems.shared.control import ChargeMode, Intent, VehicleIntent


def entity(cfg: Config, object_id: str) -> EntitySpec:
    return next(e for e in build_entities(cfg) if e.object_id == object_id)


def state(cfg: Config, object_id: str, inputs: HaInputs) -> str:
    return entity(cfg, object_id).value(inputs)


def config_of(cfg: Config, object_id: str) -> dict:
    return json.loads(discovery(entity(cfg, object_id), cfg.homeassistant.mqtt)[1])


def test_entities_from_config(cfg) -> None:
    ids = [e.object_id for e in build_entities(cfg)]
    assert ids == [
        "price_now",
        "feed_in_price",
        "pv_forecast_today",
        "pv_forecast_tomorrow",
        "grid_power",
        "pv_power",
        "battery_power",
        "house_power",
        "battery_soc",
        "ems_mode",
        "core_online",
        "problem_competitor",
        "problem_prices",
        "problem_device_offline",
        "watchdog_tripped",
        "regulation",
        "guest_mode",
        "problem_assignment_unclear",
        "tesla_soc",
        "tesla_mode",
        "tesla_boost",
        "tesla_boost_stop",
        "tesla_wallbox",
        "egolf_soc",
        "egolf_mode",
        "egolf_boost",
        "egolf_boost_stop",
        "egolf_wallbox",
        "evcs_power",
        "evcs_assignment",
        "evcs_occupant",
        "twc_power",
        "twc_assignment",
        "twc_occupant",
    ]
    names = {e.object_id: e.device.name for e in build_entities(cfg)}
    assert (names["egolf_soc"], names["twc_power"]) == (
        "BindaEMS e-Golf",
        "BindaEMS Wall Connector",
    )
    assert names["evcs_power"] == "BindaEMS EVCS"


def test_object_ids_are_sanitized() -> None:
    data = yaml.safe_load(Path("deploy/config.example.yaml").read_text())
    data["wallboxes"]["Garage-1"] = data["wallboxes"].pop("twc")
    data["vehicles"]["tesla"]["default_wallbox"] = "Garage-1"
    cfg = Config.model_validate(data)
    spec = entity(cfg, "garage_1_power")
    assert spec.device.identifier == "bindaems_garage_1"
    inputs = sample_inputs(derived={"wallbox_w": {"Garage-1": 1234.4}})
    assert spec.value(inputs) == "1234"


def test_discovery_payload_for_price_sensor(cfg) -> None:
    spec = entity(cfg, "price_now")
    topic, payload = discovery(spec, cfg.homeassistant.mqtt)
    data = json.loads(payload)
    assert topic == "homeassistant/sensor/bindaems/price_now/config"
    assert data["unique_id"] == "bindaems_price_now" and data["has_entity_name"] is True
    assert (data["state_topic"], data["availability_topic"]) == (
        "bindaems/state/price_now",
        "bindaems/status",
    )
    assert data["json_attributes_topic"] == "bindaems/state/price_now/attributes"
    assert data["device"]["identifiers"] == ["bindaems"] and data["origin"]["name"] == "BindaEMS"
    assert data["device"]["manufacturer"] == "BindaEMS" and data["name"] == "Bezugspreis"
    assert (data["unit_of_measurement"], data["state_class"]) == ("ct/kWh", "measurement")
    assert payload == json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def test_binary_sensor_payloads(cfg) -> None:
    topic, payload = discovery(entity(cfg, "problem_prices"), cfg.homeassistant.mqtt)
    data = json.loads(payload)
    assert topic == "homeassistant/binary_sensor/bindaems/problem_prices/config"
    assert (data["device_class"], data["payload_on"], data["payload_off"]) == (
        "problem",
        "ON",
        "OFF",
    )
    core = json.loads(discovery(entity(cfg, "core_online"), cfg.homeassistant.mqtt)[1])
    assert (core["device_class"], core["entity_category"]) == ("connectivity", "diagnostic")


def test_values_formatted_and_unknown_is_none(cfg) -> None:
    inputs = sample_inputs(price_now_ct=13.4449, core_connected=False, derived={})
    assert entity(cfg, "price_now").value(inputs) == "13.44"
    assert entity(cfg, "grid_power").value(inputs) == "None"
    assert entity(cfg, "problem_competitor").value(inputs) == "None"
    assert entity(cfg, "core_online").value(inputs) == "OFF"
    assert entity(cfg, "pv_forecast_today").value(inputs) == "12.3"
    assert entity(cfg, "battery_soc").value(inputs) == "55.0"
    assert entity(cfg, "ems_mode").value(sample_inputs(mode=None)) == "None"


def test_problem_sensors(cfg) -> None:
    competitor = entity(cfg, "problem_competitor")
    assert competitor.value(sample_inputs(alarm_ids=["competitor.dess"])) == "ON"
    assert competitor.value(sample_inputs(alarm_ids=["grid.phase"])) == "OFF"
    offline = entity(cfg, "problem_device_offline")
    assert offline.value(sample_inputs(adapters_offline=["twc"])) == "ON"
    assert offline.value(sample_inputs(adapters_offline=None)) == "None"
    assert offline.attributes(sample_inputs(adapters_offline=["twc"])) == {"adapters": ["twc"]}


def test_watchdog_tripped_entity(cfg) -> None:
    spec = next(e for e in build_entities(cfg) if e.object_id == "watchdog_tripped")
    assert (spec.component, spec.name, spec.device_class) == (
        "binary_sensor",
        "Watchdog ausgelöst",
        "problem",
    )
    assert spec.value(sample_inputs(watchdog_state="tripped")) == "ON"
    assert spec.value(sample_inputs(watchdog_state="armed")) == "OFF"
    assert spec.value(sample_inputs(watchdog_state="unknown")) == on_off(None)
    # ohne Auskunft (core nicht verbunden oder Watchdog fehlt im Bericht) ist der Wert unbekannt
    assert spec.value(sample_inputs(watchdog_state=None)) == on_off(None)
    assert {spec.value(sample_inputs(watchdog_state=s)) for s in ("waiting", "disabled")} == {"OFF"}
    topic, payload = discovery(spec, cfg.homeassistant.mqtt)
    data = json.loads(payload)
    assert topic == "homeassistant/binary_sensor/bindaems/watchdog_tripped/config"
    assert (data["unique_id"], data["device"]["identifiers"]) == (
        "bindaems_watchdog_tripped",
        ["bindaems"],
    )


def test_price_attributes_list_local_times_up_to_36h(cfg) -> None:
    ahead = [(T_APP + timedelta(minutes=15 * i), 10.0 + i / 3) for i in range(200)]
    attributes = entity(cfg, "price_now").attributes(sample_inputs(prices_ahead=ahead))
    prices = attributes["prices"]
    assert len(prices) == 144 and prices[0] == {"start": "2026-10-09T10:00:00+02:00", "ct": 10.0}
    assert prices[1]["ct"] == 10.33


def test_build_inputs_from_live_prices_and_forecast(
    live, price_service, store, forecast_service
) -> None:
    inputs = build_inputs(live, price_service, store, forecast_service, T_APP)
    assert inputs.derived["grid_w"] == 512.0 and inputs.signals["battery.soc_pct"] == 55.0
    assert "vehicle.tesla.soc_pct" not in inputs.signals  # Qualität nicht ok
    assert inputs.price_now_ct == pytest.approx(13.44) and not inputs.prices_missing
    assert inputs.pv_today_kwh == pytest.approx(2.0) and inputs.pv_tomorrow_kwh is None
    assert (inputs.mode, inputs.alarm_ids, inputs.adapters_offline) == (
        "OBSERVE",
        ["competitor.dess"],
        ["twc"],
    )


def test_build_inputs_without_core_and_prices(
    live, store, settings_service, forecast_service
) -> None:
    live.connected, live.health = False, None
    inputs = build_inputs(
        live, PriceService(store, settings_service), store, forecast_service, T_APP
    )
    assert (inputs.derived, inputs.signals, inputs.adapters_offline) == ({}, {}, None)
    assert inputs.price_now_ct is None and inputs.prices_missing


def test_prices_missing_after_16_when_tomorrow_incomplete(
    live, price_service, store, forecast_service
) -> None:
    afternoon = T_APP.replace(hour=14)  # 16:00 Ortszeit
    store.save([price_row(afternoon, 10.0, "primary")])
    inputs = build_inputs(live, price_service, store, forecast_service, afternoon)
    assert inputs.price_now_ct is not None and inputs.prices_missing


def test_build_inputs_reads_watchdog_state(live, price_service, store, forecast_service) -> None:
    live.health = {**live.health, "watchdog": {"state": "tripped"}}
    inputs = build_inputs(live, price_service, store, forecast_service, T_APP)
    assert inputs.watchdog_state == "tripped"
    live.health = {k: v for k, v in live.health.items() if k != "watchdog"}
    inputs = build_inputs(live, price_service, store, forecast_service, T_APP)
    assert inputs.watchdog_state is None


@pytest.mark.parametrize("watchdog", ["tripped", {"state": 2}, {}, None])
def test_build_inputs_ignores_a_malformed_watchdog_report(
    watchdog, live, price_service, store, forecast_service
) -> None:
    live.health = {**live.health, "watchdog": watchdog}
    inputs = build_inputs(live, price_service, store, forecast_service, T_APP)
    assert inputs.watchdog_state is None


def test_build_inputs_ignores_the_watchdog_of_an_old_report_without_core(
    live, price_service, store, forecast_service
) -> None:
    live.health = {**live.health, "watchdog": {"state": "tripped"}}
    live.connected = False
    inputs = build_inputs(live, price_service, store, forecast_service, T_APP)
    assert inputs.watchdog_state is None


MODES = ["PV-Überschuss", "Min+PV", "Sofort", "Aus"]


def test_discovery_of_the_mode_select(cfg) -> None:
    topic, _ = discovery(entity(cfg, "tesla_mode"), cfg.homeassistant.mqtt)
    data = config_of(cfg, "tesla_mode")
    assert topic == "homeassistant/select/bindaems/tesla_mode/config"
    assert data["command_topic"] == "bindaems/cmd/tesla_mode"
    assert data["state_topic"] == "bindaems/state/tesla_mode"
    assert data["options"] == MODES and data["name"] == "Lademodus"
    assert data["device"]["identifiers"] == ["bindaems_tesla"]


def test_discovery_of_the_assignment_select_names_the_vehicles(cfg) -> None:
    data = config_of(cfg, "evcs_assignment")
    assert data["options"] == ["Automatisch", "Tesla Model 3", "e-Golf"]
    assert (data["name"], data["command_topic"]) == ("Zuordnung", "bindaems/cmd/evcs_assignment")


def test_discovery_of_the_switch_and_the_guest_select(cfg) -> None:
    switch = config_of(cfg, "regulation")
    assert (switch["name"], switch["payload_on"], switch["payload_off"]) == (
        "Regelbetrieb",
        "ON",
        "OFF",
    )
    assert switch["command_topic"] == "bindaems/cmd/regulation"
    guest = config_of(cfg, "guest_mode")
    assert (guest["name"], guest["options"]) == ("Gast-Lademodus", MODES)


def test_discovery_of_the_buttons_has_no_state(cfg) -> None:
    boost, stop = config_of(cfg, "tesla_boost"), config_of(cfg, "tesla_boost_stop")
    assert (boost["name"], stop["name"]) == ("Boost", "Boost beenden")
    assert boost["payload_press"] == stop["payload_press"] == "PRESS"
    assert boost["command_topic"] == "bindaems/cmd/tesla_boost"
    assert "state_topic" not in boost
    topic, _ = discovery(entity(cfg, "tesla_boost"), cfg.homeassistant.mqtt)
    assert topic == "homeassistant/button/bindaems/tesla_boost/config"


def test_sensors_have_no_command_topic(cfg) -> None:
    assert "command_topic" not in config_of(cfg, "price_now")
    assert "command_topic" not in config_of(cfg, "tesla_wallbox")
    assert config_of(cfg, "tesla_wallbox")["name"] == "Wallbox"
    assert config_of(cfg, "evcs_occupant")["name"] == "Fahrzeug"
    assert config_of(cfg, "problem_assignment_unclear")["name"] == "Zuordnung unklar"


def test_states_follow_the_intent(cfg) -> None:
    inputs = sample_inputs(
        intent=Intent(
            version=3, control=True, vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW)}
        )
    )
    assert state(cfg, "tesla_mode", inputs) == "Sofort"
    assert state(cfg, "egolf_mode", inputs) == "PV-Überschuss"
    assert state(cfg, "regulation", inputs) == "ON"
    assert state(cfg, "evcs_assignment", inputs) == "Automatisch"
    assert state(cfg, "guest_mode", inputs) == "PV-Überschuss"


def test_assignment_state_names_the_overriding_vehicle(cfg) -> None:
    intent = Intent(version=3, vehicles={"egolf": VehicleIntent(wallbox="twc")})
    inputs = sample_inputs(intent=intent)
    assert state(cfg, "twc_assignment", inputs) == "e-Golf"
    assert state(cfg, "evcs_assignment", inputs) == "Automatisch"


def test_guest_mode_state(cfg) -> None:
    intent = Intent.model_validate({"version": 1, "guest": {"mode": "off"}})
    assert state(cfg, "guest_mode", sample_inputs(intent=intent)) == "Aus"


def test_control_states_are_unknown_without_an_intent(cfg) -> None:
    inputs = sample_inputs(intent=None)
    for oid in ("tesla_mode", "regulation", "guest_mode", "evcs_assignment"):
        assert state(cfg, oid, inputs) == "None"


def test_wallbox_and_occupant_sensors_follow_health(cfg) -> None:
    inputs = sample_inputs(
        vehicles=[{"id": "tesla", "wallbox": "evcs", "assignment": "detected"}, {"id": "egolf"}],
        wallboxes=[
            {"id": "evcs", "occupant": "tesla", "name": "Tesla Model 3", "assignment": "detected"},
            {"id": "twc", "occupant": "guest", "name": "Gast", "assignment": "guest"},
        ],
    )
    assert state(cfg, "tesla_wallbox", inputs) == "EVCS"
    assert state(cfg, "egolf_wallbox", inputs) == "keine"
    assert state(cfg, "evcs_occupant", inputs) == "Tesla Model 3"
    assert state(cfg, "twc_occupant", inputs) == "Gast"
    empty = sample_inputs(wallboxes=[{"id": "evcs", "occupant": None, "name": None}])
    assert state(cfg, "evcs_occupant", empty) == "keins"
    assert state(cfg, "twc_occupant", empty) == "None"  # nicht im Health: unbekannt
    assert state(cfg, "tesla_wallbox", sample_inputs()) == "None"


def test_wallbox_sensor_shows_the_display_name(cfg) -> None:
    # wie im UI: der eigene Name aus den Einstellungen, sonst die Art der Ladestation
    vehicles = [{"id": "tesla", "wallbox": "evcs"}, {"id": "egolf", "wallbox": "twc"}]
    plain = sample_inputs(vehicles=vehicles)
    assert (state(cfg, "tesla_wallbox", plain), state(cfg, "egolf_wallbox", plain)) == (
        "EVCS",
        "Wall Connector",
    )
    named = sample_inputs(vehicles=vehicles, wallbox_names={"evcs": "Garage links"})
    assert (state(cfg, "tesla_wallbox", named), state(cfg, "egolf_wallbox", named)) == (
        "Garage links",
        "Wall Connector",
    )
    unknown = sample_inputs(vehicles=[{"id": "tesla", "wallbox": "weg"}])
    assert (
        state(cfg, "tesla_wallbox", unknown) == "weg"
    )  # nicht in der Konfiguration: der Schlüssel


def test_build_inputs_takes_the_wallbox_names(live, price_service, store, forecast_service) -> None:
    inputs = build_inputs(
        live, price_service, store, forecast_service, T_APP, wallbox_names={"evcs": "Garage"}
    )
    assert inputs.wallbox_names == {"evcs": "Garage"}


def test_assignment_unclear_problem(cfg) -> None:
    clear = sample_inputs(vehicles=[{"id": "tesla", "assignment": "detected"}])
    assert state(cfg, "problem_assignment_unclear", clear) == "OFF"
    unclear_vehicle = sample_inputs(vehicles=[{"id": "tesla", "assignment": "unclear"}])
    assert state(cfg, "problem_assignment_unclear", unclear_vehicle) == "ON"
    unclear_wallbox = sample_inputs(
        vehicles=[{"id": "tesla", "assignment": "detected"}],
        wallboxes=[{"id": "evcs", "occupant": None, "name": None, "assignment": "unclear"}],
    )
    assert state(cfg, "problem_assignment_unclear", unclear_wallbox) == "ON"
    assert state(cfg, "problem_assignment_unclear", sample_inputs()) == "None"


def test_build_inputs_takes_intent_vehicles_and_wallboxes(
    live, price_service, store, forecast_service
) -> None:
    live.health = {
        **live.health,
        "vehicles": [{"id": "tesla", "wallbox": "evcs"}, "kaputt"],
        "wallboxes": [{"id": "evcs", "occupant": None}],
    }
    intent = Intent(version=2, control=True)
    inputs = build_inputs(live, price_service, store, forecast_service, T_APP, intent)
    assert inputs.intent == intent
    assert inputs.vehicles == [{"id": "tesla", "wallbox": "evcs"}]
    assert inputs.wallboxes == [{"id": "evcs", "occupant": None}]
    live.connected = False
    gone = build_inputs(live, price_service, store, forecast_service, T_APP)
    assert (gone.intent, gone.vehicles, gone.wallboxes) == (None, [], [])


def test_config_and_entities_agree_on_object_ids(cfg) -> None:
    # Die Konfiguration prüft Kollisionen mit ihrer eigenen Liste der Objekt-IDs; sie muss stimmen
    from bindaems.shared.config import ha_object_ids

    assert sorted(ha_object_ids(cfg)) == sorted(e.object_id for e in build_entities(cfg))


def test_building_entities_fails_loudly_on_a_duplicate_object_id(cfg) -> None:
    broken = cfg.model_copy(update={"vehicles": {**cfg.vehicles, "Tesla": cfg.vehicles["tesla"]}})
    with pytest.raises(ValueError, match="tesla_soc"):
        build_entities(broken)
