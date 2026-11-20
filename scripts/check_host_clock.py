"""Sample Windows and Ubuntu clocks without starting the monitoring stack."""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECOND_NS = 1_000_000_000
CLOCK_LIMIT_NS = 10_000_000
MAX_GAP_NS = 1_500_000_000


def clock_sample():
    before = time.monotonic_ns()
    wall = time.time_ns()
    after = time.monotonic_ns()
    return {"elapsed_ns": (before + after) // 2, "utc_ns": wall, "bracket_ns": after - before}


def linux_peer():
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    print(json.dumps({"boot_id": boot}), flush=True)
    for line in sys.stdin:
        if line.strip() != "sample":
            raise ValueError("Unexpected clock probe request")
        print(json.dumps(clock_sample()), flush=True)
    return 0


def summarize(rows, seconds):
    result = {"samples": len(rows), "planned_seconds": seconds}
    for name in ("windows", "ubuntu"):
        clocks = [row[name] for row in rows]
        origin = clocks[0]["utc_ns"] - clocks[0]["elapsed_ns"]
        gaps = [b["elapsed_ns"] - a["elapsed_ns"] for a, b in zip(clocks, clocks[1:])]
        elapsed = clocks[-1]["elapsed_ns"] - clocks[0]["elapsed_ns"]
        drift = max(abs(c["utc_ns"] - c["elapsed_ns"] - origin) for c in clocks)
        max_bracket = max(c["bracket_ns"] for c in clocks)
        result[name] = {
            "elapsed_seconds": round(elapsed / SECOND_NS, 6),
            "max_clock_departure_ms": round(drift / 1e6, 6),
            "max_sample_gap_ms": round(max(gaps, default=0) / 1e6, 6),
            "max_clock_read_bracket_ms": round(max_bracket / 1e6, 6),
            "passed": (
                len(clocks) == seconds + 1
                and elapsed >= (seconds - 0.01) * SECOND_NS
                and all(0 < gap <= MAX_GAP_NS for gap in gaps)
                and drift <= CLOCK_LIMIT_NS
                and max_bracket <= CLOCK_LIMIT_NS
            ),
        }
    # A request bounds the guest read between two Windows UTC observations.
    # Use this as a diagnostic for absolute agreement, separate from clock steps.
    result["ubuntu_minus_windows_utc_interval_ms"] = [
        round(min(row["offset_low_ns"] for row in rows) / 1e6, 6),
        round(max(row["offset_high_ns"] for row in rows) / 1e6, 6),
    ]
    result["passed"] = result["windows"]["passed"] and result["ubuntu"]["passed"]
    return result


def collect(seconds, output):
    launcher = [
        sys.executable,
        "scripts/linux.py",
        "--timeout",
        str(seconds + 35),
        "--",
        "python3",
        "scripts/check_host_clock.py",
        "--_linux-peer",
    ]
    rows = []
    with (
        (output / "samples.jsonl").open("x", encoding="utf-8", newline="\n") as evidence,
        subprocess.Popen(
            launcher,
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            encoding="utf-8",
            text=True,
        ) as child,
    ):
        try:
            boot = json.loads(child.stdout.readline())["boot_id"]
            start = time.monotonic_ns()
            for index in range(seconds + 1):
                time.sleep(max(0, (start + index * SECOND_NS - time.monotonic_ns()) / SECOND_NS))
                windows = clock_sample()
                child.stdin.write("sample\n")
                child.stdin.flush()
                line = child.stdout.readline()
                received_ns = time.time_ns()
                ubuntu = json.loads(line)
                row = {
                    "sequence": index,
                    "boot_id": boot,
                    "windows": windows,
                    "ubuntu": ubuntu,
                    "offset_low_ns": ubuntu["utc_ns"] - received_ns,
                    "offset_high_ns": ubuntu["utc_ns"] - windows["utc_ns"],
                }
                rows.append(row)
                evidence.write(json.dumps(row) + "\n")
                evidence.flush()
        finally:
            child.stdin.close()
        if child.wait(timeout=10):
            raise RuntimeError("Ubuntu clock probe exited unsuccessfully")
    return summarize(rows, seconds)


def main():
    if sys.argv[1:] == ["--_linux-peer"] and sys.platform == "linux":
        return linux_peer()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=150)
    args = parser.parse_args()
    if os.name != "nt" or not 3 <= args.seconds <= 300:
        parser.error("Run from Windows with a sample duration from 3 to 300 seconds")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    output = ROOT / "evidence/p6" / f"clock-check-{stamp}"
    output.mkdir(parents=True)
    print(
        f"Sampling clocks only for {args.seconds} seconds. Evidence: {output.relative_to(ROOT)}",
        flush=True,
    )
    report = collect(args.seconds, output)
    (output / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
