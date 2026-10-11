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


def test_documented_tool_commands_start_python_directly() -> None:
    # Der ENTRYPOINT des core-Images ist ``python -m bindaems.core``: Ein Werkzeug braucht
    # ``--entrypoint python``, und das Modul muss es geben.
    text = Path("docs/betrieb.md").read_text(encoding="utf-8")
    commands = re.findall(r"docker compose run [^`]*?-m (bindaems\.tools\.\w+)", text, re.DOTALL)
    assert {
        "bindaems.tools.verify_part1",
        "bindaems.tools.watchdog_config",
        "bindaems.tools.watchdog_probe",
    } <= set(commands)
    for module in commands:
        assert importlib.util.find_spec(module) is not None
    for block in re.findall(r"docker compose run [^`]*?-m bindaems\.tools\.\w+", text, re.DOTALL):
        assert "--entrypoint python" in block


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
