"""Offline fixtures for land-setting precedence and conservative private counts."""
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

import shapely
from shapely.geometry import box, mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sf_tree_settings import classify, tax_evidence


class LandSettings(unittest.TestCase):
    def classify_fixture(self, points, parks=(), **records):
        with tempfile.TemporaryDirectory() as directory:
            layers = {}
            for name in ("streets", "city_property", "parcels", "roll"):
                path = Path(directory) / f"{name}.jsonl.gz"
                with gzip.open(path, "wt", encoding="utf-8") as stream:
                    for record in records.get(name, []):
                        stream.write(json.dumps(record) + "\n")
                layers[name] = (path, {})
            return classify(shapely.points(points), layers, parks)

    def test_parks_win_street_overlaps_and_public_land_stays_unclassified(self):
        park = {"geometry": mapping(box(0, 0, 1, 1)),
                "properties": {"UNIT_NAME": "Small park", "OBJECTID": 1}}
        larger = {"geometry": mapping(box(0, 0, 1.2, 1)),
                  "properties": {"UNIT_NAME": "Large park", "OBJECTID": 2}}
        settings, parks, private, overlap = self.classify_fixture(
            [(x, .5) for x in (.5, 1.5, 2.5, 3.5, 4.5, 6)], parks=[larger, park],
            streets=[{"the_geom": mapping(box(0, 0, 2, 1))}],
            city_property=[{"shape": mapping(box(3, 0, 4, 1))}],
            parcels=[{"blklot": "res", "shape": mapping(box(1, 0, 5, 1))},
                     {"blklot": "public", "shape": mapping(box(4, 0, 5, 1))}],
            roll=[{"parcel_number": "res", "use_code": "SRES", "assessed_land_value": "100"},
                  {"parcel_number": "public", "property_class_code": "PI", "assessed_land_value": "100"}])
        self.assertEqual(settings.tolist(), ["park", "street", "private", "unclassified", "unclassified", "unclassified"])
        self.assertEqual(dict(parks), {"Small park": 1})
        self.assertEqual(private["residential"], 1)
        self.assertEqual(sum(private.values()), 1)
        self.assertEqual(overlap, 1)

    def test_condo_footprints_golf_split_and_public_veto(self):
        settings, _, private, _ = self.classify_fixture(
            [(x, .5) for x in (.5, 2.5, 4.5, 6.5)],
            parcels=[
                {"blklot": "golf", "shape": mapping(box(0, 0, 1, 1))},
                {"blklot": "home", "mapblklot": "parent", "shape": mapping(box(2, 0, 3, 1))},
                {"blklot": "shop", "mapblklot": "parent", "shape": mapping(box(2, 0, 3, 1))},
                {"blklot": "home", "mapblklot": "parent", "shape": mapping(box(4, 0, 5, 1))},
                {"blklot": "home", "mapblklot": "public", "shape": mapping(box(6, 0, 7, 1))}],
            roll=[{"parcel_number": "golf", "use_code": "COMR", "property_class_code": "GC", "assessed_land_value": "100"},
                  {"parcel_number": "home", "use_code": "MRES", "assessed_land_value": "100"},
                  {"parcel_number": "shop", "use_code": "COMM", "assessed_land_value": "100"},
                  {"parcel_number": "public", "use_code": "GOVT", "assessed_land_value": "100"}])
        self.assertEqual(settings.tolist(), ["private", "private", "private", "unclassified"])
        self.assertEqual(private, {"residential": 1, "commercial or industrial": 0, "golf courses": 1, "mixed": 1})

    def test_tax_value_alone_is_not_private_evidence(self):
        for record in ({}, {"use_code": "SRES", "assessed_land_value": "0"},
                       {"use_code": "OTHER", "assessed_land_value": "100"}):
            with self.subTest(record=record):
                self.assertEqual(tax_evidence(record), "unknown")


if __name__ == "__main__":
    unittest.main()
