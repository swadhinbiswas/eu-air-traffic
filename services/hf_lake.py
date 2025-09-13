"""Hugging Face dataset helpers — the shared data lake.

Bronze and Silver Parquet land under ``bronze/`` and ``silver/`` in one dataset
repo. Every helper degrades to a no-op/False when ``HF_TOKEN`` is missing so the
collector and CI keep working without credentials.
"""

from __future__ import annotations

from pathlib import Path

from config.logging import logger
from config.settings import Settings, settings


def hf_enabled(app_settings: Settings | None = None) -> bool:
    return bool((app_settings or settings).huggingface_token)


def _api(app_settings: Settings | None = None):
    from huggingface_hub import HfApi

    cfg = app_settings or settings
    return HfApi(token=cfg.huggingface_token), cfg


def ensure_repo(app_settings: Settings | None = None) -> bool:
    """Create the dataset repo if it does not exist. Returns success."""
    if not hf_enabled(app_settings):
        return False
    api, cfg = _api(app_settings)
    try:
        api.create_repo(
            repo_id=cfg.huggingface_repo,
            repo_type="dataset",
            private=cfg.hf_private,
            exist_ok=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("[hf] create_repo failed: %s", exc)
        return False


def upload_file(
    local_path: str | Path, repo_path: str, app_settings: Settings | None = None
) -> bool:
    """Upload one file to the dataset repo."""
    if not hf_enabled(app_settings):
        return False
    api, cfg = _api(app_settings)
    try:
        api.upload_file(
            path_or_fileobj=str(local_path),
            path_in_repo=repo_path,
            repo_id=cfg.huggingface_repo,
            repo_type="dataset",
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("[hf] upload %s failed: %s", repo_path, exc)
        return False


def upload_directory(
    local_dir: str | Path,
    repo_prefix: str,
    patterns: list[str] | None = None,
    app_settings: Settings | None = None,
) -> int:
    """Upload every matching file under ``local_dir`` to ``repo_prefix``."""
    if not hf_enabled(app_settings):
        logger.info("[hf] no token — skipping upload of %s", local_dir)
        return 0
    root = Path(local_dir)
    if not root.exists():
        return 0
    files = [p for p in root.rglob("*") if p.is_file() and (not patterns or p.suffix in patterns)]
    if not files:
        return 0
    if not ensure_repo(app_settings):
        raise RuntimeError("[hf] cannot ensure the dataset repo — refusing to report success")
    uploaded = 0
    for path in files:
        repo_path = f"{repo_prefix.rstrip('/')}/{path.relative_to(root).as_posix()}"
        if upload_file(path, repo_path, app_settings):
            uploaded += 1
    if uploaded != len(files):
        raise RuntimeError(f"[hf] uploaded only {uploaded}/{len(files)} files to {repo_prefix}")
    logger.info("[hf] uploaded %s/%s files → %s", uploaded, len(files), repo_prefix)
    return uploaded


def download_prefix(
    repo_prefix: str,
    local_dir: str | Path,
    patterns: list[str] | None = None,
    app_settings: Settings | None = None,
) -> int:
    """Download files under ``repo_prefix`` into ``local_dir``."""
    if not hf_enabled(app_settings):
        logger.info("[hf] no token — cannot download %s", repo_prefix)
        return 0
    from huggingface_hub import snapshot_download

    cfg = app_settings or settings
    root = Path(local_dir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(
            repo_id=cfg.huggingface_repo,
            repo_type="dataset",
            allow_patterns=patterns or [f"{repo_prefix.rstrip('/')}/**"],
            local_dir=str(root),
            token=cfg.huggingface_token,
        )
    except Exception as exc:  # noqa: BLE001
        # Never silently continue: the caller merges with local state and
        # pushes the result back, so a failed pull can truncate the lake.
        logger.error("[hf] download %s failed: %s", repo_prefix, exc)
        raise RuntimeError(f"download {repo_prefix} failed: {exc}") from exc
    return len([p for p in root.rglob("*") if p.is_file()])
