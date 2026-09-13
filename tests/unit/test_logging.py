"""Log scrubbing: keyed credentials must not reach journald."""

from __future__ import annotations

import logging


def test_query_secrets_are_redacted() -> None:
    from config.logging import _RedactSecrets

    record = logging.LogRecord(
        "ingestion.fuel",
        logging.WARNING,
        __file__,
        1,
        "GET https://api.aviationstack.com/v1/flights?access_key=SUPERSECRET&limit=1 failed",
        None,
        None,
    )
    assert _RedactSecrets().filter(record) is True
    message = record.getMessage()
    assert "SUPERSECRET" not in message
    assert "access_key=***" in message


def test_plain_messages_pass_through_unchanged() -> None:
    from config.logging import _RedactSecrets

    record = logging.LogRecord("x", logging.INFO, __file__, 1, "collected %s rows", ("12",), None)
    _RedactSecrets().filter(record)
    assert record.getMessage() == "collected 12 rows"
