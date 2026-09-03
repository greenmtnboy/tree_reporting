from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from urban_tree_ml.config import ProjectConfig


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


def run_training(config: ProjectConfig, resume: str | None = None) -> dict[str, object]:
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

    taxonomy_path = (
        config.paths.root / "inventory" / config.inventory.city.lower() / "taxonomy.json"
    )
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    manifest_path = config.paths.root / "chips" / config.dataset / "chips.parquet"
    train_dataset = NpzChipDataset(manifest_path, "train")
    validation_dataset = NpzChipDataset(manifest_path, "validation")
    if not train_dataset or not validation_dataset:
        raise ValueError("training and validation chip splits must both be non-empty")
    if config.training.accelerator == "gpu" and not torch.cuda.is_available():
        raise RuntimeError(
            "training.accelerator is 'gpu', but PyTorch cannot access CUDA; "
            "check the Lambda GPU/container configuration"
        )

    class TreeTask(lightning.LightningModule):
        def __init__(self) -> None:
            super().__init__()
            self.save_hyperparameters(config.model_dump(mode="json"))
            self.network = RawImageryTreeModel(
                input_channels=config.model.input_channels,
                feature_channels=config.model.feature_channels,
                genus_classes=len(taxonomy["genera"]),
                species_classes=len(taxonomy["species"]),
                pretrained=config.model.pretrained,
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
            )
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
            self._step(batch, "validation")

        def configure_optimizers(self):
            optimizer = torch.optim.AdamW(
                self.parameters(),
                lr=config.training.learning_rate,
                weight_decay=config.training.weight_decay,
            )
            scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
                optimizer, T_max=config.training.epochs
            )
            return {"optimizer": optimizer, "lr_scheduler": scheduler}

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
    metadata = {
        "created_at": datetime.now(UTC).isoformat(),
        "config": config.model_dump(mode="json"),
        "git": _git_metadata(),
        "inventory_summary": str(taxonomy_path.with_name("summary.json")),
        "imagery_index": str(
            config.paths.root / "imagery" / config.inventory.city.lower() / "stac-items.json"
        ),
        "chip_manifest": str(manifest_path),
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
        filename="{epoch:03d}",
        monitor="validation/loss",
        mode="min",
        save_last=True,
        save_top_k=3,
        auto_insert_metric_name=False,
    )
    trainer = lightning.Trainer(
        accelerator=config.training.accelerator,
        devices=1,
        max_epochs=config.training.epochs,
        precision=config.training.precision,
        accumulate_grad_batches=config.training.accumulate_grad_batches,
        default_root_dir=run_dir,
        callbacks=[
            checkpoint,
            EarlyStopping(monitor="validation/loss", mode="min", patience=12),
        ],
        log_every_n_steps=10,
    )
    checkpoint_path: str | None = resume
    if resume == "auto":
        last = checkpoint_dir / "last.ckpt"
        checkpoint_path = str(last) if last.exists() else None
    trainer.fit(
        TreeTask(),
        train_dataloaders=train_loader,
        val_dataloaders=validation_loader,
        ckpt_path=checkpoint_path,
    )
    return {
        "run_dir": str(run_dir),
        "best_checkpoint": checkpoint.best_model_path,
        "last_checkpoint": checkpoint.last_model_path,
    }
