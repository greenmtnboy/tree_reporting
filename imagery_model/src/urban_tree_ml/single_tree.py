"""Shared geometry for image-scoped curation polygons, independent of size labels."""
import numpy as np


def polygon_points(value):
    points = np.asarray(value, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or not 3 <= len(points) <= 256 or not np.isfinite(points).all():
        raise ValueError('Curation area needs 3–256 finite polygon vertices')
    relative = points-points[0]
    if abs(np.sum(relative[:, 0]*np.roll(relative[:, 1], -1)-relative[:, 1]*np.roll(relative[:, 0], -1))) < 1e-12:
        raise ValueError('Curation polygon has no area')
    return points


def inside_polygon(x, y, points):
    """Vectorized even-odd fill, including the boundary; supports clipped windows."""
    points = polygon_points(points)
    x, y = np.broadcast_arrays(x, y)
    inside = np.zeros(x.shape, dtype=bool)
    boundary = inside.copy()
    # CRS round trips leave nominal pixel boundaries a few billionths of a
    # pixel away from zero. Test distance in pixels, including segment bounds,
    # so boundary inclusion does not depend on platform projection rounding.
    epsilon = 1e-7
    for (ax, ay), (bx, by) in zip(points, np.roll(points, -1, axis=0)):
        cross = (x-ax)*(by-ay)-(y-ay)*(bx-ax)
        length = np.hypot(bx-ax, by-ay)
        boundary |= ((np.abs(cross) <= epsilon*length)
                     & (x >= min(ax,bx)-epsilon) & (x <= max(ax,bx)+epsilon)
                     & (y >= min(ay,by)-epsilon) & (y <= max(ay,by)+epsilon))
        if by != ay:
            inside ^= ((ay > y) != (by > y)) & (x < (bx-ax)*(y-ay)/(by-ay)+ax)
    return inside | boundary
