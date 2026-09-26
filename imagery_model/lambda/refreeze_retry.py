"""Re-attest an existing published freeze locally; never read live curation."""
import argparse
import shutil
import tarfile
from pathlib import Path

from urban_tree_ml.freeze_next_run import freeze, validate_frozen_city


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--experiment', required=True)
    args = parser.parse_args()
    for name in [args.source, args.experiment]:
        if not name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name):
            parser.error('Unsafe run name')
    source = args.artifacts / 'run-inputs' / args.source
    destination = args.artifacts / 'run-inputs' / args.experiment
    freeze(source, {city: source / city for city in ['ussfo', 'usbos']}, destination)
    for name in ['imagery-dates.json', 'warm-start.json']:
        if (source / name).exists():
            shutil.copy2(source / name, destination / name)
    for city in ['ussfo', 'usbos']:
        validate_frozen_city(destination / city)
        for name in ['reviews.json', 'manifest.json', 'training-feedback.json', 'inventory.parquet', 'taxonomy.json']:
            assert (source / city / name).read_bytes() == (destination / city / name).read_bytes()
    with tarfile.open(args.artifacts / f'{args.experiment}-inputs.tar.gz', 'x:gz') as archive:
        archive.add(destination, arcname=args.experiment)
    print('Retry frozen and verified with identical original inputs:', args.experiment)


if __name__ == '__main__':
    main()
