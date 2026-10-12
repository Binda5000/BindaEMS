"""Tests des Einstiegs ``verify_part2``: Ablauf, Bericht, Exitcodes, Rückfragen und Strg+C."""

import asyncio
import json
import signal
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from tests.unit.tools.part2.conftest import FakeEvcs, FakePart

from bindaems.core.adapters.victron_mqtt import MqttMessage
from bindaems.shared.control import CommandKind
from bindaems.shared.domain import SignalKind
from bindaems.shared.timeutil import LOCAL_TZ
from bindaems.tools import verify_part2
from bindaems.tools.part2.session import Part2Session
from bindaems.tools.verify_checks import Finding
from bindaems.tools.verify_part2 import main

EVCS = ["--config", "deploy/config.example.yaml", "--punkt", "evcs"]
P = "c0619ab1234"
VENUS_OS = "platform/0/Firmware/Installed/Version"


def run(tmp_path: Path, *extra: str) -> tuple[int, str]:
    """``main`` für den Punkt EVCS mit ``--yes``: Exitcode und Bericht (leer, falls keiner da)."""
    out = tmp_path / "bericht.md"
    code = main([*EVCS, "--yes", "--out", str(out), *extra])
    return code, out.read_text(encoding="utf-8") if out.exists() else ""


def currents(evcs: FakeEvcs) -> list[float | int | None]:
    return [v for p, v in evcs.writes if p.endswith("SetCurrent")]


async def write_ten_amps(session: Part2Session) -> None:
    assert await session.set_and_confirm("wallbox.evcs", CommandKind.CURRENT, 10.0, 1.0) is not None


def config_with_portal(tmp_path: Path) -> Path:
    raw = yaml.safe_load(Path("deploy/config.example.yaml").read_text(encoding="utf-8"))
    raw["victron"]["mqtt"]["portal_id"] = P
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return config


class Cerbo:
    """Der Cerbo als MQTT-Verbindung: beantwortet Leseanfragen ``R/…`` aus ``values``."""

    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}
        self.published: list[tuple[str, bytes]] = []
        self.answers: asyncio.Queue[MqttMessage] = asyncio.Queue()

    async def __aenter__(self) -> "Cerbo":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def subscribe(self, topic: str) -> None:
        return None

    async def publish(self, topic: str, payload: bytes) -> None:
        self.published.append((topic, payload))
        _, portal, path = topic.split("/", 2)
        if topic.startswith("R/") and path in self.values:
            value = json.dumps({"value": self.values[path]}).encode()
            await self.answers.put(MqttMessage(f"N/{portal}/{path}", value))

    async def messages(self) -> AsyncIterator[MqttMessage]:
        while True:
            yield await self.answers.get()


# Vorgabe des Plans


def test_invalid_config_returns_2(capsys) -> None:
    assert main(["--config", "/gibt/es/nicht.yaml", "--punkt", "evcs"]) == 2


def test_report_is_written_and_values_restored(tmp_path, monkeypatch, fake_part) -> None:
    # fake_part ersetzt build_session und run_evcs; prüft: restore lief, Bericht liegt unter --out
    out = tmp_path / "bericht.md"
    assert (
        main(
            [
                "--config",
                "deploy/config.example.yaml",
                "--punkt",
                "evcs",
                "--yes",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert out.read_text(encoding="utf-8").startswith("# Prüfprotokoll Teil 2 – EVCS – ")
    assert fake_part.restored is True


def test_ctrl_c_restores_and_returns_130(tmp_path, fake_part) -> None:
    fake_part.raise_in_step = KeyboardInterrupt
    out = tmp_path / "bericht.md"
    assert (
        main(
            [
                "--config",
                "deploy/config.example.yaml",
                "--punkt",
                "evcs",
                "--yes",
                "--out",
                str(out),
            ]
        )
        == 130
    )
    assert fake_part.restored is True


# Aufruf


def test_invalid_config_names_the_problem(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", "/gibt/es/nicht.yaml", "--punkt", "evcs"]) == 2
    assert "Konfiguration ungültig" in capsys.readouterr().err


@pytest.mark.parametrize("punkt", ["tesla", "victron"])
def test_parts_that_are_not_there_yet_return_2(
    punkt: str, fake_part: FakePart, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--config", "deploy/config.example.yaml", "--punkt", punkt, "--yes"])
    assert code == 2
    assert capsys.readouterr().err == f"Punkt „{punkt}“ ist noch nicht umgesetzt.\n"
    assert fake_part.sessions == []


@pytest.mark.parametrize(
    ("name", "error"),
    [
        ("nirgends", "Keine EVCS „nirgends“ in wallboxes."),
        ("twc", "Keine EVCS „twc“ in wallboxes."),
    ],
)
def test_unknown_wallbox_returns_2(
    tmp_path: Path, name: str, error: str, fake_part: FakePart, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = run(tmp_path, "--wallbox", name)
    assert code == 2
    assert error in capsys.readouterr().err
    assert fake_part.sessions == []


def test_sleep_check_is_only_for_the_tesla(
    tmp_path: Path, fake_part: FakePart, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = run(tmp_path, "--schlafend")
    assert code == 2
    assert "--schlafend gilt nur für --punkt tesla." in capsys.readouterr().err
    assert fake_part.sessions == []


def test_invalid_secrets_return_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_part: FakePart
) -> None:
    monkeypatch.delenv("BINDAEMS_INTERNAL_TOKEN")
    code, _ = run(tmp_path)
    assert code == 2 and fake_part.sessions == []


# Rückfragen


@pytest.mark.parametrize("answer", ["", "n", "nein", "ja", None])
def test_declined_start_returns_1_without_secrets_or_session(
    answer: str | None,
    monkeypatch: pytest.MonkeyPatch,
    fake_part: FakePart,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def reply(_prompt: str) -> str:
        if answer is None:
            raise EOFError
        return answer

    monkeypatch.setattr("builtins.input", reply)
    monkeypatch.setattr(verify_part2, "load_secrets", Mock(side_effect=AssertionError("Secrets")))
    assert main(EVCS) == 1
    assert fake_part.sessions == []
    shown = capsys.readouterr().out
    assert "EVCS „evcs“" in shown and "Abgebrochen." in shown


def test_confirmed_start_runs_the_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_part: FakePart
) -> None:
    prompts: list[str] = []

    def reply(prompt: str) -> str:
        prompts.append(prompt)
        return "j"

    monkeypatch.setattr("builtins.input", reply)
    out = tmp_path / "bericht.md"
    assert main([*EVCS, "--out", str(out)]) == 0
    assert prompts == ["Fortfahren? [j/N] "] and len(fake_part.sessions) == 1
    assert fake_part.unattended is False  # ohne --yes macht der Betreiber die Handgriffe
    assert "Rückfragen" not in out.read_text(encoding="utf-8")


def test_questions_of_the_steps_need_a_j(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_part: FakePart
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "j")
    assert main([*EVCS, "--out", str(tmp_path / "bericht.md")]) == 0
    ask = fake_part.ask
    assert ask is not None
    prompts: list[str] = []
    for answer, expected in [("j", True), (" j ", True), ("n", False), ("ja", False), ("", False)]:
        monkeypatch.setattr("builtins.input", lambda prompt, a=answer: prompts.append(prompt) or a)
        assert ask("Weiter?") is expected
    assert set(prompts) == {"Weiter? [j/N] "}

    def eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert ask("Weiter?") is False


def test_yes_answers_every_question(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_part: FakePart,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("builtins.input", Mock(side_effect=AssertionError("--yes fragt nicht")))
    code, _ = run(tmp_path)
    assert code == 0
    ask = fake_part.ask
    assert ask is not None and ask("Weiter?") is True
    assert "Weiter? j (--yes)" in capsys.readouterr().out


def test_yes_is_named_in_the_report_and_skips_the_handgrips(
    tmp_path: Path, fake_part: FakePart
) -> None:
    code, report = run(tmp_path)
    assert code == 0
    assert fake_part.unattended is True  # die Sitzung überspringt die Schritte mit Handgriff
    assert "| Rückfragen | mit --yes beantwortet; Schritte mit Handgriff übersprungen |" in report


# Bericht und Exitcodes


def test_report_names_tool_part_device_and_firmware(tmp_path: Path, fake_part: FakePart) -> None:
    async def firmware(session: Part2Session) -> None:
        store = fake_part.sessions[0][1].store
        store.update("wallbox.evcs.firmware", "v2.09", source="victron", kind=SignalKind.STATE)

    fake_part.before = firmware
    code, report = run(tmp_path)
    assert code == 0
    for row in (
        "| Werkzeug | verify_part2 |",
        "| Punkt | evcs |",
        "| Gerät | EVCS „evcs“ |",
        "| EVCS-Firmware | v2.09 |",
        "| 17.2-6 | EVCS bereit | OK | Instanz 40 |",
    ):
        assert row in report
    assert "Venus OS" not in report  # ohne Portal-ID keine Leseanfrage


def test_default_report_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_part: FakePart
) -> None:
    config = Path("deploy/config.example.yaml").resolve()
    monkeypatch.chdir(tmp_path)
    before = datetime.now(LOCAL_TZ).date().isoformat()
    assert main(["--config", str(config), "--punkt", "evcs", "--yes"]) == 0
    after = datetime.now(LOCAL_TZ).date().isoformat()
    (report,) = tmp_path.glob("docs/verification/*-pruefprotokoll-teil2-evcs.md")
    assert report.name in {
        f"{before}-pruefprotokoll-teil2-evcs.md",
        f"{after}-pruefprotokoll-teil2-evcs.md",
    }


def test_failed_step_returns_1_and_the_report_lists_the_restore(
    tmp_path: Path, fake_part: FakePart
) -> None:
    fake_part.before = write_ten_amps
    fake_part.findings = [Finding("17.2-6", "Strom 6 A", "fail", "nicht bestätigt nach 15 s")]
    code, report = run(tmp_path)
    assert code == 1
    assert "| 17.2-6 | Strom 6 A | FEHLER | nicht bestätigt nach 15 s |" in report
    assert "| 17.2-6 | Wiederherstellen: Strom 16 A | OK | Wiederhergestellt nach " in report
    assert fake_part.calls == ["restore", "stop"]
    assert currents(fake_part.sessions[0][1]) == [10, 16]


def test_internal_error_is_reported_after_restoring(tmp_path: Path, fake_part: FakePart) -> None:
    async def broken(session: Part2Session) -> None:
        await write_ten_amps(session)
        raise RuntimeError("kaputt")

    fake_part.before = broken
    code, report = run(tmp_path)
    assert code == 1
    assert "| 17.2-6 | Interner Fehler des Werkzeugs | FEHLER | RuntimeError: kaputt |" in report
    assert "| 17.2-6 | Wiederherstellen: Strom 16 A | OK |" in report
    assert fake_part.calls == ["restore", "stop"]


def test_report_that_cannot_be_written_is_shown(
    tmp_path: Path, fake_part: FakePart, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "datei"
    blocker.write_text("")  # unter einer Datei lässt sich kein Verzeichnis anlegen
    code = main([*EVCS, "--yes", "--out", str(blocker / "bericht.md")])
    shown = capsys.readouterr()
    assert code == 1
    assert "Bericht nicht geschrieben" in shown.err
    assert "# Prüfprotokoll Teil 2 – EVCS – " in shown.out


# Strg+C


def test_ctrl_c_keeps_the_steps_before_and_restores_before_stopping(
    tmp_path: Path, fake_part: FakePart
) -> None:
    async def first_steps(session: Part2Session) -> None:
        session.record(Finding("17.2-6", "EVCS bereit", "ok", "Instanz 40"))
        await write_ten_amps(session)

    fake_part.before = first_steps
    fake_part.raise_in_step = KeyboardInterrupt
    code, report = run(tmp_path)
    assert code == 130
    assert "| 17.2-6 | EVCS bereit | OK | Instanz 40 |" in report
    assert (
        "| 17.2-6 | Abbruch | WARNUNG | mit Strg+C abgebrochen; die übrigen Schritte fehlen |"
        in report
    )
    assert "| 17.2-6 | Wiederherstellen: Strom 16 A | OK |" in report
    assert fake_part.calls == ["restore", "stop"]
    assert currents(fake_part.sessions[0][1]) == [10, 16]


@pytest.mark.skipif(
    signal.getsignal(signal.SIGINT) is not signal.default_int_handler,
    reason="SIGINT ist hier anders belegt; asyncio fängt Strg+C dann nicht",
)
def test_real_ctrl_c_during_a_step_restores_and_returns_130(
    tmp_path: Path, fake_part: FakePart, capsys: pytest.CaptureFixture[str]
) -> None:
    async def interrupted(session: Part2Session) -> None:
        await write_ten_amps(session)
        signal.raise_signal(signal.SIGINT)
        await asyncio.sleep(10)  # hier kommt der Abbruch an

    fake_part.before = interrupted
    code, report = run(tmp_path)
    assert code == 130
    assert currents(fake_part.sessions[0][1]) == [10, 16]
    assert fake_part.calls == ["restore", "stop"]
    assert "| 17.2-6 | Wiederherstellen: Strom 16 A | OK |" in report
    assert "Traceback" not in capsys.readouterr().err


def test_ctrl_c_during_the_restore_still_stops_and_reports(
    tmp_path: Path, fake_part: FakePart
) -> None:
    fake_part.before = write_ten_amps
    fake_part.raise_in_restore = KeyboardInterrupt
    code, report = run(tmp_path)
    assert code == 130
    assert (
        "| 17.2-6 | Wiederherstellen | FEHLER | mit Strg+C abgebrochen; die Werte bitte von Hand "
        "prüfen |" in report
    )
    assert fake_part.calls == ["restore", "stop"]


# Watchdog-Probe und Venus OS


def test_probe_runs_with_its_own_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_part: FakePart
) -> None:
    cerbo = Cerbo()
    connect = Mock(return_value=cerbo)
    monkeypatch.setattr(verify_part2, "AiomqttTransport", connect)
    monkeypatch.setattr(verify_part2, "VERSION_WAIT_S", 0.1)  # Venus OS antwortet hier nicht
    monkeypatch.setenv("BINDAEMS_MQTT_PASSWORD", "mqtt-geheim")
    calls: list[tuple[object, str, float, float]] = []

    async def probe(transport: object, portal: str, timings: object) -> list[Finding]:
        calls.append((transport, portal, timings.heartbeat_s, timings.timeout_s))  # type: ignore[attr-defined]
        return [Finding("17.2-5", "Watchdog erreichbar", "ok", "Version 1.1.0, Zustand wartet")]

    monkeypatch.setattr(verify_part2, "run_probe", probe)
    results: list[list[Finding]] = []

    async def call_probe(session: Part2Session) -> None:
        assert fake_part.probe is not None
        results.append(await fake_part.probe())

    fake_part.before = call_probe
    config = config_with_portal(tmp_path)
    assert (
        main(["--config", str(config), "--punkt", "evcs", "--yes", "--out", str(tmp_path / "b.md")])
        == 0
    )
    assert results == [
        [Finding("17.2-5", "Watchdog erreichbar", "ok", "Version 1.1.0, Zustand wartet")]
    ]
    assert calls == [(cerbo, P, 10, 90)]  # heartbeat_s und timeout_s aus victron.watchdog
    mqtt, password = connect.call_args_list[0].args
    assert mqtt.portal_id == P and password.get_secret_value() == "mqtt-geheim"


@pytest.mark.parametrize(
    ("config", "detail"),
    [
        ("portal", "ConnectionRefusedError: Verbindung abgelehnt"),
        ("example", "Portal-ID konnte nicht ermittelt werden"),
    ],
)
def test_probe_without_connection_is_a_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_part: FakePart, config: str, detail: str
) -> None:
    monkeypatch.setattr(
        verify_part2,
        "AiomqttTransport",
        Mock(side_effect=ConnectionRefusedError("Verbindung abgelehnt")),
    )
    results: list[list[Finding]] = []

    async def call_probe(session: Part2Session) -> None:
        assert fake_part.probe is not None
        results.append(await fake_part.probe())

    fake_part.before = call_probe
    path = (
        config_with_portal(tmp_path) if config == "portal" else Path("deploy/config.example.yaml")
    )
    main(["--config", str(path), "--punkt", "evcs", "--yes", "--out", str(tmp_path / "b.md")])
    assert results == [[Finding("17.2-5", "Verbindung zum Cerbo", "fail", detail)]]


def test_report_names_the_venus_os_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_part: FakePart
) -> None:
    cerbo = Cerbo({VENUS_OS: "v3.80"})
    monkeypatch.setattr(verify_part2, "AiomqttTransport", Mock(return_value=cerbo))
    config = config_with_portal(tmp_path)
    out = tmp_path / "bericht.md"
    assert main(["--config", str(config), "--punkt", "evcs", "--yes", "--out", str(out)]) == 0
    assert "| Venus OS | v3.80 |" in out.read_text(encoding="utf-8")
    assert cerbo.published == [(f"R/{P}/{VENUS_OS}", b"")]  # nur die Leseanfrage


def test_venus_os_stays_unknown_without_an_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_part: FakePart
) -> None:
    monkeypatch.setattr(verify_part2, "AiomqttTransport", Mock(return_value=Cerbo()))
    monkeypatch.setattr(verify_part2, "VERSION_WAIT_S", 0.1)
    config = config_with_portal(tmp_path)
    out = tmp_path / "bericht.md"
    assert main(["--config", str(config), "--punkt", "evcs", "--yes", "--out", str(out)]) == 0
    assert "Venus OS" not in out.read_text(encoding="utf-8")
