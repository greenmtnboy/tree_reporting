"""Compare completed models against one run's frozen validation labels and masks.

Writes an isolated benchmark only; never reads live curation or sealed test pixels.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

from rescore_current_validation import CITIES, mask_values, remap, sha
from score_confidence_sweep import sweep
from urban_tree_ml.chips import build_chips
from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.sweep_history import POLICIES, swept_attributes


def assert_truth_equal(actual, expected):
    actual, expected = actual.copy(), expected.copy()
    for frame in (actual, expected):
        frame['vegetation_type'] = frame.vegetation_type.fillna('')
    keys = ['chip_id', 'tree_id']
    pd.testing.assert_frame_equal(
        actual.sort_values(keys).reset_index(drop=True),
        expected.sort_values(keys).reset_index(drop=True),
        check_dtype=False, check_exact=False, atol=1e-5, rtol=1e-10)


def prediction_source(root, reference, run, cohort):
    receipt_path = root/'runs'/reference/'paired-parent-validation.json'
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt['parent_run'] == run:
            warm_path = root/'run-inputs'/reference/'warm-start.json'
            warm = json.loads(warm_path.read_text())
            assert receipt['same_decoder_and_inputs'] is True and receipt['test_evaluated'] is False
            assert receipt['checkpoint_sha256'] == warm['checkpoint_sha256']
            assert Path(warm['checkpoint']).parts[-3] == run
            paired = receipt['cohorts'][cohort]
            assert paired == 'parent-'+cohort
            return root/'runs'/reference/'evaluation'/paired, [receipt_path, warm_path]
    return root/'runs'/run/'evaluation'/cohort, []


def build(root, output, reference, runs, resume=False):
    assert reference in runs
    for run in runs:
        assert (root/'runs'/run/'COMPLETE').is_file(), run
    if resume:
        assert not (output/'COMPLETE').exists(), 'Completed benchmarks are immutable'
    from urban_tree_ml.curated_validation import load_policy, verify_cohorts
    validation_admission = None
    if load_policy(root/'run-inputs'/reference) is not None:
        cohorts = {}
        for city, (_, cohort) in CITIES.items():
            coverage = json.loads((root/'runs'/reference/'evaluation'/cohort/'inference-coverage.json').read_text())
            cohorts[city] = {row['chip_id'] for row in coverage['chips']}
        validation_admission = verify_cohorts(root, reference, cohorts)
    if resume:
        assert not (output/'COMPLETE').exists(), 'Completed benchmarks are immutable'
        provenance = json.loads((output/'provenance.json').read_text())
        assert provenance['runs'] == runs and provenance['reference'] == reference
    else:
        output.mkdir(parents=True, exist_ok=False)
        provenance = dict(created_at=datetime.now(timezone.utc).isoformat(),
                          reference=reference, runs=runs, test_evaluated=False,
                          source='reference run frozen curation, labels and masks',
                          files={}, cities={})
        if validation_admission is not None:
            provenance['validation_policy'] = validation_admission
            for path in (root/'run-inputs'/reference/'validation-policy.json',
                         root/'runs'/reference/'curated-validation.json'):
                provenance['files'][str(path)] = sha(path)
        # Freeze the local reconstruction code too: platform geometry fixes must
        # remain visible and cannot silently change during a resumed audit.
        import urban_tree_ml.single_tree as polygon_geometry
        import urban_tree_ml.targets as target_builder
        for path in (Path(__file__).resolve(), Path(build_chips.__code__.co_filename).resolve(),
                     Path(polygon_geometry.__file__).resolve(), Path(target_builder.__file__).resolve()):
            provenance['files'][str(path)] = sha(path)
        for city in CITIES:
            dst = output/'snapshots'/city
            dst.mkdir(parents=True)
            for name in ('manifest.json', 'reviews.json', 'training-feedback.json'):
                src = root/'run-inputs'/reference/city/name
                shutil.copy2(src, dst/name)
                provenance['files'][str(src)] = sha(src)
        (output/'provenance.json').write_text(json.dumps(provenance, indent=2))
    # A resumed benchmark must still use the exact same immutable sources.
    for path, expected in provenance['files'].items():
        assert sha(Path(path)) == expected, path
    for city, (name, cohort) in CITIES.items():
        if city in provenance['cities']:
            continue
        print('Rebuilding frozen validation:', city, flush=True)
        src = root/'runs'/reference/'evaluation'/cohort
        meta = json.loads((src/'evaluation-metadata.json').read_text())
        coverage = json.loads((src/'inference-coverage.json').read_text())
        ids = {row['chip_id'] for row in coverage['chips']}
        assert coverage['complete'] and len(ids) == coverage['chip_count']
        cfg = ProjectConfig.model_validate(meta['config'])
        raster = remap(cfg.imagery.local_raster, root)
        original = root/'imagery'/city/('2022' if city == 'ussfo' else '2023')/(name+('.tif' if city == 'usnyc' else '.vrt'))
        feedback = root/'run-inputs'/reference/city/'training-feedback.json'
        inventory = root/'run-inputs'/reference/city/'inventory.parquet'
        cfg.paths.root = output
        cfg.paths.annotations = output/'annotation-bundles'
        cfg.imagery.local_raster = raster
        cfg.imagery.boundary_shifts = False
        cfg.reference.taxonomy_path = root/'runs'/reference/'vegetation-taxonomy.json'
        cfg.dataset = json.loads(feedback.read_text())['dataset']
        built = build_chips(cfg, raster, feedback_path=feedback,
            use_default_feedback=False,
            feedback_source_raster=original if original != raster else None,
            inventory_source=inventory, output_dataset=city+'-reference-validation',
            allowed_chip_ids=ids, allowed_splits={'validation'},
            validation_supervision_only=True)
        chip_dir = Path(built['manifest']).parent
        truth = pd.read_parquet(built['labels'])
        native_truth = pd.read_parquet(src/'ground-truth.parquet')
        assert_truth_equal(truth, native_truth)
        manifest = pd.read_parquet(built['manifest'])
        assert set(manifest.chip_id) == ids and set(manifest.split) == {'validation'}
        native_p = pd.read_parquet(src/'predictions.parquet').reset_index(drop=True)
        np.testing.assert_allclose(mask_values(native_p, chip_dir),
                                   native_p.detection_mask_value, atol=1e-6, rtol=0)
        audit = dict(chips=len(ids), targets=len(truth),
                     vegetation_targets=int(truth.vegetation_type.fillna('').isin(['copse','shrub-mass']).sum()),
                     reference_labels_and_masks_reproduced=True, original_label_changes={})
        for run in runs:
            d, receipts = prediction_source(root, reference, run, cohort)
            for receipt_path in receipts:
                provenance['files'][str(receipt_path)] = sha(receipt_path)
            audit.setdefault('prediction_sources', {})[run] = str(d)
            cov = json.loads((d/'inference-coverage.json').read_text())
            assert cov['complete'] and {row['chip_id'] for row in cov['chips']} == ids
            metadata = json.loads((d/'evaluation-metadata.json').read_text())
            assert metadata['config']['targets'] == meta['config']['targets']
            assert metadata['config']['imagery']['resolution_m'] == cfg.imagery.resolution_m
            assert metadata['config']['imagery']['chip_pixels'] == cfg.imagery.chip_pixels
            assert json.loads((d/'taxonomy.json').read_text()) == json.loads((src/'taxonomy.json').read_text())
            old_truth = pd.read_parquet(d/'ground-truth.parquet')
            changed = []
            for chip in sorted(ids):
                try:
                    assert_truth_equal(old_truth[old_truth.chip_id.eq(chip)], truth[truth.chip_id.eq(chip)])
                except AssertionError:
                    changed.append(chip)
            audit['original_label_changes'][run] = dict(targets=len(old_truth), changed_chips=changed)
            p = pd.read_parquet(d/'predictions.parquet').reset_index(drop=True)
            p['detection_mask_value'] = mask_values(p, chip_dir)
            p = p.drop(columns=['center_target'], errors='ignore')
            dst = output/'runs'/run/'evaluation'/cohort
            dst.mkdir(parents=True, exist_ok=True)
            p.to_parquet(dst/'predictions.parquet', index=False)
            native_truth.to_parquet(dst/'ground-truth.parquet', index=False)
            (dst/'evaluation-metadata.json').write_text(json.dumps(metadata, indent=2))
            for filename in ('taxonomy.json','inference-coverage.json'):
                shutil.copy2(d/filename, dst/filename)
            for filename in ('predictions.parquet','ground-truth.parquet','evaluation-metadata.json'):
                provenance['files'][str(d/filename)] = sha(d/filename)
        for path in (feedback, inventory, cfg.reference.taxonomy_path):
            provenance['files'][str(path)] = sha(path)
        provenance['cities'][city] = audit
        (output/'provenance.json').write_text(json.dumps(provenance, indent=2))
        print(city, 'labels and masks reproduced; shared supervision exported', flush=True)
    best, curves, fixed = [], [], []
    for city, (_, cohort) in CITIES.items():
        for run in runs:
            d = output/'runs'/run/'evaluation'/cohort
            truth = pd.read_parquet(d/'ground-truth.parquet')
            p = pd.read_parquet(d/'predictions.parquet')
            cfg = json.loads((d/'evaluation-metadata.json').read_text())['config']
            scale = cfg['imagery']['resolution_m']*cfg['targets']['output_stride']
            for policy in POLICIES:
                rows = [dict(run=run,city=city,policy=policy,**r) for r in sweep(p,truth,scale,policy)]
                winner = dict(max(rows,key=lambda row:(row['f2'],row['threshold'])))
                winner['attributes_on_matched_detections'] = swept_attributes(d,winner['threshold'],policy)
                best.append(winner)
                curves.extend(rows)
                for threshold in (.13,.35):
                    row = dict(next(r for r in rows if r['threshold'] == threshold))
                    row['attributes_on_matched_detections'] = swept_attributes(d,threshold,policy)
                    fixed.append(row)
                print(city,run,policy,winner['f2'],winner['threshold'],flush=True)
    for name, data in [('best',best),('sweep',curves),('fixed',fixed)]:
        (output/(name+'.json')).write_text(json.dumps(data,indent=2))
    (output/'COMPLETE').write_text('Both models scored on identical frozen reference labels and rebuilt masks. Tests sealed.\n')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--reference',required=True)
    p.add_argument('--runs',nargs='+',required=True)
    p.add_argument('--resume',action='store_true')
    a = p.parse_args()
    build(a.root,a.output,a.reference,a.runs,a.resume)
