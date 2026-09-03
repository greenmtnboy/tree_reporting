import numpy as np
import pandas as pd

from urban_tree_ml.config import InventoryConfig
from urban_tree_ml.inventory import _add_source_label_eligibility


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
