import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from bindaems_watchdog import VERSION

ROOT = Path("cerbo-watchdog")
SCRIPTS = ["install.sh", "service/run", "service/log/run"]


def run_bash(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash is not None
    return subprocess.run(  # noqa: S603 - festes Skript aus dem Repository
        [bash, *args], capture_output=True, text=True, check=check
    )


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_syntax(name: str) -> None:
    run_bash("-n", str(ROOT / name), check=True)


def test_install_without_arguments_prints_usage() -> None:
    result = run_bash(str(ROOT / "install.sh"))
    assert result.returncode == 2 and result.stderr.startswith("Aufruf:")


@pytest.mark.parametrize("name", [*SCRIPTS, "bindaems-watchdog.py"])
def test_scripts_are_executable(name: str) -> None:
    assert os.access(ROOT / name, os.X_OK)


def test_entry_script_finds_its_package() -> None:
    # -I: weder PYTHONPATH noch das Verzeichnis des Skripts stehen von selbst in sys.path. Ohne
    # Argument endet main sofort; bei einem Konfigfehler wartet es 60 s.
    result = subprocess.run(  # noqa: S603 - festes Skript aus dem Repository
        [sys.executable, "-I", str(ROOT / "bindaems-watchdog.py")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "Aufruf: bindaems-watchdog.py <config.ini>" in result.stderr


# install.sh gegen ein nachgebildetes Gerät: ssh fügt wie das echte die Argumente zu einem Befehl
# zusammen und führt ihn lokal mit sh aus; jeder absolute Pfad darin zeigt in die Sandbox. svc,
# dbus, python3 und sleep sind Attrappen; jeder Aufruf von ssh, svc und dbus landet im Protokoll.
# Nur die Prüfung der config.ini mit dem übertragenen Paket (load_settings) läuft mit echtem Python.
FAKES = {
    "ssh": """#!/bin/sh
printf 'ssh %s\\n' "$*" >>"$FAKE_LOG"
shift
command=""
for arg in "$@"; do
  case $arg in /*) arg=$SANDBOX$arg ;; esac
  command="$command $arg"
done
exec sh -c "$command"
""",
    "svc": """#!/bin/sh
printf 'svc %s\\n' "$*" >>"$FAKE_LOG"
""",
    "dbus": f"""#!/bin/sh
printf 'dbus %s\\n' "$*" >>"$FAKE_LOG"
case $3 in /Version) echo "'{VERSION}'" ;; /State) echo 0 ;; esac
""",
    "python3": """#!/bin/sh
case $* in *load_settings*) exec "$REAL_PYTHON" "$@" ;; esac
exit "${FAKE_PYTHON_STATUS:-0}"
""",
    "sleep": """#!/bin/sh
exit 0
""",
}
TARGET = "root@cerbo.test"
PATHS = (
    "/data/bindaems-watchdog /service/bindaems-watchdog /data/rc.local /opt/victronenergy"
    " /data/bindaems-watchdog/neu"
)
TRANSFER = f"ssh {TARGET} tar -C /data/bindaems-watchdog/neu -x -o -f -"
INVALID = "[watchdog]\ntimeout_s = 0\n"  # hat [watchdog], aber der Watchdog lehnt sie ab
BLOCK = "# bindaems-watchdog begin\nln -sfn {home}/service {link}\n# bindaems-watchdog end\n"


@dataclass
class Device:
    script: Path
    root: Path
    config: Path
    env: dict[str, str]
    log: Path
    tmp: Path  # TMPDIR des Skripts

    @property
    def home(self) -> Path:
        return self.root / "data/bindaems-watchdog"

    @property
    def link(self) -> Path:
        return self.root / "service/bindaems-watchdog"

    @property
    def rc_local(self) -> Path:
        return self.root / "data/rc.local"

    @property
    def block(self) -> str:
        return BLOCK.format(home=self.home, link=self.link)

    def install(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        bash = shutil.which("bash")
        assert bash is not None
        return subprocess.run(  # noqa: S603 - festes Skript aus dem Repository
            [bash, str(self.script), *args],
            capture_output=True,
            text=True,
            env=self.env | env,
            timeout=60,
        )

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []


@pytest.fixture
def device(tmp_path: Path) -> Device:
    source = tmp_path / "cerbo-watchdog"  # eine Kopie, damit Reste von Testläufen sicher da sind
    shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns("__pycache__", ".ruff_cache"))
    (source / "bindaems_watchdog/__pycache__").mkdir()
    (source / "bindaems_watchdog/__pycache__/service.cpython-312.pyc").write_bytes(b"")
    (source / "bindaems_watchdog/alt.pyc").write_bytes(b"")
    fakes = tmp_path / "bin"
    fakes.mkdir()
    for name, text in FAKES.items():
        (fakes / name).write_text(text)
        (fakes / name).chmod(0o755)
    root = tmp_path / "cerbo"
    velib = root / "opt/victronenergy/dbus-systemcalc-py/ext/velib_python"
    velib.mkdir(parents=True)
    (velib / "vedbus.py").write_text("")
    (root / "data").mkdir()
    (root / "service").mkdir()
    config = tmp_path / "anlage.ini"  # auf dem Gerät heißt sie immer config.ini
    config.write_text((ROOT / "config.ini.example").read_text(encoding="utf-8"), encoding="utf-8")
    config.chmod(0o600)  # z. B. mit umask 077 erzeugt; auf dem Gerät gilt 0644
    log = tmp_path / "aufrufe.log"
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    env = {**os.environ, "PATH": f"{fakes}{os.pathsep}{os.environ['PATH']}"}
    env |= {"SANDBOX": str(root), "FAKE_LOG": str(log), "TMPDIR": str(tmp)}
    env |= {"REAL_PYTHON": sys.executable}
    return Device(source / "install.sh", root, config, env, log, tmp)


def test_install_sets_up_the_device(device: Device) -> None:
    result = device.install(TARGET, str(device.config))
    assert result.returncode == 0, result.stderr
    for name in ("bindaems-watchdog.py", "bindaems_watchdog/service.py", "service/log/run"):
        assert (device.home / name).is_file()
    assert (device.home / "config.ini").read_bytes() == device.config.read_bytes()
    assert (device.home / "config.ini").stat().st_mode & 0o777 == 0o644
    assert os.access(device.home / "service/run", os.X_OK)
    assert os.readlink(device.link) == f"{device.home}/service"
    assert device.rc_local.read_text() == "#!/bin/sh\n" + device.block
    assert os.access(device.rc_local, os.X_OK)
    assert {p.name for p in device.home.iterdir()} == {
        "bindaems-watchdog.py",
        "bindaems_watchdog",
        "config.ini",
        "service",
    }
    shipped = {p.name for p in (device.home / "bindaems_watchdog").iterdir()}
    assert shipped == {p.name for p in (ROOT / "bindaems_watchdog").glob("*.py")}  # ohne Bytecode
    calls = device.calls()
    assert calls[:3] == [
        f"ssh {TARGET} sh -s -- {PATHS}",
        TRANSFER,
        f"ssh {TARGET} sh -s -- {PATHS}",
    ]
    assert not any(call.startswith("svc ") for call in calls)  # svscan startet ihn selbst
    assert list(device.tmp.iterdir()) == []  # das Übertragungsverzeichnis ist wieder weg
    assert "dbus -y com.victronenergy.bindaems /Version GetValue" in calls
    assert f"Version: '{VERSION}'" in result.stdout and "Zustand: 0 (0 wartet" in result.stdout


def test_update_replaces_the_block_and_restarts(device: Device) -> None:
    device.rc_local.write_text(
        "#!/bin/sh\necho vorher\n# bindaems-watchdog begin\nalt\n# bindaems-watchdog end\n"
        "echo nachher\n"
    )
    assert device.install(TARGET, str(device.config)).returncode == 0
    (device.home / "state.json").write_text('{"armed": true}')
    (device.home / "bindaems_watchdog/alt.py").write_text("")  # aus einer älteren Version
    for supervise in ("service/supervise", "service/log/supervise"):  # legt daemontools an
        (device.home / supervise).mkdir()
        (device.home / supervise / "status").write_text("läuft")
    result = device.install(TARGET, str(device.config))
    assert result.returncode == 0, result.stderr
    expected = "#!/bin/sh\necho vorher\n" + device.block + "echo nachher\n"
    assert device.rc_local.read_text() == expected
    assert device.calls().count(f"svc -t {device.link}") == 1
    assert (device.home / "state.json").read_text() == '{"armed": true}'
    assert not (device.home / "bindaems_watchdog/alt.py").exists()  # das Paket ist ersetzt
    for supervise in ("service/supervise", "service/log/supervise"):
        assert (device.home / supervise / "status").read_text() == "läuft"
    assert not (device.home / "neu").exists()


def test_uninstall_removes_only_its_own_parts(device: Device) -> None:
    device.rc_local.write_text("#!/bin/sh\necho andere\n")
    assert device.install(TARGET, str(device.config)).returncode == 0
    result = device.install("--uninstall", TARGET)
    assert result.returncode == 0, result.stderr
    assert device.rc_local.read_text() == "#!/bin/sh\necho andere\n"
    assert not device.link.is_symlink() and not device.home.exists()
    assert f"svc -d {device.link}" in device.calls()


def test_block_without_end_mark_is_left_alone(device: Device) -> None:
    text = "#!/bin/sh\n# bindaems-watchdog begin\necho fremd\n"
    device.rc_local.write_text(text)
    result = device.install(TARGET, str(device.config))
    assert result.returncode == 1 and "keine Endmarke" in result.stderr
    assert device.rc_local.read_text() == text and not device.link.is_symlink()
    assert device.calls() == [f"ssh {TARGET} sh -s -- {PATHS}"]  # geprüft vor jeder Änderung
    assert not device.home.exists()


def test_uninstall_with_a_block_without_end_mark_changes_nothing(device: Device) -> None:
    assert device.install(TARGET, str(device.config)).returncode == 0
    text = device.rc_local.read_text().replace("# bindaems-watchdog end\n", "")
    device.rc_local.write_text(text)
    before = len(device.calls())
    result = device.install("--uninstall", TARGET)
    assert result.returncode == 1 and "keine Endmarke" in result.stderr
    assert device.rc_local.read_text() == text
    assert device.link.is_symlink() and (device.home / "service/run").is_file()
    assert device.calls()[before:] == [f"ssh {TARGET} sh -s -- {PATHS}"]  # kein svc


@pytest.mark.parametrize(
    "line", ["exit 0", "exit", "exit 1  # Ende", "exit;"], ids=["exit 0", "exit", "Kommentar", ";"]
)
def test_exit_in_rc_local_stops_before_any_change(device: Device, line: str) -> None:
    # Der Block käme hinter die exit-Zeile, und der Watchdog startete nach einem Neustart nicht.
    text = f"#!/bin/sh\necho vorher\n{line}\n"
    device.rc_local.write_text(text)
    result = device.install(TARGET, str(device.config))
    assert result.returncode == 1
    assert "exit-Zeile" in result.stderr and "entfernen" in result.stderr
    assert device.rc_local.read_text() == text and not device.link.is_symlink()
    assert device.calls() == [f"ssh {TARGET} sh -s -- {PATHS}"]  # geprüft vor jeder Änderung
    assert not device.home.exists()


def test_exit_after_an_existing_block_also_stops_the_update(device: Device) -> None:
    assert device.install(TARGET, str(device.config)).returncode == 0
    text = device.rc_local.read_text() + "exit 0\n"
    device.rc_local.write_text(text)
    before = len(device.calls())
    result = device.install(TARGET, str(device.config))
    assert result.returncode == 1 and "exit-Zeile" in result.stderr
    assert device.rc_local.read_text() == text
    assert device.calls()[before:] == [f"ssh {TARGET} sh -s -- {PATHS}"]


def test_indented_exit_in_rc_local_is_only_reported(device: Device) -> None:
    device.rc_local.write_text("#!/bin/sh\nif [ -f /data/aus ]; then\n  exit 0\nfi\n")
    result = device.install(TARGET, str(device.config))
    assert result.returncode == 0, result.stderr
    assert "eingerückte exit-Zeile" in result.stderr
    assert device.rc_local.read_text().endswith(device.block)


def test_words_starting_with_exit_are_no_exit_line(device: Device) -> None:
    device.rc_local.write_text("#!/bin/sh\nexit_code=0\nexitcheck\n")
    result = device.install(TARGET, str(device.config))
    assert result.returncode == 0, result.stderr
    assert "exit" not in result.stderr


def test_uninstall_removes_its_block_despite_an_exit_line(device: Device) -> None:
    assert device.install(TARGET, str(device.config)).returncode == 0
    device.rc_local.write_text(device.rc_local.read_text() + "exit 0\n")
    result = device.install("--uninstall", TARGET)
    assert result.returncode == 0, result.stderr
    assert device.rc_local.read_text() == "#!/bin/sh\nexit 0\n"


def test_unsuitable_device_stops_before_copying(device: Device) -> None:
    result = device.install(TARGET, str(device.config), FAKE_PYTHON_STATUS="1")
    assert result.returncode == 1 and "Python 3.8 oder neuer" in result.stderr
    assert device.calls() == [f"ssh {TARGET} sh -s -- {PATHS}"]
    assert not device.home.exists()


@pytest.mark.parametrize(
    "args",
    [
        [TARGET],
        ["--uninstall"],
        ["--uninstall", "-oProxyCommand=x"],
        ["-oProxyCommand=x", "config.ini"],
        [TARGET, "config.ini", "mehr"],
    ],
)
def test_invalid_arguments_print_the_usage_without_ssh(device: Device, args: list[str]) -> None:
    result = device.install(*args)
    assert result.returncode == 2 and result.stderr.startswith("Aufruf:")
    assert device.calls() == []


def test_missing_config_stops_without_ssh(device: Device, tmp_path: Path) -> None:
    result = device.install(TARGET, str(tmp_path / "fehlt.ini"))
    assert result.returncode == 2 and "nicht gefunden" in result.stderr
    assert device.calls() == []


@pytest.mark.parametrize(
    "text",
    ["", "\n", "; nur ein Kommentar\n", "[action:min_soc]\nservice = x\npath = /y\nvalue = 20\n"],
    ids=["leer", "Leerzeile", "Kommentar", "ohne [watchdog]"],
)
def test_config_without_watchdog_section_stops_without_ssh(device: Device, text: str) -> None:
    # Schlägt „watchdog_config … > config.ini“ fehl, bleibt eine leere Datei zurück.
    device.config.write_text(text, encoding="utf-8")
    result = device.install(TARGET, str(device.config))
    assert result.returncode == 2
    assert "[watchdog]" in result.stderr and "nichts geändert" in result.stderr
    assert device.calls() == []


def tree(root: Path) -> dict[str, bytes]:
    """Der Inhalt jeder Datei unter ``root``, je Pfad relativ zu ``root``."""
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_invalid_config_leaves_the_running_installation_alone(device: Device) -> None:
    assert device.install(TARGET, str(device.config)).returncode == 0
    (device.home / "state.json").write_text('{"armed": true}')
    before, rc_before, calls_before = tree(device.home), device.rc_local.read_text(), device.calls()
    device.config.write_text(INVALID, encoding="utf-8")
    result = device.install(TARGET, str(device.config))
    assert result.returncode == 1
    assert "config.ini: timeout_s muss eine positive Zahl sein." in result.stderr  # vom Parser
    assert "bleibt unverändert" in result.stderr
    assert tree(device.home) == before  # Programm, config.ini und state.json wie vorher
    assert device.rc_local.read_text() == rc_before and device.link.is_symlink()
    check = f"ssh {TARGET} sh -s -- {PATHS}"
    assert device.calls()[len(calls_before) :] == [check, TRANSFER, check]  # kein svc, kein dbus


def test_invalid_config_on_a_new_device_leaves_nothing_behind(device: Device) -> None:
    device.config.write_text(INVALID, encoding="utf-8")
    result = device.install(TARGET, str(device.config))
    assert result.returncode == 1
    assert "config.ini: timeout_s muss eine positive Zahl sein." in result.stderr
    assert not device.home.exists() and not device.link.is_symlink()
    assert not device.rc_local.exists()


def test_install_clears_a_leftover_staging_directory(device: Device) -> None:
    # Eine abgebrochene Übertragung hinterlässt ihr Verzeichnis; nichts davon wird installiert.
    leftover = device.home / "neu/bindaems_watchdog"
    leftover.mkdir(parents=True)
    (leftover / "rest.py").write_text("")
    result = device.install(TARGET, str(device.config))
    assert result.returncode == 0, result.stderr
    assert not (device.home / "neu").exists()
    assert not (device.home / "bindaems_watchdog/rest.py").exists()
