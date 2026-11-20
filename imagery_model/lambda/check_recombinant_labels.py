"""Resolve recombinant centers for inspection only. No training, export or GPU use."""
import argparse
import json
from pathlib import Path

from urban_tree_ml.config import load_config
from urban_tree_ml import recombinant as r
from urban_tree_ml.recombinant_labels import make_reference, bind_reference, dry_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', help='Prepared source photo ID; default initial library')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--generated-id', required=True)
    parser.add_argument('--bind', action='store_true', help='Explicitly create a shared source reference; never copies labels')
    parser.add_argument('--review-dir', type=Path)
    parser.add_argument('--raster', type=Path)
    parser.add_argument('--inventory', type=Path)
    args = parser.parse_args()
    scope = r.scoped(args.root, args.source)
    if args.bind:
        if not all((args.review_dir, args.raster, args.inventory)):
            parser.error('--bind requires --review-dir, --raster and --inventory')
        bind_reference(scope, make_reference(scope, args.review_dir, args.raster, args.inventory))
    elif any((args.review_dir, args.raster, args.inventory)):
        parser.error('Binding paths require explicit --bind')
    config = load_config(args.config)
    config.paths.root = args.root
    print(json.dumps(dry_run(scope, args.generated_id, config), indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
