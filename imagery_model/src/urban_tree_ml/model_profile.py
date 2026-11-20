"""Small artifact-derived model profiles; never load weights, pixels or live labels."""
import hashlib
import json
import math
import re
from pathlib import Path
from threading import Lock

VERSION = 3
_LOCK = Lock()
COMPONENTS = (
    ('center', 'Tree centers', 'Missing centers and false positives in supervised background.'),
    ('genus', 'Genus', 'Classification at eligible labeled locations; independent of the species head.'),
    ('species', 'Species', 'Classification at eligible labeled locations, including vegetation categories when present.'),
    ('dbh', 'Trunk diameter', 'Smooth L1 error in log diameter, where labels exist.'),
    ('crown', 'Crown size', 'Smooth L1 error in log crown size, weighted by label reliability.'),
    ('single_tree', 'Extra tree peaks', 'Additional local peaks inside explicit single-tree regions only. One peak per labeled-center partition is exempt; Copse/Shrub mass are excluded.'),
)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def finite(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def checkpoint_metrics(run_dir, result, baseline):
    selection = result.get('checkpoint_selection', {})
    if selection.get('selected') == 'parent':
        return baseline.get('metrics', {}), 0, 'parent-baseline.json'
    name = str(result.get('best_checkpoint', '')).replace('\\', '/').split('/')[-1]
    match = re.fullmatch(r'\d+-(\d+)\.ckpt', name)
    if not match:
        return {}, None, 'Unavailable: selected checkpoint step is not recorded'
    step = int(match[1])
    try:
        from tensorboard.backend.event_processing.event_accumulator import EventAccumulator
    except ImportError:
        return {}, step, 'Unavailable: TensorBoard reader is not installed'
    metrics = {}
    for path in sorted(run_dir.glob('lightning_logs/**/events.out.tfevents.*'), key=lambda p: p.stat().st_mtime_ns):
        accumulator = EventAccumulator(str(path), size_guidance={'scalars': 0}).Reload()
        for tag in accumulator.Tags().get('scalars', []):
            if tag.startswith('validation/'):
                for event in accumulator.Scalars(tag):
                    if event.step == step:
                        metrics[tag] = finite(event.value)
    return metrics, step, ('TensorBoard validation scalars at selected checkpoint step' if metrics else
                           'Unavailable: no validation scalars at selected checkpoint step')


def build_profile(root, run):
    root = Path(root)
    directory = root/'runs'/run
    metadata = read_json(directory/'run-metadata.json')
    result = read_json(directory/'training-result.json')
    baseline = read_json(directory/'parent-baseline.json')
    configs = [read_json(p) for p in sorted(directory.glob('config-*.json'))]
    config = metadata.get('config') or (configs[0] if configs else {})
    training = config.get('training', {})
    warm = read_json(root/'run-inputs'/run/'warm-start.json')
    metrics, step, source = checkpoint_metrics(directory, result, baseline)
    total = finite(metrics.get('validation/loss'))
    components = []
    for key, label, description in COMPONENTS:
        weight = finite(training.get(key+'_loss_weight'))
        tag = 'single_tree_peak_loss' if key == 'single_tree' else key+'_loss'
        raw = finite(metrics.get('validation/'+tag))
        contribution = weight*raw if weight is not None and raw is not None else None
        components.append(dict(key=key, label=label, description=description, weight=weight,
                               raw=raw, weighted=contribution,
                               share=contribution/total if contribution is not None and total else None))
    cohorts = []
    for path in sorted(directory.glob('evaluation/*/metrics.json')):
        values = read_json(path)
        if values.get('split') not in ('train', 'validation'):
            continue
        cohorts.append(dict(cohort=path.parent.name, split=values['split'],
                            chips=values.get('chips'), trees=values.get('ground_truth_trees')))
    taxonomy = read_json(directory/'evaluation/validation/taxonomy.json')
    return dict(schema_version=VERSION, run=run, complete=(directory/'COMPLETE').is_file(),
                architecture=config.get('model', {}), image_grid={
                    key: config.get('imagery', {}).get(key) for key in ('resolution_m', 'chip_pixels')}, taxonomy_counts={
                    key: len(taxonomy[key]) if key in taxonomy else None for key in ('genera','species')},
                training=training, training_budget=result.get('training_budget', metadata.get('training_budget')),
                parent=warm.get('checkpoint', metadata.get('initial_checkpoint')),
                parent_sha256=warm.get('checkpoint_sha256', baseline.get('parent_checkpoint_sha256')),
                selected_checkpoint=result.get('best_checkpoint'), selected_step=step,
                user_promotion=read_json(directory/'user-promotion.json') or None,
                selection=result.get('checkpoint_selection'), baseline_loss=baseline.get('validation_loss'),
                validation_inputs=baseline.get('validation_inputs'), loss_total=total,
                loss_source=source, loss_components=components, cohorts=cohorts,
                loss_policy=read_json(root/'run-inputs'/run/'loss-policy.json'),
                caveats=['Coefficients and loss-value shares are not gradient shares or importance percentages.',
                         'Aggregate validation loss is not F1/F2 and does not guarantee improvement in every city.',
                         'Extra-peak loss requires explicit single-tree regions; ordinary tree points alone do not activate it.',
                         'Missing historical fields are unknown, not today\'s defaults. No live annotations or test pixels are read.'])


def load_profile(root, run):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', run):
        raise ValueError('Invalid model run')
    root = Path(root)
    directory = root/'runs'/run
    if not directory.is_dir():
        raise ValueError('Unknown model run')
    paths = [directory/name for name in ('COMPLETE', 'run-metadata.json', 'training-result.json', 'parent-baseline.json', 'user-promotion.json')]
    paths += list(directory.glob('config-*.json')) + list(directory.glob('evaluation/*/metrics.json'))
    paths += list(directory.glob('lightning_logs/**/events.out.tfevents.*'))
    paths += [directory/'evaluation/validation/taxonomy.json']
    paths += [root/'run-inputs'/run/name for name in ('warm-start.json', 'loss-policy.json')]
    versions = [(str(p.relative_to(root)), p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(paths) if p.is_file()]
    signature = hashlib.sha256(json.dumps([VERSION, versions]).encode()).hexdigest()
    # Derived cache only; original run artifacts and historical configs stay immutable.
    cached = root/'qa/model-profiles'/f'{run}.json'
    with _LOCK:
        previous = read_json(cached)
        if previous.get('source_signature') == signature:
            return previous
        profile = build_profile(root, run)
        profile['source_signature'] = signature
        profile['sources'] = [v[0] for v in versions]
        cached.parent.mkdir(parents=True, exist_ok=True)
        temporary = cached.with_suffix('.tmp')
        temporary.write_text(json.dumps(profile, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        temporary.replace(cached)
        return profile
