import importlib.util
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
