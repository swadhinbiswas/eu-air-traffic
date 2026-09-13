"""Structured logging setup shared across the application."""

import logging
import re
import sys
from logging import Logger

from rich.logging import RichHandler

from config.settings import settings

_LOGGER_NAME = "air_traffic"


_SECRET_RE = re.compile(
    r"(access_key|appid|api_key|apikey|apiToken|api_token|client_secret|token)=([^&\s\"']+)",
    re.IGNORECASE,
)


class _RedactSecrets(logging.Filter):
    """Keep keyed credentials out of logs.

    ``requests`` exception strings include the full request URL, so an API key
    passed as a query parameter ends up in journald on any failed call.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging
            return True
        redacted = _SECRET_RE.sub(r"\1=***", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


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

    scrubber = _RedactSecrets()
    for name in (None, "uvicorn", "uvicorn.error", "uvicorn.access"):
        target = logging.getLogger() if name is None else logging.getLogger(name)
        for handler in target.handlers:
            handler.addFilter(scrubber)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(log_level)
    return logger


logger = setup_logging()
