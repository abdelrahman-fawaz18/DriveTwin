"""Optional run/boot-bound common source clock for wall-time qualification."""

import argparse
import json
import math
import time
from pathlib import Path

from evdt.workload.correlation import require


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def atomic_new(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps(value, allow_nan=False) + "\n")
    # The directory belongs to one qualification run. Refuse stale releases.
    require(not path.exists(), "Pacing artifact already exists")
    temporary.replace(path)


def validate_release(release, *, run_id, asset_id, boot, now):
    require(release.get("version") == "source-start/1.0.0", "Unknown pacing release")
    require(release.get("run_id") == run_id, "Pacing run mismatch")
    require(release.get("boot_id") == boot, "Pacing boot mismatch")
    assets = release.get("assets", [])
    require(
        isinstance(assets, list) and len(assets) == len(set(assets)) and asset_id in assets,
        "Pacing asset mismatch",
    )
    target = release.get("start_monotonic_s")
    require(
        isinstance(target, (float, int))
        and not isinstance(target, bool)
        and math.isfinite(target)
        and 0 < target - now <= 30,
        "Pacing start is expired or unbounded",
    )
    return target


def await_start(directory, identity):
    asset = identity["asset_id"]
    require(asset in [f"bus-{i:03}" for i in range(1, 11)], "Unknown workload asset")
    boot = boot_id()
    atomic_new(
        directory / ("ready-" + asset + ".json"),
        {
            "version": "source-ready/1.0.0",
            **identity,
            "boot_id": boot,
            "ready_monotonic_s": time.monotonic(),
        },
    )
    deadline = time.monotonic() + 180
    while not (directory / "start.json").exists():
        require(time.monotonic() < deadline, "Pacing release deadline exceeded")
        time.sleep(0.05)
    release = json.loads((directory / "start.json").read_bytes())
    target = validate_release(
        release, run_id=identity["run_id"], asset_id=asset, boot=boot, now=time.monotonic()
    )
    while time.monotonic() < target:
        time.sleep(min(0.05, max(0, target - time.monotonic())))
    return target


def release(directory, run_id, assets):
    require(len(assets) == len(set(assets)) and assets, "Duplicate/empty pacing asset set")
    boot = boot_id()
    for asset in assets:
        require(asset in [f"bus-{i:03}" for i in range(1, 11)], "Unknown workload asset")
        row = json.loads((directory / ("ready-" + asset + ".json")).read_bytes())
        require(
            row["version"] == "source-ready/1.0.0"
            and row["run_id"] == run_id
            and row["asset_id"] == asset
            and row["boot_id"] == boot
            and 0 <= time.monotonic() - row["ready_monotonic_s"] < 180,
            "Source readiness does not match the live run",
        )
    result = {
        "version": "source-start/1.0.0",
        "run_id": run_id,
        "boot_id": boot,
        "assets": assets,
        "start_monotonic_s": time.monotonic() + 5,
        "released_utc_s": time.time(),
    }
    atomic_new(directory / "start.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--assets", nargs="+", required=True)
    args = parser.parse_args()
    deadline = time.monotonic() + 120
    while not all(
        (args.directory / ("ready-" + asset + ".json")).is_file() for asset in args.assets
    ):
        require(time.monotonic() < deadline, "Not all sources reached the pacing barrier")
        time.sleep(0.1)
    print(json.dumps(release(args.directory, args.run_id, args.assets)))


if __name__ == "__main__":
    main()
