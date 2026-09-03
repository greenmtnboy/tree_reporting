from __future__ import annotations

import json

import duckdb
import numpy as np
import pandas as pd

from urban_tree_ml.config import InventoryConfig, ProjectConfig
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
    columns = ", ".join(INVENTORY_COLUMNS)
    query = f"""
        select {columns}
        from read_parquet(?)
        where city = ?
          and latitude is not null
          and longitude is not null
          and isfinite(latitude)
          and isfinite(longitude)
          and latitude between -90 and 90
          and longitude between -180 and 180
    """
    parameters: list[object] = [config.inventory.parquet_url, config.inventory.city]
    return duckdb.execute(query, parameters).fetchdf()


def _add_source_label_eligibility(
    frame: pd.DataFrame, inventory: InventoryConfig
) -> pd.DataFrame:
    """Keep every located tree while marking which optional labels are credible."""
    result = frame.copy()
    dbh = pd.to_numeric(result["diameter_at_breast_height"], errors="coerce")
    result["diameter_at_breast_height"] = dbh
    result["dbh_eligible"] = (
        np.isfinite(dbh) & dbh.between(inventory.min_dbh_in, inventory.max_dbh_in)
    )

    species = result["species"].astype("string").str.strip()
    result["species"] = species
    result["taxon_eligible"] = (
        species.notna()
        & species.ne("")
        & ~species.isin(inventory.excluded_species)
    )
    return result


def export_inventory(config: ProjectConfig) -> dict[str, object]:
    output_dir = config.paths.root / "inventory" / config.inventory.city.lower()
    output_dir.mkdir(parents=True, exist_ok=True)

    frame = _add_source_label_eligibility(_read_inventory(config), config.inventory)
    frame = assign_spatial_splits(frame, config.split, config.seed)
    training_rows = frame[
        (frame["split"] == "train") & frame["split_eligible"] & frame["taxon_eligible"]
    ]
    taxonomy = build_taxonomy(
        training_rows,
        min_species_examples=config.inventory.min_species_examples,
        max_species_classes=config.inventory.max_species_classes,
    )
    frame = encode_taxonomy(frame, taxonomy)
    frame["genus_eligible"] = frame["taxon_eligible"] & frame["genus_id"].ge(0)
    frame["species_eligible"] = frame["taxon_eligible"] & frame["species_id"].ge(0)
    frame["dbh_log1p"] = np.nan
    frame.loc[frame["dbh_eligible"], "dbh_log1p"] = np.log1p(
        frame.loc[frame["dbh_eligible"], "diameter_at_breast_height"]
    )

    inventory_path = output_dir / "inventory.parquet"
    taxonomy_path = output_dir / "taxonomy.json"
    summary_path = output_dir / "summary.json"
    frame.to_parquet(inventory_path, index=False)
    taxonomy_path.write_text(
        json.dumps(taxonomy.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    eligible = frame[frame["split_eligible"]]
    label_columns = {
        "detection": None,
        "dbh": "dbh_eligible",
        "genus": "genus_eligible",
        "species": "species_eligible",
    }
    summary: dict[str, object] = {
        "city": config.inventory.city,
        "rows": len(frame),
        "eligible_rows": len(eligible),
        "eligible_labels": {
            "detection": len(eligible),
            "dbh": int(eligible["dbh_eligible"].sum()),
            "genus": int(eligible["genus_eligible"].sum()),
            "species": int(eligible["species_eligible"].sum()),
        },
        "selected_species": len(taxonomy.species),
        "selected_genera": len(taxonomy.genera),
        "split_rows": {
            name: int(count)
            for name, count in eligible.groupby("split", observed=True).size().items()
        },
        "split_labels": {
            str(split): {
                label: len(group) if column is None else int(group[column].sum())
                for label, column in label_columns.items()
            }
            for split, group in eligible.groupby("split", observed=True)
        },
        "paths": {
            "inventory": str(inventory_path),
            "taxonomy": str(taxonomy_path),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
