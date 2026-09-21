import json
from types import SimpleNamespace

import pandas as pd

from urban_tree_ml.chip_catalog import training_chip_catalog


def test_training_progress_does_not_count_partial_or_validation_chips(tmp_path):
    directory = tmp_path / "chips" / "dataset"
    directory.mkdir(parents=True)
    (directory / "summary.json").write_text(json.dumps({"source_raster": "/data/sf.vrt"}))
    pd.DataFrame({"chip_id": ["one", "two", "three"],
                  "split": ["train", "train", "validation"]}).to_parquet(
                      directory / "chips.parquet")
    pd.DataFrame({"chip_id": ["one", "two", "two", "three"],
                  "split": ["train", "train", "train", "validation"],
                  "tree_id": ["a", "b", "c", "d"]}).to_parquet(directory / "labels.parquet")
    manifest = {"scenes": [{"scene_id": "s", "sample_ids": ["a", "b", "d"],
                            "validation_chip_id": "two", "splits": ["train"]}],
                "samples": [{"sample_id": key, "tree_id": key} for key in "abcd"]}
    state = {"scene_reviews": {"s": {"done": True}},
             "reviews": {key: {"status": "aligned"} for key in "abcd"}}
    context = SimpleNamespace(config=SimpleNamespace(paths=SimpleNamespace(root=tmp_path)),
                              raster=tmp_path / "sf.vrt")
    progress = training_chip_catalog(context, manifest, state)
    assert progress["total"] == 2
    assert progress["curated"] == 1
    assert progress["pending"] == ["two"]
    assert progress["eligible"] == ["one", "two"]  # includes done, excludes validation
    assert progress["fraction"] == 0.5


def test_explicit_review_keeps_previously_reviewed_targets(tmp_path):
    from urban_tree_ml.chip_catalog import next_training_truth
    directory = tmp_path/'chips'/'dataset'
    directory.mkdir(parents=True)
    pd.DataFrame({'chip_id':['one','one'], 'split':['train','train'],
                  'tree_id':['a','b']}).to_parquet(directory/'labels.parquet')
    inventory = tmp_path/'inventory'/'usbos'
    inventory.mkdir(parents=True)
    pd.DataFrame({'tree_id':['a','b'],'species':['Acer rubrum']*2,'genus':['Acer']*2,
                  'diameter_at_breast_height':[10,12]}).to_parquet(inventory/'inventory.parquet')
    context=SimpleNamespace(config=SimpleNamespace(paths=SimpleNamespace(root=tmp_path)),city='usbos')
    manifest={'samples':[{'tree_id':'a','sample_id':'a','scene_id':'done'}]}
    state={'scene_reviews':{'done':{'done':True}},'reviews':{'a':{'status':'aligned'}}}
    progress={'pending':['one'],'dataset':'dataset'}
    _,truth=next_training_truth(context,progress,manifest,state,include_reviewed=True)
    assert set(truth.tree_id)=={'a','b'}
    _,pending=next_training_truth(context,progress,manifest,state)
    assert set(pending.tree_id)=={'b'}
