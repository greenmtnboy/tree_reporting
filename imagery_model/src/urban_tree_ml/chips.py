from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer

from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.targets import PointLabel, build_targets


def build_chips(config: ProjectConfig, raster_path: str | Path) -> dict[str, object]:
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
    frame = frame[frame["split_eligible"]].copy()
    chip_pixels = config.imagery.chip_pixels
    output_root = config.paths.root / "chips" / config.experiment
    output_root.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, object]] = []
    skipped_mixed_split = 0
    skipped_invalid = 0
    collisions = 0
    channel_sum: np.ndarray | None = None
    channel_sum_squares: np.ndarray | None = None
    channel_pixel_count = 0
    source_path = Path(raster_path)
    with rasterio.open(source_path) as source:
        if source.crs is None:
            raise ValueError("imagery raster must declare a CRS")
        if max(config.imagery.bands) > source.count:
            raise ValueError(
                f"configured band {max(config.imagery.bands)} exceeds raster count {source.count}"
            )
        transformer = Transformer.from_crs("EPSG:4326", source.crs, always_xy=True)
        xs, ys = transformer.transform(frame["longitude"].to_numpy(), frame["latitude"].to_numpy())
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
            splits = group["split"].unique()
            if len(splits) != 1:
                skipped_mixed_split += 1
                continue
            row_off = int(chip_row * chip_pixels)
            col_off = int(chip_col * chip_pixels)
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
                    dbh_log1p=float(row.dbh_log1p),
                    genus_id=int(row.genus_id),
                    species_id=int(row.species_id),
                )
                for row in group.itertuples(index=False)
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
                background_mode=config.targets.background_mode,
                background_ndvi_max=config.targets.background_ndvi_max,
            )
            collisions += int(targets.pop("collisions"))
            split = str(splits[0])
            if split == "train":
                valid_pixels = image[:, valid_mask].astype(np.float64)
                if channel_sum is None:
                    channel_sum = np.zeros(image.shape[0], dtype=np.float64)
                    channel_sum_squares = np.zeros(image.shape[0], dtype=np.float64)
                channel_sum += valid_pixels.sum(axis=1)
                channel_sum_squares += np.square(valid_pixels).sum(axis=1)
                channel_pixel_count += valid_pixels.shape[1]
            split_dir = output_root / split
            split_dir.mkdir(parents=True, exist_ok=True)
            chip_id = f"r{chip_row:06d}_c{chip_col:06d}"
            chip_path = split_dir / f"{chip_id}.npz"
            np.savez_compressed(chip_path, image=image, **targets)
            records.append(
                {
                    "chip_id": chip_id,
                    "split": split,
                    "path": str(chip_path.relative_to(output_root)),
                    "tree_count": len(labels),
                    "valid_fraction": valid_fraction,
                    "row_offset": row_off,
                    "column_offset": col_off,
                }
            )

    manifest_path = output_root / "chips.parquet"
    pd.DataFrame.from_records(records).to_parquet(manifest_path, index=False)
    if channel_sum is None or channel_sum_squares is None or channel_pixel_count == 0:
        raise ValueError(
            "no training pixels were materialized; check the split and raster coverage"
        )
    channel_mean = channel_sum / channel_pixel_count
    channel_variance = np.maximum(
        channel_sum_squares / channel_pixel_count - np.square(channel_mean), 1e-12
    )
    normalization = {
        "mean": channel_mean.tolist(),
        "std": np.sqrt(channel_variance).tolist(),
        "source": "training split pixels only",
    }
    (output_root / "normalization.json").write_text(
        json.dumps(normalization, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary: dict[str, object] = {
        "chips": len(records),
        "trees": sum(int(record["tree_count"]) for record in records),
        "attribute_collisions": collisions,
        "skipped_mixed_split": skipped_mixed_split,
        "skipped_invalid": skipped_invalid,
        "source_raster": str(source_path.resolve()),
        "manifest": str(manifest_path),
        "normalization": str(output_root / "normalization.json"),
    }
    (output_root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary
