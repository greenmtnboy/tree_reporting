import json
from pathlib import Path

from urban_tree_ml.qa_server import (
    _inject_street_view_embed_key,
    _safe_curation_return,
    _validation_chip_review_status,
)


def test_street_view_embed_key_is_only_injected_when_configured() -> None:
    html = "<script>const streetViewEmbedApiKey = null;</script>"

    assert _inject_street_view_embed_key(html, None) == html
    assert _inject_street_view_embed_key(html, "browser-key") == (
        '<script>const streetViewEmbedApiKey = "browser-key";</script>'
    )


def test_curation_return_only_allows_internal_studio_views() -> None:
    assert _safe_curation_return(None) == "/registration"
    assert _safe_curation_return("/model?run=abc&chip=r000001_c000002") == (
        "/model?run=abc&chip=r000001_c000002"
    )
    assert _safe_curation_return("https://example.com/model") == "/registration"
    assert _safe_curation_return("//example.com/model") == "/registration"
    assert _safe_curation_return("/api/reviews") == "/registration"


def test_validation_chip_review_status_uses_scene_completion(tmp_path: Path) -> None:
    manifest = {
        "metadata": {"review_id": "review-1"},
        "samples": [],
        "scenes": [
            {"scene_id": "scene-1", "validation_chip_id": "r000001_c000002"},
            {"scene_id": "scene-2", "validation_chip_id": "r000003_c000004"},
            {"scene_id": "scene-3"},
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "reviews.json").write_text(
        json.dumps(
            {
                "metadata": {"review_id": "review-1"},
                "reviews": {},
                "scene_reviews": {"scene-1": {"done": True}},
            }
        ),
        encoding="utf-8",
    )

    assert _validation_chip_review_status(tmp_path) == {
        "chips": {
            "r000001_c000002": {"scene_id": "scene-1", "reviewed": True},
            "r000003_c000004": {"scene_id": "scene-2", "reviewed": False},
        }
    }
