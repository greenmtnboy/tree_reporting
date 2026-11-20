"""Actual source coverage for the direct, pixel-aligned NAIP mosaics we build.

GDAL can mark uncovered space in a sparse VRT as valid when no nodata is set.
Use source rectangles for these mosaics and defer to raster masks elsewhere.
"""

import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

import numpy as np

from urban_tree_ml.chip_geometry import chip_offsets


def source_rectangles(path):
    if not path:
        return ()
    path = Path(path)
    if path.suffix.lower() != ".vrt" or not path.is_file():
        return ()
    stat = path.stat()
    return _rectangles(path, stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=32)
def _rectangles(path, modified, size):
    root = ET.parse(path).getroot()
    if root.tag != "VRTDataset" or root.get("subClass"):
        return ()
    bounds = None
    for band in root.findall("VRTRasterBand"):
        if band.get("subClass"):
            return ()
        sources = [child for child in band if child.tag.endswith("Source")]
        if not sources or any(s.tag != "SimpleSource" for s in sources):
            return ()
        band_bounds = set()
        for source in sources:
            dst = source.find("DstRect")
            if dst is None:
                return ()
            x, y, w, h = (float(dst.attrib[k]) for k in ("xOff", "yOff", "xSize", "ySize"))
            if not all(v.is_integer() for v in (x, y, w, h)):
                return ()
            band_bounds.add((int(x), int(y), int(x + w), int(y + h)))
        if bounds is not None and bounds != band_bounds:
            return ()
        bounds = band_bounds
    return tuple(sorted(bounds or ()))


def missing_chips(path, pixels, chip_ids, minimum_valid_fraction=0.99):
    """Exclude sparse-mosaic tiles below the required photographed area fraction."""
    bounds = source_rectangles(path)
    if not bounds:
        return set()
    missing = set()
    for chip in chip_ids:
        row, col = chip_offsets(chip, pixels)
        if any(
            x <= col and col + pixels <= right and y <= row and row + pixels <= bottom
            for x, y, right, bottom in bounds
        ):
            continue
        covered = np.zeros((pixels, pixels), dtype=bool)
        for x, y, right, bottom in bounds:
            left, top = max(0, x - col), max(0, y - row)
            end, base = min(pixels, right - col), min(pixels, bottom - row)
            if left < end and top < base:
                covered[top:base, left:end] = True
        if covered.mean() < minimum_valid_fraction:
            missing.add(chip)
    return missing


def valid_window(source, bands, window, rectangles):
    valid = np.all(source.read_masks(bands, window=window) > 0, axis=0)
    if not rectangles:
        return valid
    col, row = int(window.col_off), int(window.row_off)
    height, width = valid.shape
    covered = np.zeros_like(valid)
    for x, y, right, bottom in rectangles:
        left, top = max(0, x - col), max(0, y - row)
        end, base = min(width, right - col), min(height, bottom - row)
        if left < end and top < base:
            covered[top:base, left:end] = True
    return valid & covered
