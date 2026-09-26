"""CPU-only, immutable rescore of saved validation predictions against one label snapshot.

Run with python -m urban_tree_ml.frozen_comparison --root ARTIFACTS --output NEW_DIR.
No training, inference, test evaluation, or annotation mutation is performed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd

from urban_tree_ml.evaluation import _attribute_metrics

OLD = "sf-naip-rgbn-species-citywide-curated-v2"
NEW = "sf-boston-naip-curated-joint-v1"


def write_csv(frame, path):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(frame.columns))
        writer.writeheader()
        writer.writerows(
            dict(zip(frame.columns, row, strict=True))
            for row in frame.itertuples(index=False, name=None)
        )


def match_frame(predictions, truth, radius):
    """Confidence-ordered nearest-unused matching, identical to evaluation semantics."""
    result = predictions.sort_values("score", ascending=False, kind="stable").copy()
    result["truth_index"] = -1
    result["distance_px"] = np.nan
    groups = {str(k): v for k, v in truth.groupby("chip_id", sort=False)}
    for chip, group in result.groupby("chip_id", sort=False):
        targets = groups.get(str(chip))
        if targets is None or targets.empty:
            continue
        xy = targets[["output_x", "output_y"]].to_numpy(dtype=float)
        indices = targets.index.to_numpy()
        used = np.zeros(len(targets), dtype=bool)
        for row in group.itertuples():
            distances = np.hypot(xy[:, 0] - row.output_x, xy[:, 1] - row.output_y)
            distances[used] = np.inf
            nearest = int(np.argmin(distances))
            if distances[nearest] <= radius:
                result.at[row.Index, "truth_index"] = indices[nearest]
                result.at[row.Index, "distance_px"] = distances[nearest]
                used[nearest] = True
    return result


def counts(frame, truth_count):
    tp = int((frame.truth_index >= 0).sum())
    fp, fn = len(frame) - tp, truth_count - tp
    return {
        "tp": tp,
        "unmatched_predictions": fp,
        "missed": fn,
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / truth_count if truth_count else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
        "f2": 5 * tp / (5 * tp + fp + 4 * fn) if 5 * tp + fp + 4 * fn else 0.0,
    }


def average_precision(frame, truth_count):
    if not truth_count:
        return None
    tp = frame.sort_values("score", ascending=False, kind="stable").truth_index >= 0
    recall = np.r_[0.0, np.cumsum(tp) / truth_count, 1.0]
    precision = np.r_[0.0, np.cumsum(tp) / np.arange(1, len(tp) + 1), 0.0]
    precision = np.maximum.accumulate(precision[::-1])[::-1]
    changes = np.flatnonzero(recall[1:] != recall[:-1]) + 1
    return float(np.sum((recall[changes] - recall[changes - 1]) * precision[changes]))


def load_metadata(directory):
    path = directory / "evaluation-metadata.json"
    if not path.exists():
        path = directory.parent.parent / "run-metadata.json"
    return json.loads(path.read_text()), path


def assert_compatible(old_meta, new_meta, old_pred, new_pred):
    for section, keys in {
        "imagery": ["resolution_m", "chip_pixels", "bands"],
        "targets": ["output_stride"],
        "evaluation": ["nms_kernel", "max_detections_per_chip"],
    }.items():
        for key in keys:
            if old_meta["config"][section][key] != new_meta["config"][section][key]:
                raise ValueError(f"Incompatible {section}.{key}")
    rasters = [
        m["config"]["imagery"]["local_raster"].replace("\\", "/").split("/")[-1]
        for m in [old_meta, new_meta]
    ]
    if rasters[0] != rasters[1]:
        raise ValueError("Different source rasters")
    keys = ["chip_id", "output_x", "output_y"]
    shared = old_pred.merge(new_pred, on=keys, suffixes=("_old", "_new"))
    if shared.empty:
        raise ValueError("Cannot verify geographic alignment: no shared prediction pixels")
    for col, tolerance in [
        ("pixel_col", 0),
        ("pixel_row", 0),
        ("longitude", 1e-8),
        ("latitude", 1e-8),
    ]:
        if (shared[col + "_old"] - shared[col + "_new"]).abs().max() > tolerance:
            raise ValueError(f"Prediction geometry differs: {col}")


def select_targets(chips, per_city=50):
    """Half density-stratified random audit, half diagnostic; disjoint and deterministic."""
    frame = chips.copy()
    frame["density"] = pd.cut(
        frame.truth_count, [-1, 5, 25, np.inf], labels=["sparse", "medium", "dense"]
    ).astype(str)
    random_count = per_city // 2
    sampled = []
    for position, (_, group) in enumerate(frame.groupby("density", sort=True)):
        n = random_count // 3 + int(position < random_count % 3)
        sampled.extend(group.sample(n=min(n, len(group)), random_state=20260906).index)
    remaining = frame.drop(index=sampled)
    if len(sampled) < random_count:
        sampled.extend(
            remaining.sample(
                n=min(random_count - len(sampled), len(remaining)), random_state=20260906
            ).index
        )
    representative = frame.loc[sampled].copy()
    representative["queue"] = "representative audit"
    representative["reason"] = "Density-stratified random sample; audit without model overlay first"
    remaining = frame.drop(index=sampled).copy()
    remaining["priority"] = (
        remaining.new_missed
        + remaining.new_unmatched_predictions
        + (remaining.old_f2 - remaining.new_f2).clip(lower=0) * 30
    )
    diagnostic = remaining.sort_values(
        ["reviewed_now", "priority", "chip_id"], ascending=[True, False, True]
    ).head(per_city - len(sampled))
    diagnostic = diagnostic.copy()
    diagnostic["queue"] = "diagnostic"
    diagnostic["reason"] = diagnostic.apply(
        lambda r: (
            "Regression vs previous model"
            if r.old_f2 > r.new_f2 + 0.05
            else "Many missed inventory targets"
            if r.new_missed >= r.new_unmatched_predictions
            else "Many unmatched predictions: check unlabeled trees"
        ),
        axis=1,
    )
    return pd.concat([representative, diagnostic], ignore_index=True)


def build(root: Path, output: Path, studio: str):
    if output.exists():
        raise ValueError("Output already exists; choose a new snapshot directory")
    output.mkdir(parents=True)
    provenance = {
        "created_at": datetime.now(UTC).isoformat(),
        "old_run": OLD,
        "new_run": NEW,
        "truth_source": "Joint-run evaluation exports; NOT current unpublished edits",
        "files": {},
        "cities": {},
    }
    metrics, curves, all_chips, all_targets, size_rows = [], [], [], [], []

    def freeze(path, destination):
        content = path.read_bytes()
        provenance["files"][str(path)] = hashlib.sha256(content).hexdigest()
        destination.write_bytes(content)

    for city, old_cohort, new_cohort in [
        ("ussfo", "validation", "validation"),
        ("usbos", "external-usbos", "validation-usbos"),
    ]:
        print(f"Comparing {city}...", flush=True)
        folder = output / city
        folder.mkdir()
        old_dir, new_dir = [
            root / "runs" / run / "evaluation" / cohort
            for run, cohort in [(OLD, old_cohort), (NEW, new_cohort)]
        ]
        old_meta, old_meta_path = load_metadata(old_dir)
        new_meta, new_meta_path = load_metadata(new_dir)
        if new_meta["split"] != "validation":
            raise ValueError("Only validation is permitted")
        for name, path in [
            ("old-metadata.json", old_meta_path),
            ("new-metadata.json", new_meta_path),
            ("ground-truth.parquet", new_dir / "ground-truth.parquet"),
            ("old-ground-truth.parquet", old_dir / "ground-truth.parquet"),
            ("taxonomy.json", new_dir / "taxonomy.json"),
            ("old-taxonomy.json", old_dir / "taxonomy.json"),
            ("old-predictions.parquet", old_dir / "predictions.parquet"),
            ("new-predictions.parquet", new_dir / "predictions.parquet"),
        ]:
            freeze(path, folder / name)
        if json.loads((folder / "taxonomy.json").read_text()) != json.loads(
            (folder / "old-taxonomy.json").read_text()
        ):
            raise ValueError("Taxonomy IDs differ; remapping required")
        truth = pd.read_parquet(folder / "ground-truth.parquet")
        old_truth = pd.read_parquet(folder / "old-ground-truth.parquet")
        if set(truth.split) != {"validation"} or set(old_truth.split) != {"validation"}:
            raise ValueError("Non-validation labels present")
        old_pred = pd.read_parquet(folder / "old-predictions.parquet")
        new_pred = pd.read_parquet(folder / "new-predictions.parquet")
        assert_compatible(old_meta, new_meta, old_pred, new_pred)
        common = sorted(set(old_pred.chip_id) & set(new_pred.chip_id))
        truth = truth[truth.chip_id.isin(common)].reset_index(drop=True)
        truth.to_parquet(folder / "comparison-truth.parquet", index=False)
        ui_run = NEW if city == "ussfo" else NEW + "::" + new_cohort
        with urlopen(
            studio + "/api/curation-status?" + urlencode({"run": ui_run}), timeout=30
        ) as response:
            status = json.load(response)
        (folder / "review-status-now.json").write_text(json.dumps(status, indent=2))
        reviewed = {k for k, v in status["chips"].items() if v.get("reviewed")}
        chip_rows = {
            chip: {
                "city": city,
                "chip_id": chip,
                "split": "validation",
                "truth_count": int((truth.chip_id == chip).sum()),
                "reviewed_now": chip in reviewed,
                "review_url": studio
                + "/model?"
                + urlencode(
                    {
                        "run": ui_run,
                        "chip": chip,
                        "radius": "4.0",
                        "threshold": "0.35",
                        "unreviewed": "0",
                    }
                ),
                "compare_url": studio + "/compare?" + urlencode({"run": ui_run, "chip": chip}),
            }
            for chip in common
        }
        provenance["cities"][city] = {
            "common_chips": len(common),
            "old_only_chips": sorted(set(old_pred.chip_id) - set(common)),
            "new_only_chips": sorted(set(new_pred.chip_id) - set(common)),
            "frozen_truth_trees": len(truth),
            "reviewed_now_chips": len(set(common) & reviewed),
            "species_eligible_trees": int(truth.species_id.notna().sum()),
        }
        m_per_px = (
            new_meta["config"]["imagery"]["resolution_m"]
            * new_meta["config"]["targets"]["output_stride"]
        )
        for label, predictions in [("old", old_pred), ("new", new_pred)]:
            predictions = predictions[predictions.chip_id.isin(common)].reset_index(drop=True)
            for radius in [2.0, 4.0]:
                matched = match_frame(predictions, truth, radius / m_per_px)
                visible = matched[matched.score >= 0.35]
                for subset, ids in [
                    ("all", set(common)),
                    ("reviewed_now", reviewed),
                    ("not_reviewed_now", set(common) - reviewed),
                ]:
                    selected_truth = truth[truth.chip_id.isin(ids)]
                    selected = visible[visible.chip_id.isin(ids)]
                    full = matched[matched.chip_id.isin(ids)]
                    pairs = [
                        (int(r.Index), int(r.truth_index), float(r.distance_px))
                        for r in selected.itertuples()
                        if r.truth_index >= 0
                    ]
                    metrics.append(
                        {
                            "city": city,
                            "run": label,
                            "radius_m": radius,
                            "subset": subset,
                            "chips": len(set(common) & ids),
                            "truth_count": len(selected_truth),
                            **counts(selected, len(selected_truth)),
                            "ap_saved_candidates": average_precision(full, len(selected_truth)),
                            "attributes": _attribute_metrics(
                                predictions, selected_truth, pairs, dbh_tolerance_in=2.0
                            ),
                        }
                    )
                for threshold in [
                    0.0,
                    0.05,
                    0.1,
                    0.15,
                    0.2,
                    0.25,
                    0.3,
                    0.35,
                    0.4,
                    0.45,
                    0.5,
                    0.6,
                    0.7,
                    0.8,
                    0.9,
                ]:
                    curves.append(
                        {
                            "city": city,
                            "run": label,
                            "radius_m": radius,
                            "threshold": threshold,
                            **counts(matched[matched.score >= threshold], len(truth)),
                        }
                    )
                detected = set(visible.loc[visible.truth_index >= 0, "truth_index"])
                dbh = np.expm1(truth.dbh_log1p)
                for size, mask in [
                    ("unknown", dbh.isna()),
                    ("under_4in", dbh < 4),
                    ("4_to_8in", (dbh >= 4) & (dbh < 8)),
                    ("8_to_16in", (dbh >= 8) & (dbh < 16)),
                    ("16in_plus", dbh >= 16),
                ]:
                    ids = set(truth.index[mask])
                    size_rows.append(
                        {
                            "city": city,
                            "run": label,
                            "radius_m": radius,
                            "dbh_bin": size,
                            "truth_count": len(ids),
                            "detected": len(ids & detected),
                            "recall": len(ids & detected) / len(ids) if ids else None,
                        }
                    )
                if radius == 4:
                    visible_groups = {k: v for k, v in visible.groupby("chip_id")}
                    for chip, row in chip_rows.items():
                        values = counts(
                            visible_groups.get(chip, visible.iloc[:0]), row["truth_count"]
                        )
                        row.update({label + "_" + k: v for k, v in values.items()})
        city_chips = pd.DataFrame(chip_rows.values())
        all_chips.append(city_chips)
        all_targets.append(select_targets(city_chips))
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2))
    write_csv(pd.DataFrame(curves), output / "threshold-curves.csv")
    write_csv(pd.DataFrame(size_rows), output / "size-recall.csv")
    write_csv(pd.concat(all_chips), output / "chip-scores.csv")
    targets = pd.concat(all_targets, ignore_index=True)
    write_csv(targets, output / "validation-targets.csv")
    lines = [
        "# Frozen SF / Boston comparison",
        "",
        "Both saved prediction sets rescored against the joint run's frozen validation labels, "
        "on shared chips only. No GPU, retraining, or test evaluation.",
        "",
        "## Results at 0.35 confidence",
        "",
        "| City | Run | Radius | Recall | Precision* | F1* | F2* |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in metrics:
        if row["subset"] == "all":
            lines.append(
                f"| {row['city']} | {row['run']} | {row['radius_m']} m | "
                + " | ".join(f"{100 * row[k]:.1f}%" for k in ["recall", "precision", "f1", "f2"])
                + " |"
            )
    lines += [
        "",
        "## Confidence calibration (exploratory)",
        "",
        "Best F2 among tested thresholds, on these validation labels only:",
        "",
        "| City | Run | Radius | Threshold | Recall | Precision* | F2* |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for city in ["ussfo", "usbos"]:
        for run in ["old", "new"]:
            for radius in [2.0, 4.0]:
                best = max(
                    (
                        r
                        for r in curves
                        if r["city"] == city and r["run"] == run and r["radius_m"] == radius
                    ),
                    key=lambda r: r["f2"],
                )
                lines.append(
                    f"| {city} | {run} | {radius} m | {best['threshold']} | "
                    + " | ".join(f"{100 * best[k]:.1f}%" for k in ["recall", "precision", "f2"])
                    + " |"
                )
    lines += [
        "",
        "## Size breakdown (4 m, 0.35)",
        "",
        "| City | Run | DBH | Eligible trees | Recall |",
        "|---|---|---|---:|---:|",
    ]
    for row in size_rows:
        if row["radius_m"] == 4 and row["truth_count"]:
            lines.append(
                f"| {row['city']} | {row['run']} | {row['dbh_bin']} | "
                f"{row['truth_count']} | {100 * row['recall']:.1f}% |"
            )
    lines += ["", "## Coverage and vocabulary", ""]
    for city, details in provenance["cities"].items():
        lines.append(
            f"- {city}: {details['common_chips']} shared validation chips; "
            f"{details['reviewed_now_chips']} marked reviewed in the current UI. "
            f"Species labels supported for {details['species_eligible_trees']} / "
            f"{details['frozen_truth_trees']} retained truth trees."
        )
        for chip in details["old_only_chips"]:
            url = studio + "/model?" + urlencode({"run": OLD, "chip": chip, "unreviewed": "0"})
            lines.append(
                f"- Missing from new evaluation: [{city}/{chip}]({url}). "
                "Excluded from paired scores, NOT treated as zero predictions. "
                "Retain cleared/background-only chips in a future evaluation export."
            )
    lines += [
        "",
        "## Interpretation limits",
        "",
        "*Precision/F1/F2/AP are inventory-relative: unmatched predictions may be real, "
        "unlabeled trees. They are not proof of false detections or training penalties. "
        "Historical loss-mask values are intentionally not reused as a common scoring mask.",
        "",
        "Reviewed-now subsets use the saved UI status, not proof of exhaustive visible-tree "
        "annotation. Their labels remain the joint-run export; subsequent edits are NOT applied.",
        "",
        "AP/threshold sweeps use saved NMS candidates (at most 512 per chip), not raw heatmaps. "
        "Threshold selection is exploratory validation tuning, not held-out test performance.",
        "",
        "Models differ in training data and curation: this isolates evaluation labels, "
        "not the causal effect of curation. UI links show live labels/original run scores, "
        "not this immutable rescore. Review changes require a NEW benchmark version.",
        "",
        "## Validation targets",
        "",
        "25 density-stratified random audit chips + 25 diagnostic chips per city. "
        "The random audit is density-balanced, not a city-prevalence-weighted estimate. "
        "Diagnostics favor unfinished high-error chips; "
        "do not use that queue to estimate accuracy.",
        "",
        "For the representative audit, inspect imagery without predictions first. "
        "Check every inventory point, distinguish invisible/uncertain from invalid, and note "
        "unlabeled visible trees or unknown areas. Done alone does not certify complete labels. "
        "Then inspect predictions. Keep validation labels out of training.",
        "",
    ]
    for city in ["ussfo", "usbos"]:
        lines += [
            f"### {city}",
            "",
            "| Queue | Chip | Reason | Reviewed now |",
            "|---|---|---|---|",
        ]
        for row in targets[targets.city == city].itertuples():
            lines.append(
                f"| {row.queue} | [{row.chip_id}]({row.review_url}) | "
                f"{row.reason} | {row.reviewed_now} |"
            )
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    provenance["implementation_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    provenance["output_sha256"] = {
        str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.rglob("*"))
        if path.is_file()
    }
    (output / "manifest.json").write_text(json.dumps(provenance, indent=2))
    (output / "COMPLETE").write_text("success\n")
    print(f"Results and 100 review targets: {output / 'REPORT.md'}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--studio", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    build(args.root, args.output, args.studio)
