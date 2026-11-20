from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from urban_tree_ml.config import ProjectConfig, taxonomy_path
from urban_tree_ml.training_budget import training_budget, validate_resume_budget
from urban_tree_ml.checkpoint_selection import POLICY, sha256, validation_signature, select_checkpoint


def require_finite_losses(losses, stage):
    """Raise rather than allowing nonfinite early-stop to look like success."""
    import torch

    if not torch.stack([torch.isfinite(value).all() for value in losses.values()]).all():
        bad = [name for name, value in losses.items() if not torch.isfinite(value).all()]
        raise FloatingPointError(f"Nonfinite {stage} losses: {', '.join(bad)}")


def _git_metadata() -> dict[str, object]:
    repository = Path(__file__).resolve().parents[3]
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        return {"revision": revision, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"revision": None, "dirty": None}


def run_training(config: ProjectConfig, resume: str | None = None, *,
                 initial_checkpoint: str | None = None, parent_taxonomy: dict | None = None) -> dict[str, object]:
    if resume and initial_checkpoint:
        raise ValueError("Resume and weights-only warm start are mutually exclusive")
    base_path = config.paths.root/'run-inputs'/config.experiment/'base-start.json'
    base_recipe = json.loads(base_path.read_text()) if base_path.exists() else None
    cutoff = None
    if base_recipe or 'TREE_ML_TRAIN_UNTIL' in os.environ:
        from urban_tree_ml.capacity_run import training_deadline
        cutoff = training_deadline()
    if base_recipe:
        from urban_tree_ml.capacity_run import validate_base_recipe
        validate_base_recipe(base_recipe)
        if resume or initial_checkpoint or config.model.model_dump(mode='json') != base_recipe['model']:
            raise ValueError('Base experiment must start fresh with the frozen architecture')
    try:
        import lightning as lightning
        import torch
        from lightning.pytorch.callbacks import EarlyStopping, ModelCheckpoint
        from torch.utils.data import DataLoader
    except ImportError as error:  # pragma: no cover
        raise RuntimeError("Install the train dependency group: uv sync --group train") from error

    from urban_tree_ml.dataset import NpzChipDataset
    from urban_tree_ml.losses import multitask_loss
    from urban_tree_ml.model import RawImageryTreeModel

    selected_taxonomy_path = taxonomy_path(config)
    taxonomy = json.loads(selected_taxonomy_path.read_text(encoding="utf-8"))
    manifest_path = config.paths.root / "chips" / config.dataset / "chips.parquet"
    summary_path = manifest_path.parent / 'summary.json'
    if summary_path.exists() and json.loads(summary_path.read_text()).get('label_status') == 'unavailable':
        raise ValueError('Prediction-only imagery has no training labels; curate and rebuild first')
    from urban_tree_ml.fairfax_curation import verify_training_admission
    verify_training_admission(config, manifest_path)
    if config.imagery.acquisition_id is not None:
        from urban_tree_ml.temporal_training import verify_dataset
        verify_dataset(manifest_path.parent, config)
    train_dataset = NpzChipDataset(
        manifest_path,
        "train",
        random_dihedral=config.training.random_dihedral,
        augmentation=config.training.augmentation,
        seed=config.seed,
        require_confirmed_background_mask=config.training.confirmed_background_loss_weight > 1,
    )
    validation_dataset = NpzChipDataset(manifest_path, "validation",
        require_confirmed_background_mask=config.training.confirmed_background_loss_weight > 1)
    from urban_tree_ml.curated_validation import load_policy, verify_cohorts
    if load_policy(config.paths.root/'run-inputs'/config.experiment) is not None:
        rows = validation_dataset.rows
        if not {'city','source_chip_id'} <= set(rows.columns):
            raise ValueError('Curated validation requires explicit joint city/chip identities')
        verify_cohorts(config.paths.root, config.experiment,
                       {city:set(group.source_chip_id) for city,group in rows.groupby('city')})
    if config.model.crown_head:
        import numpy as np
        measured = False
        for row in train_dataset.rows.itertuples(index=False):
            with np.load(train_dataset.root / row.path) as chip:
                if "crown_mask" in chip and chip["crown_mask"].any():
                    measured = True
                    break
        if not measured:
            raise ValueError("Crown head requires crown labels in training chips; rebuild with measured overrides or allometric supervision")
    if not train_dataset or not validation_dataset:
        raise ValueError("training and validation chip splits must both be non-empty")
    budget = training_budget(config.training, len(train_dataset.rows), len(train_dataset))
    if config.training.accelerator == "gpu" and not torch.cuda.is_available():
        raise RuntimeError(
            "training.accelerator is 'gpu', but PyTorch cannot access CUDA; "
            "check the Lambda GPU/container configuration"
        )

    class TreeTask(lightning.LightningModule):
        def __init__(self) -> None:
            super().__init__()
            self.parent_baseline = None
            self.baseline_only = False
            self.validation_samples = 0
            self.save_hyperparameters(config.model_dump(mode="json"))
            self.network = RawImageryTreeModel(
                backbone=config.model.backbone,
                input_channels=config.model.input_channels,
                feature_channels=config.model.feature_channels,
                genus_classes=len(taxonomy["genera"]),
                species_classes=len(taxonomy["species"]),
                crown_head=config.model.crown_head,
                pretrained=config.model.pretrained and not initial_checkpoint,
            )

        def forward(self, image):
            return self.network(image)

        def _step(self, batch, stage: str):
            losses = multitask_loss(
                self(batch["image"]),
                batch,
                center_weight=config.training.center_loss_weight,
                dbh_weight=config.training.dbh_loss_weight,
                genus_weight=config.training.genus_loss_weight,
                species_weight=config.training.species_loss_weight,
                crown_weight=config.training.crown_loss_weight,
                single_tree_weight=config.training.single_tree_loss_weight,
                confirmed_background_weight=config.training.confirmed_background_loss_weight,
            )
            require_finite_losses(losses, stage)
            for name, value in losses.items():
                self.log(
                    f"{stage}/{name}",
                    value,
                    on_step=stage == "train",
                    on_epoch=True,
                    prog_bar=name == "loss",
                    batch_size=batch["image"].shape[0],
                )
            return losses["loss"]

        def training_step(self, batch, _batch_index):
            return self._step(batch, "train")

        def validation_step(self, batch, _batch_index):
            self.validation_samples += int(batch['image'].shape[0])
            self._step(batch, "validation")

        def on_validation_epoch_start(self):
            self.validation_samples = 0

        def on_save_checkpoint(self, checkpoint):
            checkpoint['training_budget'] = budget
            if self.parent_baseline is not None:
                checkpoint['parent_baseline'] = self.parent_baseline
            checkpoint['baseline_only'] = self.baseline_only

        def on_load_checkpoint(self, checkpoint):
            if checkpoint.get('baseline_only'):
                raise ValueError('Step-zero baseline is weights-only; use a warm start, not full-state resume')
            validate_resume_budget(checkpoint, budget)
            self.parent_baseline = checkpoint.get('parent_baseline')
            if self.parent_baseline is not None:
                if validation_signature(validation_dataset, config, taxonomy) != self.parent_baseline['validation_inputs']:
                    raise ValueError('Resume validation inputs/loss policy differ from the parent baseline')
                baseline_path = checkpoint_dir/self.parent_baseline['checkpoint_file']
                if sha256(baseline_path) != self.parent_baseline['checkpoint_sha256']:
                    raise ValueError('Resume parent baseline checkpoint hash mismatch')

        def configure_optimizers(self):
            optimizer = torch.optim.AdamW(
                self.parameters(),
                lr=config.training.learning_rate,
                weight_decay=config.training.weight_decay,
            )
            scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
                optimizer, T_max=budget['scheduler_t_max']
            )
            if config.training.warmup_source_passes:
                if budget['scheduler_interval'] != 'step':
                    raise ValueError('Warmup requires source-pass step scheduling')
                warmup = max(1, round(config.training.warmup_source_passes * budget['optimizer_steps_per_source_pass']))
                if warmup >= budget['scheduler_t_max']:
                    raise ValueError('Warmup must finish before the training cap')
                scheduler = torch.optim.lr_scheduler.SequentialLR(optimizer, schedulers=[
                    torch.optim.lr_scheduler.LinearLR(optimizer, start_factor=.1, total_iters=warmup),
                    torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=budget['scheduler_t_max']-warmup),
                ], milestones=[warmup])
            return {"optimizer": optimizer, "lr_scheduler": {
                'scheduler': scheduler, 'interval': budget['scheduler_interval'],
                'frequency': 1,
            }}

    lightning.seed_everything(config.seed, workers=True)
    train_loader = DataLoader(
        train_dataset,
        batch_size=config.training.batch_size,
        shuffle=True,
        num_workers=config.training.workers,
        pin_memory=True,
        persistent_workers=config.training.workers > 0,
    )
    validation_loader = DataLoader(
        validation_dataset,
        batch_size=config.training.batch_size,
        shuffle=False,
        num_workers=config.training.workers,
        pin_memory=True,
        persistent_workers=config.training.workers > 0,
    )
    run_dir = config.paths.root / "runs" / config.experiment
    run_dir.mkdir(parents=True, exist_ok=True)
    if train_dataset.fixed_pair:
        (run_dir / "augmentation-recipes.json").write_text(json.dumps({
            "version": config.training.augmentation, "seed": config.seed,
            "dataset": config.dataset, "source_windows": len(train_dataset.rows),
            "presentations_per_epoch": len(train_dataset),
            "fixed_across_epochs": True, "validation_augmented": False,
            "views": [train_dataset.augmentation_recipe(i) for i in range(len(train_dataset))],
        }, indent=2) + "\n", encoding="utf-8")
    metadata = {
        "created_at": datetime.now(UTC).isoformat(),
        "config": config.model_dump(mode="json"),
        "git": _git_metadata(),
        "inventory_summary": str(
            config.paths.root / "inventory" / config.inventory.city.lower() / "summary.json"
        ),
        "taxonomy": str(selected_taxonomy_path),
        "imagery_index": str(
            config.paths.root / "imagery" / config.inventory.city.lower() / "stac-items.json"
        ),
        "chip_manifest": str(manifest_path),
        "initial_checkpoint": initial_checkpoint,
        "initialization": base_recipe['mode'] if base_recipe else ("weights_only_fresh_optimizer" if initial_checkpoint else "default"),
        "training_source_windows": len(train_dataset.rows),
        "training_presentations_per_epoch": len(train_dataset),
        "training_budget": budget,
        "torch": {
            "version": torch.__version__,
            "cuda_version": torch.version.cuda,
            "cuda_available": torch.cuda.is_available(),
        },
    }
    (run_dir / "run-metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    checkpoint_dir = run_dir / "checkpoints"
    checkpoint = ModelCheckpoint(
        dirpath=checkpoint_dir,
        filename="{epoch:03d}-{step:07d}",
        monitor="validation/loss",
        mode="min",
        save_last=True,
        save_top_k=3,
        save_on_train_epoch_end=False,
        auto_insert_metric_name=False,
    )
    early_stopping = EarlyStopping(monitor="validation/loss", mode="min", min_delta=0.0,
                                  patience=config.training.early_stopping_patience_checks,
                                  check_on_train_epoch_end=False)
    additional_callbacks = []
    if cutoff is not None:
        from urban_tree_ml.capacity_run import curve_callback
        curves = curve_callback(run_dir, cutoff)
        additional_callbacks.append(curves)
    trainer = lightning.Trainer(
        accelerator=config.training.accelerator,
        devices=1,
        **budget['trainer'],
        precision=config.training.precision,
        accumulate_grad_batches=config.training.accumulate_grad_batches,
        default_root_dir=run_dir,
        callbacks=[
            checkpoint,
            early_stopping,
            *additional_callbacks,
        ],
        # Warm starts get a full explicit step-zero validation, not a two-batch sanity score.
        num_sanity_val_steps=0 if initial_checkpoint or base_recipe else 2,
        log_every_n_steps=10,
    )
    checkpoint_path: str | None = resume
    if resume == "auto":
        last = checkpoint_dir / "last.ckpt"
        checkpoint_path = str(last) if last.exists() else None
    task = TreeTask()
    if base_recipe:
        signature = validation_signature(validation_dataset, config, taxonomy)
        with torch.random.fork_rng(devices=list(range(torch.cuda.device_count()))
                                   if config.training.accelerator == 'gpu' else []):
            scores = trainer.validate(task, dataloaders=validation_loader, verbose=False)
        if len(scores) != 1 or task.validation_samples != len(validation_dataset):
            raise RuntimeError('Base initialization did not validate the full curated cohort')
        require_finite_losses({'loss': torch.tensor(scores[0]['validation/loss'])}, 'base initialization')
        (run_dir/'initialization-baseline.json').write_text(json.dumps(dict(
            initialization=base_recipe['mode'], metrics=scores[0], global_step=0,
            validation_inputs=signature, tree_checkpoint_loaded=False), indent=2)+'\n')
    if initial_checkpoint:
        parent_sha = sha256(initial_checkpoint)
        saved = torch.load(initial_checkpoint, map_location="cpu", weights_only=False)
        if parent_taxonomy is not None:
            from urban_tree_ml.vegetation_groups import migrate_group_head
            saved['state_dict'] = migrate_group_head(saved['state_dict'], task.state_dict(), parent_taxonomy, taxonomy)
        from urban_tree_ml.crown_size import validate_warm_start_keys
        validate_warm_start_keys(set(task.state_dict()), set(saved["state_dict"]))
        missing, _ = task.load_state_dict(saved["state_dict"], strict=False)
        # The vegetation recipe also passes the taxonomy for an already-expanded
        # parent. Identical 99-class continuation is not an architecture change.
        adapted = (parent_taxonomy is not None and parent_taxonomy != taxonomy) or bool(missing)
        del saved
        print(f"Loaded warm-start weights: {initial_checkpoint}; fresh optimizer", flush=True)
        validation_inputs = validation_signature(validation_dataset, config, taxonomy)
        # Validation must not consume training's random stream or optimizer budget.
        with torch.random.fork_rng(devices=list(range(torch.cuda.device_count()))
                                   if config.training.accelerator == 'gpu' else []):
            scores = trainer.validate(task, dataloaders=validation_loader, verbose=False)
        if len(scores) != 1 or task.validation_samples != len(validation_dataset):
            raise RuntimeError('Parent baseline did not validate the complete validation split')
        baseline_loss = float(scores[0]['validation/loss'])
        require_finite_losses({'validation/loss': torch.tensor(baseline_loss)}, 'parent baseline')
        if validation_signature(validation_dataset, config, taxonomy) != validation_inputs:
            raise ValueError('Validation inputs changed during parent baseline evaluation')
        if sha256(initial_checkpoint) != parent_sha:
            raise ValueError('Parent checkpoint changed during baseline evaluation')
        task.parent_baseline = {
            'policy': POLICY, 'validation_loss': baseline_loss, 'metrics': scores[0],
            'validation_inputs': validation_inputs, 'global_step': 0,
            'parent_checkpoint': str(initial_checkpoint), 'parent_checkpoint_sha256': parent_sha,
            'architecture_adapted': adapted, 'checkpoint_file': 'parent-baseline.ckpt',
        }
        # A local baseline copy makes collected runs self-contained. It is never
        # the final optimizer state and must not replace last.ckpt on resume.
        task.baseline_only = True
        baseline_path = checkpoint_dir/'parent-baseline.ckpt'
        trainer.save_checkpoint(baseline_path, weights_only=True)
        task.baseline_only = False
        task.parent_baseline['checkpoint_sha256'] = sha256(baseline_path)
        (run_dir/'parent-baseline.json').write_text(json.dumps(task.parent_baseline, indent=2)+'\n')
        early_stopping.best_score = torch.tensor(baseline_loss)
        early_stopping.wait_count = 0
        print(f'Full step-zero parent validation loss: {baseline_loss:.8f}; '
              f'{task.validation_samples} validation windows; parent is eligible to win', flush=True)
    trainer.fit(
        task,
        train_dataloaders=train_loader,
        val_dataloaders=validation_loader,
        ckpt_path=checkpoint_path,
    )
    require_finite_losses({"validation/loss": trainer.callback_metrics["validation/loss"]},
                          "final validation")
    if not checkpoint.best_model_path or not Path(checkpoint.best_model_path).is_file():
        raise RuntimeError("Training finished without a best checkpoint")
    # Always persist the true final state, even when the final epoch did not
    # enter the top-k or max_steps stopped between validation checks.
    final_checkpoint = checkpoint_dir / 'last.ckpt'
    trainer.save_checkpoint(final_checkpoint)
    result = {
        "run_dir": str(run_dir),
        "best_checkpoint": checkpoint.best_model_path,
        "last_checkpoint": str(final_checkpoint),
        "global_step": trainer.global_step,
        "training_budget": budget,
    }
    if cutoff is not None:
        result.update(stop_reason=('wall_clock_training_cutoff' if curves.deadline_reached else
                                  'early_stopping' if early_stopping.stopped_epoch else 'source_pass_cap'),
                      training_cutoff_epoch=cutoff)
    if base_recipe:
        if validation_signature(validation_dataset, config, taxonomy) != signature:
            raise ValueError('Base experiment validation changed during training')
        result.update(initialization=base_recipe['mode'],
                      stop_reason=('wall_clock_training_cutoff' if curves.deadline_reached else
                                   'early_stopping' if early_stopping.stopped_epoch else 'source_pass_cap'),
                      training_cutoff_epoch=cutoff, best_validation_loss=float(checkpoint.best_model_score))
        result['checkpoint_selection'] = dict(policy='imagenet_capacity_comparison_v1',
            selected='trained', eligible_for_parent_advance=False,
            best_checkpoint=str(checkpoint.best_model_path),
            checkpoint_sha256=sha256(checkpoint.best_model_path),
            reason='Architecture experiment; compare results before explicit promotion')
        (run_dir/'checkpoint-selection.json').write_text(json.dumps(result['checkpoint_selection'], indent=2)+'\n')
    if task.parent_baseline is not None:
        if validation_signature(validation_dataset, config, taxonomy) != task.parent_baseline['validation_inputs']:
            raise ValueError('Validation inputs changed during fine-tuning; cannot select a winner')
        selection = select_checkpoint(task.parent_baseline, checkpoint.best_model_path,
                                      checkpoint.best_model_score, checkpoint_dir)
        result['checkpoint_selection'] = selection
        result['best_checkpoint'] = selection['best_checkpoint']
        result['best_trained_checkpoint'] = checkpoint.best_model_path
        (run_dir/'checkpoint-selection.json').write_text(json.dumps(selection, indent=2)+'\n')
        print(f"Checkpoint selection: {selection['selected']}; "
              f"parent={selection['baseline_validation_loss']:.8f}, "
              f"trained={selection['trained_validation_loss']:.8f}", flush=True)
    elif checkpoint_path:
        # Historical full-state resumes have no same-input step-zero control.
        result['checkpoint_selection'] = {'policy': 'legacy_resume_unverified',
                                         'eligible_for_parent_advance': False}
    return result
