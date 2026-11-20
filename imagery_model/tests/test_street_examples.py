import io
import json
import sqlite3

import pytest
from PIL import Image

from urban_tree_ml import street_examples as street


def test_curator_crop_saved_with_stable_identity(tmp_path, monkeypatch):
    image = io.BytesIO()
    Image.new("RGB", (320, 240), "green").save(image, "JPEG")
    monkeypatch.setattr(street, "photo", lambda key: ({"id": key, "creator": {"username": "mapper"},
                        "computed_geometry": {"coordinates": [-122.4199, 37.7701]},
                        "computed_compass_angle": 0}, image.getvalue()))
    example = {"city": "ussfo", "scene": "sf-block", "tree": "sf-1", "image_id": "123",
               "latitude": 37.77, "longitude": -122.42, "crop": [10, 20, 60, 170],
               "species": "Quercus agrifolia", "label_source": "inventory"}
    for _ in range(2):
        street.save(tmp_path, "ussfo", example)
    with sqlite3.connect(tmp_path / "street-species/examples.sqlite3") as db:
        [(label, provenance, jpeg)] = db.execute("select species, provenance, jpeg from examples")
    assert label == "Quercus agrifolia"
    assert Image.open(io.BytesIO(jpeg)).size == (50, 150)
    record = json.loads(provenance)
    assert record["image"]["creator"]["username"] == "mapper"
    assert record["camera"] == {"lat": 37.7701, "lng": -122.4199, "heading": 0,
                                "position_source": "computed_geometry", "is_pano": False}
    assert (record["latitude"], record["longitude"]) == (37.77, -122.42)
    assert record["tree"] == "sf-1" and record["label_source"] == "inventory"
    for bad in ([0, 0, 500, 500], [20, 20, 10, 50], None):
        with pytest.raises(ValueError):
            street.save(tmp_path, "ussfo", dict(example, crop=bad))


def test_nearby_filters_frames_facing_away(monkeypatch):
    class Response:
        def json(self):
            return {"data": [
                {"id": "1", "geometry": {"coordinates": [0, -0.0001]}, "compass_angle": 0},
                {"id": "2", "geometry": {"coordinates": [0, -0.0001]}, "computed_compass_angle": 180},
                {"id": "3", "geometry": {"coordinates": [0, -0.0002]}, "is_pano": True},
            ]}
    monkeypatch.setattr(street, "mapillary_get", lambda url, params: Response())
    frames = street.nearby(0, 0)
    assert [f["id"] for f in frames] == ["1", "3"]
    assert frames[0]["camera"] == {"lat": -0.0001, "lng": 0, "heading": 0,
                                   "position_source": "geometry", "is_pano": False}
    assert frames[1]["camera"]["heading"] is None and frames[1]["camera"]["is_pano"]
    with pytest.raises(ValueError):
        street.nearby(float("nan"), 0)


def test_suggestions_use_only_selected_pixels(monkeypatch):
    monkeypatch.setenv("PLANTNET_API_KEY", "fixture")
    monkeypatch.setattr(street, "cropped", lambda payload: ({}, (200, 100), b"cropped"))
    class Match:
        ok = True
        status_code = 200
        def json(self):
            return {"results": [{"species": {"scientificNameWithoutAuthor": "Acer rubrum"}, "score": 0.7}]}
    def post(url, **kwargs):
        assert kwargs["files"]["images"][1] == b"cropped"
        assert kwargs["params"]["api-key"] == "fixture"
        return Match()
    monkeypatch.setattr(street.requests, "post", post)
    assert street.identify({}) == [{"species": "Acer rubrum", "score": 0.7}]
