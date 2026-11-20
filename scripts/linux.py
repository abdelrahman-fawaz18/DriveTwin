"""Run foreground development commands in Linux without changing the app agent.

Windows: python scripts/linux.py [options] -- COMMAND [ARG ...]
Linux:   python3 scripts/linux.py [options] -- COMMAND [ARG ...]

Requests are JSON files, not shell strings. stdin/stdout/stderr pass through.
Cancellation targets only the command's Linux process group. A changing lease
also lets Linux stop that group if the Windows controller is forcibly killed.
This is a process supervisor, not a security sandbox or a service manager.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import tomllib
import uuid

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / ".runtime" / "launcher"


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=True) + "\n", encoding="utf-8")
    # Windows/DrvFs readers can briefly deny replacement of an open file.
    # Keep atomic replacement and bound the wait well below the lease timeout.
    deadline = time.monotonic() + 1.0
    try:
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_directory(run_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("Run ID must be 32 lowercase hexadecimal characters.")
    return RUNS / run_id


def project_path(value: str) -> Path:
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("The path must stay inside the EV-DT project.")
    return path


def positive_seconds(value: str | float) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Time limits must be finite positive numbers.")
    return number


def group_alive(group_id: int) -> bool:
    # Exited zombies are not running work. /proc/stat identifies the group;
    # the only target is the group created for this invocation, never a name.
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / "stat").read_text().rpartition(")")[2].split()
            if int(fields[2]) == group_id and fields[0] not in {"Z", "X"}:
                return True
        except (FileNotFoundError, ProcessLookupError):
            continue
    return False


def stop_group(process: subprocess.Popen, grace: float) -> bool:
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 2.0)):
        process.poll()
        if not group_alive(process.pid):
            return True
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return True
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            process.poll()
            if not group_alive(process.pid):
                return True
            time.sleep(0.05)
    return not group_alive(process.pid)


def worker(run_id: str) -> int:
    if sys.platform != "linux" or os.getuid() == 0:
        raise ValueError("The command worker requires a regular Linux user.")
    directory = run_directory(run_id)
    request = read_json(directory / "request.json")
    argv = request["argv"]
    if not isinstance(argv, list) or not argv or any(
        not isinstance(arg, str) or "\0" in arg for arg in argv
    ):
        raise ValueError("A command must be a nonempty list of strings without NUL characters.")
    cwd = project_path(request["cwd"])
    grace = positive_seconds(request["grace"])
    lease_timeout = positive_seconds(request["lease_timeout"])
    environment = os.environ.copy()
    overrides = request.get("environment", {})
    if not isinstance(overrides, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        or not key or "=" in key or "\0" in key or "\0" in value
        for key, value in overrides.items()
    ):
        raise ValueError("Configured environment settings must be valid string name/value pairs.")
    environment.update(overrides)
    result = {
        "run_id": run_id, "state": "running", "platform": sys.platform,
        "distribution": os.environ.get("WSL_DISTRO_NAME"), "uid": os.getuid(),
        "cwd": str(cwd), "started_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    process = None
    reason, code, cleanup = "completed", 125, True
    interrupted = False

    def worker_interrupted(_signal, _frame):
        nonlocal interrupted
        interrupted = True

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, worker_interrupted)
    try:
        if (directory / "cancel.json").exists():
            reason = read_json(directory / "cancel.json")["reason"]
        else:
            process = subprocess.Popen(argv, cwd=cwd, env=environment, start_new_session=True)
            result["pid"] = process.pid
            write_json(directory / "state.json", result)
            lease = (directory / "lease.json").read_bytes()
            last_change = time.monotonic()
            while process.poll() is None:
                if interrupted:
                    reason = "cancelled"
                    break
                if (directory / "cancel.json").exists():
                    reason = read_json(directory / "cancel.json")["reason"]
                    break
                current_lease = (directory / "lease.json").read_bytes()
                if current_lease != lease:
                    lease, last_change = current_lease, time.monotonic()
                if time.monotonic() - last_change >= lease_timeout:
                    reason = "parent_lost"
                    break
                time.sleep(0.05)
            cleanup = stop_group(process, grace)
            process.wait(timeout=3)
            code = process.returncode if process.returncode >= 0 else 128 - process.returncode
    except FileNotFoundError as error:
        reason, code = "command_not_found", 127
        result["error"] = str(error)
        print(f"ev-dt: {error}", file=sys.stderr)
    except OSError as error:
        reason, code = "execution_error", 126
        result["error"] = str(error)
        print(f"ev-dt: {error}", file=sys.stderr)
    finally:
        if process is not None:
            try:
                cleanup = stop_group(process, grace) and cleanup
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired) as error:
                cleanup = False
                result["cleanup_error"] = str(error)
            result["child_exit_code"] = process.returncode
        code = {"cancelled": 130, "timed_out": 124, "parent_lost": 129}.get(reason, code)
        if not cleanup:
            reason, code = "cleanup_unconfirmed", 125
        result.update(
            state="finished", reason=reason, exit_code=code,
            cleanup_confirmed=cleanup,
            finished_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        write_json(directory / "state.json", result)
    return code


def controller(args: argparse.Namespace) -> int:
    if args.cancel:
        directory = run_directory(args.cancel)
        if not directory.is_dir():
            raise ValueError("No such launcher run exists.")
        state = read_json(directory / "state.json")
        if state["state"] != "finished":
            write_json(directory / "cancel.json", {"reason": "cancelled"})
        return 0

    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise ValueError("Supply a command after --, for example: -- python3 --version")
    cwd = project_path(args.cwd)
    if not cwd.is_dir():
        raise ValueError("The requested working directory does not exist.")
    receipt = project_path(args.receipt) if args.receipt else None
    settings = tomllib.loads((ROOT / "environments" / "dev.toml").read_text())["linux"]
    grace = positive_seconds(settings["termination_grace_seconds"])
    lease_timeout = positive_seconds(settings["heartbeat_timeout_seconds"])
    run_id = args.run_id or uuid.uuid4().hex
    directory = run_directory(run_id)
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "request.json", {
        "argv": command, "cwd": cwd.relative_to(ROOT).as_posix(),
        "grace": grace, "lease_timeout": lease_timeout,
        "environment": settings.get("environment", {}),
    })
    write_json(directory / "state.json", {"run_id": run_id, "state": "starting"})
    write_json(directory / "lease.json", {"sequence": 0})
    if os.name == "nt":
        if not re.fullmatch(r"[A-Za-z]:", ROOT.drive):
            raise ValueError("The launcher currently requires a local Windows drive.")
        linux_root = settings["windows_mount_root"].rstrip("/") + "/" + ROOT.drive[0].lower()
        linux_root += "/" + "/".join(ROOT.parts[1:])
        invocation = [
            "wsl.exe", "--distribution", settings["distribution"],
            "--user", settings["user"], "--cd", linux_root,
            "--exec", "python3", "scripts/linux.py", "--_worker", run_id,
        ]
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    elif sys.platform == "linux":
        invocation = [sys.executable, str(Path(__file__).resolve()), "--_worker", run_id]
        options = {"start_new_session": True}
    else:
        raise ValueError("This launcher supports Windows + WSL or direct Linux execution.")

    requested = None

    def interrupted(_signal, _frame):
        nonlocal requested
        requested = "cancelled"

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupted)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, interrupted)
    start = last_lease = time.monotonic()
    sequence, cancel_time = 0, None
    process = subprocess.Popen(invocation, cwd=ROOT, **options)
    while process.poll() is None:
        now = time.monotonic()
        if args.timeout is not None and now - start >= args.timeout and requested is None:
            requested = "timed_out"
        if requested is not None and cancel_time is None:
            write_json(directory / "cancel.json", {"reason": requested})
            cancel_time = now
        if now - last_lease >= 1:
            sequence += 1
            write_json(directory / "lease.json", {"sequence": sequence})
            last_lease = now
        if cancel_time is not None and now - cancel_time > lease_timeout + grace + 5:
            process.terminate()
            process.wait(timeout=5)
            raise RuntimeError(f"Run {run_id}: cancellation cleanup could not be confirmed.")
        time.sleep(0.05)
    result = read_json(directory / "state.json")
    if result["state"] != "finished" or process.returncode != result["exit_code"]:
        raise RuntimeError(f"Run {run_id}: WSL ended without a matching Linux completion record.")
    if receipt:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        write_json(receipt, result)
    return result["exit_code"]


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--_worker":
        return worker(sys.argv[2])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", default=".", help="Working directory relative to project root")
    parser.add_argument("--timeout", type=positive_seconds, help="Optional command deadline in seconds")
    parser.add_argument("--run-id", help="Optional unique 32-character hexadecimal identifier")
    parser.add_argument("--receipt", help="Save completion metadata to a project-relative JSON file")
    parser.add_argument("--cancel", metavar="RUN_ID", help="Cancel one existing invocation")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    try:
        return controller(parser.parse_args())
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"ev-dt: {error}", file=sys.stderr)
        return 125


if __name__ == "__main__":
    raise SystemExit(main())
