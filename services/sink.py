"""Kafka sink — drains the event bus into the Bronze lake.

Consumes every data-product topic and writes two immutable artifacts under
``warehouse/bronze``:

* ``<source>/<date>/*.jsonl`` — raw records exactly as published.
* ``parquet/<source>/*.parquet`` — columnar consolidation for fast downstream reads.

Both are uploaded to the Hugging Face dataset (``bronze/…``). The same class runs
continuously on the VPS and in short-lived GitHub Actions drain jobs, so the lake
stays current whether or not a broker-side process is long-lived.

Run::

    python -m services.sink                 # drain until idle, then exit
    python -m services.sink --loop          # run forever (VPS)
    python -m services.sink --max-seconds 5400
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.logging import logger, setup_logging
from config.settings import Settings, settings
from services import hf_lake


class KafkaSink:
    """Consumes Kafka topics and persists Bronze JSONL + Parquet (+ HF upload)."""

    # Shared topics are split back into datasets by the record ``_kind``.
    _WEATHER_KINDS = {
        "metar": "weather",
        "taf": "weather_taf",
        "forecast": "weather_forecast",
    }
    # Reference records carry a ``_kind`` discriminator and are split per dataset.
    _REFERENCE_KINDS = {
        "airport": "airports",
        "route": "routes",
        "aircraft": "aircraft",
        "emission": "emissions",
        "holiday": "holidays",
    }
    # Topics from the retired 8-topic layout. Subscribed only while they still
    # exist so the backlog drains exactly once; afterwards the broker stops
    # returning them and the sink runs on the 5 live topics with no code change.
    LEGACY_TOPICS = ("eu-metar", "eu-taf", "eu-forecast", "eu-collect-meta")
    # Legacy records predate the ``_kind`` field; route them by record shape.
    _LEGACY_WEATHER_TOPICS = {
        "eu-metar": "weather",
        "eu-taf": "weather_taf",
        "eu-forecast": "weather_forecast",
    }

    def __init__(self, app_settings: Settings | None = None, upload: bool = True) -> None:
        self.settings = app_settings or settings
        self.upload = upload
        self._consumer: Any = None
        self._buffers: dict[str, list[dict[str, Any]]] = {}
        self.counts: dict[str, int] = {}
        self.files: list[Path] = []

    def _datasets_for(self, topic: str, value: dict[str, Any]) -> list[str]:
        """Resolve a Kafka message to one or more Bronze dataset names."""
        topics = self.settings.kafka_topics
        if topic == topics["weather"]:
            kind = str(value.get("_kind") or self._infer_weather_kind(value))
            return [self._WEATHER_KINDS.get(kind, "weather")]
        if topic == topics["reference"]:
            kind = str(value.get("_kind") or "")
            return [self._REFERENCE_KINDS.get(kind, "reference")]
        if topic in self._LEGACY_WEATHER_TOPICS:
            return [self._LEGACY_WEATHER_TOPICS[topic]]
        if topic == "eu-collect-meta":
            return []  # retired run-metadata topic; nothing consumes it
        # positions / flights / fuel land under their own dataset name.
        reverse = {name: role for role, name in topics.items()}
        return [reverse.get(topic, topic)]

    @staticmethod
    def _infer_weather_kind(value: dict[str, Any]) -> str:
        """Route kind-less records (pre-``_kind`` producers) by record shape."""
        if "raw_taf" in value:
            return "taf"
        if "is_forecast" in value:
            return "forecast"
        return "metar"

    # ── connection ─────────────────────────────────────────────────────────
    def _security(self) -> dict[str, Any]:
        from services.kafka_bus import kafka_client_config

        cfg = kafka_client_config(self.settings)
        cfg.pop("bootstrap_servers", None)
        return cfg

    def connect(self) -> bool:
        if not self.settings.kafka_enabled:
            logger.error("[sink] Kafka is not configured")
            return False
        try:
            from kafka import KafkaConsumer  # optional dependency
        except ImportError:
            logger.error("[sink] kafka-python-ng not installed (install the 'stream' extra)")
            return False

        s = self.settings
        topics = list(dict.fromkeys(s.kafka_topics.values()))
        try:
            self._consumer = KafkaConsumer(
                bootstrap_servers=f"{s.aiven_kafka_host}:{s.aiven_kafka_port}",
                group_id=s.sink_consumer_group,
                auto_offset_reset="earliest",
                # Commit only after records are safely written (see run()).
                enable_auto_commit=False,
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                consumer_timeout_ms=s.sink_poll_timeout_ms,
                **self._security(),
            )
            try:
                existing = set(self._consumer.topics())
            except Exception:  # noqa: BLE001 - fall back to the live topics only
                existing = set()
            legacy = [t for t in self.LEGACY_TOPICS if t in existing]
            if legacy:
                logger.info("[sink] also draining retired topics: %s", legacy)
            self._consumer.subscribe(topics + legacy)
            logger.info("[sink] subscribed to %s topics: %s", len(topics + legacy), topics + legacy)
            return True
        except Exception as exc:  # noqa: BLE001 - let the caller retry
            logger.error("[sink] connection failed: %s", exc)
            return False

    # ── persistence ────────────────────────────────────────────────────────
    def _write_jsonl(self, source: str, records: list[dict[str, Any]]) -> Path:
        now = datetime.now(UTC)
        out_dir = self.settings.bronze_dir / source / now.strftime("%Y-%m-%d")
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y-%m-%dT%H%M%S%fZ")
        path = out_dir / f"{source}_{stamp}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return path

    def _write_parquet(self, source: str, records: list[dict[str, Any]]) -> Path | None:
        try:
            import polars as pl

            frame = pl.DataFrame(records, infer_schema_length=None)
        except Exception as exc:  # noqa: BLE001
            logger.error("[sink] parquet build for %s failed: %s", source, exc)
            return None
        out_dir = self.settings.bronze_dir / "parquet" / source
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%S%fZ")
        path = out_dir / f"{source}_{stamp}.parquet"
        frame.write_parquet(path, compression="zstd")
        return path

    def flush(self, source: str) -> int:
        records = self._buffers.pop(source, [])
        if not records:
            return 0
        jsonl = self._write_jsonl(source, records)
        parquet = self._write_parquet(source, records)
        self.files.extend(p for p in (jsonl, parquet) if p is not None)
        self.counts[source] = self.counts.get(source, 0) + len(records)
        if self.upload:
            prefix = self.settings.hf_bronze_prefix
            hf_lake.upload_file(
                jsonl, f"{prefix}/raw/{source}/{jsonl.parent.name}/{jsonl.name}", self.settings
            )
            if parquet is not None:
                hf_lake.upload_file(
                    parquet, f"{prefix}/parquet/{source}/{parquet.name}", self.settings
                )
        logger.info("[sink] %s rows=%s → %s", source, len(records), jsonl.name)
        return len(records)

    # ── run ────────────────────────────────────────────────────────────────
    def run(
        self, max_seconds: float | None = None, window_seconds: float | None = None
    ) -> dict[str, int]:
        """Drain the bus until caught up (or ``max_seconds``/``window_seconds``).

        A single run reads the backlog since the last committed offset, so the
        GitHub Actions job can be short-lived and stateless. ``window_seconds``
        caps how far the consumer will follow the log, leaving headroom before
        the next scheduled run (defaults to ``lake_window_seconds``).
        """
        if not self.connect():
            return {}
        started = time.monotonic()
        max_batch = self.settings.sink_batch_size
        budget = window_seconds or max_seconds or float(self.settings.lake_window_seconds)

        try:
            for message in self._consumer:
                value = message.value
                if not isinstance(value, dict):
                    continue
                for source in self._datasets_for(message.topic, value):
                    buffer = self._buffers.setdefault(source, [])
                    buffer.append(value)
                    if len(buffer) >= max_batch:
                        self.flush(source)
                        self._commit()
                if time.monotonic() - started >= budget:
                    logger.info("[sink] window %.0fs reached — stopping this run", budget)
                    break
        finally:
            for source in list(self._buffers):
                self.flush(source)
            # Commit offsets only when records were actually written, so a run
            # that read nothing (e.g. still connecting) never advances the
            # cursor and silently skips data. Silver dedupes, so at-least-once
            # is safe.
            if any(self.counts.values()):
                self._commit()
            else:
                logger.info("[sink] nothing consumed — offsets left unchanged")
            if self._consumer is not None:
                self._consumer.close()
        return self.counts

    def _commit(self) -> None:
        if self._consumer is None:
            return
        try:
            self._consumer.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[sink] offset commit failed: %s", exc)


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Kafka → Bronze → Hugging Face sink")
    parser.add_argument("--loop", action="store_true", help="run forever (not used on the VPS)")
    parser.add_argument(
        "--window-seconds",
        type=float,
        default=None,
        help="max seconds to follow the log this run (default: lake_window_seconds)",
    )
    parser.add_argument(
        "--max-seconds", type=float, default=None, help="alias for --window-seconds"
    )
    parser.add_argument("--no-upload", action="store_true", help="skip the Hugging Face upload")
    args = parser.parse_args()

    sink = KafkaSink(upload=not args.no_upload)
    if args.loop:
        while True:
            try:
                sink.run(window_seconds=args.window_seconds or args.max_seconds)
            except KeyboardInterrupt:  # pragma: no cover - interactive stop
                return 0
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                logger.error("[sink] cycle failed: %s", exc)
    counts = sink.run(max_seconds=args.max_seconds, window_seconds=args.window_seconds)
    if not counts:
        logger.warning("[sink] no records written")
        return 1
    logger.info("[sink] written: %s", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
