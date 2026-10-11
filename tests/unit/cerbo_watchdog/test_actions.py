import logging
from collections.abc import Iterable

import pytest
from bindaems_watchdog.actions import ActionRunner
from bindaems_watchdog.config import Action
from tests.unit.cerbo_watchdog.fakes import FakeAccess

SETTINGS_PATH = ("com.victronenergy.settings", "/Settings/CGwacs/BatteryLife/MinimumSocLimit")
MIN_SOC = Action("min_soc", *SETTINGS_PATH, 20)


def run(runner: ActionRunner, times: Iterable[float]) -> None:
    runner.start(0.0)
    for t in times:
        runner.tick(t)


def test_skips_write_when_value_already_safe() -> None:
    access = FakeAccess({SETTINGS_PATH: 20})
    runner = ActionRunner(access, [MIN_SOC], retries=6, retry_s=5.0)
    run(runner, [0.0])
    assert (access.writes, runner.results, runner.busy) == ([], {"min_soc": "unverändert"}, False)


def test_writes_and_verifies() -> None:
    access = FakeAccess({SETTINGS_PATH: 30})
    runner = ActionRunner(access, [MIN_SOC], retries=6, retry_s=5.0)
    run(runner, [0.0])
    assert (access.writes, runner.results) == ([(*SETTINGS_PATH, 20)], {"min_soc": "ok"})


def test_retries_until_success() -> None:
    access = FakeAccess({SETTINGS_PATH: 30}, failing_sets=2)
    runner = ActionRunner(access, [MIN_SOC], retries=6, retry_s=5.0)
    run(runner, [0.0, 4.0, 5.0, 10.0])
    assert (len(access.writes), runner.results, runner.failures) == (3, {"min_soc": "ok"}, 0)


def test_counts_failures_after_retries() -> None:
    access = FakeAccess({SETTINGS_PATH: 30}, failing_sets=100)
    runner = ActionRunner(access, [MIN_SOC], retries=2, retry_s=5.0)
    run(runner, [0.0, 5.0, 10.0])
    assert (runner.results, runner.failures, runner.busy) == (
        {"min_soc": "fehlgeschlagen"},
        1,
        False,
    )


def test_resolves_evcharger_by_instance() -> None:
    name = "com.victronenergy.evcharger.tcp_192_168_81_41"
    access = FakeAccess(
        {(name, "/SetCurrent"): 16}, services={("com.victronenergy.evcharger", 40): name}
    )
    action = Action("evcs_evcs", "com.victronenergy.evcharger", "/SetCurrent", 6, instance=40)
    run(ActionRunner(access, [action], retries=0, retry_s=5.0), [0.0])
    assert access.writes == [(name, "/SetCurrent", 6)]


def test_missing_service_counts_as_failure() -> None:
    action = Action("evcs_evcs", "com.victronenergy.evcharger", "/SetCurrent", 6, instance=40)
    runner = ActionRunner(FakeAccess({}), [action], retries=0, retry_s=5.0)
    run(runner, [0.0])
    assert (runner.results, runner.failures) == ({"evcs_evcs": "fehlgeschlagen"}, 1)


def test_null_value_compares_with_invalid() -> None:
    access = FakeAccess({("com.victronenergy.hub4", "/Overrides/Setpoint"): []})
    action = Action("setpoint", "com.victronenergy.hub4", "/Overrides/Setpoint", None)
    runner = ActionRunner(access, [action], retries=0, retry_s=5.0)
    run(runner, [0.0])
    assert runner.results == {"setpoint": "unverändert"}


# Weitere Randfälle

EVCS = Action("evcs_evcs", "com.victronenergy.evcharger", "/SetCurrent", 6, instance=40)


class SilentAccess(FakeAccess):
    """Der Dienst antwortet nicht: Jedes Lesen wirft."""

    def get_value(self, service: str, path: str) -> object:
        raise RuntimeError("org.freedesktop.DBus.Error.NoReply")


class ClampingAccess(FakeAccess):
    """Der Dienst nimmt jeden Wert an, hält aber 25 fest."""

    def set_value(self, service: str, path: str, value: object) -> bool:
        self.writes.append((service, path, value))
        self.values[(service, path)] = 25
        return True


def test_exception_is_a_failed_attempt(caplog: pytest.LogCaptureFixture) -> None:
    runner = ActionRunner(SilentAccess({}), [MIN_SOC], retries=1, retry_s=5.0)
    run(runner, [0.0])
    assert (runner.results, runner.busy) == ({"min_soc": "offen"}, True)
    runner.tick(5.0)
    assert (runner.results, runner.failures, runner.busy) == (
        {"min_soc": "fehlgeschlagen"},
        1,
        False,
    )
    assert "Aktion min_soc: Versuch gescheitert: org.freedesktop.DBus.Error.NoReply" in caplog.text


def test_read_back_must_show_the_safe_value() -> None:
    access = ClampingAccess({SETTINGS_PATH: 30})
    runner = ActionRunner(access, [MIN_SOC], retries=0, retry_s=5.0)
    run(runner, [0.0])
    assert (access.writes, runner.results) == (
        [(*SETTINGS_PATH, 20)],
        {"min_soc": "fehlgeschlagen"},
    )


@pytest.mark.parametrize(
    ("current", "safe", "tolerance", "unchanged"),
    [
        (20.5, 20, 0.5, True),  # Zahlen bis zur Toleranz gleich, die Grenze eingeschlossen
        (19.5, 20, 0.5, True),
        (20.6, 20, 0.5, False),
        (20, 20.0, 0.0, True),
        (20.1, 20, 0.0, False),
        (None, None, 0.5, True),  # ungültig: None oder leeres Array
        ([], None, 0.5, True),
        (0, None, 0.5, False),
        ([1], None, 0.5, False),
        (None, 20, 0.5, False),
        (1.4, True, 0.5, False),  # bool ist keine Zahl, also keine Toleranz
        (True, True, 0.5, True),
        ("an", "an", 0.5, True),
        ("an", "aus", 0.5, False),
    ],
)
def test_comparison_with_the_safe_value(
    current: object, safe: object, tolerance: float, unchanged: bool
) -> None:
    access = FakeAccess({SETTINGS_PATH: current})
    action = Action("x", *SETTINGS_PATH, safe, tolerance=tolerance)
    runner = ActionRunner(access, [action], retries=0, retry_s=5.0)
    run(runner, [0.0])
    assert runner.results == {"x": "unverändert" if unchanged else "ok"}
    assert access.writes == ([] if unchanged else [(*SETTINGS_PATH, safe)])


def test_every_attempt_reads_before_writing() -> None:
    access = FakeAccess({SETTINGS_PATH: 30}, failing_sets=1)
    runner = ActionRunner(access, [MIN_SOC], retries=6, retry_s=5.0)
    run(runner, [0.0])
    access.values[SETTINGS_PATH] = 20  # inzwischen steht der Sicherwert schon
    runner.tick(5.0)
    assert (len(access.writes), runner.results) == (1, {"min_soc": "unverändert"})


def test_actions_are_independent() -> None:
    access = FakeAccess({SETTINGS_PATH: 30})
    runner = ActionRunner(access, [EVCS, MIN_SOC], retries=0, retry_s=5.0)
    run(runner, [0.0])
    assert runner.results == {"evcs_evcs": "fehlgeschlagen", "min_soc": "ok"}
    assert (runner.failures, runner.busy) == (1, False)


def test_start_begins_a_new_run() -> None:
    access = FakeAccess({SETTINGS_PATH: 30}, failing_sets=1)
    runner = ActionRunner(access, [MIN_SOC], retries=0, retry_s=5.0)
    run(runner, [0.0])
    assert runner.failures == 1
    runner.start(100.0)
    assert (runner.results, runner.failures, runner.busy) == ({"min_soc": "offen"}, 0, True)
    runner.tick(100.0)
    assert (runner.results, runner.failures) == ({"min_soc": "ok"}, 0)


def test_nothing_happens_before_start() -> None:
    access = FakeAccess({SETTINGS_PATH: 30})
    runner = ActionRunner(access, [MIN_SOC], retries=0, retry_s=5.0)
    runner.tick(0.0)
    assert (access.writes, runner.results, runner.busy, runner.failures) == ([], {}, False, 0)


def test_log_names_old_and_new_value(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    access = FakeAccess({SETTINGS_PATH: 30.0})
    run(ActionRunner(access, [MIN_SOC], retries=0, retry_s=5.0), [0.0])
    assert (
        "Aktion min_soc: com.victronenergy.settings /Settings/CGwacs/BatteryLife/MinimumSocLimit"
        " von 30.0 auf 20 gesetzt." in caplog.text
    )


def test_waits_retry_s_between_attempts() -> None:
    access = FakeAccess({SETTINGS_PATH: 30}, failing_sets=100)
    runner = ActionRunner(access, [MIN_SOC], retries=6, retry_s=5.0)
    run(runner, [0.0, 1.0, 4.9])
    assert len(access.writes) == 1
    runner.tick(5.0)
    assert len(access.writes) == 2


class RejectingAccess(FakeAccess):
    """Der Dienst übernimmt den Wert, meldet das Schreiben aber als abgelehnt."""

    def set_value(self, service: str, path: str, value: object) -> bool:
        super().set_value(service, path, value)
        return False


def test_rejected_write_is_a_failed_attempt() -> None:
    access = RejectingAccess({SETTINGS_PATH: 30})
    runner = ActionRunner(access, [MIN_SOC], retries=1, retry_s=5.0)
    run(runner, [0.0])
    assert runner.results == {"min_soc": "offen"}
    runner.tick(5.0)  # der zweite Versuch liest zuerst und findet den Sicherwert
    assert (len(access.writes), runner.results) == (1, {"min_soc": "unverändert"})
