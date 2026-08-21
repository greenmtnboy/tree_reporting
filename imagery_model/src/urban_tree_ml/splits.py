from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from pyproj import Transformer

from urban_tree_ml.config import SplitConfig


@dataclass(frozen=True)
class Block:
    x: int
    y: int


def _block_score(block: Block, seed: int) -> float:
    digest = hashlib.blake2b(f"{seed}:{block.x}:{block.y}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / float(2**64)


def split_for_block(block: Block, config: SplitConfig, seed: int) -> str:
    score = _block_score(block, seed)
    if score < config.train_fraction:
        return "train"
    if score < config.train_fraction + config.validation_fraction:
        return "validation"
    return "test"


def _is_guarded(
    x: float,
    y: float,
    block: Block,
    split: str,
    config: SplitConfig,
    seed: int,
) -> bool:
    size = config.block_size_m
    local_x = x - block.x * size
    local_y = y - block.y * size
    neighbors = (
        (local_x, Block(block.x - 1, block.y)),
        (size - local_x, Block(block.x + 1, block.y)),
        (local_y, Block(block.x, block.y - 1)),
        (size - local_y, Block(block.x, block.y + 1)),
    )
    return any(
        distance < config.guard_m and split_for_block(neighbor, config, seed) != split
        for distance, neighbor in neighbors
    )


def assign_spatial_splits(
    frame: pd.DataFrame,
    config: SplitConfig,
    seed: int,
) -> pd.DataFrame:
    """Assign stable projected blocks and remove pixels near cross-split edges."""
    required = {"longitude", "latitude"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing coordinate columns: {sorted(missing)}")

    transformer = Transformer.from_crs("EPSG:4326", config.projected_crs, always_xy=True)
    x_values, y_values = transformer.transform(
        frame["longitude"].to_numpy(), frame["latitude"].to_numpy()
    )
    blocks = [
        Block(math.floor(x / config.block_size_m), math.floor(y / config.block_size_m))
        for x, y in zip(x_values, y_values, strict=True)
    ]
    splits = [split_for_block(block, config, seed) for block in blocks]
    guarded = [
        _is_guarded(x, y, block, split, config, seed)
        for x, y, block, split in zip(x_values, y_values, blocks, splits, strict=True)
    ]

    result = frame.copy()
    result["projected_x"] = np.asarray(x_values)
    result["projected_y"] = np.asarray(y_values)
    result["split_block_x"] = [block.x for block in blocks]
    result["split_block_y"] = [block.y for block in blocks]
    result["split"] = splits
    result["split_eligible"] = np.logical_not(guarded)
    return result
