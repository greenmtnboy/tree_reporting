import pandas as pd
from pyproj import Transformer

from urban_tree_ml.config import SplitConfig
from urban_tree_ml.splits import Block, assign_spatial_splits, split_for_block


def _config() -> SplitConfig:
    return SplitConfig(
        projected_crs="EPSG:32610",
        block_size_m=768,
        guard_m=64,
        train_fraction=0.7,
        validation_fraction=0.15,
        test_fraction=0.15,
    )


def test_spatial_split_is_stable_and_guards_cross_split_edges() -> None:
    config = _config()
    seed = 20260820
    block = next(
        Block(x, 5420)
        for x in range(700, 900)
        if split_for_block(Block(x, 5420), config, seed)
        != split_for_block(Block(x + 1, 5420), config, seed)
    )
    # One point is near the differing right neighbor; one is in the block interior.
    projected = [
        (block.x * 768 + 767, block.y * 768 + 384),
        (block.x * 768 + 384, block.y * 768 + 384),
    ]
    inverse = Transformer.from_crs("EPSG:32610", "EPSG:4326", always_xy=True)
    longitude, latitude = inverse.transform(
        [point[0] for point in projected], [point[1] for point in projected]
    )
    frame = pd.DataFrame({"longitude": longitude, "latitude": latitude})

    first = assign_spatial_splits(frame, config, seed)
    second = assign_spatial_splits(frame, config, seed)

    assert first["split"].tolist() == second["split"].tolist()
    assert first["split_eligible"].tolist() == [False, True]
