"""Logging estructurado con structlog. JSON por defecto.

Regla de seguridad (5.6): nunca loggear secretos, ni enmascarados.
"""

from __future__ import annotations

import logging
from typing import cast

import structlog
from structlog.typing import FilteringBoundLogger, Processor


def configure_logging(*, level: str = "INFO", json_logs: bool = True) -> None:
    level_no = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=level_no)

    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level_no),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "olibuguard") -> FilteringBoundLogger:
    return cast(FilteringBoundLogger, structlog.get_logger(name))
