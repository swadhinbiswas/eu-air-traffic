"""Kafka event bus producer (Aiven, SASL_SSL).

Thin wrapper around ``kafka-python-ng`` that publishes the collector's events to
the Aiven Kafka topics. Designed to degrade gracefully: if Kafka is not
configured or unreachable, producers no-op and the collector keeps running so
Bronze ingestion and the live feed are never blocked by the event bus.
"""

from __future__ import annotations

import json
import ssl
from typing import Any

from config.logging import logger
from config.settings import Settings, settings


def kafka_client_config(s: Settings | None = None) -> dict[str, Any]:
    """Shared client config for Aiven (SASL_SSL) or a local PLAINTEXT broker."""
    cfg = s or settings
    common: dict[str, Any] = {
        "bootstrap_servers": f"{cfg.aiven_kafka_host}:{cfg.aiven_kafka_port}",
        "security_protocol": cfg.kafka_security_protocol,
    }
    if cfg.kafka_security_protocol == "PLAINTEXT":
        return common
    common.update(
        {
            "sasl_mechanism": cfg.kafka_sasl_mechanism,
            "sasl_plain_username": cfg.aiven_kafka_username,
            "sasl_plain_password": cfg.aiven_kafka_password,
        }
    )
    if cfg.aiven_kafka_ca_cert:
        common["ssl_context"] = ssl.create_default_context(cafile=cfg.aiven_kafka_ca_cert)
    return common


class KafkaBus:
    """Publishes records to Kafka; a safe no-op when unconfigured."""

    # Records queued per flush; keeps a single huge product (e.g. the hourly
    # forecast) from blocking the loop past the flush timeout.
    _CHUNK = 2_000

    def __init__(self, app_settings: Settings | None = None) -> None:
        self.settings = app_settings or settings
        self._producer: Any = None
        self._available = False

    @property
    def configured(self) -> bool:
        s = self.settings
        if not s.aiven_kafka_host:
            return False
        # PLAINTEXT (local Redpanda) needs no credentials.
        if s.kafka_security_protocol == "PLAINTEXT":
            return True
        return bool(s.aiven_kafka_username and s.aiven_kafka_password)

    @property
    def available(self) -> bool:
        return self._available

    def connect(self) -> bool:
        """Create the producer. Returns True when the bus is usable."""
        if self._producer is not None:
            return self._available
        if not self.configured:
            logger.warning("[kafka] not configured — events will not be published")
            return False
        try:
            from kafka import KafkaProducer  # local import: optional dependency
        except ImportError:
            logger.warning("[kafka] kafka-python-ng not installed — install the 'stream' extra")
            return False

        s = self.settings
        try:
            self._producer = KafkaProducer(
                value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
                key_serializer=lambda k: str(k).encode("utf-8") if k is not None else None,
                acks="all",
                retries=5,
                linger_ms=200,
                request_timeout_ms=30_000,
                max_block_ms=15_000,
                **kafka_client_config(s),
            )
            self._available = True
            logger.info("[kafka] connected to %s:%s", s.aiven_kafka_host, s.aiven_kafka_port)
            self.ensure_topics(list(dict.fromkeys(s.kafka_topics.values())))
        except Exception as exc:  # noqa: BLE001 - never block the collector
            logger.error("[kafka] connection failed: %s", exc)
            self._producer = None
            self._available = False
        return self._available

    def ensure_topics(self, topics: list[str]) -> list[str]:
        """Create missing topics and pin retention to ``kafka_retention_hours``.

        Retention must comfortably exceed the GitHub Actions pull interval, or
        records are deleted before the 9-minute job reads them.

        Best-effort: returns the topics created and keeps the collector running
        if the broker refuses a config change. Call once on startup.
        """
        if not self.configured:
            return []
        admin = None
        try:
            from kafka.admin import ConfigResource, ConfigResourceType, KafkaAdminClient, NewTopic

            retention_ms = str(self.settings.kafka_retention_hours * 3_600_000)
            topic_configs = {"retention.ms": retention_ms, "cleanup.policy": "delete"}
            admin = KafkaAdminClient(
                client_id="eu-air-traffic-collector", **kafka_client_config(self.settings)
            )
            existing = set(admin.list_topics())
            missing = [topic for topic in topics if topic not in existing]
            if missing:
                admin.create_topics(
                    [
                        NewTopic(
                            name=topic,
                            num_partitions=2,
                            replication_factor=1,
                            topic_configs=topic_configs,
                        )
                        for topic in missing
                    ]
                )
                logger.info("[kafka] created topics: %s", missing)
            try:
                admin.alter_configs(
                    [
                        ConfigResource(ConfigResourceType.TOPIC, topic, topic_configs)
                        for topic in topics
                    ]
                )
                logger.info("[kafka] retention set to %sh", self.settings.kafka_retention_hours)
            except Exception as exc:  # noqa: BLE001 - some plans forbid config changes
                logger.warning("[kafka] could not set retention (set it in Aiven): %s", exc)
            return missing
        except Exception as exc:  # noqa: BLE001
            logger.warning("[kafka] ensure_topics failed (create them manually): %s", exc)
            return []
        finally:
            if admin is not None:
                try:
                    admin.close()
                except Exception:  # noqa: BLE001
                    pass

    def produce(self, topic: str, records: list[dict[str, Any]], key: str = "id") -> int:
        """Publish records to a topic in chunks. Returns the number queued.

        Records are queued (Kafka's client batches them via ``linger_ms``) and
        flushed every ``_CHUNK`` records with a bounded timeout, so a slow broker
        delays the loop for at most ``kafka_flush_timeout_seconds`` per chunk
        instead of blocking on one huge batch.
        """
        if not records or self._producer is None:
            return 0
        sent = 0
        for start in range(0, len(records), self._CHUNK):
            for record in records[start : start + self._CHUNK]:
                try:
                    self._producer.send(topic, key=record.get(key), value=record)
                    sent += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error("[kafka] send to %s failed: %s", topic, exc)
                    return sent
            try:
                self._producer.flush(timeout=self.settings.kafka_flush_timeout_seconds)
            except Exception as exc:  # noqa: BLE001
                logger.error("[kafka] flush to %s timed out: %s", topic, exc)
        return sent

    def close(self) -> None:
        if self._producer is not None:
            try:
                self._producer.flush(timeout=10)
                self._producer.close(timeout=10)
            except Exception:  # noqa: BLE001
                pass
        self._producer = None
        self._available = False
