"""Paired inference-only comparison of NYC 2022/60cm and 2023/native30cm.

Reuses frozen chip geography, labels, split, taxonomy, normalization and v7
weights. No training, held-out test access, live curation or reviewer changes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.shutil import copy as raster_copy
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window, bounds
import requests
from shapely.geometry import box

from evaluate_nyc_cold_start import PARENT, RUN as OLD, CHECKPOINT_SHA
from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.evaluation import run_evaluation, _greedy_matches, _attribute_metrics
from urban_tree_ml.imagery import _sign_asset_href

RUN = 'nyc-naip-v7-native30-comparison-v2'
ITEM = 'nj_m_4007309_sw_18_030_20230820_20231019'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def valid_pixels(image):
    # Some source mosaics have no nodata tag and claim blank gaps are valid.
    return np.isfinite(image).all(axis=0) & np.any(image != 0, axis=0)


def range_download(href, partial):
    """Bounded range requests avoid workstation proxy full-response stalls."""
    signed = _sign_asset_href(href)
    head = requests.get(signed, headers={'Range': 'bytes=0-0'}, timeout=30)
    if head.status_code != 206:
        raise ValueError('Expected range support')
    size = int(head.headers['Content-Range'].split('/')[-1])
    etag = head.headers['ETag']
    chunk = 8 * 1024 * 1024
    def fetch(start):
        end = min(start + chunk, size) - 1
        for attempt in range(3):
            try:
                response = requests.get(signed, headers={'Range': f'bytes={start}-{end}', 'If-Match': etag}, timeout=45)
                if (response.status_code != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{size}'
                        or response.headers.get('ETag') != etag or len(response.content) != end-start+1):
                    raise ValueError('Range response identity/length mismatch')
                return response.content
            except (requests.RequestException, ValueError):
                if attempt == 2:
                    raise RuntimeError('Range download failed after three attempts') from None
    sha = hashlib.sha256()
    with partial.open('wb') as output, ThreadPoolExecutor(max_workers=4) as pool:
        # At most four chunks in memory; never submit the entire orthophoto.
        starts = iter(range(0, size, chunk))
        pending = [pool.submit(fetch, start) for start in [next(starts) for _ in range(min(4, (size+chunk-1)//chunk))]]
        written = 0
        while pending:
            content = pending.pop(0).result()
            output.write(content)
            sha.update(content)
            written += len(content)
            if (written // chunk) % 32 == 0:
                print(json.dumps(dict(stage='download', bytes=written, total=size)), flush=True)
            start = next(starts, None)
            if start is not None:
                pending.append(pool.submit(fetch, start))
    assert partial.stat().st_size == size
    return size, size, sha.hexdigest()


def acquire(root, target):
    metadata = target / 'source-item.json'
    if not metadata.exists():
        response = requests.get('https://planetarycomputer.microsoft.com/api/stac/v1/collections/naip/items/' + ITEM, timeout=60)
        response.raise_for_status()
        save(metadata, response.json())
    item = json.loads(metadata.read_text())
    assert item['properties']['gsd'] == .3
    path = root / 'imagery/usnyc/2023' / (ITEM + '.tif')
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = path.with_suffix('.manifest.json')
    if not path.exists():
        print('Downloading one native 30cm orthophoto', flush=True)
        partial = path.with_suffix('.tif.part')
        size, expected, sha = range_download(item['assets']['image']['href'], partial)
        with rasterio.open(partial) as src:
            assert src.count == 4 and src.res == (.3, .3)
        partial.replace(path)
        save(manifest, dict(item_id=ITEM, source_href=item['assets']['image']['href'],
            acquisition_datetime=item['properties']['datetime'], bytes=size,
            server_content_length=expected, sha256=sha))
    assert digest(path) == json.loads(manifest.read_text())['sha256']
    return path


def prepare(root):
    target = root / 'run-inputs' / RUN
    if (target / 'PREPARED.json').exists():
        return json.loads((target / 'PREPARED.json').read_text())
    native_path = acquire(root, target)
    old_inputs = root / 'run-inputs' / OLD
    old_config = json.loads((old_inputs / 'config.json').read_text())
    aligned_path = target / 'native30-on-frozen-grid.vrt'
    # Keep original geotransform/IDs exactly. This is a 30->30cm subpixel
    # alignment, NOT a 60cm intermediate or an enlargement of old imagery.
    with rasterio.open(old_config['imagery']['local_raster']) as grid, rasterio.open(native_path) as native:
        assert native.crs == grid.crs and grid.res == native.res == (.3, .3)
        with WarpedVRT(native, crs=grid.crs, transform=grid.transform,
                       width=grid.width, height=grid.height, resampling=Resampling.bilinear) as aligned:
            raster_copy(aligned, aligned_path, driver='VRT')
        footprint = box(*native.bounds)
        jobs = []
        for name, filename in [('labeled', 'config.json'), ('central-park', 'config-central-park.json')]:
            config = json.loads((old_inputs / filename).read_text())
            source = root / 'chips' / config['dataset']
            destination = root / 'chips' / (RUN + '-' + name)
            if (destination / 'chips.parquet').exists():
                # Resume only this recipe's own verified completed subset.
                existing = json.loads((destination / 'summary.json').read_text())
                assert existing['source_dataset'] == config['dataset']
                assert existing['source_raster'] == str(aligned_path)
            destination.mkdir(parents=True, exist_ok=True)
            manifest = pd.read_parquet(source / 'chips.parquet')
            selected, audit = [], []
            with rasterio.open(aligned_path) as aligned:
                for row in manifest.itertuples(index=False):
                    assert row.split != 'test'
                    window = Window(row.column_offset, row.row_offset, 512, 512)
                    if not footprint.covers(box(*bounds(window, grid.transform))):
                        continue
                    with np.load(source / row.path) as old_chip:
                        arrays = {key: old_chip[key].copy() for key in old_chip.files}
                    old_valid = float(valid_pixels(arrays['image']).mean())
                    image = aligned.read([1, 2, 3, 4], window=window)
                    # Do not synthesize nodata=0: zero in an individual channel
                    # is legitimate (especially over water). Source masks are
                    # inherited, and full footprint containment is checked above.
                    new_valid = float((valid_pixels(image) & (aligned.read_masks([1,2,3,4], window=window) > 0).all(axis=0)).mean())
                    keep = old_valid == 1 and new_valid == 1
                    audit.append(dict(chip_id=row.chip_id, split=row.split, old_valid_fraction=old_valid,
                                      new_valid_fraction=new_valid, included=keep))
                    if not keep:
                        continue
                    arrays['image'] = image.astype(np.float32) * config['imagery']['input_scale']
                    output = destination / row.path
                    output.parent.mkdir(parents=True, exist_ok=True)
                    np.savez_compressed(output, **arrays)
                    selected.append(row.chip_id)
            result = manifest[manifest.chip_id.isin(selected)].copy()
            assert len(result) > 0
            result.to_parquet(destination / 'chips.parquet', index=False)
            labels = pd.read_parquet(source / 'labels.parquet')
            labels = labels[labels.chip_id.isin(selected)].copy()
            labels.to_parquet(destination / 'labels.parquet', index=False)
            shutil.copyfile(source / 'normalization.json', destination / 'normalization.json')
            summary = dict(chips=len(result), trees=len(labels), source_raster=str(aligned_path),
                           purpose='inference-only paired imagery comparison; frozen 2022 labels/targets',
                           label_status='unavailable' if name == 'central-park' else 'inventory',
                           source_dataset=config['dataset'])
            save(destination / 'summary.json', summary)
            config.update(experiment=RUN, dataset=destination.name)
            config['imagery'].update(local_raster=str(aligned_path), source_resolution_m=.3,
                item_ids=[ITEM], datetime='2023-08-20/2023-08-20')
            # Retain the OLD planting cutoff and labels for the paired comparison.
            config['training'].update(batch_size=1, workers=0)
            save(target / filename, config)
            save(target / (name + '-pixel-audit.json'), audit)
            for split, group in result.groupby('split'):
                cohort = split + '-usnyc' + ('-central-park' if name == 'central-park' else '')
                jobs.append(dict(config=filename, cohort=cohort, split=split, chips=len(group),
                                 labels=int((labels.split == split).sum())))
            print(json.dumps(dict(stage='prepared', name=name, chips=len(result), labels=len(labels),
                                  rejected=sum(not x['included'] for x in audit))), flush=True)
    prepared = dict(jobs=jobs, native_raster=str(native_path), native_sha256=digest(native_path),
                    checkpoint_sha256=CHECKPOINT_SHA)
    save(target / 'PREPARED.json', prepared)
    return prepared


def infer(root, prepared):
    import torch
    torch.set_num_threads(4)
    checkpoint = root / 'runs' / PARENT / 'checkpoints/007.ckpt'
    assert digest(checkpoint) == CHECKPOINT_SHA
    target = root / 'run-inputs' / RUN
    output = root / 'runs' / RUN
    for job in prepared['jobs']:
        directory = output / 'evaluation' / job['cohort']
        if (directory / 'inference-coverage.json').exists():
            assert json.loads((directory / 'inference-coverage.json').read_text())['chip_count'] == job['chips']
            continue
        config = ProjectConfig.model_validate(json.loads((target / job['config']).read_text()))
        print(json.dumps(dict(stage='inference', **job)), flush=True)
        start = time.monotonic()
        result = run_evaluation(config, checkpoint, split=job['split'], cohort=job['cohort'],
                                output_run=output, device_name='cpu')
        assert result['chips'] == job['chips']
        print(json.dumps(dict(stage='complete', cohort=job['cohort'], seconds=round(time.monotonic()-start,1))), flush=True)


def score(predictions, truth, radius):
    predictions = predictions[predictions.score >= .13].reset_index(drop=True)
    truth = truth.reset_index(drop=True)
    matches = _greedy_matches(predictions, truth, radius_output_px=radius / .6)
    attrs = _attribute_metrics(predictions, truth, matches, dbh_tolerance_in=2)
    paired = {}
    for p, t, _ in matches:
        row = truth.loc[t]
        if pd.notna(row.species_id):
            paired[(row.chip_id, str(row.tree_id))] = int(predictions.loc[p, 'species_id']) == int(row.species_id)
    return dict(attributes=attrs, matched=len(matches), predictions=len(predictions), labels=len(truth)), paired


def report(root, path):
    target = root / 'run-inputs' / RUN
    old_eval, new_eval = [root / 'runs' / run / 'evaluation' for run in (PARENT, RUN)]
    prepared = json.loads((target / 'PREPARED.json').read_text())
    for job in prepared['jobs']:
        directory = new_eval / job['cohort']
        coverage = json.loads((directory / 'inference-coverage.json').read_text())
        config = json.loads((target / job['config']).read_text())
        chip_root = root / 'chips' / config['dataset']
        manifest = pd.read_parquet(chip_root / 'chips.parquet')
        assert coverage['complete'] and coverage['chip_count'] == job['chips']
        assert set(manifest.loc[manifest.split == job['split'], 'chip_id']) == {x['chip_id'] for x in coverage['chips']}
        assert json.loads((directory/'taxonomy.json').read_text()) == json.loads(Path(config['reference']['taxonomy_path']).read_text())
        normalization = json.loads((chip_root/'normalization.json').read_text())
        reference = json.loads(Path(config['reference']['normalization_path']).read_text())
        assert all(normalization[k] == reference[k] for k in ('mean', 'std'))
        source_dataset = json.loads((chip_root/'summary.json').read_text())['source_dataset']
        assert (chip_root/'normalization.json').read_bytes() == (root/'chips'/source_dataset/'normalization.json').read_bytes()
        if 'central-park' in job['cohort']:
            assert pd.read_parquet(directory/'ground-truth.parquet').empty
            assert pd.read_parquet(directory/'matches.parquet').empty
            assert json.loads((directory/'metrics.json').read_text())['prediction_only']
    data = {}
    for split in ('train', 'validation'):
        cohort = split + '-usnyc'
        truth = pd.read_parquet(new_eval / cohort / 'ground-truth.parquet')
        chips = {x['chip_id'] for x in json.loads((new_eval/cohort/'inference-coverage.json').read_text())['chips']}
        old_truth = pd.read_parquet(old_eval / cohort / 'ground-truth.parquet')
        pd.testing.assert_frame_equal(truth.reset_index(drop=True), old_truth[old_truth.chip_id.isin(chips)].reset_index(drop=True))
        old_pred = pd.read_parquet(old_eval / cohort / 'predictions.parquet')
        new_pred = pd.read_parquet(new_eval / cohort / 'predictions.parquet')
        data[split] = (old_pred[old_pred.chip_id.isin(chips)], new_pred, truth, len(chips))
    data['combined'] = tuple(pd.concat([data[s][i] for s in ('train','validation')],ignore_index=True) for i in range(3)) + (sum(data[s][3] for s in ('train','validation')),)
    rows = []
    for split, (old_pred, new_pred, truth, chips) in data.items():
        for radius in (2., 4.):
            old, a = score(old_pred, truth, radius)
            new, b = score(new_pred, truth, radius)
            common = a.keys() & b.keys()
            rows.append(dict(split=split, chips=chips, radius_m=radius, old=old, new=new,
                common_matched_supported=len(common), old_correct_common=sum(a[k] for k in common),
                new_correct_common=sum(b[k] for k in common)))
    distributions = {}
    for key, idx in [('old', 0), ('new', 1)]:
        pred = data['combined'][idx]
        distributions[key] = pred.loc[pred.score >= .13, 'species'].value_counts().head(15).to_dict()
    save(target / 'comparison.json', dict(threshold=.13, rows=rows, checkpoint_sha256=CHECKPOINT_SHA,
                                         top_predicted_species=distributions, jobs=prepared['jobs']))
    lines = ['# NYC native 30 cm paired inference comparison', '',
        'Frozen v7 checkpoint 007; CPU inference only. No NYC training, test access, reviewer restart or live curation changes.', '',
        'One 2023-08-20 native 30 cm RGBNIR orthophoto, NJ-prefixed '+ITEM+'. Covers Central Park and surrounding Manhattan streets. Native raster hash and source STAC are in run inputs.', '',
        'Original 512px/153.6m window geography, labels, planting cutoff, taxonomy, normalization and confidence 0.13 retained. Native 30cm source aligned directly onto the frozen 30cm grid with bilinear sampling. Every accepted chip has 100% nonblank pixels in BOTH images; source-footprint containment and new raster masks also checked. Old evaluations preserved; only the paired valid subset is compared.', '',
        '| Subset | Chips | Radius | 2022 60cm species | Samples | 2023 30cm species | Samples |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        a,b=[row[k]['attributes']['species'] for k in ('old','new')]
        lines.append(f"| {row['split']} | {row['chips']} | {row['radius_m']}m | {a['accuracy']:.2%} | {a['samples']} | {b['accuracy']:.2%} | {b['samples']} |")
    lines += ['', '## Top-5 species on the combined paired street subset', '',
              '| Radius | 2022 top-5 | 2023 top-5 |', '|---|---:|---:|']
    for row in rows:
        if row['split'] == 'combined':
            a,b=[row[k]['attributes']['species'] for k in ('old','new')]
            lines.append(f"| {row['radius_m']}m | {a['top_k_accuracy']:.2%} | {b['top_k_accuracy']:.2%} |")
    lines += ['', 'The newer imagery modestly increases observed top-1 accuracy and more strongly increases top-5 inclusion, but top-1 remains far too poor for automatic species publishing. Small absolute correct counts and imperfect spatial matching limit conclusions.', '']
    lines += ['', '## Same reference trees matched in both images', '',
        '| Subset | Radius | Common supported trees | 2022 correct | 2023 correct |', '|---|---:|---:|---:|---:|']
    for row in rows:
        lines.append(f"| {row['split']} | {row['radius_m']}m | {row['common_matched_supported']} | {row['old_correct_common']} | {row['new_correct_common']} |")
    lines += ['', 'Species accuracy is conditional on spatial matches to supported inventory taxa. Common-tree comparison reduces changes in matched sample composition, but spatial matches can still identify the wrong crown. Municipal inventory is incomplete and may be temporally/positionally imperfect; detection precision is not exhaustive tree accuracy.', '',
        f'Train/validation are existing curation split assignments: neither NYC subset trained this frozen model. The {data["validation"][3]}-chip validation subset is small; combined figures are a cold-start diagnostic, not a new held-out benchmark.', '',
        '188 Central Park chips were inferred and remain unscored because there is no reference ground truth. The strict all-zero-pixel check excluded one street chip (1 pixel) and one park chip (2 pixels); these are not broad source coverage gaps. This comparison changes acquisition year/date, sensor/color processing AND resolution, so improvements cannot be attributed to resolution alone.', '',
        'Outputs: '+str(root / 'runs' / RUN), 'Recipe/audit: '+str(target), '']
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines), encoding='utf-8')
    save(target / 'COMPLETE.json', dict(report=str(path), checkpoint_sha256=CHECKPOINT_SHA))
    print(json.dumps(dict(stage='reported', report=str(path), street_chips=data['combined'][3],
                         coverage_verified=True, label_equality_verified=True)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--stage', choices=['prepare','infer','report','all'], default='all')
    parser.add_argument('--report', type=Path, default=Path('reports/nyc-v7-native30-comparison.md'))
    args = parser.parse_args()
    if args.stage in ('prepare','all'):
        prepared = prepare(args.root)
    else:
        prepared = json.loads((args.root/'run-inputs'/RUN/'PREPARED.json').read_text())
    if args.stage in ('infer','all'):
        infer(args.root, prepared)
    if args.stage in ('report','all'):
        report(args.root, args.report)
