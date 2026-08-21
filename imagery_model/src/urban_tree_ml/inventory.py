from __future__ import annotations

import json

import duckdb
import numpy as np
import pandas as pd

from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.splits import assign_spatial_splits
from urban_tree_ml.taxonomy import build_taxonomy, encode_taxonomy

INVENTORY_COLUMNS = (
    "tree_id",
    "city",
    "data_source",
    "species",
    "plant_date",
    "diameter_at_breast_height",
    "latitude",
    "longitude",
)


def _read_inventory(config: ProjectConfig) -> pd.DataFrame:
    inventory = config.inventory
    excluded = ", ".join("?" for _ in inventory.excluded_species)
    columns = ", ".join(INVENTORY_COLUMNS)
    query = f"""
        select {columns}
        from read_parquet(?)
        where city = ?
          and latitude is not null
          and longitude is not null
          and diameter_at_breast_height between ? and ?
          and species is not null
          and species not in ({excluded})
    """
    parameters: list[object] = [
        inventory.parquet_url,
        inventory.city,
        inventory.min_dbh_in,
        inventory.max_dbh_in,
        *inventory.excluded_species,
    ]
    return duckdb.execute(query, parameters).fetchdf()


def export_inventory(config: ProjectConfig) -> dict[str, object]:
    output_dir = config.paths.root / "inventory" / config.inventory.city.lower()
    output_dir.mkdir(parents=True, exist_ok=True)

    frame = assign_spatial_splits(_read_inventory(config), config.split, config.seed)
    training_rows = frame[(frame["split"] == "train") & frame["split_eligible"]]
    taxonomy = build_taxonomy(
        training_rows,
        min_species_examples=config.inventory.min_species_examples,
        max_species_classes=config.inventory.max_species_classes,
    )
    frame = encode_taxonomy(frame, taxonomy)
    frame["dbh_log1p"] = np.log1p(frame["diameter_at_breast_height"].astype("float32"))

    inventory_path = output_dir / "inventory.parquet"
    taxonomy_path = output_dir / "taxonomy.json"
    summary_path = output_dir / "summary.json"
    frame.to_parquet(inventory_path, index=False)
    taxonomy_path.write_text(
        json.dumps(taxonomy.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    eligible = frame[frame["split_eligible"]]
    summary: dict[str, object] = {
        "city": config.inventory.city,
        "rows": len(frame),
        "eligible_rows": len(eligible),
        "selected_species": len(taxonomy.species),
        "selected_genera": len(taxonomy.genera),
        "split_rows": {
            name: int(count)
            for name, count in eligible.groupby("split", observed=True).size().items()
        },
        "paths": {
            "inventory": str(inventory_path),
            "taxonomy": str(taxonomy_path),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
