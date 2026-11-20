"""Explicit local-host warnings and fail-closed emergency guards, not capacity proof."""

import json
import math
import os
import signal
import subprocess
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY = {
    "version": "phase1-local-host/1.0.1",
    "warning_available_bytes": 2 * 1024**3,
    "warning_duration_s": 30,
    "emergency_available_bytes": 512 * 1024**2,
    "emergency_commit_headroom_bytes": 1024**3,
    "emergency_duration_s": 10,
    "capacity_qualification_phase": 6,
    "setup_reclaim_policy": "clean-file-cache-before-browser/1.0.0",
}


def reclaim_setup_cache():
    """Discard clean setup/build cache before browsers/sources, with monitoring still active."""
    if os.name != "nt":
        raise RuntimeError("Use the Windows controller for explicit WSL setup preparation")
    settings = tomllib.loads((ROOT / "environments/dev.toml").read_text(encoding="utf-8"))
    code = (
        "import json,os;"
        "cached=lambda: int(next(x.split()[1] for x in open('/proc/meminfo') "
        "if x.startswith('Cached:')))*1024;"
        "before=cached();os.sync();open('/proc/sys/vm/drop_caches','w').write('3');"
        "print(json.dumps({'before_cached_bytes':before,'after_cached_bytes':cached()}))"
    )
    result = subprocess.run(
        [
            "wsl.exe",
            "--distribution",
            settings["linux"]["distribution"],
            "--user",
            "root",
            "--exec",
            "python3",
            "-c",
            code,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    measured = json.loads(result.stdout)
    if set(measured) != {"before_cached_bytes", "after_cached_bytes"} or any(
        type(value) is not int or value < 0 for value in measured.values()
    ):
        raise ValueError("Invalid setup-cache preparation evidence")
    return {"policy": POLICY["setup_reclaim_policy"], **measured}


def evidence_destination(path):
    relative = path.resolve().relative_to(ROOT.resolve())
    if relative.parts[:2] != ("evidence", "p1"):
        raise ValueError("Partial evidence must stay under evidence/p1")
    return relative.as_posix()


def policy():
    configured = json.loads((ROOT / "benchmarks/monitoring-host.json").read_bytes())
    if configured != POLICY:
        raise ValueError("Unreviewed local host-memory policy")
    return configured


def values(row):
    stamp = row["monotonic_s"]
    physical = row["physical_available_bytes"]
    commit = row["commit"]
    if not isinstance(commit, dict):
        raise ValueError("Invalid commit observation")
    if (
        isinstance(stamp, bool)
        or not isinstance(stamp, (float, int))
        or not math.isfinite(stamp)
        or stamp < 0
        or any(type(v) is not int or v < 0 for v in (physical, *commit.values()))
        or set(commit) != {"total_bytes", "limit_bytes", "available_bytes"}
        or commit["limit_bytes"] <= 0
        or commit["total_bytes"] + commit["available_bytes"] != commit["limit_bytes"]
    ):
        raise ValueError("Invalid physical/commit memory observation")
    return stamp, physical, commit["available_bytes"]


class Guard:
    """Use observed elapsed time; gaps and missing counters abort rather than disappear."""

    def __init__(self):
        self.policy = policy()
        self.previous = None
        self.low = {"physical": None, "commit": None}
        self.failure = None

    def observe(self, row):
        if self.failure:
            return
        try:
            now, physical, commit = values(row)
            if self.previous is not None and not 0 < now - self.previous <= 1.5:
                raise ValueError("Host-memory sample coverage gap")
            for name, value, floor in (
                ("physical", physical, self.policy["emergency_available_bytes"]),
                ("commit", commit, self.policy["emergency_commit_headroom_bytes"]),
            ):
                if value < floor:
                    if self.low[name] is None:
                        self.low[name] = now if self.previous is None else self.previous
                    if now - self.low[name] >= self.policy["emergency_duration_s"]:
                        self.failure = {
                            "reason": name + " memory emergency",
                            "observed_duration_s": now - self.low[name],
                            "observed_bytes": value,
                            "floor_bytes": floor,
                        }
                else:
                    self.low[name] = None
            self.previous = now
        except (KeyError, TypeError, ValueError) as exc:
            self.failure = {"reason": str(exc)}


def low_upper(rows, field, floor):
    longest = 0.0
    began = None
    for i, row in enumerate(rows):
        stamp, physical, commit = values(row)
        value = physical if field == "physical" else commit
        if value < floor:
            if began is None:
                began = values(rows[max(0, i - 1)])[0]
        elif began is not None:
            longest = max(longest, stamp - began)
            began = None
    if began is not None:
        longest = max(longest, values(rows[-1])[0] - began + 1.5)
    return longest


def assess(rows):
    if len(rows) < 2:
        raise ValueError("Insufficient host-memory observations")
    guard = Guard()
    for row in rows:
        guard.observe(row)
    p = guard.policy
    physical = low_upper(rows, "physical", p["emergency_available_bytes"])
    commit = low_upper(rows, "commit", p["emergency_commit_headroom_bytes"])
    warning = low_upper(rows, "physical", p["warning_available_bytes"])
    return {
        "policy": p,
        "passed": not guard.failure and max(physical, commit) < p["emergency_duration_s"],
        "failure": guard.failure,
        "available_min_bytes": min(values(r)[1] for r in rows),
        "commit_headroom_min_bytes": min(values(r)[2] for r in rows),
        "physical_emergency_upper_s": physical,
        "commit_emergency_upper_s": commit,
        "below_2GiB_upper_s": warning,
        "capacity_warning": warning > p["warning_duration_s"],
        "host_capacity_qualified": False,
    }


class Emergency(RuntimeError):
    pass


def run_guarded(argv, *, cwd, env, sampler, guard, timeout):
    """Own exactly one fresh browser-runner tree and preserve its bounded output."""
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    deadline = time.monotonic() + timeout
    try:
        while True:
            if guard.failure or sampler.error:
                raise Emergency(json.dumps(guard.failure or {"reason": sampler.error}))
            if time.monotonic() >= deadline:
                raise TimeoutError("Owned browser runner exceeded its deadline")
            try:
                stdout, stderr = process.communicate(timeout=0.5)
                return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                pass
    finally:
        if process.poll() is None:
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
                process.communicate(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                if os.name == "nt" and process.poll() is None:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=10,
                        check=False,
                    )
                elif process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)
