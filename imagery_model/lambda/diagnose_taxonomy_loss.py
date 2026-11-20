"""Local CPU, frozen-validation-only taxonomy checkpoint diagnosis.

No optimizer, downloads, cloud calls or live annotation reads. FP32 is a
diagnostic comparison, not a replacement for the original CUDA bf16 gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from urban_tree_ml.model import RawImageryTreeModel


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main(root, output, limit=0, threads=4, sample_batches=0):
    torch.set_num_threads(threads)
    run = 'sf-boston-nyc-naip-swin-30cm-v16-curated-finetune'
    source = root / 'runs' / run
    rebuilt = root / 'benchmarks/v15-vs-v16-single-tree-occupancy'
    assert (rebuilt / 'COMPLETE').exists()
    output.mkdir(parents=True, exist_ok=False)
    cfg = json.loads((source / 'config-ussfo.json').read_text())
    taxonomy = json.loads((source / 'vegetation-taxonomy.json').read_text())
    checkpoints = {'parent': 'parent-baseline.ckpt', 'best': '001-0000848.ckpt',
                   'final': 'last.ckpt'}
    models = {}
    provenance = {'precision': 'CPU float32', 'test_evaluated': False,
                  'training_performed': False, 'checkpoints': {}, 'files': {},
                  'limit': limit, 'threads': threads, 'sample_batches': sample_batches}
    for label, filename in checkpoints.items():
        path = source / 'checkpoints' / filename
        checkpoint = torch.load(path, map_location='cpu', weights_only=False)
        args = dict(cfg['model'], pretrained=False)
        model = RawImageryTreeModel(**args, genus_classes=len(taxonomy['genera']),
                                   species_classes=len(taxonomy['species'])).eval()
        state = {k.removeprefix('network.'): v for k, v in checkpoint['state_dict'].items()}
        model.load_state_dict(state, strict=True)
        models[label] = model
        provenance['checkpoints'][label] = dict(path=str(path), sha256=sha(path),
                                               step=checkpoint['global_step'])
        del checkpoint, state
    assert provenance['checkpoints']['parent']['sha256'] == '3b9e7cab863698c63c812b3dee89f55070134656d5e3803054ec8e3354362a82'
    frames, norms = [], []
    for city in ('ussfo', 'usbos', 'usnyc'):
        original = root / 'chips' / (run + '-' + city)
        normpath = original / 'normalization.json'
        norms.append(json.loads(normpath.read_text()))
        provenance['files'][str(normpath)] = sha(normpath)
        manifest = pd.read_parquet(original / 'chips.parquet')
        manifest = manifest[manifest.split == 'validation'].copy()
        directory = rebuilt / 'chips' / (city + '-groups')
        rebuilt_manifest = pd.read_parquet(directory / 'chips.parquet')
        assert set(manifest.chip_id) == set(rebuilt_manifest.chip_id)
        paths = rebuilt_manifest.set_index('chip_id').path.to_dict()
        manifest['file'] = manifest.chip_id.map(lambda chip: directory / paths[chip])
        manifest['city'] = city
        frames.append(manifest)
    assert all(n['mean'] == norms[0]['mean'] and n['std'] == norms[0]['std'] for n in norms)
    windows = pd.concat(frames, ignore_index=True)
    assert len(windows) == 1223
    windows['original_batch'] = np.arange(len(windows)) // 2
    windows['selection'] = 'full'
    if sample_batches:
        # Keep original batch pairs, including a possible city boundary pair.
        primary = json.loads((root/'benchmarks/v15-vs-v16-frozen-validation-confidence-sweep/provenance.json').read_text())
        rng = np.random.default_rng(20261026)
        chosen = set(windows.loc[windows.city.eq('usnyc'), 'original_batch'])
        for city in ('ussfo', 'usbos'):
            population = windows.loc[windows.city.eq(city), 'original_batch'].unique()
            chosen.update(rng.choice(population, size=min(sample_batches, len(population)), replace=False).tolist())
        windows.loc[windows.original_batch.isin(chosen), 'selection'] = 'random_city_batches_or_all_nyc'
        for city, info in primary['cities'].items():
            changed = info['original_label_changes'][primary['runs'][0]]['changed_chips']
            extra = set(windows.loc[windows.city.eq(city) & windows.chip_id.isin(changed), 'original_batch'])
            chosen.update(extra)
            windows.loc[windows.original_batch.isin(extra), 'selection'] = 'changed_labels_batch'
        windows = windows[windows.original_batch.isin(chosen)].copy()
        provenance['sampling'] = 'Seed20261026; equal random original batch counts SF/Boston, all NYC, plus all changed-label batches. Descriptive enriched sample, not population estimate.'
    if limit:
        windows = windows.iloc[:limit]
    mean = torch.tensor(norms[0]['mean'], dtype=torch.float32)[:, None, None]
    std = torch.tensor(norms[0]['std'], dtype=torch.float32)[:, None, None]
    provenance['windows'] = len(windows)
    windows[['city','chip_id','original_batch','selection']].to_parquet(output/'windows.parquet', index=False)
    provenance['files'][str(source / 'vegetation-taxonomy.json')] = sha(source / 'vegetation-taxonomy.json')
    (output / 'provenance.json').write_text(json.dumps(provenance, indent=2))
    records, batch_metrics = [], []
    start = time.perf_counter()
    with torch.inference_mode():
        for index in range(0, len(windows), 2):
            batch = windows.iloc[index:index+2]
            samples = []
            for row in batch.itertuples():
                with np.load(row.file) as data:
                    samples.append({k: data[k].copy() for k in
                                    ('image', 'genus', 'species', 'genus_mask', 'species_mask')})
                provenance['files'][str(row.file)] = sha(row.file)
            images = torch.stack([(torch.from_numpy(s['image']).float()-mean)/std for s in samples])
            for label, model in models.items():
                prediction = model(images)
                for head, names in (('genus', taxonomy['genera']), ('species', taxonomy['species'])):
                    counts = [int(((s[head+'_mask'] > 0) & (s[head] >= 0)).sum()) for s in samples]
                    n = sum(counts)
                    total_loss = 0.0
                    for j, row in enumerate(batch.itertuples()):
                        s = samples[j]
                        mask = (s[head+'_mask'] > 0) & (s[head] >= 0)
                        y, x = np.where(mask)
                        target = torch.from_numpy(s[head][mask]).long()
                        logits = prediction[head+'_logits'][j, :, y, x].T.float()
                        assert logits.shape == (len(target), len(names))
                        assert torch.isfinite(logits).all()
                        lp = logits.log_softmax(-1)
                        ce = -lp[torch.arange(len(target)), target]
                        pred = logits.argmax(-1)
                        top5 = logits.topk(min(5, len(names)), -1).indices
                        correct5 = (top5 == target[:, None]).any(-1)
                        confidence = lp.exp().max(-1).values
                        entropy = -(lp.exp()*lp).sum(-1)
                        total_loss += ce.sum().item()
                        for k in range(len(target)):
                            records.append(dict(model=label, city=row.city, chip_id=row.chip_id,
                                batch=index//2, head=head, y=int(y[k]), x=int(x[k]),
                                target=int(target[k]), target_name=names[int(target[k])],
                                predicted=int(pred[k]), predicted_name=names[int(pred[k])],
                                loss=float(ce[k]), probability_true=float((-ce[k]).exp()),
                                confidence=float(confidence[k]), entropy=float(entropy[k]),
                                correct=bool(pred[k] == target[k]), top5=bool(correct5[k]),
                                objective_weight=len(batch)/len(windows)/n))
                    batch_metrics.append(dict(model=label, head=head, batch=index//2,
                        samples=len(batch), targets=n, loss=total_loss/n if n else 0.0))
                del prediction
            if index % 20 == 0 or index+2 >= len(windows):
                pd.DataFrame(records).to_parquet(output/'targets.parquet', index=False)
                pd.DataFrame(batch_metrics).to_parquet(output/'batches.parquet', index=False)
                print(f'{min(index+2,len(windows))}/{len(windows)} windows, {time.perf_counter()-start:.1f}s', flush=True)
    (output/'provenance.json').write_text(json.dumps(provenance, indent=2))
    (output/'COMPLETE').write_text('CPU fp32 frozen-validation taxonomy diagnosis; not original CUDA bf16 selection.\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--sample-batches', type=int, default=0)
    args = parser.parse_args()
    main(args.root, args.output, args.limit, args.threads, args.sample_batches)
