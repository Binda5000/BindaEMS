import hashlib
import json
import threading
import time
from datetime import timedelta
from typing import Literal

import pyotp
import pytest
from argon2 import PasswordHasher
from sqlalchemy import select
from tests.app_helpers import PASSWORD, T_APP

from bindaems.app.auth.service import (
    AuthService,
    InvalidCredentialsError,
    InvalidUsernameError,
    LastAdminError,
    LockedError,
    PasswordPolicyError,
    TotpRequiredError,
    UserExistsError,
)
from bindaems.app.db.schema import session_table, user_table


def test_create_user_stores_argon2id_hash_and_lowercase_name(auth, engine) -> None:
    user = auth.create_user("Chris", PASSWORD, "admin", actor="cli", source="cli")
    assert (user.username, user.role) == ("chris", "admin")
    with engine.connect() as conn:
        stored = conn.execute(select(user_table.c.password_hash)).scalar_one()
    assert stored.startswith("$argon2id$") and PASSWORD not in stored


@pytest.mark.parametrize("name", ["ab", "mit leerzeichen", "x" * 33, "ümlaut"])
def test_invalid_usernames_rejected(auth, name: str) -> None:
    with pytest.raises(InvalidUsernameError):
        auth.create_user(name, PASSWORD, "viewer", actor="cli", source="cli")


def test_short_password_rejected(auth) -> None:
    with pytest.raises(PasswordPolicyError):
        auth.create_user("chris", "zu-kurz", "viewer", actor="cli", source="cli")


def test_duplicate_username_rejected_case_insensitive(auth) -> None:
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    with pytest.raises(UserExistsError):
        auth.create_user("CHRIS", PASSWORD, "viewer", actor="cli", source="cli")


def test_login_stores_only_the_token_hash(auth, engine) -> None:
    auth.create_user("chris", PASSWORD, "operator", actor="cli", source="cli")
    session = auth.login("Chris", PASSWORD, totp=None, remember=False)
    with engine.connect() as conn:
        ids = conn.execute(select(session_table.c.id)).scalars().all()
    assert ids == [hashlib.sha256(session.token.encode()).hexdigest()]
    assert auth.resolve(session.token).user.username == "chris"


def test_wrong_password_and_unknown_user_look_the_same(auth) -> None:
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    with pytest.raises(InvalidCredentialsError) as wrong:
        auth.login("chris", "falsch-falsch", totp=None, remember=False)
    with pytest.raises(InvalidCredentialsError) as unknown:
        auth.login("niemand", "falsch-falsch", totp=None, remember=False)
    assert str(wrong.value) == str(unknown.value) == "Benutzername oder Passwort falsch"


def test_lockout_after_five_failures_within_15_minutes(auth, clock) -> None:
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    for _ in range(5):  # Fehlversuche bei T+0 … T+4 min
        with pytest.raises(InvalidCredentialsError):
            auth.login("chris", "falsch-falsch", totp=None, remember=False)
        clock.advance(60)
    with pytest.raises(LockedError) as locked:
        auth.login("chris", PASSWORD, totp=None, remember=False)
    assert locked.value.until == T_APP + timedelta(minutes=19)
    clock.advance(timedelta(minutes=14))  # jetzt T+19 min
    assert auth.login("chris", PASSWORD, totp=None, remember=False).user.username == "chris"


def test_failures_spread_over_more_than_15_minutes_do_not_lock(auth, clock) -> None:
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    for _ in range(5):  # T+0, 4, 8, 12, 16 min
        with pytest.raises(InvalidCredentialsError):
            auth.login("chris", "falsch-falsch", totp=None, remember=False)
        clock.advance(timedelta(minutes=4))
    assert auth.login("chris", PASSWORD, totp=None, remember=False)


def test_unknown_usernames_are_locked_like_real_ones(auth) -> None:
    for _ in range(5):
        with pytest.raises(InvalidCredentialsError):
            auth.login("niemand", "falsch-falsch", totp=None, remember=False)
    with pytest.raises(LockedError):
        auth.login("niemand", "falsch-falsch", totp=None, remember=False)


def test_successful_login_clears_failures(auth) -> None:
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    for _ in range(2):
        for _ in range(4):
            with pytest.raises(InvalidCredentialsError):
                auth.login("chris", "falsch-falsch", totp=None, remember=False)
        assert auth.login("chris", PASSWORD, totp=None, remember=False)


def test_idle_session_expires_after_30_minutes(auth, clock) -> None:
    auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    session = auth.login("chris", PASSWORD, totp=None, remember=False)
    clock.advance(timedelta(minutes=30))
    assert auth.resolve(session.token) is None


def test_activity_extends_idle_session(auth, clock) -> None:
    auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    session = auth.login("chris", PASSWORD, totp=None, remember=False)
    clock.advance(timedelta(minutes=29))
    assert auth.resolve(session.token) is not None
    clock.advance(timedelta(minutes=29))
    assert auth.resolve(session.token) is not None


def test_remember_me_keeps_operator_logged_in_for_30_days(auth, clock) -> None:
    auth.create_user("chris", PASSWORD, "operator", actor="cli", source="cli")
    session = auth.login("chris", PASSWORD, totp=None, remember=True)
    clock.advance(timedelta(days=29))
    assert auth.resolve(session.token) is not None
    clock.advance(timedelta(days=1))
    assert auth.resolve(session.token) is None


def test_remember_me_is_ignored_for_admins(auth) -> None:
    auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    session = auth.login("chris", PASSWORD, totp=None, remember=True)
    assert session.remember is False and session.expires_at == T_APP + timedelta(minutes=30)


def test_logout_deletes_session(auth) -> None:
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    session = auth.login("chris", PASSWORD, totp=None, remember=False)
    auth.logout(session.token)
    assert auth.resolve(session.token) is None


def test_totp_required_once_enabled_and_codes_not_reusable(auth, clock) -> None:
    user = auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    secret, uri = auth.totp_begin(user.id, PASSWORD)
    assert uri.startswith("otpauth://totp/BindaEMS:chris?")
    totp = pyotp.TOTP(secret)
    auth.totp_enable(user.id, totp.at(clock.now()), actor="chris", source="ui")
    with pytest.raises(TotpRequiredError):
        auth.login("chris", PASSWORD, totp=None, remember=False)
    clock.advance(30)
    code = totp.at(clock.now())
    assert auth.login("chris", PASSWORD, totp=code, remember=False)
    with pytest.raises(InvalidCredentialsError):
        auth.login("chris", PASSWORD, totp=code, remember=False)


def test_wrong_totp_codes_count_as_failures(auth, clock) -> None:
    user = auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    secret, _ = auth.totp_begin(user.id, PASSWORD)
    auth.totp_enable(user.id, pyotp.TOTP(secret).at(clock.now()), actor="chris", source="ui")
    for _ in range(5):
        with pytest.raises(InvalidCredentialsError):
            auth.login("chris", PASSWORD, totp="000000", remember=False)
    with pytest.raises(LockedError):
        auth.login("chris", PASSWORD, totp="000000", remember=False)


def test_role_change_ends_sessions_and_last_admin_is_protected(auth) -> None:
    admin = auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    operator = auth.create_user("gast", PASSWORD, "operator", actor="chris", source="ui")
    session = auth.login("gast", PASSWORD, totp=None, remember=True)
    auth.set_role(operator.id, "viewer", actor="chris", source="ui")
    assert auth.resolve(session.token) is None
    with pytest.raises(LastAdminError):
        auth.set_role(admin.id, "operator", actor="chris", source="ui")
    with pytest.raises(LastAdminError):
        auth.delete_user(admin.id, actor="chris", source="ui")


def test_password_change_ends_other_sessions(auth) -> None:
    user = auth.create_user("chris", PASSWORD, "operator", actor="cli", source="cli")
    keep = auth.login("chris", PASSWORD, totp=None, remember=False)
    other = auth.login("chris", PASSWORD, totp=None, remember=False)
    auth.set_password(
        user.id,
        "neues-passwort-1",
        actor="chris",
        source="ui",
        keep_session=auth.resolve(keep.token).session_id,
    )
    assert auth.resolve(keep.token) is not None and auth.resolve(other.token) is None


def test_changes_are_audited_without_secrets(auth, audit) -> None:
    user = auth.create_user("chris", PASSWORD, "operator", actor="cli", source="cli")
    auth.set_role(user.id, "viewer", actor="admin", source="ui")
    entries = audit.recent()
    assert [e.action for e in entries] == ["user.role", "user.create"]
    assert PASSWORD not in json.dumps([e.details for e in entries])


def test_admins_without_totp_are_reported(auth) -> None:
    auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    assert auth.warnings() == ["Admin „chris“ hat keine Zwei-Faktor-Anmeldung (TOTP)."]


class SlowHasher(PasswordHasher):
    """Schnelle Argon2-Parameter, aber jede Prüfung dauert 20 ms; zählt gleichzeitige Prüfungen."""

    def __init__(self) -> None:
        super().__init__(time_cost=1, memory_cost=8, parallelism=1)
        self._guard = threading.Lock()
        self.active = 0
        self.peak = 0

    def verify(self, hash: str | bytes, password: str | bytes) -> Literal[True]:
        with self._guard:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(0.02)
            return super().verify(hash, password)
        finally:
            with self._guard:
                self.active -= 1


def _parallel_logins(auth: AuthService, names: list[str]) -> list[str]:
    results: list[str] = []

    def attempt(name: str) -> None:
        try:
            auth.login(name, "falsch-falsch-1", totp=None, remember=False)
        except InvalidCredentialsError:
            results.append("invalid")
        except LockedError:
            results.append("locked")

    threads = [threading.Thread(target=attempt, args=(name,)) for name in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def test_parallel_wrong_passwords_cannot_bypass_the_lockout(engine, clock, audit) -> None:
    auth = AuthService(engine, clock, audit, hasher=SlowHasher())
    auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    results = _parallel_logins(auth, ["chris"] * 40)
    assert (results.count("invalid"), results.count("locked")) == (5, 35)


def test_password_checks_run_at_most_two_at_a_time(engine, clock, audit) -> None:
    hasher = SlowHasher()
    auth = AuthService(engine, clock, audit, hasher=hasher)
    _parallel_logins(auth, [f"gast{i}" for i in range(12)])  # Argon2 braucht je 64 MiB
    assert 1 <= hasher.peak <= 2


def _demote_in_parallel(auth: AuthService, user_ids: list[int]) -> list[str]:
    results: list[str] = []

    def demote(user_id: int) -> None:
        try:
            auth.set_role(user_id, "viewer", actor="test", source="ui")
            results.append("ok")
        except LastAdminError:
            results.append("last-admin")

    threads = [threading.Thread(target=demote, args=(user_id,)) for user_id in user_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def test_parallel_demotions_keep_one_admin(auth, monkeypatch) -> None:
    anna = auth.create_user("anna", PASSWORD, "admin", actor="cli", source="cli")
    bert = auth.create_user("bert", PASSWORD, "admin", actor="cli", source="cli")
    count = AuthService._admin_count

    def slow_count(conn):
        result = count(conn)
        time.sleep(0.05)  # Zeitfenster zwischen Zählen und Ändern weiten
        return result

    monkeypatch.setattr(AuthService, "_admin_count", staticmethod(slow_count))
    results = _demote_in_parallel(auth, [anna.id, bert.id])
    assert sorted(results) == ["last-admin", "ok"]
    assert [user.role for user in auth.list_users()].count("admin") == 1


def test_password_checks_count_towards_the_login_lockout(auth) -> None:
    # Mit einer offenen Sitzung ließe sich das Passwort sonst unbegrenzt durchprobieren
    user = auth.create_user("chris", PASSWORD, "operator", actor="cli", source="cli")
    for _ in range(5):
        with pytest.raises(InvalidCredentialsError):
            auth.check_password(user.id, "falsch-falsch")
    with pytest.raises(LockedError):
        auth.check_password(user.id, PASSWORD)
    with pytest.raises(LockedError):
        auth.login("chris", PASSWORD, totp=None, remember=False)


def test_totp_setup_needs_the_password(auth) -> None:
    user = auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    with pytest.raises(InvalidCredentialsError):
        auth.totp_begin(user.id, "falsch-falsch")


def test_reset_totp_turns_two_factor_off_without_password(auth, audit, clock) -> None:
    user = auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    secret, _ = auth.totp_begin(user.id, PASSWORD)
    auth.totp_enable(user.id, pyotp.TOTP(secret).at(clock.now()), actor="chris", source="ui")
    auth.reset_totp(user.id, actor="cli", source="cli")
    assert not auth.get_user(user.id).totp_enabled
    assert auth.login("chris", PASSWORD, totp=None, remember=False)
    assert (audit.recent()[0].action, audit.recent()[0].source) == ("user.totp_disable", "cli")
