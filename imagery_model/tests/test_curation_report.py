import json
from types import SimpleNamespace

import pytest

from urban_tree_ml.curation_report import city_report, detection_f2


def test_f2_weights_missed_trees_more_than_false_positives():
    assert detection_f2(2, 1, 3) == pytest.approx(10 / 23)
    assert detection_f2(0, 0, 0) is None
    assert detection_f2(0, 2, 0) == 0


def test_report_keeps_completion_separate_from_tree_labels_and_scores(tmp_path, monkeypatch):
    monkeypatch.setattr("urban_tree_ml.curation_report.training_chip_catalog", lambda *args: None)
    manifest = {"scenes": [
        {"scene_id": "one", "sample_ids": ["a"], "splits": ["train"]},
        {"scene_id": "two", "sample_ids": ["b"], "splits": ["validation"],
         "validation_chip_id": "r000000_c000001"},
    ]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "training-feedback.json").write_text(
        json.dumps({"source_reviews_sha256": "old"})
    )
    monkeypatch.setattr("urban_tree_ml.curation_report.load_persisted_reviews", lambda _: {
        "reviews": {"a": {"status": "aligned"}, "b": {"status": "uncertain"}},
        "scene_reviews": {"one": {"done": True}}, "mask_regions": [],
        "state_revision": "current",
    })
    catalog = SimpleNamespace(summary=lambda: {"runs": []})
    result = city_report(SimpleNamespace(directory=tmp_path, city="ussfo", label="SF"), catalog)
    assert result["done"] == 1
    assert result["scenes"] == 2
    assert result["rows"][1]["status"] == "in-progress"
    assert result["rows"][1]["f2"] is None
    assert result["published"] is False
    assert result["split_scenes"] == {"train": 1, "validation": 1}
