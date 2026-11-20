"""Verify NYC cold-start coverage and report frozen-label species diagnostics."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from evaluate_nyc_cold_start import PARENT, RUN, CHECKPOINT_SHA


def main(root, report):
    inputs = root / 'run-inputs' / RUN
    evaluations = root / 'runs' / PARENT / 'evaluation'
    taxonomy = json.loads((evaluations / 'validation/taxonomy.json').read_text())
    audit = json.loads((inputs / 'species-coverage.json').read_text())
    with (root / 'runs' / PARENT / 'checkpoints/007.ckpt').open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == CHECKPOINT_SHA
    cohorts = {}
    for name, expected in [('train-usnyc', 200), ('validation-usnyc', 100), ('train-usnyc-central-park', 189)]:
        directory = evaluations / name
        metrics = json.loads((directory / 'metrics.json').read_text())
        coverage = json.loads((directory / 'inference-coverage.json').read_text())
        manifest = pd.read_parquet(root / 'chips' / metrics['dataset'] / 'chips.parquet')
        expected_ids = set(manifest.loc[manifest.split == metrics['split'], 'chip_id'])
        assert coverage['complete'] and coverage['chip_count'] == expected
        assert expected_ids == {c['chip_id'] for c in coverage['chips']}
        assert json.loads((directory / 'taxonomy.json').read_text()) == taxonomy
        norm = json.loads((root / 'chips' / metrics['dataset'] / 'normalization.json').read_text())
        reference = json.loads((root / 'chips' / (PARENT+'-ussfo') / 'normalization.json').read_text())
        assert all(norm[k] == reference[k] for k in ('mean', 'std'))
        if metrics['prediction_only']:
            assert pd.read_parquet(directory / 'ground-truth.parquet').empty
            assert pd.read_parquet(directory / 'matches.parquet').empty
            assert all(v['detection']['f1'] is None for v in metrics['metrics_by_match_radius_m'].values())
        cohorts[name] = metrics
    directory = evaluations / 'validation-usnyc'
    predictions = pd.read_parquet(directory / 'predictions.parquet')
    truth = pd.read_parquet(directory / 'ground-truth.parquet')
    inventory = pd.read_parquet(inputs / 'municipal-inventory.parquet')
    truth = truth.merge(inventory[['tree_id', 'species']], on='tree_id', validate='many_to_one')
    eligible = truth.dropna(subset=['species_id'])
    assert all(taxonomy['species'][int(row.species_id)] == row.species for row in eligible.itertuples())
    matches = pd.read_parquet(directory / 'matches.parquet')
    details = {}
    for radius in (2., 4.):
        selected = matches[matches.radius_m == radius]
        joined = selected.merge(truth[['tree_id', 'species', 'species_id']], on='tree_id', validate='many_to_one')
        joined['predicted_species'] = predictions.loc[joined.prediction_index, 'species'].to_numpy()
        joined['correct'] = joined.species == joined.predicted_species
        details[str(radius)] = [dict(species=row['species'], inventory_trees=row['trees'],
            validation_labels=int((truth.species == row['species']).sum()),
            matched=int((joined.species == row['species']).sum()),
            correct=int(((joined.species == row['species']) & joined.correct).sum()))
            for row in audit['top_species']]
    result = dict(checkpoint_sha256=CHECKPOINT_SHA, coverage_verified=True, species_id_mapping_verified=True,
                  threshold=.13, top_species_validation=details,
                  predicted_species_counts=predictions.loc[predictions.score >= .13, 'species'].value_counts().to_dict(),
                  cohorts=cohorts)
    output = inputs / 'validation-audit.json'
    output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    lines = ['# NYC v7 cold-start results', '',
        'Inference only: no NYC training, no GPU rental, no test evaluation. Existing v7 checkpoint 007.', '',
        f'Checkpoint SHA256: `{CHECKPOINT_SHA}`.', '',
        '## Coverage and provenance', '',
        '- 300 labeled street chips: 60 per borough, with 40 training-route and 20 validation chips. The training-route name is a curation assignment, not evidence that NYC was used to train this checkpoint.',
        '- 189 additional chips intersecting the official Central Park polygon. No ground truth is assumed there; all matching/accuracy/error counts are unavailable, not zero. Park-edge chips may show adjacent street inventory in the live editor, but the entire prediction-only cohort remains unscored.',
        '- All 489 expected chip IDs inferred and verified, including zero-detection chips. Taxonomy and normalization are exactly the parent model references.',
        '- Nine pinned 2022 NAIP RGB+NIR orthophotos, native 60 cm bilinearly resampled to 30 cm; 512 pixels / 153.6 m per window. Inputs and deterministic selection recipe are under `artifacts/run-inputs/'+RUN+'`.',
        '- Street sampling uses fixed-seed hashing within borough boundaries, one spatial block and its guard; test blocks and all Central Park-intersecting windows are excluded. Plantings after the conservative 2022-07-19 cutoff are excluded.',
        '- Frozen municipal inventory: https://storage.googleapis.com/trilogy_public_models/duckdb/trees/usnyc_tree_info_v2.parquet (NYC_OPENDATA rows only). Park boundary: https://data.cityofnewyork.us/resource/enfh-gkve.json (M010). Borough boundaries: https://data.cityofnewyork.us/City-Government/Borough-Boundaries/gthc-hcne.', '',
        '## Species vocabulary versus accuracy', '',
        f'All top 20 named municipal taxa can be emitted. Exact vocabulary coverage is {audit["named_row_coverage"]:.1%} of named inventory rows. Unsupported labels include genus-only entries and genuine missing species (e.g. Robinia pseudoacacia, Metasequoia glyptostroboides, Quercus phellos); none were silently remapped.', '',
        'At detection confidence 0.13 on the 100 validation chips:', '',
        '| Match radius | Species top-1 | Top-5 | Matched supported labels | Inventory precision* | Inventory recall* |',
        '|---|---:|---:|---:|---:|---:|']
    for radius, row in cohorts['validation-usnyc']['metrics_by_match_radius_m'].items():
        species = row['attributes_on_matched_detections']['species']
        detection = row['detection']
        lines.append(f'| {radius} m | {species["accuracy"]:.2%} | {species["top_k_accuracy"]:.2%} | {species["samples"]} | {detection["precision"]:.2%} | {detection["recall"]:.2%} |')
    lines += ['', '*Inventory diagnostics, not exhaustive tree-detection accuracy: unlabeled real trees, imperfect stem locations, and inventory/2022 imagery date mismatch affect these numbers. Species accuracy is conditional on a spatial match with a supported species, not accuracy for all NYC trees. No confidence sweep or NYC-specific calibration was performed.', '',
        'The prediction mix is strongly SF-like (including frequent Lophostemon confertus, Corymbia ficifolia and Ficus microcarpa). Label-index mapping was checked against the frozen source inventory with zero mismatches. This supports a domain-transfer concern; it does not isolate imagery, geographic, positional or temporal causes. Do not auto-publish NYC species from this run.', '',
        '### Top 20 taxa: 4 m matched validation diagnostic', '',
        '| Species | NYC inventory | Validation labels | Matched | Correct species | Accuracy |', '|---|---:|---:|---:|---:|---:|']
    for row in details['4.0']:
        accuracy = f'{row["correct"]/row["matched"]:.1%}' if row['matched'] else '—'
        lines.append(f'| {row["species"]} | {row["inventory_trees"]:,} | {row["validation_labels"]} | {row["matched"]} | {row["correct"]} | {accuracy} |')
    lines += ['', '## Curation integration', '',
        '`configs/studio_sf_boston_nyc.yaml` adds NYC without changing SF/Boston review workspaces. Select v7, NYC, and “Prediction only (unlabeled)” to isolate Central Park. Empty viewports use a geographic anchor, never a synthetic tree. Missing ground truth cannot create false-positive penalties; direct training on prediction-only chips is rejected. Future training requires rebuilding curated labels and applying the spatial split policy.', '',
        'New evaluations live under the existing v7 run so the three-city combined view works. The detailed reproducible audit is `'+str(output)+'`.', '']
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(dict(report=str(report), audit=str(output), coverage=489, species_mapping_mismatches=0)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    main(args.root, args.report)
