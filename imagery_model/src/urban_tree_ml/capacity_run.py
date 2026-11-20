"""Explicit ImageNet-start experiments with bounded optimization and learning curves."""
import json
import math
import os
import time


def validate_base_recipe(recipe):
    if recipe.get('mode') != 'imagenet_fresh_heads_fresh_optimizer_v1':
        raise ValueError('Unknown base initialization mode')
    if 'checkpoint' in recipe or 'checkpoint_sha256' in recipe:
        raise ValueError('Base experiment must not reference tree-model weights')
    if recipe['model']['backbone'] not in ('swin_tiny', 'swin_small') or recipe['model']['pretrained'] is not True:
        raise ValueError('Base experiment requires pretrained Tiny or Small')
    if recipe.get('validation_policy') != 'more_done_validation_v1':
        raise ValueError('Base experiment requires explicit more_done validation')


def training_deadline():
    deadline = float(os.environ['TREE_ML_TRAIN_UNTIL'])
    if not math.isfinite(deadline) or deadline <= time.time():
        raise ValueError('Missing/expired training cutoff; cannot start optimization')
    return deadline


def curve_callback(run_dir, deadline):
    import lightning as pl

    class CurvesAndDeadline(pl.Callback):
        def __init__(self):
            self.deadline_reached = False

        def write(self, trainer, stage):
            metrics = {}
            for key, value in trainer.callback_metrics.items():
                try:
                    metrics[key] = float(value)
                except (TypeError, ValueError):
                    continue
            row = dict(at=time.time(), stage=stage, step=trainer.global_step,
                       epoch=trainer.current_epoch, metrics=metrics)
            if trainer.optimizers:
                row['learning_rate'] = trainer.optimizers[0].param_groups[0]['lr']
            with (run_dir/'learning-curves.jsonl').open('a') as handle:
                handle.write(json.dumps(row)+'\n')
                handle.flush()

        def on_validation_end(self, trainer, pl_module):
            if not trainer.sanity_checking:
                self.write(trainer, 'validation')

        def on_train_batch_end(self, trainer, pl_module, outputs, batch, batch_idx):
            if batch_idx % 200 == 0:
                self.write(trainer, 'train')
            if time.time() >= deadline:
                self.deadline_reached = True
                trainer.should_stop = True

    return CurvesAndDeadline()
