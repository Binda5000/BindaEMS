from bindaems.core.adapters.base import Backoff


def test_backoff_sequence_and_reset() -> None:
    b = Backoff()
    assert [b.next() for _ in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]
    b.reset()
    assert b.next() == 1
