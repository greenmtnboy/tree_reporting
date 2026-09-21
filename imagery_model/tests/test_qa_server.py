import json
from pathlib import Path

import pytest

from urban_tree_ml.config import load_config
from urban_tree_ml.qa_server import (
    _checked_review_context,
    _inject_street_view_embed_key,
    _safe_curation_return,
    _validation_chip_review_status,
)


def test_review_context_rejects_a_city_config_mismatch(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    raster = tmp_path / "imagery.tif"
    raster.touch()
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    (review_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    context = _checked_review_context(
        "ussfo",
        "San Francisco",
        config,
        raster,
        review_dir,
        tmp_path / "evaluation",
    )

    assert context.config.inventory.city == "USSFO"
    with pytest.raises(ValueError, match="does not match"):
        _checked_review_context(
            "usbos",
            "Boston",
            config,
            raster,
            review_dir,
            tmp_path / "evaluation",
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
            "r000001_c000002": {"scene_id": "scene-1", "reviewed": True, "more_done": False},
            "r000003_c000004": {"scene_id": "scene-2", "reviewed": False, "more_done": False},
        }
    }


@pytest.mark.parametrize('job_state', [
    {'running': True, 'error': None},
    {'running': False, 'error': 'Cloud upload failed'},
])
def test_backup_status_exposes_running_and_failed_jobs(tmp_path, monkeypatch, job_state):
    from types import SimpleNamespace
    from urban_tree_ml import qa_server

    config = load_config(Path(__file__).parents[1] / 'configs/sf_naip_baseline.yaml')
    config.paths.root = tmp_path / 'artifacts'
    config.paths.annotations = tmp_path / 'annotations'
    bundle = config.paths.annotations / 'ussfo' / 'review-1'
    bundle.mkdir(parents=True)
    (bundle / 'bundle.json').write_text(json.dumps({
        'feedback_current': False, 'review_state_sha256': 'saved',
    }))
    context = qa_server.ReviewContext('ussfo', 'SF', config, tmp_path / 'source.tif',
                                     tmp_path / 'review', tmp_path / 'evaluation')
    monkeypatch.setattr(qa_server, 'RunDebugCatalog',
                        lambda *args: SimpleNamespace(summary=lambda: {'runs': []}))
    monkeypatch.setattr(qa_server, 'read_collection', lambda directory: (
        {'metadata': {'review_id': 'review-1'}}, {'state_revision': 'saved'}))
    monkeypatch.setattr(qa_server, 'BackupJob',
                        lambda **kwargs: SimpleNamespace(status=lambda root: job_state))
    responses = []

    class Server:
        def __init__(self, address, handler):
            self.handler = handler.func

        def serve_forever(self):
            request = object.__new__(self.handler)
            request.path = '/api/backup-status?city=ussfo'
            request._json_response = lambda status, payload: responses.append((status, payload))
            request.do_GET()

        def server_close(self):
            pass

    monkeypatch.setattr(qa_server, 'ThreadingHTTPServer', Server)
    qa_server._serve_review_contexts({'ussfo': context}, default_city='ussfo',
                                    bind='127.0.0.1', port=0)
    status, payload = responses[0]
    assert status == 200
    assert payload['job'] == job_state
    assert not payload['snapshot']['pending']
    assert payload['published_for_training'] is False
