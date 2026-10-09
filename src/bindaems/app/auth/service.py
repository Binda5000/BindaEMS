"""Benutzer, Passwörter (Argon2id), Sitzungen, Sperre nach Fehlversuchen und TOTP (Spec 14).

Sitzungen liegen serverseitig in SQLite. Das Cookie trägt nur ein zufälliges Token; gespeichert
wird dessen SHA-256. Die Rolle wird bei jeder Anfrage frisch gelesen.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, get_args

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import Connection, Engine, Row, delete, func, insert, select, update

from bindaems.app.audit import AuditLog, Source
from bindaems.app.db.schema import login_failure_table, session_table, user_table
from bindaems.shared.timeutil import LOCAL_TZ, Clock

Role = Literal["admin", "operator", "viewer"]
ROLES: tuple[Role, ...] = get_args(Role)
ROLE_RANK: dict[Role, int] = {"viewer": 0, "operator": 1, "admin": 2}

IDLE_TIMEOUT = timedelta(minutes=30)
REMEMBER_FOR = timedelta(days=30)
LOCKOUT_FAILURES = 5
LOCKOUT_WINDOW = timedelta(minutes=15)
LOCKOUT_FOR = timedelta(minutes=15)
TOUCH_EVERY = timedelta(seconds=60)
FAILURE_RETENTION = timedelta(days=1)
MIN_PASSWORD_LEN = 10
MAX_PASSWORD_LEN = 1024
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
TOTP_ISSUER = "BindaEMS"
LOGIN_STRIPES = 64  # Anmeldungen desselben Namens laufen nacheinander (Sperre ohne Wettlauf)
HASH_CONCURRENCY = 2  # gleichzeitige Argon2-Berechnungen; jede braucht rund 64 MiB

_Row = Row[*tuple[Any, ...]]


class AuthError(Exception):
    """Fehler der Anmeldung; der Text ist für Menschen gedacht (deutsch)."""


class InvalidCredentialsError(AuthError):
    def __init__(self) -> None:
        super().__init__("Benutzername oder Passwort falsch")


class TotpRequiredError(AuthError):
    def __init__(self) -> None:
        super().__init__("Bestätigungscode erforderlich")


class LockedError(AuthError):
    def __init__(self, until: datetime, now: datetime) -> None:
        self.until = until
        self.retry_after_s = max(1, math.ceil((until - now).total_seconds()))
        local = until.astimezone(LOCAL_TZ)
        super().__init__(f"Zu viele Fehlversuche – Anmeldung gesperrt bis {local:%H:%M} Uhr")


class InvalidTotpError(AuthError):
    def __init__(self) -> None:
        super().__init__("Bestätigungscode ungültig")


class TotpAlreadyEnabledError(AuthError):
    def __init__(self) -> None:
        super().__init__("Die Zwei-Faktor-Anmeldung ist bereits aktiv; zuerst deaktivieren")


class UserExistsError(AuthError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Benutzer „{name}“ existiert bereits")


class UserNotFoundError(AuthError):
    def __init__(self) -> None:
        super().__init__("Benutzer nicht gefunden")


class LastAdminError(AuthError):
    def __init__(self) -> None:
        super().__init__("Der letzte Admin kann nicht entfernt oder herabgestuft werden")


class PasswordPolicyError(AuthError):
    def __init__(self, message: str = "Das Passwort muss mindestens 10 Zeichen lang sein") -> None:
        super().__init__(message)


class InvalidUsernameError(AuthError):
    def __init__(self) -> None:
        super().__init__("Benutzername: 3–32 Zeichen aus a–z, 0–9, Punkt, Bindestrich, Unterstrich")


@dataclass(frozen=True)
class User:
    id: int
    username: str
    role: Role
    totp_enabled: bool
    created_at: datetime


@dataclass(frozen=True)
class NewSession:
    token: str
    csrf_token: str
    expires_at: datetime
    remember: bool
    user: User


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    csrf_token: str
    expires_at: datetime
    remember: bool
    user: User


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _normalize_username(username: str) -> str:
    name = username.strip().lower()
    if not USERNAME_RE.fullmatch(name):
        raise InvalidUsernameError()
    return name


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise PasswordPolicyError()
    if len(password) > MAX_PASSWORD_LEN:
        raise PasswordPolicyError("Das Passwort darf höchstens 1024 Zeichen lang sein")


def _check_role(role: str) -> Role:
    for known in ROLES:
        if role == known:
            return known
    raise ValueError(f"unbekannte Rolle: {role}")


def _user(row: _Row) -> User:
    return User(
        id=row.id,
        username=row.username,
        role=_check_role(row.role),
        totp_enabled=bool(row.totp_enabled),
        created_at=row.created_at,
    )


class AuthService:
    def __init__(
        self,
        engine: Engine,
        clock: Clock,
        audit: AuditLog,
        *,
        hasher: PasswordHasher | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._audit = audit
        self._hasher = hasher or PasswordHasher()
        # unbekannte Namen werden gegen diesen Hash geprüft, damit die Antwortzeit gleich bleibt
        self._login_locks = [threading.Lock() for _ in range(LOGIN_STRIPES)]
        self._hash_slots = threading.BoundedSemaphore(HASH_CONCURRENCY)
        self._dummy_hash = self._hash(secrets.token_urlsafe(16))

    # --- Benutzer ------------------------------------------------------------------------

    def create_user(
        self, username: str, password: str, role: Role, *, actor: str, source: Source
    ) -> User:
        name = _normalize_username(username)
        _check_password(password)
        role = _check_role(role)
        password_hash = self._hash(password)
        now = self._clock.now()
        with self._engine.begin() as conn:
            if self._find(conn, name) is not None:
                raise UserExistsError(name)
            user_id: int = conn.execute(
                insert(user_table)
                .values(
                    username=name,
                    password_hash=password_hash,
                    role=role,
                    totp_secret=None,
                    totp_enabled=False,
                    totp_last_step=None,
                    created_at=now,
                    updated_at=now,
                )
                .returning(user_table.c.id)
            ).scalar_one()
            self._audit.record(actor, source, "user.create", name, {"role": role}, conn=conn)
        return User(user_id, name, role, False, now)

    def list_users(self) -> list[User]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(user_table).order_by(user_table.c.username)).all()
        return [_user(row) for row in rows]

    def get_user(self, user_id: int) -> User:
        with self._engine.connect() as conn:
            return _user(self._get(conn, user_id))

    def set_role(self, user_id: int, role: Role, *, actor: str, source: Source) -> User:
        role = _check_role(role)
        with self._engine.begin() as conn:
            row = self._get(conn, user_id)
            if row.role == "admin" and role != "admin" and self._admin_count(conn) == 1:
                raise LastAdminError()
            conn.execute(
                update(user_table)
                .where(user_table.c.id == user_id)
                .values(role=role, updated_at=self._clock.now())
            )
            # alte Sitzungen könnten sonst Rechte oder „angemeldet bleiben“ der alten Rolle behalten
            conn.execute(delete(session_table).where(session_table.c.user_id == user_id))
            self._audit.record(
                actor, source, "user.role", row.username, {"from": row.role, "to": role}, conn=conn
            )
            return _user(self._get(conn, user_id))

    def set_password(
        self,
        user_id: int,
        password: str,
        *,
        actor: str,
        source: Source,
        keep_session: str | None = None,
    ) -> None:
        _check_password(password)
        password_hash = self._hash(password)
        with self._engine.begin() as conn:
            row = self._get(conn, user_id)
            conn.execute(
                update(user_table)
                .where(user_table.c.id == user_id)
                .values(password_hash=password_hash, updated_at=self._clock.now())
            )
            sessions = delete(session_table).where(session_table.c.user_id == user_id)
            if keep_session is not None:
                sessions = sessions.where(session_table.c.id != keep_session)
            conn.execute(sessions)
            self._audit.record(actor, source, "user.password", row.username, conn=conn)

    def verify_password(self, user_id: int, password: str) -> bool:
        with self._engine.connect() as conn:
            row = self._get(conn, user_id)
        return self._verify(row.password_hash, password)

    def delete_user(self, user_id: int, *, actor: str, source: Source) -> None:
        with self._engine.begin() as conn:
            row = self._get(conn, user_id)
            if row.role == "admin" and self._admin_count(conn) == 1:
                raise LastAdminError()
            conn.execute(delete(user_table).where(user_table.c.id == user_id))  # Sitzungen: CASCADE
            self._audit.record(actor, source, "user.delete", row.username, conn=conn)

    def warnings(self) -> list[str]:
        query = (
            select(user_table.c.username)
            .where(user_table.c.role == "admin", user_table.c.totp_enabled.is_(False))
            .order_by(user_table.c.username)
        )
        with self._engine.connect() as conn:
            names = conn.execute(query).scalars().all()
        return [f"Admin „{name}“ hat keine Zwei-Faktor-Anmeldung (TOTP)." for name in names]

    # --- Anmeldung -----------------------------------------------------------------------

    def login(
        self, username: str, password: str, *, totp: str | None, remember: bool
    ) -> NewSession:
        key = username.strip().lower()[:64]
        # Prüfen der Sperre, Passwortprüfung und Zählen des Fehlversuchs ohne Wettlauf
        with self._login_locks[hash(key) % LOGIN_STRIPES]:
            return self._login(key, password, totp=totp, remember=remember)

    def _login(self, key: str, password: str, *, totp: str | None, remember: bool) -> NewSession:
        now = self._clock.now()
        until = self._locked_until(key)
        if until is not None and now < until:
            raise LockedError(until, now)  # in der Sperre wird nichts geprüft oder gezählt
        with self._engine.connect() as conn:
            row = self._find(conn, key)
        if row is None:
            self._verify(self._dummy_hash, password)
            self._fail(key, now)
            raise InvalidCredentialsError()
        if not self._verify(row.password_hash, password):
            self._fail(key, now)
            raise InvalidCredentialsError()
        step: int | None = None
        if row.totp_enabled:
            if not totp:
                raise TotpRequiredError()
            step = self._totp_step(row.totp_secret, totp, row.totp_last_step, now)
            if step is None:
                self._fail(key, now)
                raise InvalidCredentialsError()
        rehash = (
            self._hash(password) if self._hasher.check_needs_rehash(row.password_hash) else None
        )
        session: NewSession | None = None
        with self._engine.begin() as conn:
            # der Schritt wird nur einmal angenommen, auch bei gleichzeitigen Anmeldungen
            if step is None or self._consume_step(conn, row.id, step):
                conn.execute(
                    delete(login_failure_table).where(login_failure_table.c.username == key)
                )
                if rehash is not None:
                    conn.execute(
                        update(user_table)
                        .where(user_table.c.id == row.id)
                        .values(password_hash=rehash)
                    )
                session = self._create_session(conn, _user(row), remember, now)
        if session is None:
            self._fail(key, now)
            raise InvalidCredentialsError()
        return session

    def resolve(self, token: str, *, touch: bool = True) -> SessionInfo | None:
        sid = hash_token(token)
        now = self._clock.now()
        query = (
            select(
                session_table.c.csrf_token,
                session_table.c.last_seen_at,
                session_table.c.expires_at,
                session_table.c.remember,
                user_table.c.id,
                user_table.c.username,
                user_table.c.role,
                user_table.c.totp_enabled,
                user_table.c.created_at,
            )
            .join(user_table, user_table.c.id == session_table.c.user_id)
            .where(session_table.c.id == sid)
        )
        with self._engine.begin() as conn:
            row = conn.execute(query).first()
            if row is None:
                return None
            if now >= row.expires_at:
                conn.execute(delete(session_table).where(session_table.c.id == sid))
                return None
            expires_at: datetime = row.expires_at
            if touch and now - row.last_seen_at >= TOUCH_EVERY:
                values: dict[str, Any] = {"last_seen_at": now}
                if not row.remember:
                    expires_at = now + IDLE_TIMEOUT
                    values["expires_at"] = expires_at
                conn.execute(
                    update(session_table).where(session_table.c.id == sid).values(**values)
                )
        return SessionInfo(sid, row.csrf_token, expires_at, bool(row.remember), _user(row))

    def logout(self, token: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(session_table).where(session_table.c.id == hash_token(token)))

    def cleanup(self) -> int:
        """Löscht abgelaufene Sitzungen und alte Fehlversuche; liefert die Zahl der Sitzungen."""
        now = self._clock.now()
        with self._engine.begin() as conn:
            sessions = conn.execute(delete(session_table).where(session_table.c.expires_at <= now))
            conn.execute(
                delete(login_failure_table).where(
                    login_failure_table.c.ts < now - FAILURE_RETENTION
                )
            )
        return int(sessions.rowcount)

    # --- TOTP ----------------------------------------------------------------------------

    def totp_begin(self, user_id: int) -> tuple[str, str]:
        secret = pyotp.random_base32()
        with self._engine.begin() as conn:
            row = self._get(conn, user_id)
            if row.totp_enabled:
                raise TotpAlreadyEnabledError()  # sonst ließe sich TOTP ohne Passwort abschalten
            conn.execute(
                update(user_table)
                .where(user_table.c.id == user_id)
                .values(totp_secret=secret, totp_enabled=False, totp_last_step=None)
            )
        uri = pyotp.TOTP(secret).provisioning_uri(name=row.username, issuer_name=TOTP_ISSUER)
        return secret, uri

    def totp_enable(self, user_id: int, code: str, *, actor: str, source: Source) -> None:
        now = self._clock.now()
        with self._engine.begin() as conn:
            row = self._get(conn, user_id)
            if row.totp_secret is None:
                raise InvalidTotpError()
            step = self._totp_step(row.totp_secret, code, row.totp_last_step, now)
            if step is None:
                raise InvalidTotpError()
            conn.execute(
                update(user_table)
                .where(user_table.c.id == user_id)
                .values(totp_enabled=True, totp_last_step=step, updated_at=now)
            )
            self._audit.record(actor, source, "user.totp_enable", row.username, conn=conn)

    def totp_disable(self, user_id: int, password: str, *, actor: str, source: Source) -> None:
        if not self.verify_password(user_id, password):
            raise InvalidCredentialsError()
        with self._engine.begin() as conn:
            row = self._get(conn, user_id)
            conn.execute(
                update(user_table)
                .where(user_table.c.id == user_id)
                .values(
                    totp_secret=None,
                    totp_enabled=False,
                    totp_last_step=None,
                    updated_at=self._clock.now(),
                )
            )
            self._audit.record(actor, source, "user.totp_disable", row.username, conn=conn)

    # --- intern --------------------------------------------------------------------------

    def _hash(self, password: str) -> str:
        with self._hash_slots:
            return self._hasher.hash(password)

    def _verify(self, password_hash: str, password: str) -> bool:
        if len(password) > MAX_PASSWORD_LEN:
            return False
        try:
            with self._hash_slots:
                return self._hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    def _totp_step(
        self, secret: str, code: str, last_step: int | None, now: datetime
    ) -> int | None:
        totp = pyotp.TOTP(secret)
        current = totp.timecode(now)
        given = code.strip().encode()
        for step in (current - 1, current, current + 1):
            if last_step is not None and step <= last_step:
                continue  # Code schon verwendet (Wiederholung)
            if hmac.compare_digest(totp.generate_otp(step).encode(), given):
                return step
        return None

    def _consume_step(self, conn: Connection, user_id: int, step: int) -> bool:
        """Merkt sich den TOTP-Schritt; ``False``, wenn ihn schon eine andere Anmeldung nutzte."""
        result = conn.execute(
            update(user_table)
            .where(
                user_table.c.id == user_id,
                (user_table.c.totp_last_step.is_(None)) | (user_table.c.totp_last_step < step),
            )
            .values(totp_last_step=step)
        )
        return bool(result.rowcount == 1)

    def _locked_until(self, key: str) -> datetime | None:
        query = (
            select(login_failure_table.c.ts)
            .where(login_failure_table.c.username == key)
            .order_by(login_failure_table.c.ts.desc())
            .limit(LOCKOUT_FAILURES)
        )
        with self._engine.connect() as conn:
            recent: list[datetime] = list(conn.execute(query).scalars().all())
        if len(recent) < LOCKOUT_FAILURES:
            return None
        newest, oldest = recent[0], recent[-1]
        if newest - oldest > LOCKOUT_WINDOW:
            return None
        return newest + LOCKOUT_FOR

    def _fail(self, key: str, now: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(insert(login_failure_table).values(username=key, ts=now))

    def _create_session(
        self, conn: Connection, user: User, remember: bool, now: datetime
    ) -> NewSession:
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        remember = remember and user.role != "admin"  # Admins: immer 30 min Leerlauf
        expires_at = now + (REMEMBER_FOR if remember else IDLE_TIMEOUT)
        conn.execute(
            insert(session_table).values(
                id=hash_token(token),
                user_id=user.id,
                csrf_token=csrf_token,
                created_at=now,
                last_seen_at=now,
                expires_at=expires_at,
                remember=remember,
            )
        )
        return NewSession(token, csrf_token, expires_at, remember, user)

    @staticmethod
    def _find(conn: Connection, name: str) -> _Row | None:
        return conn.execute(select(user_table).where(user_table.c.username == name)).first()

    @staticmethod
    def _get(conn: Connection, user_id: int) -> _Row:
        row = conn.execute(select(user_table).where(user_table.c.id == user_id)).first()
        if row is None:
            raise UserNotFoundError()
        return row

    @staticmethod
    def _admin_count(conn: Connection) -> int:
        query = select(func.count()).select_from(user_table).where(user_table.c.role == "admin")
        return int(conn.execute(query).scalar_one())
