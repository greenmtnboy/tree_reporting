"""Install a frozen joint-run bundle and start an independently supervised service."""
import argparse
import os
import shlex
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--ip", required=True)
parser.add_argument("--instance-id", required=True)
parser.add_argument("--ssh-key", required=True)
parser.add_argument("--artifacts", type=Path, required=True)
args = parser.parse_args()
experiment = "sf-boston-naip-curated-joint-v1"
remote = f"ubuntu@{args.ip}"
ssh = ["ssh", "-i", args.ssh_key, "-o", "BatchMode=yes", remote]
root = "/lambda/nfs/tree-reporting-dev/urban-tree-ml"
work = "/home/ubuntu/joint-run"
subprocess.run(ssh + [
    f"mkdir -p {work}; tar -xzf /home/ubuntu/joint-source.tar.gz -C {work}"
], check=True)
subprocess.run(ssh + ["sudo usermod -aG docker ubuntu"], check=True)
secret_writer = (
    "import os,sys; from pathlib import Path; os.umask(0o077); "
    "Path('/home/ubuntu/.joint-lambda-key').write_text(sys.stdin.read().strip())"
)
subprocess.run(ssh + ["python3 -c " + shlex.quote(secret_writer)],
               input=os.environ["LAMBDA_KEY"], text=True, check=True)
supervisor = (
    f"/usr/bin/python3 {work}/lambda/supervise.py --instance-id {args.instance_id} "
    f"--key-file /home/ubuntu/.joint-lambda-key --status-file {root}/{experiment}-status.json"
)
# A separate deadline survives failure of the training supervisor itself.
subprocess.run(ssh + [supervisor + " --check-api"], check=True)
subprocess.run(ssh + [
    "sudo systemd-run --unit=tree-joint-deadline --on-active=9h "
    f"{supervisor} /usr/bin/true"
], check=True)
subprocess.run(ssh + [
    "sudo systemd-run --unit=tree-joint --uid=ubuntu "
    f"--property=WorkingDirectory={work} "
    f"--property=StandardOutput=append:{root}/{experiment}.log "
    f"--property=StandardError=append:{root}/{experiment}.log "
    f"--setenv=TREE_ML_DATA_ROOT={root} --setenv=TREE_ML_EXPERIMENT={experiment} "
    f"{supervisor} /usr/bin/bash lambda/joint.sh"
], check=True)
print("Started tree-joint service with automatic termination and independent 9-hour deadline.")
