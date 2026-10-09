import io
from pathlib import Path

import pyotp
import yaml
from tests.app_helpers import PASSWORD, mock_all_sources

from bindaems.app.audit import AuditLog
from bindaems.app.auth.service import AuthService
from bindaems.app.cli import main
from bindaems.app.db.engine import DB_FILENAME, migrate, open_database
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


def test_reset_totp_lets_a_user_with_a_lost_device_log_in(tmp_path, capsys) -> None:
    path = write_app_config(tmp_path)
    engine = open_database(tmp_path / DB_FILENAME)
    migrate(engine)
    auth = AuthService(engine, SystemClock(), AuditLog(engine, SystemClock()))
    user = auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    secret, _ = auth.totp_begin(user.id, PASSWORD)
    auth.totp_enable(user.id, pyotp.TOTP(secret).now(), actor="chris", source="ui")
    try:
        assert main(["reset-totp", "--username", "Chris", "--config", str(path)]) == 0
        assert "Zwei-Faktor-Anmeldung für „chris“ abgeschaltet." in capsys.readouterr().out
        assert auth.login("chris", PASSWORD, totp=None, remember=False)
    finally:
        engine.dispose()


def test_reset_totp_for_unknown_user_fails(tmp_path, capsys) -> None:
    argv = ["reset-totp", "--username", "niemand", "--config", str(write_app_config(tmp_path))]
    assert main(argv) == 1
    assert "Benutzer nicht gefunden" in capsys.readouterr().err


def test_cli_rejects_invalid_config(tmp_path, capsys) -> None:
    (tmp_path / "config.yaml").write_text("grid: {}\n")
    argv = ["create-admin", "--username", "chris", "--config", str(tmp_path / "config.yaml")]
    assert main(argv) == 2
    assert "Konfiguration ungültig" in capsys.readouterr().err


def test_verify_prices_needs_no_config(tmp_path, respx_mock, capsys) -> None:
    mock_all_sources(respx_mock)
    assert main(["verify-prices", "--out", str(tmp_path)]) == 0
    [protocol] = tmp_path.glob("pruefprotokoll-preise-*.md")
    assert "Ergebnis: bestanden" in protocol.read_text()
    assert str(protocol) in capsys.readouterr().out


def test_serve_returns_2_on_invalid_config(tmp_path, capsys) -> None:
    (tmp_path / "config.yaml").write_text("grid: {}\n")
    assert main(["serve", "--config", str(tmp_path / "config.yaml")]) == 2
    assert "Konfiguration ungültig" in capsys.readouterr().err


def test_serve_returns_2_without_internal_token(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("BINDAEMS_INTERNAL_TOKEN", raising=False)
    assert main(["--config", str(write_app_config(tmp_path))]) == 2
    assert "Secrets ungültig" in capsys.readouterr().err


def test_serve_never_echoes_secret_values(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("BINDAEMS_INTERNAL_TOKEN", "geheim-zu-kurz")
    assert main(["--config", str(write_app_config(tmp_path))]) == 2
    assert "geheim-zu-kurz" not in capsys.readouterr().err
