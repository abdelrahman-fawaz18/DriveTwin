"""Submit completed-step observations with the separate observer credential."""

import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import UUID

from command_wire import post
from live_speed_evidence import HISTORY, speeds
from run_speed_limit import read_speed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command_id", type=UUID)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    run, asset = os.environ["EVDT_RUN_ID"], os.environ["COMMAND_ASSET_ID"]
    if args.live:
        reference = json.loads(Path("/evidence/reference-observations.json").read_bytes())
        token = Path("/run/secrets/command_monitor_reader").read_text().strip()
        request = Request(
            "http://api:8000" + HISTORY.format(asset=asset),
            headers={"Authorization": "Bearer " + token},
        )
        with urlopen(request, timeout=4) as response:
            current = json.load(response)
        if reference["run_id"] == run or current["run_id"] != run:
            raise ValueError("Observation history belongs to the wrong live run")
        baseline, commanded = speeds(reference, asset), speeds(current, asset)
    else:
        drive = Path("/evidence/drive")
        baseline = read_speed(drive / "baseline/observations.json")
        commanded = read_speed(drive / "commanded/observations.json")
    value = {
        "schema_version": "evdt-speed-evidence/1.0.0",
        "command_id": str(args.command_id),
        "run_id": run,
        "asset_id": asset,
        "signal_id": "motion.speed",
        "unit": "m/s",
        "baseline": baseline,
        "commanded": commanded,
    }
    post("effect", "observer", json.dumps(value, allow_nan=False).encode())


if __name__ == "__main__":
    main()
