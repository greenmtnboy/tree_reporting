"""Read-only discovery of completed, run-scoped benchmark review queues."""

import csv
import hashlib
import json


def geographic_assignments(context):
    """Explicitly portable queues, bound to a verified source and imagery grid.

    Benchmark/error queues remain model-scoped unless separately opted in here.
    This only prioritizes review; it never grants training or validation admission.
    """
    root = context.config.paths.root
    path = root / 'qa/geographic-review-assignments.json'
    if not path.exists():
        return {}
    acquisition = getattr(context, 'acquisition_id', None)
    city = getattr(context, 'city', '').lower()
    entries = json.loads(path.read_text(encoding='utf-8'))['assignments']
    snapshots = {name: rows for name, _, rows in _completed_snapshots(root)}
    result = {}
    for entry in entries:
        scope = entry['acquisitions'].get(acquisition)
        if entry['city'] != city or scope is None:
            continue
        if (scope['raster_sha256'] != hashlib.sha256(context.raster.read_bytes()).hexdigest()
                or scope['chip_pixels'] != context.config.imagery.chip_pixels):
            continue  # Same chip IDs on a different grid are not the same places.
        source = root / 'benchmarks' / entry['snapshot'] / 'validation-targets.csv'
        if (entry['snapshot'] not in snapshots or
                hashlib.sha256(source.read_bytes()).hexdigest() != entry['source_sha256']):
            continue
        for row in snapshots[entry['snapshot']]:
            if row['city'] == city and row['queue'] == entry['queue']:
                result[row['chip_id']] = {**row, 'split': entry['split'], 'geographic': True}
    return result


def unpredicted_assignment_rows(context):
    """Open already prepared development scenes even without model inference."""
    assignments = geographic_assignments(context)
    if not assignments:
        return [], {}
    manifest = json.loads((context.directory / 'manifest.json').read_text(encoding='utf-8'))
    rows = []
    for scene in manifest['scenes']:
        chip = scene.get('validation_chip_id')
        assignment = assignments.get(chip)
        if (assignment is None or scene.get('splits') != [assignment['split']]
                or scene.get('valid_fraction', 0) < .98):
            continue
        rows.append(dict(chip_id=chip, split=assignment['split'], scene_id=scene['scene_id'],
                         scene_image=scene['image'], inference_run=None, review_run=None,
                         missed=None, detection_f1=None, error_score=None,
                         false_positive=None, ground_truth=None))
    return rows, {row['chip_id']: assignments[row['chip_id']] for row in rows}


def _completed_snapshots(root):
    for directory in sorted((root / "benchmarks").glob("*"), reverse=True):
        if not (directory / "COMPLETE").exists():
            continue
        try:
            manifest = json.loads((directory / "manifest.json").read_text())
            path = directory / "validation-targets.csv"
            expected = manifest.get("output_sha256", {}).get(path.name)
            if expected != hashlib.sha256(path.read_bytes()).hexdigest():
                continue
            with path.open(newline="", encoding="utf-8") as handle:
                rows = [{key: row[key] for key in ('city', 'chip_id', 'queue', 'reason')}
                        for row in csv.DictReader(handle)]
            yield directory.name, manifest, rows
        except (OSError, ValueError, KeyError):
            continue


def review_assignments(root, run_id, city):
    for snapshot, manifest, rows in _completed_snapshots(root):
        if manifest.get('new_run') == run_id:
            return {
                "snapshot": snapshot,
                "chips": {
                    row["chip_id"]: {"queue": row["queue"], "reason": row["reason"]}
                    for row in rows if row['city'] == city.lower()
                },
            }
    return {"snapshot": None, "chips": {}}


def assignment_choices(root, records, cities):
    """Discover named queues with an explicit owning evaluation for navigation."""
    choices, seen = [], set()
    cities = {city.lower() for city in cities}
    for snapshot, manifest, rows in _completed_snapshots(root):
        family = manifest.get('new_run')
        if family in seen:
            continue
        seen.add(family)
        eligible = [record for record in records
                    if record['training_run_id'] == family
                    and record['city'].lower() in cities
                    and record['metrics'].get('split') in ('train', 'validation')
                    and (record['evaluation_dir'].parent.parent / 'COMPLETE').exists()]
        for queue in sorted({row['queue'] for row in rows if row['city'].lower() in cities}):
            queue_cities = {row['city'].lower() for row in rows if row['queue'] == queue}
            owner = next((record for record in eligible if record['city'].lower() in queue_cities), None)
            if owner:
                choices.append({'queue': queue, 'run_id': owner['run_id'],
                                'training_run_id': family, 'snapshot': snapshot})
    return choices
