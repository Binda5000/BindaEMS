from datetime import datetime

import pytest
from sqlalchemy import Engine, insert
from sqlalchemy.exc import StatementError
from tests.app_helpers import T_APP

from bindaems.app.audit import AuditLog
from bindaems.app.db.schema import audit_log
from bindaems.shared.timeutil import ManualClock


def test_audit_lists_newest_first_with_aware_timestamps(
    audit: AuditLog, clock: ManualClock
) -> None:
    audit.record("chris", "ui", "settings.update", "einstellungen", {"version": 2})
    clock.advance(60)
    audit.record("cli", "cli", "user.create", "chris")
    first, second = audit.recent()
    assert (first.action, second.action) == ("user.create", "settings.update")
    assert second.details == {"version": 2} and second.ts == T_APP
    assert second.ts.tzinfo is not None and second.target == "einstellungen"


def test_audit_limit(audit: AuditLog) -> None:
    for n in range(3):
        audit.record("chris", "ui", f"a{n}")
    assert [e.action for e in audit.recent(limit=2)] == ["a2", "a1"]


def test_naive_timestamps_are_rejected(engine: Engine) -> None:
    naive = datetime(2026, 10, 9)  # absichtlich ohne Zeitzone
    with pytest.raises(StatementError, match="naiver Zeitpunkt"), engine.begin() as conn:
        conn.execute(insert(audit_log).values(ts=naive, actor="x", source="ui", action="y"))
