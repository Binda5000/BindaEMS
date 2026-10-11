from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from bindaems_watchdog.config import parse_settings

from bindaems.shared.config import Config, ConfigError, WatchdogConfig
from bindaems.tools.watchdog_config import main, render_config

EXAMPLE = Path("cerbo-watchdog/config.ini.example")


def test_example_matches_rendered_example(cfg: Config) -> None:
    assert render_config(cfg) == EXAMPLE.read_text(encoding="utf-8")


def test_rendered_config_parses(cfg: Config) -> None:
    settings = parse_settings(render_config(cfg))
    assert [a.name for a in settings.actions] == ["evcs_evcs", "min_soc", "max_discharge"]
    assert settings.actions[0].instance == 40


def test_evcs_without_gx_instance_gets_no_action(cfg: Config) -> None:
    instances = cfg.victron.instances.model_copy(update={"evcharger": {}})
    text = render_config(
        cfg.model_copy(update={"victron": cfg.victron.model_copy(update={"instances": instances})})
    )
    assert "[action:evcs_evcs]" not in text
    assert "; EVCS „evcs“: keine evcharger-Instanz in victron.instances.evcharger" in text


def test_values_come_from_the_configuration(cfg: Config) -> None:
    victron = cfg.victron.model_copy(
        update={"watchdog": WatchdogConfig(heartbeat_s=7, timeout_s=75.5)}
    )
    battery = cfg.battery.model_copy(update={"reserve_soc_pct": 12.5})
    evcs = cfg.wallboxes["evcs"].model_copy(update={"safe_a": 8})
    changed = cfg.model_copy(
        update={
            "victron": victron,
            "battery": battery,
            "wallboxes": {**cfg.wallboxes, "evcs": evcs},
        }
    )
    settings = parse_settings(render_config(changed))
    assert (settings.timeout_s, settings.gap_s) == (75.5, 17.5)  # Lücke = 2,5 × Heartbeat
    values = {action.name: action.value for action in settings.actions}
    assert values == {"evcs_evcs": 8, "min_soc": 12.5, "max_discharge": -1}


def test_each_evcs_gets_its_own_action_by_name(cfg: Config) -> None:
    second = cfg.wallboxes["evcs"].model_copy(update={"safe_a": 10})
    instances = cfg.victron.instances.model_copy(update={"evcharger": {41: "evcs2", 40: "evcs"}})
    changed = cfg.model_copy(
        update={
            "victron": cfg.victron.model_copy(update={"instances": instances}),
            "wallboxes": {**cfg.wallboxes, "evcs2": second},
        }
    )
    actions = {a.name: a for a in parse_settings(render_config(changed)).actions}
    assert [(a.instance, a.value) for a in actions.values() if a.name.startswith("evcs_")] == [
        (40, 6),
        (41, 10),
    ]
    assert actions["evcs_evcs2"].instance == 41
    assert "evcs_twc" not in actions  # die Tesla-Wallbox hat keinen Strom für den Watchdog


def test_main_writes_file(cfg: Config, tmp_path: Path) -> None:
    out = tmp_path / "config.ini"
    assert main(["--config", "deploy/config.example.yaml", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == render_config(cfg)


def test_main_prints_to_stdout_without_out(cfg: Config, capsys) -> None:
    assert main(["--config", "deploy/config.example.yaml"]) == 0
    captured = capsys.readouterr()
    assert captured.out == render_config(cfg)
    assert captured.err == ""  # nichts zu warnen


def test_main_rejects_invalid_config(tmp_path: Path, capsys) -> None:
    broken = tmp_path / "config.yaml"
    broken.write_text("grid: 1\n")
    assert main(["--config", str(broken)]) == 2
    assert "Konfiguration ungültig" in capsys.readouterr().err


def _config_file(tmp_path: Path, change: Callable[[dict[str, Any]], object]) -> Path:
    raw = yaml.safe_load(Path("deploy/config.example.yaml").read_text(encoding="utf-8"))
    change(raw)
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return config


def _with_watchdog(cfg: Config, **values: float) -> Config:
    victron = cfg.victron.model_copy(update={"watchdog": WatchdogConfig(**values)})
    return cfg.model_copy(update={"victron": victron})


@pytest.mark.parametrize(
    ("heartbeat_s", "timeout_s"),
    [
        (40, 90),  # Lücke 100 s
        (36, 90),  # Lücke genau 90 s: der Watchdog verlangt „kleiner“
        (36, 90.0000004),  # in der Datei stehen beide als 90
    ],
)
def test_render_config_refuses_a_gap_the_watchdog_would_reject(
    cfg: Config, heartbeat_s: float, timeout_s: float
) -> None:
    with pytest.raises(ConfigError) as caught:
        render_config(_with_watchdog(cfg, heartbeat_s=heartbeat_s, timeout_s=timeout_s))
    message = str(caught.value)
    assert message.startswith("Konfiguration ungültig: victron.watchdog: ")
    assert "2,5 × heartbeat_s" in message and "kleiner sein als timeout_s" in message


def test_refusal_names_both_values(cfg: Config) -> None:
    with pytest.raises(ConfigError) as caught:
        render_config(_with_watchdog(cfg, heartbeat_s=40, timeout_s=90))
    assert "(100 s)" in str(caught.value) and "(90 s)" in str(caught.value)


def test_render_config_accepts_a_gap_just_below_the_timeout(cfg: Config) -> None:
    settings = parse_settings(render_config(_with_watchdog(cfg, heartbeat_s=35.9, timeout_s=90)))
    assert (settings.gap_s, settings.timeout_s) == (89.75, 90)  # die Datei lädt der Watchdog


def test_main_rejects_a_gap_the_watchdog_would_reject(tmp_path: Path, capsys) -> None:
    config = _config_file(
        tmp_path, lambda raw: raw["victron"].update(watchdog={"heartbeat_s": 40, "timeout_s": 90})
    )
    out = tmp_path / "config.ini"
    assert main(["--config", str(config), "--out", str(out)]) == 2
    captured = capsys.readouterr()
    assert "heartbeat_s" in captured.err and "timeout_s" in captured.err
    assert captured.out == ""
    assert not out.exists()  # keine Datei, die der Watchdog nicht laden würde


def test_main_warns_for_every_evcs_without_gx_instance(tmp_path: Path, capsys) -> None:
    def without_instances(raw: dict[str, Any]) -> None:
        raw["victron"]["instances"]["evcharger"] = {}
        raw["wallboxes"]["evcs2"] = dict(raw["wallboxes"]["evcs"])

    config = _config_file(tmp_path, without_instances)
    assert main(["--config", str(config)]) == 0
    captured = capsys.readouterr()
    reason = "keine evcharger-Instanz in victron.instances.evcharger – der Watchdog setzt keinen"
    assert captured.err.splitlines() == [
        f"Warnung: EVCS „evcs“: {reason} sicheren Strom",
        f"Warnung: EVCS „evcs2“: {reason} sicheren Strom",
    ]
    # der Kommentar in der Datei bleibt
    assert f"; EVCS „evcs“: {reason} sicheren Strom" in captured.out
    assert f"; EVCS „evcs2“: {reason} sicheren Strom" in captured.out


def test_main_reports_an_unwritable_out(tmp_path: Path, capsys) -> None:
    out = tmp_path / "gibt-es-nicht" / "config.ini"
    assert main(["--config", "deploy/config.example.yaml", "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert "gibt-es-nicht" in err  # die Meldung nennt den Pfad
    assert "Traceback" not in err
