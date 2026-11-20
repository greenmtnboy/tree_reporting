"""Read-only unique-tree edit counts relative to an evaluation curation snapshot."""
import json
import argparse
from collections import Counter
from pathlib import Path
import pandas as pd
from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.splits import assign_spatial_splits

ROOT = Path('C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts')
BASE = ROOT/'benchmarks/v11-v12-current-validation-20261010'
RUN = 'sf-boston-nyc-naip-swin-30cm-v12-curated-finetune'
CITIES = {'ussfo':('ussfo-2022-mosaic','validation'),
          'usbos':('usbos-2023-external','validation-usbos'),
          'usnyc':('nj_m_4007309_sw_18_030_20230820_20231019','validation-usnyc')}

def clean(record):
    return {k:v for k,v in (record or {}).items() if k not in
            {'created_at','updated_at','saved_at','reviewed_at'}}

def count_delta(root=ROOT, base=BASE, current=None, run=RUN):
    """Count net semantic tree edits, using captured snapshots when supplied."""
    assert (base/'COMPLETE').exists(), 'Baseline must be a completed rescore'
    result = {'baseline':json.loads((base/'provenance.json').read_text())['created_at'],
              'baseline_path':str(base),'current_snapshot':str(current) if current else None,
              'count_policy':'unique net saved tree-record changes; timestamps excluded; regions separate',
              'cities':{}}
    for city,(name,cohort) in CITIES.items():
        live = current/'snapshots'/city if current else root/'qa/registration'/name
        raw = (live/'reviews.json').read_bytes()
        new = json.loads(raw)
        old = json.loads((base/'snapshots'/city/'reviews.json').read_text())
        manifest = json.loads((live/'manifest.json').read_text())
        old_manifest = json.loads((base/'snapshots'/city/'manifest.json').read_text())
        val = {str(s['tree_id']) for m in (manifest,old_manifest) for s in m['samples'] if s['split']=='validation'}
        evaluation = root/'runs'/run/'evaluation'/cohort
        cfg = ProjectConfig.model_validate(json.loads((evaluation/'evaluation-metadata.json').read_text())['config'])
        last_truth = pd.read_parquet(base/'runs'/run/'evaluation'/cohort/'ground-truth.parquet')
        cohort_trees = set(last_truth.tree_id)
        changes = {}
        fields = Counter()
        categories = Counter()
        for tree in sorted(set(old['tree_reviews']) | set(new['tree_reviews'])):
            a,b = old['tree_reviews'].get(tree),new['tree_reviews'].get(tree)
            if tree not in val or clean(a)==clean(b):
                continue
            kind = 'newly_reviewed_inventory' if a is None else 'review_removed' if b is None else 'revised_inventory'
            changes[tree] = kind
            categories[kind] += 1
            fields.update(k for k in set(clean(a))|set(clean(b)) if clean(a).get(k)!=clean(b).get(k))
        for tree in sorted(set(old.get('added_trees',{})) | set(new.get('added_trees',{}))):
            a,b = old.get('added_trees',{}).get(tree),new.get('added_trees',{}).get(tree)
            if clean(a)==clean(b):
                continue
            points = pd.DataFrame([r for r in (a,b) if r is not None])
            splits = assign_spatial_splits(points,cfg.split,cfg.seed)
            if not ((splits.split=='validation') & splits.split_eligible).any():
                continue
            kind = 'new_manual_tree' if a is None else 'removed_manual_tree' if b is None else 'revised_manual_tree'
            changes[tree] = kind
            categories[kind] += 1
            fields.update(k for k in set(clean(a))|set(clean(b)) if clean(a).get(k)!=clean(b).get(k))
        # Region and scene edits are reported separately, not inflated into per-tree actions.
        regions=[]
        ar={r['region_id']:r for r in old.get('mask_regions',[])}
        br={r['region_id']:r for r in new.get('mask_regions',[])}
        for rid in sorted(set(ar)|set(br)):
            a,b=ar.get(rid),br.get(rid)
            if clean(a)==clean(b): continue
            points=pd.DataFrame([r for r in (a,b) if r is not None])
            splits=assign_spatial_splits(points,cfg.split,cfg.seed)
            if ((splits.split=='validation') & splits.split_eligible).any():
                regions.append({'id':rid,'mode':(b or a)['mode']})
        scenes={s['scene_id'] for m in (manifest,old_manifest) for s in m['scenes'] if 'validation' in s['splits']}
        scene_changes=[s for s in scenes if old.get('scene_reviews',{}).get(s)!=new.get('scene_reviews',{}).get(s)]
        assert raw==(live/'reviews.json').read_bytes(), 'Saved edits changed during count; rerun'
        result['cities'][city]={'unique_tree_edits':len(changes),'categories':dict(categories),
            'field_changes_nonexclusive':dict(fields),'changed_trees_present_in_last_truth':len(set(changes)&cohort_trees),
            'changed_validation_regions':regions,'changed_validation_scenes':len(scene_changes),
            'saved_at':new.get('saved_at'),'tree_ids':changes}
    return result
if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--baseline',type=Path,default=BASE)
    parser.add_argument('--current',type=Path)
    parser.add_argument('--run',default=RUN)
    args=parser.parse_args()
    result=count_delta(args.root,args.baseline,args.current,args.run)
    print(json.dumps({**result,'cities':{city:{k:v for k,v in row.items() if k!='tree_ids'}
                                       for city,row in result['cities'].items()}},indent=2))
