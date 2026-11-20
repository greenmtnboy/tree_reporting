import json
from types import SimpleNamespace

import polars as pl
import pytest

from urban_tree_ml.all_review import combined_summary
from urban_tree_ml.dynamic_ranking import rank_evaluation
from urban_tree_ml.review_tables import review_backfill_directory
from urban_tree_ml.queue_navigation import require_chip


def test_validation_gallery_and_navigation_use_full_city_grid(tmp_path):
    directory = tmp_path / 'runs' / 'model'
    directory.mkdir(parents=True)
    receipt = {'cities': {'usbos': {'chip_ids': ['scored'], 'excluded_validation_chips': ['new']},
                          'ussfo': {'chip_ids': ['other'], 'excluded_validation_chips': ['sf-only']}}}
    (directory / 'curated-validation.json').write_text(json.dumps(receipt))
    dataset = tmp_path / 'chips' / 'boston'
    dataset.mkdir(parents=True)
    pl.DataFrame({'chip_id': ['scored', 'train', 'test'], 'split': ['validation', 'train', 'test']}).write_parquet(dataset/'chips.parquet')
    config = SimpleNamespace(inventory=SimpleNamespace(city='USBOS'), paths=SimpleNamespace(root=tmp_path))
    context = SimpleNamespace(config=config, raster=tmp_path/'bos.vrt')
    record = dict(run_id='model::validation-usbos', training_run_id='model', city='USBOS', dataset='boston',
                  metrics={'split':'validation'})
    bundle = SimpleNamespace(config=config, training_run_id='model', chips=[dict(chip_id='scored', missed=3)],
                             summary=lambda: dict(display={}, city='USBOS'))
    catalog = SimpleNamespace(records=lambda:[record], bundle=lambda _:bundle)
    chips = combined_summary(catalog, record['run_id'], context, validation_only=True)['chips']
    assert [(c['chip_id'], c['inference_run'], c['missed']) for c in chips] == [
        ('scored', record['run_id'], 3), ('new', None, None)]
    assert chips[1]['review_run'] == record['run_id']
    cohort = directory / 'evaluation' / 'validation-usbos'
    record['evaluation_dir'] = cohort
    extra = tmp_path / 'review-inference' / 'model' / 'evaluation' / cohort.name
    extra.mkdir(parents=True)
    assert review_backfill_directory(record) is None
    (extra/'review-provenance.json').write_text('{}')
    catalog.review_backfill = lambda _: SimpleNamespace(chips=[dict(chip_id='new', missed=4)])
    chips = combined_summary(catalog, record['run_id'], context, validation_only=True)['chips']
    assert [(c['chip_id'], c['inference_run'], c['missed']) for c in chips] == [
        ('scored', record['run_id'], 3), ('new', record['run_id'], 4)]
    assert chips[1]['review_only']
    assert bundle.chips == [dict(chip_id='scored', missed=3)]
    require_chip(context, record, 'new', 'validation')
    for chip in ['train', 'test', 'sf-only', 'absent']:
        with pytest.raises(ValueError):
            require_chip(context, record, chip, 'validation')
    (directory/'curated-validation.json').unlink()
    require_chip(context, record, 'scored', 'validation')
    with pytest.raises(ValueError):
        require_chip(context, record, 'new', 'validation')


def test_review_ranking_at_custom_settings_includes_published_backfill(tmp_path):
    record = dict(evaluation_dir=tmp_path/'runs'/'v19'/'evaluation'/'validation',
                  metrics={'split': 'validation'},
                  bundle_config=SimpleNamespace(imagery=SimpleNamespace(resolution_m=1),
                                                targets=SimpleNamespace(output_stride=1)))
    extra = tmp_path/'review-inference'/'v19'/'evaluation'/'validation'
    for directory, chip in [(record['evaluation_dir'], 'scored'), (extra, 'unreviewed')]:
        directory.mkdir(parents=True)
        truth = pl.DataFrame(dict(chip_id=[chip], output_x=[1], output_y=[0], species_id=[1]))
        truth.write_parquet(directory/'ground-truth.parquet')
        truth.with_columns(pl.lit(0).alias('output_x'), pl.lit(.25).alias('score')).write_parquet(
            directory/'predictions.parquet')
    assert len(rank_evaluation(record, .13, 2)) == 1
    (extra/'review-provenance.json').write_text('{}')
    for threshold, radius, missed in [(.13, 2, 0), (.35, 2, 1), (.13, .5, 1)]:
        rows = rank_evaluation(record, threshold, radius)
        assert {r['chip_id']: r['missed'] for r in rows} == {'scored': missed, 'unreviewed': missed}
    assert review_backfill_directory({**record, 'metrics': {'split': 'test'}}) is None
