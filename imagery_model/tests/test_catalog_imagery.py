import json
import os
from types import SimpleNamespace

import polars as pl
import pytest

from urban_tree_ml.catalog_imagery import matches_catalog_imagery
from urban_tree_ml.chip_catalog import training_chip_catalog
from urban_tree_ml.training_queue import validate_training_image
from urban_tree_ml.all_review import combined_summary
from urban_tree_ml.queue_navigation import prepare_member, require_chip


def test_same_filename_cannot_import_another_city_catalog(tmp_path, monkeypatch):
    rasters = [tmp_path / city / 'imagery-30cm.vrt' for city in ('fairfax', 'sf')]
    for raster in rasters:
        raster.parent.mkdir()
        raster.write_text('not a VRT clone')
    for n, (name, raster) in enumerate(zip(('fairfax', 'sf'), rasters)):
        folder = tmp_path / 'chips' / name
        folder.mkdir(parents=True)
        summary = folder / 'summary.json'
        summary.write_text(json.dumps({'source_raster': str(raster)}))
        os.utime(summary, (100+n, 100+n))
        pl.DataFrame({'chip_id': [name], 'split': ['train']}).write_parquet(folder/'chips.parquet')
        pl.DataFrame(schema={'chip_id': pl.String, 'tree_id': pl.String, 'split': pl.String}).write_parquet(folder/'labels.parquet')
    partial = tmp_path/'chips/partial'
    partial.mkdir()
    (partial/'summary.json').write_text(json.dumps({'source_raster':str(rasters[0]),'catalog_role':'supplemental_inference'}))
    pl.DataFrame({'chip_id':['partial'], 'split':['train']}).write_parquet(partial/'chips.parquet')
    pl.DataFrame(schema={'chip_id':pl.String,'tree_id':pl.String,'split':pl.String}).write_parquet(partial/'labels.parquet')
    config = SimpleNamespace(paths=SimpleNamespace(root=tmp_path), inventory=SimpleNamespace(city='USFFX'))
    context = SimpleNamespace(config=config, raster=rasters[0], city='usffx')
    assert matches_catalog_imagery(tmp_path, rasters[0], rasters[0])
    assert not matches_catalog_imagery(tmp_path, rasters[1], rasters[0])
    manifest, state = {'scenes': [], 'samples': []}, {'scene_reviews': {}, 'reviews': {}}
    assert training_chip_catalog(context, manifest, state)['eligible'] == ['fairfax']
    assert training_chip_catalog(context, manifest, state, dataset='sf') is None
    validate_training_image(context, 'fairfax')
    with pytest.raises(ValueError):
        validate_training_image(context, 'sf')
    record = dict(run_id='v20::train-usffx', training_run_id='v20', city='USFFX', metrics={'split':'train'})
    bundle = SimpleNamespace(config=config, training_run_id='v20', chips=[], summary=lambda:dict(display={},city='USFFX'))
    monkeypatch.setattr('urban_tree_ml.all_review.geographic_assignments', lambda _: {})
    monkeypatch.setattr('urban_tree_ml.all_review.assignment_choices', lambda *args: [])
    catalog = SimpleNamespace(records=lambda:[record], bundle=lambda _:bundle)
    assert [r['chip_id'] for r in combined_summary(catalog,record['run_id'],context)['chips']] == ['fairfax']


def test_review_prediction_grid_and_navigation_are_independent_of_scored_subset(tmp_path, monkeypatch):
    run = tmp_path/'runs/v20'
    run.mkdir(parents=True)
    (run/'curated-validation.json').write_text(json.dumps({'cities':{'usffx':{'chip_ids':['scored'],'excluded_validation_chips':[]}}}))
    config = SimpleNamespace(paths=SimpleNamespace(root=tmp_path))
    context = SimpleNamespace(config=config, city='usffx')
    records=[]
    for name, chip, prediction_only in [('scored','scored',False),('review','new',True)]:
        dataset = tmp_path/'chips'/name
        dataset.mkdir(parents=True)
        pl.DataFrame({'chip_id':[chip,'sealed'], 'split':['validation','test']}).write_parquet(dataset/'chips.parquet')
        records.append(dict(run_id='v20::'+name,training_run_id='v20',city='USFFX',dataset=name,
                            metrics={'split':'validation','prediction_only':prediction_only},
                            bundle_config=config,raster_path=None,evaluation_dir=run/'evaluation'/name))
    monkeypatch.setattr('urban_tree_ml.qa_server._context_matches_bundle', lambda *args: True)
    result=prepare_member(context,records,{'scenes':[]},'new','validation','v20::scored','auto')
    assert result['run']=='v20::review'
    for record in records:
        with pytest.raises(ValueError):
            require_chip(context,record,'sealed','validation')
