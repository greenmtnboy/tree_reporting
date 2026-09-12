"""Training-chip coverage from retained targets and completed review scenes."""
import json

import pandas as pd


def training_chip_catalog(context, manifest, state):
    candidates = []
    for path in (context.config.paths.root / "chips").glob("*/summary.json"):
        try:
            summary = json.loads(path.read_text())
            raster = str(summary.get("source_raster", "")).replace("\\", "/").split("/")[-1]
            if raster == context.raster.name and (path.parent / "chips.parquet").exists():
                candidates.append(path)
        except (OSError, ValueError):
            continue
    if not candidates:
        return None
    directory = max(candidates, key=lambda p: p.stat().st_mtime).parent
    if not (directory / "labels.parquet").exists():
        return None
    chips = pd.read_parquet(directory / "chips.parquet")
    labels = pd.read_parquet(directory / "labels.parquet")
    chips = chips[chips.split == "train"]
    labels = labels[labels.split == "train"]
    completed_samples = {
        sample_id for scene in manifest["scenes"]
        if state["scene_reviews"].get(scene["scene_id"], {}).get("done")
        for sample_id in scene["sample_ids"]
    }
    reviewed_ids = {
        str(sample["tree_id"]) for sample in manifest["samples"]
        if sample["sample_id"] in completed_samples
        and state["reviews"].get(sample["sample_id"], {}).get("status")
    }
    trees = labels.groupby("chip_id").tree_id.agg(lambda ids: set(ids.astype(str))).to_dict()
    explicit_done = {scene.get("validation_chip_id") for scene in manifest["scenes"]
                     if state["scene_reviews"].get(scene["scene_id"], {}).get("done")
                     and "train" in scene.get("splits", [])}
    done = [str(chip) for chip in chips.chip_id if (chip in explicit_done and not trees.get(chip))
            or (trees.get(chip) and trees[chip] <= reviewed_ids)]
    pending = [str(chip) for chip in chips.chip_id if chip not in done]
    return {"total": len(chips), "curated": len(done),
            "fraction": len(done) / len(chips) if len(chips) else 0,
            "pending": pending, "dataset": directory.name,
            "without_targets": sum(not trees.get(chip) for chip in chips.chip_id)}


def next_training_truth(context, progress, manifest, state):
    if not progress or not progress["pending"]:
        raise ValueError("No unfinished training chips with retained targets remain")
    chip = progress["pending"][0]
    directory = context.config.paths.root / "chips" / progress["dataset"]
    labels = pd.read_parquet(directory / "labels.parquet")
    labels = labels[(labels.chip_id == chip) & (labels.split == "train")]
    done_scenes = {key for key, value in state["scene_reviews"].items() if value.get("done")}
    done_trees = {str(sample["tree_id"]) for sample in manifest["samples"]
                  if sample.get("scene_id") in done_scenes
                  and state["reviews"].get(sample["sample_id"], {}).get("status")}
    inventory = pd.read_parquet(
        context.config.paths.root / "inventory" / context.city / "inventory.parquet"
    )
    inventory["tree_id"] = inventory.tree_id.astype(str)
    inventory["dbh_in"] = inventory["diameter_at_breast_height"]
    if labels.empty:
        import rasterio
        from pyproj import Transformer

        with rasterio.open(context.raster) as source:
            projector = Transformer.from_crs("EPSG:4326", source.crs, always_xy=True)
            x, y = projector.transform(
                inventory.longitude.to_numpy(), inventory.latitude.to_numpy()
            )
            col, row = ~source.transform * (x, y)
        inventory["pixel_col"], inventory["pixel_row"] = col, row
        size = context.config.imagery.chip_pixels
        row0, col0 = int(chip[1:7]) * size, int(chip[9:]) * size
        frame = inventory[(inventory.split == "train") & inventory.split_eligible
                          & (col >= col0) & (col < col0 + size)
                          & (row >= row0) & (row < row0 + size)].copy()
        if frame.empty:
            raise ValueError("Chip has no inventory points; background-only review is unavailable")
        return chip, frame
    labels = labels[~labels.tree_id.astype(str).isin(done_trees)].copy()
    labels["tree_id"] = labels.tree_id.astype(str)
    frame = labels.merge(inventory[["tree_id", "species", "genus", "dbh_in"]],
                         on="tree_id", validate="one_to_one")
    if len(frame) != len(labels):
        raise ValueError("Training chip targets are missing from the local inventory")
    return chip, frame
