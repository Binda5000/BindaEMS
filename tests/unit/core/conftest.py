"""Gemeinsame Fixtures für alle Tests unter ``tests/unit/core`` (auch ``control/``)."""

import pytest

from bindaems.core.safety.limits import HardLimits
from bindaems.shared.config import Config


@pytest.fixture
def limits(cfg: Config) -> HardLimits:
    """Harte Grenzen der Beispielkonfiguration; kein Aktor ist für den Live-Betrieb freigegeben."""
    return HardLimits.from_config(cfg)


@pytest.fixture
def limits_live(cfg: Config) -> HardLimits:
    """Wie ``limits``, aber ``live_allowed`` ist für Victron, die EVCS, den Tesla und die
    Klimaanlage gesetzt."""
    live = cfg.model_copy(
        update={
            "victron": cfg.victron.model_copy(update={"live_allowed": True}),
            "wallboxes": {
                **cfg.wallboxes,
                "evcs": cfg.wallboxes["evcs"].model_copy(update={"live_allowed": True}),
            },
            "vehicles": {
                **cfg.vehicles,
                "tesla": cfg.vehicles["tesla"].model_copy(update={"live_allowed": True}),
            },
            "thermal": cfg.thermal.model_copy(
                update={
                    "devices": {
                        **cfg.thermal.devices,
                        "klima": cfg.thermal.devices["klima"].model_copy(
                            update={"live_allowed": True}
                        ),
                    }
                }
            ),
        }
    )
    return HardLimits.from_config(live)
