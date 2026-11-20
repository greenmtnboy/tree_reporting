"""Training-chip coverage from retained targets and completed review scenes."""
import json

import polars as pl
from urban_tree_ml.catalog_imagery import matching_catalog_summary, matches_catalog_imagery


def training_chip_catalog(context, manifest, state, dataset=None, *, split="train"):
    candidates = []
    for path in (context.config.paths.root / "chips").glob("*/summary.json"):
        if dataset is not None and path.parent.name != dataset:
            continue
        try:
            summary = json.loads(path.read_text())
            matches = (matches_catalog_imagery(context.config.paths.root, summary.get('source_raster'),
                       context.raster, allow_resolution_derivative=True) if dataset is not None else
                       matching_catalog_summary(context.config.paths.root, summary, context.raster))
            if matches and (path.parent / "chips.parquet").exists():
                candidates.append(path)
        except (OSError, ValueError):
            continue
    if not candidates:
        return None
    directory = max(candidates, key=lambda p: p.stat().st_mtime).parent
    if not (directory / "labels.parquet").exists():
        return None
    chips = (pl.scan_parquet(directory / "chips.parquet").filter(pl.col('split') == split)
             .select('chip_id').collect())['chip_id']
    labels = (pl.scan_parquet(directory / "labels.parquet").filter(pl.col('split') == split)
              .select('chip_id', pl.col('tree_id').cast(pl.String)).collect())
    completed_samples = {
        sample_id for scene in manifest["scenes"]
        if any(state["scene_reviews"].get(scene["scene_id"], {}).get(k) for k in ("done", "more_done"))
        for sample_id in scene["sample_ids"]
    }
    reviewed_ids = {
        str(sample["tree_id"]) for sample in manifest["samples"]
        if sample["sample_id"] in completed_samples
        and state["reviews"].get(sample["sample_id"], {}).get("status")
    }
    trees = {chip: set(ids) for chip, ids in labels.group_by('chip_id').agg('tree_id').iter_rows()}
    explicit_done = {scene.get("validation_chip_id") for scene in manifest["scenes"]
                     if any(state["scene_reviews"].get(scene["scene_id"], {}).get(k) for k in ("done", "more_done"))
                     and split in scene.get("splits", [])}
    done = [str(chip) for chip in chips if (chip in explicit_done and not trees.get(chip))
            or (trees.get(chip) and trees[chip] <= reviewed_ids)]
    pending = [str(chip) for chip in chips if chip not in done]
    # Weight coverage by unique retained training targets, not equally sized chips.
    # A done small crop contributes only its actual trees; overlapping crops never
    # count the same inventory tree twice. Completion, not an automatic default
    # status or inferred chip completion, gates this metric.
    eligible_trees = set(labels.filter(pl.col('chip_id').is_in(chips.implode()))['tree_id'])
    completed_trees = {str(sample['tree_id']) for sample in manifest['samples']
                       if sample['sample_id'] in completed_samples}
    covered_trees = eligible_trees & completed_trees
    # Explicit full-chip second-pass completion is separate from target coverage
    # and must not change the pending training-assignment queue.
    more_reviewed_ids = {
        str(scene["validation_chip_id"]) for scene in manifest["scenes"]
        if scene.get("validation_chip_id")
        and state["scene_reviews"].get(scene["scene_id"], {}).get("more_done")
    } & set(chips.cast(pl.String))
    return {"total": len(chips), "curated": len(done),
            "more_reviewed": len(more_reviewed_ids),
            "more_reviewed_fraction": len(more_reviewed_ids) / len(chips) if len(chips) else 0,
            "eligible": [str(chip) for chip in chips],
            "fraction": len(done) / len(chips) if len(chips) else 0,
            "trees_total": len(eligible_trees), "trees_in_done_scenes": len(covered_trees),
            "tree_fraction": len(covered_trees) / len(eligible_trees) if eligible_trees else None,
            "pending": pending, "dataset": directory.name,
            "without_targets": sum(not trees.get(chip) for chip in chips)}


def next_training_truth(context, progress, manifest, state, *, include_reviewed=False, id_chip_pixels=None):
    if not progress or not progress["pending"]:
        raise ValueError("No unfinished training chips with retained targets remain")
    chip = progress["pending"][0]
    return chip, inventory_truth_for_chip(context, progress["dataset"], chip, "train", manifest, state,
                                          include_reviewed=include_reviewed, id_chip_pixels=id_chip_pixels)


def inventory_truth_for_chip(context, dataset, chip, split, manifest, state, *, include_reviewed, id_chip_pixels):
    directory = context.config.paths.root / "chips" / dataset
    labels = (pl.scan_parquet(directory / "labels.parquet")
              .filter((pl.col('chip_id') == chip) & (pl.col('split') == split))
              .with_columns(pl.col('tree_id').cast(pl.String)).collect())
    done_scenes = {key for key, value in state["scene_reviews"].items() if value.get("done")}
    done_trees = {str(sample["tree_id"]) for sample in manifest["samples"]
                  if sample.get("scene_id") in done_scenes
                  and state["reviews"].get(sample["sample_id"], {}).get("status")}
    inventory = pl.read_parquet(
        context.config.paths.root / "inventory" / context.city / "inventory.parquet"
    )
    inventory = inventory.with_columns(pl.col('tree_id').cast(pl.String),
                                       pl.col('diameter_at_breast_height').alias('dbh_in'))
    if labels.is_empty():
        import rasterio
        from pyproj import Transformer

        with rasterio.open(context.raster) as source:
            projector = Transformer.from_crs("EPSG:4326", source.crs, always_xy=True)
            x, y = projector.transform(
                inventory["longitude"].to_numpy(), inventory["latitude"].to_numpy()
            )
            col, row = ~source.transform * (x, y)
        inventory = inventory.with_columns(pl.Series("pixel_col", col), pl.Series("pixel_row", row))
        size = context.config.imagery.chip_pixels
        from urban_tree_ml.chip_geometry import chip_offsets
        row0, col0 = chip_offsets(chip, size, id_chip_pixels=id_chip_pixels)
        frame = inventory.filter((pl.col('split') == split) & pl.col('split_eligible')
                                 & pl.col('pixel_col').is_between(col0, col0+size, closed='left')
                                 & pl.col('pixel_row').is_between(row0, row0+size, closed='left'))
        if frame.is_empty():
            raise ValueError("Chip has no inventory points; background-only review is unavailable")
        return frame
    if not include_reviewed:
        labels = labels.filter(~pl.col('tree_id').is_in(done_trees))
    frame = labels.join(inventory.select('tree_id', 'species', 'genus', 'dbh_in'),
                        on='tree_id', validate='1:1', maintain_order='left')
    if len(frame) != len(labels):
        raise ValueError("Training chip targets are missing from the local inventory")
    return frame
