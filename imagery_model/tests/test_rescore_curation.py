from contextlib import nullcontext
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from affine import Affine
from pyproj import Transformer

from urban_tree_ml.rescore_curation import labels_from_feedback


def test_rebuild_offsets_exclusions_collisions_and_sealed_cohort(monkeypatch):
    import rasterio

    monkeypatch.setattr(
        rasterio,
        "open",
        lambda _: nullcontext(
            SimpleNamespace(crs="EPSG:3857", transform=Affine.identity(), width=64, height=64)
        ),
    )
    lon, lat = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True).transform(
        [2.0, 6.0, 34.0], [2.0, 6.0, 2.0]
    )
    inventory = pd.DataFrame(
        {
            "tree_id": ["a", "b", "test"],
            "longitude": lon,
            "latitude": lat,
            "split": ["validation", "validation", "test"],
            "split_eligible": True,
            "dbh_eligible": True,
            "species_eligible": False,
            "genus_eligible": False,
            "dbh_log1p": np.log1p(20.0),
            "species_id": 3,
            "genus_id": 1,
        }
    )
    feedback = {
        "registration": {"east_m": 0.0, "north_m": 0.0},
        "exclusions": [],
        "point_corrections": [],
    }
    args = ("unused", 32, 2, {"r000000_c000000"})
    original = labels_from_feedback(inventory, feedback, *args)
    assert original.tree_id.tolist() == ["a", "b"]
    assert original.species_id.isna().all()
    feedback["point_corrections"] = [
        {"tree_id": "a", "split": "validation", "east_m": 4.0, "north_m": 4.0}
    ]
    assert labels_from_feedback(inventory, feedback, *args).empty
    feedback["exclusions"] = [{"tree_id": "b"}]
    new = labels_from_feedback(inventory, feedback, *args)
    assert new.tree_id.tolist() == ["a"]
    assert new.iloc[0].output_x == 3
    assert new.iloc[0].output_y == 3


def test_mixed_split_correction_fails_closed(monkeypatch):
    import rasterio

    monkeypatch.setattr(
        rasterio,
        "open",
        lambda _: nullcontext(
            SimpleNamespace(crs="EPSG:4326", transform=Affine.identity(), width=64, height=64)
        ),
    )
    inventory = pd.DataFrame(
        {
            "tree_id": ["a", "b"],
            "longitude": [2.0, 6.0],
            "latitude": [2.0, 6.0],
            "split": ["validation", "train"],
            "split_eligible": True,
        }
    )
    feedback = {
        "registration": {"east_m": 0.0, "north_m": 0.0},
        "exclusions": [],
        "point_corrections": [],
    }
    with pytest.raises(ValueError, match="mixed split"):
        labels_from_feedback(inventory, feedback, "unused", 32, 2, {"r000000_c000000"})
