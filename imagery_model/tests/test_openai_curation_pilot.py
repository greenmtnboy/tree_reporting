import pytest
from urban_tree_ml.openai_curation_pilot import validate


def test_clear_negative_requires_consistent_status_confidence_and_evidence():
    inputs={'width':256,'height':256,'points':[{'id':'p1'}]}
    row={'id':'p1','status':'no_visible_tree','x':None,'y':None,'confidence':.95,
         'clear_not_tree':True,'reason':'Continuous open pavement, no nearby crown'}
    assert validate({'trees':[row]},inputs)==[row]
    for changed in [{'confidence':.5},{'status':'uncertain'},{'clear_not_tree':'true'},{'reason':''}]:
        with pytest.raises(ValueError):validate({'trees':[{**row,**changed}]},inputs)
