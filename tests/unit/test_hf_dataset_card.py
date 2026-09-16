"""Tests for the generated Hugging Face dataset card.

The card must stay a plain, valid dataset README: correct front matter for the
Hub UI, one config per real Silver split, schemas that match the committed
Parquet files, and no reviewer-hostile claims.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import yaml

from scripts import hf_dataset_card as card

REPO_ID = "swadhinbiswas/air-traffic"
REPO_URL = "https://huggingface.co/datasets/swadhinbiswas/air-traffic"


def _front_matter(text: str) -> dict:
    raw = text.split("---\n", 1)[1].split("---", 1)[0]
    return yaml.safe_load(raw)


def test_front_matter_has_exact_keys():
    fm = _front_matter(card.render_card())
    assert set(fm) == {
        "language",
        "license",
        "tags",
        "task_categories",
        "pretty_name",
        "configs",
    }
    assert fm["language"] == ["en"]
    assert fm["license"] == "mit"
    assert fm["pretty_name"] == "EU Air Traffic Lake"


def test_tags_are_hub_friendly():
    fm = _front_matter(card.render_card())
    for tag in fm["tags"]:
        assert tag == tag.lower().replace("_", "-") or "_" not in tag.replace("-", ""), tag
    assert "parquet" in fm["tags"]
    assert "aviation" in fm["tags"]


def test_one_config_per_silver_split():
    fm = _front_matter(card.render_card())
    splits = [f["split"] for f in fm["configs"][0]["data_files"]]
    assert splits == list(card.SILVER_ORDER)
    for entry in fm["configs"][0]["data_files"]:
        assert entry["path"].endswith(".parquet")
    bronze = {f.get("split"): f.get("path") for f in fm["configs"][1]["data_files"]}
    assert set(bronze) == {"parquet", "raw"}


def test_feature_counts_match_committed_schemas():
    body = card.render_card().split("---", 2)[2]
    for split, (path, _) in card.SILVER_FILES.items():
        assert path in body
        # every declared field of the split appears once in its details block
        # (in the Field reference section, not the earlier Contents mention)
        field_ref = body.split("## Field reference", 1)[1]
        block = field_ref.split(path)[1].split("</details>")[0]
        for name, _ in card.FEATURES[split]:
            assert f"`{name}`" in block, (split, name)


def test_known_field_types_are_correct():
    text = card.render_card()
    # spot-check the fields reviewers actually sort and filter on
    for col in ("icao24", "delay_minutes", "scheduled_departure", "co2_kg_per_hour"):
        assert f"`{col}`" in text
    assert "`delay_minutes`" in text
    # limitation notes use the real on-disk names
    assert "wind_dir_deg" in text
    assert "delay_minutes/actual_* are null" in text


def test_no_sidecar_and_only_readme_is_written():
    assert card.get_card_repo_path() == "README.md"
    text = card.render_card()
    assert "README.yaml" not in text


def test_push_refuses_the_wrong_repo_without_touching_the_network():
    fake = SimpleNamespace(huggingface_repo="someone/else", huggingface_token="x" * 8)
    try:
        card.push_card(fake)
    except ValueError as exc:
        assert "someone/else" in str(exc)
    else:  # pragma: no cover - must never upload to the wrong repo
        raise AssertionError("push_card accepted the wrong repo")


def test_push_skips_without_a_token():
    fake = SimpleNamespace(huggingface_repo=REPO_ID, huggingface_token=None)
    assert card.push_card(fake) == {"skipped": True, "reason": "HF_TOKEN not set"}


def test_repo_links_point_at_the_real_dataset():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    readme = (root / "README.md").read_text(encoding="utf-8")
    docs = (root / "docs" / "huggingface-dataset.md").read_text(encoding="utf-8")
    assert REPO_URL in readme
    assert REPO_URL in docs
    example = (root / ".env.example").read_text(encoding="utf-8")
    assert f"HF_REPO={REPO_ID}" in example


def test_external_urls_resolve_to_real_pages():
    text = card.render_card()
    for url in set(re.findall(r"https?://\S+", text)):
        assert "example.com" not in url
        assert url.rstrip("}).,") in (
            card.REPO_URL,
            card.GITHUB_URL,
            card.SOFTWARE_DOI_URL,
        )


def test_citation_block_is_complete():
    text = card.render_card()
    assert "@misc{swadhinbiswas_air_traffic_lake," in text
    assert "Swadhin Biswas" in text
    assert "2026" in text
