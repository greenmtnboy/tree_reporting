from types import SimpleNamespace

import pytest

from urban_tree_ml.review_models import default_review_model
from urban_tree_ml.all_review import combined_summary


def test_dated_only_city_uses_its_own_default_acquisition(tmp_path):
    records=[]
    for city,acquisition,created in [('usnyc',None,'2026-10-01'),('uswas','uswas-naip-2025','2026-11-18'),('uswas','uswas-naip-2023','2026-11-19')]:
        evaluation=tmp_path/city/'evaluation'/(acquisition or 'original')
        evaluation.mkdir(parents=True)
        (evaluation.parent.parent/'COMPLETE').touch()
        records.append(dict(run_id=city+'::'+str(acquisition),training_run_id='v22',city=city,
            created_at=created,evaluation_dir=evaluation,metrics={'split':'validation'},
            bundle_config=SimpleNamespace(imagery=SimpleNamespace(acquisition_id=acquisition))))
    dc=default_review_model(records,'uswas',preferred_acquisition='uswas-naip-2025')
    assert dc['run_id']=='uswas::uswas-naip-2025'
    assert default_review_model(records,'usnyc')['city']=='usnyc'
    assert default_review_model(records,'uswas','uswas-naip-2023')['run_id']=='uswas::uswas-naip-2023'
    assert default_review_model(records,'uswas','all')['city']=='uswas'
    assert default_review_model(records,'uswas','usnyc-naip-2023') is None
    assert default_review_model(records,'unknown') is None


def test_summary_refuses_global_model_from_different_city():
    selected=SimpleNamespace(config=SimpleNamespace(inventory=SimpleNamespace(city='USNYC')))
    catalog=SimpleNamespace(bundle=lambda run:selected)
    with pytest.raises(ValueError,match='another city'):
        combined_summary(catalog,None,SimpleNamespace(city='uswas'),records=[])
