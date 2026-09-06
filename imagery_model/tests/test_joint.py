import json

import pandas as pd
import pytest

from urban_tree_ml.joint import combine_manifests


def test_joint_preserves_city_identity_and_sealed_splits(tmp_path):
    sources = {}
    for city in ("ussfo", "usbos"):
        directory = tmp_path / city
        directory.mkdir()
        pd.DataFrame([
            {"chip_id": str(i), "split": split, "path": f"{split}/{i}.npz"}
            for i, split in enumerate(("train", "validation", "test"))
        ]).to_parquet(directory / "chips.parquet")
        (directory / "normalization.json").write_text(
            json.dumps({"mean": [0.5], "std": [0.2]})
        )
        sources[city] = directory
    destination = tmp_path / "joint"
    summary = combine_manifests(sources, destination)
    rows = pd.read_parquet(destination / "chips.parquet")
    assert rows.chip_id.is_unique
    assert list(rows[rows.split == "train"].chip_id) == ["ussfo:0", "usbos:0"]
    assert list(rows[rows.split == "test"].source_chip_id) == ["2", "2"]
    assert rows.iloc[0].path == "../ussfo/train/0.npz"
    assert summary["cities"]["usbos"]["validation"] == 1
    (sources["usbos"] / "normalization.json").write_text(
        json.dumps({"mean": [0.7], "std": [0.2]})
    )
    with pytest.raises(ValueError, match="identical normalization"):
        combine_manifests(sources, destination)
