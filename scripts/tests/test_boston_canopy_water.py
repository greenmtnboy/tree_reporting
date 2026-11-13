"""Guard water threshold direction and consistency across chart samples."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boston_canopy_water import apply_water_exclusion, is_majority_water


class WaterExclusionTests(unittest.TestCase):
    def test_half_water_retained_and_majority_water_excluded(self):
        self.assertFalse(is_majority_water({"land_fraction": .5}))
        self.assertFalse(is_majority_water({"land_fraction": 1.}))
        self.assertTrue(is_majority_water({"land_fraction": .499}))
        self.assertTrue(is_majority_water({"land_fraction": 0.}))

    def test_metro_and_local_samples_filter_identically_without_changing_baseline(self):
        cells = [{"id": str(i), "land_fraction": .49 if i==8 else .5, "eligible": True,
                  "crown_completeness": 1., "canopy_percent": i*4., "tree_density": i*100,
                  "temperature": 45.-i} for i in range(9)]
        result = {"cells": cells, "canopy_comparison": {"paired_n": 9,
                  "paired_cell_ids": [c["id"] for c in cells], "associations": {}, "sample": "test"}}
        views = [{"cells": [dict(c) for c in cells], "all_cell_ids": [c["id"] for c in cells],
                  "baseline_temperature": 40., "paired_n": 9}]
        rows = apply_water_exclusion(result, views)
        self.assertEqual(len(rows), 8)
        self.assertEqual(result["canopy_comparison"]["paired_n"], 8)
        self.assertEqual(views[0]["paired_n"], 8)
        self.assertEqual(views[0]["baseline_temperature"], 40.)
        self.assertEqual(views[0]["water_excluded_cell_ids"], ["8"])
        self.assertEqual(result["water_filter"]["excluded_metro_cell_ids"], ["8"])


if __name__ == "__main__":
    unittest.main()
