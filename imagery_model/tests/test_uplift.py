import json

import pandas as pd
import pytest

from urban_tree_ml.uplift import build, cohort_ids, score


def test_label_model_decomposition():
    old_truth = pd.DataFrame({"chip_id": ["a", "a"], "output_x": [0, 9], "output_y": [0, 0]})
    new_truth = old_truth.iloc[:1].copy()
    old_pred = pd.DataFrame({"chip_id": ["a"], "output_x": [5], "output_y": [0], "score": [0.9]})
    new_pred = old_pred.assign(output_x=0)
    oo, _ = score(old_pred, old_truth, 1, 0.35)
    on, _ = score(old_pred, new_truth, 1, 0.35)
    nn, _ = score(new_pred, new_truth, 1, 0.35)
    assert on["f2"] == oo["f2"] == 0
    assert nn["f2"] == 1
    unchanged, _ = score(new_pred, old_truth, 1, 0.35)
    assert unchanged["f2"] == pytest.approx(5 / 9)
    assert nn["f2"] - unchanged["f2"] > 0  # answer-key change alone improves score


def test_empty_chip_is_not_silently_lost():
    pred = pd.DataFrame({"chip_id": ["empty"], "output_x": [0], "output_y": [0], "score": [0.9]})
    truth = pd.DataFrame(columns=["chip_id", "output_x", "output_y"])
    assert cohort_ids(pred, truth, {"chips": 1}) == {"empty"}
    values, _ = score(pred, truth, 1, 0.35)
    assert values["unmatched_predictions"] == 1
    assert values["recall"] is None
    with pytest.raises(ValueError, match="Incomplete chip"):
        cohort_ids(pred, truth, {"chips": 2})


def test_refuses_existing_snapshot_and_invalid_run(tmp_path):
    with pytest.raises(FileExistsError):
        build(tmp_path, "old", "new", tmp_path)
    with pytest.raises(ValueError):
        build(tmp_path, "../old", "new", tmp_path / "output")


def test_full_build_changed_taxonomy_empty_extra_chip(tmp_path):
    for city, cohort in [("ussfo", "validation"), ("usbos", "validation-usbos")]:
        for run in ["old", "new"]:
            folder = tmp_path / "runs" / run / "evaluation" / cohort
            folder.mkdir(parents=True)
            meta = {
                "split": "validation",
                "config": {
                    "imagery": {
                        "resolution_m": 0.6,
                        "chip_pixels": 256,
                        "bands": [1, 2, 3, 4],
                        "local_raster": "city.tif",
                    },
                    "targets": {"output_stride": 2},
                    "evaluation": {"nms_kernel": 3, "max_detections_per_chip": 512},
                },
            }
            (folder / "evaluation-metadata.json").write_text(json.dumps(meta))
            pred = pd.DataFrame(
                {
                    "chip_id": ["paired"],
                    "output_x": [0],
                    "output_y": [0],
                    "pixel_col": [0],
                    "pixel_row": [0],
                    "longitude": [1.0],
                    "latitude": [1.0],
                    "score": [0.9],
                }
            )
            if run == "new":
                pred = pd.concat([pred, pred.assign(chip_id="extra")], ignore_index=True)
            pred.to_parquet(folder / "predictions.parquet")
            pd.DataFrame(
                {
                    "chip_id": ["paired"],
                    "output_x": [0],
                    "output_y": [0],
                    "split": ["validation"],
                    "dbh_log1p": [2.0],
                }
            ).to_parquet(folder / "ground-truth.parquet")
            (folder / "metrics.json").write_text(
                json.dumps({"split": "validation", "city": city, "chips": len(pred)})
            )
            (folder / "taxonomy.json").write_text(json.dumps({"different": run}))
    output = tmp_path / "report"
    rows = build(tmp_path, "old", "new", output)
    assert len(rows) == 16
    assert all(row["f2"] == 1 for row in rows)
    assert all(
        row["unmatched_predictions"] == 1
        for row in json.loads((output / "unpaired-chips.json").read_text())
    )
    assert (output / "COMPLETE").exists()
