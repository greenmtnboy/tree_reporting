import io
from types import SimpleNamespace
from xml.etree import ElementTree

import numpy as np
import rasterio
from PIL import Image
from affine import Affine
from rasterio.shutil import copy as copy_raster
from rasterio.vrt import WarpedVRT

from urban_tree_ml.review_context import display_grid, review_window_image
from urban_tree_ml.resolution_recipe import finer_vrt
import pytest


def test_native_review_pixels_and_neighbor_alignment(tmp_path):
    native = tmp_path / "imagery-30cm.vrt"
    review = tmp_path / "imagery.vrt"
    transform = Affine(0.3, 0, 320000, 0, -0.3, 4310000)
    # One-pixel stripes disappear if the review grid is read and enlarged.
    pixels = np.tile(np.arange(160, dtype=np.uint8) % 2 * 255, (160, 1))
    with rasterio.open(tmp_path / "photo.tif", "w", driver="GTiff", height=160, width=160,
                       count=3, dtype="uint8", transform=transform, crs="EPSG:32618") as photo:
        for band in (1, 2, 3):
            photo.write(pixels, band)
    copy_raster(tmp_path / "photo.tif", native, driver="VRT")
    with rasterio.open(native) as source, WarpedVRT(
        source, transform=transform * Affine.scale(2), width=80, height=80
    ) as view:
        copy_raster(view, review, driver="VRT")
    context = SimpleNamespace(raster=review, config=SimpleNamespace(
        imagery=SimpleNamespace(bands=(1, 2, 3), input_scale=1/255)))
    def read(border):
        return np.array(Image.open(io.BytesIO(review_window_image(context, 20, 18, 32, 32, border))))
    core = read(0)
    np.testing.assert_array_equal(core[:, :, 0], pixels[36:100, 40:104])
    np.testing.assert_array_equal(read(16)[32:-32, 32:-32], core)

    # An unrelated native mosaic beside an ordinary review VRT is never substituted.
    copy_raster(tmp_path / "photo.tif", review, driver="VRT")
    assert display_grid(review) == (review, 1)


def test_direct_mosaic_native_display_checks_source_placement(tmp_path):
    photo = tmp_path / 'source.tif'
    review = tmp_path / 'city.vrt'
    native = review.with_suffix('.native.vrt')
    transform = Affine(0.3, 0, 320000, 0, -0.3, 4310000)
    with rasterio.open(photo, 'w', driver='GTiff', height=16, width=16,
                       count=1, dtype='uint8', transform=transform, crs='EPSG:32618') as out:
        out.write(np.arange(256, dtype='uint8').reshape(16, 16), 1)
    copy_raster(photo, review, driver='VRT')
    doc = ElementTree.parse(review)
    doc.getroot().attrib.update(rasterXSize='8', rasterYSize='8')
    doc.find('GeoTransform').text = ','.join(map(str, (transform * Affine.scale(2)).to_gdal()))
    doc.find('.//DstRect').attrib.update(xSize='8', ySize='8')
    doc.write(review)
    finer_vrt(review, native)
    assert display_grid(review) == (native, 2)
    with rasterio.open(photo) as source, rasterio.open(native) as detail:
        np.testing.assert_array_equal(source.read(), detail.read())
    doc = ElementTree.parse(native)
    doc.find('.//SrcRect').set('xOff', '1')
    doc.write(native)
    with pytest.raises(ValueError, match='verified resolution-only derivative'):
        display_grid(review)
