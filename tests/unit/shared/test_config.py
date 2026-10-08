from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from bindaems.shared.config import ConfigError, load_config, load_secrets

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
