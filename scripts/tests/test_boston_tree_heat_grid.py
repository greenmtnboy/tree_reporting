"""Check spatial assignment and temperature-only selection, using toy cells."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boston_tree_heat_grid import flat_indices, select_pair, cell_eligible


def cell(name, x, y, temperature, density=1000, eligible=True):
    return {"id": name, "west": x, "south": y, "temperature": temperature,
            "tree_density": density, "eligible": eligible}


def test_grid_edges_are_half_open_and_never_double_counted():
    ids, valid = flat_indices(np.array([-.1, 0, 499.9, 500, 999.9, 1000]),
                             np.zeros(6), 0, 0, 2, 2)
    assert valid.tolist() == [False, True, True, True, True, False]
    assert ids[valid].tolist() == [0, 0, 1, 1]
    counts = np.bincount(ids[valid], minlength=4)
    assert counts.tolist() == [2, 2, 0, 0]


def test_grid_north_edges_and_projected_origin():
    ids, valid = flat_indices(np.array([325000, 325000, 325000]),
                             np.array([4685000, 4685500, 4686000]),
                             325000, 4685000, 2, 2)
    assert ids[valid].tolist() == [0, 2]
    assert valid.tolist() == [True, True, False]


def test_pair_excludes_incomplete_and_edge_crossing_cells():
    rows = [cell("cool", 0, 0, 30), cell("hot", 500, 0, 40),
            cell("missing", 0, 500, 100, eligible=False),
            cell("crosses", 750, 500, -10)]
    pair = select_pair(rows, [0, 0, 1000, 1000])
    assert pair["candidate_n"] == 2
    assert pair["cool"]["id"] == "cool"
    assert pair["hot"]["id"] == "hot"


def test_pair_selection_does_not_consult_tree_density():
    rows = [cell("a", 0, 0, 31, 9000), cell("b", 500, 0, 39, 100),
            cell("c", 0, 500, 35, 5000)]
    first = select_pair(rows, [0, 0, 1000, 1000])
    for r in rows:
        r["tree_density"] = -r["tree_density"]
    second = select_pair(rows, [0, 0, 1000, 1000])
    assert [first[k]["id"] for k in ("cool", "hot")] == [second[k]["id"] for k in ("cool", "hot")]


def test_temperature_ties_have_stable_distinct_choices():
    rows = [cell("b", 500, 0, 35), cell("a", 0, 0, 35)]
    pair = select_pair(rows, [0, 0, 1000, 1000])
    assert pair["cool"]["id"] == "a"
    assert pair["hot"]["id"] == "b"
    assert select_pair(rows[::-1], [0, 0, 1000, 1000]) == pair


def test_missing_pairs_fail_instead_of_relaxing_coverage():
    with pytest.raises(ValueError, match="Fewer than two"):
        select_pair([cell("a", 0, 0, 35)], [0, 0, 1000, 1000])


def test_removing_water_cutoff_keeps_land_heat_requirements():
    assert not cell_eligible(.4, .95, 100)
    assert cell_eligible(.4, .95, 100, min_land_fraction=0)
    assert not cell_eligible(.4, .89, 100, min_land_fraction=0)
    assert not cell_eligible(.4, .95, 29, min_land_fraction=0)
    assert not cell_eligible(0, 1, 100, min_land_fraction=0)
