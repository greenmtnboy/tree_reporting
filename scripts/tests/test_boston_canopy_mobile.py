"""Mobile selection spans the existing views and links only actual observations."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render_boston_canopy_mobile import select_mobile_views


def test_mobile_rank_selection_is_stable_and_ignores_correlation_strength():
    views = []
    for i in range(12):
        views.append({'place': f'Place {i}', 'letter': chr(65+i), 'baseline_temperature': 30+i,
                      'paired_cells': [{'id': f'{i}-{j}', 'temperature': 30+i+j,
                                        'canopy_percent': 20+j} for j in range(3)],
                      'correlation': -1+i/11})
    chosen, audit = select_mobile_views(views[::-1])
    assert [v['place'] for v in chosen] == ['Place 0', 'Place 4', 'Place 7', 'Place 11']
    assert audit['temperature_ranks_one_based'] == [1, 5, 8, 12]
    assert all(v['callout_cell_id'].endswith('-1') for v in chosen)
    assert all(v['callout_cell_id'] in {c['id'] for c in v['paired_cells']} for v in chosen)
    assert all('callout_cell_id' not in v for v in views)
    for v in views:
        v['correlation'] *= -1
    assert [v['place'] for v in select_mobile_views(views)[0]] == [v['place'] for v in chosen]
