import io
from pathlib import Path

import yaml
from tests.app_helpers import PASSWORD

from bindaems.app.audit import AuditLog
from bindaems.app.auth.service import AuthService
from bindaems.app.cli import main
from bindaems.app.db.engine import DB_FILENAME, open_database
from bindaems.shared.timeutil import SystemClock

EXAMPLE = Path("deploy/config.example.yaml")


def write_app_config(tmp_path: Path) -> Path:
    data = yaml.safe_load(EXAMPLE.read_text())
    data["app"]["data_dir"] = str(tmp_path)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_create_admin_reads_password_from_stdin(tmp_path, monkeypatch, capsys) -> None:
    path = write_app_config(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    argv = ["create-admin", "--username", "Chris", "--password-stdin", "--config", str(path)]
    assert main(argv) == 0
    assert "Admin „chris“ angelegt." in capsys.readouterr().out
    engine = open_database(tmp_path / DB_FILENAME)
    [user] = AuthService(engine, SystemClock(), AuditLog(engine, SystemClock())).list_users()
    engine.dispose()
    assert (user.username, user.role) == ("chris", "admin")


def test_create_admin_twice_fails(tmp_path, monkeypatch, capsys) -> None:
    path = write_app_config(tmp_path)
    argv = ["create-admin", "--username", "chris", "--password-stdin", "--config", str(path)]
    for expected in (0, 1):
        monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
        assert main(argv) == expected
    assert "existiert bereits" in capsys.readouterr().err


def test_set_password_for_unknown_user_fails(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    path = write_app_config(tmp_path)
    argv = ["set-password", "--username", "niemand", "--password-stdin", "--config", str(path)]
    assert main(argv) == 1
    assert "Benutzer nicht gefunden" in capsys.readouterr().err


def test_cli_rejects_invalid_config(tmp_path, capsys) -> None:
    (tmp_path / "config.yaml").write_text("grid: {}\n")
    argv = ["create-admin", "--username", "chris", "--config", str(tmp_path / "config.yaml")]
    assert main(argv) == 2
    assert "Konfiguration ungültig" in capsys.readouterr().err
