"""Shared HTTP / file-writing utilities for ingestion collectors.

Contains:
- ``retry`` / ``AsyncRetryState``: exponential-backoff retry decorators.
- ``atomic_write_json``: crash-safe file writes via temp file + rename.
- ``iter_nested``: deep traversal helper for heterogeneous JSON payloads.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from functools import wraps
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

import requests

from config.logging import logger
from config.settings import settings

P = ParamSpec("P")
R = TypeVar("R")


class RetryExhausted(RuntimeError):
    """Raised when all retry attempts for an upstream call fail."""


def retry(
    exceptions: tuple[type[BaseException], ...] = (requests.RequestException,),
    max_attempts: int | None = None,
    backoff_base: float | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Retry a synchronous callable with exponential backoff + jitter.

    Respects ``settings.max_retries`` / ``settings.retry_backoff_base`` unless
    overridden explicitly.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            attempts = max_attempts if max_attempts is not None else settings.max_retries + 1
            base = backoff_base if backoff_base is not None else settings.retry_backoff_base

            last_error: BaseException | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_error = exc
                    if attempt == attempts:
                        break
                    import random

                    delay = (base ** (attempt - 1)) + random.uniform(0, 0.5)
                    logger.warning(
                        "Retry %s/%s for %s failed: %s (retrying in %.2fs)",
                        attempt,
                        attempts,
                        getattr(func, "__name__", "callable"),
                        exc,
                        delay,
                    )
                    time_sleep(delay)

            raise RetryExhausted(str(last_error)) from last_error

        return wrapper

    return decorator


def time_sleep(seconds: float) -> None:
    """Sleep that works under an active event loop (blocks, safe for sync code)."""
    import time

    time.sleep(seconds)


class AsyncRetryState:
    """Async equivalent of :func:`retry` for use inside worker loops."""

    def __init__(self, max_attempts: int | None = None, backoff_base: float | None = None) -> None:
        self.max_attempts = max_attempts or settings.max_retries + 1
        self.base = backoff_base or settings.retry_backoff_base
        self.attempt = 0

    async def should_retry(self, error: BaseException) -> bool:
        self.attempt += 1
        if self.attempt >= self.max_attempts:
            return False
        import random

        delay = (self.base ** (self.attempt - 1)) + random.uniform(0, 0.5)
        logger.warning("Async retry %s/%s after error: %s", self.attempt, self.max_attempts, error)
        await asyncio.sleep(delay)
        return True


def atomic_write_json(path: Path, payload: Any, *, indent: int = 2) -> Path:
    """Write ``payload`` as JSON to ``path`` atomically.

    Writes to a temp file in the same directory first, then renames over the
    destination so a crash never leaves a truncated file behind.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=indent, ensure_ascii=False, default=str)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    return path


def iter_nested(data: Any, key: str) -> Iterator[Any]:
    """Yield every value found for ``key`` at any depth of nested dicts/lists.

    Useful for APIs that wrap collections unpredictably.
    """
    if isinstance(data, dict):
        if key in data:
            yield data[key]
        for value in data.values():
            yield from iter_nested(value, key)
    elif isinstance(data, list):
        for item in data:
            yield from iter_nested(item, key)


def safe_get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    """Dot-path accessor, e.g. ``safe_get(d, "a.b.c")``."""
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def to_float(value: Any) -> float | None:
    """Best-effort numeric coercion used before storing measurements."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None
