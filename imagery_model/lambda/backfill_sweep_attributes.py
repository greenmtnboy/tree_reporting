"""Backfill attribute metrics in verified sweeps, without inference or live labels."""
import argparse
import json
import os
from pathlib import Path
from urban_tree_ml.sweep_history import best_sweeps, swept_attributes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    for (run, city), winner in best_sweeps(args.root).items():
        if 'attributes_on_matched_detections' in winner:
            continue
        directory = args.root/'runs'/run/'evaluation'/('validation' if city == 'ussfo' else 'validation-usbos')
        attributes = swept_attributes(directory, winner['threshold'])
        path = args.root/winner['source']
        original_stat = path.stat()
        rows = json.loads(path.read_text())
        for row in rows:
            if (row['run'] == run and row['city'].lower() == city
                    and row['threshold'] == winner['threshold']
                    and row.get('policy', 'crown-half-radius-2-4m') == winner['policy']):
                row['attributes_on_matched_detections'] = attributes
        pending = path.with_suffix('.tmp')
        pending.write_text(json.dumps(rows, indent=2), encoding='utf-8')
        pending.replace(path)
        # Attribute enrichment must not change which historical sweep is newest.
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        print(run, city, attributes.get('species'), flush=True)


if __name__ == '__main__':
    main()
