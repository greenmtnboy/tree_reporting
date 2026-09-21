"""Create a small, immutable validation review queue without modifying annotations."""

import csv
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window

from urban_tree_ml.frozen_comparison import match_frame


def build(root, destination, run="sf-boston-naip-vocab-v2-retry1", count=10, exclude=()):
    if destination.exists():
        raise ValueError("Choose a new immutable queue directory")
    rows, inputs, pools = [], {}, {}
    for city, review, cohort, raster in [
        ("ussfo", "ussfo-2022-mosaic", "validation", "ussfo/2022/ussfo-2022-mosaic.vrt"),
        ("usbos", "usbos-2023-external", "validation-usbos", "usbos/2023/usbos-2023-external.vrt"),
    ]:
        folder = root / "runs" / run / "evaluation" / cohort
        config = json.loads((folder / "evaluation-metadata.json").read_text())["config"]
        review_dir = root / "qa/registration" / review
        manifest = json.loads((review_dir / "manifest.json").read_text())
        state = json.loads((review_dir / "reviews.json").read_text())
        for path in [
            folder / "predictions.parquet",
            folder / "ground-truth.parquet",
            folder / "evaluation-metadata.json",
            review_dir / "manifest.json",
            review_dir / "reviews.json",
        ]:
            inputs[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        done = {key for key, value in state["scene_reviews"].items() if value.get("done")}
        done_chips = {
            s.get("validation_chip_id") for s in manifest["scenes"] if s["scene_id"] in done
        }
        done_trees = {str(s["tree_id"]) for s in manifest["samples"] if s["scene_id"] in done}
        index = pd.read_parquet(root / "chips" / config["dataset"] / "chips.parquet")
        eligible = index[
            (index.split == "validation")
            & ~index.chip_id.isin(done_chips)
            & index.candidate_tree_count.between(8, 60)
            & (index.collision_excluded_count <= 5)
        ]
        truth = pd.read_parquet(folder / "ground-truth.parquet")
        pred = pd.read_parquet(folder / "predictions.parquet")
        pred = pred[(pred.score >= 0.35) & pred.chip_id.isin(eligible.chip_id)]
        truth = truth[truth.chip_id.isin(eligible.chip_id)].reset_index(drop=True)
        matched = match_frame(pred.reset_index(drop=True), truth, 4 / 1.2)
        candidates = []
        for chip in eligible.itertuples():
            if city + ':' + chip.chip_id in exclude:
                continue
            t = truth[truth.chip_id == chip.chip_id]
            if t.empty or set(t.tree_id.astype(str)) <= done_trees:
                continue
            p = matched[matched.chip_id == chip.chip_id]
            tp = int((p.truth_index >= 0).sum())
            missed = len(t) - tp
            if missed < 5:
                continue
            candidates.append(
                {
                    "city": city,
                    "chip_id": chip.chip_id,
                    "queue": "diagnostic",
                    "missed": missed,
                    "truth": len(t),
                    "matched": tp,
                    "candidate_trees": int(chip.candidate_tree_count),
                    "collisions": int(chip.collision_excluded_count),
                    "row": int(chip.row_offset),
                    "col": int(chip.column_offset),
                    "already_reviewed_truth": int(t.tree_id.astype(str).isin(done_trees).sum()),
                }
            )
        candidates.sort(key=lambda r: (-r["missed"], r["candidate_trees"], r["chip_id"]))
        chosen = []
        with rasterio.open(root / "imagery" / raster) as source:
            for item in candidates:
                raw = source.read(
                    [1, 2, 3, 4], window=Window(item["col"], item["row"], 256, 256)
                ).astype("float32")
                ndvi = (raw[3] - raw[0]) / np.maximum(raw[3] + raw[0], 1)
                vegetation = float(np.mean(ndvi > 0.3))
                item["vegetated_fraction"] = round(vegetation, 4)
                if vegetation > 0.55:
                    continue
                item["reason"] = (
                    f"Sparse-canopy misses: {item['missed']}/{item['truth']} missed at 4 m / 0.35; "
                    f"{item['candidate_trees']} inventory candidates, "
                    f"{vegetation:.0%} NDVI vegetation; "
                    "density proxy, not confirmed independent crowns"
                )
                run_id = run if city == "ussfo" else run + "::validation-usbos"
                item["url"] = "http://127.0.0.1:8765/model?" + urlencode(
                    {
                        "run": run_id,
                        "city": city,
                        "assignment": "diagnostic",
                        "unreviewed": "1",
                        "radius": "4.0",
                        "threshold": ".35",
                        "sort": "missed",
                        "limit": "12",
                        "chip": item["chip_id"],
                    }
                )
                chosen.append(item)
                if len(chosen) == count:
                    break
        if len(chosen) != count:
            raise ValueError(
                f"{city}: only {len(chosen)} eligible; do not silently relax density limits"
            )
        pools[city] = {"metadata_candidates": len(candidates), "selected": len(chosen)}
        rows.extend(chosen)
    destination.mkdir(parents=True)
    target = destination / "validation-targets.csv"
    with target.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    metadata = {
        "new_run": run,
        "visual_density_exclusions": list(exclude),
        "policy": "10 per city, validation only, >=5 missed at 4m/.35; "
        "8–60 candidate trees, <=5 collision exclusions, <=55% NDVI>.3; "
        "exclude done chips and fully reviewed truth sets. Ranked by missed count.",
        "test_evaluated": False,
        "inputs": inputs,
        "pools": pools,
        "output_sha256": {target.name: hashlib.sha256(target.read_bytes()).hexdigest()},
    }
    (destination / "manifest.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    report = "# Sparse-canopy missed-tree review targets\n\n"
    report += (
        metadata["policy"] + "\n\nUses the last completed run, not the running curation-v3 model. "
    )
    report += (
        "NDVI cover and inventory count are proxies; grass and unlabeled trees can confound them. "
    )
    report += (
        "No annotations changed. These are validation-label audits, not new training chips.\n\n"
    )
    report += (
        "| City | Chip | Missed / truth | Candidate trees | Vegetated |\n|---|---|---:|---:|---:|\n"
    )
    for row in rows:
        report += (
            f"| {row['city']} | [{row['chip_id']}]({row['url']}) | "
            f"{row['missed']}/{row['truth']} | {row['candidate_trees']} | "
            f"{row['vegetated_fraction']:.0%} |\n"
        )
    (destination / "REPORT.md").write_text(report, encoding="utf-8")
    (destination / "COMPLETE").write_text("Queue complete; no training or annotation mutation.\n")
    return rows


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.output, exclude=args.exclude), indent=2))
