import json
from types import SimpleNamespace

import pandas as pd
import polars as pl
import pytest

from urban_tree_ml.dynamic_ranking import rank_evaluation
from urban_tree_ml.review_ranking import calculate


@pytest.mark.parametrize('legacy_mask,prediction_only', [(False, False), (True, False), (False, True)])
def test_dynamic_ranking_matches_reference(tmp_path, legacy_mask, prediction_only):
    config = SimpleNamespace(imagery=SimpleNamespace(resolution_m=0.25),
                             targets=SimpleNamespace(output_stride=2))
    record = dict(bundle_config=config, evaluation_dir=tmp_path, metrics={'prediction_only': prediction_only})
    # Interleaved chips, equal scores and equal distances make file-order ties observable.
    predictions = pd.DataFrame([
        ('tie', 0, 0, 4, 0.5, 0), ('below', 0, 0, 1, 0.01, 1),
        ('tie', -1, 0, 2, 0.5, 1), ('pred', 0, 0, 1, 0.9, -1),
        ('nan', 0, 0, 1, float('nan'), 1), ('pred', 0, 0, 2, 0.7, None),
        ('unknown', 0, 0, None, 0.5, 1), ('mask', 3, 4, 3, 0.5, 0),
    ], columns=['chip_id', 'output_x', 'output_y', 'species_id', 'score', 'detection_mask_value'])
    if legacy_mask:
        predictions = predictions.drop(columns='detection_mask_value')
    truth = pd.DataFrame([
        ('tie', -1, 0, 4), ('truth-only', 1, 1, -1), ('tie', 1, 0, 2),
        ('unknown', 0, 0, 1), ('mask', 0, 0, 3),
    ], columns=['chip_id', 'output_x', 'output_y', 'species_id'])
    predictions.to_parquet(tmp_path / 'predictions.parquet', row_group_size=2)
    truth.to_parquet(tmp_path / 'ground-truth.parquet', row_group_size=2)
    (tmp_path / 'inference-coverage.json').write_text(json.dumps({
        'complete': True, 'chips': [{'chip_id': 'empty'}]
    }))
    bundle = SimpleNamespace(config=config, predictions=predictions, ground_truth=truth, metrics=record['metrics'],
                             chip_ids=sorted(set(predictions.chip_id) | set(truth.chip_id) | {'empty'}))
    for threshold, radius in [(0.5, 0.5), (0.5, 2.5), (1.0, 1.0), (0.0, 0.25)]:
        assert rank_evaluation(record, threshold, radius) == calculate(bundle, threshold, radius)


def test_empty_evaluation_and_changed_inference(tmp_path):
    schema = dict(chip_id=pl.String, output_x=pl.Float64, output_y=pl.Float64, species_id=pl.Int64)
    pl.DataFrame(schema=schema).write_parquet(tmp_path / 'ground-truth.parquet')
    path = tmp_path / 'predictions.parquet'
    schema['score'] = pl.Float64
    pl.DataFrame(schema=schema).write_parquet(path)
    record = dict(metrics={}, evaluation_dir=tmp_path, bundle_config=SimpleNamespace(
        imagery=SimpleNamespace(resolution_m=1), targets=SimpleNamespace(output_stride=1)))
    assert rank_evaluation(record, 0.5, 1) == []
    pl.DataFrame([('updated', 0, 0, 1, 0.5)], schema=schema, orient='row').write_parquet(path)
    [row] = rank_evaluation(record, 0.5, 1)
    assert (row['chip_id'], row['false_positive']) == ('updated', 1)


@pytest.mark.parametrize('threshold,radius', [('nan', 1), (2, 1), (0, 0), (1, 101)])
def test_invalid_settings_rejected_before_reading(threshold, radius):
    with pytest.raises(ValueError):
        rank_evaluation({}, threshold, radius)
