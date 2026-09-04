from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?}")


def _expand_environment(value: object) -> object:
    if isinstance(value, dict):
        return {key: _expand_environment(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_environment(item) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        raise ValueError(f"Required environment variable {name!r} is not set")

    return _ENV_PATTERN.sub(replace, value)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathsConfig(StrictModel):
    root: Path
    annotations: Path = Path("./annotations")


class InventoryConfig(StrictModel):
    city: str
    parquet_url: str
    min_dbh_in: float = Field(gt=0)
    max_dbh_in: float = Field(gt=0)
    excluded_species: list[str]
    min_species_examples: int = Field(ge=2)
    max_species_classes: int = Field(ge=2)

    @model_validator(mode="after")
    def dbh_range_is_ordered(self) -> InventoryConfig:
        if self.max_dbh_in <= self.min_dbh_in:
            raise ValueError("max_dbh_in must be greater than min_dbh_in")
        return self


class SplitConfig(StrictModel):
    projected_crs: str
    block_size_m: float = Field(gt=0)
    guard_m: float = Field(ge=0)
    train_fraction: float = Field(gt=0, lt=1)
    validation_fraction: float = Field(gt=0, lt=1)
    test_fraction: float = Field(gt=0, lt=1)

    @model_validator(mode="after")
    def fractions_sum_to_one(self) -> SplitConfig:
        total = self.train_fraction + self.validation_fraction + self.test_fraction
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"split fractions must sum to 1.0, got {total}")
        if self.guard_m * 2 >= self.block_size_m:
            raise ValueError("guard_m must be less than half of block_size_m")
        return self


class ImageryConfig(StrictModel):
    provider: Literal["planetary_computer", "local"]
    stac_url: str | None = None
    collection: str | None = None
    datetime: str | None = None
    asset_key: str | None = None
    bands: list[int]
    input_scale: float = Field(gt=0)
    resolution_m: float = Field(gt=0)
    chip_pixels: int = Field(ge=32)
    minimum_valid_fraction: float = Field(ge=0, le=1)
    local_raster: Path | None = None


class TargetsConfig(StrictModel):
    output_stride: Literal[2]
    gaussian_sigma_px: float = Field(gt=0)
    positive_supervision_radius_m: float = Field(gt=0)
    collision_policy: Literal["discard"]
    background_mode: Literal["ndvi_positive_unlabeled", "all"]
    background_ndvi_max: float = Field(ge=-1, le=1)


class ModelConfig(StrictModel):
    input_channels: int = Field(ge=3)
    backbone: Literal["resnet34"]
    feature_channels: int = Field(ge=32)
    pretrained: bool


class TrainingConfig(StrictModel):
    accelerator: Literal["auto", "cpu", "gpu"]
    batch_size: int = Field(ge=1)
    workers: int = Field(ge=0)
    epochs: int = Field(ge=1)
    learning_rate: float = Field(gt=0)
    weight_decay: float = Field(ge=0)
    precision: str
    accumulate_grad_batches: int = Field(ge=1)
    random_dihedral: bool = False
    center_loss_weight: float = Field(ge=0)
    dbh_loss_weight: float = Field(ge=0)
    genus_loss_weight: float = Field(ge=0)
    species_loss_weight: float = Field(ge=0)


class EvaluationConfig(StrictModel):
    match_radii_m: list[float]
    dbh_tolerance_in: float = Field(gt=0)
    confidence_threshold: float = Field(gt=0, lt=1)
    nms_kernel: int = Field(default=3, ge=3)
    max_detections_per_chip: int = Field(default=512, ge=1)

    @model_validator(mode="after")
    def nms_kernel_is_odd(self) -> EvaluationConfig:
        if self.nms_kernel % 2 == 0:
            raise ValueError("nms_kernel must be odd")
        return self


class ProjectConfig(StrictModel):
    experiment: str
    dataset: str
    seed: int
    paths: PathsConfig
    inventory: InventoryConfig
    split: SplitConfig
    imagery: ImageryConfig
    targets: TargetsConfig
    model: ModelConfig
    training: TrainingConfig
    evaluation: EvaluationConfig

    @model_validator(mode="after")
    def channels_match(self) -> ProjectConfig:
        if len(self.imagery.bands) != self.model.input_channels:
            raise ValueError(
                "model.input_channels must equal the number of configured imagery bands"
            )
        return self


def load_config(path: str | Path) -> ProjectConfig:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    expanded = _expand_environment(raw)
    config = ProjectConfig.model_validate(expanded)
    annotations_root = os.environ.get("TREE_ML_ANNOTATIONS_ROOT")
    if annotations_root:
        config.paths.annotations = Path(annotations_root)
    if not config.paths.root.is_absolute():
        config.paths.root = (config_path.parent.parent / config.paths.root).resolve()
    if not config.paths.annotations.is_absolute():
        config.paths.annotations = (
            config_path.parent.parent / config.paths.annotations
        ).resolve()
    if config.imagery.local_raster is not None and not config.imagery.local_raster.is_absolute():
        config.imagery.local_raster = (
            config_path.parent.parent / config.imagery.local_raster
        ).resolve()
    return config
