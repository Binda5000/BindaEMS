"""Verteilt Nachrichten an alle Abonnenten (interner Live-Stream)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any

Message = dict[str, Any]


class Broadcast:
    """``publish`` blockiert nie: Ein langsamer Abonnent verliert seine älteste Nachricht."""

    def __init__(self, maxsize: int = 100) -> None:
        self._maxsize = maxsize
        self._queues: set[asyncio.Queue[Message]] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)

    def publish(self, msg: Message) -> None:
        for queue in self._queues:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(msg)

    def subscribe(self) -> AbstractAsyncContextManager[AsyncIterator[Message]]:
        return self._subscription()

    @contextlib.asynccontextmanager
    async def _subscription(self) -> AsyncIterator[AsyncIterator[Message]]:
        queue: asyncio.Queue[Message] = asyncio.Queue(self._maxsize)
        self._queues.add(queue)
        try:
            yield _drain(queue)
        finally:
            self._queues.discard(queue)


async def _drain(queue: asyncio.Queue[Message]) -> AsyncIterator[Message]:
    while True:
        yield await queue.get()
