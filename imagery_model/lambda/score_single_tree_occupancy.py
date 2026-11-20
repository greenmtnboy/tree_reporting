"""Offline occupancy diagnostic on rebuilt explicit single-tree validation groups.

One detection per retained-label ownership group is allowed. This is an occupancy
proxy, NOT distance-matched detection precision/recall or duplicate-classifier AP.
Never accesses live annotations or training/test pixels.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def summarize(counts):
    counts = np.asarray(counts, dtype=int)
    assert np.all(counts >= 0)
    total = int(counts.sum())
    covered = int((counts > 0).sum())
    return dict(groups=len(counts), detections=total, covered=covered,
                missed=int((counts == 0).sum()),
                duplicated_groups=int((counts > 1).sum()),
                excess_peaks=int(np.maximum(counts - 1, 0).sum()),
                occupancy_precision=covered / total if total else None,
                occupancy_recall=covered / len(counts) if len(counts) else None)


def build(benchmark, output, root):
    from rescore_current_validation import CITIES, remap
    from score_frozen_validation import assert_truth_equal
    from urban_tree_ml.chips import build_chips
    from urban_tree_ml.config import ProjectConfig
    assert (benchmark / 'COMPLETE').exists()
    provenance = json.loads((benchmark / 'provenance.json').read_text())
    output.mkdir(parents=True, exist_ok=False)
    rows, coverage, hashes = [], {}, {}
    for city, cohort in [('ussfo', 'validation'), ('usbos', 'validation-usbos'), ('usnyc', 'validation-usnyc')]:
        reference = provenance['reference']
        native = root / 'runs' / reference / 'evaluation' / cohort
        cfg = ProjectConfig.model_validate(json.loads((native / 'evaluation-metadata.json').read_text())['config'])
        raster = remap(cfg.imagery.local_raster, root)
        name = CITIES[city][0]
        original = root/'imagery'/city/('2022' if city == 'ussfo' else '2023')/(name+('.tif' if city == 'usnyc' else '.vrt'))
        frozen = root / 'run-inputs' / reference / city
        ids = set(pd.read_parquet(benchmark/'chips'/(city+'-reference-validation')/'chips.parquet').chip_id)
        cfg.paths.root = output
        cfg.paths.annotations = output / 'annotation-bundles'
        cfg.imagery.local_raster = raster
        cfg.imagery.boundary_shifts = False
        cfg.reference.taxonomy_path = root / 'runs' / reference / 'vegetation-taxonomy.json'
        cfg.dataset = json.loads((frozen/'training-feedback.json').read_text())['dataset']
        print('Rebuilding full validation groups:', city, flush=True)
        built = build_chips(cfg, raster, feedback_path=frozen/'training-feedback.json',
                            use_default_feedback=False, feedback_source_raster=original if original != raster else None,
                            inventory_source=frozen/'inventory.parquet', output_dataset=city+'-groups',
                            allowed_chip_ids=ids, allowed_splits={'validation'},
                            validation_supervision_only=True, validation_full_targets=True)
        chips = Path(built['manifest']).parent
        assert_truth_equal(pd.read_parquet(built['labels']), pd.read_parquet(native/'ground-truth.parquet'))
        manifest = pd.read_parquet(chips / 'chips.parquet')
        maps = {}
        for row in manifest.itertuples():
            with np.load(chips / row.path) as arrays:
                groups = arrays['single_tree_groups'].squeeze()
                with np.load(benchmark/'chips'/(city+'-reference-validation')/row.path) as masks:
                    np.testing.assert_array_equal(arrays['scoring_mask'], masks['scoring_mask'])
                ids = np.unique(groups[groups > 0])
                if len(ids):
                    maps[row.chip_id] = (groups.copy(), ids)
                hashes[str(chips / row.path) + ':single_tree_groups'] = hashlib.sha256(groups.tobytes()).hexdigest()
        coverage[city] = dict(validation_chips=len(manifest), chips_with_groups=len(maps),
                              materialized_groups=sum(len(ids) for _, ids in maps.values()))
        for run in provenance['runs']:
            path = benchmark / 'runs' / run / 'evaluation' / cohort / 'predictions.parquet'
            predictions = pd.read_parquet(path)
            hashes[str(path)] = hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()
            by_chip = dict(tuple(predictions.groupby('chip_id')))
            for threshold in (.13, .35):
                counts = []
                for chip, (groups, ids) in maps.items():
                    p = by_chip.get(chip, predictions.iloc[:0])
                    p = p[p.score >= threshold]
                    assigned = groups[p.output_y.to_numpy(dtype=int), p.output_x.to_numpy(dtype=int)]
                    counts.extend(int((assigned == group).sum()) for group in ids)
                rows.append(dict(city=city, run=run, threshold=threshold, **summarize(counts)))
    (output / 'metrics.json').write_text(json.dumps(dict(coverage=coverage, rows=rows), indent=2))
    (output / 'provenance.json').write_text(json.dumps(hashes, indent=2))
    (output / 'COMPLETE').write_text('Frozen validation labels and masks verified. Occupancy proxy only.\n')
    print(json.dumps(dict(coverage=coverage, rows=rows), indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--benchmark', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--root', type=Path, required=True)
    a = p.parse_args()
    build(a.benchmark, a.output, a.root)
