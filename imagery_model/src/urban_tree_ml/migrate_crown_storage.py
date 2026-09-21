"""Offline, backed-up migration of mutable reviews only. Frozen runs are untouched."""
import argparse
import hashlib
import json
import socket
from pathlib import Path

from urban_tree_ml.feedback import (
    _write_json_atomic, normalize_review_payload, normalize_mask_region_payload,
    normalize_scene_review_payload,
)
from urban_tree_ml.tree_curation import migrate_crown_storage


def plan(directory):
    directory = Path(directory)
    path = directory / 'reviews.json'
    original = path.read_bytes()
    payload = json.loads(original)
    if payload.get('schema_version') != 2:
        raise ValueError(f'Expected tree-keyed schema 2: {path}')
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    migrated = migrate_crown_storage(payload)
    for normalize in (normalize_review_payload, normalize_mask_region_payload, normalize_scene_review_payload):
        if normalize(payload, manifest) != normalize(migrated, manifest):
            raise ValueError(f'Migration changes annotation meaning: {path}')
    return path, original, migrated, {
        'path': str(path), 'changed': migrated != payload,
        'linked_crowns': sum('crown_radius_m' in r for r in migrated['tree_reviews'].values()),
        'added_trees': len(migrated['added_trees']), 'area_masks': len(migrated['mask_regions']),
        'before_sha256': hashlib.sha256(original).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directories', nargs='+', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir', type=Path)
    args = parser.parse_args()
    plans = [plan(d) for d in args.directories]
    if args.apply:
        if not args.backup_dir:
            parser.error('--apply requires a new --backup-dir')
        with socket.socket() as connection:
            if connection.connect_ex(('127.0.0.1', 8765)) == 0:
                raise RuntimeError('Stop the reviewer after autosaving before migrating')
        args.backup_dir.mkdir(parents=True, exist_ok=False)
        # Back up every input before replacing any. Never overwrite prior backups.
        for index, (path, original, _, receipt) in enumerate(plans):
            backup = args.backup_dir / f'{index}-reviews.json'
            backup.write_bytes(original)
            if backup.read_bytes() != original:
                raise RuntimeError('Backup verification failed')
            receipt['backup'] = str(backup)
        _write_json_atomic(args.backup_dir/'receipt.json', [p[3] for p in plans])
        for path, original, migrated, receipt in plans:
            if path.read_bytes() != original:
                raise RuntimeError(f'Concurrent write detected; stopped before overwriting {path}')
            if receipt['changed']:
                _write_json_atomic(path, migrated)
            if json.loads(path.read_bytes()) != migrated:
                raise RuntimeError(f'Read-back failed: {path}')
            receipt['after_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        _write_json_atomic(args.backup_dir/'receipt.json', [p[3] for p in plans])
    print(json.dumps([p[3] for p in plans], indent=2))


if __name__ == '__main__':
    main()
