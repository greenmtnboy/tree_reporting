"""Frozen, explicit second-pass validation admission for future fine-tunes."""
import hashlib
import json
from pathlib import Path

import pandas as pd

POLICY = 'more_done_validation_v1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reviewed_ids(manifest, state):
    # A completed small crop cannot promote its surrounding full chip. Mixed
    # scene sample splits are allowed; the materialized chip split is decisive.
    return sorted({s['validation_chip_id'] for s in manifest['scenes']
                   if s.get('validation_chip_id') and 'validation' in s.get('splits', [])
                   and 'test' not in s.get('splits', [])
                   and state['scene_reviews'].get(s['scene_id'], {}).get('more_done') is True})


def freeze_policy(inputs, cities):
    inputs = Path(inputs)
    path = inputs/'validation-policy.json'
    if path.exists():
        raise ValueError('Validation policy already frozen')
    policy = dict(policy=POLICY, flag='more_done', test_evaluated=False, cities={})
    for city in cities:
        d = inputs/city
        manifest = json.loads((d/'manifest.json').read_text())
        state = json.loads((d/'reviews.json').read_text())
        ids = reviewed_ids(manifest, state)
        if not ids:
            raise ValueError(f'{city}: no full validation tiles marked more done')
        policy['cities'][city] = dict(reviewed_chip_ids=ids,
            hashes={name:sha(d/name) for name in ('manifest.json','reviews.json')})
    path.write_text(json.dumps(policy, indent=2)+'\n')
    return policy


def load_policy(inputs):
    inputs = Path(inputs)
    path = inputs/'validation-policy.json'
    if not path.exists():
        for name in ('warm-start.json', 'base-start.json'):
            warm = inputs/name
            if warm.exists() and json.loads(warm.read_text()).get('validation_policy'):
                raise ValueError('Required frozen validation policy missing')
        return None  # Legacy runs retain their historical cohort.
    policy = json.loads(path.read_text())
    if policy.get('policy') != POLICY or policy.get('flag') != 'more_done' or policy.get('test_evaluated') is not False:
        raise ValueError('Invalid curated validation policy')
    for city, item in policy['cities'].items():
        d = inputs/city
        if set(item['hashes']) != {'manifest.json','reviews.json'}:
            raise ValueError('Incomplete validation source hashes')
        for name, expected in item['hashes'].items():
            if sha(d/name) != expected:
                raise ValueError(f'{city}: validation curation source changed')
        ids = reviewed_ids(json.loads((d/'manifest.json').read_text()), json.loads((d/'reviews.json').read_text()))
        if not ids or ids != item['reviewed_chip_ids']:
            raise ValueError(f'{city}: validation admission differs from more_done flags')
    return policy


def restrict_sources(inputs, sources, run_dir):
    """Restrict newly built city manifests before joint training AND inference."""
    policy = load_policy(inputs)
    if policy is None:
        return None
    if set(sources) != set(policy['cities']):
        raise ValueError('Validation policy must cover every training city')
    if (Path(run_dir)/'COMPLETE').exists():
        raise ValueError('Completed runs are immutable')
    receipt = dict(policy=POLICY, policy_sha256=sha(Path(inputs)/'validation-policy.json'), cities={})
    pending = []
    for city,directory in sources.items():
        directory = Path(directory)
        frame = pd.read_parquet(directory/'chips.parquet')
        labels = pd.read_parquet(directory/'labels.parquet')
        eligible = set(frame.loc[frame.split.eq('validation'),'chip_id'])
        reviewed = set(policy['cities'][city]['reviewed_chip_ids'])
        admitted = eligible & reviewed
        if not admitted:
            raise ValueError(f'{city}: no materialized more_done validation tiles; no fallback')
        keep = ~frame.split.eq('validation') | frame.chip_id.isin(admitted)
        keep_labels = ~labels.split.eq('validation') | labels.chip_id.isin(admitted)
        pending.append((directory,frame[keep],labels[keep_labels]))
        receipt['cities'][city] = dict(chip_ids=sorted(admitted), chips=len(admitted),
            original_validation_chips=len(eligible), excluded_validation_chips=sorted(eligible-admitted),
            reviewed_outside_validation=sorted(reviewed-eligible),
            targets=int((labels.split.eq('validation') & labels.chip_id.isin(admitted)).sum()))
    for directory,frame,labels in pending:
        frame.to_parquet(directory/'chips.parquet',index=False)
        labels.to_parquet(directory/'labels.parquet',index=False)
    for city,directory in sources.items():
        receipt['cities'][city]['manifest_sha256'] = sha(Path(directory)/'chips.parquet')
    (Path(run_dir)/'curated-validation.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


def verify_cohorts(root, experiment, cohorts):
    """Fail closed before training/scoring if a city or admitted chip changes."""
    root = Path(root)
    inputs = root/'run-inputs'/experiment
    policy = load_policy(inputs)
    if policy is None:
        return None
    cohorts = dict(cohorts)
    temporal_path = inputs/'temporal-joint.json'
    if temporal_path.exists():
        from urban_tree_ml.temporal_training import verify_dataset
        from urban_tree_ml.config import ProjectConfig
        temporal = json.loads(temporal_path.read_text())
        for key in temporal['configs']:
            config = ProjectConfig.model_validate_json((root/'runs'/experiment/f'config-{key}.json').read_text())
            directory = root/'chips'/config.dataset
            verify_dataset(directory, config)
            admission = json.loads((inputs/key/'temporal-admission.json').read_text())
            expected = {chip for chip, split in admission['admitted'].items() if split == 'validation'}
            actual = set(cohorts.pop(key, set()))
            if actual != expected:
                raise ValueError(key+': temporal validation includes unreviewed or missing tiles')
    receipt = json.loads((root/'runs'/experiment/'curated-validation.json').read_text())
    if receipt['policy'] != POLICY or receipt['policy_sha256'] != sha(inputs/'validation-policy.json'):
        raise ValueError('Validation materialization receipt differs from frozen policy')
    if set(cohorts) != set(policy['cities']) or set(receipt['cities']) != set(cohorts):
        raise ValueError('Missing city in curated validation')
    for city,ids in cohorts.items():
        admitted = set(receipt['cities'][city]['chip_ids'])
        if not admitted or set(ids) != admitted or not admitted <= set(policy['cities'][city]['reviewed_chip_ids']):
            raise ValueError(f'{city}: validation includes unreviewed or missing tiles')
    return receipt
