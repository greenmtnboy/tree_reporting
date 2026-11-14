"""Ensure the heat backdrop preserves missingness, dates and geographic alignment."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
from affine import Affine
from pyproj import Transformer
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render_boston_correlation_insets import prepare_heat_tiles
from treelines_data import project_lonlat


class HeatInsetTests(unittest.TestCase):
    def test_shared_dates_water_missingness_and_cell_corner_alignment(self):
        transform = Affine(30, 0, 325000, 0, -30, 4690000)
        inverse = Transformer.from_crs(32619, 4326, always_xy=True)
        west, south = inverse.transform(325000, 4689940)
        east, north = inverse.transform(325090, 4690000)
        projection = {"central_longitude": (west+east)/2,
                      "central_latitude": (south+north)/2}
        dates = [f"day{i}" for i in range(6)]
        stack = np.stack([np.full((2, 3), 30.+i) for i in range(5)] + [np.full((2, 3), 999.)])
        stack[2, 0, 0] = np.nan
        water_lon, water_lat = inverse.transform(325075, 4689955)
        water = box(water_lon-.00002, water_lat-.00002, water_lon+.00002, water_lat+.00002)
        meta = {"year": 2025, "dates": dates, "crs": "EPSG:32619",
                "transform": list(transform)[:6], "bbox": [west, south, east, north],
                "grid_m": 30, "native_thermal_sampling_m": 100, "quality": "fixture"}
        row = {"id": "test", "place": "test", "land": box(west-.001, south-.001, east+.001, north+.001),
               "bounds": [-1, 1, -1, 1], "daily_temperature": [30., 31., 32., 33., 34.]}
        with patch("render_boston_correlation_insets.load_heat", return_value=(stack, meta)):
            prepare_heat_tiles([row], {"temperature": meta, "dates": dates[:5]}, projection, water)
        gx, gy, heat = row["heat_grid"]
        self.assertEqual(heat.shape, (2, 3))
        self.assertTrue(np.isnan(heat[0, 0]))  # Do not substitute a four-date mean.
        self.assertTrue(np.isnan(heat[1, 2]))  # Vector water mask is retained.
        np.testing.assert_allclose(heat[np.isfinite(heat)], 32.)  # Sixth date excluded.
        self.assertEqual(gx.shape, (3, 4))
        corner = project_lonlat(*inverse.transform(325000, 4690000), projection)
        np.testing.assert_allclose([gx[0, 0], gy[0, 0]], corner, atol=1e-10)


if __name__ == "__main__":
    unittest.main()
