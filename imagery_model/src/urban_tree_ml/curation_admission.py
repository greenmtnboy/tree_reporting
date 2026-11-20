"""Locate published human curation on the model grid, independently of inventories.

Membership is evidence of an edit, not permission to label the rest of a tile as
background. Callers must preserve spatial split membership and supervision masks.
"""
import math
import json
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import Point, Polygon, box


def human_review(review):
    return bool(review) and (review.get('crown_source') == 'human'
        or review.get('species_override') is not None
        or review.get('source', 'human') not in {
            'heuristic', 'crown-overlap', 'vegetation-heuristic', 'model', 'prediction', 'inventory-suggestion'})


def touched_tiles(manifest, state, source, pixels, feedback, *, intersection_tolerance_pixels=0.):
    """Return tile -> reasons for human points, regions, and completed viewports.

    Coordinates are transformed rather than copying review tile IDs: old review
    images may have a different resolution from the model's current pixel grid.
    No image pixels or sealed test labels are accessed here.
    """
    transformer = Transformer.from_crs('EPSG:4326', source.crs, always_xy=True)
    inverse = ~source.transform
    reasons = {}

    def position(longitude, latitude, east=0., north=0.):
        x, y = transformer.transform(longitude, latitude)
        return inverse * (x + east, y + north)

    def add(shape, reason, full_review=False):
        left, top, right, bottom = shape.bounds
        for row in range(max(0, math.floor(top/pixels)), min(math.ceil(source.height/pixels), math.floor(bottom/pixels)+1)):
            for col in range(max(0, math.floor(left/pixels)), min(math.ceil(source.width/pixels), math.floor(right/pixels)+1)):
                tile = box(col*pixels, row*pixels, (col+1)*pixels, (row+1)*pixels)
                intersection = shape.intersection(tile)
                if intersection.is_empty or (shape.area > 0 and intersection.area <= 1e-6):
                    continue
                # Geographic roundtrips of an exact tile boundary can protrude
                # by a tiny fraction of a pixel. Acquisition admission may opt
                # into an interior test so that this does not add a neighbor.
                if (shape.area > 0 and intersection_tolerance_pixels > 0
                        and shape.buffer(-intersection_tolerance_pixels).intersection(tile).area <= 1e-6):
                    continue
                selected = reasons.setdefault(f'r{row:06d}_c{col:06d}', set())
                selected.add(reason)
                # Review PNGs may be 256px at 60cm while model tiles are 512px
                # at 30cm. Compare their geographic footprint, not pixel counts.
                # Sub-millimetre projection roundoff is irrelevant to membership.
                if full_review and shape.covers(tile.buffer(-1e-3)):
                    selected.add('full-more-done-tile')

    samples = {str(s['sample_id']): s for s in manifest.get('samples', [])}
    for sid, review in state.get('reviews', {}).items():
        sample = samples.get(sid)
        if not sample or sample.get('split') == 'test' or not human_review(review):
            continue
        add(Point(position(sample['longitude'], sample['latitude'])), 'reviewed-point')
        if review.get('east_m') is not None and review.get('north_m') is not None:
            add(Point(position(sample['longitude'], sample['latitude'], review['east_m'], review['north_m'])), 'corrected-point')

    for scene in manifest.get('scenes', []):
        review = state.get('scene_reviews', {}).get(scene['scene_id'], {})
        if not (review.get('done') or review.get('more_done')) or 'test' in scene.get('splits', []):
            continue
        anchor = scene.get('anchor') or next((samples[sid] for sid in scene.get('sample_ids', []) if sid in samples), None)
        if anchor is None:
            continue
        x, y = transformer.transform(anchor['longitude'], anchor['latitude'])
        a, b, d, e = (anchor.get('transform_'+key, 0.) for key in ('a', 'b', 'd', 'e'))
        if not a or not e:
            raise ValueError('Completed review viewport has no coordinate transform')
        corners = []
        for px, py in [(0,0), (scene['image_width'],0), (scene['image_width'],scene['image_height']), (0,scene['image_height'])]:
            dx, dy = px-anchor['target_x'], py-anchor['target_y']
            corners.append(inverse * (x+a*dx+b*dy, y+d*dx+e*dy))
        add(Polygon(corners), 'completed-viewport', review.get('more_done') is True)

    for region in feedback.get('region_overrides', []):
        if not human_review(region) or not set(region.get('splits', [])) & {'train','validation'}:
            continue
        if region.get('polygon'):
            shape = Polygon([position(lon, lat) for lon, lat in region['polygon']])
        else:
            center = position(region['anchor_longitude'], region['anchor_latitude'], region.get('east_m',0.), region.get('north_m',0.))
            # The NAIP model grids are square, north-up metric grids.
            shape = (Point(center) if region['mode'] == 'confirmed-tree' else
                     Point(center).buffer(region['radius_m']/abs(source.transform.a)))
        if not shape.is_valid:
            # The editor permits self-intersecting polygons; target generation
            # retains its existing even/odd rasterization. Use a conservative
            # envelope ONLY to discover candidate tiles, never as supervision.
            shape = shape.envelope
        add(shape, 'region:'+region['mode'])
    return {chip: sorted(value) for chip, value in sorted(reasons.items())}


POLICY = 'any_human_curation_v1'


def frozen_context(directory):
    from urban_tree_ml.freeze_next_run import validate_frozen_city
    directory = Path(directory)
    state = validate_frozen_city(directory)
    manifest = json.loads((directory/'manifest.json').read_text())
    feedback = json.loads((directory/'training-feedback.json').read_text())
    return manifest, state, feedback


def planned_training_ids(directory, source, pixels, planned):
    manifest, state, feedback = frozen_context(directory)
    touched = touched_tiles(manifest, state, source, pixels, feedback)
    return sorted(set(touched) & {chip for chip, split in planned.items() if split == 'train'})


def enable(directory):
    """Opt a new immutable input bundle into the revised training policy."""
    from urban_tree_ml.prepared_finetune import sha
    directory = Path(directory)
    frozen_context(directory)
    path = directory/'tile-admission.json'
    if path.exists():
        raise ValueError('Tile admission already frozen')
    path.write_text(json.dumps(dict(policy=POLICY,
        integrity_sha256=sha(directory/'integrity.json')), indent=2)+'\n')


def load(directory):
    from urban_tree_ml.prepared_finetune import sha
    directory = Path(directory)
    path = directory/'tile-admission.json'
    if not path.exists():
        return None  # Historical reconstructions keep their archived semantics.
    policy = json.loads(path.read_text())
    if policy != dict(policy=POLICY, integrity_sha256=sha(directory/'integrity.json')):
        raise ValueError('Curated tile admission differs from frozen publication')
    return frozen_context(directory)


def trusted_tree_ids(manifest, state):
    samples = {s['sample_id']: s for s in manifest.get('samples', [])}
    return {str(samples[sid]['tree_id']) for sid, review in state.get('reviews', {}).items()
            if sid in samples and human_review(review) and review.get('status') in {'aligned','offset'}
            and samples[sid].get('split') != 'test'}
