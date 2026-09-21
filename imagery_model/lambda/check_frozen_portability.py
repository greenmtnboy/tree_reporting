"""Small CPU-only cross-platform check; no torch or imagery materialization."""
import sys
from pathlib import Path

from urban_tree_ml.feedback import load_persisted_reviews
from urban_tree_ml.freeze_next_run import validate_frozen_city

for city in ['ussfo', 'usbos']:
    directory = Path(sys.argv[1]) / city
    frozen = validate_frozen_city(directory)
    expanded = load_persisted_reviews(directory)
    differences = []
    for sid, record in frozen['reviews'].items():
        for field in ['east_m', 'north_m']:
            if field in record:
                delta = abs(record[field] - expanded['reviews'][sid][field])
                if delta:
                    differences.append(delta)
    print(city, 'portable integrity PASS',
          'legacy projected hash matches:', frozen['state_revision'] == expanded['state_revision'],
          'changed offsets:', len(differences), 'max meters:', max(differences, default=0), flush=True)
