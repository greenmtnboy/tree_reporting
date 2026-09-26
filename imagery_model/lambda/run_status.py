"""Read epoch progress from remote TensorBoard events without changing the run."""
import argparse
import shlex
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--ip', required=True)
parser.add_argument('--ssh-key', required=True)
parser.add_argument('--experiment', required=True)
parser.add_argument('--container-experiment', help='Parent container for a sequential experiment')
parser.add_argument('--probe-focal-numerics', action='store_true')
args = parser.parse_args()
if not args.experiment or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.experiment):
    parser.error('invalid experiment')
container_experiment = args.container_experiment or args.experiment
if not container_experiment or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in container_experiment):
    parser.error('invalid container experiment')
code = f'''
import json
from pathlib import Path
from tensorboard.backend.event_processing.event_accumulator import EventAccumulator
root=Path('/lambda/nfs/tree-reporting-dev/urban-tree-ml/runs/{args.experiment}')
print('checkpoints', [(p.name,p.stat().st_size) for p in root.glob('checkpoints/*.ckpt')])
for event in root.glob('lightning_logs/**/events.out.tfevents.*'):
    acc=EventAccumulator(str(event)); acc.Reload()
    print(json.dumps({{tag: {{'step': acc.Scalars(tag)[-1].step, 'value': acc.Scalars(tag)[-1].value, 'time': acc.Scalars(tag)[-1].wall_time}} for tag in acc.Tags()['scalars'] if tag in ['epoch','train/loss_step','train/loss_epoch'] or tag.startswith('validation/')}}))
'''
if args.probe_focal_numerics:
    code += '''
import torch
from urban_tree_ml.losses import masked_centernet_focal_loss
for dtype in [torch.bfloat16, torch.float32]:
    x=torch.tensor([8.0], dtype=dtype)
    print('focal numeric probe', str(dtype), masked_centernet_focal_loss(x, torch.zeros(1), torch.ones(1)).item())
'''
command = ['sudo', 'docker', 'exec', 'tree-' + container_experiment,
           'uv', 'run', '--frozen', '--no-sync', 'python', '-c', code]
subprocess.run(['ssh', '-i', args.ssh_key, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                'ubuntu@' + args.ip, shlex.join(command)], check=True, timeout=45)
