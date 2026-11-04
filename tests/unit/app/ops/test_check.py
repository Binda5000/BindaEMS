import copy
from datetime import UTC, datetime
from typing import Any

from bindaems.app.ops.check import (
    DecisionRates,
    OpsCheck,
    check_json,
    operations_check,
)
from bindaems.app.status import ComponentStatus

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
NO_RATES = DecisionRates({})


def _actuator(id_: str, label: str, requested: str, effective: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": id_,
        "label": label,
        "requested": requested,
        "effective": effective,
        "live_allowed": True,
        "reasons": [],
        "locked": [],
        "watchdog_needed": id_ in {"victron", "wallbox.evcs"},
    } | extra


def _health() -> dict[str, Any]:
    """Health wie an der Anlage nach Victron live."""
    return {
        "mode": "CONTROL",
        "version": "1.0",
        "adapters": [{"name": "mqtt", "connected": True}],
        "selfcheck": [{"id": "limits", "status": "ok", "message": "ok"}],
        "alarms": [],
        "mode_reasons": [],
        "intent": {"version": 3, "control": True, "actuators": {}},
        "actuators": [
            _actuator("victron", "Victron", "live", "live"),
            _actuator("wallbox.evcs", "Wallbox", "live", "live"),
            _actuator("vehicle.tesla", "Tesla", "live", "live"),
            _actuator("thermal.klima", "Klima", "dry_run", "dry_run"),
        ],
        "vehicles": [{"id": "tesla", "name": "Tesla", "commands_hour": 3}],
        "wallboxes": [{"id": "wb1", "name": "Garage", "assignment": "detected"}],
        "battery": {"state": "ok"},
        "watchdog": {
            "state": "armed",
            "version": "2",
            "expected_version": "2",
            "required": True,
            "armed": True,
        },
        "inputs": {"ok": True, "problems": []},
        "plan": {"state": "valid"},
    }


def _run(
    health: dict[str, Any] | None = None,
    *,
    connected: bool = True,
    components: list[ComponentStatus] | None = None,
    rates: DecisionRates | None = NO_RATES,
) -> OpsCheck:
    return operations_check(
        connected=connected,
        health=_health() if health is None and connected else health,
        components=components or [],
        rates=rates,
        now=NOW,
    )


def _item(check: OpsCheck, id_: str) -> Any:
    return next(item for item in check.items if item.id == id_)


def test_all_good_is_ok() -> None:
    check = _run()
    assert check.level == "ok"
    assert [i.id for i in check.items][:2] == ["core", "mode"]
    assert _item(check, "actuator.thermal.klima").text == "Dry-Run (so gewünscht)"
    assert _item(check, "actuator.victron").text == "live"
    assert all(i.level == "ok" for i in check.items)


def test_core_down_is_one_error_rest_unknown() -> None:
    check = _run(None, connected=False)
    assert check.level == "error"
    assert _item(check, "core").level == "error"
    others = [i for i in check.items if i.id not in {"core", "app"}]
    assert others
    assert all(
        i.level == "unknown" and i.text == "unbekannt, core nicht erreichbar" for i in others
    )
    assert _item(check, "app").level == "ok"


def test_core_down_still_evaluates_components() -> None:
    bad = ComponentStatus("Preise", ok=False, message="kaputt")
    assert _item(_run(None, connected=False, components=[bad]), "app").level == "warn"


def test_connected_without_health_is_unknown() -> None:
    check = operations_check(connected=True, health=None, components=[], rates=None, now=NOW)
    assert check.level == "unknown"
    assert _item(check, "core").level == "ok"


def test_missing_fields_are_unknown() -> None:
    health = {"mode": "CONTROL"}
    check = _run(health)
    assert check.level == "ok"
    for id_ in ("watchdog", "alarms", "plan", "battery", "inputs", "adapters", "selfcheck"):
        assert _item(check, id_).level == "unknown"
        assert _item(check, id_).text == "vom core nicht gemeldet"


def test_live_requested_but_dry_run_is_error_with_reason() -> None:
    health = _health()
    health["actuators"][1] = _actuator(
        "wallbox.evcs",
        "Wallbox",
        "live",
        "dry_run",
        reasons=["Watchdog am Cerbo ist nicht scharf."],
    )
    check = _run(health)
    item = _item(check, "actuator.wallbox.evcs")
    assert item.level == "error"
    assert "Watchdog am Cerbo ist nicht scharf." in item.text
    assert check.level == "error"


def test_locked_actuator_is_error() -> None:
    health = _health()
    health["actuators"][0] = _actuator("victron", "Victron", "live", "dry_run", locked=["fremd"])
    item = _item(_run(health), "actuator.victron")
    assert item.level == "error"
    assert "fremd" in item.text


def test_watchdog_needed_but_not_armed_is_error() -> None:
    health = _health()
    health["watchdog"].update(armed=False, state="waiting")
    assert _item(_run(health), "watchdog").level == "error"


def test_watchdog_tripped_is_error() -> None:
    health = _health()
    health["watchdog"]["state"] = "tripped"
    assert _item(_run(health), "watchdog").level == "error"


def test_watchdog_version_mismatch_is_warn() -> None:
    health = _health()
    health["watchdog"]["version"] = "1"
    assert _item(_run(health), "watchdog").level == "warn"


def test_watchdog_not_needed_is_ok() -> None:
    health = _health()
    health["watchdog"].update(required=False, armed=False, state="disabled")
    item = _item(_run(health), "watchdog")
    assert (item.level, item.text) == ("ok", "nicht nötig")


def test_observe_without_control_is_warn() -> None:
    health = _health()
    health["mode"] = "OBSERVE"
    item = _item(_run(health), "mode")
    assert item.level == "warn"
    assert "Regelbetrieb ist aus" in item.text


def test_degraded_warn_safe_error_start_unknown() -> None:
    health = _health()
    health["mode_reasons"] = ["Eingang ausgefallen"]
    for mode, level in (("DEGRADED", "warn"), ("SAFE", "error"), ("START", "unknown")):
        health["mode"] = mode
        item = _item(_run(health), "mode")
        assert item.level == level
    health["mode"] = "SAFE"
    assert "Eingang ausgefallen" in _item(_run(health), "mode").text


def test_error_alarm_beats_warning() -> None:
    health = _health()
    health["alarms"] = [
        {"id": "a", "severity": "warning", "message": "leise"},
        {"id": "b", "severity": "error", "message": "laut"},
        {"id": "c", "severity": "info", "message": "egal"},
    ]
    item = _item(_run(health), "alarms")
    assert item.level == "error"
    assert "laut" in item.text


def test_warning_alarm_is_warn_and_info_is_ok() -> None:
    health = _health()
    health["alarms"] = [{"id": "a", "severity": "warning", "message": "leise"}]
    assert _item(_run(health), "alarms").level == "warn"
    health["alarms"] = [{"id": "c", "severity": "info", "message": "egal"}]
    assert _item(_run(health), "alarms").level == "ok"


def test_unclear_assignment_is_warn_with_link() -> None:
    health = _health()
    health["wallboxes"][0]["assignment"] = "unclear"
    item = _item(_run(health), "assignment")
    assert item.level == "warn"
    assert item.link == "/laden"
    assert "wb1" in item.text


def test_tesla_commands_over_16_is_warn() -> None:
    health = _health()
    health["vehicles"][0]["commands_hour"] = 16
    assert _item(_run(health), "tesla_commands").level == "ok"
    health["vehicles"][0]["commands_hour"] = 17
    item = _item(_run(health), "tesla_commands")
    assert item.level == "warn"
    assert "Reserve greift" in item.text


def test_evcs_rate_over_20_is_warn() -> None:
    assert _item(_run(rates=DecisionRates({"wallbox.evcs": 20})), "evcs_rate").level == "ok"
    item = _item(_run(rates=DecisionRates({"wallbox.evcs": 21})), "evcs_rate")
    assert item.level == "warn"
    assert item.link == "/entscheidungen?range=hour&actuator=wallbox.evcs"
    assert "Hysterese prüfen" in item.text


def test_victron_rate_over_60_is_warn() -> None:
    assert _item(_run(rates=DecisionRates({"victron": 60})), "victron_rate").level == "ok"
    item = _item(_run(rates=DecisionRates({"victron": 61})), "victron_rate")
    assert item.level == "warn"
    assert item.link == "/entscheidungen?range=hour&actuator=victron"
    assert "Totband prüfen" in item.text


def test_rates_unknown_without_store() -> None:
    check = _run(rates=None)
    assert _item(check, "evcs_rate").level == "unknown"
    assert _item(check, "victron_rate").level == "unknown"
    assert check.level == "ok"


def test_expired_plan_is_warn() -> None:
    health = _health()
    for state in ("expired", "none"):
        health["plan"] = {"state": state}
        assert _item(_run(health), "plan").level == "warn"


def test_battery_states() -> None:
    health = _health()
    for state, level in (("idle", "ok"), ("unknown", "warn"), ("locked", "error")):
        health["battery"] = {"state": state}
        assert _item(_run(health), "battery").level == level


def test_inputs_adapters_selfcheck_problems() -> None:
    health = _health()
    health["inputs"] = {"ok": False, "problems": ["a", "b", "c"]}
    health["adapters"] = [{"name": "mqtt", "connected": False}]
    health["selfcheck"] = [{"id": "x", "status": "warn", "message": "m"}]
    check = _run(health)
    assert _item(check, "inputs").text == "a; b"
    assert "mqtt" in _item(check, "adapters").text
    assert _item(check, "selfcheck").level == "warn"
    health["selfcheck"].append({"id": "y", "status": "fail", "message": "n"})
    assert _item(_run(health), "selfcheck").level == "error"


def test_component_not_ok_is_warn() -> None:
    bad = ComponentStatus("Preise", ok=False, message="kaputt")
    good = ComponentStatus("Verlauf", ok=True, message="ok")
    check = _run(components=[good, bad])
    item = _item(check, "app")
    assert item.level == "warn"
    assert "Preise" in item.text
    assert "Verlauf" not in item.text
    assert check.level == "warn"


def test_overall_level_is_worst() -> None:
    health = _health()
    health["plan"] = {"state": "none"}
    assert _run(health).level == "warn"
    health["battery"] = {"state": "locked"}
    assert _run(health).level == "error"


def test_check_json_shape() -> None:
    health = copy.deepcopy(_health())
    data = check_json(_run(health))
    assert data["level"] == "ok"
    assert data["checked_at"] == NOW.isoformat()
    assert data["items"][0] == {
        "id": "core",
        "label": "core",
        "level": "ok",
        "text": "core verbunden",
        "link": None,
    }
