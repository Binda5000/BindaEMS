"""HTTP-Abruf externer Quellen; die rohe Antwort bleibt zur Archivierung erhalten."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

import httpx

from bindaems import __version__
from bindaems.shared.timeutil import Clock

TIMEOUT_S = 15.0
USER_AGENT = f"BindaEMS/{__version__}"


@dataclass(frozen=True)
class RawResponse:
    source: str
    url: str
    fetched_at: datetime
    status: int
    body: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class FetchError(Exception):
    """Keine Antwort erhalten (Netzwerk, Zeitüberschreitung, TLS)."""


async def get_raw(
    http: httpx.AsyncClient,
    clock: Clock,
    source: str,
    url: str,
    params: Mapping[str, str | int | float],
) -> RawResponse:
    """GET mit eigenem User-Agent; HTTP-Fehlerstatus kommen als Antwort zurück."""
    try:
        response = await http.get(
            url, params=dict(params), headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S
        )
    except httpx.HTTPError as exc:
        raise FetchError(f"{source}: {type(exc).__name__}: {exc}") from exc
    return RawResponse(
        source=source,
        url=str(response.request.url),
        fetched_at=clock.now(),
        status=response.status_code,
        body=response.text,
    )
