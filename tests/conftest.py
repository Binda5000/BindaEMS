from pathlib import Path

import pytest

from bindaems.shared.config import Config, load_config


@pytest.fixture
def cfg() -> Config:
    return load_config(Path("deploy/config.example.yaml"))
