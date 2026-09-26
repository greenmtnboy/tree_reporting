import pandas as pd
import pytest

from urban_tree_ml.prepare_next_run import reencode, validate_encoding
from urban_tree_ml.taxonomy import Taxonomy


def test_freeze_refuses_unpublished_curation_before_creating_destination(tmp_path, monkeypatch):
    import json

    from urban_tree_ml import freeze_next_run

    reviews = tmp_path / "reviews"
    reviews.mkdir()
    for name, content in {
        "manifest.json": {},
        "reviews.json": {},
        "training-feedback.json": {"source_reviews_sha256": "old"},
    }.items():
        (reviews / name).write_text(json.dumps(content))
    monkeypatch.setattr(
        freeze_next_run, "load_persisted_reviews", lambda path: {"state_revision": "new"}
    )
    destination = tmp_path / "frozen"
    with pytest.raises(ValueError, match="publish current curation first"):
        freeze_next_run.freeze(tmp_path, {"ussfo": reviews}, destination)
    assert not destination.exists()


def test_reencoding_keeps_geometry_splits_and_optional_targets():
    frame = pd.DataFrame(
        {
            "tree_id": ["a", "b", "c"],
            "species": ["Alias", "Acer rare", None],
            "split": ["train", "validation", "test"],
            "latitude": [1.0, 2.0, 3.0],
            "dbh_eligible": [True, False, True],
            "dbh_log1p": [1.0, None, 3.0],
        }
    )
    taxonomy = Taxonomy(["Acer rubrum"], ["Acer"], [0])
    result = reencode(frame, taxonomy, lambda s: "Acer rubrum" if s == "Alias" else s)
    assert result.species_id.tolist() == [0, -1, -1]
    assert result.genus_id.tolist() == [0, 0, -1]
    assert result.genus_eligible.tolist() == [True, True, False]
    assert result.species_eligible.tolist() == [True, False, False]
    for column in ["split", "latitude", "dbh_eligible", "dbh_log1p"]:
        pd.testing.assert_series_equal(result[column], frame[column])
    result.loc[0, "species_id"] = 8
    with pytest.raises(ValueError, match="Stale species IDs"):
        validate_encoding(result, taxonomy)
