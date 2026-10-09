from bindaems.shared.retry import Backoff


def test_backoff_sequence_and_reset() -> None:
    b = Backoff()
    assert [b.next() for _ in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]
    b.reset()
    assert b.next() == 1


def test_status_log_reports_changes_and_throttles_repeats() -> None:
    from structlog.testing import capture_logs

    from bindaems.shared.retry import StatusLog

    now = [0.0]
    status = StatusLog("evcs", monotonic=lambda: now[0])
    with capture_logs() as logs:
        status.connected()
        status.connected()  # unverändert: keine Zeile
        status.failed("TimeoutError: weg", 1.0)
        now[0] = 100.0
        status.failed("TimeoutError: weg", 2.0)  # gleicher Fehler < 5 min: keine Zeile
        status.failed("ConnectionError: abgelehnt", 4.0)  # anderer Fehler: neue Zeile
        now[0] = 500.0
        status.failed("ConnectionError: abgelehnt", 8.0)  # nach 5 min erneut
        status.connected()
    assert [(e["event"], e.get("error")) for e in logs] == [
        ("Adapter verbunden", None),
        ("Adapter-Fehler", "TimeoutError: weg"),
        ("Adapter-Fehler", "ConnectionError: abgelehnt"),
        ("Adapter-Fehler", "ConnectionError: abgelehnt"),
        ("Adapter verbunden", None),
    ]
    assert all(e["adapter"] == "evcs" for e in logs)
    assert logs[1]["log_level"] == "warning" and logs[1]["retry_in_s"] == 1.0
