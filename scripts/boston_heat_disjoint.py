"""Select twelve non-overlapping 2 km landmark windows using coverage and geography."""
import json
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

from boston_canopy_grid import matched_comparison
from boston_heat_location_audit import CHOSEN, coverage_summary, summarize_set
from boston_tree_heat_grid import CELL_M
from boston_tree_heat_local import augment_local

VIEW_M = 2000
MIN_PAIRED = 13  # At least 80% of 16 cells, rounded up; after majority-water filtering.
MAX_OFFSET_M = 1000


def choose_windows(candidates, cell_ids, count=12):
    """One window per landmark, with no shared cells; minimize replacement and shift."""
    names = sorted({c['place'] for c in candidates})
    cell_index = {key: i for i, key in enumerate(cell_ids)}
    name_index = {name: len(cell_index)+i for i, name in enumerate(names)}
    matrix = lil_matrix((len(cell_index)+len(names)+1, len(candidates)))
    for j, candidate in enumerate(candidates):
        for key in candidate['cell_ids']:
            matrix[cell_index[key], j] = 1
        matrix[name_index[candidate['place']], j] = 1
        matrix[-1, j] = 1
    low = np.zeros(matrix.shape[0])
    high = np.ones(matrix.shape[0])
    low[-1] = high[-1] = count
    # A replacement costs more than all twelve possible shifts together.
    costs = [(0 if c['place'] in CHOSEN else count+1)
             +(c['offset_m']/MAX_OFFSET_M)**2 for c in candidates]
    solved = milp(costs, integrality=np.ones(len(candidates)), bounds=Bounds(0, 1),
                  constraints=LinearConstraint(matrix.tocsc(), low, high),
                  options={'time_limit': 45, 'mip_rel_gap': 0})
    if not solved.success:
        raise ValueError(f'Cannot certify {count} disjoint windows: {solved.message}')
    selected = [c for c, value in zip(candidates, solved.x) if value > .5]
    ids = [key for c in selected for key in c['cell_ids']]
    if len(selected) != count or len(ids) != len(set(ids)):
        raise AssertionError('Disjoint selection failed validation')
    return selected


def disjoint_locations(result, data, original):
    catalog = json.loads(Path(__file__).with_name('boston_heat_location_candidates.json').read_text(encoding='utf-8'))
    anchors = {v['place']: v['anchor_lonlat'] for v in original}
    anchors.update({v['place']: v['anchor_lonlat'] for v in catalog['candidates']})
    lookup = {(c['west'], c['south']): c for c in result['cells']}
    # Count actual raster samples; summing per-cell counts reproduces view coverage.
    xs = np.floor(data['east']/CELL_M).astype(int)
    ys = np.floor(data['north']/CELL_M).astype(int)
    x0, y0 = xs.min(), ys.min()
    nx = xs.max()-x0+1
    pixel_ids = (ys-y0)*nx+xs-x0
    land = np.bincount(pixel_ids[~data['wet']], minlength=int(pixel_ids.max())+1)
    valid = np.bincount(pixel_ids[np.isfinite(data['mean'])], minlength=len(land))
    candidates = []
    for place, anchor in sorted(anchors.items()):
        ae, an = data['forward'].transform(*anchor)
        for w in np.arange(np.floor((ae-VIEW_M)/CELL_M)*CELL_M, ae+1, CELL_M):
            for s in np.arange(np.floor((an-VIEW_M)/CELL_M)*CELL_M, an+1, CELL_M):
                offset = float(np.hypot(w+VIEW_M/2-ae, s+VIEW_M/2-an))
                if offset > MAX_OFFSET_M or not (w <= ae < w+VIEW_M and s <= an < s+VIEW_M):
                    continue
                cells = [lookup.get((w+i*CELL_M, s+j*CELL_M)) for i in range(4) for j in range(4)]
                if any(c is None for c in cells):
                    continue
                good = [c for c in cells if c['eligible'] and c['land_fraction'] >= .5
                        and c.get('crown_completeness', 0) >= .95
                        and c.get('canopy_percent') is not None and np.isfinite(c['canopy_percent'])]
                indexes = [int((c['south']/CELL_M-y0)*nx+c['west']/CELL_M-x0) for c in cells]
                denominator = land[indexes].sum()
                coverage = float(valid[indexes].sum()/denominator) if denominator else 0
                if len(good) >= MIN_PAIRED and coverage >= .9:
                    candidates.append({'place': place, 'anchor_lonlat': anchor,
                        'bounds_utm': [float(w), float(s), float(w+VIEW_M), float(s+VIEW_M)],
                        'cell_ids': [c['id'] for c in cells], 'offset_m': offset,
                        'heat_coverage': coverage, 'paired_n': len(good)})
    selected = choose_windows(candidates, [c['id'] for c in result['cells']])
    views = augment_local(result, data, anchors={c['place']: c['anchor_lonlat'] for c in selected},
                          view_m=VIEW_M, bounds_by_place={c['place']: c['bounds_utm'] for c in selected})
    for v in views:
        paired, scores = matched_comparison([c for c in v['cells'] if c['land_fraction'] >= .5])
        v.update(paired_cells=paired, paired_n=len(paired), paired_associations=scores)
        if v['paired_n'] < MIN_PAIRED or v['baseline_valid_land_fraction'] < .9:
            raise AssertionError('Selected window does not reproduce coverage thresholds')
    result['view_size_m'] = VIEW_M
    result['location_audit'] = {
        'anchor_sources': catalog,
        'selection': 'Twelve non-overlapping 2 km windows; >=90% common-five-date land heat, >=13/16 qualifying canopy/heat cells after >50% water exclusion. Each window contains its named landmark and center is within 1 km. First minimize replacements of previous landmarks, then total squared center offsets. Neither heat means nor correlations are selection criteria.',
        'bias': 'Tiles selected for available heat coverage; not a geographically representative sample. Disjoint views can still be spatially correlated.',
        'before': summarize_set(original), 'after': summarize_set(views),
        'candidate_count': len(candidates), 'candidates': candidates,
        'chosen': [coverage_summary(v) for v in views], 'selected_windows': selected,
        'shared_cell_count': 0, 'max_center_offset_m': MAX_OFFSET_M,
        'unchanged': 'Metro scatter sample, heat dates, per-cell quality requirements and measurements unchanged. Local windows, baselines and correlations recomputed.',
    }
    result['local_method']['landmark_selection'] = result['location_audit']['selection']
    result['local_method']['interpretation'] = 'No shared cells between views; local samples remain small and spatially correlated. Subtracting a common baseline does not change within-view correlations.'
    print('Disjoint selection:', [(v['place'], v['paired_n'], round(v['baseline_valid_land_fraction'],3)) for v in views], flush=True)
    return views
