import json
import subprocess

import pandas as pd
import polars as pl

from types import SimpleNamespace

from urban_tree_ml.model_debug import MODEL_DEBUG_HTML, ModelDebugBundle, _index_chips, _read_predictions


def test_compact_predictions_preserve_legacy_rows_and_nested_candidates(tmp_path):
    path = tmp_path / "predictions.parquet"
    frame = pd.DataFrame({
        "chip_id": ["west", "east", "north", "west"],
        "genus": ["Acer", None, "Quercus", "Acer"],
        "species_top_ids": [[5, 1], [], None, [3]],
        "score": [0.25, 0.375, 0.625, 0.875],
    }, index=[7, 4, 19, 23])
    frame.to_parquet(path, row_group_size=2)
    compact = _read_predictions(path)
    assert compact['prediction_id'].to_list() == list(frame.index)
    assert compact['chip_id'].dtype == pl.Categorical
    assert compact['species_top_ids'].dtype == pl.List(pl.Int64)
    assert compact.drop('prediction_id').to_dicts() == json.loads(frame.to_json(orient='records'))
    frame.index = pd.RangeIndex(11, 19, 2)
    frame[['score']].to_parquet(path)
    assert _read_predictions(path)['prediction_id'].to_list() == list(frame.index)
    frame.index = pd.Index([2, 17, 4, 8], name='export_row')
    frame[['score']].to_parquet(path)
    assert _read_predictions(path).columns == ['score', 'prediction_id']
    assert _read_predictions(path)['prediction_id'].to_list() == list(frame.index)


def test_interleaved_chip_slices_keep_prediction_identity_and_file_order():
    frame = pl.DataFrame({'chip_id': ['b', 'a', 'b'], 'prediction_id': [12, 7, 2]})
    grouped, slices = _index_chips(frame)
    assert grouped.slice(*slices['b'])['prediction_id'].to_list() == [12, 2]
    assert grouped.slice(*slices['a'])['prediction_id'].to_list() == [7]


def test_nullable_species_and_separate_inventory_name(tmp_path):
    bundle = object.__new__(ModelDebugBundle)
    bundle.artifact_root = tmp_path
    bundle.training_run_id = 'fixture'
    bundle.config = SimpleNamespace(inventory=SimpleNamespace(city='USNYC'))
    bundle.taxonomy = {'species': ['Acer rubrum'], 'genera': ['Acer']}
    path = tmp_path / 'run-inputs' / 'fixture' / 'usnyc'
    path.mkdir(parents=True)
    pl.DataFrame({'tree_id': ['t'], 'species': ['Acer rare']}).write_parquet(path / 'inventory.parquet')
    bundle.ground_truth = pl.DataFrame({'chip_id': ['c'], 'tree_id': ['t'], 'species_id': [None],
                                        'genus_id': [0], 'dbh_log1p': [None]})
    bundle._add_ground_truth_names()
    row = bundle.ground_truth.row(0, named=True)
    assert (row['species'], row['inventory_species'], row['genus']) == (None, 'Acer rare', 'Acer')
    bundle.chip_ids = ['c']
    bundle.metrics = {'confidence_threshold': 0.35}
    bundle.predictions = pl.DataFrame({'prediction_id': [9], 'chip_id': ['c'], 'species_id': [None],
                                       'score': [0.5]})
    bundle.matches = pl.DataFrame({'radius_m': [2], 'prediction_index': [9], 'tree_id': ['t']})
    [summary] = bundle._chip_summaries()
    assert summary['matched'] == 1 and summary['species_errors'] == 0


def test_filter_change_cancels_stale_work_and_shares_unchanged_ranking():
    logic = MODEL_DEBUG_HTML.split("const reviewRankRequests=", 1)[1].split("let galleryGeneration", 1)[0]
    js = "const reviewRankRequests=" + logic + """
    const assert = require('node:assert/strict');
    const threshold={value:'.13',checkValidity:()=>true}, radius={value:'4'};
    const $ = id=>({threshold,radius}[id] || {});
    const state={run_id:'model',chips:[{chip_id:'chip'}]}, calls=[];
    function fetch(url,{signal}){
      return new Promise((resolve,reject)=>{
        calls.push({signal,finish:()=>resolve({ok:true,json:async()=>({chips:[{chip_id:'chip'}]})})});
        signal.addEventListener('abort',()=>reject(signal.reason),{once:true});
      });
    }
    (async()=>{
      const first=startOverlayRender(), a=rankedReviewChips(false,first);
      const next=startOverlayRender(), b=rankedReviewChips(false,next);
      assert(first.aborted); assert(!calls[0].signal.aborted); assert.equal(calls.length,1);
      threshold.value='.2';
      const changed=startOverlayRender(), c=rankedReviewChips(false,changed);
      assert(next.aborted); assert(calls[0].signal.aborted); assert.equal(calls.length,2);
      calls[1].finish();
      assert.deepEqual((await Promise.allSettled([a,b,c])).map(x=>x.status),['rejected','rejected','fulfilled']);
      await rankedReviewChips(false,startOverlayRender());
      assert.equal(calls.length,2);
    })().catch(e=>{console.error(e);process.exitCode=1});
    """
    subprocess.run(["node"], input=js, encoding="utf-8", check=True)
