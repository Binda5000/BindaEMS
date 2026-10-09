from datetime import datetime

import pytest

from bindaems.app.schedule import next_local
from bindaems.shared.timeutil import LOCAL_TZ


def local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=LOCAL_TZ)


def test_next_local_finds_first_matching_minute() -> None:
    now = local("2026-10-09T10:00:30")
    assert next_local(now, lambda t: t.minute == 7) == local("2026-10-09T10:07")
    assert next_local(local("2026-10-09T10:07"), lambda t: t.minute == 7) == local(
        "2026-10-09T11:07"
    )


def test_next_local_gives_up_after_limit() -> None:
    with pytest.raises(ValueError, match="60"):
        next_local(local("2026-10-09T10:00"), lambda t: False, limit_minutes=60)
