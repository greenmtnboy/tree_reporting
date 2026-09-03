import pandas as pd

from urban_tree_ml.taxonomy import build_taxonomy, encode_taxonomy


def test_taxonomy_uses_only_supported_training_species() -> None:
    training = pd.DataFrame(
        {
            "species": [
                "Platanus x hispanica",
                "Platanus x hispanica",
                "Prunus serrulata",
                "Prunus serrulata",
                "Rare tree",
            ]
        }
    )
    taxonomy = build_taxonomy(training, min_species_examples=2, max_species_classes=10)

    assert taxonomy.species == ["Platanus x hispanica", "Prunus serrulata"]
    assert taxonomy.genera == ["Platanus", "Prunus"]

    encoded = encode_taxonomy(
        pd.DataFrame({"species": ["Prunus serrulata", "Private unknown", None]}), taxonomy
    )
    assert encoded["species_id"].tolist() == [1, -1, -1]
    assert encoded["genus_id"].tolist() == [1, -1, -1]
