"""On-demand, CPU-only stratification of frozen development predictions."""
from collections import Counter
from threading import Lock, Thread
import math

import numpy as np
from urban_tree_ml.review_tables import above_threshold, by_chip

from urban_tree_ml.chip_tags import chip_key, expected_genus, load_tags
from urban_tree_ml.review_gallery import read_collection

POLICIES = ('crown-half-radius-2-4m', 'fixed-2m', 'fixed-4m')


def validate_options(threshold, policy, split):
    threshold = float(threshold)
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError('Confidence must be between 0 and 1')
    if policy not in POLICIES or split not in ('train', 'validation', 'prediction-only'):
        raise ValueError('Choose a supported matching policy and development split')
    return threshold


def score_chips(bundle, threshold, policy, progress=None):
    """Same confidence-ordered, nearest-eligible one-to-one crown matching.

    Keep counts, never averages of chip percentages. Missing species predictions
    count as incorrect when a matched scientific species target exists.
    """
    if bundle.metrics.get('split') == 'test':
        raise ValueError('Test is sealed')
    validate_options(threshold, policy, 'validation')
    taxonomy = bundle.taxonomy
    species = taxonomy['species']
    genera = taxonomy['genera']
    expected = {i: expected_genus(taxonomy, i) for i in range(len(species))}
    def name(value):
        return species[int(value)] if value is not None and math.isfinite(value) and 0 <= value < len(species) else None
    predictions = bundle.predictions.filter(above_threshold(threshold))
    pg = by_chip(predictions)
    tg = by_chip(bundle.ground_truth)
    scale = bundle.config.imagery.resolution_m * bundle.config.targets.output_stride
    rows = []
    prediction_only = bool(bundle.metrics.get('prediction_only'))
    for chip_number, chip in enumerate(bundle.chip_ids):
        if progress and chip_number % 100 == 0:
            progress(chip_number, len(bundle.chip_ids))
        ps = pg.get(chip, predictions.head(0)).sort('score', descending=True, maintain_order=True)
        ts = tg.get(chip, bundle.ground_truth.head(0))
        xy = ts[['output_x', 'output_y']].to_numpy().astype(float, copy=False)
        labels = ts["species_id"].to_numpy()
        radii = ts['crown_radius_m'].to_numpy() if 'crown_radius_m' in ts else np.full(len(ts), np.nan)
        radii = np.where(np.isfinite(radii) & (radii > 0), np.clip(.5*radii, 2, 4), 2)
        if policy != POLICIES[0]:
            radii[:] = 2 if policy == 'fixed-2m' else 4
        used = np.zeros(len(ts), dtype=bool)
        stats, conflicts, head_counts = {}, {}, {}
        def species_row(label):
            return stats.setdefault(label, dict(labeled=0, matched=0, correct=0,
                                                top5=0, top5_samples=0, confusions={}))
        if not prediction_only:
            for value in labels:
                label = name(value)
                if label and (expected[int(value)] is not None or label.startswith('__vegetation_')):
                    species_row(label)['labeled'] += 1
        matched = ignored = flags = eligible_heads = 0
        masks_known = True
        for p in ps.iter_rows(named=True):
            sid, gid = p.get('species_id'), p.get('genus_id')
            label = name(sid)
            genus = genera[int(gid)] if gid is not None and math.isfinite(gid) and 0 <= gid < len(genera) else None
            wanted = expected.get(int(sid)) if sid is not None and math.isfinite(sid) else None
            if wanted is not None and genus is not None:
                eligible_heads += 1
                head_counts[label] = head_counts.get(label, 0) + 1
                if wanted != genus:
                    flags += 1
                    entry = conflicts.setdefault(label, {})
                    entry[genus] = entry.get(genus, 0) + 1
            mask = p.get('detection_mask_value')
            masks_known &= mask is not None and math.isfinite(mask)
            if prediction_only:
                continue
            distances = np.hypot(xy[:, 0] - p['output_x'], xy[:, 1] - p['output_y'])
            eligible = np.flatnonzero(~used & (distances <= radii / scale))
            if not len(eligible):
                ignored += int(mask is not None and math.isfinite(mask) and mask <= 0)
                continue
            i = eligible[np.argmin(distances[eligible])]
            used[i] = True
            matched += 1
            truth_name = name(labels[i])
            if truth_name not in stats:
                continue
            entry = species_row(truth_name)
            entry['matched'] += 1
            entry['correct'] += int(label == truth_name)
            if label != truth_name:
                other = label or '(unavailable)'
                entry['confusions'][other] = entry['confusions'].get(other, 0) + 1
            top = p.get('species_top_ids')
            if isinstance(top, (list, tuple, np.ndarray)) and len(top):
                entry['top5_samples'] += 1
                entry['top5'] += int(labels[i] in top[:5])
        rows.append(dict(chip_id=chip, run=bundle.evaluation_id,
                         prediction_only=prediction_only, shifted=chip.startswith('shift_'),
                         predictions=len(ps), tp=matched, fn=len(ts)-matched,
                         fp=len(ps)-matched-ignored, ignored=ignored,
                         masks_known=masks_known, species=stats, conflicts=conflicts,
                         head_counts=head_counts,
                         genus_disagreements=flags, genus_comparable=eligible_heads))
    if progress:
        progress(len(bundle.chip_ids), len(bundle.chip_ids))
    return rows


class InsightService:
    """One background calculation/result: bounded, single flight, no GPU."""
    def __init__(self):
        self.lock = Lock()
        self.running = None
        self.result = None
        self.error = None
        self.stage = ''

    def request(self, key, calculate):
        with self.lock:
            if self.result and self.result[0] == key:
                return {'state': 'ready', 'rows': self.result[1]}
            if self.error and self.error[0] == key:
                error = self.error[1]
                self.error = None  # Report once; an explicit subsequent request can retry.
                return {'state': 'error', 'error': error}
            if self.running is not None:
                prefix = 'Waiting for another calculation · ' if self.running != key else ''
                return {'state': 'running', 'stage': prefix + (self.stage or 'Reading predictions')}
            self.running = key
            self.error = None
            self.stage = 'Reading frozen development predictions'
        def progress(stage):
            with self.lock:
                self.stage = stage
        def run():
            try:
                rows = calculate(progress)
                with self.lock:
                    self.result = key, rows
            except Exception as error:
                with self.lock:
                    self.error = key, str(error)
            finally:
                with self.lock:
                    self.running = None
        Thread(target=run, name='chip-insights', daemon=True).start()
        return {'state': 'running', 'stage': self.stage}


def collect(catalog, contexts, records, threshold, policy, owns, progress):
    from urban_tree_ml.model_debug import ModelDebugBundle
    rows, seen = [], set()
    for cohort_number, record in enumerate(records, 1):
        city = record['city'].lower()
        stage = f"Cohort {cohort_number}/{len(records)} · {city} · {record['cohort']}"
        progress(stage + ' · reading predictions')
        # Load a fresh, worker-owned cohort. Do not hold the interactive bundle
        # cache lock or reuse stale predictions after an evaluation file changes.
        bundle = ModelDebugBundle(record['bundle_config'], record['evaluation_dir'],
                                  record['raster_path'], evaluation_id=record['run_id'],
                                  artifact_root=catalog.config.paths.root)
        owner = contexts.get(city)
        if owner is None or not owns(owner, bundle):
            raise ValueError(f'{city}: selected model does not share the curation imagery')
        for row in score_chips(bundle, threshold, policy,
                               lambda done, total: progress(stage + f' · {done:,}/{total:,} chips scored')):
            key = chip_key(bundle, row['chip_id'])
            if (city, key) in seen:
                continue  # Identical footprint/cohort aliases are not extra coverage.
            seen.add((city, key))
            # The registration manifest retains the originating model's chip
            # ID even when its rendered pixels use the older raster resolution.
            rows.append(dict(row, city=city, footprint=key, review_chip=row['chip_id']))
        del bundle  # Keep only small per-chip counts between cohorts.
    return rows


def decorate(rows, contexts, *, collections=None):
    """Tags/completion are live; frozen model scores are not recomputed on saves."""
    tags, coverage = {}, {}
    for city in {r['city'] for r in rows}:
        context = contexts[city]
        tags[city] = load_tags(context)['chips']
        manifest, state = collections[city] if collections is not None else read_collection(context.directory)
        coverage[city] = {}
        for scene in manifest['scenes']:
            chip = scene.get('validation_chip_id')
            if not chip:
                continue
            review = state['scene_reviews'].get(scene['scene_id'], {})
            done = bool(review.get('done'))
            reviewed = done or any(s in state['reviews'] for s in scene['sample_ids'])
            coverage[city][chip] = dict(reviewed=reviewed, complete=done)
    return [dict(row, tags=tags[row['city']].get(row['footprint'], {}).get('tags', []),
                 **coverage[row['city']].get(row['review_chip'], dict(reviewed=False, complete=False))) for row in rows]


def metrics(rows):
    scored = [r for r in rows if not r['prediction_only']]
    counts = {k: sum(r[k] for r in scored) for k in ('tp', 'fp', 'fn', 'ignored')}
    tp, fp, fn = (counts[k] for k in ('tp', 'fp', 'fn'))
    safe = bool(scored) and all(r['masks_known'] for r in scored)
    scientific = [v for r in scored for k, v in r['species'].items() if not k.startswith('__vegetation_')]
    labeled, matched, correct, top5, top5_samples = (sum(v[k] for v in scientific)
        for k in ('labeled', 'matched', 'correct', 'top5', 'top5_samples'))
    def ratio(a,b): return a/b if b else None
    return dict(chips=len(rows), base_chips=sum(not r['shifted'] for r in rows),
                shifted_chips=sum(r['shifted'] for r in rows),
                reviewed=sum(r['reviewed'] for r in rows), complete=sum(r['complete'] for r in rows),
                labeled_trees=tp+fn if scored else None, scientific_labeled=labeled,
                species_matched=matched, species_correct=correct,
                precision=ratio(tp,tp+fp) if safe else None, recall=ratio(tp,tp+fn) if scored else None,
                f1=ratio(2*tp,2*tp+fp+fn) if safe else None,
                f2=ratio(5*tp,5*tp+fp+4*fn) if safe else None,
                species_accuracy=ratio(correct,matched), species_recall=ratio(correct,labeled),
                top5_accuracy=ratio(top5,top5_samples), top5_samples=top5_samples,
                genus_disagreements=sum(r['genus_disagreements'] for r in rows),
                genus_comparable=sum(r['genus_comparable'] for r in rows))


def aggregate(rows, *, city='all', tag='', species='', flags_only=False, include_shifted=True):
    rows = [r for r in rows if (city == 'all' or r['city'] == city)
            and (not tag or tag in r['tags'] or (tag == '(untagged)' and not r['tags']))
            and (include_shifted or not r['shifted'])
            and (not flags_only or r['genus_disagreements'])
            and (not flags_only or not species or species in r['conflicts'])
            and (not species or species in r['species'] or species in r['conflicts'])]
    groups, species_rows, conflicts = [], [], []
    for owner in sorted({r['city'] for r in rows}):
        selected = [r for r in rows if r['city'] == owner]
        for label in ['(all)', '(untagged)', *sorted({t for r in selected for t in r['tags']})]:
            subset = selected if label == '(all)' else [r for r in selected if label in r['tags'] or (label == '(untagged)' and not r['tags'])]
            if subset:
                groups.append(dict(city=owner, tag=label, **metrics(subset)))
        for name in sorted({s for r in selected for s in r['species']}):
            chips = [r for r in selected if name in r['species']]
            totals = {k: sum(r['species'][name][k] for r in chips)
                      for k in ('labeled', 'matched', 'correct', 'top5', 'top5_samples')}
            confusion = Counter()
            for r in chips:
                confusion.update(r['species'][name]['confusions'])
            species_rows.append(dict(city=owner, species=name, chips=len(chips), **totals,
                                     category=name.startswith('__vegetation_'),
                                     confusions=confusion.most_common(5)))
        for name in sorted({s for r in selected for s in r['conflicts']}):
            heads = Counter()
            chips = [r for r in selected if name in r['conflicts']]
            for r in chips:
                heads.update(r['conflicts'][name])
            comparable = sum(r.get('head_counts', {}).get(name, 0) for r in selected)
            conflicts.append(dict(city=owner, species=name, chips=len(chips), count=sum(heads.values()),
                                  comparable=comparable,
                                  rate=sum(heads.values())/comparable if comparable else None,
                                  predicted_genera=heads.most_common()))
    def error_rate(r):
        values = [v for s,v in r['species'].items() if not species or s == species]
        matched = sum(v['matched'] for v in values)
        correct = sum(v['correct'] for v in values)
        return (correct/matched if matched else 2, -(matched-correct))
    chips = []
    for r in sorted(rows, key=error_rate)[:200]:
        # When drilling into a species, both the ranking and displayed support
        # must describe that species, not all the other trees on the chip.
        selected = r if not species else dict(r, species={species: r['species'][species]} if species in r['species'] else {})
        chips.append(dict(city=r['city'], chip_id=r['chip_id'], run=r['run'], tags=r['tags'],
                          shifted=r['shifted'], **metrics([selected])))
    return dict(groups=groups, species=species_rows, conflicts=conflicts, chips=chips,
                matching_chips=len(rows), summary=metrics(rows))
