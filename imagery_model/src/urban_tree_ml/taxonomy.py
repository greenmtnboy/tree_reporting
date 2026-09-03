from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd


def genus_for_species(species: str) -> str:
    return species.strip().split(maxsplit=1)[0]


@dataclass(frozen=True)
class Taxonomy:
    species: list[str]
    genera: list[str]
    species_to_genus: list[int]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_taxonomy(
    training_rows: pd.DataFrame,
    min_species_examples: int,
    max_species_classes: int,
) -> Taxonomy:
    counts = training_rows["species"].value_counts()
    selected = counts[counts >= min_species_examples]
    # Stable tie breaking makes the vocabulary byte-for-byte reproducible.
    ordered_species = sorted(selected.index, key=lambda name: (-int(selected[name]), name))
    species = ordered_species[:max_species_classes]
    genera = sorted({genus_for_species(name) for name in species})
    genus_ids = {name: index for index, name in enumerate(genera)}
    return Taxonomy(
        species=species,
        genera=genera,
        species_to_genus=[genus_ids[genus_for_species(name)] for name in species],
    )


def encode_taxonomy(frame: pd.DataFrame, taxonomy: Taxonomy) -> pd.DataFrame:
    species_ids = {name: index for index, name in enumerate(taxonomy.species)}
    genus_ids = {name: index for index, name in enumerate(taxonomy.genera)}
    result = frame.copy()
    result["species_id"] = result["species"].map(species_ids).fillna(-1).astype("int32")
    result["genus"] = result["species"].map(
        lambda value: genus_for_species(value) if isinstance(value, str) and value.strip() else None
    )
    result["genus_id"] = result["genus"].map(genus_ids).fillna(-1).astype("int32")
    return result
