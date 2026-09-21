import pandas as pd

from urban_tree_ml.expand_taxonomy import expanded_taxonomy
from urban_tree_ml.taxonomy import Taxonomy, encode_taxonomy


def test_append_only_training_support_and_genus_fallback():
    base = Taxonomy(["Old species"], ["Old"], [0])
    frame = pd.DataFrame(
        {
            "species": [
                "New common",
                "New common",
                "Tail a",
                "Tail b",
                "Leak species",
                "Leak species",
                "Bad species",
            ],
            "split": ["train"] * 4 + ["validation", "test", "train"],
            "taxon_eligible": [True] * 6 + [False],
        }
    )
    result = expanded_taxonomy(frame, base, minimum=2)
    assert result.species == ["Old species", "New common"]
    assert result.genera == ["Old", "New", "Tail"]
    encoded = encode_taxonomy(pd.DataFrame({"species": ["Tail a"]}), result)
    assert encoded.species_id.iloc[0] == -1
    assert encoded.genus_id.iloc[0] == 2
