"""Freeze current saved curation, rebuild validation supervision, rescore saved models.

Only benchmark outputs are written. No inference, training, live publishing, or test pixels.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from urban_tree_ml.chips import build_chips
from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.feedback import finalize_registration_feedback, load_persisted_reviews, _json_sha256
from urban_tree_ml.sweep_history import POLICIES, swept_attributes
from score_confidence_sweep import sweep

RUNS = ['sf-boston-nyc-naip-swin-30cm-v11-curated-finetune-retry1',
        'sf-boston-nyc-naip-swin-30cm-v12-curated-finetune']
CITIES = {'ussfo': ('ussfo-2022-mosaic', 'validation'),
          'usbos': ('usbos-2023-external', 'validation-usbos'),
          'usnyc': ('nj_m_4007309_sw_18_030_20230820_20231019', 'validation-usnyc')}


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def remap(path, root):
    text = str(path).replace('\\', '/')
    marker = '/urban-tree-ml/'
    return root/text.split(marker, 1)[1] if marker in text else Path(path)


def mask_values(predictions, chip_dir):
    chips = pd.read_parquet(chip_dir/'chips.parquet').set_index('chip_id')
    values = np.empty(len(predictions), dtype=np.float32)
    for chip, rows in predictions.groupby('chip_id', sort=False):
        with np.load(chip_dir/chips.loc[chip, 'path']) as arrays:
            mask = arrays['scoring_mask'].squeeze()
            values[rows.index] = mask[rows.output_y.to_numpy(dtype=int), rows.output_x.to_numpy(dtype=int)]
    return values


def build(root, output, resume=False, runs=None, count_baseline=None):
    runs = runs or RUNS
    if resume:
        assert not (output/'COMPLETE').exists(), 'Completed outputs are immutable'
        provenance = json.loads((output/'provenance.json').read_text())
        assert provenance['runs'] == runs
    else:
        output.mkdir(parents=True, exist_ok=False)
        provenance = None
    provenance = provenance or dict(created_at=datetime.now(timezone.utc).isoformat(), runs=runs,
                      source='current saved curation snapshot; not a live annotation write',
                      test_evaluated=False, files={}, cities={})
    # Capture every city before any lengthy builds, and verify reads were stable.
    for city, (name, _) in CITIES.items():
        if resume:
            continue  # Preserve the original captured curation, not later edits.
        review = root/'qa/registration'/name
        snapshot = {n:(review/n).read_bytes() for n in ('manifest.json','reviews.json','training-feedback.json')}
        assert all((review/n).read_bytes() == data for n,data in snapshot.items()), 'Curation changed during capture'
        folder = output/'snapshots'/city
        folder.mkdir(parents=True)
        for n,data in snapshot.items():
            (folder/n).write_bytes(data)
            provenance['files'][str(review/n)] = hashlib.sha256(data).hexdigest()
    # Count against precisely the same captured edits used for scoring, before
    # stale publication normalization can rewrite any isolated snapshot files.
    if count_baseline and not (output/'curation-counts.json').exists():
        from count_validation_curation_delta import count_delta
        counts = count_delta(root, count_baseline, output, runs[-1])
        (output/'curation-counts.json').write_text(json.dumps(counts, indent=2))
        print('Updated unique validation-tree edits:',
              {city: row['unique_tree_edits'] for city,row in counts['cities'].items()}, flush=True)
    (output/'provenance.json').write_text(json.dumps(provenance,indent=2))
    for city, (name, cohort) in CITIES.items():
        if city in provenance['cities']:
            continue
        print('Rebuilding validation supervision:', city, flush=True)
        source = root/'runs'/runs[-1]/'evaluation'/cohort
        meta = json.loads((source/'evaluation-metadata.json').read_text())
        cfg = ProjectConfig.model_validate(meta['config'])
        raster = remap(cfg.imagery.local_raster, root)
        original_raster = root/'imagery'/city/('2022' if city=='ussfo' else '2023')/(name+('.tif' if city=='usnyc' else '.vrt'))
        cfg.paths.root = output
        cfg.paths.annotations = output/'annotation-bundles'
        cfg.imagery.local_raster = raster
        cfg.imagery.boundary_shifts = False
        cfg.reference.taxonomy_path = root/'runs'/runs[-1]/'vegetation-taxonomy.json'
        inventory = root/'run-inputs'/runs[-1]/city/'inventory.parquet'
        provenance['files'][str(inventory)] = sha(inventory)
        coverage = json.loads((source/'inference-coverage.json').read_text())
        ids = {r['chip_id'] for r in coverage['chips']}
        assert coverage['complete'] and len(ids)==coverage['chip_count']
        frozen_feedback = root/'run-inputs'/runs[-1]/city/'training-feedback.json'
        old = json.loads(frozen_feedback.read_text())
        cfg.dataset = old['dataset']
        # NYC validation was pinned to the uncurated v8 cohort, not rebuilt
        # from the joint run's curated training feedback.
        if city == 'usnyc':
            pinned_inventory = root/'run-inputs/nyc-naip-swin-30cm-v8-adaptation/inventory.parquet'
            assert sha(inventory) == sha(pinned_inventory)
        old_build = build_chips(cfg, raster, feedback_path=None if city == 'usnyc' else frozen_feedback,
            use_default_feedback=False,
            feedback_source_raster=original_raster if original_raster != raster else None,
            inventory_source=inventory, output_dataset=city+'-original-check',
            allowed_chip_ids=ids, allowed_splits={'validation'}, validation_supervision_only=True)
        original_truth = pd.read_parquet(source/'ground-truth.parquet')
        rebuilt = pd.read_parquet(old_build['labels'])
        # Legacy pinned NYC rows predate vegetation groups: null and empty
        # vegetation_type both mean an ordinary scientific-species tree.
        for frame in (original_truth, rebuilt):
            frame['vegetation_type'] = frame.vegetation_type.fillna('')
        keys = ['chip_id','tree_id']
        pd.testing.assert_frame_equal(original_truth.sort_values(keys).reset_index(drop=True),
            rebuilt.sort_values(keys).reset_index(drop=True), check_dtype=False, check_exact=False,
            atol=1e-5, rtol=1e-10)
        predictions = pd.read_parquet(source/'predictions.parquet').reset_index(drop=True)
        np.testing.assert_allclose(mask_values(predictions,Path(old_build['manifest']).parent),
                                   predictions.detection_mask_value, atol=1e-6,rtol=0)
        snapshot = output/'snapshots'/city
        saved = load_persisted_reviews(snapshot)
        manifest = json.loads((snapshot/'manifest.json').read_text())
        published = json.loads((snapshot/'training-feedback.json').read_text())
        current = (published['source_reviews_sha256']==saved['state_revision'] and
                   published['source_manifest_sha256']==_json_sha256(manifest))
        # Derive stale feedback only inside the isolated snapshot; preserve the
        # captured original publication as provenance, never publish live state.
        if not current:
            (snapshot/'previous-published-feedback.json').write_bytes((snapshot/'training-feedback.json').read_bytes())
            finalize_registration_feedback(cfg, original_raster, review_dir=snapshot)
        feedback = json.loads((snapshot/'training-feedback.json').read_text())
        cfg.dataset = feedback['dataset']
        fresh = build_chips(cfg, raster, feedback_path=snapshot/'training-feedback.json',
            feedback_source_raster=original_raster if original_raster != raster else None,
            inventory_source=inventory, output_dataset=city+'-current',
            allowed_chip_ids=ids, allowed_splits={'validation'}, validation_supervision_only=True)
        truth = pd.read_parquet(fresh['labels'])
        changed = []
        for chip in sorted(ids):
            a=original_truth[original_truth.chip_id.eq(chip)].sort_values('tree_id').reset_index(drop=True)
            b=truth[truth.chip_id.eq(chip)].sort_values('tree_id').reset_index(drop=True)
            try: pd.testing.assert_frame_equal(a,b,check_dtype=False,check_exact=False,atol=1e-5,rtol=1e-10)
            except AssertionError: changed.append(chip)
        provenance['cities'][city] = dict(chips=len(ids),old_targets=len(original_truth),new_targets=len(truth),
            changed_label_chips=changed,original_labels_and_masks_reproduced=True,
            publication_was_current=current,review_revision=feedback['source_reviews_sha256'],
            vegetation_targets=int(truth.vegetation_type.fillna('').ne('').sum()))
        for run in runs:
            src=root/'runs'/run/'evaluation'/cohort
            cov=json.loads((src/'inference-coverage.json').read_text())
            assert cov['complete'] and {r['chip_id'] for r in cov['chips']}==ids
            metadata=json.loads((src/'evaluation-metadata.json').read_text())
            assert metadata['config']['targets']==meta['config']['targets']
            assert metadata['config']['imagery']['resolution_m']==cfg.imagery.resolution_m
            assert json.loads((src/'taxonomy.json').read_text())==json.loads((source/'taxonomy.json').read_text())
            p=pd.read_parquet(src/'predictions.parquet').reset_index(drop=True)
            for name in ('predictions.parquet','ground-truth.parquet','evaluation-metadata.json'):
                provenance['files'][str(src/name)]=sha(src/name)
            p['detection_mask_value']=mask_values(p,Path(fresh['manifest']).parent)
            p=p.drop(columns=['center_target'],errors='ignore')  # old-label target value is stale
            dst=output/'runs'/run/'evaluation'/cohort;dst.mkdir(parents=True)
            p.to_parquet(dst/'predictions.parquet',index=False)
            truth.to_parquet(dst/'ground-truth.parquet',index=False)
            (dst/'evaluation-metadata.json').write_text(json.dumps(metadata,indent=2))
        print(city,provenance['cities'][city],flush=True)
        (output/'provenance.json').write_text(json.dumps(provenance,indent=2))
    best=[]; curves=[]
    for city,(_,cohort) in CITIES.items():
        for run in runs:
            d=output/'runs'/run/'evaluation'/cohort
            truth=pd.read_parquet(d/'ground-truth.parquet');p=pd.read_parquet(d/'predictions.parquet')
            for policy in POLICIES:
                rows=[dict(run=run,city=city,policy=policy,**r) for r in sweep(p,truth,.6,policy)]
                winner=max(rows,key=lambda r:(r['f2'],r['threshold']))
                winner['attributes_on_matched_detections']=swept_attributes(d,winner['threshold'],policy)
                best.append(winner);curves.extend(rows)
                print(city,run,policy,'F2',winner['f2'],'threshold',winner['threshold'],flush=True)
    (output/'best.json').write_text(json.dumps(best,indent=2))
    (output/'sweep.json').write_text(json.dumps(curves,indent=2))
    (output/'COMPLETE').write_text('Saved predictions rescored against identical current validation supervision. No retraining or validation-loss recomputation.\n')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--resume',action='store_true',help='Continue the same frozen snapshot after an interrupted build')
    parser.add_argument('--runs',nargs='+',choices=RUNS,default=RUNS)
    parser.add_argument('--count-baseline',type=Path,help='Prior completed rescore snapshot for unique reviewed-tree deltas')
    args=parser.parse_args();build(args.root,args.output,args.resume,args.runs,args.count_baseline)
