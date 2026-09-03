import pandas as pd
import pytest
import torch

from urban_tree_ml.evaluation import (
    _average_precision,
    _classification_metrics,
    _decode_batch,
    _greedy_matches,
)


def test_greedy_matching_is_one_to_one_and_confidence_ordered() -> None:
    predictions = pd.DataFrame(
        [
            {"chip_id": "a", "output_x": 1.0, "output_y": 1.0, "score": 0.9},
            {"chip_id": "a", "output_x": 1.2, "output_y": 1.0, "score": 0.8},
            {"chip_id": "b", "output_x": 4.0, "output_y": 4.0, "score": 0.7},
        ]
    )
    ground_truth = pd.DataFrame(
        [
            {"chip_id": "a", "output_x": 1.0, "output_y": 1.0},
            {"chip_id": "b", "output_x": 4.5, "output_y": 4.0},
        ]
    )

    matches = _greedy_matches(predictions, ground_truth, radius_output_px=0.6)

    assert [(prediction, truth) for prediction, truth, _ in matches] == [(0, 0), (2, 1)]
    average_precision = _average_precision(
        predictions, ground_truth, radius_output_px=0.6
    )
    assert average_precision == pytest.approx(5 / 6)


def test_classification_metrics_include_macro_and_top_k_accuracy() -> None:
    metrics = _classification_metrics(
        [0, 0, 1],
        [0, 1, 1],
        top_ids=[[0, 2], [0, 1], [1, 0]],
    )

    assert metrics is not None
    assert metrics["accuracy"] == pytest.approx(2 / 3)
    assert metrics["macro_f1"] == pytest.approx(2 / 3)
    assert metrics["top_k_accuracy"] == 1.0


def test_decode_batch_reads_attributes_at_center_peak() -> None:
    center = torch.full((1, 4, 4), -10.0)
    center[0, 2, 1] = 5.0
    dbh = torch.zeros((1, 4, 4))
    dbh[0, 2, 1] = torch.log1p(torch.tensor(10.0))
    genus = torch.zeros((1, 2, 4, 4))
    genus[0, 1, 2, 1] = 4.0
    species = torch.zeros((1, 3, 4, 4))
    species[0, 2, 2, 1] = 4.0

    decoded = _decode_batch(
        {
            "center_logits": center,
            "dbh_log1p": dbh,
            "genus_logits": genus,
            "species_logits": species,
        },
        ["chip"],
        max_detections_per_chip=1,
        nms_kernel=3,
    )

    assert len(decoded) == 1
    assert decoded[0]["output_x"] == 1
    assert decoded[0]["output_y"] == 2
    assert decoded[0]["dbh_in"] == pytest.approx(10.0)
    assert decoded[0]["genus_id"] == 1
    assert decoded[0]["species_id"] == 2
