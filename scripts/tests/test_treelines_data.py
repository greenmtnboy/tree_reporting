"""Guard inventory-only rendering against silently including other sources."""
from pathlib import Path
import sys
import tempfile
import unittest
import json

import duckdb
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from treelines_data import load_city_points, project_lonlat
from boston_municipal_boundaries import shared_boundaries


class PublishedSourceSelection(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "trees.parquet")
        with duckdb.connect() as con:
            con.execute("""CREATE TABLE fixture(tree_id VARCHAR,city VARCHAR,data_source VARCHAR,
                latitude DOUBLE,longitude DOUBLE,reported_missing BOOLEAN,cluster_id VARCHAR)""")
            con.executemany("INSERT INTO fixture VALUES (?,?,?,?,?,?,?)", [
                ("city", "USSFO", "SF_OPENDATA", 37.77, -122.42, False, "city"),
                ("osm", "USSFO", "OSM_USSFO", 37.77, -122.42, False, None),
                ("model", "USSFO", "PREDICTED_USSFO", 37.77, -122.42, False, None),
                ("reviewed", "USSFO", "SATELLITE_USSFO", 37.77, -122.42, False, None),
                ("missing", "USSFO", "SF_OPENDATA", 37.77, -122.42, True, None),
                ("absorbed", "USSFO", "SF_OPENDATA", 37.77, -122.42, False, "city"),
                ("bad", "USSFO", "SF_OPENDATA", None, -122.42, False, None),
                ("elsewhere", "USBOS", "SF_OPENDATA", 42.3, -71.1, False, None),
            ])
            con.table("fixture").write_parquet(self.path)

    def test_inventory_excludes_other_sources_and_non_survivors(self):
        points, metadata = load_city_points(self.path, "USSFO", ("SF_OPENDATA",), "inventory")
        self.assertEqual(points["tree_id"].tolist(), ["city"])
        self.assertEqual(metadata["excluded_counts_by_source"],
                         {"OSM_USSFO": 1, "PREDICTED_USSFO": 1, "SATELLITE_USSFO": 1})

    def test_non_aerial_and_all_are_explicit_opt_ins(self):
        points, _ = load_city_points(self.path, "USSFO", ("SF_OPENDATA",), "non-aerial")
        self.assertEqual(set(points["tree_id"]), {"city", "osm"})
        points, _ = load_city_points(self.path, "USSFO", ("SF_OPENDATA",), "all")
        self.assertEqual(set(points["tree_id"]), {"city", "osm", "model", "reviewed"})

    def test_empty_inventory_is_an_error_not_a_fallback_to_all(self):
        with self.assertRaises(ValueError):
            load_city_points(self.path, "USSFO", ("UNKNOWN",), "inventory")


class MapGeometry(unittest.TestCase):
    def test_shared_borders_omit_coast_and_island_perimeters(self):
        borders = shared_boundaries([("A", box(0, 0, 1, 1)),
                                     ("B", box(1, 0, 2, 1)),
                                     ("Island", box(4, 4, 5, 5))])
        self.assertAlmostEqual(borders.length, 1)
        self.assertEqual(borders.bounds, (1, 0, 1, 1))

    def test_sf_viewport_contains_the_entire_circular_fade(self):
        config = json.loads((Path(__file__).resolve().parents[1] / "treelines_cities/ussfo.json").read_text())
        lon, lat = config["projection_center_lonlat"]
        projection = {"central_longitude": lon, "central_latitude": lat}
        west, south, east, north = config["viewport_lonlat"]
        xmin, ymin = project_lonlat(west, south, projection)
        xmax, ymax = project_lonlat(east, north, projection)
        cx, cy = project_lonlat(*config["circle_center_lonlat"], projection)
        fade_end = config["circle_radius_km"] * 1.03
        self.assertGreater(cx - fade_end, xmin)
        self.assertLess(cx + fade_end, xmax)
        self.assertGreater(cy - fade_end, ymin)
        self.assertLess(cy + fade_end, ymax)
        self.assertAlmostEqual(xmax-xmin, 20)
        self.assertAlmostEqual(ymax-ymin, 20)


if __name__ == "__main__":
    unittest.main()
