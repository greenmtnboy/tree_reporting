"""Discover NAIP metadata, audit a data bundle, or prepare Fairfax review tiles."""
import argparse
import json
from pathlib import Path

from urban_tree_ml.fairfax_pilot import (
    PARENT, acquire_imagery, audit_bundle, discover_catalog, predict_pilot, prepare_pilot, save_new,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    catalog = commands.add_parser('catalog', help='Snapshot candidate metadata without downloading imagery')
    catalog.add_argument('--years', type=int, nargs='+', default=[2021, 2023])
    catalog.add_argument('--output', type=Path, required=True)
    audit = commands.add_parser('audit', help='Verify upstream boundary/inventory hashes and vocabulary coverage')
    audit.add_argument('--bundle', type=Path, required=True)
    audit.add_argument('--artifacts', type=Path, required=True)
    audit.add_argument('--output', type=Path, required=True)
    acquire = commands.add_parser('acquire', help='Download explicit same-year county-intersecting NAIP assets')
    acquire.add_argument('--bundle', type=Path, required=True)
    acquire.add_argument('--artifacts', type=Path, required=True)
    acquire.add_argument('--catalog', type=Path, required=True)
    acquire.add_argument('--year', type=int, required=True)
    acquire.add_argument('--item-ids', nargs='+', required=True)
    acquire.add_argument('--output', type=Path, required=True)
    prepare = commands.add_parser('prepare', help='Materialize isolated prediction-only 512px RGBN review tiles')
    prepare.add_argument('--bundle', type=Path, required=True)
    prepare.add_argument('--artifacts', type=Path, required=True)
    prepare.add_argument('--source-vrt', type=Path, required=True, help='Native 60cm mosaic of direct TIFF sources')
    prepare.add_argument('--acquisition-date', required=True, help='Latest actual source acquisition day, YYYY-MM-DD')
    prepare.add_argument('--output', type=Path, required=True, help='New isolated pilot directory, must not exist')
    prepare.add_argument('--train-tiles', type=int, default=80)
    prepare.add_argument('--validation-tiles', type=int, default=40)
    predict = commands.add_parser('predict', help='Run retained-parent CPU inference on both prepared review cohorts')
    predict.add_argument('--pilot', type=Path, required=True)
    register = commands.add_parser('register', help='Import the pilot into shared curation with unavailable labels')
    register.add_argument('--pilot', type=Path, required=True)
    register.add_argument('--artifacts', type=Path, required=True)
    register.add_argument('--annotations', type=Path, required=True)
    holdout = commands.add_parser('seed-holdout', help='Reserve sealed test pixels outside development datasets')
    holdout.add_argument('--pilot', type=Path, required=True)
    holdout.add_argument('--bundle', type=Path, required=True)
    holdout.add_argument('--artifacts', type=Path, required=True)
    holdout.add_argument('--tiles', type=int, default=40)
    training = commands.add_parser('prepare-training', help='Freeze published more_done tiles and build curated targets')
    training.add_argument('--artifacts', type=Path, required=True)
    training.add_argument('--output', type=Path, required=True, help='New immutable freeze directory')
    training.add_argument('--dataset', required=True, help='New dataset name under artifacts/chips')
    training.add_argument('--taxonomy', type=Path, required=True)
    inventory = commands.add_parser('inventory-review', help='Prepare, infer or publish sparse inventory-backed Fairfax review tiles')
    inventory.add_argument('phase', choices=['prepare', 'evaluate', 'publish'])
    inventory.add_argument('--artifacts', type=Path, required=True)
    inventory.add_argument('--name', required=True)
    inventory.add_argument('--bundle', type=Path)
    inventory.add_argument('--checkpoint', type=Path)
    inventory.add_argument('--server', default='http://127.0.0.1:8765')
    args = parser.parse_args()
    if args.command == 'inventory-review':
        from urban_tree_ml import fairfax_inventory
        if args.phase == 'prepare':
            if args.bundle is None or args.checkpoint is None:
                parser.error('Inventory preparation requires --bundle and --checkpoint')
            result = fairfax_inventory.prepare(args.artifacts, args.bundle, args.name, args.checkpoint)
        elif args.phase == 'evaluate':
            result = fairfax_inventory.evaluate(args.artifacts, args.name)
        else:
            result = fairfax_inventory.publish(args.artifacts, args.name, args.server)
    elif args.command == 'catalog':
        result = discover_catalog(args.years, args.output)
    elif args.command == 'audit':
        result = audit_bundle(args.bundle, args.artifacts / 'runs' / PARENT / 'vegetation-taxonomy.json')
        save_new(args.output, result)
    elif args.command == 'acquire':
        result = acquire_imagery(args.bundle, args.artifacts, args.catalog, args.year, args.item_ids, args.output)
    elif args.command == 'predict':
        result = predict_pilot(args.pilot)
    elif args.command == 'register':
        from urban_tree_ml.fairfax_curation import register
        result = register(args.pilot, args.artifacts, args.annotations)
    elif args.command == 'seed-holdout':
        from urban_tree_ml.fairfax_curation import seed_holdout
        result = seed_holdout(args.pilot, args.bundle, args.artifacts, args.tiles)
    elif args.command == 'prepare-training':
        from urban_tree_ml.fairfax_curation import freeze_training, verify_dataset
        from urban_tree_ml.chips import build_chips
        from urban_tree_ml.config import load_config
        if (not args.dataset or Path(args.dataset).name != args.dataset
                or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.dataset)):
            parser.error('Dataset must contain lowercase letters, digits and hyphens only')
        directory = args.artifacts / 'chips' / args.dataset
        if directory.exists():
            raise ValueError('Training dataset already exists')
        result = freeze_training(args.artifacts, args.output, args.taxonomy)
        config = load_config(args.output / 'config.json')
        result['build'] = build_chips(config, config.imagery.local_raster,
            feedback_path=args.output / 'usffx/training-feedback.json',
            inventory_source=args.output / 'usffx/inventory.parquet', output_dataset=args.dataset)
        verify_dataset(directory)
        config.dataset = args.dataset
        save_new(args.output / 'training-config.json', config.model_dump(mode='json'))
    else:
        result = prepare_pilot(args.bundle, args.artifacts, args.source_vrt, args.acquisition_date,
                               args.output, {'train': args.train_tiles, 'validation': args.validation_tiles})
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
