from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PointLabel:
    x: float
    y: float
    dbh_log1p: float
    genus_id: int
    species_id: int


def _downsample_mean(array: np.ndarray, stride: int) -> np.ndarray:
    height = array.shape[0] // stride * stride
    width = array.shape[1] // stride * stride
    cropped = array[:height, :width]
    return cropped.reshape(height // stride, stride, width // stride, stride).mean(axis=(1, 3))


def _draw_gaussian(heatmap: np.ndarray, x: int, y: int, sigma: float) -> None:
    radius = max(1, int(np.ceil(3 * sigma)))
    x0, x1 = max(0, x - radius), min(heatmap.shape[1], x + radius + 1)
    y0, y1 = max(0, y - radius), min(heatmap.shape[0], y + radius + 1)
    grid_y, grid_x = np.mgrid[y0:y1, x0:x1]
    gaussian = np.exp(-((grid_x - x) ** 2 + (grid_y - y) ** 2) / (2 * sigma**2))
    heatmap[y0:y1, x0:x1] = np.maximum(heatmap[y0:y1, x0:x1], gaussian)


def build_targets(
    image_height: int,
    image_width: int,
    labels: list[PointLabel],
    *,
    stride: int,
    gaussian_sigma_px: float,
    supervision_radius_px: float,
    valid_mask: np.ndarray | None = None,
    ndvi: np.ndarray | None = None,
    background_mode: str = "ndvi_positive_unlabeled",
    background_ndvi_max: float = 0.05,
) -> dict[str, np.ndarray | int]:
    output_height = image_height // stride
    output_width = image_width // stride
    shape = (output_height, output_width)
    center = np.zeros(shape, dtype=np.float32)
    detection_mask = np.zeros(shape, dtype=np.float32)
    attribute_mask = np.zeros(shape, dtype=np.float32)
    dbh = np.zeros(shape, dtype=np.float32)
    genus = np.full(shape, -1, dtype=np.int64)
    species = np.full(shape, -1, dtype=np.int64)

    if valid_mask is None:
        downsampled_valid = np.ones(shape, dtype=bool)
    else:
        downsampled_valid = _downsample_mean(valid_mask.astype(np.float32), stride) >= 0.999

    if background_mode == "all":
        detection_mask[downsampled_valid] = 1.0
    elif background_mode == "ndvi_positive_unlabeled":
        if ndvi is None:
            raise ValueError("ndvi is required for ndvi_positive_unlabeled background mode")
        low_vegetation = _downsample_mean(ndvi.astype(np.float32), stride) <= background_ndvi_max
        detection_mask[np.logical_and(low_vegetation, downsampled_valid)] = 1.0
    else:
        raise ValueError(f"unknown background mode: {background_mode}")

    collisions = 0
    radius = supervision_radius_px / stride
    for label in labels:
        x = int(round(label.x / stride))
        y = int(round(label.y / stride))
        if not (0 <= x < output_width and 0 <= y < output_height):
            continue
        _draw_gaussian(center, x, y, gaussian_sigma_px)
        grid_y, grid_x = np.ogrid[:output_height, :output_width]
        local = (grid_x - x) ** 2 + (grid_y - y) ** 2 <= radius**2
        detection_mask[np.logical_and(local, downsampled_valid)] = 1.0

        if attribute_mask[y, x] > 0:
            collisions += 1
            # The larger stem is a more plausible dominant crown in a collision.
            if label.dbh_log1p <= dbh[y, x]:
                continue
        attribute_mask[y, x] = 1.0
        dbh[y, x] = label.dbh_log1p
        genus[y, x] = label.genus_id
        species[y, x] = label.species_id

    detection_mask[center > 0] = 1.0
    return {
        "center": center,
        "detection_mask": detection_mask,
        "attribute_mask": attribute_mask,
        "dbh": dbh,
        "genus": genus,
        "species": species,
        "collisions": collisions,
    }
