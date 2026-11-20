"""Inference-only NYC cohorts for the existing v7 model; no fitting or test scoring."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.evaluation import run_evaluation

PARENT = 'sf-boston-naip-swin-30cm-v7-finetune'
RUN = 'nyc-naip-swin-30cm-v7-cold-start-v1'
CHECKPOINT_SHA = 'b04d57130251dcbfffab4857e6db6aa98c16661785d145be92d2490f52218ee6'


def main(root, device):
    import torch
    torch.set_num_threads(4)
    target = root / 'run-inputs' / RUN
    checkpoint = root / 'runs' / PARENT / 'checkpoints/007.ckpt'
    with checkpoint.open('rb') as source:
        actual = hashlib.file_digest(source, 'sha256').hexdigest()
    if actual != CHECKPOINT_SHA:
        raise ValueError('Checkpoint hash differs from verified v7 model')
    results = []
    # Same training_run_id permits the existing All-cities view to combine NYC
    # with the v7 SF and Boston evaluations. No existing cohort is replaced.
    for filename, split, cohort in [
        ('config-central-park.json', 'train', 'train-usnyc-central-park'),
        ('config.json', 'validation', 'validation-usnyc'),
        ('config.json', 'train', 'train-usnyc')]:
        output = root / 'runs' / PARENT / 'evaluation' / cohort
        if output.exists():
            raise ValueError(f'Refusing to overwrite existing cohort {cohort}')
        config = ProjectConfig.model_validate(json.loads((target / filename).read_text()))
        config.training.batch_size = 1 if device == 'cpu' else 2
        started = time.monotonic()
        print(json.dumps(dict(stage='inference', cohort=cohort, device=device, checkpoint_sha256=actual)), flush=True)
        result = run_evaluation(config, checkpoint, split=split, cohort=cohort, device_name=device)
        print(json.dumps(dict(stage='complete', cohort=cohort, chips=result['chips'],
                             seconds=round(time.monotonic()-started,1))), flush=True)
        results.append(result)
        (target / 'inference-progress.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
    (target / 'COMPLETE.json').write_text(json.dumps(dict(checkpoint_sha256=actual, results=results), indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--device', choices=['cpu', 'cuda'], default='cpu')
    args = parser.parse_args()
    main(args.root.resolve(), args.device)
