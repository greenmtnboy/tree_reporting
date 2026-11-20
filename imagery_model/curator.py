"""Start the local curator with consistent paths and environment.

Run with the Python environment that already has the imagery dependencies installed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import ProxyHandler, build_opener

WORKSPACE = Path(__file__).resolve().parent


@contextmanager
def startup_lock(port: int):
    """Keep a kernel lock throughout startup/serving, including before bind."""
    directory = Path(os.environ["TREE_ML_DATA_ROOT"]) / "qa" / "curator-logs"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"port-{port}.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
        handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Another curator launcher is already starting/running on this port") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main_repository() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=WORKSPACE, capture_output=True, text=True, check=True,
    )
    return Path(result.stdout.strip()).parent


def status(port: int) -> str:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
    except OSError:
        return "stopped"
    try:
        with build_opener(ProxyHandler({})).open(
            f"http://127.0.0.1:{port}/", timeout=3,
        ) as response:
            if response.status == 200 and b"backup-health" in response.read():
                return "ready"
    except OSError:
        pass
    return "occupied or unresponsive"


def start_with_resolved_keys(repository: Path, args: list[str]) -> int | None:
    from dotenv import dotenv_values

    files = {**dotenv_values(repository / "src" / ".env.local"), **dotenv_values(repository / ".env")}
    names = ("MAPILLARY_KEY", "GOOGLE_MAPS_EMBED_API_KEY", "PLANTNET_API_KEY", "VITE_PLANTNET_API_KEY")
    refs = {k: files[k] for k in names if not os.environ.get(k) and (files.get(k) or "").startswith("vault:")}
    if not refs:
        return None
    launcher = shutil.which("envx.ps1")
    if not launcher:
        raise RuntimeError("Launch the curator with its street imagery credentials in the process environment")
    # Only pass references used by the curator to the local credential launcher.
    with tempfile.TemporaryDirectory() as directory:
        env_file = Path(directory) / ".env"
        env_file.write_text("".join(f"{key}={ref}\n" for key, ref in refs.items()), encoding="utf-8")
        return subprocess.run(["powershell.exe", "-NoProfile", "-File", launcher, "run", "-f",
                               str(env_file), "--", sys.executable, str(Path(__file__).resolve()), *args]).returncode


def configure(repository: Path) -> None:
    # Load secrets without printing them. Explicit process values take precedence.
    from dotenv import dotenv_values, load_dotenv

    load_dotenv(repository / ".env", override=False)
    plantnet = dotenv_values(repository / "src" / ".env.local").get("VITE_PLANTNET_API_KEY")
    if plantnet:
        os.environ.setdefault("VITE_PLANTNET_API_KEY", plantnet)
    if not any(os.environ.get(name) and not os.environ[name].startswith("vault:")
               for name in ("GOOGLE_MAPS_EMBED_API_KEY", "MAPILLARY_KEY")):
        raise RuntimeError("Supply a resolved MAPILLARY_KEY or GOOGLE_MAPS_EMBED_API_KEY to start the curator")
    defaults = {
        "TREE_ML_DATA_ROOT": str(repository / "imagery_model" / "artifacts"),
        "TREE_ML_ANNOTATIONS_ROOT": str(WORKSPACE / "annotations"),
        "TREE_ML_MODEL_CACHE_MB": "2048",
        # Bound parallel decode buffers on the local, multi-request server.
        "POLARS_MAX_THREADS": str(min(4, os.cpu_count() or 1)),
        "GDAL_CACHEMAX": "128",
        "GDAL_MAX_DATASET_POOL_SIZE": "32",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    if not Path(os.environ["TREE_ML_DATA_ROOT"]).is_dir():
        raise RuntimeError("TREE_ML_DATA_ROOT must point to the existing artifacts directory")
    sys.path.insert(0, str(WORKSPACE / "src"))
    os.chdir(WORKSPACE)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "status", "stop"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--repository", type=Path, help="Main checkout containing .env and artifacts")
    parser.add_argument("--background", action="store_true", help="Start hidden, logging to artifacts/qa/curator-logs")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    repository = (args.repository or main_repository()).resolve()
    if args.command == "stop":
        from curator_control import stop
        stop(args.port, repository, WORKSPACE, status)
        print(f"Curator stopped on port {args.port}")
        return 0
    current = status(args.port)
    url = f"http://127.0.0.1:{args.port}/"
    if args.command == "status" or current != "stopped":
        print(f"Curator: {current} - {url}")
        return 0 if current == "ready" else 1

    resolved = start_with_resolved_keys(repository, sys.argv[1:] if argv is None else argv)
    if resolved is not None:
        return resolved
    configure(repository)
    if args.background:
        logs = Path(os.environ["TREE_ML_DATA_ROOT"]) / "qa" / "curator-logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / f"studio-{datetime.now():%Y%m%d-%H%M%S-%f}.log"
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        with log.open("wb") as output:
            process = subprocess.Popen(
                [sys.executable, "-u", str(Path(__file__).resolve()), "start",
                 "--repository", str(repository), "--port", str(args.port)],
                cwd=WORKSPACE, stdin=subprocess.DEVNULL, stdout=output,
                stderr=subprocess.STDOUT, **options,
            )
        print(f"Starting curator (PID {process.pid}); log: {log}", flush=True)
        for _ in range(30):
            if process.poll() is not None:
                print(f"Startup exited {process.returncode}; inspect {log}")
                return 1
            if status(args.port) == "ready":
                print(f"Curator ready: {url}")
                return 0
            time.sleep(1)
        print(f"Readiness timed out; process may still be starting. Use status; inspect {log}. Do not start another copy.")
        return 1

    from curator_control import control_path, stop_channel
    with startup_lock(args.port), stop_channel(control_path(os.environ["TREE_ML_DATA_ROOT"], args.port)):
        from urban_tree_ml.config import load_studio_config
        from urban_tree_ml.qa_server import serve_model_studio

        print(f"Starting curator: {url}", flush=True)
        serve_model_studio(
            load_studio_config(WORKSPACE / "configs" / "studio_sf_boston_nyc.yaml"),
            bind="127.0.0.1", port=args.port,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        pass
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"Curator command failed: {error}", file=sys.stderr)
        raise SystemExit(1)
