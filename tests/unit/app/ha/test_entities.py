import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from tests.app_helpers import T_APP, price_row, sample_inputs
from tests.plan_helpers import (
    EV,
    HOLD_REASON,
    SCENARIO_START,
    at,
    planned_car,
    sample_plan,
    scenario,
)

from bindaems.app.charging.sessions import ChargingSession, FlowSplit
from bindaems.app.ha.entities import (
    UNKNOWN,
    EntitySpec,
    HaInputs,
    build_entities,
    build_inputs,
    discovery,
    on_off,
)
from bindaems.app.learning.driving_profile import VehicleProfile, WeekdayProfile
from bindaems.app.ledger.service import MonthPeak
from bindaems.app.planning.builder import build_plan
from bindaems.app.prices.service import PriceService
from bindaems.app.stats.service import TodayStats
from bindaems.shared.config import Config
from bindaems.shared.control import ChargeMode, Intent, SupportIntent, VehicleIntent
from bindaems.shared.plan import BatteryIntent, PlanVehicle
from bindaems.shared.timeutil import LOCAL_TZ, SLOT


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
        "month_peak",
        "savings_today",
        "savings_month",
        "autarky_today",
        "battery_plan_now",
        "battery_plan_next",
        "planned_soc",
        "budget",
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
        "grid_charge",
        "battery_support",
        "support_soc",
        "replan",
        "problem_assignment_unclear",
        "problem_target_unreachable",
        "tesla_soc",
        "tesla_mode",
        "tesla_boost",
        "tesla_boost_stop",
        "tesla_wallbox",
        "tesla_charge_energy",
        "tesla_target_soc",
        "tesla_departure",
        "tesla_done_by",
        "tesla_target_reachable",
        "tesla_auto_target",
        "tesla_suggested_departure",
        "tesla_suggested_target",
        "tesla_next_departure",
        "egolf_soc",
        "egolf_mode",
        "egolf_boost",
        "egolf_boost_stop",
        "egolf_wallbox",
        "egolf_charge_energy",
        "egolf_target_soc",
        "egolf_departure",
        "egolf_done_by",
        "egolf_target_reachable",
        "egolf_auto_target",
        "egolf_suggested_departure",
        "egolf_suggested_target",
        "egolf_next_departure",
        "evcs_power",
        "evcs_assignment",
        "evcs_occupant",
        "twc_power",
        "twc_assignment",
        "twc_occupant",
        "thermal_klima_offset",
        "thermal_klima_mode",
        "thermal_klima_power",
        "thermal_klima_wish",
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


def test_build_inputs_derives_the_execution(live, price_service, store, forecast_service) -> None:
    created = datetime(2026, 10, 16, 10, 1, tzinfo=UTC)
    plan = sample_plan(created.isoformat(), ["10:00", "10:15"]).model_copy(update={"id": 7})
    now = created + timedelta(minutes=4)

    def execution(at: datetime = now, **changes: Any) -> str | None:
        return build_inputs(
            live, price_service, store, forecast_service, at, plan=plan, **changes
        ).execution

    assert execution(grid_charge_allowed=None) is None  # Schalter unbekannt
    assert execution(grid_charge_allowed=False) == "off"  # der core hat keine Absicht
    live.health = {
        **live.health,
        "mode": "CONTROL",
        "intent": {"control": True, "plan": {"id": 7, "grid_charge_allowed": False}},
        "actuators": [{"id": "victron", "effective": "live", "locked": []}],
        "plan": {"id": 7, "state": "valid"},
    }
    assert execution(grid_charge_allowed=False) == "live"
    assert (
        execution(created + timedelta(hours=3), grid_charge_allowed=False) == "none"
    )  # abgelaufen
    live.connected = False
    assert execution(grid_charge_allowed=False) == "unreachable"
    assert (
        build_inputs(live, price_service, store, forecast_service, now, grid_charge_allowed=False)
    ).execution == "none"


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


MODES = ["PV-Überschuss", "Min+PV", "Sofort", "Aus", "Geplant"]
GUEST_MODES = ["PV-Überschuss", "Min+PV", "Sofort", "Aus"]  # ein Gast kann nicht „Geplant“ laden


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
    assert (guest["name"], guest["options"]) == ("Gast-Lademodus", GUEST_MODES)


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


def test_assignment_state_shows_the_effective_override_like_the_ui(cfg) -> None:
    # wie ``overrideVehicle`` im UI: Die Absicht nennt die Wallbox, und Health bestätigt die
    # Übersteuerung, zeigt sie wartend oder ist nicht aktuell bzw. ohne Stand der Regelung
    intent = Intent(version=3, vehicles={"egolf": VehicleIntent(wallbox="twc")})

    def at_twc(intent_version: int | None, **egolf: Any) -> str:
        shown = {"id": "egolf", "mode": "pv", "assignment": "default", "wallbox": None} | egolf
        inputs = sample_inputs(intent=intent, intent_version=intent_version, vehicles=[shown])
        return state(cfg, "twc_assignment", inputs)

    assert at_twc(3) == "Automatisch"  # nach dem Abstecken beendet
    assert at_twc(3, assignment="override", wallbox="twc") == "e-Golf"
    assert at_twc(3, assignment="override", wallbox="evcs") == "Automatisch"
    assert at_twc(4, override_pending=True) == "e-Golf"
    assert at_twc(3, mode=None) == "e-Golf"  # Stand der Regelung unbekannt
    assert at_twc(2) == "e-Golf"  # Health kennt die Version der Übersteuerung noch nicht
    assert at_twc(None) == "e-Golf"


def test_guest_mode_state(cfg) -> None:
    intent = Intent.model_validate({"version": 1, "guest": {"mode": "off"}})
    assert state(cfg, "guest_mode", sample_inputs(intent=intent)) == "Aus"


def test_planned_mode_state(cfg) -> None:
    intent = Intent.model_validate({"version": 1, "vehicles": {"tesla": {"mode": "planned"}}})
    inputs = sample_inputs(intent=intent)
    assert state(cfg, "tesla_mode", inputs) == "Geplant"
    assert state(cfg, "egolf_mode", inputs) == "PV-Überschuss"


def test_guest_select_has_no_planned_option(cfg) -> None:
    assert "Geplant" not in config_of(cfg, "guest_mode")["options"]
    assert "Geplant" in config_of(cfg, "tesla_mode")["options"]
    assert "Geplant" in config_of(cfg, "egolf_mode")["options"]


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


def test_month_peak_sensor(cfg) -> None:
    inputs = sample_inputs(month_peak=MonthPeak(7.43, datetime(2026, 10, 5, 17, 15, tzinfo=UTC)))
    spec = entity(cfg, "month_peak")
    assert (
        spec.value(inputs) == "7.4"
        and spec.attributes(inputs)["slot"] == "2026-10-05T17:15:00+00:00"
    )
    assert (spec.name, spec.unit, spec.device_class, spec.state_class) == (
        "Monatsspitze",
        "kW",
        "power",
        "measurement",
    )


def test_month_peak_sensor_without_peak_is_unknown(cfg) -> None:
    inputs = sample_inputs()
    spec = entity(cfg, "month_peak")
    assert spec.value(inputs) == "None" and spec.attributes(inputs) == {"slot": None}


def _plan_inputs(**changes):
    plan = sample_plan(
        created="2026-10-16T10:01:00+00:00", slots=["10:00", "10:15"], intents=[HOLD_REASON, EV]
    )
    return sample_inputs(plan=plan, plan_now=plan.slots[0], plan_next=plan.slots[1], **changes)


def test_plan_now_sensor(cfg) -> None:
    inputs = _plan_inputs()
    spec = entity(cfg, "battery_plan_now")
    assert spec.value(inputs) == "Halten"
    attrs = spec.attributes(inputs)
    assert attrs["reason"] == "Akku hält 50 %, weil 18:00–20:00 Uhr 38,0 ct erwartet werden."
    assert (attrs["codes"], attrs["planned_soc_pct"], attrs["source"]) == (
        ["HALTEN_FUER_SPITZE"],
        50.0,
        "optimizer",
    )
    assert (attrs["grid_charge_w"], attrs["start"]) == (0.0, "2026-10-16T12:00:00+02:00")
    assert entity(cfg, "battery_plan_next").value(inputs) == "Eigenverbrauch"
    assert (
        entity(cfg, "battery_plan_next").attributes(inputs)["start"] == "2026-10-16T12:15:00+02:00"
    )


def test_plan_sensors_unknown_without_valid_plan(cfg) -> None:
    inputs = sample_inputs()
    names = ("battery_plan_now", "battery_plan_next", "planned_soc", "budget")
    assert {entity(cfg, oid).value(inputs) for oid in names} == {"None"}
    assert entity(cfg, "battery_plan_now").attributes(inputs) == {"execution": None}
    assert entity(cfg, "planned_soc").attributes(inputs) == {"execution": None, "hourly": []}


def test_planned_soc_and_hourly_attribute(cfg) -> None:
    plan = sample_plan(
        created="2026-10-16T10:01:00+00:00",
        slots=["10:00", "10:15", "10:30", "10:45", "11:00"],
        soc=(40.0, 41.26),
    )
    slots = [
        slot.model_copy(update={"soc_start_pct": 40.0 + i, "budget_kw": 7.14})
        for i, slot in enumerate(plan.slots)
    ]
    plan = plan.model_copy(update={"slots": slots})
    inputs = sample_inputs(plan=plan, plan_now=slots[1], plan_next=slots[2])
    spec = entity(cfg, "planned_soc")
    assert spec.value(inputs) == "41.3"
    assert spec.attributes(inputs)["hourly"] == [
        {"start": "2026-10-16T12:00:00+02:00", "soc_pct": 40.0},
        {"start": "2026-10-16T13:00:00+02:00", "soc_pct": 44.0},
    ]
    assert entity(cfg, "budget").value(inputs) == "7.1"
    assert config_of(cfg, "budget")["unit_of_measurement"] == "kW"
    assert config_of(cfg, "planned_soc")["unit_of_measurement"] == "%"
    assert config_of(cfg, "battery_plan_now")["name"] == "Akku-Plan jetzt"
    assert config_of(cfg, "battery_plan_next")["name"] == "Akku-Plan nächster Slot"
    assert config_of(cfg, "planned_soc")["name"] == "Geplanter SOC"


def test_build_inputs_takes_the_slots_of_a_valid_plan(
    live, price_service, store, forecast_service
) -> None:
    plan = sample_plan(created="2026-10-09T07:58:00+00:00", slots=["08:00", "08:15"])
    inputs = build_inputs(
        live, price_service, store, forecast_service, T_APP, plan=plan, grid_charge_allowed=True
    )
    assert inputs.plan is plan and inputs.grid_charge_allowed is True
    assert (inputs.plan_now, inputs.plan_next) == (plan.slots[0], plan.slots[1])
    last = build_inputs(
        live, price_service, store, forecast_service, T_APP + timedelta(minutes=15), plan=plan
    )
    assert (last.plan_now, last.plan_next) == (plan.slots[1], None)


def test_build_inputs_ignores_expired_plan(live, price_service, store, forecast_service) -> None:
    plan = sample_plan(created="2026-10-09T06:00:00+00:00", slots=["08:00", "08:15"])
    inputs = build_inputs(live, price_service, store, forecast_service, T_APP, plan=plan)
    assert (inputs.plan, inputs.plan_now, inputs.plan_next) == (None, None, None)
    assert (
        build_inputs(live, price_service, store, forecast_service, T_APP).grid_charge_allowed
        is None
    )


def test_grid_charge_switch_state_and_discovery(cfg) -> None:
    assert state(cfg, "grid_charge", sample_inputs(grid_charge_allowed=True)) == "ON"
    assert state(cfg, "grid_charge", sample_inputs(grid_charge_allowed=False)) == "OFF"
    assert state(cfg, "grid_charge", sample_inputs()) == "None"
    data = config_of(cfg, "grid_charge")
    assert data["command_topic"] == "bindaems/cmd/grid_charge"
    assert (data["name"], data["payload_on"], data["payload_off"]) == (
        "Netzladen erlaubt",
        "ON",
        "OFF",
    )
    assert entity(cfg, "grid_charge").component == "switch"


def test_replan_button_discovery(cfg) -> None:
    data = config_of(cfg, "replan")
    assert (data["name"], data["payload_press"]) == ("Neu planen", "PRESS")
    assert data["command_topic"] == "bindaems/cmd/replan" and "state_topic" not in data
    assert entity(cfg, "replan").device.identifier == "bindaems"


def test_plan_sensors_carry_the_execution(cfg) -> None:
    # Der Stand der Ausführung (Plan 3c) ersetzt `preview` an jedem Plan-Sensor, mit und ohne Plan.
    names = ("battery_plan_now", "battery_plan_next", "planned_soc", "budget")
    for state in ("none", "pending", "dry_run", "live"):
        for inputs in (_plan_inputs(execution=state), sample_inputs(execution=state)):
            for oid in names:
                attributes = entity(cfg, oid).attributes(inputs)
                assert attributes["execution"] == state and "preview" not in attributes


def test_support_switch_state_and_discovery(cfg) -> None:
    assert state(cfg, "battery_support", sample_inputs(support_allowed=True)) == "ON"
    assert state(cfg, "battery_support", sample_inputs(support_allowed=False)) == "OFF"
    assert state(cfg, "battery_support", sample_inputs()) == "None"
    data = config_of(cfg, "battery_support")
    assert data["command_topic"] == "bindaems/cmd/battery_support"
    assert data["state_topic"] == "bindaems/state/battery_support"
    assert (data["name"], data["payload_on"], data["payload_off"]) == (
        "Akku-Unterstützung erlaubt",
        "ON",
        "OFF",
    )
    assert entity(cfg, "battery_support").component == "switch"
    assert entity(cfg, "battery_support").device.identifier == "bindaems"


def test_number_entity_discovery(cfg) -> None:
    config = config_of(cfg, "support_soc")
    assert (
        config["min"],
        config["max"],
        config["step"],
        config["unit_of_measurement"],
        config["mode"],
    ) == (20.0, 100.0, 1.0, "%", "box")
    assert config["name"] == "Unterstützungsschwelle"
    assert config["command_topic"] == "bindaems/cmd/support_soc"
    assert config["state_topic"] == "bindaems/state/support_soc"
    topic, _ = discovery(entity(cfg, "support_soc"), cfg.homeassistant.mqtt)
    assert topic == "homeassistant/number/bindaems/support_soc/config"
    assert entity(cfg, "support_soc").component == "number"


def test_number_range_follows_the_ups_reserve(cfg) -> None:
    battery = cfg.battery.model_copy(update={"reserve_soc_pct": 35.0})
    shifted = cfg.model_copy(update={"battery": battery})
    assert (config_of(shifted, "support_soc")["min"]) == 35.0


def test_only_the_number_carries_a_range(cfg) -> None:
    for spec in build_entities(cfg):
        data = config_of(cfg, spec.object_id)
        has_range = {"min", "max", "step", "mode"} & set(data)
        assert bool(has_range) == (spec.component == "number"), spec.object_id


def test_support_threshold_state(cfg) -> None:
    assert state(cfg, "support_soc", sample_inputs(support_soc_pct=65.0)) == "65.0"
    assert state(cfg, "support_soc", sample_inputs(support_soc_pct=70.0)) == "70.0"
    assert state(cfg, "support_soc", sample_inputs()) == "None"


def test_build_inputs_takes_the_support_from_the_intent_or_the_config(
    live, price_service, store, forecast_service
) -> None:
    def inputs(intent: Intent | None) -> HaInputs:
        return build_inputs(
            live, price_service, store, forecast_service, T_APP, intent, support_default_pct=70.0
        )

    own = inputs(Intent(version=1, support=SupportIntent(allowed=False, soc_pct=55.0)))
    assert (own.support_allowed, own.support_soc_pct) == (False, 55.0)
    default = inputs(Intent(version=1))
    assert (default.support_allowed, default.support_soc_pct) == (True, 70.0)
    unknown = inputs(None)
    assert (unknown.support_allowed, unknown.support_soc_pct) == (None, None)


def test_savings_sensors_are_monetary_with_two_decimals(cfg) -> None:
    inputs = sample_inputs()
    today, month = entity(cfg, "savings_today"), entity(cfg, "savings_month")
    assert (today.value(inputs), month.value(inputs)) == ("1.23", "20.46")
    assert (today.name, month.name) == ("Ersparnis heute", "Ersparnis Monat")
    for spec in (today, month):
        assert (spec.component, spec.unit, spec.device_class, spec.state_class) == (
            "sensor",
            "EUR",
            "monetary",
            None,  # ``monetary`` erlaubt in HA kein ``measurement``
        )
        assert spec.device.identifier == "bindaems"
    assert today.attributes(inputs) == {"same_import_eur": 0.9}
    assert month.attributes(inputs) == {"same_import_eur": 15.0}
    payload = config_of(cfg, "savings_today")
    assert payload["device_class"] == "monetary" and "state_class" not in payload
    assert payload["json_attributes_topic"] == "bindaems/state/savings_today/attributes"


def test_autarky_today_is_a_whole_percent(cfg) -> None:
    inputs = sample_inputs()
    spec = entity(cfg, "autarky_today")
    assert spec.value(inputs) == "63"
    assert spec.attributes(inputs) == {"month_pct": 58}
    assert (spec.name, spec.unit, spec.state_class) == ("Autarkie heute", "%", "measurement")
    assert spec.device_class is None
    full = sample_inputs(stats=TodayStats(0, 0, 0, 0, 1.0, 0.0))
    assert spec.value(full) == "100" and spec.attributes(full) == {"month_pct": 0}


def test_charge_energy_shows_open_or_last_session(cfg) -> None:
    spec = entity(cfg, "tesla_charge_energy")
    assert (spec.name, spec.unit, spec.device_class, spec.state_class) == (
        "Ladeenergie",
        "kWh",
        "energy",
        None,  # HA warnt bei ``energy`` mit ``measurement``
    )
    assert "state_class" not in config_of(cfg, "tesla_charge_energy")
    assert spec.device.identifier == "bindaems_tesla"
    inputs = sample_inputs()  # offene Session
    assert spec.value(inputs) == "12.3"
    assert spec.attributes(inputs) == {
        "start": "2026-10-09T08:30:00+02:00",  # Ortszeit wie bei den Preisen
        "end": None,
        "pv_kwh": 5.0,
        "battery_kwh": 2.34,
        "grid_kwh": 5.0,
        "cost_eur": 1.5,
        "month_kwh": 42.0,
    }
    finished = ChargingSession(
        6,
        "evcs",
        "tesla",
        datetime(2026, 10, 8, 16, 0, tzinfo=UTC),
        datetime(2026, 10, 8, 18, 30, tzinfo=UTC),
        FlowSplit(kwh=20.0, pv_kwh=0.0, battery_kwh=0.0, grid_kwh=20.0, cost_eur=None),
    )
    shown = sample_inputs(sessions={"tesla": finished})
    assert spec.value(shown) == "20.0"
    attributes = spec.attributes(shown)
    assert attributes["end"] == "2026-10-08T20:30:00+02:00"
    assert attributes["cost_eur"] is None  # unbekannte Kosten bleiben unbekannt


def test_charge_energy_is_per_vehicle(cfg) -> None:
    inputs = sample_inputs()
    egolf = entity(cfg, "egolf_charge_energy")
    assert egolf.device.identifier == "bindaems_egolf"
    assert egolf.value(inputs) == "None"  # keine Session
    assert egolf.attributes(inputs)["month_kwh"] == 0.0
    assert egolf.attributes(inputs)["start"] is None


def test_statistics_sensors_unknown_without_data(cfg) -> None:
    inputs = sample_inputs(stats=None, sessions={}, month_kwh={})
    for oid in ("savings_today", "savings_month", "autarky_today"):
        assert state(cfg, oid, inputs) == "None", oid
    assert entity(cfg, "savings_today").attributes(inputs) == {"same_import_eur": None}
    assert entity(cfg, "autarky_today").attributes(inputs) == {"month_pct": None}
    for oid in ("tesla_charge_energy", "egolf_charge_energy"):
        assert state(cfg, oid, inputs) == "None", oid
        assert entity(cfg, oid).attributes(inputs) == {
            "start": None,
            "end": None,
            "pv_kwh": None,
            "battery_kwh": None,
            "grid_kwh": None,
            "cost_eur": None,
            "month_kwh": None,
        }
    # einzelne unbekannte Werte
    partial = sample_inputs(stats=TodayStats(None, None, 2.0, None, None, 0.5))
    assert state(cfg, "savings_today", partial) == "None"
    assert state(cfg, "savings_month", partial) == "2.00"
    assert entity(cfg, "savings_month").attributes(partial) == {"same_import_eur": None}
    assert state(cfg, "autarky_today", partial) == "None"
    assert entity(cfg, "autarky_today").attributes(partial) == {"month_pct": 50}


def test_inputs_without_statistics_default_to_unknown(
    live, price_service, store, forecast_service
) -> None:
    inputs = build_inputs(live, price_service, store, forecast_service, T_APP)
    assert (inputs.stats, dict(inputs.sessions), dict(inputs.month_kwh)) == (None, {}, {})
    stats = TodayStats(1, 1, 1, 1, 0.5, 0.5)
    given = build_inputs(
        live,
        price_service,
        store,
        forecast_service,
        T_APP,
        stats=stats,
        sessions={"tesla": None},
        month_kwh={"tesla": 3.0},
    )
    assert given.stats == stats and given.sessions == {"tesla": None}
    assert given.month_kwh == {"tesla": 3.0}


# Geplantes Laden (Plan 4b, Entscheidungen 12 und 13). Der Tesla lädt an der EVCS mit voller
# Leistung 16 A · 230 V · 3 = 11,04 kW; 11,04 kWh Bedarf dauern also genau eine Stunde.
PLANNED = Intent(version=3, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PLANNED)})
DEPARTURE = T_APP + timedelta(hours=10)


def _health_car(state: str = "surplus", **planned: Any) -> dict[str, Any]:
    return {
        "id": "tesla",
        "mode": "planned",
        "plugged": True,
        "wallbox": "evcs",
        "soc_pct": 40.0,
        "soc_source": "measured",
        "planned": {
            "state": state,
            "target_soc_pct": 80.0,
            "departure_at": DEPARTURE.isoformat(),
            "need_kwh": 11.04,
            "latest_start": (T_APP + timedelta(hours=2)).isoformat(),
            "plan_w": None,
        }
        | planned,
    }


def _plan_car(**changes: Any) -> PlanVehicle:
    fields: dict[str, Any] = {
        "key": "tesla",
        "name": "Tesla Model 3",
        "wallbox": "evcs",
        "target_soc_pct": 80.0,
        "departure_at": DEPARTURE,
        "soc_start_pct": 40.0,
        "soc_assumed": False,
        "limit_pct": None,
        "need_kwh": 33.3,
        "planned_kwh": 33.3,
        "shortfall_kwh": 0.0,
        "expected_soc_pct": 80.0,
        "finish_at": T_APP + timedelta(hours=6, minutes=15),
        "reachable": True,
    }
    return PlanVehicle(**(fields | changes))


def _planned(**changes: Any) -> HaInputs:
    values: dict[str, Any] = {"intent": PLANNED, "intent_version": 3, "now": T_APP}
    return sample_inputs(**(values | changes))


def local(moment: datetime) -> str:
    return moment.astimezone(LOCAL_TZ).isoformat()


def test_target_soc_number_and_departure_text(cfg) -> None:
    number, text = config_of(cfg, "tesla_target_soc"), config_of(cfg, "tesla_departure")
    assert (number["name"], number["min"], number["max"], number["step"]) == (
        "Ziel-SOC",
        20.0,
        100.0,
        1.0,
    )
    assert (number["unit_of_measurement"], number["command_topic"]) == (
        "%",
        "bindaems/cmd/tesla_target_soc",
    )
    assert (text["name"], text["pattern"]) == ("Abfahrtszeit", r"^([01]\d|2[0-3]):[0-5]\d$")
    assert (text["command_topic"], text["state_topic"]) == (
        "bindaems/cmd/tesla_departure",
        "bindaems/state/tesla_departure",
    )
    topic, _ = discovery(entity(cfg, "tesla_departure"), cfg.homeassistant.mqtt)
    assert topic == "homeassistant/text/bindaems/tesla_departure/config"
    intent = Intent(
        version=2, vehicles={"tesla": VehicleIntent(target_soc_pct=90, departure="06:30")}
    )
    inputs = sample_inputs(intent=intent)
    assert (state(cfg, "tesla_target_soc", inputs), state(cfg, "tesla_departure", inputs)) == (
        "90",
        "06:30",
    )
    # ohne Eintrag gelten die Standardwerte, ohne Absicht ist beides unbekannt
    assert (state(cfg, "egolf_target_soc", inputs), state(cfg, "egolf_departure", inputs)) == (
        "80",
        "07:00",
    )
    unknown = sample_inputs(intent=None)
    assert {state(cfg, f"tesla_{s}", unknown) for s in ("target_soc", "departure")} == {"None"}


def test_done_by_and_target_reachable_discovery(cfg) -> None:
    done_by, reachable = config_of(cfg, "tesla_done_by"), config_of(cfg, "tesla_target_reachable")
    assert (done_by["name"], done_by["device_class"]) == ("Fertig um", "timestamp")
    assert "command_topic" not in done_by
    assert (reachable["name"], reachable["device_class"], reachable["options"]) == (
        "Ziel erreichbar",
        "enum",
        ["ja", "nein"],
    )
    assert reachable["json_attributes_topic"] == "bindaems/state/tesla_target_reachable/attributes"
    problem = config_of(cfg, "problem_target_unreachable")
    assert (problem["name"], problem["device_class"]) == ("Ziel-SOC unerreichbar", "problem")
    topic, _ = discovery(entity(cfg, "problem_target_unreachable"), cfg.homeassistant.mqtt)
    assert topic == "homeassistant/binary_sensor/bindaems/problem_target_unreachable/config"


def test_done_by_from_plan_and_from_latest_start_without_plan(cfg) -> None:
    # mit Plan: das Ende des letzten Slots, in dem der Plan das Auto lädt
    planned = _planned(plan_vehicles=[_plan_car()], vehicles=[_health_car()])
    assert state(cfg, "tesla_done_by", planned) == local(T_APP + timedelta(hours=6, minutes=15))
    assert state(cfg, "tesla_target_reachable", planned) == "ja"
    # ohne Plan: ab der spätesten Startzeit mit voller Leistung, bis der Bedarf geladen ist
    surplus = _planned(vehicles=[_health_car()])
    assert state(cfg, "tesla_done_by", surplus) == local(T_APP + timedelta(hours=3))
    assert state(cfg, "tesla_target_reachable", surplus) == "ja"
    # ist die späteste Startzeit erreicht, lädt das Auto ab jetzt mit voller Leistung
    started = (T_APP - timedelta(minutes=30)).isoformat()
    due = _planned(vehicles=[_health_car("due", latest_start=started)])
    assert state(cfg, "tesla_done_by", due) == local(T_APP + timedelta(hours=1))
    # ein Auto, das der Plan nicht nennt (etwa nach dem Plan angesteckt), rechnet wie ohne Plan
    other = _planned(plan_vehicles=[_plan_car(key="egolf")], vehicles=[_health_car()])
    assert state(cfg, "tesla_done_by", other) == local(T_APP + timedelta(hours=3))


def test_done_by_unknown_when_the_plan_ends_before_a_departure_beyond_the_horizon(cfg) -> None:
    # Vor den Preisen für morgen endet der Horizont vor der Abfahrt: Der Plan lädt im Horizont nur
    # einen Slot, den Rest lädt der core danach. „Fertig um“ ist dann unbekannt (Ruling R20), das
    # Ziel bleibt erreichbar
    car = planned_car(departure_at=at(SCENARIO_START) + 20 * SLOT, horizon_need_kwh=0.0)
    inputs = scenario(prices=[20.0] * 8, pv=[0.0] * 8, load=[500.0] * 8, vehicles=[car])
    ev_w: list[dict[str, float]] = [{}, {}, {"tesla": 3450.0}, {}, {}, {}, {}, {}]
    plan = build_plan(
        inputs,
        [BatteryIntent()] * 8,
        source="optimizer",
        solver_status="optimal",
        ev_w=ev_w,
        shortfall_kwh={},
    )
    planned = _planned(plan_vehicles=plan.vehicles, vehicles=[_health_car()])
    assert state(cfg, "tesla_done_by", planned) == "None"
    assert state(cfg, "tesla_target_reachable", planned) == "ja"
    assert entity(cfg, "tesla_target_reachable").attributes(planned)["expected_soc_pct"] == 80.0


def test_done_by_without_plan_is_stable_within_a_minute(cfg) -> None:
    # Ist die späteste Startzeit vorbei, rechnet „Fertig um“ ohne Plan ab jetzt. Auf die volle
    # Minute aufgerundet ändert es sich nicht mit jedem Publish innerhalb derselben Minute.
    started = (T_APP - timedelta(minutes=30)).isoformat()
    for car in (_health_car("due", latest_start=started), _health_car(latest_start=started)):
        early = _planned(vehicles=[car], now=T_APP + timedelta(seconds=10.5))
        late = _planned(vehicles=[car], now=T_APP + timedelta(seconds=40.2))
        expected = local(T_APP + timedelta(hours=1, minutes=1))
        assert state(cfg, "tesla_done_by", early) == expected
        assert state(cfg, "tesla_done_by", late) == expected


def test_target_reachable_from_plan_with_attributes(cfg) -> None:
    car = _plan_car(
        reachable=False,
        shortfall_kwh=4.0,
        expected_soc_pct=71.234,
        soc_assumed=True,
        soc_start_pct=0.0,
    )
    inputs = _planned(
        plan_vehicles=[car], vehicles=[_health_car() | {"soc_source": "assumed", "soc_pct": 0.0}]
    )
    spec = entity(cfg, "tesla_target_reachable")
    assert spec.value(inputs) == "nein"
    assert spec.attributes(inputs) == {
        "expected_soc_pct": 71.2,
        "departure_at": local(DEPARTURE),
        "soc_source": "assumed",
    }
    assert state(cfg, "problem_target_unreachable", inputs) == "ON"
    assert entity(cfg, "problem_target_unreachable").attributes(inputs) == {
        "vehicles": ["Tesla Model 3"]
    }


def test_target_reachable_without_plan_from_the_latest_start(cfg) -> None:
    # 30 min vor der Abfahrt, die späteste Startzeit vorbei: In 0,5 h gibt die Wallbox 5,52 kWh ab,
    # davon kommen 4,968 kWh ins Auto (η 90 %), das sind 6,624 % von 75 kWh
    soon = (T_APP + timedelta(minutes=30)).isoformat()
    late = (T_APP - timedelta(hours=1)).isoformat()
    inputs = _planned(vehicles=[_health_car("due", departure_at=soon, latest_start=late)])
    spec = entity(cfg, "tesla_target_reachable")
    assert spec.value(inputs) == "nein"
    assert spec.attributes(inputs) == {
        "expected_soc_pct": 46.6,
        "departure_at": local(T_APP + timedelta(minutes=30)),
        "soc_source": "measured",
    }
    assert state(cfg, "tesla_done_by", inputs) == local(T_APP + timedelta(hours=1))
    assert state(cfg, "problem_target_unreachable", inputs) == "ON"
    reachable = _planned(vehicles=[_health_car()])
    assert entity(cfg, "tesla_target_reachable").attributes(reachable)["expected_soc_pct"] == 80.0


def test_target_reached_has_no_done_by_and_is_reachable(cfg) -> None:
    inputs = _planned(
        vehicles=[_health_car("reached", need_kwh=0.0, latest_start=None) | {"soc_pct": 81.0}]
    )
    assert state(cfg, "tesla_done_by", inputs) == "None"
    assert state(cfg, "tesla_target_reachable", inputs) == "ja"
    assert entity(cfg, "tesla_target_reachable").attributes(inputs)["expected_soc_pct"] == 81.0
    assert state(cfg, "problem_target_unreachable", inputs) == "OFF"
    # das Ziel ist erreicht, auch wenn der Plan das Auto noch laden lässt
    late = _plan_car(reachable=False, finish_at=T_APP + timedelta(hours=9))
    planned = _planned(plan_vehicles=[late], vehicles=inputs.vehicles)
    assert state(cfg, "tesla_done_by", planned) == "None"
    assert state(cfg, "tesla_target_reachable", planned) == "ja"


def test_planned_sensors_are_unknown_outside_of_planned(cfg) -> None:
    names = ("tesla_done_by", "tesla_target_reachable")
    pv = Intent(version=3, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PV)})
    cases = [
        _planned(intent=pv, plan_vehicles=[_plan_car()], vehicles=[_health_car()]),
        _planned(vehicles=[_health_car() | {"plugged": False}]),
        _planned(vehicles=[_health_car() | {"planned": None}]),
        _planned(vehicles=[]),
    ]
    for inputs in cases:
        assert {state(cfg, oid, inputs) for oid in names} == {"None"}
        assert entity(cfg, "tesla_target_reachable").attributes(inputs) == {
            "expected_soc_pct": None,
            "departure_at": None,
            "soc_source": None,
        }
        assert state(cfg, "problem_target_unreachable", inputs) == "OFF"
    assert state(cfg, "problem_target_unreachable", sample_inputs(core_connected=False)) == "None"


def test_build_inputs_takes_the_plan_vehicles_and_the_time(
    live, price_service, store, forecast_service
) -> None:
    car = _plan_car()
    plan = sample_plan(created="2026-10-09T07:58:00+00:00", slots=["08:00", "08:15"])
    plan = plan.model_copy(update={"vehicles": [car]})
    inputs = build_inputs(live, price_service, store, forecast_service, T_APP, plan=plan)
    assert (list(inputs.plan_vehicles), inputs.now) == ([car], T_APP)
    later = T_APP + timedelta(hours=2)  # der Plan ist abgelaufen
    expired = build_inputs(live, price_service, store, forecast_service, later, plan=plan)
    assert list(expired.plan_vehicles) == []


def test_expected_car_has_no_unreachable_warning(cfg) -> None:
    # Plan 5b, Präzisierung 10 (Ruling T10-5): Ein erwartetes Auto im Plan hat kein „Fertig um“,
    # kein „Ziel erreichbar“ und meldet kein „Ziel-SOC unerreichbar“
    expected = _plan_car(
        reachable=False,
        shortfall_kwh=10.0,
        expected_soc_pct=60.0,
        expected=True,
        arrival_at=T_APP + timedelta(hours=4),
    )
    inputs = _planned(plan_vehicles=[expected], vehicles=[])
    assert state(cfg, "tesla_done_by", inputs) == "None"
    assert state(cfg, "tesla_target_reachable", inputs) == "None"
    assert state(cfg, "problem_target_unreachable", inputs) == "OFF"
    assert entity(cfg, "problem_target_unreachable").attributes(inputs) == {"vehicles": []}
    # ohne core steht nur ein erwartetes Auto im Plan: unbekannt wie ganz ohne Plan
    offline = _planned(plan_vehicles=[expected], vehicles=[], core_connected=False)
    assert state(cfg, "problem_target_unreachable", offline) == "None"
    # steckt es inzwischen an, gilt bis zum neuen Plan die späteste Startzeit aus Health
    plugged = _planned(plan_vehicles=[expected], vehicles=[_health_car()])
    assert state(cfg, "tesla_done_by", plugged) == local(T_APP + timedelta(hours=3))
    assert state(cfg, "tesla_target_reachable", plugged) == "ja"


def test_build_inputs_leaves_out_expected_cars(
    live, price_service, store, forecast_service
) -> None:
    plugged = _plan_car(key="egolf")
    expected = _plan_car(expected=True, arrival_at=T_APP + timedelta(hours=4))
    plan = sample_plan(created="2026-10-09T07:58:00+00:00", slots=["08:00", "08:15"])
    plan = plan.model_copy(update={"vehicles": [plugged, expected]})
    inputs = build_inputs(live, price_service, store, forecast_service, T_APP, plan=plan)
    assert list(inputs.plan_vehicles) == [plugged]


# Klimaanlage (Plan 6a, Entscheidung 12): vier Sensoren je konfiguriertem Gerät
THERMAL_HEALTH: dict[str, Any] = {
    "klima": {
        "label": "Klimaanlage",
        "connected": True,
        "mode": "kuehlen",
        "running": True,
        "offset_sent_k": -1.0,
        "offset_active_k": -1.5,
        "wish": 3,
        "power_w": 1234.4,
    }
}


def test_thermal_entities_exist_only_for_configured_devices(cfg) -> None:
    ids = {e.object_id for e in build_entities(cfg)}
    assert {f"thermal_klima_{s}" for s in ("offset", "mode", "power", "wish")} <= ids
    bare = cfg.model_copy(update={"thermal": cfg.thermal.model_copy(update={"devices": {}})})
    assert not any(e.object_id.startswith("thermal_") for e in build_entities(bare))


def test_thermal_sensors_follow_health(cfg) -> None:
    inputs = sample_inputs(thermal=THERMAL_HEALTH)
    # der Versatz ist das Echo der UVR, nicht der gesendete Wert
    assert state(cfg, "thermal_klima_offset", inputs) == "-1.5"
    assert state(cfg, "thermal_klima_mode", inputs) == "kuehlen"
    assert state(cfg, "thermal_klima_power", inputs) == "1234"
    assert state(cfg, "thermal_klima_wish", inputs) == "3"


def test_thermal_offset_falls_back_to_the_sent_value_without_echo(cfg) -> None:
    item = {**THERMAL_HEALTH["klima"], "offset_active_k": None}
    assert state(cfg, "thermal_klima_offset", sample_inputs(thermal={"klima": item})) == "-1.0"


def test_thermal_sensors_are_unknown_without_values(cfg) -> None:
    for inputs in (
        sample_inputs(),  # kein Eintrag im Health (core getrennt oder Gerät unbekannt)
        sample_inputs(thermal={"klima": {"label": "Klimaanlage", "connected": False}}),
        sample_inputs(
            thermal={
                "klima": {
                    "mode": None,
                    "offset_sent_k": None,
                    "offset_active_k": None,
                    "wish": None,
                    "power_w": None,
                }
            }
        ),
    ):
        for suffix in ("offset", "mode", "power", "wish"):
            assert state(cfg, f"thermal_klima_{suffix}", inputs) == "None"


def test_thermal_mode_only_publishes_known_options(cfg) -> None:
    for mode in ("heizen", "kuehlen", "lueften", "aus", "unbekannt"):
        item = {**THERMAL_HEALTH["klima"], "mode": mode}
        assert state(cfg, "thermal_klima_mode", sample_inputs(thermal={"klima": item})) == mode
    odd = {**THERMAL_HEALTH["klima"], "mode": "kaffee"}
    assert state(cfg, "thermal_klima_mode", sample_inputs(thermal={"klima": odd})) == "None"


def test_thermal_discovery_payloads(cfg) -> None:
    offset = config_of(cfg, "thermal_klima_offset")
    assert offset["name"] == "Klima Versatz"
    assert (offset["unit_of_measurement"], offset["state_class"]) == ("K", "measurement")
    assert offset["unique_id"] == "bindaems_thermal_klima_offset"
    assert offset["state_topic"] == "bindaems/state/thermal_klima_offset"
    assert "command_topic" not in offset
    mode = config_of(cfg, "thermal_klima_mode")
    assert (mode["name"], mode["device_class"]) == ("Klima Betriebsart", "enum")
    assert mode["options"] == ["heizen", "kuehlen", "lueften", "aus", "unbekannt"]
    power = config_of(cfg, "thermal_klima_power")
    assert power["name"] == "Klima Leistung"
    assert (power["unit_of_measurement"], power["device_class"]) == ("W", "power")
    assert power["state_class"] == "measurement"
    wish = config_of(cfg, "thermal_klima_wish")
    assert wish["name"] == "Klima Wunsch" and "device_class" not in wish


def test_thermal_names_follow_a_custom_label(cfg) -> None:
    device = cfg.thermal.devices["klima"].model_copy(update={"label": "Büro"})
    custom = cfg.model_copy(
        update={"thermal": cfg.thermal.model_copy(update={"devices": {"klima": device}})}
    )
    assert config_of(custom, "thermal_klima_offset")["name"] == "Büro Versatz"
    assert config_of(custom, "thermal_klima_wish")["name"] == "Büro Wunsch"


def test_build_inputs_takes_the_thermal_health(
    live, price_service, store, forecast_service
) -> None:
    live.health = {**live.health, "thermal": {**THERMAL_HEALTH, "bogus": "x"}}
    inputs = build_inputs(live, price_service, store, forecast_service, T_APP)
    assert inputs.thermal == THERMAL_HEALTH  # nur Einträge, die Verzeichnisse sind
    live.health = {k: v for k, v in live.health.items() if k != "thermal"}
    assert build_inputs(live, price_service, store, forecast_service, T_APP).thermal == {}
    live.connected = False
    assert build_inputs(live, price_service, store, forecast_service, T_APP).thermal == {}


# --- Fahrverhalten und Automatik (Plan 5b, Präzisierung 13) ------------------------------------

# Sonntag, 25.10.2026 12:00 Uhr Ortszeit (MEZ, die Sommerzeit endete in der Nacht): Montag, der
# 26.10., ist ein Feiertag und gilt als „So/Feiertag“ (6).
SUNDAY = datetime(2026, 10, 25, 11, 0, tzinfo=UTC)


def _weekday(weekday: int, **changes: Any) -> WeekdayProfile:
    values: dict[str, Any] = {
        "weekday": weekday,
        "basis": "weekday",
        "days": 8,
        "trips": 8,
        "trip_share": 1.0,
        "departure_min": None,
        "departure_p50_min": None,
        "departure_p80_min": None,
        "departure_confidence": None,
        "arrival_p50_min": None,
        "arrival_soc_p50": None,
        "consumption_p50_pp": None,
        "consumption_p80_pp": None,
        "target_soc_pct": None,
        "target_clamped": False,
        "target_confidence": None,
    }
    return WeekdayProfile(**(values | changes))


def _profile(**by_weekday: dict[str, Any]) -> VehicleProfile:
    """Ein Profil, in dem nur die genannten Wochentage (``d0`` bis ``d6``) einen Vorschlag haben."""
    weekdays = [_weekday(i, **by_weekday.get(f"d{i}", {})) for i in range(7)]
    return VehicleProfile(
        key="tesla",
        source="tessie",
        data_from=SUNDAY - timedelta(days=56),
        data_to=SUNDAY,
        days=56,
        weekdays=weekdays,
        presence=[[0.0] * 96 for _ in range(7)],
    )


HOLIDAY_TRIP = {
    "d6": {
        "departure_min": 7 * 60 + 30,
        "departure_confidence": "high",
        "target_soc_pct": 75,
        "target_confidence": "medium",
    }
}


def test_vehicle_has_driving_entities(cfg) -> None:
    switch = config_of(cfg, "tesla_auto_target")
    assert (switch["name"], switch["payload_on"], switch["payload_off"]) == (
        "Automatik Ziel und Abfahrt",
        "ON",
        "OFF",
    )
    assert switch["command_topic"] == "bindaems/cmd/tesla_auto_target"
    assert switch["state_topic"] == "bindaems/state/tesla_auto_target"
    assert entity(cfg, "tesla_auto_target").component == "switch"
    departure = config_of(cfg, "tesla_suggested_departure")
    assert (departure["name"], departure["device_class"]) == ("Vorschlag Abfahrt", "timestamp")
    assert (
        departure["json_attributes_topic"] == "bindaems/state/tesla_suggested_departure/attributes"
    )
    target = config_of(cfg, "tesla_suggested_target")
    assert (target["name"], target["unit_of_measurement"]) == ("Vorschlag Ziel-SOC", "%")
    assert target["json_attributes_topic"] == "bindaems/state/tesla_suggested_target/attributes"
    nxt = config_of(cfg, "tesla_next_departure")
    assert (nxt["name"], nxt["device_class"]) == ("Nächste Abfahrt", "timestamp")
    for oid in ("suggested_departure", "suggested_target", "next_departure"):
        spec = entity(cfg, f"tesla_{oid}")
        assert spec.component == "sensor" and not spec.command, oid
    assert "json_attributes_topic" not in nxt
    # die Entitäten gehören zum Gerät des Fahrzeugs
    assert entity(cfg, "egolf_auto_target").device.name == "BindaEMS e-Golf"


def test_suggested_departure_is_timestamp_with_confidence(cfg) -> None:
    inputs = sample_inputs(driving_profiles={"tesla": _profile(**HOLIDAY_TRIP)}, now=SUNDAY)
    spec = entity(cfg, "tesla_suggested_departure")
    # Montag, 26.10. (Feiertag), 07:30 Uhr Ortszeit (MEZ, UTC+1) als UTC-Zeitpunkt
    assert spec.value(inputs) == "2026-10-26T06:30:00+00:00"
    assert spec.attributes(inputs) == {"confidence": "hoch", "basis": "Wochentag"}
    target = entity(cfg, "tesla_suggested_target")
    assert target.value(inputs) == "75"
    assert target.attributes(inputs) == {"confidence": "mittel", "basis": "Wochentag"}


@pytest.mark.parametrize(
    ("weekday", "now", "basis"),
    [
        # ``now`` ist jeweils der Tag vor der Fahrt, mittags
        (0, datetime(2026, 10, 18, 11, 0, tzinfo=UTC), "Gruppe Mo–Fr"),  # Montag 19.10.
        (4, datetime(2026, 10, 15, 11, 0, tzinfo=UTC), "Gruppe Mo–Fr"),  # Freitag 16.10.
        (5, datetime(2026, 10, 16, 11, 0, tzinfo=UTC), "Gruppe Sa"),  # Samstag 17.10.
        (6, datetime(2026, 10, 17, 11, 0, tzinfo=UTC), "Gruppe So/Feiertag"),  # Sonntag 18.10.
    ],
)
def test_basis_names_the_group_of_the_trip_day(cfg, weekday, now, basis) -> None:
    day = {
        "basis": "group",
        "departure_min": 8 * 60,
        "departure_confidence": "low",
        "target_soc_pct": 60,
        "target_confidence": "low",
    }
    inputs = sample_inputs(driving_profiles={"tesla": _profile(**{f"d{weekday}": day})}, now=now)
    for oid in ("tesla_suggested_departure", "tesla_suggested_target"):
        assert entity(cfg, oid).attributes(inputs) == {"confidence": "niedrig", "basis": basis}


def test_a_holiday_uses_the_sunday_profile(cfg) -> None:
    # der Montag, 26.10., ist ein Feiertag: Es zählt der Eintrag 6, nicht der des Montags
    monday = {"departure_min": 6 * 60, "departure_confidence": "low"}
    profile = _profile(d0=monday, **HOLIDAY_TRIP)
    inputs = sample_inputs(driving_profiles={"tesla": profile}, now=SUNDAY)
    assert state(cfg, "tesla_suggested_departure", inputs) == "2026-10-26T06:30:00+00:00"


def test_a_holiday_with_group_basis_names_the_sunday_group(cfg) -> None:
    # der Montag, 26.10., ist ein Feiertag: Eintrag 6 aus der Gruppe „So/Feiertag“, nicht der
    # Montag aus der Gruppe „Mo–Fr“
    monday = {"basis": "group", "departure_min": 6 * 60, "departure_confidence": "low"}
    holiday = {"d6": HOLIDAY_TRIP["d6"] | {"basis": "group"}}
    inputs = sample_inputs(driving_profiles={"tesla": _profile(d0=monday, **holiday)}, now=SUNDAY)
    for oid in ("tesla_suggested_departure", "tesla_suggested_target"):
        assert entity(cfg, oid).attributes(inputs)["basis"] == "Gruppe So/Feiertag", oid


def test_no_suggestion_is_unknown_not_none(cfg) -> None:
    # ohne Profil, ohne Zeitpunkt und ohne Fahrt in den nächsten Tagen: HA „unbekannt“ (die
    # Nutzlast, die HA als unbekannt liest, nie ein Ersatzwert) und leere Attribute
    nothing = _profile()
    for inputs in (
        sample_inputs(now=SUNDAY),
        sample_inputs(driving_profiles={"tesla": nothing}, now=SUNDAY),
        sample_inputs(driving_profiles={"tesla": _profile(**HOLIDAY_TRIP)}, now=None),
    ):
        for oid in ("tesla_suggested_departure", "tesla_suggested_target"):
            spec = entity(cfg, oid)
            assert spec.value(inputs) == UNKNOWN, oid
            assert spec.attributes(inputs) == {"confidence": None, "basis": None}, oid


def test_suggested_target_unknown_when_only_the_departure_is_known(cfg) -> None:
    only_departure = {
        "d6": HOLIDAY_TRIP["d6"] | {"target_soc_pct": None, "target_confidence": None}
    }
    inputs = sample_inputs(driving_profiles={"tesla": _profile(**only_departure)}, now=SUNDAY)
    assert state(cfg, "tesla_suggested_target", inputs) == UNKNOWN
    assert entity(cfg, "tesla_suggested_target").attributes(inputs) == {
        "confidence": None,
        "basis": None,
    }
    assert state(cfg, "tesla_suggested_departure", inputs) == "2026-10-26T06:30:00+00:00"


def test_the_suggestion_of_another_vehicle_does_not_leak(cfg) -> None:
    inputs = sample_inputs(driving_profiles={"tesla": _profile(**HOLIDAY_TRIP)}, now=SUNDAY)
    assert state(cfg, "egolf_suggested_departure", inputs) == UNKNOWN


def test_auto_target_switch_state(cfg) -> None:
    on, off = sample_inputs(auto_target={"tesla": True, "egolf": False}), sample_inputs()
    assert (state(cfg, "tesla_auto_target", on), state(cfg, "egolf_auto_target", on)) == (
        "ON",
        "OFF",
    )
    assert state(cfg, "tesla_auto_target", off) == UNKNOWN  # Zustand unbekannt


def test_next_departure_from_health(cfg) -> None:
    inputs = _planned(vehicles=[_health_car()])
    assert state(cfg, "tesla_next_departure", inputs) == DEPARTURE.astimezone(UTC).isoformat()
    # ohne Teil „planned“, mit unlesbarem Zeitpunkt oder ohne Eintrag: unbekannt
    for vehicles in (
        [{"id": "tesla", "planned": None}],
        [_health_car(departure_at="morgen")],
        [_health_car(departure_at=None)],
        [],
    ):
        assert state(cfg, "tesla_next_departure", _planned(vehicles=vehicles)) == UNKNOWN
    assert entity(cfg, "tesla_next_departure").attributes is None


def test_build_inputs_takes_the_driving_values(
    live, price_service, store, forecast_service
) -> None:
    profiles = {"tesla": _profile()}
    inputs = build_inputs(
        live,
        price_service,
        store,
        forecast_service,
        T_APP,
        driving_profiles=profiles,
        auto_target={"tesla": True},
    )
    assert inputs.driving_profiles == profiles and inputs.auto_target == {"tesla": True}
    plain = build_inputs(live, price_service, store, forecast_service, T_APP)
    assert (plain.driving_profiles, plain.auto_target) == ({}, {})
