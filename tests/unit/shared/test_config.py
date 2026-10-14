from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from bindaems.shared.config import (
    Config,
    ConfigError,
    ControlConfig,
    GridConfig,
    VehicleCommandLimits,
    WakeConfig,
    load_config,
    load_secrets,
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
