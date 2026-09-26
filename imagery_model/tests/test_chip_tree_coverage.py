import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from urban_tree_ml.chip_catalog import training_chip_catalog


def test_tree_coverage_weights_density_deduplicates_and_requires_done(tmp_path):
    dataset=tmp_path/'chips'/'fixture';dataset.mkdir(parents=True)
    (dataset/'summary.json').write_text(json.dumps({'source_raster':'fixture.vrt'}))
    pd.DataFrame({'chip_id':['dense','sparse','heldout'],'split':['train','train','validation']}).to_parquet(dataset/'chips.parquet')
    pd.DataFrame([{'chip_id':'dense','tree_id':str(i),'split':'train'} for i in range(9)] +
                 [{'chip_id':'sparse','tree_id':'9','split':'train'},
                  {'chip_id':'heldout','tree_id':'v','split':'validation'}]).to_parquet(dataset/'labels.parquet')
    context=SimpleNamespace(config=SimpleNamespace(paths=SimpleNamespace(root=tmp_path)),raster=Path('fixture.vrt'))
    manifest={'scenes':[{'scene_id':'done','sample_ids':['a','b'],'splits':['train']},
                        {'scene_id':'overlap','sample_ids':['a2'],'splits':['train']},
                        {'scene_id':'pending','sample_ids':['c'],'splits':['train']},
                        {'scene_id':'val','sample_ids':['v'],'splits':['validation']}],
              'samples':[{'sample_id':sid,'tree_id':tid} for sid,tid in [('a','0'),('b','1'),('a2','0'),('c','2'),('v','v')]]}
    state={'reviews':{s['sample_id']:{'status':'aligned'} for s in manifest['samples']},
           'scene_reviews':{'done':{'done':True},'overlap':{'done':True,'more_done':True},'val':{'done':True}}}
    result=training_chip_catalog(context,manifest,state)
    assert result['trees_total']==10
    assert result['trees_in_done_scenes']==2
    assert result['tree_fraction']==pytest.approx(.2)
    assert result['curated']==0  # Neither full training chip is entirely covered.


def test_coverage_ui_identifies_tree_denominator():
    from urban_tree_ml.curation_report import CURATION_REPORT_HTML
    assert 'of training trees in done tiles' in CURATION_REPORT_HTML
    assert 'Validation/test trees are excluded' in CURATION_REPORT_HTML
