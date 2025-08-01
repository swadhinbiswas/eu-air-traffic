"""Structured logging setup shared across the application."""

import logging
import sys
from logging import Logger

from rich.logging import RichHandler

from config.settings import settings

_LOGGER_NAME = "air_traffic"


def setup_logging(level: str | int | None = None) -> Logger:
    """Configure and return the application logger.

    Uses the ``rich`` handler for human-readable output in development and a
    plain handler (machine-parseable) in production/test environments.
    """
    log_level = level or settings.log_level
    handlers: list[logging.Handler]

    if settings.environment == "production":
        handlers = [
            logging.StreamHandler(sys.stdout)  # structured plain logs for container collection
        ]
    else:
        handlers = [RichHandler(rich_tracebacks=True)]

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        handlers=handlers,
        force=True,
    )

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(log_level)
    return logger


logger = setup_logging()
