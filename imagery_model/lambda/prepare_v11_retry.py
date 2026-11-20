"""Clone the failed v11 freeze without reading or changing live annotations."""
import hashlib
import json
import shutil
import tarfile
from pathlib import Path

from urban_tree_ml.curated_nyc_joint import validate
from urban_tree_ml.freeze_next_run import validate_frozen_city

ROOT = Path('C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts')
OLD = 'sf-boston-nyc-naip-swin-30cm-v11-curated-finetune'
NEW = OLD + '-retry1'

def main():
    source, target = (ROOT/'run-inputs'/name for name in (OLD, NEW))
    if target.exists():
        raise FileExistsError(target)
    shutil.copytree(source, target)
    policy_path = target/'curated-nyc-joint.json'
    policy = json.loads(policy_path.read_text())
    policy['experiment'] = NEW
    policy['config']['reference']['taxonomy_path'] = policy['config']['reference']['taxonomy_path'].replace(OLD, NEW)
    policy['checksums'] = {k.replace(OLD, NEW): v for k, v in policy['checksums'].items()}
    policy_path.write_text(json.dumps(policy, indent=2)+'\n')
    hashes = {}
    for city in ('ussfo', 'usbos', 'usnyc'):
        validate_frozen_city(target/city)
        for path in (source/city).rglob('*'):
            if path.is_file():
                relative = path.relative_to(source)
                assert path.read_bytes() == (target/relative).read_bytes(), relative
                hashes[str(relative).replace('\\', '/')] = hashlib.sha256(path.read_bytes()).hexdigest()
    validate(ROOT, NEW)
    (target/'retry-provenance.json').write_text(json.dumps(dict(retry_of=OLD,
        reason='Synthetic backward failure before training; deterministic gradient diagnostics added.',
        frozen_city_hashes=hashes, live_annotations_read=False, test_evaluated=False), indent=2)+'\n')
    with tarfile.open(ROOT/(NEW+'-inputs.tar.gz'), 'w:gz') as archive:
        archive.add(target, arcname=NEW)
    print('Retry frozen inputs verified:', NEW, 'city files:', len(hashes), flush=True)

if __name__ == '__main__':
    main()
