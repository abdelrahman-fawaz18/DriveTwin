"""Apply the existing host guards to the bounded Energy and Operator browser checks."""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / p) for p in ("scripts/release", "scripts/release_checks", "scripts/commands", "scripts/monitoring", "src")
]
from qualify_restarts import monitor  # noqa: E402
from smoke_policy import WindowsSmokeGuard, preflight  # noqa: E402


class ReviewGuard:
    def __init__(self, output, *, project=None, linux_failure=None):
        from windows_resources import Sampler

        self.output = output / "host"
        self.output.mkdir()
        self.active = False
        self.collector = None
        self.log = None
        self.sampler = None
        self.guard = WindowsSmokeGuard(
            linux_failure or self.output / "linux-guard.json", live_clock=True
        )
        result = preflight(self.output)
        if not result["passed"]:
            raise RuntimeError("Host precheck held the service review; no services started")
        try:
            self.sampler = Sampler(
                self.output / "windows-resources.jsonl", on_sample=self.guard.observe
            )
            self.sampler.start()
            self.active = True
            deadline = time.monotonic() + 6
            while not self.guard.first_sample_validated:
                self.check()
                if time.monotonic() > deadline:
                    raise RuntimeError("Initial Windows resource sample missing")
                time.sleep(0.1)
            if project:
                self.log = (self.output / "collector.log").open("xb")
                self.collector = subprocess.Popen(
                    [
                        sys.executable,
                        "scripts/linux.py",
                        "--timeout",
                        "1500",
                        "--",
                        "python3",
                        "scripts/release/dashboard_service_guard.py",
                        "--project",
                        project,
                        "--output",
                        self.output.relative_to(ROOT).as_posix(),
                    ],
                    cwd=ROOT,
                    stdout=self.log,
                    stderr=self.log,
                )
                deadline = time.monotonic() + 15
                while not (self.output / "collector-ready").exists():
                    self.check()
                    if self.collector.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("Linux resource collector did not become ready")
                    time.sleep(0.1)
        except BaseException:
            self.close()
            raise

    def check(self):
        if self.sampler and (self.sampler.error or self.guard.failure):
            raise RuntimeError(str(self.sampler.error or self.guard.failure))
        if self.collector and self.collector.poll() is not None:
            raise RuntimeError("Linux resource collector stopped unexpectedly")

    def wait(self, process, timeout):
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            self.check()
            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(process.args, timeout)
            time.sleep(0.2)
        self.check()
        return process.returncode

    def run(self, args, *, input=None, timeout=180, check=False, **kwargs):
        if not self.active:
            return subprocess.run(args, input=input, timeout=timeout, check=check, **kwargs)
        self.check()
        args = list(args)
        invocation = None
        if len(args) > 1 and Path(args[1]).name == "linux.py":
            invocation = uuid4().hex
            args[2:2] = ["--run-id", invocation]
        capture = kwargs.pop("capture_output", False)
        if capture:
            kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if input is not None:
            kwargs["stdin"] = subprocess.PIPE
        process = subprocess.Popen(args, **kwargs)
        deadline = time.monotonic() + timeout
        try:
            while True:
                self.check()
                if time.monotonic() > deadline:
                    raise subprocess.TimeoutExpired(args, timeout)
                try:
                    stdout, stderr = process.communicate(input=input, timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    input = None
            self.check()
            result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
            if check:
                result.check_returncode()
            return result
        finally:
            if process.poll() is None:
                if invocation:
                    subprocess.run(
                        [sys.executable, "scripts/linux.py", "--cancel", invocation],
                        cwd=ROOT,
                        timeout=20,
                        check=True,
                        capture_output=True,
                    )
                    process.wait(timeout=60)
                else:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        timeout=15,
                        capture_output=True,
                        check=True,
                    )
                    process.wait(timeout=10)

    def finish_measurement(self):
        self.check()
        ended = time.monotonic()
        if self.guard.previous is None or ended - self.guard.previous > 1.5:
            raise RuntimeError("Resource samples did not cover service completion")
        if self.guard.low_start is not None and ended - self.guard.low_start > 30:
            raise RuntimeError("Windows available RAM below 2 GiB through completion")

    def close(self):
        self.active = False
        try:
            if self.collector:
                (self.output / "collector-stop").touch()
                if self.collector.wait(timeout=12) != 0:
                    raise RuntimeError("Linux resource guard failed")
        finally:
            if self.log:
                self.log.close()
            if self.sampler:
                self.sampler.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if os.name == "nt" or not re.fullmatch(r"evdt-e2c-[a-f0-9]{12}", args.project):
        parser.error("Require the exact owned Linux review project")
    if not output.is_relative_to(ROOT / "evidence") or not output.is_dir():
        parser.error("Require an existing private evidence directory")
    try:
        with monitor(args.project, output, allow_replacement=True):
            (output / "collector-ready").touch()
            deadline = time.monotonic() + 1450
            while not (output / "collector-stop").exists():
                if time.monotonic() > deadline or (output / "linux-guard.json").exists():
                    raise RuntimeError("Linux review guard stopped")
                time.sleep(0.2)
        return 0
    except Exception as exc:
        (output / "linux-guard.json").write_text(json.dumps({"reason": str(exc)}) + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
