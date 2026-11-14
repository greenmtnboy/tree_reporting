"""Keep geographic and temporal comparisons consistent across the drilldowns."""
from pathlib import Path
import sys
import unittest

import numpy as np
from affine import Affine
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boston_income_heat_data import clear_land_mask
from render_boston_income_heat import sample_heat, select_areas


class IncomeHeatComparisons(unittest.TestCase):
    def test_cloud_water_uncertainty_and_distance_masks(self):
        qa = np.array([0, 1, 2, 4, 8, 16, 32, 128, 2 << 8, 0, 0, 0])
        dn = np.full(12, 44000)
        uncertainty = np.full(12, 100)
        distance = np.full(12, 200)
        uncertainty[9] = 301
        distance[10] = 99
        dn[11] = 0
        self.assertEqual(clear_land_mask(dn, qa, uncertainty, distance).tolist(), [True] + [False]*11)

    def test_temperature_uses_shared_dates_with_equal_day_weight(self):
        # EPSG:4326 here lets the fixture use small, simple square polygons.
        areas = [{"land": box(0, 0, 1, 1)}, {"land": box(1, 0, 2, 1)}]
        stack = np.array([[[20., 30.]], [[22., 32.]], [[24., 34.]], [[50., np.nan]]])
        meta = {"crs": "EPSG:4326", "transform": list(Affine(1, 0, 0, 0, -1, 1))[:6],
                "dates": ["one", "two", "three", "cloudy"]}
        common = sample_heat(areas, stack, meta)
        self.assertEqual(common.tolist(), [True, True, True, False])
        self.assertEqual([a["temperature_c"] for a in areas], [22., 32.])
        self.assertEqual(areas[0]["temperature_dates"], areas[1]["temperature_dates"])

    def test_income_selection_does_not_depend_on_heat_or_trees(self):
        candidates = [{"label": str(i), "income": i*10000, "land_share": 1,
                       "income_land_coverage": 1, "estimated_households": 1000,
                       "temperature_c": 100-i, "tree_density_km2": i % 2} for i in range(1, 12)]
        before = [a["label"] for a in select_areas(candidates)]
        for a in candidates:
            a["temperature_c"] *= -1
            a["tree_density_km2"] *= 999
        self.assertEqual(before, [a["label"] for a in select_areas(candidates)])
        self.assertEqual(before, ["2", "6", "10"])


if __name__ == "__main__":
    unittest.main()
