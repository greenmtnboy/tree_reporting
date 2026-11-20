"""Work-normalized budgets, independent of the number of augmented views."""
from math import ceil


def training_budget(training, source_windows: int, presentations: int) -> dict:
    if source_windows < 1 or presentations < source_windows:
        raise ValueError('Training budget requires non-empty source windows and presentations')
    batches = ceil(source_windows / training.batch_size)
    steps = ceil(batches / training.accumulate_grad_batches)
    legacy = training.budget_policy == 'legacy_epochs'
    return {
        'policy': training.budget_policy,
        'source_windows': source_windows,
        'presentations_per_expanded_epoch': presentations,
        'source_batches_per_pass': batches,
        'optimizer_steps_per_source_pass': steps,
        'patience_validation_checks': training.early_stopping_patience_checks,
        'scheduler_interval': 'epoch' if legacy else 'step',
        'scheduler_t_max': training.epochs if legacy else training.epochs * steps,
        'trainer': ({'max_epochs': training.epochs} if legacy else {
            'max_epochs': -1,
            'max_steps': training.epochs * steps,
            # Count training batches continuously across expanded epoch boundaries.
            # Final partial batches/accumulations can cause one-step cadence jitter.
            'val_check_interval': batches,
            'check_val_every_n_epoch': None,
        }),
    }


def validate_resume_budget(saved: dict, current: dict) -> None:
    """Never silently restore an epoch scheduler into a step-budgeted trainer."""
    previous = saved.get('training_budget')
    if previous is None:
        if current['policy'] != 'legacy_epochs':
            raise ValueError('Legacy checkpoint resume requires budget_policy=legacy_epochs; '
                             'use a weights-only warm start for the new stopping budget')
    elif previous != current:
        raise ValueError('Resume training budget differs from checkpoint; '
                         'restore the original budget or use a weights-only warm start')
