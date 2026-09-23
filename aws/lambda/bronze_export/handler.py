"""Bronze-layer export job (stage 1 of the AWS version).

The free deployment keeps its lake on Hugging Face / a small S3 prefix. This
Lambda is the bridge into the AWS analytics plane: it lands that data as raw
Parquet in the landing zone so Glue can crawl it, transform it, and Athena can
query it.

It reads the existing lake (``SOURCE_BUCKET``) for the last ``LOOKBACK_HOURS``
and writes one partitioned object per source per run:

    s3://<raw-bucket>/<source>/dt=YYYY-MM-DD/<source>_<run>.parquet

The landing zone is **append-only**: every run writes new objects and never
rewrites an existing partition, so a re-run cannot destroy earlier data. The
Glue job deduplicates when it builds the curated zone. That is the same
"never let a publish silently overwrite history" rule the main pipeline uses.

Environment (set by infra/terraform-serverless/lambda.tf):
    RAW_BUCKET          destination bucket
    RAW_PREFIX          optional root prefix (default "")
    RAW_PREFIX_<DOMAIN> optional per-domain root prefix; Terraform sets one per
                        domain (RAW_PREFIX_ADSB, RAW_PREFIX_OPENSKY, …) so the
                        layout stays a Terraform concern. Falls back to
                        RAW_PREFIX, then to the domain name.
    SOURCE_BUCKET       the existing lake bucket to read (optional)
    SOURCE_PREFIX       root prefix inside the source bucket (default "")
    DOMAINS             comma-separated sources (default: the five topics)
    LOOKBACK_HOURS      how far back to read (default: 6)
    HF_REPO / HF_TOKEN  read from the Hugging Face lake instead of S3 (optional)
    LOG_LEVEL           default INFO

Packaging: pyarrow is required (Parquet writing); the optional Hugging Face path
also needs huggingface_hub. Ship them in a Lambda layer (see aws/README.md and
var.lambda_layer_arns) or as a container image.
"""

from __future__ import annotations

import io
import json
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

# The five products the Kafka bus carries (see services/kafka_bus.py).
DEFAULT_DOMAINS = ("adsb", "opensky", "airlabs", "metar", "eurostat")


def _domains() -> list[str]:
    raw = os.environ.get("DOMAINS", "")
    return [d.strip() for d in raw.split(",") if d.strip()] or list(DEFAULT_DOMAINS)


def _window() -> datetime:
    hours = int(os.environ.get("LOOKBACK_HOURS", "6"))
    return datetime.now(UTC) - timedelta(hours=hours)


def _list_recent(s3, bucket: str, prefix: str, since: datetime) -> list[str]:
    """Keys under ``prefix`` modified since ``since``."""
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["LastModified"] >= since:
                keys.append(obj["Key"])
    return keys


def _read_object(s3, bucket: str, key: str) -> list[dict[str, Any]]:
    """Read one lake object as records, accepting JSONL or Parquet."""
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if key.endswith(".parquet"):
        return pq.read_table(io.BytesIO(body)).to_pylist()
    records: list[dict[str, Any]] = []
    for line in body.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("skipping unparseable line in s3://%s/%s", bucket, key)
    return records


def _read_domain_from_s3(s3, domain: str) -> list[dict[str, Any]]:
    bucket = os.environ.get("SOURCE_BUCKET")
    if not bucket:
        return []
    root = os.environ.get("SOURCE_PREFIX", "").strip("/")
    prefix = f"{root}/{domain}" if root else domain
    # Also look under a bronze/ layout, which is where the collector writes.
    keys = _list_recent(s3, bucket, prefix, _window())
    if not keys:
        keys = _list_recent(s3, bucket, f"bronze/{domain}", _window())
    records: list[dict[str, Any]] = []
    for key in keys:
        try:
            records.extend(_read_object(s3, bucket, key))
        except Exception as exc:  # noqa: BLE001 - one bad object must not kill the run
            logger.error("failed to read s3://%s/%s: %s", bucket, key, exc)
    return records


def _read_domain_from_hf(domain: str) -> list[dict[str, Any]]:
    """Optional Hugging Face source, used when SOURCE_BUCKET is not set."""
    repo = os.environ.get("HF_REPO")
    if not repo:
        return []
    from huggingface_hub import HfApi, hf_hub_download  # imported lazily

    api = HfApi(token=os.environ.get("HF_TOKEN"))
    files = [
        f
        for f in api.list_repo_files(repo, repo_type="dataset")
        if f.startswith(f"bronze/{domain}/") and f.endswith(".jsonl")
    ][-20:]
    records: list[dict[str, Any]] = []
    for name in files:
        path = hf_hub_download(
            repo_id=repo,
            filename=name,
            repo_type="dataset",
            token=os.environ.get("HF_TOKEN"),
        )
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


def _raw_prefix(domain: str) -> str:
    """Root key prefix for one domain's objects.

    Terraform sets ``RAW_PREFIX_<DOMAIN>`` per domain; ``RAW_PREFIX`` is the
    blanket fallback, and an empty result means "write at the bucket root".
    """
    specific = os.environ.get(f"RAW_PREFIX_{domain.upper()}")
    value = specific if specific is not None else os.environ.get("RAW_PREFIX", "")
    return (value or "").strip("/")


def _write_partition(s3, domain: str, records: list[dict[str, Any]], run_id: str) -> str:
    """Write one Parquet object for this domain/run and return its key."""
    table = pa.Table.from_pylist(records)
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    buffer.seek(0)

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    key = f"{domain}/dt={day}/{domain}_{run_id}.parquet"
    prefix = _raw_prefix(domain)
    if prefix:
        key = f"{prefix}/{key}"
    s3.put_object(Bucket=os.environ["RAW_BUCKET"], Key=key, Body=buffer.getvalue())
    return key


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entry point. Returns a per-domain summary so the CloudWatch log is useful."""
    s3 = boto3.client("s3")
    run_id = uuid.uuid4().hex[:12]
    written: dict[str, dict[str, Any]] = {}

    for domain in _domains():
        records = _read_domain_from_s3(s3, domain)
        if not records:
            records = _read_domain_from_hf(domain)
        if not records:
            logger.info("[export] %s: nothing in the lookback window", domain)
            written[domain] = {"records": 0, "key": None}
            continue
        key = _write_partition(s3, domain, records, run_id)
        written[domain] = {"records": len(records), "key": key}
        logger.info("[export] %s: %s records → %s", domain, len(records), key)

    total = sum(item["records"] for item in written.values())
    logger.info("[export] run %s: %s records across %s domains", run_id, total, len(written))
    return {"run_id": run_id, "domains": written, "total_records": total}


if __name__ == "__main__":  # local smoke test without AWS
    print(
        json.dumps(
            {"domains": _domains(), "window_hours": int(os.environ.get("LOOKBACK_HOURS", "6"))}
        )
    )
