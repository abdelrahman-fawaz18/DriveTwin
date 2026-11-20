"""Bounded, renewable Windows sleep request; never changes a power plan."""

import argparse
import ctypes
import json
import os
import signal
import time
from ctypes import wintypes
from pathlib import Path


def live_process_stamp(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:
            return None
        raise ctypes.WinError(error)
    try:
        if kernel.WaitForSingleObject(handle, 0) != 258:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("hold", "renew", "stop"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--lease-seconds", type=int, default=900)
    parser.add_argument("--max-seconds", type=int, default=43200)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / ".runtime"
    directory = args.directory.resolve()
    if not directory.is_relative_to(root) or directory == root:
        parser.error("Choose a dedicated directory under this project's .runtime")
    if os.name != "nt":
        parser.error("Sleep requests must run on Windows")
    if args.action != "hold":
        if not (directory / "state.json").is_file():
            parser.error("No recorded sleep helper in that directory")
        if args.action == "renew":
            snapshot = json.loads((directory / "state.json").read_bytes())
            if (
                snapshot.get("status") != "holding"
                or not snapshot.get("process_stamp")
                or live_process_stamp(snapshot["pid"]) != snapshot["process_stamp"]
            ):
                parser.error("Sleep helper is not live; start a new bounded holder")
        name = "lease" if args.action == "renew" else "stop"
        (directory / name).write_text(str(time.time()), encoding="utf-8", newline="\n")
        print(json.dumps({"action": args.action, "directory": str(directory)}), flush=True)
        return
    if not 5 <= args.lease_seconds <= 3600 or not 5 <= args.max_seconds <= 86400:
        parser.error("Lease must be 5..3600 seconds; maximum lifetime 5..86400 seconds")
    directory.mkdir(parents=True, exist_ok=False)
    lease = directory / "lease"
    lease.write_text(str(time.time()), encoding="utf-8", newline="\n")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    request = kernel.SetThreadExecutionState
    request.argtypes = [wintypes.DWORD]
    request.restype = wintypes.DWORD
    continuous = 0x80000000
    system_required = 0x00000001
    interrupted = False

    def stop(_signum, _frame):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    started = time.monotonic()
    process_stamp = live_process_stamp(os.getpid())
    if process_stamp is None:
        raise RuntimeError("Cannot verify the holding process")

    def state(status, reason=None):
        data = {
            "status": status,
            "pid": os.getpid(),
            "process_stamp": process_stamp,
            "utc_s": time.time(),
            "elapsed_s": time.monotonic() - started,
            "lease_seconds": args.lease_seconds,
            "maximum_seconds": args.max_seconds,
            "reason": reason,
            "power_plan_changed": False,
            "display_required": False,
        }
        temporary = directory / "state.tmp"
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
        temporary.replace(directory / "state.json")
        print(json.dumps(data), flush=True)

    if not request(continuous | system_required):
        raise ctypes.WinError(ctypes.get_last_error())
    reason = "exception"
    last_stamp = None
    deadline = started + args.lease_seconds
    try:
        state("holding")
        while True:
            if interrupted or (directory / "stop").exists():
                reason = "stopped"
                break
            if time.monotonic() - started >= args.max_seconds:
                reason = "maximum lifetime"
                break
            # Observe each renewal through a monotonic deadline. Wall time is
            # audit-only, so changing Windows time cannot extend this request.
            lease_stamp = lease.stat().st_mtime_ns
            if lease_stamp != last_stamp:
                last_stamp = lease_stamp
                deadline = time.monotonic() + args.lease_seconds
            if time.monotonic() >= deadline:
                reason = "lease expired"
                break
            time.sleep(1)
    finally:
        if not request(continuous):
            raise ctypes.WinError(ctypes.get_last_error())
        state("released", reason)


if __name__ == "__main__":
    main()
