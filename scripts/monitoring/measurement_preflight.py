"""Measure active-Ubuntu host headroom before starting a heavy workload."""

import json
import subprocess
import sys
import time
from pathlib import Path

from windows_resources import Sampler

ROOT = Path(__file__).resolve().parents[2]


def run(evidence):
    from host_memory_policy import assess
    from measurement_report import read_rows

    sampler = None
    result = {"passed": False, "scope": "active Ubuntu host preflight; not a workload result"}
    with (evidence / "preflight-linux.log").open("w", encoding="utf-8") as log:
        active = subprocess.Popen(
            [
                sys.executable,
                "scripts/linux.py",
                "--timeout",
                "60",
                "--",
                "python3",
                "-c",
                "import time; print('ACTIVE', flush=True); time.sleep(45)",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            while "ACTIVE" not in (evidence / "preflight-linux.log").read_text(encoding="utf-8"):
                if active.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Active Ubuntu preflight did not start")
                time.sleep(0.2)
            sampler = Sampler(evidence / "preflight-windows.jsonl")
            sampler.start()
            time.sleep(35)
            sampler.close()
            sampler = None
            if active.poll() is not None:
                raise RuntimeError("Ubuntu exited before the host sampling completed")
            rows = read_rows(evidence / "preflight-windows.jsonl")
            from evdt.workload.correlation import resource_summary

            measured = resource_summary(rows, platform="windows")
            host = assess(rows)
            result.update(
                windows_resources=measured,
                local_host=host,
                passed=host["passed"],
            )
            if not all(
                r["wsl_name_total_bytes"] is not None and r["wsl_name_total_bytes"] > 0
                for r in rows
            ):
                raise RuntimeError("Active Ubuntu working-set coverage missing")
        except Exception as exc:
            result.update(passed=False, error=str(exc))
        finally:
            if sampler is not None:
                sampler.close()
            # The small helper exits on its own; never shut down Ubuntu or user apps.
            try:
                active.wait(timeout=55)
            except subprocess.TimeoutExpired:
                active.terminate()
                active.wait(timeout=10)
                result.update(passed=False, error="Active Ubuntu helper exceeded its deadline")
            if active.returncode:
                result.update(passed=False, error="Active Ubuntu helper failed")
    (evidence / "preflight.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return result
