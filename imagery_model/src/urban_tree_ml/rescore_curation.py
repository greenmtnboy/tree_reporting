"""Same saved predictions, new published curation; CPU-only label-version audit."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer

from urban_tree_ml.feedback import _json_sha256, load_persisted_reviews
from urban_tree_ml.frozen_comparison import counts, match_frame


def labels_from_feedback(inventory, feedback, raster, size, stride, cohort):
    import rasterio

    frame = inventory[inventory.split_eligible].copy()
    excluded = {str(r["tree_id"]) for r in feedback["exclusions"]}
    corrections = {
        (str(r["tree_id"]), r["split"]): (r["east_m"], r["north_m"])
        for r in feedback["point_corrections"]
    }
    registration = feedback["registration"]
    with rasterio.open(raster) as source:
        transform = Transformer.from_crs("EPSG:4326", source.crs, always_xy=True)
        x, y = transform.transform(frame.longitude.to_numpy(), frame.latitude.to_numpy())
        offsets = np.array(
            [
                corrections.get((str(t), s), (registration["east_m"], registration["north_m"]))
                for t, s in zip(frame.tree_id, frame.split, strict=True)
            ]
        )
        col, row = (~source.transform) * (x + offsets[:, 0], y + offsets[:, 1])
        inside = (col >= 0) & (row >= 0) & (col < source.width) & (row < source.height)
    frame["pixel_col"], frame["pixel_row"] = col, row
    frame = frame[inside].copy()
    frame["chip_id"] = [
        f"r{int(y // size):06d}_c{int(x // size):06d}"
        for x, y in zip(frame.pixel_col, frame.pixel_row, strict=True)
    ]
    frame = frame[frame.chip_id.isin(cohort) & ~frame.tree_id.astype(str).isin(excluded)].copy()
    mixed = frame.groupby("chip_id").split.nunique()
    if (mixed > 1).any():
        raise ValueError("Correction created mixed split chip; explicit cohort policy required")
    frame = frame[frame.split == "validation"].copy()
    frame["output_x"] = np.minimum(
        np.rint((frame.pixel_col % size) / stride), size // stride - 1
    ).astype(int)
    frame["output_y"] = np.minimum(
        np.rint((frame.pixel_row % size) / stride), size // stride - 1
    ).astype(int)
    frame = frame[~frame.duplicated(["chip_id", "output_x", "output_y"], keep=False)].copy()
    for target, eligible in [
        ("dbh_log1p", "dbh_eligible"),
        ("genus_id", "genus_eligible"),
        ("species_id", "species_eligible"),
    ]:
        frame.loc[~frame[eligible], target] = np.nan
    columns = [
        "tree_id",
        "split",
        "chip_id",
        "longitude",
        "latitude",
        "pixel_col",
        "pixel_row",
        "output_x",
        "output_y",
        "dbh_log1p",
        "genus_id",
        "species_id",
    ]
    return frame.sort_values("chip_id", kind="stable")[columns].reset_index(drop=True)


def build(root, output):
    model = "sf-boston-naip-curation-v3-retry2"
    source = root / "runs" / model / "evaluation/validation-usbos"
    review = root / "qa/registration/usbos-2023-external"
    names = ["manifest.json", "reviews.json", "training-feedback.json"]
    snapshot = {n: (review / n).read_bytes() for n in names}
    state = load_persisted_reviews(review)
    feedback = json.loads(snapshot["training-feedback.json"])
    manifest = json.loads(snapshot["manifest.json"])
    assert feedback["source_reviews_sha256"] == state["state_revision"], "Publish Boston first"
    assert feedback["source_manifest_sha256"] == _json_sha256(manifest), "Publish Boston first"
    assert all((review / n).read_bytes() == data for n, data in snapshot.items()), (
        "Reviews changed; retry"
    )
    output.mkdir(parents=True, exist_ok=False)
    for n, data in snapshot.items():
        (output / n).write_bytes(data)
    meta = json.loads((source / "evaluation-metadata.json").read_text())
    config = meta["config"]
    frozen = root / "run-inputs" / model / "usbos"
    inventory = pd.read_parquet(frozen / "inventory.parquet")
    old_feedback = json.loads((frozen / "training-feedback.json").read_text())
    old_truth = pd.read_parquet(source / "ground-truth.parquet")
    predictions = pd.read_parquet(source / "predictions.parquet")
    chips = set(predictions.chip_id) | set(old_truth.chip_id)
    assert len(chips) == json.loads((source / "metrics.json").read_text())["chips"]
    raster = root / "imagery/usbos/2023/usbos-2023-external.vrt"
    args = (raster, config["imagery"]["chip_pixels"], config["targets"]["output_stride"], chips)
    reproduced = labels_from_feedback(inventory, old_feedback, *args)
    pd.testing.assert_frame_equal(
        reproduced.sort_values("tree_id").reset_index(drop=True),
        old_truth.sort_values("tree_id").reset_index(drop=True),
        check_dtype=False,
        check_exact=False,
        atol=1e-7,
        rtol=1e-10,
    )
    new_truth = labels_from_feedback(inventory, feedback, *args)
    new_truth.to_parquet(output / "new-ground-truth.parquet", index=False)
    old_truth.to_parquet(output / "old-ground-truth.parquet", index=False)
    pred_path = source / "predictions.parquet"
    (output / "predictions.parquet").write_bytes(pred_path.read_bytes())
    changed = set()
    for chip in chips:
        a = old_truth[old_truth.chip_id == chip].sort_values("tree_id").reset_index(drop=True)
        b = new_truth[new_truth.chip_id == chip].sort_values("tree_id").reset_index(drop=True)
        try:
            pd.testing.assert_frame_equal(a, b, check_dtype=False, atol=1e-7, rtol=1e-10)
        except AssertionError:
            changed.add(chip)
    results = []
    for scope, ids in [("all-validation", chips), ("changed-chips", changed)]:
        pred = predictions[(predictions.score >= 0.35) & predictions.chip_id.isin(ids)]
        for radius in [2.0, 4.0]:
            for label, truth in [("old", old_truth), ("new", new_truth)]:
                truth = truth[truth.chip_id.isin(ids)]
                matches = match_frame(
                    pred,
                    truth,
                    radius / config["imagery"]["resolution_m"] / config["targets"]["output_stride"],
                )
                results.append(
                    {
                        "scope": scope,
                        "chips": len(ids),
                        "radius_m": radius,
                        "labels": label,
                        "truth": len(truth),
                        **counts(matches, len(truth)),
                    }
                )
    result = {
        "model": model,
        "threshold": 0.35,
        "baseline_labels_reproduced": True,
        "prediction_sha256": hashlib.sha256(pred_path.read_bytes()).hexdigest(),
        "review_revision": state["state_revision"],
        "changed_chips": sorted(changed),
        "regions": len(feedback.get("region_overrides", [])),
        "results": results,
    }
    (output / "metrics.json").write_text(json.dumps(result, indent=2))
    lines = [
        "# Boston: same checkpoint, updated curation",
        "",
        f"Model: {model}; confidence 0.35. Original labels reproduced before scoring.",
        "",
        "| Cohort | Radius | Labels | Trees | Precision | Recall | F2 |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for row in results:
        lines.append(
            f"| {row['scope']} ({row['chips']}) | {row['radius_m']}m | {row['labels']} | "
            f"{row['truth']} | {row['precision']:.1%} | {row['recall']:.1%} | {row['f2']:.1%} |"
        )
    lines += [
        "",
        "These are label effects, not model improvements. Same predictions, chip cohort, "
        "threshold and matching rule. No retraining, GPU inference, test evaluation or live edits.",
        "Detection matching does not apply loss-mask regions. Dense logits were not saved, "
        "so validation loss was not recomputed. Changed-chip results are selected diagnostics, "
        "not a representative estimate. Frozen inventory/taxonomy held fixed.",
    ]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n")
    (output / "COMPLETE").write_text("Same-checkpoint label rescore complete.\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.root, args.output)
