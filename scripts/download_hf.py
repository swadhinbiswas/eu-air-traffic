"""Download warehouse data from Hugging Face Hub.

Pulls the Silver/Gold Parquet files and DuckDB warehouse from a HF dataset
repo into the local warehouse directory. Used by the HF Spaces app on startup.

Usage:
    python -m scripts.download_hf
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config.logging import logger
from config.settings import settings


def download_warehouse(app_settings: Any = None) -> dict[str, Any]:
    """Pull warehouse artifacts from HF Hub into the local warehouse dir."""
    s = app_settings or settings

    if not s.huggingface_token:
        logger.warning("[hf-download] HF_TOKEN not set — skipping download")
        return {"skipped": True, "reason": "HF_TOKEN not set"}

    try:
        from huggingface_hub import HfApi, hf_hub_download
    except ImportError as exc:
        logger.warning("[hf-download] huggingface_hub not installed — skipping")
        return {"skipped": True, "reason": str(exc)}

    api = HfApi(token=s.huggingface_token)
    repo = s.huggingface_repo

    try:
        api.repo_info(repo_id=repo, repo_type="dataset")
    except Exception:  # noqa: BLE001
        logger.warning("[hf-download] repo %s not found or not accessible", repo)
        return {"skipped": True, "reason": f"repo {repo} not found"}

    s.ensure_directories()

    downloaded = 0
    try:
        files = api.list_repo_tree(repo_id=repo, repo_type="dataset", recursive=True)
        for item in files:
            if not hasattr(item, "path") or not item.path.endswith(".parquet"):
                continue

            local_path = _repo_path_to_local(item.path, s)
            if local_path is None:
                continue

            local_path.parent.mkdir(parents=True, exist_ok=True)
            hf_hub_download(
                repo_id=repo,
                filename=item.path,
                repo_type="dataset",
                token=s.huggingface_token,
                local_dir=str(s.warehouse_dir),
            )
            downloaded += 1
            logger.debug("[hf-download] downloaded %s", item.path)

    except Exception as exc:  # noqa: BLE001
        logger.error("[hf-download] download failed: %s", exc)
        return {"downloaded": downloaded, "error": str(exc)}

    logger.info("[hf-download] downloaded %s files from %s", downloaded, repo)
    return {"downloaded": downloaded, "repo": repo}


def _repo_path_to_local(repo_path: str, s: Any) -> Path | None:
    """Map a repo path to a local warehouse path."""
    parts = repo_path.split("/")

    if len(parts) >= 2 and parts[0] == "silver":
        source = parts[1]
        return s.silver_dir / source / "data.parquet"

    if len(parts) >= 2 and parts[0] == "gold":
        mart = parts[1]
        return s.gold_dir / mart / f"{mart}.parquet"

    if len(parts) == 1 and parts[0].endswith(".duckdb"):
        return s.duckdb_path

    return None


def main() -> None:
    result = download_warehouse()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
