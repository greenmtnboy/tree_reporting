from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
from pyproj import Transformer

from urban_tree_ml.config import ProjectConfig, normalization_path
from urban_tree_ml.confirmed_trees import confirmed_tree_rows
from urban_tree_ml.feedback import load_training_feedback
from urban_tree_ml.inventory import planted_after_imagery
from urban_tree_ml.imagery_dates import resolve_planting_cutoff
from urban_tree_ml.prepared_image_cache import PreparedImageCache
from urban_tree_ml.targets import (
    DetectionMaskRegion,
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
    output_dataset: str | None = None,
    inventory_source: Path | None = None,
    feedback_source_raster: Path | None = None,
    boundary_shift_recipes: Path | None = None,
    allowed_chip_ids: set[str] | None = None,
    allowed_splits: set[str] | None = None,
    validation_supervision_only: bool = False,
    validation_full_targets: bool = False,
    source_supervision_only: bool = False,
) -> dict[str, object]:
    temporal_splits = None
    if config.imagery.acquisition_id is not None:
        from urban_tree_ml.temporal_training import build_admission
        temporal_splits = build_admission(config, raster_path, feedback_path, inventory_source)
        if (boundary_shift_recipes or validation_supervision_only or source_supervision_only
                or allowed_chip_ids is not None and allowed_chip_ids != set(temporal_splits)
                or allowed_splits is not None and allowed_splits != set(temporal_splits.values())):
            raise ValueError('Temporal training requires the complete frozen curated cohort')
        allowed_chip_ids = set(temporal_splits)
        allowed_splits = set(temporal_splits.values())
    fairfax_splits = None
    if config.inventory.city.lower() == 'usffx' and temporal_splits is None:
        from urban_tree_ml.fairfax_curation import build_admission
        if (config.paths.root / 'chips' / (output_dataset or config.dataset)).exists():
            raise ValueError('Fairfax curated dataset already exists; use a fresh destination')
        fairfax_splits = build_admission(config, raster_path, feedback_path, inventory_source)
        if (boundary_shift_recipes or validation_supervision_only or source_supervision_only
                or allowed_chip_ids is not None and allowed_chip_ids != set(fairfax_splits)
                or allowed_splits is not None and allowed_splits != {'train', 'validation'}):
            raise ValueError('Fairfax requires the complete frozen curated development cohort')
        allowed_chip_ids, allowed_splits = set(fairfax_splits), {'train', 'validation'}
    if validation_full_targets and not validation_supervision_only:
        raise ValueError('Full validation targets require explicit pinned validation supervision')
    if source_supervision_only and (
        feedback_path is None or allowed_splits != {'train'}
        or validation_supervision_only or boundary_shift_recipes is not None
    ):
        raise ValueError('Source supervision resolution requires explicit feedback and train only')
    if validation_supervision_only and (
        allowed_splits != {'validation'} or not allowed_chip_ids
        or config.imagery.boundary_shifts or boundary_shift_recipes is not None
    ):
        raise ValueError('Validation supervision export requires explicit validation chips and no shifts')
    try:
        import rasterio
        from rasterio.windows import Window
    except ImportError as error:  # pragma: no cover - exercised by CLI installations
        raise RuntimeError(
            "Install the imagery dependency group: uv sync --group imagery"
        ) from error

    inventory_path = inventory_source or (
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
    if source_supervision_only:
        frame = frame[frame['split'].eq('train')].copy()
    if validation_supervision_only:
        frame = frame[frame['split'].eq('validation')].copy()
    acquisition = resolve_planting_cutoff(config.imagery, raster_path)
    frame["planted_after_imagery"] = planted_after_imagery(
        frame, acquisition["cutoff"]
    )
    from urban_tree_ml.boundary_shifts import digest
    inventory_hash = digest(inventory_path) if config.imagery.boundary_shifts or boundary_shift_recipes else None
    chip_pixels = config.imagery.chip_pixels
    output_root = config.paths.root / "chips" / (output_dataset or config.dataset)
    if boundary_shift_recipes is not None:
        if not output_dataset or output_dataset == config.dataset or output_root.exists():
            raise ValueError('boundary shifts require a new, separate output_dataset')
        if feedback_path is None:
            raise ValueError('boundary shifts require explicit published feedback')
    if not source_supervision_only:
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
    feedback_hash = digest(selected_feedback_path) if selected_feedback_path and (config.imagery.boundary_shifts or boundary_shift_recipes) else None
    feedback = (
        load_training_feedback(selected_feedback_path,
                               Path(feedback_path).parent/'imagery.vrt' if temporal_splits is not None else source_path,
                               config.dataset,
                               source_raster=None if temporal_splits is not None else feedback_source_raster)
        if selected_feedback_path is not None
        else None
    )
    from urban_tree_ml.curation_admission import load as load_tile_admission, touched_tiles, trusted_tree_ids
    admission = load_tile_admission(selected_feedback_path.parent) if selected_feedback_path else None
    admission_records = []
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
    feedback_region_records = feedback.get("region_overrides", []) if feedback is not None else []
    feedback_regions: list[dict[str, object]] = []
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
        image_cache = PreparedImageCache.configured(
            source, config.imagery, config.paths.root, "all-band-raster-masks-v1"
        )
        added_trees = confirmed_tree_rows(feedback_region_records, config, source.crs)
        from urban_tree_ml.vegetation_groups import GROUP_CLASSES, replace_enclosed_centers
        from urban_tree_ml.config import taxonomy_path
        if any(r['mode'] in GROUP_CLASSES for r in feedback_region_records):
            vocabulary = json.loads(taxonomy_path(config).read_text())
            if not set(GROUP_CLASSES.values()) <= set(vocabulary['species']):
                raise ValueError('Vegetation groups require explicit append-only taxonomy/head migration before chip building')
        if not added_trees.empty:
            if validation_supervision_only:
                added_trees = added_trees[added_trees['split'].eq('validation')].copy()
            frame = pd.concat([frame, added_trees], ignore_index=True)
            for tree in added_trees.itertuples(index=False):
                # Human centers are absolute, not inventory registration offsets.
                point_corrections[(tree.tree_id, tree.split)] = (0.0, 0.0)
        for region in feedback_region_records:
            if region["mode"] == "confirmed-tree":
                continue
            if region['mode'] in GROUP_CLASSES and (
                added_trees.empty or 'manual-' + str(region['region_id']) not in set(added_trees.tree_id)
            ):
                continue  # A guarded-out group must not erase individual supervision.
            center_x, center_y = transformer.transform(
                float(region["anchor_longitude"]),
                float(region["anchor_latitude"]),
            )
            center_col, center_row = (~source.transform) * (
                center_x + float(region["east_m"]),
                center_y + float(region["north_m"]),
            )
            feedback_regions.append(
                {
                    "region_id": str(region["region_id"]),
                    "mode": str(region["mode"]),
                    "splits": {str(split) for split in region["splits"]},
                    "pixel_col": float(center_col),
                    "pixel_row": float(center_row),
                    "radius_px": float(region["radius_m"])
                    / config.imagery.resolution_m,
                }
            )
            if 'polygon' in region:
                points = np.asarray(region['polygon'])
                px, py = transformer.transform(points[:,0], points[:,1])
                cols, rows = (~source.transform) * (px, py)
                feedback_regions[-1]['polygon'] = list(zip(cols, rows))
        from urban_tree_ml.crown_size import assign_crown_labels
        from urban_tree_ml.config import taxonomy_path
        if config.model.crown_head or any(r.get('tree_id') or r.get('species') or r['mode'] in GROUP_CLASSES for r in feedback_region_records):
            vocabulary = json.loads(taxonomy_path(config).read_text())
            frame = assign_crown_labels(frame, feedback_region_records, vocabulary, estimated=config.model.crown_head)
        # Explicit reviewed taxonomy wins over historical inventory/crown metadata.
        # Apply after added rows too, without changing centers or sealed test labels.
        if feedback and feedback.get('taxonomy_corrections'):
            from urban_tree_ml.species_corrections import apply_taxonomy_corrections
            frame = apply_taxonomy_corrections(frame, feedback['taxonomy_corrections'],
                json.loads(taxonomy_path(config).read_text()))
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
        frame = replace_enclosed_centers(frame, feedback_regions)
        if source_supervision_only:
            # Same canonical registration/curation logic as real chip building.
            # No raster pixels, target arrays, chip files or statistics are read/written.
            return dict(frame=frame[frame['split'].eq('train')].copy(),
                        regions=[r for r in feedback_regions if 'train' in r['splits']],
                        crs=source.crs.to_string(), transform=list(source.transform),
                        training_ready=False)
        frame = frame[
            (frame["pixel_col"] >= 0)
            & (frame["pixel_col"] < source.width)
            & (frame["pixel_row"] >= 0)
            & (frame["pixel_row"] < source.height)
        ].copy()
        frame["chip_col"] = (frame["pixel_col"] // chip_pixels).astype("int64")
        frame["chip_row"] = (frame["pixel_row"] // chip_pixels).astype("int64")

        curated_tiles, curated_train, fully_reviewed, trusted = {}, set(), set(), set()
        if admission is not None and not validation_supervision_only:
            from urban_tree_ml.boundary_shifts import training_footprint
            manifest, state, published_feedback = admission
            curated_tiles = touched_tiles(manifest, state, source, chip_pixels, published_feedback)
            trusted = trusted_tree_ids(manifest, state)
            fully_reviewed = {chip for chip, reasons in curated_tiles.items() if 'full-more-done-tile' in reasons}
            # Frozen Fairfax/NYC manifests explicitly identify their native full
            # windows, including older schema-only fixtures without view anchors.
            if config.inventory.city.lower() in {'usnyc','usffx'} and temporal_splits is None:
                fully_reviewed.update(s['validation_chip_id'] for s in manifest['scenes']
                    if s.get('validation_chip_id') and s.get('image_width') == chip_pixels
                    and s.get('image_height') == chip_pixels
                    and state['scene_reviews'].get(s['scene_id'], {}).get('more_done') is True)
            for chip in curated_tiles:
                if allowed_chip_ids is not None and chip not in allowed_chip_ids:
                    continue
                row, col = map(int, (chip[1:7], chip.split('_c')[1]))
                if training_footprint(source, col*chip_pixels, row*chip_pixels, chip_pixels, config):
                    curated_train.add(chip)
        from urban_tree_ml.mosaic_coverage import source_rectangles, valid_window
        coverage_rectangles = source_rectangles(source_path)
        from urban_tree_ml import mosaic_coverage
        from urban_tree_ml.prepared_image_cache import digest
        image_cache = PreparedImageCache.configured(
            source, config.imagery, config.paths.root,
            "mosaic-coverage-" + digest(Path(mosaic_coverage.__file__).read_bytes()),
        )

        recipe_payloads = []
        shifted = []
        if boundary_shift_recipes is not None:
            from urban_tree_ml.boundary_shifts import validate_recipes
            recipe_payloads = [json.loads(Path(boundary_shift_recipes).read_text())]
            shifted = validate_recipes(recipe_payloads[0], source, source_path, chip_pixels, config, frame)
        elif config.imagery.boundary_shifts:
            from urban_tree_ml.boundary_shifts import automatic_plans
            recipe_payloads = automatic_plans(source, source_path, chip_pixels, config, frame)
            shifted = [r for p in recipe_payloads for r in p['recipes']]

        def windows():
            if validation_supervision_only or fairfax_splits is not None or temporal_splits is not None:
                # Preserve pinned windows, including reviewed empty tiles.
                import re
                for chip_id in sorted(allowed_chip_ids):
                    match = re.fullmatch(r'r(\d+)_c(\d+)', chip_id)
                    if not match:
                        raise ValueError('Only unshifted pinned windows are supported')
                    row, col = map(int, match.groups())
                    group = frame[(frame.chip_row == row) & (frame.chip_col == col)]
                    yield chip_id, row*chip_pixels, col*chip_pixels, group, None
                return
            emitted = set()
            for (chip_row, chip_col), group in frame.groupby(
                ["chip_row", "chip_col"], sort=True, observed=True
            ):
                chip_id = f"r{chip_row:06d}_c{chip_col:06d}"
                emitted.add(chip_id)
                yield (chip_id, int(chip_row*chip_pixels),
                       int(chip_col*chip_pixels), group, None)
            for chip_id in sorted(curated_train - emitted):
                row, col = int(chip_id[1:7]), int(chip_id.split('_c')[1])
                yield chip_id, row*chip_pixels, col*chip_pixels, frame.iloc[:0], None
            for recipe in shifted:
                row, col = recipe['row_offset'], recipe['column_offset']
                group = frame[(frame.pixel_col >= col) & (frame.pixel_col < col+chip_pixels)
                              & (frame.pixel_row >= row) & (frame.pixel_row < row+chip_pixels)]
                yield recipe['chip_id'], row, col, group, recipe

        for chip_id, row_off, col_off, group, recipe in windows():
            if allowed_chip_ids is not None and chip_id not in allowed_chip_ids:
                continue
            positive_group = group[~group["feedback_excluded"] & ~group["planted_after_imagery"]]
            strict_curated = False
            if admission is not None and not validation_supervision_only:
                curated_city = config.inventory.city.lower() in {'usnyc','usffx'} or temporal_splits is not None
                strict_curated = ((curated_city and chip_id not in fully_reviewed)
                    or (chip_id in curated_train and chip_id not in fully_reviewed and not any(
                        ~positive_group.tree_id.astype(str).str.startswith('manual-'))))
                if curated_city and chip_id not in fully_reviewed:
                    positive_group = positive_group[positive_group.tree_id.astype(str).isin(trusted)
                        | positive_group.tree_id.astype(str).str.startswith('manual-')]
            pinned_splits = temporal_splits if temporal_splits is not None else fairfax_splits
            if pinned_splits is not None:
                splits = np.asarray([pinned_splits[chip_id]])
                if not set(positive_group['split']) <= {pinned_splits[chip_id]}:
                    raise ValueError('Human labels cross the frozen split boundary')
            elif validation_supervision_only:
                splits = np.asarray(['validation'])
            elif recipe is not None:
                # Cleared reviewed windows remain useful negative examples.
                splits = np.asarray(['train'])
            elif positive_group.empty and chip_id in curated_train and set(group['split']) <= {'train'}:
                # Human negative/partial edits are training examples without
                # requiring a surviving municipal inventory point.
                splits = np.asarray(['train'])
            elif positive_group.empty:
                # A cleared validation tile is still an evaluation example. Do not
                # turn excluded points into positives or add empty training tiles.
                splits = group["split"].unique()
                if len(splits) != 1 or (splits[0] != "validation" and not (
                    allowed_chip_ids is not None and splits[0] == 'train')):
                    continue
            else:
                splits = positive_group["split"].unique()
            if len(splits) != 1:
                skipped_mixed_split += 1
                continue
            if allowed_splits is not None and str(splits[0]) not in allowed_splits:
                continue
            if row_off + chip_pixels > source.height or col_off + chip_pixels > source.width:
                skipped_invalid += 1
                continue
            window = Window(col_off, row_off, chip_pixels, chip_pixels)
            prepared_image = None
            if image_cache is not None:
                prepared_image = image_cache.read(
                    window,
                    lambda window=window: valid_window(source, config.imagery.bands, window, coverage_rectangles),
                )
            if prepared_image is None:
                raw = source.read(config.imagery.bands, window=window)
                valid_mask = valid_window(source, config.imagery.bands, window, coverage_rectangles)
                image = raw.astype(np.float32) * config.imagery.input_scale
            else:
                image, valid_mask = prepared_image.image, prepared_image.valid_mask
            valid_fraction = float(valid_mask.mean())
            if valid_fraction < config.imagery.minimum_valid_fraction:
                skipped_invalid += 1
                continue
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
                    crown_radius_m=getattr(row, "crown_radius_m", None),
                    crown_weight=getattr(row, "crown_weight", 1.0),
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
                        "crown_radius_m": row.get("crown_radius_m", None),
                        "crown_source": row.get("crown_source", None),
                        "vegetation_type": row.get("vegetation_type", '') if pd.notna(row.get("vegetation_type", '')) else '',
                    }
                )
            ignored_locations = [
                (float(row.pixel_col - col_off), float(row.pixel_row - row_off))
                for row in group[group["feedback_excluded"] & ~group["planted_after_imagery"]].itertuples(index=False)
            ]
            split = str(splits[0])
            mask_regions = [
                DetectionMaskRegion(
                    x=float(region["pixel_col"]) - col_off,
                    y=float(region["pixel_row"]) - row_off,
                    radius=float(region["radius_px"]),
                    mode=str(region["mode"]),
                    polygon=tuple((x-col_off, y-row_off) for x,y in region.get('polygon', [])),
                )
                for region in feedback_regions
                if split in region["splits"]
                and float(region["pixel_col"]) + float(region["radius_px"]) >= col_off
                and float(region["pixel_col"]) - float(region["radius_px"])
                < col_off + chip_pixels
                and float(region["pixel_row"]) + float(region["radius_px"]) >= row_off
                and float(region["pixel_row"]) - float(region["radius_px"])
                < row_off + chip_pixels
            ]
            targets = build_targets(
                chip_pixels,
                chip_pixels,
                labels,
                stride=config.targets.output_stride,
                gaussian_sigma_px=config.targets.gaussian_sigma_px,
                crown_scaled_center=config.targets.crown_scaled_center and split == 'train',
                resolution_m=config.imagery.resolution_m,
                crown_center_fraction=config.targets.crown_center_fraction,
                crown_center_max_sigma_m=config.targets.crown_center_max_sigma_m,
                crown_estimated_scale=config.targets.crown_estimated_scale,
                supervision_radius_px=(
                    config.targets.positive_supervision_radius_m / config.imagery.resolution_m
                ),
                valid_mask=valid_mask,
                ndvi=ndvi,
                ignored_locations=ignored_locations,
                mask_regions=mask_regions,
                background_mode='curated_only' if strict_curated else config.targets.background_mode,
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
            if admission is not None and chip_id in curated_tiles:
                admission_records.append(dict(chip_id=chip_id, split=split,
                    reasons=curated_tiles[chip_id], strict_curated_mask=strict_curated,
                    supervised_cells=int(np.count_nonzero(targets['detection_mask'])),
                    retained_targets=len(labels)-target_collision_excluded))
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
                with temporary_chip.open("w+b") as output:
                    if validation_supervision_only and not validation_full_targets:
                        np.savez_compressed(output, scoring_mask=targets['scoring_mask'])
                    elif prepared_image is not None:
                        prepared_image.write_targets(output, targets)
                    else:
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
                    "post_imagery_planting_count": int(group["planted_after_imagery"].sum()),
                    "feedback_region_count": len(mask_regions),
                    "valid_fraction": valid_fraction,
                    "row_offset": row_off,
                    "column_offset": col_off,
                    "augmentation_recipe": recipe['chip_id'] if recipe else None,
                    "parent_chip_ids": json.dumps(recipe['parents']) if recipe else None,
                }
            )

    manifest_path = output_root / "chips.parquet"
    if admission is not None:
        materialized = {r['chip_id'] for r in records}
        (output_root/'curation-admission.json').write_text(json.dumps(dict(
            policy='any_human_curation_v1', materialized=admission_records,
            touched_not_materialized=sorted(set(curated_tiles)-materialized),
            guarded_training_candidates=sorted(curated_train),
            missing_training_candidates=sorted(curated_train-materialized),
            test_evaluated=False), indent=2)+'\n')
    if recipe_payloads:
        if digest(inventory_path) != inventory_hash or (selected_feedback_path and digest(selected_feedback_path) != feedback_hash):
            raise RuntimeError('inputs changed during shifted-chip build; rebuild from a frozen snapshot')
        # Pin this build; recipes themselves carry no copied curation labels.
        (output_root / 'boundary-shift-build.json').write_text(json.dumps(dict(
            recipes=recipe_payloads,
            recipe_sha256=hashlib.sha256(json.dumps(recipe_payloads, sort_keys=True).encode()).hexdigest(),
            inventory_sha256=inventory_hash,
            feedback_sha256=feedback_hash,
            orthophoto_sha256={p['orthophoto']: digest(p['orthophoto']) for p in recipe_payloads},
            config=config.model_dump(mode='json'),
            materialized=[r['chip_id'] for r in records if r['augmentation_recipe']],
        ), indent=2), encoding='utf-8')
    temporal_path = output_root / "post-imagery-planting-exclusions.parquet"
    frame.loc[frame["planted_after_imagery"],
              ["tree_id", "split", "plant_date", "pixel_col", "pixel_row"]
              if "plant_date" in frame else ["tree_id", "split", "pixel_col", "pixel_row"]
              ].to_parquet(temporal_path, index=False)
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
        "crown_radius_m",
        "crown_source",
        "vegetation_type",
    ]
    pd.DataFrame.from_records(label_records, columns=label_columns).to_parquet(
        labels_path, index=False
    )
    if validation_supervision_only:
        if {r['chip_id'] for r in records} != allowed_chip_ids:
            raise ValueError('Validation supervision coverage differs from pinned cohort')
        return dict(chips=len(records), labels=str(labels_path), manifest=str(manifest_path),
                    validation_supervision_only=True)
    local_normalization = None
    if channel_sum is not None and channel_sum_squares is not None and channel_pixel_count:
        channel_mean = channel_sum / channel_pixel_count
        channel_variance = np.maximum(
            channel_sum_squares / channel_pixel_count - np.square(channel_mean), 1e-12
        )
        local_normalization = {
            "mean": channel_mean.tolist(),
            "std": np.sqrt(channel_variance).tolist(),
            "source": "training split pixels only",
        }
    elif not (temporal_splits and set(temporal_splits.values()) == {'validation'}
              and {r['chip_id'] for r in records} == set(temporal_splits)
              and config.reference is not None):
        raise ValueError(
            "no training pixels were materialized; check the split and raster coverage"
        )
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
        if local_normalization is not None:
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
        "planting_date_cutoff": acquisition["cutoff"],
        "acquisition_date_provenance": acquisition,
        "post_imagery_planting_excluded_points": int(frame["planted_after_imagery"].sum()),
        "post_imagery_planting_exclusions": str(temporal_path),
        "chips": len(records),
        "boundary_shift_chips": sum(r['augmentation_recipe'] is not None for r in records),
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
        "feedback_source_raster": str(feedback_source_raster) if feedback_source_raster else None,
        "registration_correction_m": {
            "east": correction_east_m,
            "north": correction_north_m,
        },
        "feedback_excluded_points": int(frame["feedback_excluded"].sum()),
        "feedback_point_corrected_points": int(frame["feedback_point_corrected"].sum()),
        "feedback_mask_regions": len(feedback_regions),
        "feedback_region_chip_intersections": sum(
            int(record["feedback_region_count"]) for record in records
        ),
        "source_raster": str(source_path.resolve()),
        "manifest": str(manifest_path),
        "normalization": str(output_root / "normalization.json"),
        "local_normalization": (
            str(local_normalization_path) if local_normalization_path is not None else None
        ),
    }
    if image_cache is not None:
        image_cache.validate_source()
        summary["prepared_image_cache"] = image_cache.stats
    (output_root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if fairfax_splits is not None:
        from urban_tree_ml.fairfax_curation import record_dataset
        record_dataset(output_root, Path(feedback_path).parent, records)
    return summary
