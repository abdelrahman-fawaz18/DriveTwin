"""Check or attempt a frozen Phase 6 monitoring profile."""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/monitoring"))


def main():
    from smoke_policy import CAPACITY, SMOKE, monitoring_profile, verify_sources

    if sys.argv[1:2] == ["--check"]:
        requested = sys.argv[2:] or [SMOKE]
        if len(requested) != 1 or requested[0] not in (SMOKE, *CAPACITY):
            raise SystemExit("Use --check with monitoring-smoke, W0-30 or W1-30")
        workload = requested[0]
        print(
            json.dumps(
                {
                    "source": verify_sources(committed=False, workload=workload),
                    "profile": monitoring_profile(workload),
                },
                indent=2,
            )
        )
        return 0
    if os.name != "nt":
        raise SystemExit("Start the Phase 6 controller in Windows PowerShell")
    import msvcrt

    from qualify_extension import main as run

    path = ROOT / ".runtime/p6-monitoring.lock"
    path.parent.mkdir(exist_ok=True)
    with path.open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise SystemExit(
                "An owned Phase 6 controller already holds the local run lock"
            ) from exc
        try:
            return run(workload_measurement=True, sustained=True, phase6=True)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    raise SystemExit(main())
