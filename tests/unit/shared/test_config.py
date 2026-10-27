import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from bindaems.core.control.fleet import GUEST, guest_key
from bindaems.shared.config import (
    HA_VEHICLE_SUFFIXES,
    AppConfig,
    CoeChannel,
    Config,
    ConfigError,
    ControlConfig,
    GridConfig,
    VehicleCommandLimits,
    WakeConfig,
    ha_entities,
    ha_object_ids,
    home_signal,
    load_config,
    load_secrets,
    soc_signal,
)

EXAMPLE = Path("deploy/config.example.yaml")


def write_cfg(tmp_path: Path, mutate: Callable[[dict[str, Any]], None]) -> Path:
    data = yaml.safe_load(EXAMPLE.read_text())
    mutate(data)
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_example_config_is_valid() -> None:
    cfg = load_config(EXAMPLE)
    assert cfg.grid.fuse_a == 35
    assert cfg.battery.reserve_soc_pct == 20
    assert cfg.wallbox_order == ["evcs", "twc"]
    assert cfg.victron.instances.pv == {31: "huawei"}


def test_unknown_key_rejected(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["grid"].update(foo=1))
    with pytest.raises(ConfigError, match=r"grid\.foo"):
        load_config(p)


def test_fuse_margin_must_be_below_fuse(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["grid"].update(fuse_a=10, fuse_margin_a=10))
    with pytest.raises(ConfigError, match="Konfiguration ungültig"):
        load_config(p)


def test_reserve_must_be_below_soc_max(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["battery"].update(reserve_soc_pct=100))
    with pytest.raises(ConfigError):
        load_config(p)


def test_phase_map_must_be_permutation(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["wallboxes"]["evcs"].update(phase_map=["L1", "L1", "L2"]))
    with pytest.raises(ConfigError):
        load_config(p)


def test_evcs_current_order(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["wallboxes"]["evcs"].update(safe_a=4))
    with pytest.raises(ConfigError):
        load_config(p)


def test_vehicle_default_wallbox_must_exist(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["vehicles"]["tesla"].update(default_wallbox="garage"))
    with pytest.raises(ConfigError):
        load_config(p)


def test_internal_token_min_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BINDAEMS_INTERNAL_TOKEN", "kurz")
    with pytest.raises(ValidationError):
        load_secrets()


def test_secrets_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BINDAEMS_INTERNAL_TOKEN", "x" * 32)
    monkeypatch.setenv("BINDAEMS_TESSIE_TOKEN", "abc")
    token = load_secrets().tessie_token
    assert token is not None and token.get_secret_value() == "abc"


def test_empty_secret_variables_count_as_unset(monkeypatch) -> None:
    # .env.example enthält leere Einträge; leer heißt „nicht gesetzt“, nicht „leeres Token“
    monkeypatch.setenv("BINDAEMS_INTERNAL_TOKEN", "x" * 32)
    monkeypatch.setenv("BINDAEMS_TESSIE_TOKEN", "")
    monkeypatch.setenv("BINDAEMS_MQTT_PASSWORD", "")
    secrets = load_secrets()
    assert secrets.tessie_token is None and secrets.mqtt_password is None


@pytest.mark.parametrize("days", [0, 3651])
def test_decisions_keep_days_is_limited(days: int) -> None:
    with pytest.raises(ValidationError):
        AppConfig(decisions_keep_days=days)
    assert AppConfig().decisions_keep_days == 180
    assert AppConfig(decisions_keep_days=3650).decisions_keep_days == 3650


def test_app_and_ha_mqtt_from_example() -> None:
    cfg = load_config(EXAMPLE)
    assert (cfg.app.port, cfg.app.core_url) == (8080, "http://ems-core:8081")
    assert (cfg.app.data_dir, cfg.app.backup_dir) == (Path("/data"), Path("/backup"))
    assert cfg.app.trusted_proxies == ["192.168.1.10"]
    assert cfg.app.cookie_secure is True and cfg.app.ui_dir == Path("/app/ui")
    assert cfg.homeassistant is not None
    mqtt = cfg.homeassistant.mqtt
    assert mqtt is not None
    assert (mqtt.host, mqtt.port, mqtt.tls, mqtt.username) == (
        "homeassistant.lan",
        1883,
        False,
        "bindaems",
    )
    assert (mqtt.discovery_prefix, mqtt.base_topic, mqtt.publish_interval_s) == (
        "homeassistant",
        "bindaems",
        10.0,
    )


def test_app_section_is_optional(tmp_path: Path) -> None:
    cfg = load_config(write_cfg(tmp_path, lambda d: d.pop("app")))
    assert cfg.app.port == 8080 and cfg.app.trusted_proxies == []


@pytest.mark.parametrize("value", ["proxy.lan", "300.1.1.1", "10.0.0.0/33"])
def test_trusted_proxies_must_be_ip_or_network(tmp_path: Path, value: str) -> None:
    p = write_cfg(tmp_path, lambda d: d["app"].update(trusted_proxies=[value]))
    with pytest.raises(ConfigError, match="trusted_proxies"):
        load_config(p)


def test_trusted_proxies_accept_addresses_and_networks(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["app"].update(trusted_proxies=["10.0.0.0/8", "fd00::1"]))
    assert load_config(p).app.trusted_proxies == ["10.0.0.0/8", "fd00::1"]


def test_ha_mqtt_topics_must_be_plain_names(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["homeassistant"]["mqtt"].update(base_topic="bindaems/#"))
    with pytest.raises(ConfigError, match="base_topic"):
        load_config(p)


def test_ha_mqtt_password_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BINDAEMS_INTERNAL_TOKEN", "x" * 32)
    monkeypatch.setenv("BINDAEMS_HA_MQTT_PASSWORD", "mqtt-geheim")
    password = load_secrets().ha_mqtt_password
    assert password is not None and password.get_secret_value() == "mqtt-geheim"


def test_safety_defaults() -> None:
    cfg = load_config(EXAMPLE)
    assert cfg.victron.live_allowed is False
    assert cfg.grid.max_import_kw is None
    assert cfg.safety.lock_release_s == 60.0
    assert cfg.core.data_dir == Path("/data")
    assert cfg.vehicles["tesla"].commands == VehicleCommandLimits(
        current_interval_s=90.0, current_step_a=2, per_hour=20, start_stop_per_hour=6
    )


def test_vehicle_live_needs_tessie(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["vehicles"]["egolf"].update(live_allowed=True))
    with pytest.raises(ConfigError, match="live_allowed verlangt einen tessie-Abschnitt"):
        load_config(p)


@pytest.mark.parametrize(
    "evcharger", [{}, {41: "evcs2"}], ids=["ohne Instanz", "Instanz für eine andere Wallbox"]
)
def test_live_evcs_needs_its_gx_instance(tmp_path: Path, evcharger: dict[int, str]) -> None:
    # Ohne die Zuordnung setzt der Watchdog am Cerbo für diese EVCS keinen sicheren Strom.
    def live_without_instance(d: dict[str, Any]) -> None:
        d["wallboxes"]["evcs"]["live_allowed"] = True
        d["victron"]["instances"]["evcharger"] = evcharger

    p = write_cfg(tmp_path, live_without_instance)
    with pytest.raises(ConfigError) as caught:
        load_config(p)
    assert (
        "wallboxes.evcs.live_allowed verlangt einen Eintrag „<Instanz>: evcs“ in "
        "victron.instances.evcharger; ohne ihn setzt der Watchdog am Cerbo für diese EVCS keinen "
        "sicheren Strom"
    ) in str(caught.value)


def test_live_evcs_with_its_gx_instance_is_valid(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["wallboxes"]["evcs"].update(live_allowed=True))
    assert load_config(p).wallboxes["evcs"].live_allowed is True  # {40: evcs} aus dem Beispiel


def test_evcs_without_gx_instance_is_fine_while_not_live(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["victron"]["instances"].update(evcharger={}))
    assert load_config(p).victron.instances.evcharger == {}


def test_start_stop_limit_within_hourly_limit(tmp_path: Path) -> None:
    limits = {"per_hour": 5, "start_stop_per_hour": 6}
    p = write_cfg(tmp_path, lambda d: d["vehicles"]["tesla"].update(commands=limits))
    with pytest.raises(ConfigError, match="start_stop_per_hour darf per_hour nicht übersteigen"):
        load_config(p)


@pytest.mark.parametrize("seconds", [0, 3601])
def test_lock_release_bounds(tmp_path: Path, seconds: int) -> None:
    p = write_cfg(tmp_path, lambda d: d.update(safety={"lock_release_s": seconds}))
    with pytest.raises(ConfigError, match=r"safety\.lock_release_s"):
        load_config(p)


def test_max_import_must_be_positive(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["grid"].update(max_import_kw=0))
    with pytest.raises(ConfigError, match=r"grid\.max_import_kw"):
        load_config(p)


# Laderegelung (Plan 2c-1): Importlimit, Abschnitt control und Weckregeln


def test_charging_defaults(cfg) -> None:
    assert cfg.grid.import_limit_a == 20.0
    assert cfg.control == ControlConfig(
        import_margin_a=1.0,
        surplus_margin_w=100.0,
        start_after_s=120.0,
        stop_after_s=300.0,
        min_on_s=600.0,
        min_off_s=600.0,
        reserve_guard_pct=5.0,
        tesla_average_s=90.0,
        tesla_caution_s=60.0,
        evcs_hysteresis_a=0.5,
    )
    assert cfg.vehicles["tesla"].wake == WakeConfig(interval_s=1800.0, per_day=6)


def test_charging_sections_are_optional(tmp_path: Path, cfg) -> None:
    def drop(d: dict[str, Any]) -> None:
        del d["control"], d["grid"]["import_limit_a"], d["vehicles"]["tesla"]["wake"]

    loaded = load_config(write_cfg(tmp_path, drop))
    # fehlen die Angaben, gelten dieselben Werte, die das Beispiel nennt
    assert loaded.grid.import_limit_a == cfg.grid.import_limit_a
    assert loaded.control == ControlConfig() == cfg.control
    assert loaded.vehicles["tesla"].wake == WakeConfig() == cfg.vehicles["tesla"].wake
    assert loaded.vehicles["egolf"].wake == WakeConfig()


def test_import_limit_must_stay_below_the_fuse_margin() -> None:
    with pytest.raises(
        ValidationError, match="import_limit_a muss unter fuse_a − fuse_margin_a liegen"
    ):
        GridConfig(fuse_a=35, fuse_margin_a=2, import_limit_a=33)
    assert GridConfig(fuse_a=35, fuse_margin_a=2, import_limit_a=32.9).import_limit_a == 32.9


def test_default_import_limit_needs_a_fuse_above_it() -> None:
    # mit einer kleinen Sicherung muss das Importlimit ausdrücklich darunter gesetzt werden
    with pytest.raises(ValidationError, match="import_limit_a muss unter"):
        GridConfig(fuse_a=20, fuse_margin_a=2)
    assert GridConfig(fuse_a=20, fuse_margin_a=2, import_limit_a=17).import_limit_a == 17


def test_import_margin_must_stay_below_the_import_limit(cfg) -> None:
    data = cfg.model_dump() | {"control": {"import_margin_a": 20}}
    with pytest.raises(
        ValidationError,
        match=r"control\.import_margin_a muss kleiner als grid\.import_limit_a sein",
    ):
        Config.model_validate(data)
    just_below = cfg.model_dump() | {"control": {"import_margin_a": 19.9}}
    assert Config.model_validate(just_below).control.import_margin_a == 19.9


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("grid", "import_limit_a", 0),
        ("control", "import_margin_a", -0.1),
        ("control", "surplus_margin_w", -1),
        ("control", "start_after_s", -1),
        ("control", "stop_after_s", -1),
        ("control", "min_on_s", -1),
        ("control", "min_off_s", -1),
        ("control", "reserve_guard_pct", -1),
        ("control", "reserve_guard_pct", 100.1),
        ("control", "tesla_average_s", 0),
        ("control", "tesla_caution_s", 0),
        ("control", "evcs_hysteresis_a", -0.1),
        ("control", "unbekannt", 1),
    ],
)
def test_charging_values_are_bounded(
    tmp_path: Path, section: str, field: str, value: float
) -> None:
    p = write_cfg(tmp_path, lambda d: d[section].update({field: value}))
    with pytest.raises(ConfigError, match=rf"{section}\.{field}"):
        load_config(p)


@pytest.mark.parametrize(
    ("field", "value"),
    [("interval_s", -1), ("per_day", -1), ("per_day", 1.5), ("unbekannt", 1)],
)
def test_wake_values_are_bounded(tmp_path: Path, field: str, value: float) -> None:
    p = write_cfg(tmp_path, lambda d: d["vehicles"]["tesla"]["wake"].update({field: value}))
    with pytest.raises(ConfigError, match=rf"vehicles\.tesla\.wake\.{field}"):
        load_config(p)


def test_charging_values_may_be_zero(tmp_path: Path) -> None:
    # 0 heißt: keine Verzögerung bzw. kein Weckvorgang; reserve_guard_pct reicht bis 100
    def zeros(d: dict[str, Any]) -> None:
        d["control"].update(
            import_margin_a=0,
            surplus_margin_w=0,
            start_after_s=0,
            stop_after_s=0,
            min_on_s=0,
            min_off_s=0,
            reserve_guard_pct=100,
            evcs_hysteresis_a=0,
        )
        d["vehicles"]["tesla"]["wake"].update(interval_s=0, per_day=0)

    loaded = load_config(write_cfg(tmp_path, zeros))
    assert (loaded.control.start_after_s, loaded.control.reserve_guard_pct) == (0.0, 100.0)
    assert loaded.control.evcs_hysteresis_a == 0.0
    assert loaded.vehicles["tesla"].wake == WakeConfig(interval_s=0.0, per_day=0)


# Akku-Sollwert und 15-min-Wächter (Plan 2c-2a): Parameter im Abschnitt control


def test_battery_and_peak_defaults(cfg) -> None:
    control = cfg.control
    assert (control.support_soc_pct, control.support_hysteresis_pct) == (70.0, 5.0)
    assert (control.support_max_w, control.support_max_s) == (3000.0, 900.0)
    assert (control.setpoint_deadband_w, control.override_refresh_s) == (200.0, 240.0)
    assert (control.peak_target_kw, control.peak_margin_kw) == (None, 0.3)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("support_soc_pct", -1),
        ("support_soc_pct", 101),
        ("support_hysteresis_pct", -1),
        ("support_max_w", -1),
        ("support_max_s", -1),
        ("setpoint_deadband_w", -1),
        ("setpoint_deadband_w", 0),  # ohne Totband entstünde bei W' = 0 ein Override (Basis)
        ("override_refresh_s", 0),
        ("override_refresh_s", 281),
        ("peak_target_kw", 0),
        ("peak_target_kw", -1),
        ("peak_margin_kw", -0.1),
    ],
)
def test_invalid_battery_and_peak_values_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError) as caught:
        ControlConfig(**{field: value})
    # abgelehnt wegen des Werts, nicht weil das Feld unbekannt ist (extra="forbid")
    assert [error["type"] for error in caught.value.errors()] != ["extra_forbidden"]


def test_override_refresh_may_reach_280_s() -> None:
    assert ControlConfig(override_refresh_s=280).override_refresh_s == 280


def test_battery_and_peak_values_may_sit_on_their_bounds() -> None:
    # 0 heißt: keine Schwelle, keine Hysterese, keine Unterstützung, keine Marge
    zeros = ControlConfig(
        support_soc_pct=0,
        support_hysteresis_pct=0,
        support_max_w=0,
        support_max_s=0,
        peak_margin_kw=0,
    )
    assert (zeros.support_soc_pct, zeros.support_hysteresis_pct) == (0.0, 0.0)
    assert (zeros.support_max_w, zeros.support_max_s) == (0.0, 0.0)
    assert zeros.peak_margin_kw == 0.0
    assert ControlConfig(support_soc_pct=100).support_soc_pct == 100.0
    assert ControlConfig(peak_target_kw=0.1).peak_target_kw == 0.1
    assert ControlConfig(setpoint_deadband_w=0.1).setpoint_deadband_w == 0.1  # nur über 0


def test_peak_target_is_set_in_the_config_file(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["control"].update(peak_target_kw=8))
    assert load_config(p).control.peak_target_kw == 8.0


def test_commented_peak_target_of_the_example_is_valid_when_enabled(tmp_path: Path) -> None:
    # der Betreiber nimmt die Zeile aus dem Beispiel, indem er das „#“ entfernt
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "\n  # peak_target_kw: 8 " in text
    enabled = tmp_path / "config.yaml"
    uncommented = text.replace("\n  # peak_target_kw: 8", "\n  peak_target_kw: 8", 1)
    enabled.write_text(uncommented, encoding="utf-8")
    assert load_config(enabled).control.peak_target_kw == 8.0


# Endliche Werte: Override und grid_budget_w sind nie NaN oder ±∞, also nimmt die Konfiguration
# solche Werte für die neuen Felder nicht an

FINITE_CONTROL_FIELDS = (
    "support_soc_pct",
    "support_hysteresis_pct",
    "support_max_w",
    "support_max_s",
    "setpoint_deadband_w",
    "override_refresh_s",
    "peak_target_kw",
    "peak_margin_kw",
    "tesla_against_s",
    "tesla_against_retry_s",
)


@pytest.mark.parametrize(
    "value", [float("inf"), float("-inf"), float("nan")], ids=["inf", "-inf", "nan"]
)
@pytest.mark.parametrize("field", FINITE_CONTROL_FIELDS)
def test_battery_and_peak_values_must_be_finite(field: str, value: float) -> None:
    with pytest.raises(ValidationError) as caught:
        ControlConfig(**{field: value})
    # Die Prüfung auf endliche Zahlen greift vor den Grenzen, auch bei support_soc_pct und
    # override_refresh_s, die schon ein Maximum haben.
    assert [error["type"] for error in caught.value.errors()] == ["finite_number"]


@pytest.mark.parametrize("field", FINITE_CONTROL_FIELDS)
def test_infinite_battery_and_peak_values_in_the_config_file_are_rejected(
    tmp_path: Path, field: str
) -> None:
    # in YAML steht unendlich als „.inf“
    p = write_cfg(tmp_path, lambda d: d["control"].update({field: float("inf")}))
    with pytest.raises(ConfigError, match=rf"control\.{field}"):
        load_config(p)


# Zuordnung (Plan 2c-2b): home_entity aus Home Assistant


def test_home_entity_needs_homeassistant(tmp_path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d.pop("homeassistant")
        d["vehicles"]["egolf"].pop("soc_entity")  # sonst meldet sich zuerst soc_entity

    with pytest.raises(
        ConfigError, match=r"vehicles\.egolf\.home_entity verlangt einen homeassistant-Abschnitt"
    ):
        load_config(write_cfg(tmp_path, mutate))


def test_home_entities_are_subscribed_under_their_signal(cfg) -> None:
    assert home_signal("egolf") == "vehicle.egolf.home_state"
    assert ha_entities(cfg) == {
        "vehicle.egolf.soc_pct": "sensor.egolf_soc",
        "vehicle.egolf.home_state": "device_tracker.egolf",
    }
    assert cfg.homeassistant.entities == {}  # beide kommen aus den Fahrzeugen
    assert ha_entities(cfg.model_copy(update={"homeassistant": None})) == {}


def test_a_vehicle_without_home_entity_adds_no_subscription(tmp_path: Path) -> None:
    loaded = load_config(write_cfg(tmp_path, lambda d: d["vehicles"]["egolf"].pop("home_entity")))
    assert loaded.vehicles["egolf"].home_entity is None
    assert ha_entities(loaded) == {"vehicle.egolf.soc_pct": "sensor.egolf_soc"}


def test_ha_entities_leave_the_configured_ones_untouched(cfg) -> None:
    ha_entities(cfg)
    assert cfg.homeassistant.entities == {}


# Plan 4b, Präzisierung 4: soc_entity wird unter soc_signal abonniert


def test_soc_entity_is_subscribed_under_the_soc_signal(cfg) -> None:
    assert soc_signal("egolf") == "vehicle.egolf.soc_pct"
    assert ha_entities(cfg)[soc_signal("egolf")] == "sensor.egolf_soc"
    # der Tesla hat keine soc_entity: sein Ladestand kommt aus Tessie
    assert soc_signal("tesla") not in ha_entities(cfg)


def test_soc_entity_conflicting_with_entities_is_rejected(tmp_path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["homeassistant"]["entities"] = {"vehicle.egolf.soc_pct": "sensor.other"}

    with pytest.raises(
        ConfigError,
        match=r"vehicle\.egolf\.soc_pct ist in homeassistant\.entities anders belegt als "
        r"soc_entity",
    ):
        load_config(write_cfg(tmp_path, mutate))


def test_soc_entity_equal_to_the_entities_entry_is_fine(tmp_path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["homeassistant"]["entities"] = {"vehicle.egolf.soc_pct": "sensor.egolf_soc"}

    loaded = load_config(write_cfg(tmp_path, mutate))
    assert ha_entities(loaded)["vehicle.egolf.soc_pct"] == "sensor.egolf_soc"


def test_charge_efficiency_defaults_to_90_and_is_limited(cfg, tmp_path) -> None:
    assert cfg.vehicles["egolf"].charge_efficiency_pct == 90.0
    assert cfg.vehicles["tesla"].charge_efficiency_pct == 90.0
    for value in (49.9, 100.1):
        with pytest.raises(ConfigError, match=r"vehicles\.egolf\.charge_efficiency_pct"):
            load_config(
                write_cfg(
                    tmp_path,
                    lambda d, v=value: d["vehicles"]["egolf"].update({"charge_efficiency_pct": v}),
                )
            )
    ok = load_config(
        write_cfg(
            tmp_path,
            lambda d: d["vehicles"]["egolf"].update({"charge_efficiency_pct": 50}),
        )
    )
    assert ok.vehicles["egolf"].charge_efficiency_pct == 50.0


def test_charge_limit_sync_is_off_by_default_and_needs_tessie(cfg, tmp_path) -> None:
    assert not cfg.vehicles["tesla"].charge_limit_sync
    synced = load_config(
        write_cfg(tmp_path, lambda d: d["vehicles"]["tesla"].update({"charge_limit_sync": True}))
    )
    assert synced.vehicles["tesla"].charge_limit_sync
    with pytest.raises(ConfigError, match=r"charge_limit_sync verlangt einen tessie-Abschnitt"):
        load_config(
            write_cfg(
                tmp_path, lambda d: d["vehicles"]["egolf"].update({"charge_limit_sync": True})
            )
        )


def test_object_ids_include_planned_and_statistics_entities(cfg) -> None:
    ids = ha_object_ids(cfg)
    assert {
        "savings_today",
        "savings_month",
        "autarky_today",
        "problem_target_unreachable",
        "tesla_target_soc",
        "tesla_departure",
        "tesla_done_by",
        "tesla_target_reachable",
        "egolf_charge_energy",
        "tesla_auto_target",
        "tesla_suggested_departure",
        "egolf_suggested_target",
        "egolf_next_departure",
    } <= set(ids)
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("key", [GUEST, guest_key("evcs"), "guest@nowhere"])
def test_vehicle_keys_of_guests_are_rejected(tmp_path, key) -> None:
    # Die Gast-Schlüssel der Zuordnung (fleet) wären sonst zweideutig (Gesamtreview, Konfiguration)
    def mutate(d: dict[str, Any]) -> None:
        d["vehicles"][key] = d["vehicles"].pop("egolf")

    with pytest.raises(ConfigError, match=rf"vehicles\.{key}: .*Gast"):
        load_config(write_cfg(tmp_path, mutate))


def test_a_vehicle_key_may_merely_contain_guest(tmp_path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["vehicles"]["guest_car"] = d["vehicles"].pop("egolf")

    assert "guest_car" in load_config(write_cfg(tmp_path, mutate)).vehicles


def _rename(vehicle: str, name: str) -> Callable[[dict[str, Any]], None]:
    def mutate(d: dict[str, Any]) -> None:
        d["vehicles"][vehicle]["name"] = name

    return mutate


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("", r"vehicles\.egolf\.name: .*nicht leer"),
        ("   ", r"vehicles\.egolf\.name: .*nicht leer"),
        (" e-Golf", r"vehicles\.egolf\.name: .*Leerzeichen"),
        ("e-Golf\n", r"vehicles\.egolf\.name: .*Leerzeichen"),
        ("Automatisch", r"vehicles\.egolf\.name: .*Automatisch.*vorbehalten"),
        ("AUTOMATISCH", r"vehicles\.egolf\.name: .*Automatisch.*vorbehalten"),
        ("tesla model 3", r"vehicles\.tesla\.name: .*vehicles\.egolf"),
        ("TESLA MODEL 3", r"vehicles\.tesla\.name: .*vehicles\.egolf"),
    ],
)
def test_vehicle_names_must_be_usable_as_options(tmp_path, name, message) -> None:
    # Die Auswahl der Zuordnung in Home Assistant bildet Namen auf Fahrzeuge ab
    with pytest.raises(ConfigError, match=message):
        load_config(write_cfg(tmp_path, _rename("egolf", name)))


def test_vehicle_names_may_differ_in_more_than_case(tmp_path) -> None:
    loaded = load_config(write_cfg(tmp_path, _rename("egolf", "Tesla Model 4")))
    assert loaded.vehicles["egolf"].name == "Tesla Model 4"


def test_vehicle_keys_must_not_collide_as_ha_object_ids(tmp_path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["vehicles"]["Tesla"] = d["vehicles"].pop("egolf")  # gleiche Objekt-ID wie „tesla“

    with pytest.raises(ConfigError, match=r"vehicles\.tesla: .*Objekt-ID .*tesla.*vehicles\.Tesla"):
        load_config(write_cfg(tmp_path, mutate))


def test_a_vehicle_key_must_not_collide_with_a_wallbox_key(tmp_path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["vehicles"]["twc"] = d["vehicles"].pop("egolf")
        d["vehicles"]["twc"]["default_wallbox"] = "twc"

    with pytest.raises(ConfigError, match=r"wallboxes\.twc: .*Objekt-ID .*twc.*vehicles\.twc"):
        load_config(write_cfg(tmp_path, mutate))


@pytest.mark.parametrize(("key", "entity"), [("Guest", "guest_mode"), ("battery", "battery_soc")])
def test_a_vehicle_key_must_not_collide_with_a_system_entity(tmp_path, key, entity) -> None:
    # „Guest“ ergäbe die Entität guest_mode des Fahrzeugs neben dem Gast-Lademodus des Systems
    def mutate(d: dict[str, Any]) -> None:
        d["vehicles"][key] = d["vehicles"].pop("egolf")

    with pytest.raises(ConfigError, match=rf"vehicles\.{key}: .*{entity}"):
        load_config(write_cfg(tmp_path, mutate))


def test_reserve_defaults_to_four() -> None:
    assert load_config(EXAMPLE).control.tesla_reserve_commands == 4


def test_reserve_must_stay_below_the_hourly_limit(tmp_path: Path) -> None:
    def change(d: dict) -> None:
        d["control"]["tesla_reserve_commands"] = 20  # per_hour 20 aus dem Beispiel

    with pytest.raises(
        ConfigError,
        match=r"control\.tesla_reserve_commands muss unter "
        r"vehicles\.tesla\.commands\.per_hour liegen",
    ):
        load_config(write_cfg(tmp_path, change))
    ok = write_cfg(tmp_path, lambda d: d["control"].update(tesla_reserve_commands=19))
    assert load_config(ok).control.tesla_reserve_commands == 19


def test_reserve_is_free_for_a_vehicle_without_tessie(tmp_path: Path) -> None:
    def change(d: dict) -> None:
        d["control"]["tesla_reserve_commands"] = 20
        d["vehicles"]["tesla"].update(tessie=None, live_allowed=False)

    assert load_config(write_cfg(tmp_path, change)).control.tesla_reserve_commands == 20


def test_self_start_defaults_to_120_s_and_10_min() -> None:
    control = load_config(EXAMPLE).control
    assert (control.tesla_against_s, control.tesla_against_retry_s) == (120.0, 600.0)


@pytest.mark.parametrize("field", ["tesla_against_s", "tesla_against_retry_s"])
@pytest.mark.parametrize("value", [0, -1])
def test_self_start_times_must_be_positive(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        ControlConfig(**{field: value})


def test_notify_service_pattern(tmp_path: Path) -> None:
    p = write_cfg(
        tmp_path, lambda d: d["homeassistant"].update(notify_service="notify.mobile_app_handy")
    )
    assert load_config(p).homeassistant.notify_service == "notify.mobile_app_handy"
    for wrong in ("light.kueche", "notify.", "notify.Handy", "mobile_app_handy"):
        p = write_cfg(
            tmp_path, lambda d, wrong=wrong: d["homeassistant"].update(notify_service=wrong)
        )
        with pytest.raises(ConfigError, match="notify_service"):
            load_config(p)
    assert load_config(write_cfg(tmp_path, lambda d: None)).homeassistant.notify_service is None


def test_load_history_is_optional_and_checked(tmp_path: Path) -> None:
    assert load_config(write_cfg(tmp_path, lambda d: None)).homeassistant.load_history is None
    p = write_cfg(
        tmp_path,
        lambda d: d["homeassistant"].update(
            load_history={"entity": "sensor.hausverbrauch", "subtract": ["sensor.wallbox_leistung"]}
        ),
    )
    history = load_config(p).homeassistant.load_history
    assert history.entity == "sensor.hausverbrauch" and history.subtract == [
        "sensor.wallbox_leistung"
    ]
    for wrong in ("light.kueche", "sensor.", "sensor.Haus"):
        p = write_cfg(
            tmp_path,
            lambda d, wrong=wrong: d["homeassistant"].update(load_history={"entity": wrong}),
        )
        with pytest.raises(ConfigError, match=r"load_history\.entity"):
            load_config(p)
    p = write_cfg(
        tmp_path,
        lambda d: d["homeassistant"].update(
            load_history={"entity": "sensor.haus", "subtract": ["switch.x"]}
        ),
    )
    with pytest.raises(ConfigError, match=r"load_history\.subtract"):
        load_config(p)


def test_load_history_needs_ha_database(tmp_path: Path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["homeassistant"]["load_history"] = {"entity": "sensor.hausverbrauch"}
        d["influxdb"].pop("ha_database")

    with pytest.raises(
        ConfigError, match=r"homeassistant\.load_history verlangt influxdb\.ha_database"
    ):
        load_config(write_cfg(tmp_path, mutate))


def _example_dict() -> dict[str, Any]:
    return yaml.safe_load(EXAMPLE.read_text())


def _thermal_dict(**changes: Any) -> dict[str, Any]:
    data = _example_dict()
    data["thermal"]["devices"]["klima"].update(changes)
    return data


def test_thermal_defaults_empty() -> None:
    data = _example_dict()
    data.pop("thermal")
    assert Config.model_validate(data).thermal.devices == {}
    data["thermal"] = {}
    assert Config.model_validate(data).thermal.devices == {}


def test_example_has_klima(cfg: Config) -> None:
    dev = cfg.thermal.devices["klima"]
    assert dev.kind == "coe_offset" and str(dev.cmi_host) == "192.168.81.92"
    assert dev.label == "Klimaanlage" and dev.port == 5442 and dev.listen_port == 5442
    assert dev.own_node == 60 and dev.own_output == 1 and dev.max_offset_k == 3.0
    assert dev.step_k == 0.5 and dev.step_interval_s == 600
    assert dev.send_interval_s == 30 and dev.stale_after_s == 180
    assert dev.receive.offset_active_k.analog == 3 and dev.receive.running.digital == 5
    assert dev.receive.actual_c.node == 56 and dev.receive.actual_c.unit == 1
    assert dev.receive.setpoint_c.analog == 2 and dev.receive.cmd_on is not None
    assert dev.receive.cmd_on.digital == 1
    assert (dev.receive.cooling.digital, dev.receive.venting.digital) == (2, 3)
    assert dev.receive.heating.digital == 4
    power = dev.receive.power_kw
    assert power is not None and (power.analog, power.unit, power.scale) == (4, 10, 100)
    assert dev.live_allowed is False
    assert dev.strategy.more_above_w == 800 and dev.strategy.import_above_w == 300
    assert dev.strategy.battery_soc_pct == 90 and dev.strategy.less_max_steps == 2


@pytest.mark.parametrize(
    "bad",
    [{"max_offset_k": 3.5}, {"max_offset_k": 1.2}, {"max_offset_k": 0}, {"own_node": 56}],
)
def test_coe_offset_rejects(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(_thermal_dict(**bad))


@pytest.mark.parametrize(
    ("field", "channel", "message"),
    [
        (
            "heating",
            {"node": 56, "digital": 2},
            "cooling und heating lesen denselben Wert (Knoten 56, digital 2)",
        ),
        (
            "setpoint_c",
            {"node": 56, "analog": 1, "unit": 1},
            "actual_c und setpoint_c lesen denselben Wert (Knoten 56, analog 1)",
        ),
    ],
)
def test_receive_channels_must_differ(field: str, channel: dict[str, Any], message: str) -> None:
    # Abschlussreview M9 (T1): Lesen zwei Größen denselben Wert, wäre etwa die Betriebsart beim
    # Kühlen immer Heizen, und das Vorzeichen des Versatzes kippte.
    data = _example_dict()
    data["thermal"]["devices"]["klima"]["receive"][field] = channel
    with pytest.raises(ValidationError, match=re.escape(message)):
        Config.model_validate(data)


def test_receive_channels_may_share_a_number_across_kinds() -> None:
    # Analog 2 und digital 2 desselben Knotens sind verschiedene Werte (so im Beispiel).
    receive = Config.model_validate(_example_dict()).thermal.devices["klima"].receive
    assert (receive.setpoint_c.node, receive.setpoint_c.analog) == (56, 2)
    assert (receive.cooling.node, receive.cooling.digital) == (56, 2)


def test_own_node_as_receive_node_has_a_german_message() -> None:
    with pytest.raises(ValidationError, match="own_node ist auch ein Empfangsknoten"):
        Config.model_validate(_thermal_dict(own_node=56))


@pytest.mark.parametrize(
    "bad",
    [
        {"step_k": 1.0},
        {"step_interval_s": 59},
        {"send_interval_s": 4},
        {"send_interval_s": 61},
        {"stale_after_s": 59},
        {"stale_after_s": 291},
        {"port": 0},
        {"own_output": 65},
        {"cmi_host": "cmi.lan"},
        {"strategy": {"less_max_steps": 7}},
        {"strategy": {"battery_soc_pct": 101}},
        {"foo": 1},
    ],
)
def test_coe_offset_values_are_bounded(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(_thermal_dict(**bad))


def test_coe_offset_may_sit_on_its_bounds() -> None:
    cfg = Config.model_validate(
        _thermal_dict(max_offset_k=0.5, step_interval_s=60, send_interval_s=5, stale_after_s=290)
    )
    assert cfg.thermal.devices["klima"].max_offset_k == 0.5


def test_only_one_coe_device() -> None:
    data = _example_dict()
    data["thermal"]["devices"]["zweite"] = dict(data["thermal"]["devices"]["klima"])
    with pytest.raises(ValidationError, match=r"Phase 6a kennt nur eine Klimaanlage über CoE\."):
        Config.model_validate(data)


@pytest.mark.parametrize("name", ["Klima", "1klima", "kli-ma", "klima anlage", ""])
def test_thermal_names_are_plain(name: str) -> None:
    data = _example_dict()
    data["thermal"]["devices"] = {name: data["thermal"]["devices"]["klima"]}
    with pytest.raises(ValidationError):
        Config.model_validate(data)


def test_thermal_loads_from_the_config_file(tmp_path: Path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["thermal"]["devices"]["klima"]["max_offset_k"] = 3.5

    with pytest.raises(ConfigError, match=r"thermal\.devices\.klima\..*max_offset_k"):
        load_config(write_cfg(tmp_path, mutate))


@pytest.mark.parametrize(
    "channel",
    [
        {"node": 56},
        {"node": 56, "analog": 1, "digital": 1, "unit": 1},
        {"node": 0, "analog": 1, "unit": 1},
        {"node": 63, "analog": 1, "unit": 1},
        {"node": 56, "analog": 0, "unit": 1},
        {"node": 56, "digital": 65},
    ],
)
def test_channel_needs_exactly_one_of_analog_digital(channel: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CoeChannel.model_validate(channel)


def test_channel_with_one_address_is_valid() -> None:
    assert CoeChannel(node=56, analog=1, unit=1).analog == 1
    assert CoeChannel(node=56, digital=64).digital == 64


@pytest.mark.parametrize("unit", [1, 7])
def test_channel_scale_is_optional_for_tenths_units(unit: int) -> None:
    assert CoeChannel(node=56, analog=1, unit=unit).scale is None


def test_channel_scale_required_for_unit_10() -> None:
    with pytest.raises(ValidationError, match="scale"):
        CoeChannel(node=56, analog=4, unit=10)
    assert CoeChannel(node=56, analog=4, unit=10, scale=100).scale == 100


@pytest.mark.parametrize("unit", [-1, 256])
def test_channel_unit_out_of_range_is_rejected(unit: int) -> None:
    # die Einheit ist ein Byte im Paket
    with pytest.raises(ValidationError):
        CoeChannel(node=56, analog=4, unit=unit, scale=10)


@pytest.mark.parametrize("unit", [0, 7, 255])
def test_channel_unit_in_range_is_accepted(unit: int) -> None:
    assert CoeChannel(node=56, analog=4, unit=unit, scale=10).unit == unit


@pytest.mark.parametrize("scale", [0, -1])
def test_channel_scale_must_be_positive(scale: float) -> None:
    with pytest.raises(ValidationError):
        CoeChannel(node=56, analog=4, unit=10, scale=scale)


def test_receive_needs_the_mandatory_channels() -> None:
    data = _thermal_dict()
    del data["thermal"]["devices"]["klima"]["receive"]["running"]
    with pytest.raises(ValidationError):
        Config.model_validate(data)
    data = _thermal_dict()
    for optional in ("power_kw", "cmd_on"):
        del data["thermal"]["devices"]["klima"]["receive"][optional]
    receive = Config.model_validate(data).thermal.devices["klima"].receive
    assert receive.power_kw is None and receive.cmd_on is None


def test_object_ids_include_thermal_entities(cfg) -> None:
    ids = ha_object_ids(cfg)
    suffixes = ("offset", "mode", "power", "wish", "planned_offset")
    assert {f"thermal_klima_{s}" for s in suffixes} <= set(ids)
    assert "thermal_plan" in ids  # der globale Schalter „Vorheizen und Vorkühlen“
    assert len(ids) == len(set(ids))
    bare = cfg.model_copy(update={"thermal": cfg.thermal.model_copy(update={"devices": {}})})
    assert "thermal_plan" not in ha_object_ids(bare)  # ohne Klimaanlage nichts davon


def test_a_vehicle_key_must_not_collide_with_a_thermal_entity(tmp_path) -> None:
    # das Fahrzeug „thermal_klima“ ergäbe die Entität thermal_klima_mode der Klimaanlage
    def mutate(d: dict[str, Any]) -> None:
        d["vehicles"]["thermal_klima"] = d["vehicles"].pop("egolf")

    with pytest.raises(
        ConfigError, match=r"thermal\.klima: .*thermal_klima_mode.*vehicles\.thermal_klima"
    ):
        load_config(write_cfg(tmp_path, mutate))


def test_driving_suffixes_are_ha_vehicle_suffixes() -> None:
    assert {"auto_target", "suggested_departure", "suggested_target", "next_departure"} <= set(
        HA_VEHICLE_SUFFIXES
    )
    assert len(HA_VEHICLE_SUFFIXES) == len(set(HA_VEHICLE_SUFFIXES))


@pytest.mark.parametrize(
    ("key", "entity"),
    [("tesla_suggested", "tesla_suggested_departure"), ("tesla_next", "tesla_next_departure")],
)
def test_suffix_collision_is_detected(tmp_path, key, entity) -> None:
    # „tesla_next“ mit der Endung „departure“ ergäbe dieselbe Entität wie „tesla“ mit
    # „next_departure“
    def mutate(d: dict[str, Any]) -> None:
        d["vehicles"][key] = d["vehicles"].pop("egolf")

    with pytest.raises(ConfigError, match=rf"vehicles\.{key}: .*{entity}.*vehicles\.tesla"):
        load_config(write_cfg(tmp_path, mutate))
