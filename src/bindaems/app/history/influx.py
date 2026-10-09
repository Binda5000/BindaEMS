"""Lesender InfluxDB-1.x-Client (InfluxQL über ``/query``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import SecretStr

from bindaems.shared.config import InfluxConfig

RP_RAW = "raw"
RP_LONG = "long"
TIMEOUT_S = 15.0


@dataclass(frozen=True)
class Series:
    name: str
    tags: Mapping[str, str]
    columns: list[str]
    values: list[list[Any]]


class InfluxQueryError(Exception):
    """Abfrage gescheitert (Netz, HTTP-Status oder Fehler im Ergebnis)."""


def quote_ident(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def quote_str(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


class InfluxReader:
    def __init__(
        self, cfg: InfluxConfig, password: SecretStr | None, http: httpx.AsyncClient
    ) -> None:
        self._url = cfg.url.rstrip("/") + "/query"
        self._database = cfg.database
        self._auth = (
            (cfg.username, password.get_secret_value() if password is not None else "")
            if cfg.username is not None
            else None
        )
        self._http = http

    async def query(self, q: str, *, database: str | None = None) -> list[Series]:
        params = {"db": database or self._database, "q": q, "epoch": "ms"}
        try:
            response = await self._http.get(
                self._url,
                params=params,
                auth=self._auth if self._auth is not None else httpx.USE_CLIENT_DEFAULT,
                timeout=TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            raise InfluxQueryError(
                f"InfluxDB nicht erreichbar: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code != 200:
            raise InfluxQueryError(f"InfluxDB: HTTP {response.status_code} {response.text[:200]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise InfluxQueryError("InfluxDB: Antwort ist kein JSON") from exc
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise InfluxQueryError("InfluxDB: Antwort ohne results")
        series: list[Series] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            if "error" in result:
                raise InfluxQueryError(f"InfluxDB: {result['error']}")
            for item in result.get("series", []):
                series.append(
                    Series(
                        name=str(item.get("name", "")),
                        tags={str(k): str(v) for k, v in item.get("tags", {}).items()},
                        columns=[str(column) for column in item.get("columns", [])],
                        values=[list(row) for row in item.get("values", [])],
                    )
                )
        return series
