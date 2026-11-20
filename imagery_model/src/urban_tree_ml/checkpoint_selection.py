"""Auditable parent-versus-descendant selection, independent of Lightning."""
import hashlib
import json
import math
from pathlib import Path

POLICY = 'parent_baseline_v1'


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validation_signature(dataset, config, taxonomy):
    """Hash only development validation inputs, including masks inside each NPZ."""
    root = Path(dataset.root)
    files = [('chips.parquet', sha256(root/'chips.parquet')),
             ('normalization.json', sha256(root/'normalization.json'))]
    from urban_tree_ml.curated_validation import load_policy
    admission_path = config.paths.root/'run-inputs'/config.experiment/'validation-policy.json'
    admission = load_policy(admission_path.parent)
    if admission is not None:
        files.append(('validation-policy.json', sha256(admission_path)))
    for row in dataset.rows.itertuples(index=False):
        files.append((str(row.path), sha256(root/row.path)))
    training = config.training.model_dump(mode='json')
    policy = {k: v for k, v in training.items() if k.endswith('_loss_weight')}
    policy.update(precision=config.training.precision, batch_size=config.training.batch_size,
                  model=config.model.model_dump(mode='json'), taxonomy=taxonomy)
    digest = hashlib.sha256(json.dumps({'files': files, 'policy': policy},
                            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    result = {'sha256': digest, 'windows': len(dataset), 'loss_policy': policy}
    if admission is not None:
        result['admission_policy'] = admission['policy']
        result['admission_policy_sha256'] = sha256(admission_path)
    return result


def select_checkpoint(baseline, candidate_path, candidate_loss, checkpoint_dir):
    parent_loss = float(baseline['validation_loss'])
    candidate_loss = float(candidate_loss)
    if not math.isfinite(parent_loss) or not math.isfinite(candidate_loss):
        raise FloatingPointError('Checkpoint selection requires finite baseline and candidate losses')
    parent_path = Path(checkpoint_dir)/baseline['checkpoint_file']
    if sha256(parent_path) != baseline['checkpoint_sha256']:
        raise ValueError('Parent baseline checkpoint hash mismatch')
    if not Path(candidate_path).is_file():
        raise FileNotFoundError('Best trained checkpoint is missing')
    improved = candidate_loss < parent_loss  # ties retain the parent
    winner = str(candidate_path) if improved else str(parent_path)
    return {
        'policy': POLICY, 'monitor': 'validation/loss', 'mode': 'min', 'min_delta': 0.0,
        'selected': 'trained' if improved else 'parent',
        'baseline_validation_loss': parent_loss, 'trained_validation_loss': candidate_loss,
        'selected_validation_loss': min(parent_loss, candidate_loss),
        'best_checkpoint': winner, 'selected_checkpoint_sha256': sha256(winner),
        'best_trained_checkpoint': str(candidate_path),
        'parent_checkpoint': baseline['parent_checkpoint'],
        'parent_checkpoint_sha256': baseline['parent_checkpoint_sha256'],
        'validation_inputs': baseline['validation_inputs'],
        'architecture_adapted': baseline['architecture_adapted'],
        # An expanded/new head is compared to its initialized adaptation, not the
        # old architecture. Require explicit review before changing that lineage.
        'eligible_for_parent_advance': improved and not baseline['architecture_adapted'],
    }


def require_parent_advance(result):
    """A completed no-improvement run is not an automatically eligible parent."""
    selection = result.get('checkpoint_selection')
    if selection is not None and selection.get('eligible_for_parent_advance') is not True:
        raise ValueError('Run is not eligible for automatic parent advancement; retain its '
                         'previous parent (or explicitly review an architecture change)')


def resolve_parent_checkpoint(run_dir):
    """Honor a hash-bound explicit user promotion without rewriting training history."""
    run_dir = Path(run_dir)
    result_path = run_dir / 'training-result.json'
    result = json.loads(result_path.read_text())
    if not (run_dir / 'COMPLETE').is_file():
        raise ValueError('Warm-start source is not complete')
    promotion_path = run_dir / 'user-promotion.json'
    if promotion_path.exists():
        promotion = json.loads(promotion_path.read_text())
        if (promotion.get('policy') != 'explicit_user_checkpoint_promotion_v1'
                or promotion.get('run') != run_dir.name
                or promotion.get('training_result_sha256') != sha256(result_path)
                or not promotion.get('authorization') or not promotion.get('reason')):
            raise ValueError('Invalid explicit promotion provenance')
        name = promotion.get('checkpoint', '')
        if not name or Path(name).name != name or '/' in name or '\\' in name:
            raise ValueError('Promotion checkpoint must be a filename')
        checkpoint = run_dir / 'checkpoints' / name
        if sha256(checkpoint) != promotion.get('checkpoint_sha256'):
            raise ValueError('Promoted checkpoint hash mismatch')
        return checkpoint
    require_parent_advance(result)
    return run_dir / 'checkpoints' / Path(result['best_checkpoint']).name
