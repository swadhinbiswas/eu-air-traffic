"""Object-store abstraction for the data lake.

The lake has been Hugging Face from day one; the AWS deployment puts the same
Bronze/Silver Parquet on S3. Both are "a remote prefix of files", and the rest
of the pipeline does not care which, so the read/write path lives behind one
small interface:

    backend = get_lake_backend()                 # hf | s3 | local, from LAKE_BACKEND
    backend.upload_file(path, "silver/flights/part-1.parquet")
    backend.upload_directory(silver_dir, "silver")
    backend.download_prefix("silver", warehouse_dir)

The S3 backend preserves the property the HF path depends on: **an unchanged
file is a no-op**. It compares the local size against the remote object's
``ContentLength`` and only uploads when they differ, so a scheduled cycle never
rewrites the files it did not change — which is both the storage-budget guard
and the reason a publish cannot be mistaken for real movement.

The ``local`` backend writes under the warehouse directory and is what the unit
tests use; it needs no credentials and no network.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from config.logging import logger
from config.settings import Settings, settings


class LakeBackend(ABC):
    """Read/write a tree of files under a remote prefix."""

    #: Short name used in logs and by the ``status`` action.
    name: str = "backend"

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Whether the backend has what it needs to talk to the store."""

    @abstractmethod
    def upload_file(self, local_path: str | Path, key: str) -> bool:
        """Upload one file to ``key`` (relative to the backend root)."""

    @abstractmethod
    def upload_directory(
        self, local_dir: str | Path, prefix: str, patterns: list[str] | None = None
    ) -> int:
        """Upload every matching file under ``local_dir`` to ``prefix``."""

    @abstractmethod
    def download_prefix(
        self, prefix: str, local_dir: str | Path, patterns: list[str] | None = None
    ) -> int:
        """Download everything under ``prefix`` into ``local_dir``."""

    def describe(self) -> str:
        return self.name


class LocalLake(LakeBackend):
    """Filesystem backend: the lake is a directory. Used by tests and demos."""

    name = "local"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @property
    def enabled(self) -> bool:
        return True

    def _target(self, key: str) -> Path:
        return self.root / key

    def upload_file(self, local_path: str | Path, key: str) -> bool:
        source = Path(local_path)
        if not source.is_file():
            return False
        target = self._target(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size == source.stat().st_size:
            return True
        shutil.copyfile(source, target)
        return True

    def upload_directory(
        self, local_dir: str | Path, prefix: str, patterns: list[str] | None = None
    ) -> int:
        root = Path(local_dir)
        if not root.exists():
            return 0
        files = [
            p for p in root.rglob("*") if p.is_file() and (not patterns or p.suffix in patterns)
        ]
        uploaded = 0
        for path in files:
            key = f"{prefix.rstrip('/')}/{path.relative_to(root).as_posix()}"
            if self.upload_file(path, key):
                uploaded += 1
        if uploaded != len(files):
            raise RuntimeError(f"[lake] uploaded only {uploaded}/{len(files)} files to {prefix}")
        return uploaded

    def download_prefix(
        self, prefix: str, local_dir: str | Path, patterns: list[str] | None = None
    ) -> int:
        root = self._target(prefix.strip("/"))
        if not root.exists():
            return 0
        dest = Path(local_dir)
        count = 0
        for path in root.rglob("*"):
            if not path.is_file() or (patterns and path.suffix not in patterns):
                continue
            relative = path.relative_to(self.root)
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            count += 1
        return count

    def describe(self) -> str:
        return f"local:{self.root}"


class HFLake(LakeBackend):
    """Hugging Face dataset backend — the historical behaviour."""

    name = "hf"

    def __init__(self, app_settings: Settings) -> None:
        self.settings = app_settings

    @property
    def enabled(self) -> bool:
        from services import hf_lake

        return hf_lake.hf_enabled(self.settings)

    def upload_file(self, local_path: str | Path, key: str) -> bool:
        from services import hf_lake

        return hf_lake.upload_file(local_path, key, self.settings)

    def upload_directory(
        self, local_dir: str | Path, prefix: str, patterns: list[str] | None = None
    ) -> int:
        from services import hf_lake

        return hf_lake.upload_directory(local_dir, prefix, patterns, self.settings)

    def download_prefix(
        self, prefix: str, local_dir: str | Path, patterns: list[str] | None = None
    ) -> int:
        from services import hf_lake

        return hf_lake.download_prefix(prefix, local_dir, patterns, self.settings)

    def describe(self) -> str:
        return f"hf:{self.settings.huggingface_repo}"


class S3Lake(LakeBackend):
    """S3 backend for the AWS data lake.

    Credentials come from the normal boto3 chain (task role on ECS, env vars,
    or an SSO profile locally) — never from the app config — so the container
    needs no long-lived key. ``S3_ENDPOINT_URL`` is only set for LocalStack or
    MinIO in tests.
    """

    name = "s3"

    def __init__(self, app_settings: Settings) -> None:
        self.settings = app_settings
        self._client: object | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.s3_bucket)

    @property
    def bucket(self) -> str:
        if not self.settings.s3_bucket:
            raise RuntimeError("[s3] S3_BUCKET is not configured")
        return self.settings.s3_bucket

    def _s3(self):
        if self._client is None:
            import boto3  # imported lazily so the package is optional

            self._client = boto3.client(
                "s3",
                region_name=self.settings.aws_region,
                endpoint_url=self.settings.s3_endpoint_url,
            )
        return self._client

    def _key(self, key: str) -> str:
        prefix = (self.settings.s3_prefix or "").strip("/")
        key = key.lstrip("/")
        return f"{prefix}/{key}" if prefix else key

    def _remote_size(self, key: str) -> int | None:
        """Object size in bytes, or None when the object does not exist."""
        from botocore.exceptions import ClientError

        try:
            head = self._s3().head_object(Bucket=self.bucket, Key=key)
            return int(head["ContentLength"])
        except ClientError as exc:
            code = (exc.response.get("Error") or {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def upload_file(self, local_path: str | Path, key: str) -> bool:
        source = Path(local_path)
        if not source.is_file():
            return False
        remote_key = self._key(key)
        size = source.stat().st_size
        if self._remote_size(remote_key) == size:
            logger.debug("[s3] %s already up to date", remote_key)
            return True
        try:
            extra = {"StorageClass": self.settings.s3_storage_class}
            self._s3().upload_file(
                Filename=str(source),
                Bucket=self.bucket,
                Key=remote_key,
                ExtraArgs=extra,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced, never swallowed
            logger.error("[s3] upload %s failed: %s", remote_key, exc)
            return False
        # Confirm against the remote before calling it a success: a publish that
        # reports success without writing is the failure mode this project has
        # already been burned by.
        if self._remote_size(remote_key) != size:
            logger.error("[s3] %s uploaded but remote size does not match", remote_key)
            return False
        return True

    def upload_directory(
        self, local_dir: str | Path, prefix: str, patterns: list[str] | None = None
    ) -> int:
        root = Path(local_dir)
        if not root.exists():
            return 0
        files = [
            p for p in root.rglob("*") if p.is_file() and (not patterns or p.suffix in patterns)
        ]
        if not files:
            return 0
        if not self.enabled:
            raise RuntimeError("[s3] S3_BUCKET is not configured — refusing to report success")
        uploaded = 0
        for path in files:
            key = f"{prefix.rstrip('/')}/{path.relative_to(root).as_posix()}"
            if self.upload_file(path, key):
                uploaded += 1
        if uploaded != len(files):
            raise RuntimeError(f"[s3] uploaded only {uploaded}/{len(files)} files to {prefix}")
        logger.info(
            "[s3] uploaded %s/%s files → s3://%s/%s", uploaded, len(files), self.bucket, prefix
        )
        return uploaded

    def download_prefix(
        self, prefix: str, local_dir: str | Path, patterns: list[str] | None = None
    ) -> int:
        if not self.enabled:
            logger.info("[s3] no bucket configured — cannot download %s", prefix)
            return 0
        dest = Path(local_dir)
        root = prefix.strip("/")
        # Strip the root prefix on the way down so the local tree matches the HF
        # layout exactly (warehouse/silver/…, not warehouse/<bucket-prefix>/silver/…).
        strip = self.settings.s3_prefix.strip("/")
        paginator = self._s3().get_paginator("list_objects_v2")
        count = 0
        try:
            for page in paginator.paginate(Bucket=self.bucket, Prefix=self._key(root)):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    relative = key[len(strip) :].lstrip("/") if strip else key
                    if patterns and not any(relative.endswith(p) for p in patterns):
                        continue
                    target = dest / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    self._s3().download_file(self.bucket, key, str(target))
                    count += 1
        except Exception as exc:  # noqa: BLE001
            # Never silently continue: the caller merges with local state and
            # pushes the result back, so a failed pull can truncate the lake.
            logger.error("[s3] download %s failed: %s", prefix, exc)
            raise RuntimeError(f"download {prefix} failed: {exc}") from exc
        return count

    def describe(self) -> str:
        return f"s3://{self.settings.s3_bucket}/{self.settings.s3_prefix}".rstrip("/")


def _build(app_settings: Settings) -> LakeBackend:
    backend = (app_settings.lake_backend or "hf").lower()
    if backend == "s3":
        return S3Lake(app_settings)
    if backend == "local":
        return LocalLake(app_settings.warehouse_dir / "lake")
    return HFLake(app_settings)


_default: LakeBackend | None = None


def get_lake_backend(app_settings: Settings | None = None) -> LakeBackend:
    """Return the configured backend.

    The global settings singleton gets a cached instance so the sink's per-file
    uploads reuse one S3 client; an explicitly passed Settings (tests) always
    builds a fresh one.
    """
    global _default
    cfg = app_settings or settings
    if cfg is settings:
        if _default is None:
            _default = _build(cfg)
        return _default
    return _build(cfg)


def reset_lake_backend() -> None:
    """Drop the cached backend (tests that change LAKE_BACKEND in-process)."""
    global _default
    _default = None


def lake_enabled(app_settings: Settings | None = None) -> bool:
    return get_lake_backend(app_settings).enabled


def upload_file(local_path: str | Path, key: str, app_settings: Settings | None = None) -> bool:
    return get_lake_backend(app_settings).upload_file(local_path, key)


def upload_directory(
    local_dir: str | Path,
    prefix: str,
    patterns: list[str] | None = None,
    app_settings: Settings | None = None,
) -> int:
    return get_lake_backend(app_settings).upload_directory(local_dir, prefix, patterns)


def download_prefix(
    prefix: str,
    local_dir: str | Path,
    patterns: list[str] | None = None,
    app_settings: Settings | None = None,
) -> int:
    return get_lake_backend(app_settings).download_prefix(prefix, local_dir, patterns)
