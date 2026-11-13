"""Scientific invariants for estimated crown footprint, independent of real data."""
from pathlib import Path
import sys
import unittest

import numpy as np
import shapely
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_boston_canopy_heat import crown_cover, comparison


class CrownAreaTests(unittest.TestCase):
    def test_identical_crowns_count_once_and_approximate_circle_area(self):
        land = box(-20, -20, 20, 20)
        one, _ = crown_cover(land, shapely.points([0], [0]), np.array([10.]), .25)
        two, _ = crown_cover(land, shapely.points([0, 0], [0, 0]), np.array([10., 10.]), .25)
        self.assertEqual(one, two)
        self.assertAlmostEqual(one, np.pi*100, delta=2)

    def test_crown_outside_boundary_contributes_only_intersection(self):
        land = box(0, -20, 20, 20)
        centers = shapely.points([-2], [0])
        area, raster_land = crown_cover(land, centers, np.array([10.]), .25)
        analytic = shapely.buffer(centers[0], 10., quad_segs=128).intersection(land).area
        self.assertGreater(area, 0)
        self.assertAlmostEqual(area, analytic, delta=2)
        self.assertEqual(raster_land, 800.)

    def test_water_holes_and_missing_widths_remain_uncovered(self):
        land = box(-10, -10, 10, 10).difference(box(-4, -4, 4, 4))
        area, raster_land = crown_cover(land, shapely.points([0, 8], [0, 8]), np.array([4., np.nan]), .25)
        self.assertEqual(area, 0)
        self.assertEqual(raster_land, 336.)

    def test_comparison_uses_identical_rows_for_both_metrics(self):
        rows = [{"tree_density": x, "canopy_percent": x*x, "summed_crown_percent": x*x,
                 "temperature": 50-x*x} for x in range(1, 10)]
        result = comparison(rows)
        self.assertEqual(result["tree_density"]["n"], result["canopy_percent"]["n"])
        self.assertAlmostEqual(result["canopy_percent"]["pearson"], -1)
        self.assertGreater(abs(result["canopy_percent"]["pearson"]), abs(result["tree_density"]["pearson"]))


if __name__ == "__main__":
    unittest.main()
