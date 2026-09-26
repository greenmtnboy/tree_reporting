"""Read-only comparison of one chip's frozen publication and live tree state."""
from pathlib import Path
import json
from collections import Counter
import pandas as pd

r = Path('C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts')
chip = 'r000020_c000091'
run = 'sf-boston-naip-curation-v9-retry1'
p = r / 'run-inputs' / run / 'ussfo'
live = r / 'qa/registration/ussfo-2022-mosaic'
truth = pd.read_parquet(r / 'runs' / run / 'evaluation/validation/ground-truth.parquet')
truth = truth[truth.chip_id == chip]
truth_ids = set(truth.tree_id.astype(str))
chips = pd.read_parquet(r / 'chips' / f'{run}-ussfo/chips.parquet')
print('chip build record', list(chips[chips.chip_id == chip].itertuples(index=False, name=None)))
samples = []
for label, directory in [('frozen', p), ('live', live)]:
    manifest = json.loads((directory / 'manifest.json').read_text())
    reviews = json.loads((directory / 'reviews.json').read_text())
    scenes = [s for s in manifest['scenes'] if s.get('validation_chip_id') == chip]
    scene_ids = {s['scene_id'] for s in scenes}
    selected = [s for s in manifest['samples'] if s.get('scene_id') in scene_ids]
    if label == 'live': samples = selected
    print(label, 'scenes', [(s['scene_id'], len(s['sample_ids'])) for s in scenes])
    print(label, 'completion', {sid: reviews.get('scene_reviews', {}).get(sid) for sid in scene_ids})
    print(label, 'status counts', Counter(reviews.get('tree_reviews', {}).get(str(s['tree_id']), {}).get('status', 'absent') for s in selected))
    feedback = json.loads((directory / 'training-feedback.json').read_text())
    ids = {str(s['tree_id']) for s in selected} | truth_ids
    print(label, 'published_at', feedback['created_at'])
    print(label, 'published exclusions', [e for e in feedback['exclusions'] if str(e['tree_id']) in ids])
    print(label, 'retained target statuses', [(tid, reviews.get('tree_reviews', {}).get(tid)) for tid in sorted(truth_ids)])
frozen_records = json.loads((p / 'reviews.json').read_text()).get('tree_reviews', {})
live_records = json.loads((live / 'reviews.json').read_text()).get('tree_reviews', {})
print('changed chip tree records', [(str(s['tree_id']), frozen_records.get(str(s['tree_id'])), live_records.get(str(s['tree_id']))) for s in samples if frozen_records.get(str(s['tree_id'])) != live_records.get(str(s['tree_id']))])
