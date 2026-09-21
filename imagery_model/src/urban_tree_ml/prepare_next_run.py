"""Prepare encoded inventories without changing live curation or starting training."""

import argparse
import hashlib
import json
import types
from pathlib import Path

import pandas as pd

from urban_tree_ml.profile_vocabulary import taxon_parts
from urban_tree_ml.taxonomy import Taxonomy


def reencode(frame, taxonomy, normalize):
    result = frame.copy()
    result["source_species"] = result.species
    result["species"] = result.species.map(lambda s: normalize(s) if isinstance(s, str) else None)
    result["genus"] = result.species.map(lambda s: taxon_parts(s)[0])
    species_ids = {s: i for i, s in enumerate(taxonomy.species)}
    genus_ids = {g: i for i, g in enumerate(taxonomy.genera)}
    result["species_id"] = result.species.map(species_ids).fillna(-1).astype("int32")
    result["genus_id"] = result.genus.map(genus_ids).fillna(-1).astype("int32")
    result["taxon_eligible"] = result.genus.notna()
    result["species_eligible"] = result.taxon_eligible & result.species_id.ge(0)
    result["genus_eligible"] = result.taxon_eligible & result.genus_id.ge(0)
    validate_encoding(result, taxonomy)
    return result


def validate_encoding(frame, taxonomy):
    if len(set(taxonomy.species)) != len(taxonomy.species):
        raise ValueError("Duplicate species IDs")
    if len(set(taxonomy.genera)) != len(taxonomy.genera):
        raise ValueError("Duplicate genus IDs")
    if taxonomy.species_to_genus != [
        taxonomy.genera.index(taxon_parts(s)[0]) for s in taxonomy.species
    ]:
        raise ValueError("Species/genus mapping mismatch")
    for column, vocabulary in [("species", taxonomy.species), ("genus", taxonomy.genera)]:
        expected = frame[column].map({s: i for i, s in enumerate(vocabulary)}).fillna(-1)
        if not expected.eq(frame[column + "_id"]).all():
            raise ValueError(f"Stale {column} IDs: re-encode frozen inventory")
        if not (frame.taxon_eligible & expected.ge(0)).eq(frame[column + "_eligible"]).all():
            raise ValueError(f"Stale {column} eligibility mask")


def prepare(root, source_run, profile, output):
    if output.exists():
        raise ValueError("Output already exists")
    metadata = json.loads((profile / "manifest.json").read_text())
    source = (profile / "upstream_ingest_snapshot.py").read_bytes()
    if hashlib.sha256(source).hexdigest() != metadata["normalizer_sha256"]:
        raise ValueError("Upstream normalizer hash differs from profile")
    module = types.ModuleType("pinned_ingest")
    exec(compile(source, "pinned_ingest.py", "exec"), module.__dict__)
    taxonomy = Taxonomy(**json.loads((profile / "recommended-taxonomy.json").read_text()))
    output.mkdir(parents=True)
    audit = {
        "source_run": source_run,
        "profile": str(profile.resolve()),
        "species": len(taxonomy.species),
        "genera": len(taxonomy.genera),
        "normalizer_sha256": metadata["normalizer_sha256"],
        "cities": {},
        "status": "prepared inventories only; await published curation for final freeze",
    }
    for city in ["ussfo", "usbos"]:
        path = root / "run-inputs" / source_run / city / "inventory.parquet"
        before = pd.read_parquet(path)
        after = reencode(before, taxonomy, module.sanitize_species)
        preserved = [
            c
            for c in before.columns
            if c
            not in {
                "species",
                "genus",
                "species_id",
                "genus_id",
                "taxon_eligible",
                "species_eligible",
                "genus_eligible",
            }
        ]
        pd.testing.assert_frame_equal(before[preserved], after[preserved])
        target = output / city
        target.mkdir()
        after.to_parquet(target / "inventory.parquet", index=False)
        (target / "taxonomy.json").write_text(json.dumps(taxonomy.to_dict(), indent=2))
        audit["cities"][city] = {
            "rows": len(after),
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "encoded_sha256": hashlib.sha256(
                (target / "inventory.parquet").read_bytes()
            ).hexdigest(),
        }
    (output / "preparation.json").write_text(json.dumps(audit, indent=2))
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-run", default="sf-boston-naip-curated-joint-v1")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.root, args.source_run, args.profile, args.output)
