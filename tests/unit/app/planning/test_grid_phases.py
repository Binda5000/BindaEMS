"""Tests von ``app.planning.grid_phases``: Grenzen je Phase, Phasen der Autos, Platz ``R_t``."""

from __future__ import annotations

from pathlib import Path

import pytest

from bindaems.app.planning.grid_phases import (
    PHASES,
    car_grid_phases,
    grid_import_limits_w,
    phase_room_w,
    phase_shares,
)
from bindaems.shared.config import Config, load_config


@pytest.fixture
def cfg() -> Config:
    return load_config(Path("deploy/config.example.yaml"))


def _limited(cfg: Config, **grid: float | None) -> Config:
    grid_cfg = cfg.grid.model_copy(
        update={"fuse_a": 35, "fuse_margin_a": 2, "import_limit_a": 20, **grid}
    )
    control = cfg.control.model_copy(update={"import_margin_a": 1})
    return cfg.model_copy(update={"grid": grid_cfg, "control": control})


def test_limits_from_fuse_and_import_limit(cfg: Config) -> None:
    limited = _limited(cfg)
    assert grid_import_limits_w(limited.grid, limited.control) == (22770.0, 4370.0)


def test_max_import_kw_replaces_only_the_sum(cfg: Config) -> None:
    limited = _limited(cfg, max_import_kw=11)
    assert grid_import_limits_w(limited.grid, limited.control) == (11000.0, 4370.0)


def test_car_phases_follow_the_wallbox_phase_map(cfg: Config) -> None:
    evcs = cfg.wallboxes["evcs"].model_copy(update={"phase_map": ("L2", "L3", "L1")})
    rotated = cfg.model_copy(update={"wallboxes": {**cfg.wallboxes, "evcs": evcs}})
    assert car_grid_phases(rotated, "egolf", "evcs") == ["L2", "L3"]
    assert car_grid_phases(cfg, "egolf", "evcs") == ["L1", "L2"]
    assert car_grid_phases(cfg, "tesla", "twc") == ["L1", "L2", "L3"]


def test_phase_shares() -> None:
    assert phase_shares(["L1", "L2"]) == {"L1": 0.5, "L2": 0.5}
    assert phase_shares(["L3"]) == {"L3": 1.0}
    third = 1 / 3
    assert phase_shares(None) == {"L1": third, "L2": third, "L3": third}
    assert PHASES == ("L1", "L2", "L3")


@pytest.mark.parametrize(
    ("load_w", "pv_w", "expected"),
    [(15000, 0, 5000.0), (600, 0, 4370.0), (600, 5000, 4370.0), (15000, 20000, 4370.0)],
)
def test_phase_room_keeps_the_model_solvable(load_w: float, pv_w: float, expected: float) -> None:
    assert phase_room_w(4370, load_w, pv_w) == pytest.approx(expected)
