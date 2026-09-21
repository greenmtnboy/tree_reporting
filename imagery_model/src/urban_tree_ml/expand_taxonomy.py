"""Stage an append-only vocabulary from retained training targets; never mutate live inputs."""

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from urban_tree_ml.taxonomy import Taxonomy, genus_for_species


def expanded_taxonomy(rows, base, minimum=100):
    if minimum < 1:
        raise ValueError("minimum support must be positive")
    training = rows[(rows.split == "train") & rows.taxon_eligible].copy()
    support = training.species.value_counts()
    additions = sorted(
        name
        for name, count in support.items()
        if count >= minimum
        and name not in base.species
        and len(name.split()) >= 2
        and name.split()[1].lower().rstrip(".") not in {"genus", "sp", "spp", "unknown"}
    )
    species = [*base.species, *additions]
    genus_support = training.species.map(genus_for_species).value_counts()
    genera = [
        *base.genera,
        *sorted(
            (
                {genus_for_species(name) for name in additions}
                | {name for name, count in genus_support.items() if count >= minimum}
            )
            - set(base.genera)
        ),
    ]
    # Preserve all old IDs, including genus-only labels for long-tail species.
    genera = list(dict.fromkeys(genera))
    return Taxonomy(species, genera, [genera.index(genus_for_species(s)) for s in species])


def stage(root, source_run, output, minimum=100):
    if output.exists():
        raise ValueError("Refusing to overwrite an existing vocabulary")
    frames, hashes = [], {}
    for city in ["ussfo", "usbos"]:
        inventory_path = root / "run-inputs" / source_run / city / "inventory.parquet"
        labels_path = root / "chips" / f"{source_run}-{city}" / "labels.parquet"
        inventory = pd.read_parquet(inventory_path)
        labels = pd.read_parquet(labels_path)
        # Retained train targets already reflect the run's curation/collision exclusions.
        ids = set(labels.loc[labels.split == "train", "tree_id"].astype(str))
        selected = inventory[
            (inventory.split == "train")
            & inventory.split_eligible
            & inventory.tree_id.astype(str).isin(ids)
        ].copy()
        selected["city"] = city
        frames.append(selected)
        for path in [inventory_path, labels_path]:
            hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    base_path = root / "run-inputs" / source_run / "ussfo" / "taxonomy.json"
    hashes[str(base_path)] = hashlib.sha256(base_path.read_bytes()).hexdigest()
    base = Taxonomy(**json.loads(base_path.read_text()))
    rows = pd.concat(frames, ignore_index=True)
    taxonomy = expanded_taxonomy(rows, base, minimum)
    output.mkdir(parents=True)
    (output / "taxonomy.json").write_text(json.dumps(taxonomy.to_dict(), indent=2))
    report = {
        "source_run": source_run,
        "input_sha256": hashes,
        "minimum_train_support": minimum,
        "old_species": len(base.species),
        "new_species": len(taxonomy.species),
        "old_genera": len(base.genera),
        "new_genera": len(taxonomy.genera),
        "added_species": taxonomy.species[len(base.species) :],
        "added_genera": taxonomy.genera[len(base.genera) :],
        "cities": {},
        "status": "candidate only; audit synonyms, rebuild labels and train a NEW checkpoint",
        "support_basis": "retained training inventory targets, not independent chips",
    }
    for city, frame in rows.groupby("city"):
        eligible = frame[frame.taxon_eligible]
        report["cities"][city] = {
            "retained_train_targets": len(frame),
            "taxon_eligible": len(eligible),
            "old_species_supported": int(eligible.species.isin(base.species).sum()),
            "new_species_supported": int(eligible.species.isin(taxonomy.species).sum()),
            "new_genus_supported": int(
                eligible.species.map(genus_for_species).isin(taxonomy.genera).sum()
            ),
        }
    (output / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum", type=int, default=100)
    args = parser.parse_args()
    stage(args.root, args.source_run, args.output, args.minimum)
