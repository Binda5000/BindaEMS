"""Strukturierte JSON-Logs auf stdout mit UTC-Zeitstempeln (auch für stdlib-Logger)."""

from __future__ import annotations

import logging
import sys

import structlog

QUIET_LOGGERS = ("httpx", "httpcore", "uvicorn.error", "uvicorn.access")


def configure_logging(level: str = "INFO") -> None:
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
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=False,
    )
    handler = logging.StreamHandler(sys.stdout)
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
