"""Cooperative shutdown of a local curator, including older hidden Windows launches."""
import _thread
import ctypes
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from urllib.request import ProxyHandler, build_opener




def control_path(root, port):
    return Path(root) / "qa" / "curator-logs" / f"control-{port}.json"


@contextmanager
def stop_channel(path):
    # The launcher holds its per-port startup lock for the lifetime of this channel.
    nonce = secrets.token_hex(16)
    request = path.with_suffix(f".{nonce}.stop")
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(nonce))
    temp.replace(path)
    done = threading.Event()

    def watch():
        while not done.wait(0.25):
            if request.exists():
                _thread.interrupt_main()
                return

    worker = threading.Thread(target=watch, daemon=True)
    worker.start()
    try:
        yield
    finally:
        done.set()
        worker.join()
        path.unlink(missing_ok=True)
        request.unlink(missing_ok=True)


def idle(port, workspace):
    import yaml

    opener = build_opener(ProxyHandler({}))
    cities = yaml.safe_load((workspace / "configs/studio_sf_boston_nyc.yaml").read_text())["cities"]
    for item in cities:
        city = item["city"]
        with opener.open(f"http://127.0.0.1:{port}/api/backup-status?city={city}", timeout=6) as response:
            state = json.load(response)
        if state["job"]["running"] or state["snapshot"]["pending"] or state["snapshot"]["running"]:
            raise RuntimeError(f"Curator is saving {city}; retry stop once it finishes")


def legacy_pids(port, expected):
    # This compatibility path is only for instances started before the control file existed.
    command = f"""
    $ErrorActionPreference = 'Stop'
    $owner = (Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort {int(port)}).OwningProcess
    $server = Get-CimInstance Win32_Process -Filter "ProcessId=$owner"
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($server.ParentProcessId)"
    @($server,$parent) | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress
    """
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                            capture_output=True, text=True, check=True)
    server, parent = json.loads(result.stdout)
    if os.path.normcase(server["CommandLine"].strip()) != os.path.normcase(expected):
        raise RuntimeError("Port belongs to another process; refusing to interrupt it")
    members = [server["ProcessId"]]
    if parent and parent.get("CommandLine") == server["CommandLine"]:
        members.append(parent["ProcessId"])
    return members


def interrupt_console(members):
    # Run in an isolated helper: Ctrl+C is console-wide on Windows.
    kernel = ctypes.windll.kernel32
    kernel.FreeConsole()
    if not kernel.AttachConsole(members[0]):
        raise RuntimeError("Cannot attach to the curator console")
    try:
        processes = (ctypes.c_ulong * 16)()
        count = kernel.GetConsoleProcessList(processes, len(processes))
        if not 0 < count <= len(processes) or set(processes[:count]) != {*members, os.getpid()}:
            raise RuntimeError("Curator shares a console; use Ctrl+C in its terminal")
        if not kernel.SetConsoleCtrlHandler(None, True) or not kernel.GenerateConsoleCtrlEvent(0, 0):
            raise RuntimeError("Could not send Ctrl+C to the curator console")
        time.sleep(0.2)
    finally:
        kernel.FreeConsole()


def stop(port, repository, workspace, status):
    current = status(port)
    if current == "stopped":
        return
    if current != "ready":
        raise RuntimeError("Port is occupied by an unrecognized or unresponsive service")
    idle(port, workspace)
    root = os.environ.get("TREE_ML_DATA_ROOT", repository / "imagery_model/artifacts")
    path = control_path(root, port)
    if path.exists():
        nonce = json.loads(path.read_text())
        if not isinstance(nonce, str) or len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
            raise RuntimeError("Invalid curator control file")
        path.with_suffix(f".{nonce}.stop").touch()
    elif os.name == "nt":
        expected = subprocess.list2cmdline([sys.executable, "-u", str(workspace / "curator.py"),
                                           "start", "--repository", str(repository), "--port", str(port)])
        members = legacy_pids(port, expected)
        subprocess.run([sys._base_executable, "-I", str(Path(__file__).resolve()), *map(str, members)],
                       creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    else:
        raise RuntimeError("Older curator has no control file; stop it with Ctrl+C once")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if status(port) == "stopped" and not path.exists():
            return
        time.sleep(0.3)
    raise RuntimeError("Curator did not finish stopping within 30 seconds")


if __name__ == "__main__":
    interrupt_console(list(map(int, sys.argv[1:])))
