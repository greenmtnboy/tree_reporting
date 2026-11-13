"""The view selection must prevent reused areas without selecting on outcomes."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boston_heat_disjoint import choose_windows
from boston_tree_heat_local import snapped_view


def test_smaller_windows_keep_whole_cells_and_nearby_centers():
    for x, y in ((325012, 4685999), (325500, 4685500), (0, 0)):
        w, s, e, n = snapped_view(x, y, 2000)
        assert e-w == n-s == 2000
        assert all(v % 500 == 0 for v in (w, s, e, n))
        assert abs((w+e)/2-x) <= 250
        assert abs((s+n)/2-y) <= 250


def test_selection_moves_window_instead_of_reusing_cells_or_replacing_landmark():
    candidates = [
        {'place': 'Newmarket', 'cell_ids': ['a', 'b'], 'offset_m': 0},
        {'place': 'Nubian Square', 'cell_ids': ['b', 'c'], 'offset_m': 0},
        {'place': 'Nubian Square', 'cell_ids': ['c', 'd'], 'offset_m': 500},
        {'place': 'Alternative', 'cell_ids': ['e', 'f'], 'offset_m': 0},
    ]
    chosen = choose_windows(candidates, list('abcdef'), 2)
    assert chosen == [candidates[0], candidates[2]]
    # Outcomes must not steer selection, even if a candidate appears much stronger.
    for i, candidate in enumerate(candidates):
        candidate.update(temperature=20+i*10, spearman=-i/3)
    assert choose_windows(candidates, list('abcdef'), 2) == chosen


def test_selection_rejects_impossible_count_and_duplicate_landmarks():
    same_name = [{'place': 'Newmarket', 'cell_ids': [key], 'offset_m': 0} for key in 'ab']
    with pytest.raises(ValueError, match='Cannot certify'):
        choose_windows(same_name, list('ab'), 2)
    overlap = [{'place': name, 'cell_ids': ['a'], 'offset_m': 0} for name in ('Newmarket', 'Nubian Square')]
    with pytest.raises(ValueError, match='Cannot certify'):
        choose_windows(overlap, ['a'], 2)
