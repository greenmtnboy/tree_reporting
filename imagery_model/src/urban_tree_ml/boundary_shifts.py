"""Deterministic half-chip windows; no pixel compositing or copied annotations."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from functools import lru_cache

from pyproj import Transformer

from urban_tree_ml.splits import Block, split_for_block


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def grid(source):
    return dict(crs=source.crs.to_string(), transform=list(source.transform),
                width=source.width, height=source.height)


def source_members(path):
    """Follow nested VRTs so a recipe cannot name an unrelated photograph."""
    import rasterio
    seen, pending = set(), [Path(path).resolve()]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        if current.suffix.lower() == '.vrt':
            with rasterio.open(current) as source:
                pending.extend(Path(p).resolve() for p in source.files if Path(p).exists())
    return seen


def within_photo(source, photo, col, row, size):
    if (source.crs != photo.crs or source.transform.b or source.transform.d
            or photo.transform.b or photo.transform.d):
        raise ValueError('boundary shifts require matching CRS and north-up imagery')
    left, top = source.transform * (col, row)
    right, bottom = source.transform * (col + size, row + size)
    b = photo.bounds
    return b.left <= left < right <= b.right and b.bottom <= bottom < top <= b.top


def other_photo_bounds(source, source_path, photo_path):
    import rasterio
    result = []
    for member in source_members(source_path):
        if member == Path(photo_path).resolve() or member.suffix.lower() not in {'.tif', '.tiff'}:
            continue
        with rasterio.open(member) as other:
            if other.crs != source.crs:
                raise ValueError('mixed source CRS is not supported by boundary shifts')
            result.append(other.bounds)
    return result


def exclusive_photo(source, others, col, row, size):
    """Reject ambiguous mosaic overlap: containment alone doesn't name pixel owner."""
    left, top = source.transform * (col, row)
    right, bottom = source.transform * (col+size, row+size)
    return not any(max(left, b.left) < min(right, b.right)
                   and max(bottom, b.bottom) < min(top, b.top) for b in others)


@lru_cache(maxsize=32)
def _split_transformer(source_crs, target_crs):
    return Transformer.from_crs(source_crs, target_crs, always_xy=True)


def training_footprint(source, col, row, size, config):
    """Conservatively reject any touched held-out spatial block, including guard."""
    from rasterio.windows import Window, bounds
    transformer = _split_transformer(source.crs.to_string(), config.split.projected_crs)
    left, bottom, right, top = transformer.transform_bounds(
        *bounds(Window(col, row, size, size), source.transform), densify_pts=21)
    guard, block = config.split.guard_m, config.split.block_size_m
    return all(split_for_block(Block(x, y), config.split, config.seed) == 'train'
               for x in range(math.floor((left-guard)/block), math.floor((right+guard)/block)+1)
               for y in range(math.floor((bottom-guard)/block), math.floor((top+guard)/block)+1))


def plan(manifest, approved_ids, source_path, orthophoto_path, chip_pixels):
    """Complete 2x2 train grids; optional selection is not a reviewed-status gate."""
    import rasterio
    if chip_pixels <= 0 or chip_pixels % 2:
        raise ValueError('chip size must be positive and even')
    if manifest.chip_id.duplicated().any():
        raise ValueError('duplicate chip IDs in parent manifest')
    approved = set(approved_ids) if approved_ids is not None else set(manifest[manifest.split == 'train'].chip_id)
    if not approved <= set(manifest.chip_id):
        raise ValueError('approved chip missing from manifest')
    parents = manifest[manifest.chip_id.isin(approved)]
    if not (parents.split == 'train').all():
        raise ValueError('only training parents may be approved')
    photo_path = Path(orthophoto_path).resolve()
    if photo_path not in source_members(source_path) or photo_path.suffix.lower() not in {'.tif', '.tiff'}:
        raise ValueError('orthophoto must be a source GeoTIFF of the input raster')
    positions = {(int(r.row_offset), int(r.column_offset)): str(r.chip_id)
                 for r in parents.itertuples()}
    if len(positions) != len(parents):
        raise ValueError('duplicate parent windows')
    recipes = []
    with rasterio.open(source_path) as source, rasterio.open(photo_path) as photo:
        others = other_photo_bounds(source, source_path, photo_path)
        for row, col in sorted(positions):
            corners = [(row, col), (row, col+chip_pixels),
                       (row+chip_pixels, col), (row+chip_pixels, col+chip_pixels)]
            if not all(p in positions for p in corners):
                continue
            # Require the entire donor block in one photo, not just its center.
            if not within_photo(source, photo, col, row, 2*chip_pixels):
                continue
            if row < 0 or col < 0 or row+2*chip_pixels > source.height or col+2*chip_pixels > source.width:
                continue
            if not exclusive_photo(source, others, col+chip_pixels//2, row+chip_pixels//2, chip_pixels):
                continue
            recipes.append(dict(chip_id=f'shift_r{row+chip_pixels//2:06d}_c{col+chip_pixels//2:06d}',
                                row_offset=row+chip_pixels//2, column_offset=col+chip_pixels//2,
                                parents=[positions[p] for p in corners]))
        return dict(schema_version=1, operation='intersection-half-chip', chip_pixels=chip_pixels,
                    grid=grid(source), orthophoto=str(photo_path), recipes=recipes)


def validate_recipes(payload, source, source_path, size, config, frame):
    """Fail closed before writing chips. Recheck splits against this build's data."""
    import rasterio
    if (payload.get('schema_version') != 1 or payload.get('operation') != 'intersection-half-chip'
            or payload.get('chip_pixels') != size or payload.get('grid') != grid(source)):
        raise ValueError('boundary-shift recipe/grid mismatch')
    photo_path = Path(payload['orthophoto']).resolve()
    if photo_path not in source_members(source_path) or photo_path.suffix.lower() not in {'.tif', '.tiff'}:
        raise ValueError('recipe orthophoto is not a source GeoTIFF')
    seen = set()
    with rasterio.open(photo_path) as photo:
        others = other_photo_bounds(source, source_path, photo_path)
        for recipe in payload['recipes']:
            row, col = recipe['row_offset'], recipe['column_offset']
            if (type(row) is not int or type(col) is not int or row < size//2 or col < size//2
                    or row+size > source.height or col+size > source.width
                    or recipe['chip_id'] != f'shift_r{row:06d}_c{col:06d}'
                    or recipe['chip_id'] in seen or len(set(recipe['parents'])) != 4):
                raise ValueError('invalid or duplicate boundary-shift recipe')
            seen.add(recipe['chip_id'])
            if not within_photo(source, photo, col-size//2, row-size//2, size*2):
                raise ValueError('donor block crosses orthophoto boundary')
            if not exclusive_photo(source, others, col, row, size):
                raise ValueError('shifted window has ambiguous mosaic source ownership')
            if not training_footprint(source, col, row, size, config):
                raise ValueError('boundary-shift window touches held-out split or guard')
            subset = frame[(frame.pixel_col >= col) & (frame.pixel_col < col+size)
                           & (frame.pixel_row >= row) & (frame.pixel_row < row+size)]
            if not (subset.split == 'train').all():
                raise ValueError('boundary-shift window includes non-training labels')
    return payload['recipes']


def automatic_plans(source, source_path, size, config, frame):
    """Build all eligible intersections without consulting review status."""
    import pandas as pd
    parents = []
    for (row, col), group in frame.groupby(['chip_row', 'chip_col'], sort=True):
        if (group.split == 'train').all():
            parents.append(dict(chip_id=f'r{row:06d}_c{col:06d}', split='train',
                                row_offset=int(row*size), column_offset=int(col*size)))
    if not parents:
        return []
    manifest = pd.DataFrame(parents)
    plans = []
    for photo in sorted(source_members(source_path)):
        if photo.suffix.lower() not in {'.tif', '.tiff'}:
            continue
        payload = plan(manifest, None, source_path, photo, size)
        payload['recipes'] = [r for r in payload['recipes']
                              if training_footprint(source, r['column_offset'], r['row_offset'], size, config)]
        if payload['recipes']:
            validate_recipes(payload, source, source_path, size, config, frame)
            plans.append(payload)
    return plans


def main():
    import argparse
    import pandas as pd
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'raster', 'orthophoto', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--approved', type=Path, help='Optional JSON list of selected parent IDs; otherwise all train chips')
    parser.add_argument('--chip-pixels', type=int, default=512)
    args = parser.parse_args()
    result = plan(pd.read_parquet(args.manifest), json.loads(args.approved.read_text()) if args.approved else None,
                  args.raster, args.orthophoto, args.chip_pixels)
    # Never silently replace an existing recipe file.
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(result, output, indent=2)
    print(f"Planned {len(result['recipes'])} shifted training windows")


if __name__ == '__main__':
    main()
