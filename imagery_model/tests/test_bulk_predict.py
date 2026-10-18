import json

import pyarrow.parquet as pq

from urban_tree_ml import bulk_predict
from urban_tree_ml.bulk_predict import (
    STAGED_COLUMNS,
    dilate,
    drop_seam_duplicates,
    prediction_id,
    rows_from_bundle,
    tile_centre,
    tile_index,
    utm_epsg,
)
from urban_tree_ml.point_predict import grid_tile_origin


def bundle(tile="USBOS-naip2023-abc-p1", chip="p1", predictions=(), run="run-v13", city="USBOS"):
    return {
        "tileId": tile, "chipId": chip, "city": city, "imageryVersion": "naip2023-abc",
        "predictionLayer": {"runId": run, "predictions": list(predictions)},
    }


def prediction(pid, lat, lon, score, genus="Acer", species="Acer rubrum", crown=5.0, method="model_crown_head"):
    return {
        "predictionId": pid, "latitude": lat, "longitude": lon, "centerConfidence": score,
        "genus": genus, "species": species, "modelCrownWidthM": crown, "modelCrownWidthMethod": method,
    }


def test_boston_is_utm_19():
    assert utm_epsg(-71.06) == 26919
    assert utm_epsg(-122.42) == 26910


def test_a_planned_tile_is_the_tile_point_predict_cuts():
    """The centre sits on point_predict's half-tile lattice, so asking for it
    returns the planned tile, not a neighbour half a tile over."""
    tile_m = 153.6
    for index in [(2123, 30532), (-4, 7)]:
        cx, cy = tile_centre(index, tile_m)
        west, north = grid_tile_origin(cx, cy, tile_m)
        assert abs(west - index[0] * tile_m) < 1e-6
        assert abs(north - (index[1] + 1) * tile_m) < 1e-6
        assert tile_index(cx, cy, tile_m) == index


def test_dilate_adds_a_ring():
    assert dilate({(0, 0)}) == {(i, j) for i in (-1, 0, 1) for j in (-1, 0, 1)}
    assert len(dilate({(0, 0), (1, 0)})) == 12


def test_ids_are_stable_and_prefixed():
    a = prediction_id("run", "naip2023-abc", "p1", "p1:10:12")
    assert a == prediction_id("run", "naip2023-abc", "p1", "p1:10:12")
    assert a.startswith("pred-") and len(a) == 25
    assert a != prediction_id("run-v14", "naip2023-abc", "p1", "p1:10:12")


def test_rows_carry_only_a_model_crown_head_width():
    rows = rows_from_bundle(bundle(predictions=[
        prediction("a", 42.3, -71.1, 0.5),
        prediction("b", 42.31, -71.1, 0.4, method="tallo_genus"),
    ]))
    assert [r["crown_width_m"] for r in rows] == [5.0, None]
    assert rows[0]["genus"] == "Acer" and rows[0]["score"] == 0.5
    assert {"run_id", "imagery_version", "_tile"} <= set(rows[0])


def test_seam_duplicates_across_tiles_keep_the_higher_score():
    nudge = 0.000009  # ~1 m of latitude
    rows = rows_from_bundle(bundle(tile="t1", predictions=[prediction("a", 42.3, -71.1, 0.4)]))
    rows += rows_from_bundle(bundle(tile="t2", predictions=[prediction("b", 42.3 + nudge, -71.1, 0.6)]))
    kept = drop_seam_duplicates(rows, radius_m=2.0)
    assert [r["_tile"] for r in kept] == ["t2"]


def test_two_detections_of_one_tile_are_left_alone():
    nudge = 0.000009
    rows = rows_from_bundle(bundle(tile="t1", predictions=[
        prediction("a", 42.3, -71.1, 0.4), prediction("b", 42.3 + nudge, -71.1, 0.6),
    ]))
    assert len(drop_seam_duplicates(rows, radius_m=2.0)) == 2


def test_far_apart_detections_in_different_tiles_both_stay():
    rows = rows_from_bundle(bundle(tile="t1", predictions=[prediction("a", 42.3, -71.1, 0.4)]))
    rows += rows_from_bundle(bundle(tile="t2", predictions=[prediction("b", 42.3001, -71.1, 0.6)]))
    assert len(drop_seam_duplicates(rows, radius_m=2.0)) == 2


def test_assemble_from_bundles_writes_the_staged_contract(tmp_path):
    tiles = tmp_path / "tiles"
    tiles.mkdir()
    (tiles / "a.json").write_text(json.dumps(bundle(predictions=[
        prediction("keep", 42.33, -71.08, 0.5),
        prediction("low", 42.331, -71.08, 0.2),
        prediction("outside", 40.7, -73.9, 0.9),  # not in Boston's territory
    ])))
    (tiles / "a.png").write_bytes(b"")  # bundles sit beside their images
    out = tmp_path / "out"
    assert bulk_predict.main(["assemble", "--out", str(out), "--threshold", "0.35", "--from-bundles", str(tiles)]) == 0
    table = pq.read_table(out / "usbos_predicted_staging.parquet")
    assert tuple(table.column_names) == STAGED_COLUMNS
    rows = table.to_pylist()
    assert len(rows) == 1 and rows[0]["tree_id"].startswith("pred-") and rows[0]["city"] == "USBOS"


def test_the_staged_contract_is_the_ingest_contract():
    """data/raw/predicted_tree_info.py documents the columns it reads."""
    from pathlib import Path

    ingest = (Path(__file__).resolve().parents[2] / "data" / "raw" / "predicted_tree_info.py").read_text(encoding="utf-8")
    for column in STAGED_COLUMNS:
        assert f"    {column} " in ingest, column
