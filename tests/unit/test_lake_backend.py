"""Unit tests for the lake backend abstraction.

The S3 path runs against moto's in-memory S3, so the prefix handling, the
"unchanged file is a no-op" rule and the download layout are exercised without
an AWS account.
"""

from __future__ import annotations

import boto3
import pytest

from config.settings import Settings
from services.lake_backend import (
    HFLake,
    LocalLake,
    S3Lake,
    get_lake_backend,
    reset_lake_backend,
)

moto = pytest.importorskip("moto")
from moto import mock_aws  # noqa: E402


def _write(path, payload: bytes = b"payload") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_local_lake_round_trip(tmp_path):
    backend = LocalLake(tmp_path / "store")
    source = tmp_path / "silver"
    _write(source / "flights" / "part-1.parquet", b"abc")

    assert backend.upload_directory(source, "silver") == 1

    destination = tmp_path / "restore"
    assert backend.download_prefix("silver", destination) == 1
    assert (destination / "silver" / "flights" / "part-1.parquet").read_bytes() == b"abc"


def test_local_lake_upload_is_idempotent(tmp_path):
    backend = LocalLake(tmp_path / "store")
    source = tmp_path / "silver" / "part.parquet"
    _write(source, b"same")
    assert backend.upload_file(source, "silver/part.parquet") is True
    assert backend.upload_file(source, "silver/part.parquet") is True
    assert (tmp_path / "store" / "silver" / "part.parquet").read_bytes() == b"same"


def test_factory_selects_backend(tmp_path):
    reset_lake_backend()
    local = Settings(lake_backend="local", warehouse_dir=tmp_path)
    assert isinstance(get_lake_backend(local), LocalLake)

    s3 = Settings(lake_backend="s3", s3_bucket="a-bucket", aws_region="eu-central-1")
    assert isinstance(get_lake_backend(s3), S3Lake)

    hf = Settings(lake_backend="hf", huggingface_token="token")
    assert isinstance(get_lake_backend(hf), HFLake)


def test_s3_backend_disabled_without_bucket():
    backend = S3Lake(Settings(lake_backend="s3", s3_bucket=None))
    assert backend.enabled is False
    with pytest.raises(RuntimeError):
        backend.upload_directory("/tmp", "silver")  # no bucket → must not report success


@mock_aws
def test_s3_upload_download_and_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    client = boto3.client("s3", region_name="eu-central-1")
    client.create_bucket(
        Bucket="lake",
        CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
    )

    settings = Settings(
        lake_backend="s3",
        s3_bucket="lake",
        s3_prefix="eu",
        aws_region="eu-central-1",
    )
    backend = S3Lake(settings)
    source = tmp_path / "silver" / "flights"
    _write(source / "part.parquet", b"payload")

    assert backend.upload_file(source / "part.parquet", "silver/flights/part.parquet") is True
    head = client.head_object(Bucket="lake", Key="eu/silver/flights/part.parquet")
    assert head["ContentLength"] == 7

    # An unchanged file is a no-op (no new object version, still succeeds).
    assert backend.upload_file(source / "part.parquet", "silver/flights/part.parquet") is True

    destination = tmp_path / "restore"
    assert backend.download_prefix("silver", destination) == 1
    # The bucket root prefix is stripped, so the local tree matches the HF layout.
    assert (destination / "silver" / "flights" / "part.parquet").read_bytes() == b"payload"


@mock_aws
def test_s3_upload_directory_counts_files(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    client = boto3.client("s3", region_name="eu-central-1")
    client.create_bucket(
        Bucket="lake",
        CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
    )
    backend = S3Lake(Settings(lake_backend="s3", s3_bucket="lake", aws_region="eu-central-1"))
    root = tmp_path / "silver"
    _write(root / "flights" / "a.parquet", b"a")
    _write(root / "weather" / "b.parquet", b"b")
    _write(root / "notes.txt", b"ignore me")

    assert backend.upload_directory(root, "silver") == 3
    # patterns filter to one suffix
    assert backend.upload_directory(root, "silver", patterns=[".txt"]) == 1
