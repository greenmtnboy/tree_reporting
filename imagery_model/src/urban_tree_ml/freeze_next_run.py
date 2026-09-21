"""Freeze already-published curation alongside prepared inventories. Never publishes or trains."""

import argparse
import hashlib
import json
from pathlib import Path

from urban_tree_ml.feedback import _json_sha256, load_persisted_reviews


FROZEN_FILES = ('manifest.json', 'reviews.json', 'training-feedback.json',
                'inventory.parquet', 'taxonomy.json', 'published-state.json')


def validate_frozen_city(directory):
    """Verify publication at freeze time without reprojecting coordinates on the host.

    The reviewer hashes expanded offsets/masks; PROJ floating-point results need
    not be byte-identical on Windows and Linux. Bind that published state to the
    exact source bytes, rather than weakening the publication gate with tolerance.
    """
    directory = Path(directory)
    receipt = json.loads((directory / 'integrity.json').read_text())
    if receipt.get('schema_version') != 1 or set(receipt['sha256']) != set(FROZEN_FILES):
        raise ValueError('Invalid frozen integrity receipt')
    for name in FROZEN_FILES:
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != receipt['sha256'][name]:
            raise ValueError(f'Frozen input checksum mismatch: {name}')
    state = json.loads((directory / 'published-state.json').read_text())
    feedback = json.loads((directory / 'training-feedback.json').read_text())
    from urban_tree_ml.feedback import _review_state_sha256
    revision = _review_state_sha256(state['reviews'], state['scene_reviews'], state['mask_regions'])
    if revision != state['state_revision'] or revision != feedback['source_reviews_sha256']:
        raise ValueError('Unpublished frozen curation')
    manifest = json.loads((directory / 'manifest.json').read_text())
    if feedback['source_manifest_sha256'] != _json_sha256(manifest):
        raise ValueError('Stale frozen curation manifest')
    return state


def freeze(prepared, reviews, destination):
    if destination.exists():
        raise ValueError("Destination exists; choose a fresh experiment")
    snapshots = {}
    for city, directory in reviews.items():
        files = {
            name: (directory / name).read_bytes()
            for name in ["manifest.json", "reviews.json", "training-feedback.json"]
        }
        state = load_persisted_reviews(directory)
        feedback = json.loads(files["training-feedback.json"])
        if feedback["source_reviews_sha256"] != state["state_revision"]:
            raise ValueError(f"{city}: publish current curation first")
        if feedback["source_manifest_sha256"] != _json_sha256(json.loads(files["manifest.json"])):
            raise ValueError(f"{city}: publish the updated manifest first")
        if any((directory / name).read_bytes() != content for name, content in files.items()):
            raise ValueError(f"{city}: curation changed during snapshot; retry")
        for name in ["inventory.parquet", "taxonomy.json"]:
            files[name] = (prepared / city / name).read_bytes()
        files['published-state.json'] = json.dumps(state, sort_keys=True).encode('utf-8')
        snapshots[city] = files
    destination.mkdir(parents=True)
    for city, files in snapshots.items():
        target = destination / city
        target.mkdir()
        for name, content in files.items():
            (target / name).write_bytes(content)
        frozen_state = load_persisted_reviews(target)
        feedback = json.loads(files["training-feedback.json"])
        if feedback["source_reviews_sha256"] != frozen_state["state_revision"]:
            raise ValueError("Frozen review revision differs; do not launch this incomplete bundle")
        receipt = {'schema_version': 1, 'sha256': {
            name: hashlib.sha256(files[name]).hexdigest() for name in FROZEN_FILES}}
        (target / 'integrity.json').write_text(json.dumps(receipt, indent=2))
        validate_frozen_city(target)
    (destination / "preparation.json").write_bytes((prepared / "preparation.json").read_bytes())
    (destination / "FREEZE_COMPLETE").write_text(
        "Published curation frozen; no training started.\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--sf-review", type=Path, required=True)
    parser.add_argument("--boston-review", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    freeze(args.prepared, {"ussfo": args.sf_review, "usbos": args.boston_review}, args.destination)
