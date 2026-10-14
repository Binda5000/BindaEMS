import importlib.util
import json
import re
from pathlib import Path

import pytest


def test_acceptance_checklist_covers_spec_18() -> None:
    text = Path("docs/verification/abnahme-phase-1.md").read_text(encoding="utf-8")
    for criterion in (
        "7 Tage lückenlose Aufzeichnung",
        "±2 %",
        "VRM",
        "Preise und Prognose sichtbar",
        "keine Schreibpfade",
    ):
        assert criterion in text


def test_operations_guide_describes_the_web_ui() -> None:
    text = Path("docs/betrieb.md").read_text(encoding="utf-8")
    for topic in ("## 12. Web-UI", "App installieren", "Sitzung", "Bestätigungscode"):
        assert topic in text


def test_operations_guide_describes_charging() -> None:
    text = Path("docs/betrieb.md").read_text(encoding="utf-8")
    for topic in ("## Laderegelung", "## Simulator", '"vehicles"', "-m bindaems.tools.sim"):
        assert topic in text


def _guide_section(heading: str) -> str:
    """Der Abschnitt ``## heading`` des Betriebshandbuchs bis zum nächsten ``## ``."""
    text = Path("docs/betrieb.md").read_text(encoding="utf-8")
    return text.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]


def test_json_examples_of_the_charging_section_are_valid() -> None:
    # Die Beispiele sind eine Absicht für PUT /v1/intent und ein Eintrag von GET /v1/decisions: Die
    # Modelle des core müssen sie lesen können, sonst führt das Handbuch in die Irre.
    from bindaems.shared.control import Intent
    from bindaems.shared.decisions import decision_from_json

    examples = re.findall(r"```json\n(.*?)\n```", _guide_section("Laderegelung"), re.DOTALL)
    data = [json.loads(example) for example in examples]
    assert [("version" in item) for item in data].count(True) >= 2
    for item in data:
        if "version" in item:
            Intent.model_validate(item)
        else:
            decision_from_json(item)


# die Gründe der Laderegelung (Plan 2c-1, „Texte“)
CHARGING_REASONS = {
    "mode_pv",
    "mode_min_pv",
    "mode_now",
    "mode_off",
    "boost",
    "pv_surplus",
    "phase_limit",
    "import_limit",
    "reserve_guard",
    "overload",
    "wake",
    "not_taken_retry",
    "assignment_unclear",
    "resume_after_outage",
}


def test_reason_codes_in_the_guide_match_the_decision_log() -> None:
    # Die Tabelle der Gründe nennt jeden Code mit dem Text, den das Entscheidungslog zeigt.
    from bindaems.shared.control import CommandKind
    from bindaems.shared.decisions import DecisionResult, decision_text

    documented: dict[str, str] = {}
    for line in _guide_section("Laderegelung").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        codes = re.findall(r"`(\w+)`", cells[0]) if len(cells) >= 3 else []
        if line.startswith("| `") and codes and set(codes) <= CHARGING_REASONS:
            labels = [label.strip() for label in cells[1].split(", ")]
            assert len(labels) == len(codes), line
            documented.update(zip(codes, labels, strict=True))
    assert set(documented) == CHARGING_REASONS
    for code, label in documented.items():
        shown = decision_text(
            "A", CommandKind.CURRENT, 8.0, DecisionResult.DRY_RUN, reasons=(code,)
        )
        assert f"({label})" in shown, code


def test_simulator_section_matches_the_tool(capsys: pytest.CaptureFixture[str]) -> None:
    # Die Szenarien und Optionen im Handbuch gibt es, und das Werkzeug kennt keine ungenannten.
    from bindaems.tools.sim.__main__ import main
    from bindaems.tools.sim.scenarios import SCENARIOS

    with pytest.raises(SystemExit):
        main(["--help"])
    known = set(re.findall(r"--[a-z][a-z-]*", capsys.readouterr().out))
    section = _guide_section("Simulator")
    documented = set(re.findall(r"--[a-z][a-z-]*", section)) - {"--rm", "--entrypoint"}
    assert documented == known - {"--help"}
    scenarios = re.findall(r"^\| `([a-z0-9-]+)` \|", section, re.MULTILINE)
    assert scenarios == list(SCENARIOS)


def test_operations_guide_describes_operating_states_and_the_watchdog() -> None:
    text = Path("docs/betrieb.md").read_text(encoding="utf-8")
    for topic in (
        "## 13. Betriebszustände und Freigaben",
        "## 14. Watchdog am Cerbo",
        "### Reihenfolge der Inbetriebnahme",
        "### Wartung",
        "-m bindaems.tools.watchdog_config --config /config/config.yaml > config.ini.new",
        "-m bindaems.tools.watchdog_probe --config /config/config.yaml",
        "./install.sh root@cerbo.lan /opt/bindaems/config.ini",
        "./install.sh --uninstall root@cerbo.lan",
        "Watchdog ausgelöst",
    ):
        assert topic in text


@pytest.mark.parametrize("doc", ["docs/betrieb.md", "cerbo-watchdog/README.md"])
def test_watchdog_config_is_only_replaced_when_generated(doc: str) -> None:
    # Schlägt die Erzeugung fehl, leerte „> config.ini“ die bisherige Datei.
    text = Path(doc).read_text(encoding="utf-8")
    assert "> config.ini.new \\\n  && mv config.ini.new config.ini\n" in text
    assert not re.search(r"> config\.ini$", text, re.M)


TOOL_COMMAND = re.compile(r"docker compose run [^`]*?-m (bindaems\.tools\.\w+)", re.DOTALL)


def test_documented_tool_commands_start_python_directly() -> None:
    # Der ENTRYPOINT des core-Images ist ``python -m bindaems.core``: Ein Werkzeug braucht
    # ``--entrypoint python``, und das Modul muss es geben. Das Betriebshandbuch nennt alle.
    handbook = Path("docs/betrieb.md").read_text(encoding="utf-8")
    assert {
        "bindaems.tools.verify_part1",
        "bindaems.tools.verify_part2",
        "bindaems.tools.watchdog_config",
        "bindaems.tools.watchdog_probe",
    } <= set(TOOL_COMMAND.findall(handbook))
    for doc in ("docs/betrieb.md", "docs/verification/README.md", "cerbo-watchdog/README.md"):
        for command in TOOL_COMMAND.finditer(Path(doc).read_text(encoding="utf-8")):
            assert importlib.util.find_spec(command[1]) is not None, doc
            assert "--entrypoint python" in command[0], doc


def test_part2_options_in_the_docs_exist(capsys: pytest.CaptureFixture[str]) -> None:
    # Die Abschnitte zu Teil 2 nennen nur Optionen, die verify_part2 kennt (und Docker-Flags).
    from bindaems.tools.verify_part2 import main

    with pytest.raises(SystemExit):
        main(["--help"])
    known = set(re.findall(r"--[a-z][a-z-]*", capsys.readouterr().out)) | {"--rm", "--entrypoint"}
    sections = {
        "docs/betrieb.md": r"### Prüfprotokoll Teil 2\n(.*?)\n### ",
        "docs/verification/README.md": r"## Teil 2 – schreibend \(17\.2\)\n(.*?)\n## ",
    }
    for doc, pattern in sections.items():
        found = re.search(pattern, Path(doc).read_text(encoding="utf-8"), re.DOTALL)
        assert found, doc
        documented = set(re.findall(r"--[a-z][a-z-]*", found[1]))
        assert {"--punkt", "--out", "--yes", "--schlafend"} <= documented, doc
        assert documented <= known, f"{doc}: {sorted(documented - known)}"


def test_relative_links_in_the_docs_exist() -> None:
    link = re.compile(r"\]\(([^)\s]+)\)")
    docs = [Path("README.md"), Path("cerbo-watchdog/README.md"), *Path("docs").rglob("*.md")]
    missing = []
    for doc in docs:
        for target in link.findall(doc.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if not (doc.parent / target.split("#")[0]).exists():
                missing.append(f"{doc}: {target}")
    assert missing == []
