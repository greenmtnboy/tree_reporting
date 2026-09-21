"""Collect a completed supervised run, verify its archive, then acknowledge teardown."""

import argparse
import hashlib
import json
import shlex
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", required=True)
    parser.add_argument("--ssh-key", required=True)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--training-inference-source-run")
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument(
        "--compare-to",
        help="After collection/teardown ACK, run local paired uplift against this baseline",
    )
    args = parser.parse_args()
    source_run = args.training_inference_source_run
    if source_run and any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in source_run):
        parser.error("invalid source run")
    name = args.experiment
    if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name):
        parser.error("invalid experiment")
    host = "ubuntu@" + args.ip
    options = ["-i", args.ssh_key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
               "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
    ssh = ["ssh", *options, host]
    root = "/lambda/nfs/tree-reporting-dev/urban-tree-ml"
    status_path = f"{root}/{name}-status.json"
    deadline = time.monotonic() + 9 * 3600
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(ssh + [f"cat {status_path}"], capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            print("Status SSH timed out; retrying without acknowledging collection", flush=True)
            time.sleep(45)
            continue
        if result.returncode == 0 and json.loads(result.stdout).get("finished_at"):
            archive = f"{root}/{name}-results.tar.gz"
            # Keep small chip indexes/build audits too, for Studio provenance and masks.
            # Do not download or evaluate sealed test pixel payloads.
            package = (
                "import tarfile; from pathlib import Path; "
                f"root=Path({root!r}); name={name!r}; "
                "paths=[root/'runs'/name,root/'run-inputs'/name,"
                "root/(name+'.log'),root/(name+'-status.json')]; "
                "paths += [p for suffix in ('convnext-tiny','swin-tiny') "
                "for p in (root/'runs'/(name+'-'+suffix),root/'run-inputs'/(name+'-'+suffix)) if p.exists()]; "
                + (f"paths += [root/'runs'/{source_run!r}/'evaluation'/('train-'+city) "
                   "for city in ('ussfo','usbos')]; " if source_run else "") +
                "paths += [p for city in ('ussfo','usbos') "
                "for p in (root/'chips'/(name+'-'+city)).glob('*') "
                "if p.is_file() and p.suffix in ('.json','.parquet')]; "
                f"archive=tarfile.open({archive!r},'w:gz'); "
                "[archive.add(p,arcname=p.relative_to(root).as_posix()) "
                "for p in paths if p.exists()]; "
                "archive.close()"
            )
            command = 'python3 -c ' + shlex.quote(package) + f' && sha256sum {archive}'
            digest = subprocess.check_output(ssh + [command], text=True).split()[0]
            local = args.artifacts / f"{name}-results.tar.gz"
            subprocess.run(["scp", *options, f"{host}:{archive}", str(local)], check=True)
            with local.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != digest:
                raise RuntimeError("Downloaded archive checksum mismatch; not acknowledging")
            subprocess.run(["tar", "-xzf", str(local), "-C", str(args.artifacts)], check=True)
            subprocess.run(ssh + [f"touch {root}/{name}-status.downloaded"], check=True)
            print("Verified results downloaded; teardown acknowledged", flush=True)
            if args.compare_to:
                if not (args.artifacts / "runs" / name / "COMPLETE").exists():
                    raise RuntimeError("Run failed; results collected but uplift not attempted")
                from urban_tree_ml.uplift import build

                build(
                    args.artifacts,
                    args.compare_to,
                    name,
                    args.artifacts / "benchmarks" / f"{name}-vs-{args.compare_to}",
                )
            return
        time.sleep(45)
    raise TimeoutError("Collector expired; server-side deadline remains responsible for teardown")


if __name__ == "__main__":
    main()
