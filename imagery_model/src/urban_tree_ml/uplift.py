"""Immutable CPU-only detection comparison: two models crossed with two label versions."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from urban_tree_ml.frozen_comparison import (
    assert_compatible,
    average_precision,
    counts,
    load_metadata,
    match_frame,
    write_csv,
)


def cohort_ids(predictions, truth, metrics):
    """Fail closed if exports cannot identify every evaluated chip, including empty ones."""
    ids = set(predictions.chip_id) | set(truth.chip_id)
    if len(ids) != metrics["chips"]:
        raise ValueError("Incomplete chip identities: explicit evaluated-chip manifest required")
    return ids


def score(predictions, truth, radius_px, threshold):
    matched = match_frame(
        predictions.reset_index(drop=True), truth.reset_index(drop=True), radius_px
    )
    selected = matched[matched.score >= threshold]
    return {
        **counts(selected, len(truth)),
        "ap_saved_candidates": average_precision(matched, len(truth)),
    }, matched


def build(root: Path, baseline: str, candidate: str, output: Path, threshold=0.35):
    for name in (baseline, candidate):
        if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in name):
            raise ValueError("Run IDs must be simple directory names")
    if baseline == candidate:
        raise ValueError("Choose different runs")
    output.mkdir(parents=True, exist_ok=False)
    provenance = {
        "created_at": datetime.now(UTC).isoformat(),
        "baseline": baseline,
        "candidate": candidate,
        "threshold": threshold,
        "cities": {},
        "inputs": {},
    }
    rows, curves, sizes, chips, extra_rows = [], [], [], [], []
    for city, cohort in [("ussfo", "validation"), ("usbos", "validation-usbos")]:
        print(f"Scoring {city}", flush=True)
        folder = output / city
        folder.mkdir()
        data = {}
        for label, run in [("old", baseline), ("new", candidate)]:
            source = root / "runs" / run / "evaluation" / cohort
            destination = folder / label
            destination.mkdir()
            for filename in [
                "evaluation-metadata.json",
                "metrics.json",
                "predictions.parquet",
                "ground-truth.parquet",
                "taxonomy.json",
            ]:
                path = source / filename
                shutil.copy2(path, destination / filename)
                provenance["inputs"][str(path)] = hashlib.sha256(
                    (destination / filename).read_bytes()
                ).hexdigest()
            meta, _ = load_metadata(destination)
            metrics = json.loads((destination / "metrics.json").read_text())
            truth = pd.read_parquet(destination / "ground-truth.parquet")
            pred = pd.read_parquet(destination / "predictions.parquet")
            if (
                meta.get("split") != "validation"
                or metrics.get("split") != "validation"
                or not set(truth.split).issubset({"validation"})
                or metrics["city"].lower() != city
            ):
                raise ValueError("Only the expected city validation split is permitted")
            data[label] = (meta, truth, pred, cohort_ids(pred, truth, metrics))
        old_meta, old_truth, old_pred, old_ids = data["old"]
        new_meta, new_truth, new_pred, new_ids = data["new"]
        assert_compatible(old_meta, new_meta, old_pred, new_pred)
        common = old_ids & new_ids
        if not common:
            raise ValueError("No paired chips")
        # Verify alignment on every common chip, not merely somewhere in the city.
        shared = old_pred.merge(new_pred, on=["chip_id", "output_x", "output_y"])
        if not common.issubset(set(shared.chip_id)):
            raise ValueError("Cannot verify geometry for every paired chip")
        provenance["cities"][city] = {
            "common_chips": sorted(common),
            "old_only_chips": sorted(old_ids - common),
            "new_only_chips": sorted(new_ids - common),
            "old_truth_count": int(old_truth.chip_id.isin(common).sum()),
            "new_truth_count": int(new_truth.chip_id.isin(common).sum()),
        }
        metres = (
            new_meta["config"]["imagery"]["resolution_m"]
            * new_meta["config"]["targets"]["output_stride"]
        )
        for radius in [2.0, 4.0]:
            for model, (_, _, pred, _) in data.items():
                p = pred[pred.chip_id.isin(common)].reset_index(drop=True)
                for labels, (_, truth, _, _) in data.items():
                    t = truth[truth.chip_id.isin(common)].reset_index(drop=True)
                    values, matched = score(p, t, radius / metres, threshold)
                    rows.append(
                        {
                            "city": city,
                            "model": model,
                            "labels": labels,
                            "radius_m": radius,
                            "chips": len(common),
                            "truth_count": len(t),
                            **values,
                        }
                    )
                    if labels != "new":
                        continue
                    for cutoff in [
                        0,
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
                                "model": model,
                                "radius_m": radius,
                                "threshold": cutoff,
                                **counts(matched[matched.score >= cutoff], len(t)),
                            }
                        )
                    visible = matched[matched.score >= threshold]
                    detected = set(visible.loc[visible.truth_index >= 0, "truth_index"])
                    dbh = np.expm1(t.dbh_log1p)
                    for name, mask in [
                        ("unknown", dbh.isna()),
                        ("under_4in", dbh < 4),
                        ("4_to_8in", (dbh >= 4) & (dbh < 8)),
                        ("8_to_16in", (dbh >= 8) & (dbh < 16)),
                        ("16in_plus", dbh >= 16),
                    ]:
                        ids = set(t.index[mask])
                        sizes.append(
                            {
                                "city": city,
                                "model": model,
                                "radius_m": radius,
                                "dbh_bin": name,
                                "truth_count": len(ids),
                                "detected": len(ids & detected),
                                "recall": len(ids & detected) / len(ids) if ids else None,
                            }
                        )
                    groups = dict(tuple(visible.groupby("chip_id")))
                    totals = t.groupby("chip_id").size()
                    for chip in sorted(common):
                        chips.append(
                            {
                                "city": city,
                                "chip_id": chip,
                                "model": model,
                                "radius_m": radius,
                                "truth_count": int(totals.get(chip, 0)),
                                **counts(
                                    groups.get(chip, visible.iloc[:0]), int(totals.get(chip, 0))
                                ),
                            }
                        )
            for model, (_, truth, pred, ids) in data.items():
                for chip in sorted(ids - common):
                    t = truth[truth.chip_id == chip]
                    values, _ = score(pred[pred.chip_id == chip], t, radius / metres, threshold)
                    extra_rows.append(
                        {
                            "city": city,
                            "model": model,
                            "chip_id": chip,
                            "radius_m": radius,
                            "truth_count": len(t),
                            **values,
                        }
                    )
    (output / "metrics.json").write_text(json.dumps(rows, indent=2))
    (output / "unpaired-chips.json").write_text(json.dumps(extra_rows, indent=2))
    write_csv(pd.DataFrame(curves), output / "threshold-curves.csv")
    write_csv(pd.DataFrame(sizes), output / "size-recall.csv")
    write_csv(pd.DataFrame(chips), output / "chip-scores.csv")
    lines = [
        "# Paired detection uplift",
        "",
        f"Baseline: `{baseline}`; candidate: `{candidate}`.",
        "",
        f"Confidence {threshold}; shared validation chips only. "
        "Labels are frozen evaluation exports, not live edits.",
        "",
        "| City | Radius | Model | Labels | Recall | Precision | F1 | F2 | AP* |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|",
    ]

    def percent(value):
        return "N/A" if value is None else f"{100 * value:.1f}%"

    for r in rows:
        lines.append(
            f"| {r['city']} | {r['radius_m']} m | {r['model']} | {r['labels']} | "
            + " | ".join(
                percent(r[k]) for k in ["recall", "precision", "f1", "f2", "ap_saved_candidates"]
            )
            + " |"
        )
    lines += [
        "",
        "## Separating label changes from model changes",
        "",
        "These are percentage-point F2 changes on identical paired chips. "
        "The label effect holds the old model fixed; the model effect holds the new labels fixed. "
        "Their sum equals the diagonal old/old → new/new change. "
        "This decomposition is path-dependent and does not separate training curation "
        "from vocabulary effects.",
        "",
        "| City | Radius | Label effect (old model) | Model effect (new labels) | Total |",
        "|---|---:|---:|---:|---:|",
    ]
    for city in provenance["cities"]:
        for radius in [2.0, 4.0]:
            selected = {
                (r["model"], r["labels"]): r["f2"]
                for r in rows
                if r["city"] == city and r["radius_m"] == radius
            }
            a, b, c = selected["old", "old"], selected["old", "new"], selected["new", "new"]
            lines.append(
                f"| {city} | {radius} m | {100 * (b - a):+.2f} pp | "
                f"{100 * (c - b):+.2f} pp | {100 * (c - a):+.2f} pp |"
            )
    lines += ["", "## Coverage", ""]
    for city, c in provenance["cities"].items():
        lines.append(
            f"- {city}: {len(c['common_chips'])} paired chips, "
            f"old/new labels {c['old_truth_count']}/{c['new_truth_count']}; "
            f"old-only {c['old_only_chips']}; new-only {c['new_only_chips']}."
        )
    lines += [
        "",
        "Unpaired chips are scored separately in `unpaired-chips.json`; "
        "absent inference is never treated as zero detections.",
        "",
        "## Limits and artifacts",
        "",
        "- Detection-only comparison deliberately ignores taxonomy IDs: expanded vocabularies "
        "cannot be compared by raw numeric class ID. No species uplift claim is made.",
        "- Precision/F1/F2/AP are inventory-relative, not exhaustive visible-tree accuracy. "
        "Loss-free training masks are not reused as evaluation exclusions.",
        "- AP* and threshold sweeps use saved NMS candidates. "
        "Threshold tuning is exploratory validation analysis, not test performance.",
        "- `size-recall.csv` uses new-label DBH and fixed matching; `chip-scores.csv` supports "
        "paired chip diagnostics. No new audit assignments or live review edits are created.",
        "- Input copies and SHA-256 hashes freeze the answer keys and predictions. "
        "The manifest lists exact paired chip IDs. "
        "Choose a new output directory for every comparison.",
        "- No GPU inference, training, or test evaluation was performed. "
        "Model gains here are descriptive, "
        "not a causal ablation or statistical significance claim.",
        "",
    ]
    (output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    provenance["implementation_sha256"] = {
        name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ["uplift.py", "frozen_comparison.py"]
    }
    provenance["output_sha256"] = {
        str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(output.rglob("*"))
        if p.is_file()
    }
    (output / "manifest.json").write_text(json.dumps(provenance, indent=2))
    (output / "COMPLETE").write_text("success\n")
    print(output / "REPORT.md", flush=True)
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.root, args.baseline, args.candidate, args.output)
