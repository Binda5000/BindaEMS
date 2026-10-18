"""Lesender InfluxDB-1.x-Client (InfluxQL über ``/query``)."""

from __future__ import annotations

import re
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


_READING = re.compile(r"\s*(SELECT|SHOW)\b", re.IGNORECASE)
_INTO = re.compile(r"\bINTO\b", re.IGNORECASE)
# Nach diesen Wörtern beginnt mit ``/`` ein regulärer Ausdruck (wie im Scanner von InfluxQL)
_REGEX_FOLLOWS = re.compile(r"(=~|!~|\bFROM)\s*$", re.IGNORECASE)


def _statements(q: str) -> list[str]:
    """Die Anweisungen in ``q``, geteilt an ``;``, je als Text außerhalb der Literale:
    Zeichenketten, Bezeichner in Anführungszeichen und reguläre Ausdrücke stehen als ``_`` da, ein
    ``;`` darin teilt nicht. Ein Literal ohne Ende und Kommentare lehnt sie ab (``ValueError``),
    damit nichts an der Prüfung vorbei in eine weitere Anweisung gerät."""
    statements: list[str] = []
    bare: list[str] = []
    i = 0
    while i < len(q):
        char = q[i]
        if char in "'\"" or (char == "/" and _REGEX_FOLLOWS.search("".join(bare))):
            end = i + 1
            while end < len(q) and q[end] != char:
                end += 2 if q[end] == "\\" else 1
            if end >= len(q):
                raise ValueError(f"InfluxQL: {char} ohne Ende")
            bare.append(" _ ")
            i = end + 1
        elif q.startswith(("--", "/*"), i):
            raise ValueError("InfluxQL: Kommentare sind nicht erlaubt")
        elif char == ";":
            statements.append("".join(bare))
            bare = []
            i += 1
        else:
            bare.append(char)
            i += 1
    statements.append("".join(bare))
    return [statement for statement in statements if statement.strip()]


def check_reading(q: str) -> None:
    """Lässt nur lesende Anweisungen durch: jede beginnt mit ``SELECT`` oder ``SHOW`` und enthält
    kein ``INTO``. Per POST führt InfluxDB 1.x auch ``DROP``, ``DELETE`` und ``SELECT … INTO`` aus,
    per GET nicht; diese Prüfung ersetzt die Nur-Lesen-Sperre von GET (Festlegung N10)."""
    statements = _statements(q)
    if not statements:
        raise ValueError("InfluxQL: leere Abfrage")
    for statement in statements:
        if _READING.match(statement) is None or _INTO.search(statement) is not None:
            raise ValueError(
                f"InfluxQL: nur SELECT und SHOW ohne INTO erlaubt, nicht {statement.strip()[:60]!r}"
            )


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
        """Schickt ``q`` per POST im Formular-Body (InfluxDB 1.x nimmt so auch SELECT und SHOW an):
        eine lange Abfrage, etwa der Abgleich über 36 Mitternächte, passt so durch jeden Proxy,
        der URLs auf wenige KB begrenzt. Nimmt nur lesende Anweisungen an (``check_reading``,
        sonst ``ValueError``)."""
        check_reading(q)
        params = {"db": database or self._database, "epoch": "ms"}
        try:
            response = await self._http.post(
                self._url,
                params=params,
                data={"q": q},
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
