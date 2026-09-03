from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PointLabel:
    x: float
    y: float
    dbh_log1p: float | None = None
    genus_id: int | None = None
    species_id: int | None = None


def _output_cell(
    label: PointLabel,
    *,
    image_height: int,
    image_width: int,
    stride: int,
) -> tuple[int, int] | None:
    if not (0 <= label.x < image_width and 0 <= label.y < image_height):
        return None
    output_height = image_height // stride
    output_width = image_width // stride
    x = min(int(round(label.x / stride)), output_width - 1)
    y = min(int(round(label.y / stride)), output_height - 1)
    return x, y


def find_collision_groups(
    labels: list[PointLabel],
    *,
    image_height: int,
    image_width: int,
    stride: int,
) -> dict[tuple[int, int], tuple[int, ...]]:
    """Return every in-bounds output cell occupied by multiple input labels."""
    grouped: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, label in enumerate(labels):
        cell = _output_cell(
            label,
            image_height=image_height,
            image_width=image_width,
            stride=stride,
        )
        if cell is not None:
            grouped[cell].append(index)
    return {
        cell: tuple(indices)
        for cell, indices in grouped.items()
        if len(indices) > 1
    }


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
    ignored_locations: list[tuple[float, float]] | None = None,
    background_mode: str = "ndvi_positive_unlabeled",
    background_ndvi_max: float = 0.05,
    collision_policy: str = "discard",
) -> dict[str, np.ndarray | int]:
    if collision_policy != "discard":
        raise ValueError(f"unknown collision policy: {collision_policy}")
    output_height = image_height // stride
    output_width = image_width // stride
    shape = (output_height, output_width)
    center = np.zeros(shape, dtype=np.float32)
    detection_mask = np.zeros(shape, dtype=np.float32)
    dbh_mask = np.zeros(shape, dtype=np.float32)
    genus_mask = np.zeros(shape, dtype=np.float32)
    species_mask = np.zeros(shape, dtype=np.float32)
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

    collision_groups = find_collision_groups(
        labels,
        image_height=image_height,
        image_width=image_width,
        stride=stride,
    )
    collision_indices = {
        index for indices in collision_groups.values() for index in indices
    }
    collision_locations = [
        (labels[index].x, labels[index].y) for index in sorted(collision_indices)
    ]
    radius = supervision_radius_px / stride
    for index, label in enumerate(labels):
        if index in collision_indices:
            continue
        cell = _output_cell(
            label,
            image_height=image_height,
            image_width=image_width,
            stride=stride,
        )
        if cell is None:
            continue
        x, y = cell
        _draw_gaussian(center, x, y, gaussian_sigma_px)
        grid_y, grid_x = np.ogrid[:output_height, :output_width]
        local = (grid_x - x) ** 2 + (grid_y - y) ** 2 <= radius**2
        detection_mask[np.logical_and(local, downsampled_valid)] = 1.0

        if label.dbh_log1p is not None:
            dbh_mask[y, x] = 1.0
            dbh[y, x] = label.dbh_log1p
        if label.genus_id is not None and label.genus_id >= 0:
            genus_mask[y, x] = 1.0
            genus[y, x] = label.genus_id
        if label.species_id is not None and label.species_id >= 0:
            species_mask[y, x] = 1.0
            species[y, x] = label.species_id

    for ignored_x, ignored_y in [*(ignored_locations or []), *collision_locations]:
        cell = _output_cell(
            PointLabel(x=ignored_x, y=ignored_y),
            image_height=image_height,
            image_width=image_width,
            stride=stride,
        )
        if cell is None:
            continue
        x, y = cell
        grid_y, grid_x = np.ogrid[:output_height, :output_width]
        ignored = (grid_x - x) ** 2 + (grid_y - y) ** 2 <= radius**2
        detection_mask[ignored] = 0.0

    # A retained positive always wins if its supervision neighborhood overlaps
    # a rejected/uncertain inventory point.
    detection_mask[center > 0] = 1.0
    return {
        "center": center,
        "detection_mask": detection_mask,
        "dbh_mask": dbh_mask,
        "genus_mask": genus_mask,
        "species_mask": species_mask,
        "dbh": dbh,
        "genus": genus,
        "species": species,
        "collision_cells": len(collision_groups),
        "collision_excluded_points": len(collision_indices),
    }
