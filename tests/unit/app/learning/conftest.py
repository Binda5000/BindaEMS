from collections.abc import Callable

import pytest

from bindaems.app.history.influx import InfluxQueryError, Series


class FakeReader:
    """Ersatz für ``InfluxReader``: antwortet je nach Textteil der Abfrage und merkt sich sie."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, str | None]] = []
        self._answers: list[tuple[str, list[Series] | Exception]] = []

    def answer(self, fragment: str, result: list[Series] | Exception) -> None:
        self._answers.append((fragment, result))

    def texts(self, fragment: str) -> list[str]:
        return [q for q, _ in self.queries if fragment in q]

    async def query(self, q: str, *, database: str | None = None) -> list[Series]:
        self.queries.append((q, database))
        for fragment, result in self._answers:
            if fragment in q:
                if isinstance(result, Exception):
                    raise result
                return result
        return []


@pytest.fixture
def fake_reader() -> FakeReader:
    return FakeReader()


@pytest.fixture
def unreadable() -> Callable[[], InfluxQueryError]:
    return lambda: InfluxQueryError('InfluxDB: HTTP 403 {"error":"error authorizing query"}')
