"""Run one bounded job and terminate its exact Lambda instance, including on failure."""
import argparse
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path


def terminate(instance_id, key):
    request = urllib.request.Request(
        "https://cloud.lambda.ai/api/v1/instance-operations/terminate",
        data=json.dumps({"instance_ids": [instance_id]}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "urban-tree-ml/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)
    if instance_id not in [i["id"] for i in result.get("data", {}).get("terminated_instances", [])]:
        raise RuntimeError("termination not confirmed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--status-file", type=Path, required=True)
    parser.add_argument("--timeout-hours", type=float, default=8)
    parser.add_argument("--check-api", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    secret = args.key_file.read_text().strip()
    if args.check_api:
        request = urllib.request.Request(
            f"https://cloud.lambda.ai/api/v1/instances/{args.instance_id}",
            headers={"Authorization": f"Bearer {secret}", "User-Agent": "urban-tree-ml/1.0"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            print("Lambda API verified:", json.load(response)["data"]["status"])
        raise SystemExit(0)
    status = {"instance_id": args.instance_id, "started_at": time.time()}
    args.status_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        status["exit_code"] = subprocess.run(
            args.command, timeout=args.timeout_hours * 3600, check=False,
        ).returncode
    except BaseException as error:
        status["error"] = type(error).__name__
        raise
    finally:
        status["finished_at"] = time.time()
        args.status_file.write_text(json.dumps(status, indent=2))
        os.sync()
        for attempt in range(120):
            try:
                terminate(args.instance_id, secret)
                status["termination_requested"] = True
                args.status_file.write_text(json.dumps(status, indent=2))
                break
            except Exception:
                print(f"Termination attempt {attempt + 1} failed; retrying", flush=True)
                time.sleep(30)
        else:
            raise RuntimeError("Lambda termination failed; manual intervention required")
