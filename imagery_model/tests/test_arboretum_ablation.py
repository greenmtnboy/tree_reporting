import pandas as pd
from urban_tree_ml.arboretum_ablation import filter_training


def test_exclusions_are_boston_training_only():
    data = pd.DataFrame({'chip_id':['a','b','c','d'],
                         'split':['train','validation','test','train']})
    result = filter_training(data,'usbos',{'a','b','c'})
    assert result.chip_id.tolist()==['b','c','d']
    pd.testing.assert_frame_equal(filter_training(data,'ussfo',{'a','b','c'}),data)
    assert len(data)==4
