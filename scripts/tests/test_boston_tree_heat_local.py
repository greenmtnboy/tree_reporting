"""Check view alignment, normalization and small-sample score handling."""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boston_tree_heat_local import association, snapped_view, augment_local, assign_regions


def test_snapped_views_have_25_complete_cells_and_nearby_centers():
    for x, y in ((325012, 4685999), (325500, 4685500), (0, 0)):
        w, s, e, n = snapped_view(x, y)
        assert all(v % 500 == 0 for v in (w, s, e, n))
        assert e-w == n-s == 2500
        assert np.hypot((w+e)/2-x, (s+n)/2-y) <= np.sqrt(2)*250


def test_subtracting_a_common_baseline_preserves_both_correlations():
    density = [1000, 4200, 2500, 5000, 3000, 1200, 2100]
    temperature = np.array([40, 32, 37, 34, 36, 42, 39])
    raw = association(density, temperature)
    centered = association(density, temperature-temperature.mean())
    assert raw["spearman"] == pytest.approx(centered["spearman"])
    assert raw["pearson"] == pytest.approx(centered["pearson"])


def test_insufficient_or_constant_data_has_no_score():
    assert association([1, 2, 3, 4, 5], [5, 4, 3, 2, 1])["spearman"] is None
    assert association([0]*10, list(range(10)))["spearman"] is None
    assert association(list(range(10)), [10]*10)["pearson"] is None


def test_local_baseline_uses_available_land_heat_and_keeps_missingness():
    cells = [{"id": str(i), "west": (i % 5)*500, "south": (i//5)*500,
              "temperature": 20+i, "tree_density": 1000+i*10, "eligible": i != 0}
             for i in range(25)]
    result = {"cells": cells, "examples": [{"place": "Test", "anchor_lonlat": [1250, 1250]}]}
    east, north = np.meshgrid(np.arange(250, 2500, 500), np.arange(250, 2500, 500))
    heat = np.arange(20., 45.).reshape(5, 5)
    heat[0, 0] = np.nan
    data = {"forward": SimpleNamespace(transform=lambda x, y: (x, y)), "east": east,
            "north": north, "mean": heat, "wet": np.zeros((5, 5), dtype=bool),
            "water_utm": Polygon()}
    view = augment_local(result, data)[0]
    assert view["baseline_temperature"] == 32.5
    assert view["baseline_valid_land_fraction"] == 24/25
    assert view["n"] == 24
    assert all(c["temperature_delta"] == c["temperature"]-32.5 for c in view["cells"])
    assert all(c["nearby_water_percent"] == 0 for c in view["cells"])
    assert view["water_heat_association"]["spearman"] is None
    assert view["tree_heat_association"]["spearman"] == pytest.approx(1)
    assert np.isnan(data["mean"][0, 0])


def test_region_colors_use_nearest_covering_view_and_keep_outside_cells():
    views = [{"place": "West", "bounds_utm": [0, 0, 2500, 2500]},
             {"place": "East", "bounds_utm": [1000, 0, 3500, 2500]}]
    cells = [{"id": "western", "west": 1000, "south": 1000},
             {"id": "eastern", "west": 2000, "south": 1000},
             {"id": "tied", "west": 1500, "south": 1000},
             {"id": "outside", "west": 4000, "south": 1000}]
    assert assign_regions(cells, views) == {"western": "West", "eastern": "East",
                                          "tied": "East", "outside": None}
    assert assign_regions(cells, views[::-1]) == assign_regions(cells, views)
