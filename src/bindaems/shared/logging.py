"""Strukturierte JSON-Logs mit UTC-Zeitstempeln (auch für stdlib-Logger), auf stdout oder stderr."""

from __future__ import annotations

import logging
import sys
from typing import TextIO

import structlog

QUIET_LOGGERS = ("httpx", "httpcore", "uvicorn.error", "uvicorn.access")


def configure_logging(level: str = "INFO", *, stream: TextIO | None = None) -> None:
    """Stellt die Logs ein. ``stream`` ist ohne Angabe die Standardausgabe (core und app); die
    Werkzeuge geben ``sys.stderr`` an, damit auf der Standardausgabe nur ihre Zeilen stehen."""
    target = stream if stream is not None else sys.stdout
    numeric = logging.getLevelNamesMapping()[level.upper()]
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            timestamper,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=structlog.PrintLoggerFactory(target),
        cache_logger_on_first_use=False,
    )
    handler = logging.StreamHandler(target)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[
                structlog.stdlib.add_log_level,
                structlog.stdlib.add_logger_name,
                timestamper,
            ],
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(numeric)
    # je Anfrage eine Zeile (InfluxDB jede Sekunde, Wall Connector alle 2 s) und jeder
    # WebSocket-Aufbau – das füllte die Logrotation mit Rauschen
    for noisy in QUIET_LOGGERS:
        logging.getLogger(noisy).setLevel(max(numeric, logging.WARNING))
