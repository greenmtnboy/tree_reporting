import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from urban_tree_ml.config import InventoryConfig, load_config
from urban_tree_ml.inventory import _add_source_label_eligibility, export_inventory


def test_inventory_retains_detection_points_with_independent_label_eligibility() -> None:
    config = InventoryConfig(
        city="USSFO",
        parquet_url="fixture.parquet",
        min_dbh_in=4,
        max_dbh_in=60,
        excluded_species=["Unknown", "Palm"],
        min_species_examples=2,
        max_species_classes=10,
    )
    frame = pd.DataFrame(
        {
            "tree_id": ["complete", "missing-dbh", "missing-taxon", "excluded-taxon"],
            "diameter_at_breast_height": [12.0, np.nan, 8.0, 10.0],
            "species": ["Prunus serrulata", "Prunus serrulata", None, "Unknown"],
        }
    )

    labeled = _add_source_label_eligibility(frame, config)

    assert labeled["tree_id"].tolist() == frame["tree_id"].tolist()
    assert labeled["dbh_eligible"].tolist() == [True, False, True, True]
    assert labeled["taxon_eligible"].tolist() == [True, True, False, False]


def test_external_inventory_uses_reference_taxonomy_and_reports_oov(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(
        Path(__file__).parents[1] / "configs" / "boston_naip_external.yaml"
    )
    config.paths.root = tmp_path
    assert config.reference is not None
    config.reference.taxonomy_path = tmp_path / "sf-taxonomy.json"
    taxonomy = {
        "species": ["Acer rubrum"],
        "genera": ["Acer"],
        "species_to_genus": [0],
    }
    config.reference.taxonomy_path.write_text(json.dumps(taxonomy), encoding="utf-8")
    source = pd.DataFrame(
        {
            "tree_id": ["shared-species", "shared-genus", "oov"],
            "city": ["USBOS"] * 3,
            "data_source": ["fixture"] * 3,
            "species": ["Acer rubrum", "Acer saccharum", "Quercus alba"],
            "plant_date": [None] * 3,
            "diameter_at_breast_height": [8.0, 9.0, 10.0],
            "latitude": [42.35, 42.36, 42.37],
            "longitude": [-71.10, -71.09, -71.08],
        }
    )
    monkeypatch.setattr("urban_tree_ml.inventory._read_inventory", lambda _config: source)

    def assigned(frame: pd.DataFrame, *_args: object) -> pd.DataFrame:
        result = frame.copy()
        result["split"] = "validation"
        result["split_eligible"] = True
        return result

    monkeypatch.setattr("urban_tree_ml.inventory.assign_spatial_splits", assigned)

    summary = export_inventory(config)
    exported = pd.read_parquet(summary["paths"]["inventory"])

    assert exported["species_id"].tolist() == [0, -1, -1]
    assert exported["genus_id"].tolist() == [0, 0, -1]
    assert summary["out_of_vocabulary"] == {"species_rows": 2, "genus_rows": 1}
    assert json.loads(Path(summary["paths"]["taxonomy"]).read_text(encoding="utf-8")) == taxonomy
