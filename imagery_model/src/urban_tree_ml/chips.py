from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
from pyproj import Transformer

from urban_tree_ml.config import ProjectConfig, normalization_path
from urban_tree_ml.feedback import load_training_feedback
from urban_tree_ml.targets import (
    PointLabel,
    build_targets,
    find_collision_groups,
    point_label_output_cell,
)


def build_chips(
    config: ProjectConfig,
    raster_path: str | Path,
    *,
    feedback_path: str | Path | None = None,
    use_default_feedback: bool = True,
) -> dict[str, object]:
    try:
        import rasterio
        from rasterio.windows import Window
    except ImportError as error:  # pragma: no cover - exercised by CLI installations
        raise RuntimeError(
            "Install the imagery dependency group: uv sync --group imagery"
        ) from error

    inventory_path = (
        config.paths.root / "inventory" / config.inventory.city.lower() / "inventory.parquet"
    )
    frame = pd.read_parquet(inventory_path)
    label_columns = {"dbh_eligible", "genus_eligible", "species_eligible"}
    missing_label_columns = label_columns - set(frame.columns)
    if missing_label_columns:
        raise ValueError(
            "inventory uses the old shared-attribute label schema; rerun inventory export "
            f"(missing {sorted(missing_label_columns)})"
        )
    frame = frame[frame["split_eligible"]].copy()
    chip_pixels = config.imagery.chip_pixels
    output_root = config.paths.root / "chips" / config.dataset
    output_root.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, object]] = []
    skipped_mixed_split = 0
    skipped_invalid = 0
    collision_cells = 0
    collision_excluded_points = 0
    collision_records: list[dict[str, object]] = []
    label_records: list[dict[str, object]] = []
    channel_sum: np.ndarray | None = None
    channel_sum_squares: np.ndarray | None = None
    channel_pixel_count = 0
    source_path = Path(raster_path).resolve()
    selected_feedback_path = Path(feedback_path) if feedback_path is not None else None
    if selected_feedback_path is None and use_default_feedback:
        default_feedback = (
            config.paths.root
            / "qa"
            / "registration"
            / source_path.stem
            / "training-feedback.json"
        )
        if default_feedback.exists():
            selected_feedback_path = default_feedback
    feedback = (
        load_training_feedback(selected_feedback_path, source_path, config.dataset)
        if selected_feedback_path is not None
        else None
    )
    registration = feedback["registration"] if feedback is not None else {}
    correction_east_m = float(registration.get("east_m", 0.0))
    correction_north_m = float(registration.get("north_m", 0.0))
    exclusion_records = feedback["exclusions"] if feedback is not None else []
    excluded_tree_ids = {
        str(exclusion["tree_id"])
        for exclusion in exclusion_records
        if isinstance(exclusion, dict) and "tree_id" in exclusion
    }
    frame["feedback_excluded"] = frame["tree_id"].astype(str).isin(excluded_tree_ids)
    point_correction_records = feedback.get("point_corrections", []) if feedback is not None else []
    point_corrections = {
        (str(correction["tree_id"]), str(correction["split"])): (
            float(correction["east_m"]),
            float(correction["north_m"]),
        )
        for correction in point_correction_records
    }
    # Random reads across a large COG can otherwise let GDAL consume a substantial
    # fraction of host memory. A small cache is enough for 256-pixel windows and
    # keeps local preparation reliable on memory-constrained machines.
    with rasterio.Env(GDAL_CACHEMAX=128 * 1024 * 1024), rasterio.open(source_path) as source:
        if source.crs is None:
            raise ValueError("imagery raster must declare a CRS")
        if max(config.imagery.bands) > source.count:
            raise ValueError(
                f"configured band {max(config.imagery.bands)} exceeds raster count {source.count}"
            )
        transformer = Transformer.from_crs("EPSG:4326", source.crs, always_xy=True)
        source_xs, source_ys = transformer.transform(
            frame["longitude"].to_numpy(), frame["latitude"].to_numpy()
        )
        source_xs = np.asarray(source_xs)
        source_ys = np.asarray(source_ys)
        xs = source_xs + correction_east_m
        ys = source_ys + correction_north_m
        point_corrected = np.zeros(len(frame), dtype=bool)
        for position, (tree_id, split) in enumerate(
            zip(frame["tree_id"].astype(str), frame["split"].astype(str), strict=True)
        ):
            correction = point_corrections.get((tree_id, split))
            if correction is None:
                continue
            xs[position] = source_xs[position] + correction[0]
            ys[position] = source_ys[position] + correction[1]
            point_corrected[position] = True
        frame["feedback_point_corrected"] = point_corrected
        inverse = ~source.transform
        pixel_locations = [inverse * (x, y) for x, y in zip(xs, ys, strict=True)]
        frame["pixel_col"] = [location[0] for location in pixel_locations]
        frame["pixel_row"] = [location[1] for location in pixel_locations]
        frame = frame[
            (frame["pixel_col"] >= 0)
            & (frame["pixel_col"] < source.width)
            & (frame["pixel_row"] >= 0)
            & (frame["pixel_row"] < source.height)
        ].copy()
        frame["chip_col"] = (frame["pixel_col"] // chip_pixels).astype("int64")
        frame["chip_row"] = (frame["pixel_row"] // chip_pixels).astype("int64")

        for (chip_row, chip_col), group in frame.groupby(
            ["chip_row", "chip_col"], sort=True, observed=True
        ):
            positive_group = group[~group["feedback_excluded"]]
            if positive_group.empty:
                continue
            splits = positive_group["split"].unique()
            if len(splits) != 1:
                skipped_mixed_split += 1
                continue
            row_off = int(chip_row * chip_pixels)
            col_off = int(chip_col * chip_pixels)
            chip_id = f"r{chip_row:06d}_c{chip_col:06d}"
            if row_off + chip_pixels > source.height or col_off + chip_pixels > source.width:
                skipped_invalid += 1
                continue
            window = Window(col_off, row_off, chip_pixels, chip_pixels)
            raw = source.read(config.imagery.bands, window=window)
            masks = source.read_masks(config.imagery.bands, window=window)
            valid_mask = np.all(masks > 0, axis=0)
            valid_fraction = float(valid_mask.mean())
            if valid_fraction < config.imagery.minimum_valid_fraction:
                skipped_invalid += 1
                continue
            image = raw.astype(np.float32) * config.imagery.input_scale
            denominator = image[3] + image[0] if image.shape[0] >= 4 else None
            ndvi = (
                np.divide(
                    image[3] - image[0],
                    denominator,
                    out=np.zeros_like(image[0]),
                    where=np.abs(denominator) > 1e-6,
                )
                if denominator is not None
                else None
            )
            labels = [
                PointLabel(
                    x=float(row.pixel_col - col_off),
                    y=float(row.pixel_row - row_off),
                    dbh_log1p=float(row.dbh_log1p) if row.dbh_eligible else None,
                    genus_id=int(row.genus_id) if row.genus_eligible else None,
                    species_id=int(row.species_id) if row.species_eligible else None,
                )
                for row in positive_group.itertuples(index=False)
            ]
            collision_groups = find_collision_groups(
                labels,
                image_height=chip_pixels,
                image_width=chip_pixels,
                stride=config.targets.output_stride,
            )
            chip_collision_indices = {
                index for indices in collision_groups.values() for index in indices
            }
            for (output_x, output_y), indices in collision_groups.items():
                for index in indices:
                    row = positive_group.iloc[index]
                    collision_records.append(
                        {
                            "tree_id": str(row["tree_id"]),
                            "split": str(row["split"]),
                            "chip_id": chip_id,
                            "pixel_col": float(row["pixel_col"]),
                            "pixel_row": float(row["pixel_row"]),
                            "output_x": output_x,
                            "output_y": output_y,
                            "collision_size": len(indices),
                        }
                    )
            for index, label in enumerate(labels):
                if index in chip_collision_indices:
                    continue
                cell = point_label_output_cell(
                    label,
                    image_height=chip_pixels,
                    image_width=chip_pixels,
                    stride=config.targets.output_stride,
                )
                if cell is None:
                    continue
                row = positive_group.iloc[index]
                label_records.append(
                    {
                        "tree_id": str(row["tree_id"]),
                        "split": str(row["split"]),
                        "chip_id": chip_id,
                        "longitude": float(row["longitude"]),
                        "latitude": float(row["latitude"]),
                        "pixel_col": float(row["pixel_col"]),
                        "pixel_row": float(row["pixel_row"]),
                        "output_x": cell[0],
                        "output_y": cell[1],
                        "dbh_log1p": label.dbh_log1p,
                        "genus_id": label.genus_id,
                        "species_id": label.species_id,
                    }
                )
            ignored_locations = [
                (float(row.pixel_col - col_off), float(row.pixel_row - row_off))
                for row in group[group["feedback_excluded"]].itertuples(index=False)
            ]
            targets = build_targets(
                chip_pixels,
                chip_pixels,
                labels,
                stride=config.targets.output_stride,
                gaussian_sigma_px=config.targets.gaussian_sigma_px,
                supervision_radius_px=(
                    config.targets.positive_supervision_radius_m / config.imagery.resolution_m
                ),
                valid_mask=valid_mask,
                ndvi=ndvi,
                ignored_locations=ignored_locations,
                background_mode=config.targets.background_mode,
                background_ndvi_max=config.targets.background_ndvi_max,
                collision_policy=config.targets.collision_policy,
            )
            target_collision_cells = int(targets.pop("collision_cells"))
            target_collision_excluded = int(targets.pop("collision_excluded_points"))
            if target_collision_cells != len(collision_groups):
                raise RuntimeError("collision accounting differs from target generation")
            if target_collision_excluded != len(chip_collision_indices):
                raise RuntimeError("collision exclusion accounting differs from target generation")
            collision_cells += target_collision_cells
            collision_excluded_points += target_collision_excluded
            split = str(splits[0])
            if split == "train":
                if channel_sum is None:
                    channel_sum = np.zeros(image.shape[0], dtype=np.float64)
                    channel_sum_squares = np.zeros(image.shape[0], dtype=np.float64)
                valid_count = int(valid_mask.sum())
                for band_index in range(image.shape[0]):
                    valid_band = image[band_index, valid_mask].astype(np.float64)
                    channel_sum[band_index] += valid_band.sum()
                    channel_sum_squares[band_index] += np.square(valid_band).sum()
                channel_pixel_count += valid_count
            split_dir = output_root / split
            split_dir.mkdir(parents=True, exist_ok=True)
            chip_path = split_dir / f"{chip_id}.npz"
            temporary_chip = chip_path.with_name(f".{chip_path.name}.{uuid4().hex}.part")
            try:
                with temporary_chip.open("wb") as output:
                    np.savez_compressed(output, image=image, **targets)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary_chip, chip_path)
            finally:
                temporary_chip.unlink(missing_ok=True)
            records.append(
                {
                    "chip_id": chip_id,
                    "split": split,
                    "path": str(chip_path.relative_to(output_root)),
                    "candidate_tree_count": len(labels),
                    "tree_count": len(labels) - target_collision_excluded,
                    "collision_cell_count": target_collision_cells,
                    "collision_excluded_count": target_collision_excluded,
                    "feedback_ignored_count": len(ignored_locations),
                    "valid_fraction": valid_fraction,
                    "row_offset": row_off,
                    "column_offset": col_off,
                }
            )

    manifest_path = output_root / "chips.parquet"
    pd.DataFrame.from_records(records).to_parquet(manifest_path, index=False)
    collision_exclusions_path = output_root / "collision-exclusions.parquet"
    collision_columns = [
        "tree_id",
        "split",
        "chip_id",
        "pixel_col",
        "pixel_row",
        "output_x",
        "output_y",
        "collision_size",
    ]
    pd.DataFrame.from_records(collision_records, columns=collision_columns).to_parquet(
        collision_exclusions_path, index=False
    )
    labels_path = output_root / "labels.parquet"
    label_columns = [
        "tree_id",
        "split",
        "chip_id",
        "longitude",
        "latitude",
        "pixel_col",
        "pixel_row",
        "output_x",
        "output_y",
        "dbh_log1p",
        "genus_id",
        "species_id",
    ]
    pd.DataFrame.from_records(label_records, columns=label_columns).to_parquet(
        labels_path, index=False
    )
    if channel_sum is None or channel_sum_squares is None or channel_pixel_count == 0:
        raise ValueError(
            "no training pixels were materialized; check the split and raster coverage"
        )
    channel_mean = channel_sum / channel_pixel_count
    channel_variance = np.maximum(
        channel_sum_squares / channel_pixel_count - np.square(channel_mean), 1e-12
    )
    local_normalization = {
        "mean": channel_mean.tolist(),
        "std": np.sqrt(channel_variance).tolist(),
        "source": "training split pixels only",
    }
    normalization = local_normalization
    local_normalization_path: Path | None = None
    if config.reference is not None:
        reference_path = normalization_path(config)
        if not reference_path.exists():
            raise FileNotFoundError(f"reference normalization does not exist: {reference_path}")
        reference_normalization = json.loads(reference_path.read_text(encoding="utf-8"))
        mean = reference_normalization.get("mean")
        std = reference_normalization.get("std")
        if not isinstance(mean, list) or not isinstance(std, list):
            raise ValueError("reference normalization must contain mean and std lists")
        if len(mean) != len(config.imagery.bands) or len(std) != len(config.imagery.bands):
            raise ValueError(
                "reference normalization channel count does not match configured imagery bands"
            )
        if any(not np.isfinite(float(value)) for value in mean + std) or any(
            float(value) <= 0 for value in std
        ):
            raise ValueError("reference normalization values must be finite with positive std")
        local_normalization_path = output_root / "normalization-local.json"
        local_normalization_path.write_text(
            json.dumps(local_normalization, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        normalization = {
            "mean": [float(value) for value in mean],
            "std": [float(value) for value in std],
            "source": f"external reference: {reference_path.resolve()}",
        }
    (output_root / "normalization.json").write_text(
        json.dumps(normalization, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary: dict[str, object] = {
        "chips": len(records),
        "trees": sum(int(record["tree_count"]) for record in records),
        "candidate_trees": sum(int(record["candidate_tree_count"]) for record in records),
        "collision_policy": config.targets.collision_policy,
        "collision_cells": collision_cells,
        "collision_excluded_points": collision_excluded_points,
        "collision_exclusions": str(collision_exclusions_path),
        "labels": str(labels_path),
        "skipped_mixed_split": skipped_mixed_split,
        "skipped_invalid": skipped_invalid,
        "registration_feedback": str(selected_feedback_path) if feedback is not None else None,
        "registration_correction_m": {
            "east": correction_east_m,
            "north": correction_north_m,
        },
        "feedback_excluded_points": int(frame["feedback_excluded"].sum()),
        "feedback_point_corrected_points": int(frame["feedback_point_corrected"].sum()),
        "source_raster": str(source_path.resolve()),
        "manifest": str(manifest_path),
        "normalization": str(output_root / "normalization.json"),
        "local_normalization": (
            str(local_normalization_path) if local_normalization_path is not None else None
        ),
    }
    (output_root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary
