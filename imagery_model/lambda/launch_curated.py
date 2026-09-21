"""Launch the frozen September curation comparison; secrets only via environment."""
import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

ROOT = Path('C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts')
WORK = Path(__file__).resolve().parents[1]
RUN = 'sf-boston-naip-curation-v3'
PREPARED_RUN = ''
JOB = 'train'
WARM_START = ''
WARM_EPOCHS = 40
CROWN_HEAD = False
REMOTE = '/lambda/nfs/tree-reporting-dev/urban-tree-ml'
KEY = ROOT.parents[1] / '.secrets/lambda_cloud_ed25519'


def api(path, payload=None):
    request = urllib.request.Request(
        'https://cloud.lambda.ai/api/v1/' + path,
        headers={'Authorization': 'Bearer ' + os.environ['LAMBDA_KEY'],
                 'User-Agent': 'urban-tree-ml/1.0', 'Content-Type': 'application/json'},
        data=json.dumps(payload).encode() if payload is not None else None)
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def inspect():
    for route in ['instances', 'file-systems', 'ssh-keys']:
        data = api(route)['data']
        print(route, json.dumps(data), flush=True)
    types = api('instance-types')['data']
    print('A10 availability', json.dumps({k: v for k, v in types.items() if 'a10' in k}))


def prepare():
    from urban_tree_ml.config import load_config, ModelConfig, TrainingConfig, TargetsConfig
    from urban_tree_ml.freeze_next_run import freeze
    from urban_tree_ml.imagery_dates import resolve_planting_cutoff

    baseline = ROOT / 'runs/sf-boston-naip-vocab-v2-retry1'
    acquisition_dates = {}
    for city, filename in [('ussfo', 'sf_boston_vocab_v2_sf.yaml'),
                           ('usbos', 'sf_boston_vocab_v2_boston.yaml')]:
        old = json.loads((baseline / f'config-{city}.json').read_text())
        old['model'] = ModelConfig.model_validate(old['model']).model_dump(mode='json')
        old['training'] = TrainingConfig.model_validate(old['training']).model_dump(mode='json')
        old['targets'] = TargetsConfig.model_validate(old['targets']).model_dump(mode='json')
        new = load_config(WORK / 'configs' / filename).model_dump(mode='json')
        cfg = load_config(WORK / 'configs' / filename)
        acquisition_dates[city] = resolve_planting_cutoff(cfg.imagery, cfg.imagery.local_raster)
        for field in ['seed', 'model', 'training', 'targets', 'evaluation', 'split']:
            if old[field] != new[field]:
                raise ValueError(f'Baseline config mismatch: {city} {field}')
        for name in ['inventory.parquet', 'taxonomy.json']:
            prepared = ROOT / 'prepared/sf-boston-vocab-v2' / city / name
            previous = ROOT / 'run-inputs/sf-boston-naip-vocab-v2-retry1' / city / name
            if prepared.read_bytes() != previous.read_bytes():
                raise ValueError(f'Baseline input mismatch: {city} {name}')
    with tarfile.open(ROOT / 'joint-vocab-v2-retry1-source.tar.gz') as old:
        mapping = {m.name.removeprefix('./'): m for m in old.getmembers() if m.isfile()}
        for name in ['model.py', 'training.py', 'dataset.py', 'targets.py', 'losses.py',
                     'chips.py', 'evaluation.py', 'joint.py']:
            path = 'src/urban_tree_ml/' + name
            before = old.extractfile(mapping[path]).read()
            after = (WORK / path).read_bytes()
            print('Source comparison', name, 'identical' if before == after else 'CHANGED', flush=True)
    freeze(ROOT / 'prepared/sf-boston-vocab-v2',
           {city: ROOT / 'qa/registration' / scene for city, scene in
            [('ussfo', 'ussfo-2022-mosaic'), ('usbos', 'usbos-2023-external')]},
           ROOT / 'run-inputs' / RUN)
    (ROOT / 'run-inputs' / RUN / 'imagery-dates.json').write_text(json.dumps(acquisition_dates, indent=2))
    if CROWN_HEAD:
        (ROOT / 'run-inputs' / RUN / 'crown-head.json').write_text(json.dumps({'enabled':True,'human_weight':1.0,'estimated_weight':0.2}))
    if WARM_START:
        parent = ROOT / 'runs' / WARM_START
        result = json.loads((parent / 'training-result.json').read_text())
        checkpoint = parent / 'checkpoints' / Path(result['best_checkpoint']).name
        if not (checkpoint.parents[1] / 'COMPLETE').is_file():
            raise ValueError('Warm-start source is not complete')
        if json.loads((parent / 'evaluation/validation/taxonomy.json').read_text()) != json.loads(
                (ROOT / 'run-inputs' / RUN / 'ussfo/taxonomy.json').read_text()):
            raise ValueError('Warm-start vocabulary mismatch')
        warm = {'checkpoint': checkpoint.relative_to(ROOT).as_posix(),
                'checkpoint_sha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                'learning_rate': 0.00003, 'epochs': WARM_EPOCHS,
                'mode': 'weights_only_fresh_optimizer'}
        # The weights determine the architecture; do not silently rebuild a
        # ResNet when continuing a modern-backbone experiment.
        parent_configs = [load_config(path) for path in sorted(parent.glob('config-*.json'))]
        if len(parent_configs) != 2:
            raise ValueError('Warm-start requires both parent city configs')
        from urban_tree_ml.warm_recipe import recipe_from_configs
        warm.update(recipe_from_configs(parent_configs))
        (ROOT / 'run-inputs' / RUN / 'warm-start.json').write_text(json.dumps(warm, indent=2))
        # Preserve the parent's explicit center policy across curation fine-tunes.
        center_policy = ROOT / 'run-inputs' / WARM_START / 'center-policy.json'
        if center_policy.exists():
            policy = json.loads(center_policy.read_text())
            TargetsConfig.model_validate({**new['targets'], **policy})
            (ROOT / 'run-inputs' / RUN / 'center-policy.json').write_text(json.dumps(policy, indent=2))
    with tarfile.open(ROOT / f'{RUN}-inputs.tar.gz', 'w:gz') as archive:
        archive.add(ROOT / 'run-inputs' / RUN, arcname=RUN)
    with tarfile.open(ROOT / f'{RUN}-source.tar.gz', 'w:gz') as archive:
        for name in ['Dockerfile', '.dockerignore', '.python-version', 'pyproject.toml',
                     'uv.lock', 'README.md', 'src', 'configs', 'lambda']:
            archive.add(WORK / name, arcname=name,
                        filter=lambda info: None if '__pycache__' in info.name or info.name.endswith('.pyc') else info)
    print('Frozen inputs and source archive ready:', RUN, flush=True)


def launch():
    state_path = ROOT / f'{RUN}-instance.json'
    if state_path.exists():
        raise ValueError('Launch record exists; inspect it instead of launching twice')
    if not (ROOT / 'run-inputs' / (PREPARED_RUN or RUN) / 'FREEZE_COMPLETE').is_file():
        raise ValueError('Inputs are not frozen')
    if JOB == 'train' and not PREPARED_RUN:
        from urban_tree_ml.freeze_next_run import validate_frozen_city
        for city in ['ussfo', 'usbos']:
            validate_frozen_city(ROOT / 'run-inputs' / RUN / city)
    if api('instances')['data']:
        raise ValueError('An instance already exists; inspect before allocating more compute')
    result = api('instance-operations/launch', {
        'region_name': 'us-east-1', 'instance_type_name': 'gpu_1x_a10',
        'ssh_key_names': ['desktop-key'], 'file_system_names': ['tree-reporting-dev'],
        'quantity': 1, 'name': RUN})
    ids = result['data']['instance_ids']
    if len(ids) != 1:
        raise RuntimeError('Unexpected launch response; inspect Lambda immediately')
    state_path.write_text(json.dumps({'instance_id': ids[0], 'experiment': RUN,
                                     'supervised': False}, indent=2))
    print('Allocated exact instance', ids[0], flush=True)


def deploy():
    state_path = ROOT / f'{RUN}-instance.json'
    state = json.loads(state_path.read_text())
    if state.get('supervised'):
        raise ValueError('Already supervised; do not deploy twice')
    instance = state['instance_id']
    try:
        for attempt in range(90):
            info = api('instances/' + instance)['data']
            if info.get('ip') and info.get('status') == 'active':
                host = 'ubuntu@' + info['ip']
                options = ['-i', str(KEY), '-o', 'BatchMode=yes', '-o',
                           'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=10']
                ssh = ['ssh', *options, host]
                result = subprocess.run(ssh + ['test -d ' + REMOTE], capture_output=True, timeout=30)
                if result.returncode == 0:
                    break
            if attempt % 3 == 0:
                print('Waiting for instance', info['status'], flush=True)
            time.sleep(10)
        else:
            raise TimeoutError('Instance readiness timeout')
        state['ip'] = info['ip']
        state_path.write_text(json.dumps(state, indent=2))
        # Bound real storage I/O, not just mount existence, before deployment.
        probe = (
            "import os,tempfile; from pathlib import Path; "
            f"root=Path({REMOTE!r}); "
            "f=tempfile.TemporaryFile(dir=root); f.write(b'storage-health'); f.flush(); "
            "os.fsync(f.fileno()); f.seek(0); assert f.read()==b'storage-health'; f.close(); "
            "print('Persistent storage write/read verified',flush=True)"
        )
        subprocess.run(ssh + ['python3 -c ' + shlex.quote(probe)], check=True, timeout=45)
        subprocess.run(ssh + ['nvidia-smi --query-gpu=name,driver_version --format=csv,noheader'], check=True)
        subprocess.run(ssh + [f'test ! -e {REMOTE}/run-inputs/{RUN} && test ! -e {REMOTE}/runs/{RUN}'], check=True)
        uploads = [(f'{RUN}-source.tar.gz', 'joint-source.tar.gz')]
        if not PREPARED_RUN:
            uploads.append((f'{RUN}-inputs.tar.gz', 'joint-inputs.tar.gz'))
        for source, dest in uploads:
            subprocess.run(['scp', *options, str(ROOT / source), host + ':/home/ubuntu/' + dest], check=True)
        if not PREPARED_RUN:
            subprocess.run(ssh + [f'tar -xzf /home/ubuntu/joint-inputs.tar.gz -C {REMOTE}/run-inputs'], check=True)
        subprocess.run([sys.executable, 'lambda/provision_joint.py',
                        '--ip', info['ip'], '--instance-id', instance, '--ssh-key', str(KEY),
                        '--artifacts', str(ROOT), '--experiment', RUN,
                        '--prepared-run', PREPARED_RUN, '--job', JOB], cwd=WORK, check=True)
        state['supervised'] = True
        state_path.write_text(json.dumps(state, indent=2))
        print('Supervised run launched:', json.dumps(state), flush=True)
    except BaseException:
        print('Deployment failed; terminating exact instance', instance, flush=True)
        api('instance-operations/terminate', {'instance_ids': [instance]})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--launch', action='store_true')
    parser.add_argument('--deploy', action='store_true')
    parser.add_argument('--experiment', default=RUN)
    parser.add_argument('--prepared-run', default='')
    parser.add_argument('--warm-start', default='')
    parser.add_argument('--warm-epochs', type=int, default=40)
    parser.add_argument('--pack-source', action='store_true')
    parser.add_argument('--crown-head', action='store_true')
    parser.add_argument('--job', choices=['train', 'drift', 'train-inference', 'ablation', 'backbone-poc', 'deepforest'], default='train')
    args = parser.parse_args()
    for name in [args.experiment, args.prepared_run, args.warm_start]:
        if any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name):
            parser.error('Run names must contain lowercase letters, digits, and hyphens only')
    RUN, PREPARED_RUN = args.experiment, args.prepared_run
    JOB = args.job
    WARM_START = args.warm_start
    CROWN_HEAD = args.crown_head
    if args.warm_epochs < 1:
        parser.error('Warm-start epochs must be positive')
    WARM_EPOCHS = args.warm_epochs
    if args.prepare and PREPARED_RUN:
        parser.error('Prepared recovery must reuse frozen inputs, not freeze live annotations')
    if args.pack_source:
        with tarfile.open(ROOT / f'{RUN}-source.tar.gz', 'w:gz') as archive:
            for name in ['Dockerfile', '.dockerignore', '.python-version', 'pyproject.toml',
                         'uv.lock', 'README.md', 'src', 'configs', 'lambda']:
                archive.add(WORK / name, arcname=name,
                            filter=lambda info: None if '__pycache__' in info.name or info.name.endswith('.pyc') else info)
    if args.inspect:
        inspect()
    if args.prepare:
        prepare()
    if args.launch:
        launch()
    if args.deploy:
        deploy()
