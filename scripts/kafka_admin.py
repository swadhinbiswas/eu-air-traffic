"""Kafka connectivity, topic bootstrap and round-trip verification.

Production tooling for the event bus. Aiven disables automatic topic creation on
some plans, so run ``create-topics`` once after provisioning.

The platform uses exactly 5 topics (the free-plan limit): eu-positions,
eu-flights, eu-weather (metar+taf+forecast, split by ``_kind``), eu-fuel and
eu-reference. ``delete-legacy-topics`` removes the topics from the old 8-topic
layout (eu-metar, eu-taf, eu-forecast, eu-collect-meta) to free the slots.

Usage::

    python -m scripts.kafka_admin check                  # connect + report broker/topics
    python -m scripts.kafka_admin create-topics          # create missing topics
    python -m scripts.kafka_admin delete-legacy-topics   # drop the old 8-topic layout
    python -m scripts.kafka_admin roundtrip              # produce + consume a test record
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from config.logging import logger
from config.settings import settings
from services.kafka_bus import kafka_client_config

TOPICS = list(dict.fromkeys(settings.kafka_topics.values()))


def _client_setting() -> dict:
    cfg = kafka_client_config(settings)
    # The admin/consumer clients don't take a value_deserializer etc.
    return cfg


def check() -> int:
    if not settings.kafka_enabled:
        print("Kafka is not configured — set AIVEN_KAFKA_* in .env")
        return 1
    try:
        from kafka import KafkaAdminClient, KafkaProducer
    except ImportError:
        print("kafka-python-ng is not installed (pip install 'kafka-python-ng>=2.2.0')")
        return 1

    cfg = _client_setting()
    print(f"bootstrap: {cfg['bootstrap_servers']}")
    print(f"security:  {cfg['security_protocol']}")

    admin = KafkaAdminClient(client_id="eu-air-traffic-admin", **cfg)
    try:
        existing = sorted(admin.list_topics())
        print(f"topics ({len(existing)}): {', '.join(existing) or '(none)'}")
        missing = [t for t in TOPICS if t not in existing]
        print(f"required missing: {', '.join(missing) or '(none)'}")
    finally:
        admin.close()

    producer = KafkaProducer(
        client_id="eu-air-traffic-check",
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        request_timeout_ms=20_000,
        **cfg,
    )
    meta = producer.partitions_for(settings.kafka_topic_positions)
    print(f"partitions for {settings.kafka_topic_positions}: {len(meta) if meta else 0}")
    producer.close()
    print("Kafka reachable ✓")
    return 0


# Aiven's free tier caps partitions at 2 per user topic.
DEFAULT_PARTITIONS = 2


def create_topics(partitions: int = DEFAULT_PARTITIONS) -> int:
    if not settings.kafka_enabled:
        print("Kafka is not configured")
        return 1
    from kafka.admin import KafkaAdminClient, NewTopic

    admin = KafkaAdminClient(client_id="eu-air-traffic-admin", **_client_setting())
    try:
        existing = set(admin.list_topics())
        to_create = [t for t in TOPICS if t not in existing]
        if not to_create:
            print("all topics already exist")
            return 0
        admin.create_topics(
            [NewTopic(name=t, num_partitions=partitions, replication_factor=1) for t in to_create]
        )
        print(f"created: {', '.join(to_create)}")
    finally:
        admin.close()
    return 0


def roundtrip() -> int:
    """Produce one record to eu-positions and read it back."""
    if not settings.kafka_enabled:
        print("Kafka is not configured")
        return 1
    from kafka import KafkaConsumer, KafkaProducer

    cfg = _client_setting()
    topic = settings.kafka_topic_positions
    probe = {
        "icao24": "ABCDEF",
        "callsign": "PROBE1",
        "latitude": 50.0,
        "longitude": 8.0,
        "source": "kafka-roundtrip",
        "collected_at": datetime.now(UTC).isoformat(),
    }

    producer = KafkaProducer(
        client_id="eu-air-traffic-roundtrip-producer",
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        request_timeout_ms=20_000,
        **cfg,
    )
    try:
        producer.send(topic, key=b"ABCDEF", value=probe).get(timeout=30)
        producer.flush(timeout=30)
        print(f"produced 1 record → {topic}")
    finally:
        producer.close()

    consumer = KafkaConsumer(
        topic,
        client_id="eu-air-traffic-roundtrip-consumer",
        group_id=f"eu-air-traffic-roundtrip-{datetime.now(UTC).timestamp():.0f}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        consumer_timeout_ms=15_000,
        **cfg,
    )
    try:
        for message in consumer:
            if message.value.get("source") == "kafka-roundtrip":
                print(f"consumed ✓ offset={message.offset} value={message.value}")
                return 0
    finally:
        consumer.close()
    print("roundtrip FAILED — probe not consumed")
    return 1


# Topics from the retired 8-topic layout. They must be deleted for the 5-topic
# free-plan limit to hold.
LEGACY_TOPICS = ["eu-metar", "eu-taf", "eu-forecast", "eu-collect-meta"]


def delete_legacy_topics() -> int:
    if not settings.kafka_enabled:
        print("Kafka is not configured")
        return 1
    from kafka.admin import KafkaAdminClient

    admin = KafkaAdminClient(client_id="eu-air-traffic-admin", **_client_setting())
    try:
        existing = set(admin.list_topics())
        to_delete = [t for t in LEGACY_TOPICS if t in existing]
        if not to_delete:
            print("no legacy topics present")
            return 0
        admin.delete_topics(to_delete)
        print(f"deleted: {', '.join(to_delete)}")
    finally:
        admin.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Kafka admin/check tooling")
    parser.add_argument(
        "command", choices=["check", "create-topics", "delete-legacy-topics", "roundtrip"]
    )
    parser.add_argument("--partitions", type=int, default=DEFAULT_PARTITIONS)
    args = parser.parse_args()

    if args.command == "check":
        return check()
    if args.command == "create-topics":
        return create_topics(partitions=args.partitions)
    if args.command == "delete-legacy-topics":
        return delete_legacy_topics()
    return roundtrip()


if __name__ == "__main__":
    logger.debug("kafka admin invoked")
    sys.exit(main())
