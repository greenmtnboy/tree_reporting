"""Materialize explicitly unlabeled prediction viewports, including empty areas."""
import json
from pathlib import Path
import shutil

import numpy as np

from urban_tree_ml.mosaic_coverage import source_rectangles, valid_window
import pandas as pd


def build_prediction_chips(config, raster_path, windows, *, labels_template, window_splits=None):
    import rasterio
    from rasterio.windows import Window
    from urban_tree_ml.config import normalization_path
    from urban_tree_ml.targets import build_targets

    windows = sorted(set(windows))
    if window_splits is None:
        window_splits = {window: 'train' for window in windows}
    if set(window_splits) != set(windows) or any(
        split not in {'train', 'validation'} for split in window_splits.values()
    ):
        raise ValueError('Every prediction window needs a train or validation split')
    root = config.paths.root / 'chips' / config.dataset
    if (root / 'chips.parquet').exists():
        raise ValueError('Prediction chip dataset already exists; do not replace frozen inputs')
    root.mkdir(parents=True, exist_ok=True)
    records = []
    n = config.imagery.chip_pixels
    rectangles = source_rectangles(raster_path)
    with rasterio.open(raster_path) as source:
        for row, col in windows:
            if row < 0 or col < 0 or row + n > source.height or col + n > source.width:
                raise ValueError('Prediction viewport is outside the imagery')
            window = Window(col, row, n, n)
            image = source.read(config.imagery.bands, window=window).astype(np.float32) * config.imagery.input_scale
            valid = valid_window(source, config.imagery.bands, window, rectangles)
            if valid.mean() < config.imagery.minimum_valid_fraction:
                raise ValueError(f'Incomplete imagery for requested viewport {row}, {col}')
            targets = build_targets(image_height=n, image_width=n, labels=[], stride=config.targets.output_stride,
                                    gaussian_sigma_px=config.targets.gaussian_sigma_px,
                                    supervision_radius_px=config.targets.positive_supervision_radius_m / config.imagery.resolution_m,
                                    valid_mask=valid, background_mode='all')
            targets.pop('collision_cells')
            targets.pop('collision_excluded_points')
            targets['detection_mask'][:] = 0  # unknown, NOT confirmed negative
            targets['scoring_mask'][:] = 0
            chip_id = f'r{row // n:06d}_c{col // n:06d}'
            # "train" is the existing review route, not permission to fit these
            # images. summary.label_status makes training reject this dataset.
            split = window_splits[(row, col)]
            destination = root / split / (chip_id + '.npz')
            destination.parent.mkdir(exist_ok=True)
            np.savez_compressed(destination, image=image, **targets)
            records.append(dict(chip_id=chip_id, split=split, path=str(destination.relative_to(root)),
                                row_offset=row, column_offset=col, tree_count=0, candidate_tree_count=0,
                                valid_fraction=float(valid.mean()), label_status='unavailable'))
    if not records:
        raise ValueError('No prediction windows selected')
    pd.DataFrame(records).to_parquet(root / 'chips.parquet', index=False)
    pd.read_parquet(labels_template).iloc[:0].to_parquet(root / 'labels.parquet', index=False)
    shutil.copyfile(normalization_path(config), root / 'normalization.json')
    summary = dict(chips=len(records), label_status='unavailable', prediction_only=True,
                   source_raster=str(Path(raster_path).resolve()), trees=None,
                   manifest=str(root / 'chips.parquet'), purpose='inference and curation only')
    (root / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return summary
