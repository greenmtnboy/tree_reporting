"""Measured horizontal crown radius helpers; no allometric pseudo-labels."""
import numpy as np
import csv
import json
from pathlib import Path


def assign_crown_labels(frame, regions, taxonomy, *, estimated=True):
    """Human radius wins; allometric radius supplies weak supervision only."""
    table = {(r['level'], r['taxon']): r for r in csv.DictReader(
        Path(__file__).with_name('crown_width_coefficients.csv').open(encoding='utf-8'))}
    overrides = {str(r['tree_id']): r for r in regions if r.get('tree_id') and r['mode']=='confirmed-tree'}
    frame = frame.copy()
    for column, default in [('crown_radius_m', np.nan), ('crown_source', ''), ('crown_weight', 0.)]:
        if column not in frame: frame[column] = default
    species = {name: i for i, name in enumerate(taxonomy['species'])}
    genera = {name: i for i, name in enumerate(taxonomy['genera'])}
    for i, row in frame.iterrows():
        if str(row.tree_id).startswith('manual-'):
            name = str(row.get('species', '') or '')
            genus = name.split()[0] if name else ''
            frame.loc[i, ['species_id','species_eligible']] = [species.get(name,-1), name in species]
            frame.loc[i, ['genus_id','genus_eligible']] = [genera.get(genus,-1), genus in genera]
        override = overrides.get(str(row.tree_id))
        if override and str(row['split']) in override['splits']:
            frame.loc[i, ['crown_radius_m','crown_source','crown_weight']] = [override['radius_m'],'human',1.]
            continue
        if row.get('crown_source') == 'human':
            frame.loc[i, 'crown_weight'] = 1.
            continue
        if not estimated or not row.get('dbh_eligible', False) or not np.isfinite(row.dbh_log1p): continue
        name = str(row.get('species', '') or '')
        genus = name.split()[0] if name else ''
        fit = table.get(('genus',genus))
        if row.get('tree_form')=='palm' or name in {'Palm','Cactus'} or (fit and fit['family']=='Arecaceae'): continue
        fit = fit or table.get(('global','all'))
        if not fit: continue
        cm = np.clip(np.expm1(row.dbh_log1p)*2.54,1,float(fit['dbh_max_cm']))
        radius = float(fit['scale'])*cm**float(fit['b'])
        if np.isfinite(radius) and radius>0:
            frame.loc[i, ['crown_radius_m','crown_source','crown_weight']] = [radius,'allometric',.2]
    return frame


def validate_warm_start_keys(expected, saved):
    missing, extra = expected - saved, saved - expected
    crown = {key for key in expected if key.startswith('network.crown_head.')}
    if extra or (missing and missing != crown):
        raise ValueError('Checkpoint mismatch outside a complete new crown head')


def crown_metrics(predictions, truth, matches):
    errors = []
    for pred_index, truth_index, _ in matches:
        if truth.loc[truth_index].get('crown_source', 'human') != 'human': continue
        actual = truth.loc[truth_index].get('crown_radius_m')
        predicted = predictions.loc[pred_index].get('crown_radius_m')
        if actual is not None and predicted is not None and np.isfinite(actual) and np.isfinite(predicted) and actual > 0:
            errors.append(float(predicted - actual))
    if not errors:
        return None
    errors = np.asarray(errors)
    return dict(samples=len(errors), radius_mae_m=float(np.abs(errors).mean()),
                diameter_mae_m=float(2*np.abs(errors).mean()), radius_bias_m=float(errors.mean()))
