import pandas as pd

from urban_tree_ml.profile_vocabulary import coverage, taxon_parts


def test_species_and_genus_rank_including_hybrids():
    assert taxon_parts(None) == (None, False)
    assert taxon_parts(float("nan")) == (None, False)
    assert taxon_parts("Acer") == ("Acer", False)
    assert taxon_parts("Acer rubrum") == ("Acer", True)
    assert taxon_parts("Platanus x hispanica") == ("Platanus", True)
    assert taxon_parts("X amelasorbus jackii") == ("X amelasorbus", True)


def test_coverage_includes_unknowns_in_denominator_and_genus_only():
    frame = pd.DataFrame(
        {"canonical": ["Acer rubrum", "Acer", None], "genus": ["Acer", "Acer", None]}
    )
    assert coverage(frame, ["Acer rubrum"], ["Acer"]) == {
        "trees": 3,
        "species_supported": 1,
        "genus_supported": 2,
        "genus_only": 1,
    }
