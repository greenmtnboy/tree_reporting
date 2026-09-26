"""Profile retained training taxa using the upstream ingest normalizer, not a second alias map."""

import argparse
import csv
import hashlib
import json
import subprocess
import types
from pathlib import Path

import pandas as pd


def taxon_parts(name):
    if not isinstance(name, str) or not name.strip():
        return None, False
    words = name.split()
    if words[0].lower() in {"x", "×"}:
        return " ".join(words[:2]), len(words) >= 3
    return words[0], len(words) >= 2


def coverage(frame, species, genera):
    return {
        "trees": len(frame),
        "species_supported": int(frame.canonical.isin(species).sum()),
        "genus_supported": int(frame.genus.isin(genera).sum()),
        "genus_only": int((~frame.canonical.isin(species) & frame.genus.isin(genera)).sum()),
    }


def write_rows(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def profile(root, upstream, output, run):
    if output.exists():
        raise ValueError("Choose a new output directory")
    source = subprocess.check_output(
        ["git", "-C", str(upstream), "show", "origin/main:data/raw/_ingest_shared.py"]
    )
    revision = subprocess.check_output(
        ["git", "-C", str(upstream), "rev-parse", "origin/main"], text=True
    ).strip()
    module = types.ModuleType("upstream_ingest_profile")
    exec(compile(source, "upstream/_ingest_shared.py", "exec"), module.__dict__)
    frames, hashes = [], {}
    for city in ["ussfo", "usbos"]:
        inventory_path = root / "run-inputs" / run / city / "inventory.parquet"
        labels_path = root / "chips" / f"{run}-{city}" / "labels.parquet"
        inventory = pd.read_parquet(inventory_path, filters=[("split", "=", "train")])
        labels = pd.read_parquet(labels_path, filters=[("split", "=", "train")])
        inventory["tree_id"] = inventory.tree_id.astype(str)
        labels["tree_id"] = labels.tree_id.astype(str)
        frame = labels[["tree_id", "chip_id"]].merge(
            inventory.drop(columns=["chip_id"], errors="ignore"),
            on="tree_id",
            validate="one_to_one",
        )
        if len(frame) != len(labels) or not frame.split_eligible.all():
            raise ValueError("Retained training labels do not reconcile with frozen inventory")
        frame["city"] = city
        frame["chip_key"] = city + ":" + frame.chip_id
        frame["block_key"] = (
            city + ":" + frame.split_block_x.astype(str) + ":" + frame.split_block_y.astype(str)
        )
        frame["canonical"] = frame.species.map(
            lambda value: module.sanitize_species(value) if isinstance(value, str) else None
        )
        frame["genus"] = frame.canonical.map(lambda name: taxon_parts(name)[0])
        frame["species_rank"] = frame.canonical.map(lambda name: taxon_parts(name)[1])
        frames.append(frame)
        hashes[str(inventory_path)] = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
        hashes[str(labels_path)] = hashlib.sha256(labels_path.read_bytes()).hexdigest()
    rows = pd.concat(frames, ignore_index=True)
    output.mkdir(parents=True)
    (output / "upstream_ingest_snapshot.py").write_bytes(source)
    support = []
    for name, group in rows[rows.species_rank].groupby("canonical"):
        support.append(
            {
                "species": name,
                "trees": len(group),
                "chips": group.chip_key.nunique(),
                "blocks": group.block_key.nunique(),
                "ussfo": int((group.city == "ussfo").sum()),
                "usbos": int((group.city == "usbos").sum()),
            }
        )
    support.sort(key=lambda r: (-r["trees"], r["species"]))
    write_rows(output / "species-support.csv", support)
    genus_support = [
        {
            "genus": name,
            "trees": len(group),
            "chips": group.chip_key.nunique(),
            "blocks": group.block_key.nunique(),
        }
        for name, group in rows.groupby("genus")
    ]
    write_rows(output / "genus-support.csv", genus_support)
    changes = []
    for (city, raw), group in rows.groupby(["city", "species"]):
        canonical = group.canonical.iloc[0]
        canonical = canonical if isinstance(canonical, str) else None
        if raw != canonical:
            changes.append(
                {"city": city, "input": raw, "canonical": canonical, "trees": len(group)}
            )
    if changes:
        write_rows(output / "normalization-changes.csv", changes)
    profiles = []
    for minimum in [100, 250, 500, 1000]:
        for cap in [64, 96, 128, 192, 10000]:
            chosen = [r["species"] for r in support if r["trees"] >= minimum and r["chips"] >= 10][
                :cap
            ]
            for genus_minimum in [100, 250, 500]:
                genera = sorted(
                    {taxon_parts(s)[0] for s in chosen}
                    | {
                        g["genus"]
                        for g in genus_support
                        if g["trees"] >= genus_minimum and g["chips"] >= 10
                    }
                )
                for city, frame in rows.groupby("city"):
                    profiles.append(
                        {
                            "minimum": minimum,
                            "cap": cap,
                            "genus_minimum": genus_minimum,
                            "species_classes": len(chosen),
                            "genus_classes": len(genera),
                            "city": city,
                            **coverage(frame, chosen, genera),
                        }
                    )
    write_rows(output / "cutoff-profiles.csv", profiles)
    audit = {
        "upstream_revision": revision,
        "normalizer_sha256": hashlib.sha256(source).hexdigest(),
        "source_run": run,
        "inputs_sha256": hashes,
        "training_only": True,
        "changed_training_rows": sum(r["trees"] for r in changes),
        "changed_source_names": len({r["input"] for r in changes}),
        "renamed_taxon_rows": sum(r["trees"] for r in changes if r["canonical"] is not None),
        "non_taxon_rows_mapped_to_null": sum(r["trees"] for r in changes if r["canonical"] is None),
        "minimum_distinct_chips": 10,
        "note": "Frozen inputs use current upstream normalization; live inventory untouched",
    }
    (output / "manifest.json").write_text(json.dumps(audit, indent=2))
    chosen = [r["species"] for r in support if r["trees"] >= 250 and r["chips"] >= 10][:128]
    genera = sorted(
        {taxon_parts(s)[0] for s in chosen}
        | {g["genus"] for g in genus_support if g["trees"] >= 100 and g["chips"] >= 10}
    )
    (output / "recommended-taxonomy.json").write_text(
        json.dumps(
            {
                "species": chosen,
                "genera": genera,
                "species_to_genus": [genera.index(taxon_parts(s)[0]) for s in chosen],
            },
            indent=2,
        )
    )
    lines = [
        "# Cross-city vocabulary profile",
        "",
        f"Upstream normalizer: `{revision}`.",
        "",
        f"Normalization changes {audit['changed_training_rows']:,} retained training rows "
        f"across {audit['changed_source_names']} input names. No live annotations changed.",
        "",
        "Training-only support, at least 10 distinct chips; genus cutoff 100:",
        "",
        "| Min trees | Cap | Species | Genera | City | Species coverage | Genus coverage |",
        "|---:|---:|---:|---:|---|---:|---:|",
    ]
    for p in profiles:
        if p["genus_minimum"] == 100 and p["cap"] in [64, 96, 128, 10000]:
            lines.append(
                f"| {p['minimum']} | {p['cap']} | {p['species_classes']} | "
                f"{p['genus_classes']} | {p['city']} | "
                f"{100 * p['species_supported'] / p['trees']:.1f}% | "
                f"{100 * p['genus_supported'] / p['trees']:.1f}% |"
            )
    lines += [
        "",
        "Coverage denominators include ALL retained training trees, including unknown taxa.",
        "Ten chips is a heuristic, not proof of spatial independence; block counts are exported.",
        "Genus support includes genus-only source labels and rare species. Detection is unchanged.",
        "These are fresh vocabularies, not append-only legacy IDs. Re-encode both cities together; "
        "old checkpoints must retain their old taxonomy. No validation/test support used.",
    ]
    (output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", default="sf-boston-naip-curated-joint-v1")
    args = parser.parse_args()
    profile(args.root, args.upstream, args.output, args.run)
