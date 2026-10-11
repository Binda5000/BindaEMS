import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

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
    "dbus": """#!/bin/sh
printf 'dbus %s\\n' "$*" >>"$FAKE_LOG"
case $3 in /Version) echo "'1.0.0'" ;; /State) echo 0 ;; esac
""",
    "python3": """#!/bin/sh
exit "${FAKE_PYTHON_STATUS:-0}"
""",
    "sleep": """#!/bin/sh
exit 0
""",
}
TARGET = "root@cerbo.test"
PATHS = "/data/bindaems-watchdog /service/bindaems-watchdog /data/rc.local /opt/victronenergy"
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
        f"ssh {TARGET} tar -C /data/bindaems-watchdog -x -o -f -",
        f"ssh {TARGET} sh -s -- {PATHS}",
    ]
    assert not any(call.startswith("svc ") for call in calls)  # svscan startet ihn selbst
    assert list(device.tmp.iterdir()) == []  # das Übertragungsverzeichnis ist wieder weg
    assert "dbus -y com.victronenergy.bindaems /Version GetValue" in calls
    assert "Version: '1.0.0'" in result.stdout and "Zustand: 0 (0 wartet" in result.stdout


def test_update_replaces_the_block_and_restarts(device: Device) -> None:
    device.rc_local.write_text(
        "#!/bin/sh\necho vorher\n# bindaems-watchdog begin\nalt\n# bindaems-watchdog end\n"
        "echo nachher\n"
    )
    assert device.install(TARGET, str(device.config)).returncode == 0
    (device.home / "state.json").write_text('{"armed": true}')
    result = device.install(TARGET, str(device.config))
    assert result.returncode == 0, result.stderr
    expected = "#!/bin/sh\necho vorher\n" + device.block + "echo nachher\n"
    assert device.rc_local.read_text() == expected
    assert device.calls().count(f"svc -t {device.link}") == 1
    assert (device.home / "state.json").read_text() == '{"armed": true}'


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


def test_exit_in_rc_local_is_reported(device: Device) -> None:
    device.rc_local.write_text("#!/bin/sh\nexit 0\n")
    result = device.install(TARGET, str(device.config))
    assert result.returncode == 0 and "exit-Zeile" in result.stderr


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
