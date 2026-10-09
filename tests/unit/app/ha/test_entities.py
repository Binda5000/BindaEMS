import json
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from tests.app_helpers import T_APP, price_row, sample_inputs

from bindaems.app.ha.entities import EntitySpec, build_entities, build_inputs, discovery
from bindaems.app.prices.service import PriceService
from bindaems.shared.config import Config


def entity(cfg: Config, object_id: str) -> EntitySpec:
    return next(e for e in build_entities(cfg) if e.object_id == object_id)


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
        "tesla_soc",
        "egolf_soc",
        "evcs_power",
        "twc_power",
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
