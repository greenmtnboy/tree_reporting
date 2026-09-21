import json
from types import SimpleNamespace
import pandas as pd
import pytest
from urban_tree_ml.review_models import model_choices, model_selector
from urban_tree_ml.training_queue import validate_training_image


def test_latest_completed_city_model_not_latest_cohort(tmp_path):
    records=[]
    for name, city, date, complete in [('old','ussfo','2026-01',True),('new','ussfo','2026-02',True),('pending','ussfo','2026-03',False),('bos','usbos','2026-04',True)]:
        directory=tmp_path/name/'evaluation'/'validation'
        directory.mkdir(parents=True)
        if complete:(directory.parent.parent/'COMPLETE').touch()
        records.append(dict(run_id=name,training_run_id=name,city=city,created_at=date,evaluation_dir=directory,metrics={'split':'validation'}))
    assert [r['run_id'] for r in model_choices(records,'ussfo')]==['new','old']
    assert model_choices(records,'all')[0]['run_id']=='bos'
    html=model_selector('<div class="controls"></div></body>',model_choices(records,'ussfo'),'new')
    assert 'value="new" selected' in html
    assert "new URL(location.href)" in html
    assert "searchParams.set('run',event.target.value)" in html


def test_image_allows_unassigned_train_but_not_test(tmp_path):
    folder=tmp_path/'chips'/'new';folder.mkdir(parents=True)
    (folder/'summary.json').write_text(json.dumps({'source_raster':'/remote/city.vrt'}))
    pd.DataFrame({'chip_id':['train1','test1','val1'],'split':['train','test','validation']}).to_parquet(folder/'chips.parquet')
    context=SimpleNamespace(config=SimpleNamespace(paths=SimpleNamespace(root=tmp_path)),raster=tmp_path/'city.vrt')
    validate_training_image(context,'train1')
    for chip in ['test1','val1','unknown','../bad']:
        with pytest.raises(ValueError):validate_training_image(context,chip)
