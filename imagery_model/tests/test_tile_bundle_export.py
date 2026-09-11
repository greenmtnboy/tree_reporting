"""The tile bundle export: identity, georeferencing, crown estimates, and the
contract the reviewer refuses."""

from __future__ import annotations

import math

import pytest

from urban_tree_ml import tile_bundle_export as export

# SF's 2022 NAIP mosaic: EPSG:26910, 0.6 m pixels, origin at (538308, 4185438).
RASTER_AFFINE = (0.6, 0.0, 538308.0, 0.0, -0.6, 4185438.0)
CRS = "EPSG:26910"


def test_prediction_id_is_the_output_cell():
    assert export.prediction_id("r000005_c000073", 72, 59) == "r000005_c000073:72:59"
    # Stable across sorting and thresholding: it does not depend on the row.
    assert export.prediction_id("r000005_c000073", 72.0, 59.0) == "r000005_c000073:72:59"


def test_imagery_version_is_a_content_identity():
    manifest = {"year": 2022, "sources": [{"sha256": "aa"}, {"sha256": "bb"}]}
    same_in_another_order = {"year": 2022, "sources": [{"sha256": "bb"}, {"sha256": "aa"}]}
    different = {"year": 2022, "sources": [{"sha256": "aa"}, {"sha256": "cc"}]}
    assert export.imagery_version(manifest) == export.imagery_version(same_in_another_order)
    assert export.imagery_version(manifest) != export.imagery_version(different)
    assert export.imagery_version(manifest).startswith("naip2022-")
    with pytest.raises(ValueError):
        export.imagery_version({"year": 2022, "sources": []})


def test_tile_affine_matches_the_raster_on_a_mosaic_pixel():
    """A tile-local pixel through the tile affine lands where the same mosaic
    pixel lands through the raster affine -- the _add_geography convention,
    with no half-pixel shift."""
    column_offset, row_offset = 18688, 1280
    affine = export.tile_affine(RASTER_AFFINE, column_offset, row_offset)
    output_x, output_y, stride = 72, 59, 2
    x_px, y_px = output_x * stride, output_y * stride
    via_tile = export.apply_affine(affine, x_px, y_px)
    via_raster = export.apply_affine(list(RASTER_AFFINE), column_offset + x_px, row_offset + y_px)
    assert via_tile == pytest.approx(via_raster, abs=1e-9)
    # And the corner of the tile is the mosaic pixel at the chip offset.
    assert export.apply_affine(affine, 0, 0) == pytest.approx(
        export.apply_affine(list(RASTER_AFFINE), column_offset, row_offset)
    )


def test_pixel_to_lonlat_reproduces_the_exported_coordinates():
    """The v5 validation export's first prediction: chip r000005_c000073,
    output (72, 59), stride 2, offsets (18688, 1280) -> (-122.43645, 37.80743)."""
    affine = export.tile_affine(RASTER_AFFINE, 18688, 1280)
    lon, lat = export.pixel_to_lonlat(affine, CRS, 72 * 2, 59 * 2)
    assert lon == pytest.approx(-122.43645323833752, abs=1e-7)
    assert lat == pytest.approx(37.80743286248483, abs=1e-7)


def test_pixel_lonlat_affine_agrees_with_pyproj_across_the_tile():
    affine = export.tile_affine(RASTER_AFFINE, 18688, 1280)
    linear = export.pixel_lonlat_affine(affine, CRS, 256, 256)
    for x, y in ((256, 256), (128, 128), (144, 118), (7, 250)):
        lon, lat = export.pixel_to_lonlat(affine, CRS, x, y)
        lon_l, lat_l = export.apply_affine(linear, x, y)
        # Under a centimetre: 1e-7 degrees is ~1 cm.
        assert lon_l == pytest.approx(lon, abs=1e-7)
        assert lat_l == pytest.approx(lat, abs=1e-7)


def test_tile_bounds_cover_the_tile():
    affine = export.tile_affine(RASTER_AFFINE, 18688, 1280)
    bounds = export.tile_bounds_lonlat(affine, CRS, 256, 256)
    assert bounds["west"] < -122.4364 < bounds["east"]
    assert bounds["south"] < 37.8074 < bounds["north"]
    # 153.6 m square: about 0.0014 degrees of latitude.
    assert bounds["north"] - bounds["south"] == pytest.approx(153.6 / 111_320, rel=0.02)


COEFFICIENT_ROWS = [
    {"level": "genus", "taxon": "Platanus", "family": "Platanaceae", "division": "Angiosperm",
     "fit_level": "genus", "fit_taxon": "Platanus", "n": "500", "b": "0.7", "scale": "0.3",
     "dbh_max_cm": "150"},
    {"level": "genus", "taxon": "Phoenix", "family": "Arecaceae", "division": "Angiosperm",
     "fit_level": "family", "fit_taxon": "Arecaceae", "n": "50", "b": "0.5", "scale": "0.2",
     "dbh_max_cm": "60"},
    {"level": "division", "taxon": "Angiosperm", "family": "", "division": "Angiosperm",
     "fit_level": "division", "fit_taxon": "Angiosperm", "n": "211929", "b": "0.646075",
     "scale": "0.347503", "dbh_max_cm": "385"},
    {"level": "global", "taxon": "all", "family": "", "division": "",
     "fit_level": "global", "fit_taxon": "all", "n": "312339", "b": "0.626633",
     "scale": "0.352756", "dbh_max_cm": "770"},
]


def test_crown_width_applies_the_shared_power_law():
    table = export.CrownCoefficients(COEFFICIENT_ROWS)
    width, fit = table.crown_width_m("Platanus", 10.0)
    assert fit.level == "genus" and fit.taxon == "Platanus"
    assert width == pytest.approx(2 * 0.3 * (10.0 * 2.54) ** 0.7)
    # An unknown genus takes the angiosperm fit, as the prediction parquet does.
    width, fit = table.crown_width_m("Nothingia", 10.0)
    assert fit.level == "division" and fit.taxon == "Angiosperm"
    # Clamped to the fit's largest stem, not extrapolated.
    huge, _ = table.crown_width_m("Platanus", 5000.0)
    assert huge == pytest.approx(2 * 0.3 * 150 ** 0.7)
    # A palm gets no estimate; no diameter, no estimate.
    assert table.crown_width_m("Phoenix", 10.0) is None
    assert table.crown_width_m("Platanus", None) is None
    assert table.crown_width_m("Platanus", 0.0) is None
    assert table.crown_width_m("Platanus", math.nan) is None


def test_candidate_tree_ids_are_suggestions_nearest_first():
    inventory = [
        {"treeId": "far", "longitude": -122.4300, "latitude": 37.8000},
        {"treeId": "near", "longitude": -122.43645, "latitude": 37.80745},
        {"treeId": "nearer", "longitude": -122.436453, "latitude": 37.807433},
        {"treeId": "nowhere", "longitude": None, "latitude": None},
    ]
    ids = export.candidate_tree_ids(-122.43645323833752, 37.80743286248483, inventory, radius_m=6.0)
    assert ids == ["nearer", "near"]


def test_build_prediction_resolves_taxa_with_the_runs_taxonomy():
    affine = export.tile_affine(RASTER_AFFINE, 18688, 1280)
    taxonomy = {"genera": ["Acer", "Platanus"], "species": ["Acer rubrum", "Platanus x hispanica"]}
    row = {
        "chip_id": "r000005_c000073", "output_x": 72, "output_y": 59, "score": 0.41,
        "dbh_in": 7.9, "genus_id": 1, "genus_confidence": 0.47, "species_id": 1,
        "species_confidence": 0.53, "species_top_ids": [1, 0],
    }
    prediction = export.build_prediction(
        row, taxonomy=taxonomy, stride=2, affine=affine, crs=CRS,
        coefficients=export.CrownCoefficients(COEFFICIENT_ROWS), inventory=[],
    )
    assert prediction["predictionId"] == "r000005_c000073:72:59"
    assert (prediction["xPx"], prediction["yPx"]) == (144, 118)
    assert prediction["positionRole"] == "crown_center"
    assert prediction["species"] == "Platanus x hispanica"
    assert prediction["genus"] == "Platanus"
    assert prediction["speciesTop"] == ["Platanus x hispanica", "Acer rubrum"]
    assert prediction["crownWidthMethod"] == export.CROWN_WIDTH_METHOD
    assert prediction["crownFit"]["level"] == "genus"
    assert prediction["longitude"] == pytest.approx(-122.43645323833752, abs=1e-7)
    # An out-of-vocabulary id is unknown, not a wrong name.
    row["species_id"] = -1
    assert export.build_prediction(
        row, taxonomy=taxonomy, stride=2, affine=affine, crs=CRS, coefficients=None, inventory=[],
    )["species"] is None


def test_inventory_position_role_follows_the_source():
    trunk = export.inventory_tree("sf-1", "SF_OPENDATA", "Acer", 10, 37.8, -122.4, None, None, 5.0, "genus", "measured")
    osm = export.inventory_tree("osm-1", "OSM_USSFO", None, None, 37.8, -122.4, None, None, None, "none", None)
    sat = export.inventory_tree("sat-1", "SATELLITE_USSFO", None, None, 37.8, -122.4, None, None, None, "none", None)
    assert (trunk["positionRole"], osm["positionRole"], sat["positionRole"]) == ("trunk", "unknown", "crown_center")
    assert trunk["predictedCrownWidthM"] == 5.0


def test_validate_bundle_refuses_what_the_reviewer_refuses():
    bundle = {
        "schemaVersion": 1, "tileId": "USSFO-naip2022-x-r0_c0", "city": "USSFO",
        "imageryVersion": "naip2022-x", "provider": "naip", "acquisitionStart": "2022-05-18",
        "acquisitionEnd": "2022-05-19", "sourceItemIds": ["a"],
        "image": {"url": "t.png", "sha256": "0" * 64, "width": 256, "height": 256,
                  "resolutionM": 0.6, "crs": CRS, "affine": [0.6, 0, 0, 0, -0.6, 0],
                  "pixelToLonLat": [0, 0, 0, 0, 0, 0]},
        "predictionLayer": {"status": "available", "predictions": [
            {"predictionId": "r0_c0:1:1"}, {"predictionId": "r0_c0:1:1"}]},
        "inventoryVersion": "v", "inventoryTrees": [],
    }
    with pytest.raises(ValueError, match="repeat"):
        export.validate_bundle(bundle)
    bundle["predictionLayer"]["predictions"].pop()
    export.validate_bundle(bundle)
    bundle["schemaVersion"] = 2
    with pytest.raises(ValueError, match="schema version"):
        export.validate_bundle(bundle)
