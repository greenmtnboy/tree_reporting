import hashlib
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from urban_tree_ml.peer_references import directory, reference
from urban_tree_ml.dynamic_ranking import rank_evaluation
from urban_tree_ml.review_ranking import calculate


def test_peer_reference_ranking_and_drift(tmp_path):
    evaluation = tmp_path/'runs'/'model'/'evaluation'/'train-city-2025'
    evaluation.mkdir(parents=True)
    predictions = pd.DataFrame([
        ('chip', 0., 0., 0, .5, 0.), ('chip', 100., 100., 0, .5, 0.),
    ], columns=['chip_id', 'output_x', 'output_y', 'species_id', 'score', 'detection_mask_value'])
    truth = pd.DataFrame([('chip', 0., 0., 0), ('chip', 20., 20., 0)],
                         columns=['chip_id', 'output_x', 'output_y', 'species_id'])
    predictions.to_parquet(evaluation/'predictions.parquet')
    truth.iloc[:0].to_parquet(evaluation/'ground-truth.parquet')
    (evaluation/'taxonomy.json').write_text('{}')
    config = SimpleNamespace(imagery=SimpleNamespace(resolution_m=.3), targets=SimpleNamespace(output_stride=2))
    record = dict(metrics={'prediction_only': True}, evaluation_dir=evaluation, bundle_config=config)
    assert rank_evaluation(record, .13, 2)[0]['missed'] is None
    output = directory(evaluation)
    assert output == tmp_path/'city-review-references'/'model'/'train-city-2025'
    output.mkdir(parents=True)
    truth.to_parquet(output/'ground-truth.parquet')
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    receipt = dict(policy='city_inventory_reference_v1', training_eligible=False,
                   reference_sha256=sha(output/'ground-truth.parquet'),
                   predictions_sha256=sha(evaluation/'predictions.parquet'),
                   taxonomy_sha256=sha(evaluation/'taxonomy.json'))
    (output/'reference.json').write_text(json.dumps(receipt))
    assert reference(evaluation)[1] == receipt
    bundle = SimpleNamespace(config=config, predictions=predictions, ground_truth=truth,
                             metrics=record['metrics'], chip_ids=['chip'], provisional_reference=receipt)
    rows = rank_evaluation(record, .13, 2)
    assert rows == calculate(bundle, .13, 2)
    assert rows[0]['missed'] == 1 and rows[0]['matched'] == 1
    assert rows[0]['false_positive'] is None and rows[0]['detection_f1'] is None
    assert rows[0]['provisional_reference'] and rows[0]['prediction_only']
    assert pd.read_parquet(evaluation/'ground-truth.parquet').empty
    receipt['reference_trees'] = 0
    truth.iloc[:0].to_parquet(output/'ground-truth.parquet')
    receipt['reference_sha256'] = sha(output/'ground-truth.parquet')
    (output/'reference.json').write_text(json.dumps(receipt))
    assert reference(evaluation) is None
    (evaluation/'taxonomy.json').write_text('{"changed":true}')
    with pytest.raises(ValueError, match='provenance'):
        reference(evaluation)


def test_select_labels_requires_same_spatial_membership():
    from prepare_peer_references import select_labels
    metadata = dict(city='BOS', split='train', config=dict(
        imagery=dict(chip_pixels=512, resolution_m=.3), targets=dict(output_stride=2)))
    original = pd.DataFrame([('a','train',0,512),('b','validation',512,0)],
                            columns=['chip_id','split','row_offset','column_offset'])
    target = original.iloc[:1].copy()
    truth = pd.DataFrame({'chip_id':['a','b'], 'output_x':[1,2]})
    assert select_labels(metadata,metadata,original,target,truth).chip_id.tolist() == ['a']
    target.loc[0,'column_offset'] += 1
    with pytest.raises(ValueError, match='coordinates'):
        select_labels(metadata,metadata,original,target,truth)
    with pytest.raises(ValueError, match='city or split'):
        select_labels(metadata,{**metadata,'split':'validation'},original,target,truth)


def test_inventory_baseline_projects_raw_positions_and_excludes_holdout_and_new_plantings():
    from affine import Affine
    from prepare_peer_references import inventory_labels
    grid = SimpleNamespace(crs='EPSG:4326',transform=Affine.identity(),width=1024,height=512)
    inventory = pd.DataFrame(dict(tree_id=['keep','future','test'],longitude=[10.,20.,30.],latitude=[10.,10.,10.],
        split=['train','train','test'],split_eligible=[True]*3,plant_date=pd.to_datetime(['2020-01-01','2026-01-01','2020-01-01']),
        species=['Acer rubrum']*3,genus=['Acer']*3,species_eligible=[True]*3,genus_eligible=[True]*3,
        dbh_eligible=[True]*3,dbh_log1p=[1.]*3))
    target = pd.DataFrame([('r000000_c000000','train',0,0)],columns=['chip_id','split','row_offset','column_offset'])
    config = dict(imagery=dict(chip_pixels=512),targets=dict(output_stride=2))
    taxonomy = dict(species=['Acer rubrum'],genera=['Acer'])
    labels = inventory_labels(inventory,target,grid,config,taxonomy,'2025-08-02')
    assert labels.tree_id.tolist()==['keep']
    assert labels[['output_x','output_y']].values.tolist()==[[5,5]]
    assert labels.species_id.tolist()==[0]


@pytest.mark.parametrize('empty', [False, True])
def test_expanded_inventory_uses_full_source_and_keeps_empty_split_schema(tmp_path, monkeypatch, empty):
    from contextlib import nullcontext
    from datetime import date
    from affine import Affine
    from prepare_acquisition_review import prepare_review_inventory
    from urban_tree_ml.config import InventoryConfig
    import urban_tree_ml.splits as splits
    import rasterio

    source = tmp_path/'citywide.parquet'
    pd.DataFrame(dict(tree_id=['outside-old-area','future','other-window'],
        longitude=[10.,20.,600.],latitude=[10.,10.,10.],
        species=['Acer rubrum']*3, diameter_at_breast_height=[12.]*3,
        plant_date=['2020-01-01','2026-01-01','2020-01-01'])).to_parquet(source)
    taxonomy = tmp_path/'taxonomy.json'
    taxonomy.write_text(json.dumps(dict(species=['Acer rubrum'],genera=['Acer'],species_to_genus=[0])))
    config = SimpleNamespace(inventory=InventoryConfig(city='USNYC',parquet_url=str(source),
        min_dbh_in=4,max_dbh_in=60,excluded_species=['Unknown'],min_species_examples=2,max_species_classes=99),
        imagery=SimpleNamespace(local_raster=tmp_path/'grid.vrt',chip_pixels=512,planting_date_cutoff=date(2025,1,1)),
        split=None,seed=42,reference=SimpleNamespace(taxonomy_path=taxonomy),paths=SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(rasterio,'open',lambda _:nullcontext(SimpleNamespace(crs='EPSG:4326',transform=Affine.identity())))
    monkeypatch.setattr(splits,'assign_spatial_splits',lambda frame,*_:frame.assign(split=['train']*len(frame),split_eligible=True))
    chips = pd.DataFrame({'chip_id':['r000009_c000009' if empty else 'r000000_c000000']})
    path, inventory = prepare_review_inventory(config,chips,tmp_path)
    assert inventory.tree_id.tolist() == ([] if empty else ['outside-old-area'])
    assert pd.read_parquet(path).tree_id.tolist() == inventory.tree_id.tolist()
    assert str(inventory.split.dtype) == 'string'
    assert json.loads((tmp_path/'review-inventory-source.json').read_text())['source'] == str(source)
