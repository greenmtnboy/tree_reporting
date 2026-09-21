"""One-shot result recovery; never starts training; always terminates allocation."""
import json
import os
import runpy
import shlex
import subprocess
import sys
import time
from pathlib import Path

m = runpy.run_path(str(Path(__file__).with_name('launch_curated.py')))
api, root, key = m['api'], m['ROOT'], m['KEY']
run = 'sf-boston-naip-swin-v5-finetune'
if api('instances')['data']:
    raise RuntimeError('Existing instance; inspect before recovery allocation')
ids = api('instance-operations/launch', {
    'region_name': 'us-east-1', 'instance_type_name': 'gpu_1x_a10',
    'ssh_key_names': ['desktop-key'], 'file_system_names': ['tree-reporting-dev'],
    'quantity': 1, 'name': run + '-recovery',
})['data']['instance_ids']
assert len(ids) == 1
instance = ids[0]
print('Recovery allocated:', instance, flush=True)
try:
    (root / (run + '-recovery-instance.json')).write_text(json.dumps({'instance_id': instance}))
    for attempt in range(90):
        info = api('instances/' + instance)['data']
        if info.get('status') == 'active' and info.get('ip'):
            options = ['-i', str(key), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=10']
            ssh = ['ssh', *options, 'ubuntu@' + info['ip']]
            if subprocess.run(ssh + ['true'], capture_output=True, timeout=20).returncode == 0:
                break
        if attempt % 3 == 0:
            print('Waiting:', info['status'], flush=True)
        time.sleep(10)
    else:
        raise TimeoutError('Recovery readiness')
    writer = "import os,sys; from pathlib import Path; os.umask(0o077); Path('/home/ubuntu/.recovery-key').write_text(sys.stdin.read())"
    subprocess.run(ssh + ['python3 -c ' + shlex.quote(writer)], input=os.environ['LAMBDA_KEY'], text=True, check=True, timeout=30)
    subprocess.run(['scp', *options, str(Path(__file__).with_name('supervise.py')), 'ubuntu@' + info['ip'] + ':/home/ubuntu/recovery-supervise.py'], check=True, timeout=30)
    subprocess.run(ssh + [f'sudo systemd-run --unit=recovery-deadline --on-active=45m /usr/bin/python3 /home/ubuntu/recovery-supervise.py --instance-id {instance} --key-file /home/ubuntu/.recovery-key --status-file /home/ubuntu/recovery-status.json --terminate-only'], check=True, timeout=30)
    remote = m['REMOTE']
    subprocess.run(ssh + [f'cat {remote}/{run}-status.json; tail -c 6000 {remote}/{run}.log; ls {remote}/runs/{run}'], check=True, timeout=30)
    subprocess.run([sys.executable, str(Path(__file__).with_name('collect_run.py')), '--ip', info['ip'], '--ssh-key', str(key), '--experiment', run, '--artifacts', str(root)], check=True, timeout=2100)
finally:
    result = api('instance-operations/terminate', {'instance_ids': [instance]})
    print('Recovery termination requested:', instance, flush=True)
