"""Small inseparable vegetation objects; footprints are supervision, not segmentation outputs."""
from copy import deepcopy

import numpy as np

from urban_tree_ml.single_tree import inside_polygon, polygon_points

GROUP_CLASSES = {'copse': '__vegetation_copse__', 'shrub-mass': '__vegetation_shrub_mass__'}


def group_footprints_overlap(first, second):
    """Positive-area overlap in projected metres; touching edges are allowed.

    Polygon bounding circles are only a broad-phase filter, never the footprint.
    The small tolerance absorbs geographic/projected coordinate round trips.
    """
    eps = 1e-6
    a = np.array([first['world_x'], first['world_y']])
    b = np.array([second['world_x'], second['world_y']])
    if np.linalg.norm(a-b) >= first['radius_m'] + second['radius_m'] - eps:
        return False
    p, q = first.get('world_polygon'), second.get('world_polygon')
    if not p and not q:
        return True
    if not p or not q:
        center, radius, polygon = ((b, second['radius_m'], p) if p else
                                   (a, first['radius_m'], q))
        polygon = polygon_points(polygon)
        if inside_polygon(*center, polygon):
            return True
        end = np.roll(polygon, -1, axis=0)
        edges = end - polygon
        length2 = np.sum(edges*edges, axis=1)
        t = np.clip(np.sum((center-polygon)*edges, axis=1) /
                    np.where(length2 > 0, length2, 1), 0, 1)
        return bool(np.any(np.linalg.norm(polygon + t[:, None]*edges-center,
                                         axis=1) < radius-eps))
    p, q = polygon_points(p), polygon_points(q)

    def cross(v, w):
        return v[0]*w[1]-v[1]*w[0]

    # Proper edge crossings always enclose common area. Collinear edges and
    # containment (including identical polygons) are handled by scan lines.
    for start, end in zip(p, np.roll(p, -1, axis=0)):
        for other, other_end in zip(q, np.roll(q, -1, axis=0)):
            u, v = end-start, other_end-other
            denom = cross(u, v)
            if abs(denom) > eps:
                t = cross(other-start, v)/denom
                s = cross(other-start, u)/denom
                if eps < t < 1-eps and eps < s < 1-eps:
                    return True

    def intervals(poly, y):
        xs = sorted(ax + (y-ay)*(bx-ax)/(by-ay)
                    for (ax, ay), (bx, by) in zip(poly, np.roll(poly, -1, axis=0))
                    if (ay > y) != (by > y))
        return list(zip(xs[::2], xs[1::2]))

    levels = np.unique(np.r_[p[:, 1], q[:, 1]])
    for y in (levels[:-1]+levels[1:])/2:
        if any(min(r, rr)-max(l, ll) > eps
               for l, r in intervals(p, y) for ll, rr in intervals(q, y)):
            return True
    return False


def validate_group_overlaps(regions):
    """Shared whole-state guard for full saves, delta saves, and reads."""
    groups = [r for r in regions if r['mode'] in GROUP_CLASSES]
    for i, first in enumerate(groups):
        for second in groups[i+1:]:
            if group_footprints_overlap(first, second):
                raise ValueError('Vegetation group shapes overlap '
                                 f"({first['region_id']}, {second['region_id']}); "
                                 'use one group or separate their shapes')


def polygon_center(points):
    """Interior representative point via a scan line, including concave polygons."""
    points = polygon_points(points)
    mean = points.mean(axis=0)
    if inside_polygon(*mean, points):
        return mean
    levels = np.unique(points[:, 1])
    candidates = []
    for y in (levels[:-1] + levels[1:]) / 2:
        xs = sorted(ax + (y-ay)*(bx-ax)/(by-ay)
                    for (ax, ay), (bx, by) in zip(points, np.roll(points, -1, axis=0))
                    if (ay > y) != (by > y))
        for left, right in zip(xs[::2], xs[1::2]):
            candidates.append((right-left, (left+right)/2, y))
    if not candidates:
        raise ValueError('Vegetation polygon has no interior')
    _, x, y = max(candidates)
    return np.array([x, y])


def footprint(x, y, region, *, polygon_key='polygon'):
    if region.get(polygon_key):
        return inside_polygon(x, y, region[polygon_key])
    return (np.asarray(x)-region['pixel_col'])**2 + (np.asarray(y)-region['pixel_row'])**2 <= region['radius_px']**2


def replace_enclosed_centers(frame, regions):
    """Derived replacement only: removing the region restores original labels."""
    keep = np.ones(len(frame), dtype=bool)
    for region in regions:
        if region['mode'] not in GROUP_CLASSES:
            continue
        within = footprint(frame.pixel_col.to_numpy(), frame.pixel_row.to_numpy(), region)
        same_split = frame.split.isin(region['splits']).to_numpy()
        # Never remove another group target (overlaps are rejected at save time).
        ordinary = ~frame.get('vegetation_type', frame.tree_id.map(lambda _: '')).isin(GROUP_CLASSES)
        keep &= ~(within & same_split & ordinary.to_numpy())
    return frame.loc[keep].copy()


def expanded_taxonomy(parent):
    taxonomy = deepcopy(parent)
    for name in GROUP_CLASSES.values():
        if name not in taxonomy['species']:
            taxonomy['species'].append(name)
            taxonomy['species_to_genus'].append(-1)  # category, not a scientific genus
    return taxonomy


def migrate_group_head(saved, initialized, parent, candidate):
    """Append only two explicit categories; preserve every old class ID and weight."""
    if candidate != expanded_taxonomy(parent):
        raise ValueError('Only append-only vegetation group taxonomy migration is allowed')
    result = dict(saved)
    old = len(parent['species'])
    for key in ('network.species_head.1.weight', 'network.species_head.1.bias'):
        source, target = saved[key], initialized[key]
        if source.shape[0] != old or target.shape[0] != len(candidate['species']) or source.shape[1:] != target.shape[1:]:
            raise ValueError('Vegetation classifier shape mismatch')
        result[key] = target.clone()
        result[key][:old] = source
    return result
