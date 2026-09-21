"""Prepare a controlled crown-center experiment from v15, not live annotations."""
import hashlib
import json
import shutil
import tarfile
from pathlib import Path
from urban_tree_ml.freeze_next_run import validate_frozen_city

ROOT = Path('C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts')
BASELINE = 'sf-boston-naip-curation-v15-finetune'
RUN = 'sf-boston-naip-curation-v16-crown-center'


def prepare():
    source = ROOT/'run-inputs'/BASELINE
    target = ROOT/'run-inputs'/RUN
    if target.exists():
        raise ValueError('Experiment already exists; do not overwrite frozen inputs')
    assert (ROOT/'runs'/BASELINE/'COMPLETE').exists()
    work = Path(__file__).resolve().parents[1]
    with tarfile.open(ROOT/f'{BASELINE}-source.tar.gz') as archive:
        for name in ['configs/sf_boston_vocab_v2_sf.yaml','configs/sf_boston_vocab_v2_boston.yaml',
                     'src/urban_tree_ml/model.py','src/urban_tree_ml/training.py',
                     'src/urban_tree_ml/losses.py','src/urban_tree_ml/dataset.py']:
            if archive.extractfile(name).read() != (work/name).read_bytes():
                raise ValueError(f'Unexpected change beyond center-target policy: {name}')
    for city in ('ussfo','usbos'):
        validate_frozen_city(source/city)
    shutil.copytree(source, target)
    result = json.loads((ROOT/'runs'/BASELINE/'training-result.json').read_text())
    checkpoint = ROOT/'runs'/BASELINE/'checkpoints'/Path(result['best_checkpoint']).name
    warm = dict(checkpoint=checkpoint.relative_to(ROOT).as_posix(),
                checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                learning_rate=.00003, epochs=40, mode='weights_only_fresh_optimizer')
    (target/'warm-start.json').write_text(json.dumps(warm,indent=2))
    policy = dict(crown_scaled_center=True,crown_center_fraction=.3,
                  crown_center_max_sigma_m=3.,crown_estimated_scale=.5)
    (target/'center-policy.json').write_text(json.dumps(policy,indent=2))
    (target/'experiment.json').write_text(json.dumps(dict(
        baseline=BASELINE,changed='training center Gaussian width only',
        validation_targets='unchanged; train-only adjustment',
        evaluation='fixed 2/4m plus target crown radius*0.5 clipped to [2,4]m; unknown=2m',
        live_curation='not read; exact v15 frozen city inputs reused'),indent=2))
    for city in ('ussfo','usbos'):
        validate_frozen_city(target/city)
    with tarfile.open(ROOT/f'{RUN}-inputs.tar.gz','w:gz') as archive:
        archive.add(target,arcname=RUN)
    print('Frozen v15 inputs reused:',RUN)


if __name__ == '__main__':
    prepare()
