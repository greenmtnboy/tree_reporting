import copy
import json
import subprocess
from types import SimpleNamespace

import polars as pl
import pytest

from urban_tree_ml.border_inventory import neighboring_inventory, _cache
from urban_tree_ml.border_inventory_ui import BORDER_INVENTORY_JS
from urban_tree_ml.tree_curation import transformers


@pytest.fixture
def workspace(tmp_path):
    _cache.clear()
    crs = 'EPSG:32618'
    inverse = transformers(crs)[1]
    def sample(tid, x, y, scene='other'):
        lon, lat = inverse.transform(500000+x*.6, 4300000-y*.6)
        return dict(tree_id=tid, sample_id=tid, scene_id=scene, longitude=lon, latitude=lat,
                    species='Acer rubrum', dbh_in=12, split='train', target_x=x, target_y=y,
                    transform_a=.6, transform_b=0, transform_d=0, transform_e=-.6)
    owned = sample('own', 40, 40, 'current')
    neighbor = sample('neighbor', -5, 80)
    moved = sample('moved', 400, 100)
    raw = sample('unprepared', 259, 120)
    later = sample('future', 265, 200)
    source = tmp_path/'inventory/test/inventory.parquet'
    source.parent.mkdir(parents=True)
    pl.DataFrame([{**r, 'plant_date':'2026-01-01' if r is later else None} for r in [owned, neighbor, moved, raw, later]]).write_parquet(source)
    manifest = dict(metadata=dict(curation_schema_version=2, curation_crs=crs),
                    samples=[owned, neighbor, moved], scenes=[
                        dict(scene_id='current', sample_ids=['own'], splits=['train'], image_width=256, image_height=256),
                        dict(scene_id='other', sample_ids=['neighbor','moved'], splits=['train'], image_width=256, image_height=256)])
    state = dict(state_revision='first', reviews={'moved':dict(status='offset',east_m=-84,north_m=0,crown_radius_m=2)})
    split = SimpleNamespace(projected_crs=crs, block_size_m=768, guard_m=64, train_fraction=1, validation_fraction=0)
    context = SimpleNamespace(city='test', directory=tmp_path/'review', config=SimpleNamespace(
        paths=SimpleNamespace(root=tmp_path), seed=1, split=split,
        imagery=SimpleNamespace(planting_date_cutoff='2025-12-31')))
    return context, manifest, state


def test_neighbor_identity_projection_and_unprepared_inventory(workspace):
    context, manifest, state = workspace
    before = copy.deepcopy((manifest,state))
    result = neighboring_inventory(context, manifest, state, 'current')
    rows = {r['tree_id']:r for r in result['trees']}
    assert result['available']
    assert set(rows) == {'neighbor','moved','unprepared'}
    assert rows['neighbor']['x'] == pytest.approx(-5)
    assert rows['neighbor']['owner_scene_id'] == 'other'
    assert rows['moved']['x'] == pytest.approx(260)
    assert rows['moved']['radius_m'] == 2
    assert rows['unprepared']['owner_scene_id'] is None
    assert before == (manifest,state)  # No completion, annotation or sample mutations.
    assert neighboring_inventory(context,manifest,state,'current') == result


def test_revision_reprojects_changed_neighbor_and_crown_intersection(workspace):
    context, manifest, state = workspace
    neighboring_inventory(context,manifest,state,'current')
    state = {**state,'state_revision':'second','reviews':{'moved':dict(status='offset',east_m=-72,north_m=0,crown_radius_m=12)}}
    rows = {r['tree_id']:r for r in neighboring_inventory(context,manifest,state,'current')['trees']}
    assert rows['moved']['x'] == pytest.approx(280)  # Center outside padding; crown crosses it.
    state = {**state,'state_revision':'third','reviews':{}}
    assert 'moved' not in {r['tree_id'] for r in neighboring_inventory(context,manifest,state,'current')['trees']}


def test_sealed_and_guarded_inventory_not_exposed(workspace, monkeypatch):
    context, manifest, state = workspace
    context.config.split.train_fraction=0
    assert neighboring_inventory(context,manifest,state,'current')['trees'] == []
    context.config.split.train_fraction=1
    monkeypatch.setattr('urban_tree_ml.border_inventory._is_guarded',lambda *args:True)
    assert neighboring_inventory(context,manifest,state,'current')['trees'] == []


def test_neighbor_display_is_not_added_to_patch_scope(workspace, monkeypatch):
    from urban_tree_ml import review_gallery
    from urban_tree_ml.review_gallery import scoped_manifest, apply_scene_patch
    context, manifest, state = workspace
    neighboring_inventory(context,manifest,state,'current')
    scoped=scoped_manifest(manifest,'current')
    assert [s['tree_id'] for s in scoped['samples']] == ['own']
    assert scoped['scenes'][0]['sample_ids'] == ['own']
    monkeypatch.setattr(review_gallery,'read_collection',lambda directory:(manifest,state))
    monkeypatch.setitem(review_gallery._primed,str(context.directory),(None,manifest,state,None))
    with pytest.raises(ValueError,match='outside the loaded chip'):
        apply_scene_patch(context.directory,'current',dict(base_revision=state['state_revision'],reviews={'neighbor':{'status':'not-tree'}}))


def test_duplicate_guard_covers_inventory_added_and_neighbor_trees():
    script = r'''
const assert=require('node:assert/strict');
const scene={scene_id:'tile',sample_ids:['own'],image_width:256,image_height:256};
const anchor={tree_id:'own',transform_a:.6,transform_b:0,transform_d:0,transform_e:-.6};
const samplesById={own:anchor},metadata={editor_scene_id:'tile'};
let maskRegions=[],answer=false,prompts=[],selected=[],focused=false,status='aligned';
function statusOf(){return status;}
function displayedPosition(){return {x:20,y:20};}
function regionForScene(r){return {image_x:r.x,image_y:r.y};}
function confirm(s){prompts.push(s);return answer;}
function alert(s){prompts.push(s);}
function selectSample(...args){selected=args;}
function selectEditableTree(...args){selected=args;}
function addedTreeKey(id){return 'added:'+id;}
const document={querySelector:()=>({focus(){focused=true;}})},CSS={escape:x=>x};
''' + BORDER_INVENTORY_JS + r'''
borderInventory={available:true,trees:[{tree_id:'neighbor',x:260,y:100}]};
assert.equal(checkNewTree(scene,20,21),false);assert.equal(selected[1],'own');
assert.equal(checkNewTree(scene,260,101),false);assert.ok(focused);
assert.ok(prompts.at(-1).includes('neighbor inventory tree neighbor'));
answer=true;assert.equal(checkNewTree(scene,260,101),true);
answer=false;maskRegions=[{region_id:'existing',mode:'confirmed-tree',x:100,y:100}];
assert.equal(checkNewTree(scene,101,100),false);assert.equal(selected[1],'added:existing');
assert.equal(checkNewTree(scene,180,180),true);
status='not-tree';assert.equal(checkNewTree(scene,20,20),true);
borderInventory.available=false;assert.equal(checkNewTree(scene,260,180),false);
assert.equal(checkNewTree(scene,180,180),true);
assert.equal(maskRegions.length,1);
'''
    subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)
