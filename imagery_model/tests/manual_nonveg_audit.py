"""Read-only Boston review audit; stdout only, no label changes."""
import json
import math
from collections import Counter
from pathlib import Path

import rasterio
from pyproj import Transformer
from rasterio.windows import Window

from urban_tree_ml.quality import _vegetation_features

root = Path('C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts')
directory = root / 'qa/registration/usbos-2023-external'
manifest = json.loads((directory / 'manifest.json').read_text())
reviews = json.loads((directory / 'reviews.json').read_text())
by_id = {s['sample_id']: s for s in manifest['samples']}
rows = []
counts = Counter()
with rasterio.open(root / 'imagery/usbos/2023/usbos-2023-external.vrt') as source:
    transformer = Transformer.from_crs('EPSG:4326', source.crs, always_xy=True)
    for scene in manifest['scenes']:
        flagged = [by_id[i] for i in scene['sample_ids']
                   if by_id[i].get('vegetation_heuristic', {}).get('candidate')
                   and by_id[i].get('coordinate_stack_size', 1) <= 1]
        details = []
        for sample in flagged:
            review = reviews['reviews'].get(sample['sample_id'], {})
            status = review.get('status', 'aligned')
            counts[status] += 1
            item = {'sample': sample['sample_id'], 'tree': sample['tree_id'],
                    'status': status, 'species': sample['species'], 'dbh': sample['dbh_in'],
                    'xy': [sample['target_x'], sample['target_y']]}
            if status == 'offset':
                col, row = (~source.transform) * transformer.transform(
                    sample['longitude'], sample['latitude'])
                col += review['image_x'] - sample['target_x']
                row += review['image_y'] - sample['target_y']
                left, top = math.floor(col) - 7, math.floor(row) - 7
                raw = source.read([1, 2, 3, 4], window=Window(left, top, 15, 15))
                after = _vegetation_features(raw, col-left, row-top)
                item['corrected'] = after
                item['offset_m'] = math.hypot(review['east_m'], review['north_m'])
                counts['offset_still_flagged' if after['candidate'] else 'offset_cleared'] += 1
            details.append(item)
        if flagged:
            rows.append({'scene': scene['scene_id'], 'chip': scene.get('validation_chip_id'),
                         'split': scene['splits'], 'image': scene['image'],
                         'total': len(scene['sample_ids']), 'flags': len(flagged),
                         'done': reviews.get('scene_reviews', {}).get(scene['scene_id'], {}).get('done', False),
                         'details': details})
print(json.dumps({'scenes': len(manifest['scenes']), 'points': len(manifest['samples']),
                  'counts': dict(counts), 'rows': sorted(rows, key=lambda r: -r['flags'])}, indent=2))
