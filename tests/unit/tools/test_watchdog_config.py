from pathlib import Path

from bindaems_watchdog.config import parse_settings

from bindaems.shared.config import Config, WatchdogConfig
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
    assert capsys.readouterr().out == render_config(cfg)


def test_main_rejects_invalid_config(tmp_path: Path, capsys) -> None:
    broken = tmp_path / "config.yaml"
    broken.write_text("grid: 1\n")
    assert main(["--config", str(broken)]) == 2
    assert "Konfiguration ungültig" in capsys.readouterr().err
