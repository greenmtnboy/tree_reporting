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
ALLOWED_PEER_IDS = set()
HARD_LIFETIME_HOURS = None
REMOTE = '/lambda/nfs/tree-reporting-dev/urban-tree-ml'
KEY = Path(os.environ.get('LAMBDA_SSH_KEY', str(ROOT.parents[1] / '.secrets/lambda_cloud_ed25519')))


def pack_source():
    # Ship portable Rust sources, never the workstation's binary or Cargo cache.
    names = ['Dockerfile', '.dockerignore', '.python-version', 'pyproject.toml',
             'uv.lock', 'README.md', 'src', 'configs', 'lambda',
             'native/build_review_native.py', 'native/review_native/Cargo.toml',
             'native/review_native/Cargo.lock', 'native/review_native/src']
    def portable(info):
        parts = Path(info.name).parts
        return None if ('__pycache__' in parts or 'target' in parts or
                        info.name.endswith(('.pyc', '.pyd', '.so', '.dll', '.dylib'))) else info
    with tarfile.open(ROOT / f'{RUN}-source.tar.gz', 'w:gz') as archive:
        for name in names:
            archive.add(WORK / name, arcname=name, filter=portable)


def api(path, payload=None):
    request = urllib.request.Request(
        'https://cloud.lambda.ai/api/v1/' + path,
        headers={'Authorization': 'Bearer ' + os.environ['LAMBDA_KEY'],
                 'User-Agent': 'urban-tree-ml/1.0', 'Content-Type': 'application/json'},
        data=json.dumps(payload).encode() if payload is not None else None)
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def install_assets(ssh, options, host):
    # Docker-created imagery directories may be root-owned. Probe the exact
    # destinations as the installer user before transferring the large archive.
    installer = WORK/'lambda/install_frozen_assets.py'
    receipt = ROOT/f'{RUN}-assets.json'
    remote_installer = '/home/ubuntu/install_frozen_assets.py'
    remote_receipt = '/home/ubuntu/prepared-assets.json'
    for local, remote in [(installer, remote_installer), (receipt, remote_receipt)]:
        subprocess.run(['scp', *options, str(local), host+':'+remote], check=True)
        actual = subprocess.check_output(ssh+['sha256sum '+shlex.quote(remote)], text=True).split()[0]
        if hashlib.sha256(local.read_bytes()).hexdigest() != actual:
            raise ValueError('Asset installer/receipt upload checksum mismatch')
    command = 'sudo -n python3 '+shlex.quote(remote_installer)+' --root '+shlex.quote(REMOTE)+' --receipt '+shlex.quote(remote_receipt)
    subprocess.run(ssh+[command+' --check-writable'], check=True, timeout=60)
    archive = ROOT/f'{RUN}-assets.tar'
    remote_archive = '/home/ubuntu/prepared-assets.tar'
    subprocess.run(['scp', *options, str(archive), host+':'+remote_archive], check=True)
    actual = subprocess.check_output(ssh+['sha256sum '+remote_archive], text=True).split()[0]
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != actual:
            raise ValueError('Asset archive upload checksum mismatch')
    print('Frozen asset archive upload SHA256 verified:', actual, flush=True)
    subprocess.run(ssh+[command+' --archive '+remote_archive], check=True)


def inspect():
    for route in ['instances', 'file-systems', 'ssh-keys']:
        data = api(route)['data']
        print(route, json.dumps(data), flush=True)
    types = api('instance-types')['data']
    print('A10 availability', json.dumps({k: v for k, v in types.items() if 'a10' in k}))


def prepare(*, vegetation_groups=False):
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
    prepared_inventory = ROOT / 'prepared/sf-boston-vocab-v2'
    refresh_policy = ROOT / 'prepared/source-refresh-policy.json'
    if refresh_policy.exists():
        from urban_tree_ml.source_refresh import prepare as refresh_source
        prepared_inventory = refresh_source(ROOT, prepared_inventory,
            json.loads(refresh_policy.read_text()), WORK / 'configs/sf_boston_vocab_v2_sf.yaml')
    freeze(prepared_inventory,
           {city: ROOT / 'qa/registration' / scene for city, scene in
            [('ussfo', 'ussfo-2022-mosaic'), ('usbos', 'usbos-2023-external')]},
           ROOT / 'run-inputs' / RUN)
    if refresh_policy.exists():
        refresh = json.loads((prepared_inventory / 'preparation.json').read_text())['source_refresh']
        frozen_reviews = ROOT / 'run-inputs' / RUN / 'ussfo/reviews.json'
        if hashlib.sha256(frozen_reviews.read_bytes()).hexdigest() != refresh['curation_sha256']:
            raise ValueError('Curation changed after source refresh; do not launch this bundle')
    (ROOT / 'run-inputs' / RUN / 'imagery-dates.json').write_text(json.dumps(acquisition_dates, indent=2))
    if CROWN_HEAD:
        (ROOT / 'run-inputs' / RUN / 'crown-head.json').write_text(json.dumps({'enabled':True,'human_weight':1.0,'estimated_weight':0.2}))
    if RESOLUTION_30:
        from urban_tree_ml.resolution_recipe import apply_resolution_recipe
        for filename in ('sf_boston_vocab_v2_sf.yaml', 'sf_boston_vocab_v2_boston.yaml'):
            local_config = load_config(WORK / 'configs' / filename)
            print('Local 30 cm raster preflight', apply_resolution_recipe(local_config, RUN), flush=True)
        (ROOT / 'run-inputs' / RUN / 'resolution-policy.json').write_text(json.dumps({'recipe':'swin-native30-upsample60-v1'}))
        parent_policy = ROOT / 'run-inputs/sf-boston-naip-swin-v9-finetune/center-policy.json'
        if not parent_policy.exists():
            raise ValueError('Missing established crown-center policy')
        (ROOT / 'run-inputs' / RUN / 'center-policy.json').write_bytes(parent_policy.read_bytes())
    if WARM_START:
        parent = ROOT / 'runs' / WARM_START
        result = json.loads((parent / 'training-result.json').read_text())
        from urban_tree_ml.checkpoint_selection import resolve_parent_checkpoint
        checkpoint = resolve_parent_checkpoint(parent)
        if not (checkpoint.parents[1] / 'COMPLETE').is_file():
            raise ValueError('Warm-start source is not complete')
        from urban_tree_ml.warm_recipe import validate_warm_taxonomy
        validate_warm_taxonomy(
            json.loads((parent / 'evaluation/validation/taxonomy.json').read_text()),
            json.loads((ROOT / 'run-inputs' / RUN / 'ussfo/taxonomy.json').read_text()),
            vegetation_groups=vegetation_groups)
        warm = {'checkpoint': checkpoint.relative_to(ROOT).as_posix(),
                'checkpoint_sha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                'learning_rate': 0.00003, 'epochs': WARM_EPOCHS,
                'mode': 'weights_only_fresh_optimizer'}
        # The weights determine the architecture; do not silently rebuild a
        # ResNet when continuing a modern-backbone experiment.
        parent_configs = [load_config(path) for path in sorted(parent.glob('config-*.json'))]
        from urban_tree_ml.warm_recipe import recipe_from_configs, validate_parent_cohorts
        validate_parent_cohorts(parent_configs)
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
    pack_source()
    print('Frozen inputs and source archive ready:', RUN, flush=True)


def launch():
    if not KEY.is_file():
        raise ValueError('SSH key missing; run with envx so LAMBDA_SSH_KEY points to its temporary key file')
    state_path = ROOT / f'{RUN}-instance.json'
    if state_path.exists():
        raise ValueError('Launch record exists; inspect it instead of launching twice')
    if not (ROOT / 'run-inputs' / (PREPARED_RUN or RUN) / 'FREEZE_COMPLETE').is_file():
        raise ValueError('Inputs are not frozen')
    if JOB == 'prepared-finetune':
        from urban_tree_ml.prepared_finetune import validate_recipe
        validate_recipe(ROOT,RUN)
    if (ROOT/'run-inputs'/RUN/'curated-nyc-joint.json').exists():
        from urban_tree_ml.curated_nyc_joint import validate
        validate(ROOT, RUN)
    if (ROOT/'run-inputs'/RUN/'fairfax-joint.json').exists():
        from urban_tree_ml.fairfax_curation import validate_admission
        validate_admission(ROOT/'run-inputs'/RUN/'usffx')
        if not (ROOT/(RUN+'-assets.tar')).is_file() or not (ROOT/(RUN+'-assets.json')).is_file():
            raise ValueError('Fairfax portable source assets missing')
    if (ROOT / 'run-inputs' / RUN / 'frozen-parent.json').exists():
        from urban_tree_ml.frozen_pair_finetune import validate
        validate(ROOT, RUN)
    if JOB == 'train' and not PREPARED_RUN:
        from urban_tree_ml.freeze_next_run import validate_frozen_city
        for city in ['ussfo', 'usbos']:
            validate_frozen_city(ROOT / 'run-inputs' / RUN / city)
        prep = json.loads((ROOT / 'run-inputs' / RUN / 'preparation.json').read_text())
        if prep.get('source_refresh'):
            review_bytes = (ROOT / 'run-inputs' / RUN / 'ussfo/reviews.json').read_bytes()
            if hashlib.sha256(review_bytes).hexdigest() != prep['source_refresh']['curation_sha256']:
                raise ValueError('Source refresh protection does not match frozen curation')
    if any(i['id'] not in ALLOWED_PEER_IDS for i in api('instances')['data']
           if i.get('status') != 'terminated'):
        raise ValueError('An instance already exists; inspect before allocating more compute')
    allocated_at = time.time()
    result = api('instance-operations/launch', {
        'region_name': 'us-east-1', 'instance_type_name': 'gpu_1x_a10',
        'ssh_key_names': ['desktop-key'], 'file_system_names': ['tree-reporting-dev'],
        'quantity': 1, 'name': RUN})
    ids = result['data']['instance_ids']
    if len(ids) != 1:
        raise RuntimeError('Unexpected launch response; inspect Lambda immediately')
    state = {'instance_id': ids[0], 'experiment': RUN, 'supervised': False,
             'allocation_requested_at': allocated_at}
    if HARD_LIFETIME_HOURS is not None:
        state['hard_deadline_epoch'] = allocated_at + HARD_LIFETIME_HOURS * 3600
    state_path.write_text(json.dumps(state, indent=2))
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
            if info.get('id') != instance or info.get('name') != RUN:
                raise ValueError('Exact instance identity/name mismatch')
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
        if state.get('hard_deadline_epoch'):
            from datetime import datetime, UTC
            deadline = state['hard_deadline_epoch']
            if deadline <= time.time():
                raise TimeoutError('Allocation lifetime expired before deployment')
            subprocess.run(['scp', *options, str(WORK/'lambda/supervise.py'),
                host+':/home/ubuntu/deployment-watchdog.py'], check=True, timeout=60)
            writer = "import os,sys; from pathlib import Path; os.umask(0o077); Path('/home/ubuntu/.joint-lambda-key').write_text(sys.stdin.read().strip())"
            subprocess.run(ssh+['python3 -c '+shlex.quote(writer)], input=os.environ['LAMBDA_KEY'], text=True, check=True, timeout=45)
            watchdog = (f'/usr/bin/python3 /home/ubuntu/deployment-watchdog.py --instance-id {instance} '
                        '--key-file /home/ubuntu/.joint-lambda-key --status-file /home/ubuntu/deployment-status.json')
            subprocess.run(ssh+[watchdog+' --check-api'], check=True, timeout=45)
            calendar = datetime.fromtimestamp(deadline, UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
            subprocess.run(ssh+['sudo systemd-run --unit=tree-deployment-deadline --on-calendar='
                +shlex.quote(calendar)+' '+watchdog+' --terminate-only'], check=True, timeout=45)
        if JOB == 'prepared-finetune':
            # Protect the potentially large asset upload even if this workstation sleeps.
            subprocess.run(['scp',*options,str(WORK/'lambda/supervise.py'),
                host+':/home/ubuntu/deployment-watchdog.py'],check=True,timeout=60)
            writer="import os,sys; from pathlib import Path; os.umask(0o077); Path('/home/ubuntu/.joint-lambda-key').write_text(sys.stdin.read().strip())"
            subprocess.run(ssh+['python3 -c '+shlex.quote(writer)],input=os.environ['LAMBDA_KEY'],text=True,check=True,timeout=45)
            watchdog=(f'/usr/bin/python3 /home/ubuntu/deployment-watchdog.py --instance-id {instance} '
                '--key-file /home/ubuntu/.joint-lambda-key --status-file /home/ubuntu/deployment-status.json')
            subprocess.run(ssh+[watchdog+' --check-api'],check=True,timeout=45)
            subprocess.run(ssh+['sudo systemd-run --unit=tree-deployment-deadline --on-active=60m '+watchdog+' --terminate-only'],check=True,timeout=45)
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
            remote_sha = subprocess.check_output(ssh+['sha256sum /home/ubuntu/'+dest], text=True).split()[0]
            with (ROOT/source).open('rb') as uploaded:
                local_sha = hashlib.file_digest(uploaded, 'sha256').hexdigest()
            if remote_sha != local_sha:
                raise ValueError('Upload checksum mismatch: '+source)
            print('Upload SHA256 verified:', source, local_sha, flush=True)
        if not PREPARED_RUN:
            subprocess.run(ssh + [f'tar -xzf /home/ubuntu/joint-inputs.tar.gz -C {REMOTE}/run-inputs'], check=True)
        if JOB == 'prepared-finetune' or (ROOT/'run-inputs'/RUN/'fairfax-joint.json').exists():
            install_assets(ssh, options, host)
        subprocess.run([sys.executable, 'lambda/provision_joint.py',
                        '--ip', info['ip'], '--instance-id', instance, '--ssh-key', str(KEY),
                        '--artifacts', str(ROOT), '--experiment', RUN,
                        '--prepared-run', PREPARED_RUN, '--job', JOB,
                        *(['--hard-deadline-epoch', str(state['hard_deadline_epoch'])]
                          if state.get('hard_deadline_epoch') else [])], cwd=WORK, check=True)
        if JOB == 'prepared-finetune':
            subprocess.run(ssh+['systemctl is-active tree-joint-deadline.timer && sudo systemctl stop tree-deployment-deadline.timer'],check=True,timeout=45)
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
    parser.add_argument('--resolution-30', action='store_true')
    parser.add_argument('--job', choices=['train', 'drift', 'train-inference', 'ablation', 'backbone-poc', 'deepforest', 'prepared-finetune'], default='train')
    args = parser.parse_args()
    for name in [args.experiment, args.prepared_run, args.warm_start]:
        if any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name):
            parser.error('Run names must contain lowercase letters, digits, and hyphens only')
    RUN, PREPARED_RUN = args.experiment, args.prepared_run
    JOB = args.job
    WARM_START = args.warm_start
    CROWN_HEAD = args.crown_head
    RESOLUTION_30 = args.resolution_30
    if WARM_START:
        parent_resolution = ROOT / 'run-inputs' / WARM_START / 'resolution-policy.json'
        if parent_resolution.exists():
            if json.loads(parent_resolution.read_text()) != {'recipe':'swin-native30-upsample60-v1'}:
                parser.error('Unknown parent resolution recipe')
            RESOLUTION_30 = True
        elif RESOLUTION_30:
            parser.error('30 cm fine-tuning requires a 30 cm parent checkpoint')
    if RESOLUTION_30 and args.prepared_run:
        parser.error('30 cm experiment cannot reuse prepared chips')
    if args.warm_epochs < 1:
        parser.error('Warm-start epochs must be positive')
    WARM_EPOCHS = args.warm_epochs
    if args.prepare and PREPARED_RUN:
        parser.error('Prepared recovery must reuse frozen inputs, not freeze live annotations')
    if args.pack_source:
        pack_source()
    if args.inspect:
        inspect()
    if args.prepare:
        prepare()
    if args.launch:
        launch()
    if args.deploy:
        deploy()
