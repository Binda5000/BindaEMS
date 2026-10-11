import os
import re
import subprocess
import sys
from pathlib import Path

import bindaems_watchdog
import pytest
from bindaems_watchdog.config import (
    Action,
    ConfigError,
    Settings,
    load_settings,
    parse_settings,
)

EXAMPLE = Path("cerbo-watchdog/config.ini.example")
PACKAGE_ROOT = Path(bindaems_watchdog.__file__).parent.parent


def test_example_parses() -> None:
    settings = load_settings(str(EXAMPLE))
    assert (
        settings.enabled,
        settings.timeout_s,
        settings.rearm_s,
        settings.gap_s,
        settings.retries,
        settings.retry_s,
    ) == (True, 90.0, 30.0, 25.0, 6, 5.0)
    assert [a.name for a in settings.actions] == ["evcs_evcs", "min_soc", "max_discharge"]
    assert settings.actions[0] == Action(
        "evcs_evcs", "com.victronenergy.evcharger", "/SetCurrent", 6, instance=40
    )


def test_defaults_for_missing_keys() -> None:
    settings = parse_settings("[watchdog]\n")
    assert (settings.timeout_s, settings.state_file, settings.actions) == (
        90.0,
        "/data/bindaems-watchdog/state.json",
        (),
    )


def test_null_value_means_invalid() -> None:
    text = "[watchdog]\n[action:x]\nservice = a\npath = /b\nvalue = null\n"
    assert parse_settings(text).actions[0].value is None


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[sonst]\n", "config.ini: Abschnitt [watchdog] fehlt."),
        (
            "[watchdog]\n[action:x]\nservice = a\n",
            "config.ini: [action:x] braucht service, path und value.",
        ),
        (
            "[watchdog]\n[action:x]\nservice = a\npath = /b\nvalue = sechs\n",
            "config.ini: [action:x] value ist kein JSON-Wert: sechs",
        ),
        ("[watchdog]\ntimeout_s = 0\n", "config.ini: timeout_s muss eine positive Zahl sein."),
        (
            "[watchdog]\nheartbeat_gap_s = 90\n",
            "config.ini: heartbeat_gap_s muss kleiner als timeout_s sein.",
        ),
    ],
)
def test_invalid_config(text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=re.escape(message)):
        parse_settings(text)


# Weitere Randfälle

ACTION = "[watchdog]\n[action:x]\nservice = a\npath = /b\nvalue = 1\n"
INCOMPLETE = "config.ini: [action:x] braucht service, path und value."
TIMES = ["timeout_s", "rearm_s", "heartbeat_gap_s", "retry_s"]


def action_text(**fields: str) -> str:
    lines = [f"{key} = {value}" for key, value in fields.items()]
    return "[watchdog]\n[action:x]\n" + "\n".join(lines) + "\n"


def test_all_keys_are_read() -> None:
    text = (
        "[watchdog]\nenabled = false\ntimeout_s = 120\nrearm_s = 45.5\nheartbeat_gap_s = 20\n"
        "retries = 0\nretry_s = 7\nstate_file = /data/anders/state.json\n"
    )
    assert parse_settings(text) == Settings(
        enabled=False,
        timeout_s=120.0,
        rearm_s=45.5,
        gap_s=20.0,
        retries=0,
        retry_s=7.0,
        state_file="/data/anders/state.json",
        actions=(),
    )


def test_defaults_are_the_documented_ones() -> None:
    assert parse_settings("[watchdog]\n") == Settings(
        enabled=True,
        timeout_s=90.0,
        rearm_s=30.0,
        gap_s=25.0,
        retries=6,
        retry_s=5.0,
        state_file="/data/bindaems-watchdog/state.json",
        actions=(),
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("6", 6),
        ("0", 0),
        ("0.0", 0.0),
        ("-1", -1),
        ("null", None),
        ("2.5", 2.5),
        ("true", True),
        ('"x"', "x"),
        ("[]", []),
        ("[1, 2]", [1, 2]),
    ],
)
def test_json_values(raw: str, expected: object) -> None:
    value = parse_settings(action_text(service="a", path="/b", value=raw)).actions[0].value
    assert value == expected and type(value) is type(expected)


def test_optional_fields() -> None:
    text = action_text(service="a", path="/b", value="1", instance="7", tolerance="0.25")
    action = parse_settings(text).actions[0]
    assert (action.instance, action.tolerance) == (7, 0.25)
    action = parse_settings(ACTION).actions[0]
    assert (action.instance, action.tolerance) == (None, 0.5)


def test_zero_tolerance_means_exact_comparison() -> None:
    text = action_text(service="a", path="/b", value="1", tolerance="0")
    assert parse_settings(text).actions[0].tolerance == 0.0


def test_other_sections_are_ignored() -> None:
    settings = parse_settings("[logging]\nlevel = debug\n" + ACTION + "[sonst]\nx = 1\n")
    assert [a.name for a in settings.actions] == ["x"]


def test_percent_signs_are_not_interpolated() -> None:
    settings = parse_settings("[watchdog]\nstate_file = /data/100%/state.json\n")
    assert settings.state_file == "/data/100%/state.json"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (action_text(service="a", path="/b"), INCOMPLETE),
        (action_text(service="a", value="1"), INCOMPLETE),
        (action_text(path="/b", value="1"), INCOMPLETE),
        (action_text(service="", path="/b", value="1"), INCOMPLETE),
        (action_text(service="a", path="", value="1"), INCOMPLETE),
        (action_text(service="a", path="/b", value=""), INCOMPLETE),
        # NaN und Infinity kennt Pythons json, aber nicht JSON.
        (
            action_text(service="a", path="/b", value="NaN"),
            "config.ini: [action:x] value ist kein JSON-Wert: NaN",
        ),
        (
            action_text(service="a", path="/b", value="-Infinity"),
            "config.ini: [action:x] value ist kein JSON-Wert: -Infinity",
        ),
        (
            action_text(service="a", path="/b", value="1", instance="vier"),
            "config.ini: [action:x] instance muss eine ganze Zahl sein.",
        ),
        (
            action_text(service="a", path="/b", value="1", instance="4.5"),
            "config.ini: [action:x] instance muss eine ganze Zahl sein.",
        ),
        (
            action_text(service="a", path="/b", value="1", instance=""),
            "config.ini: [action:x] instance muss eine ganze Zahl sein.",
        ),
        *[
            (
                action_text(service="a", path="/b", value="1", tolerance=bad),
                "config.ini: [action:x] tolerance muss eine Zahl ≥ 0 sein.",
            )
            for bad in ("-1", "viel", "nan", "inf", "")
        ],
        ("[watchdog]\nenabled = vielleicht\n", "config.ini: enabled muss true oder false sein."),
        *[
            (f"[watchdog]\nretries = {bad}\n", "config.ini: retries muss eine ganze Zahl ≥ 0 sein.")
            for bad in ("-1", "2.5", "viele", "")
        ],
        *[
            (f"[watchdog]\n{key} = {bad}\n", f"config.ini: {key} muss eine positive Zahl sein.")
            for key in TIMES
            for bad in ("0", "-5", "abc", "nan", "inf", "")
        ],
        ("[watchdog]\nstate_file =\n", "config.ini: state_file darf nicht leer sein."),
        ("kein Abschnitt\n", "config.ini: Aufbau ungültig:"),
        ("[watchdog]\n[watchdog]\n", "config.ini: Aufbau ungültig:"),
        ("[watchdog]\nzeile ohne Gleichheitszeichen\n", "config.ini: Aufbau ungültig:"),
        (ACTION + ACTION, "config.ini: Aufbau ungültig:"),
    ],
)
def test_more_invalid_configs(text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=re.escape(message)):
        parse_settings(text)


def test_missing_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=re.escape("config.ini: Datei lässt sich nicht lesen:")):
        load_settings(str(tmp_path / "fehlt.ini"))


def test_undecodable_file_is_a_config_error(tmp_path: Path) -> None:
    path = tmp_path / "config.ini"
    path.write_bytes(b"[watchdog]\n\xff\xfe\n")
    with pytest.raises(ConfigError, match=re.escape("config.ini: Datei lässt sich nicht lesen:")):
        load_settings(str(path))


def test_file_is_read_as_utf8_whatever_the_locale() -> None:
    # Unter daemontools fehlt LANG: Python liest Dateien dann ohne ausdrückliche Kodierung als
    # ASCII, und jede erzeugte config.ini beginnt mit Umlauten und Gedankenstrichen im Kommentar.
    code = (
        "import sys; from bindaems_watchdog.config import load_settings;"
        "print(len(load_settings(sys.argv[1]).actions))"
    )
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(PACKAGE_ROOT),
        "LC_ALL": "C",
        "PYTHONCOERCECLOCALE": "0",
        "PYTHONUTF8": "0",
    }
    result = subprocess.run(  # noqa: S603 - fester Aufruf des eigenen Interpreters
        [sys.executable, "-c", code, str(EXAMPLE)], env=env, capture_output=True, text=True
    )
    assert (result.returncode, result.stdout.strip()) == (0, "3"), result.stderr
