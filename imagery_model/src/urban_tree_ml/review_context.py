"""Neighboring imagery for registration; source coordinates and chip ownership stay unchanged."""
import io
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode
from xml.etree import ElementTree

import numpy as np

CONTEXT_PIXELS = 16


def display_grid(raster):
    """Use a verified finer raster covering exactly the review footprint."""
    path = Path(raster).resolve()
    if path.suffix.lower() != '.vrt':
        return path, 1
    companion = path.with_suffix('.native.vrt')
    if companion.is_file():
        return _direct_display_grid(path, companion, path.stat().st_mtime_ns, companion.stat().st_mtime_ns)
    native = path.with_name('imagery-30cm.vrt')
    if path.name != 'imagery.vrt' or not native.is_file():
        return path, 1
    return _display_grid(path, native, path.stat().st_mtime_ns, native.stat().st_mtime_ns)


@lru_cache(maxsize=32)
def _direct_display_grid(path, native, review_version, native_version):
    import rasterio
    from urban_tree_ml.resolution_recipe import validate_resolution_derivative

    # A direct mosaic describes each source window separately. Verify every band,
    # placement and original file before using the higher-resolution display.
    validate_resolution_derivative(path, native)
    with rasterio.open(path) as review, rasterio.open(native) as detail:
        scale = detail.width // review.width
        if scale <= 1 or (detail.width, detail.height) != (review.width*scale, review.height*scale):
            raise ValueError('Native display mosaic must refine the entire review grid')
    return native, scale


@lru_cache(maxsize=32)
def _display_grid(path, native, review_version, native_version):
    import rasterio
    from affine import Affine

    source = ElementTree.parse(path).find('GDALWarpOptions/SourceDataset')
    if source is None or not source.text:
        return path, 1
    parent = path.parent if source.get('relativeToVRT') == '1' else Path.cwd()
    if (parent / source.text).resolve() != native:
        return path, 1
    with rasterio.open(path) as review, rasterio.open(native) as detail:
        scale, remainder = divmod(detail.width, review.width)
        if (remainder or scale <= 1 or detail.height != scale * review.height
                or review.crs != detail.crs or review.count != detail.count
                or not review.transform.almost_equals(detail.transform * Affine.scale(scale), 1e-8)):
            return path, 1
    return native, scale


def scene_image_urls(context, scene, preview):
    params = {'city': context.city, 'scene': scene['scene_id']}
    if acquisition := getattr(context, 'acquisition_id', None):
        params['acquisition'] = acquisition
    url = '/api/review-context?' + urlencode(params)
    return (url + '&border=0' if display_grid(context.raster)[1] > 1 else preview), url


def scene_context_image(context, scoped, border=CONTEXT_PIXELS):
    from pyproj import Transformer
    import rasterio

    scene = scoped['scenes'][0]
    anchor = scene.get('anchor') or next(iter(scoped['samples']), None)
    if anchor is None:
        raise ValueError('Scene has no geographic anchor for neighboring imagery')
    raster = Path(context.raster).resolve()
    with rasterio.open(raster) as source:
        basis = tuple(anchor[k] for k in ('transform_a', 'transform_b', 'transform_d', 'transform_e'))
        actual = (source.transform.a, source.transform.b, source.transform.d, source.transform.e)
        if not np.allclose(basis, actual, atol=1e-8, rtol=1e-8):
            raise ValueError('Scene and source imagery pixel grids differ')
        project = Transformer.from_crs('EPSG:4326', source.crs, always_xy=True)
        col, row = (~source.transform) * project.transform(anchor['longitude'], anchor['latitude'])
    col -= anchor['target_x']
    row -= anchor['target_y']
    if abs(col-round(col)) > 1e-4 or abs(row-round(row)) > 1e-4:
        raise ValueError('Scene is not aligned to source pixels')
    return review_window_image(context, round(col), round(row), int(scene['image_width']),
                               int(scene['image_height']), border)


def review_window_image(context, col, row, width, height, border):
    if border not in (0, CONTEXT_PIXELS):
        raise ValueError('Review image border must be 0 or 16 pixels')
    raster, scale = display_grid(context.raster)
    stat = raster.stat()
    return _read_context(str(raster), stat.st_mtime_ns, stat.st_size, (col-border)*scale,
                         (row-border)*scale, (width+2*border)*scale, (height+2*border)*scale,
                         tuple(context.config.imagery.bands), context.config.imagery.input_scale)


@lru_cache(maxsize=32)
def _read_context(path, mtime, size, col, row, width, height, bands, input_scale):
    import rasterio
    from rasterio.windows import Window
    from PIL import Image
    from urban_tree_ml.quality import _rgb_preview

    window = Window(col, row, width, height)
    with rasterio.open(path) as source:
        raw = source.read(bands, window=window, boundless=True, fill_value=0)
        valid = np.all(source.read_masks(bands, window=window, boundless=True) > 0, axis=0)
    # Missing photograph coverage is transparent, never stretched or repeated.
    rgba = np.dstack((_rgb_preview(raw, input_scale), valid.astype(np.uint8)*255))
    output = io.BytesIO()
    Image.fromarray(rgba).save(output, format='PNG')
    return output.getvalue()
