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
    crown_radius_m: float | None = None
    crown_weight: float = 1.0


@dataclass(frozen=True)
class DetectionMaskRegion:
    """A manual circular override in input-image pixel coordinates."""

    x: float
    y: float
    radius: float
    mode: str


def point_label_output_cell(
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
        cell = point_label_output_cell(
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


def center_sigma(label, base_sigma, output_resolution_m, fraction=.3, max_sigma_m=3., estimated_scale=.5):
    """Broaden only; estimated radii produce a weaker adjustment than human labels."""
    if output_resolution_m <= 0:
        raise ValueError('Output resolution must be positive')
    radius = label.crown_radius_m
    if radius is None or not np.isfinite(radius) or radius <= 0:
        return base_sigma
    desired = max(base_sigma, min(fraction*radius, max_sigma_m)/output_resolution_m)
    strength = 1. if label.crown_weight >= 1 else estimated_scale
    return base_sigma + strength*(desired-base_sigma)


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
    mask_regions: list[DetectionMaskRegion] | None = None,
    background_mode: str = "ndvi_positive_unlabeled",
    background_ndvi_max: float = 0.05,
    collision_policy: str = "discard",
    crown_scaled_center: bool = False,
    resolution_m: float = 1.,
    crown_center_fraction: float = .3,
    crown_center_max_sigma_m: float = 3.,
    crown_estimated_scale: float = .5,
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
    crown = np.zeros(shape, dtype=np.float32)
    crown_mask = np.zeros(shape, dtype=np.float32)
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
        cell = point_label_output_cell(
            label,
            image_height=image_height,
            image_width=image_width,
            stride=stride,
        )
        if cell is None:
            continue
        x, y = cell
        sigma = center_sigma(label, gaussian_sigma_px, resolution_m*stride,
                             crown_center_fraction, crown_center_max_sigma_m,
                             crown_estimated_scale) if crown_scaled_center else gaussian_sigma_px
        _draw_gaussian(center, x, y, sigma)
        grid_y, grid_x = np.ogrid[:output_height, :output_width]
        local = (grid_x - x) ** 2 + (grid_y - y) ** 2 <= radius**2
        detection_mask[np.logical_and(local, downsampled_valid)] = 1.0

        if label.dbh_log1p is not None:
            dbh_mask[y, x] = 1.0
            dbh[y, x] = label.dbh_log1p
        if label.crown_radius_m is not None and np.isfinite(label.crown_radius_m) and label.crown_radius_m > 0:
            crown_mask[y, x] = label.crown_weight
            crown[y, x] = np.log1p(label.crown_radius_m)
        if label.genus_id is not None and label.genus_id >= 0:
            genus_mask[y, x] = 1.0
            genus[y, x] = label.genus_id
        if label.species_id is not None and label.species_id >= 0:
            species_mask[y, x] = 1.0
            species[y, x] = label.species_id

    for ignored_x, ignored_y in [*(ignored_locations or []), *collision_locations]:
        cell = point_label_output_cell(
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

    # Apply manual regions in creation order so a later, more specific annotation
    # can replace an earlier one. Regions only alter center supervision; they do
    # not manufacture or remove inventory labels.
    grid_y, grid_x = np.ogrid[:output_height, :output_width]
    for region in mask_regions or []:
        if region.mode not in {"protect", "confirmed-background"}:
            raise ValueError(f"unknown detection mask region mode: {region.mode}")
        if region.radius <= 0:
            raise ValueError("detection mask region radius must be positive")
        output_x = region.x / stride
        output_y = region.y / stride
        output_radius = region.radius / stride
        local = (
            (grid_x - output_x) ** 2 + (grid_y - output_y) ** 2
            <= output_radius**2
        )
        local = np.logical_and(local, downsampled_valid)
        detection_mask[local] = 0.0 if region.mode == "protect" else 1.0

    # A retained positive always wins if its center overlaps an exclusion or a
    # manual background region. Invalid background annotations therefore cannot
    # silently erase known trees.
    detection_mask[center > 0] = 1.0
    return {
        "center": center,
        "detection_mask": detection_mask,
        "dbh_mask": dbh_mask,
        "genus_mask": genus_mask,
        "species_mask": species_mask,
        "dbh": dbh,
        "crown": crown,
        "crown_mask": crown_mask,
        "genus": genus,
        "species": species,
        "collision_cells": len(collision_groups),
        "collision_excluded_points": len(collision_indices),
    }
