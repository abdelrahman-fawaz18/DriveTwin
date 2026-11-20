"""Run explicit sustained-source profiles without resetting physical state at lap boundaries."""

import argparse
import hashlib
import json
import math
from pathlib import Path

from evdt.monitoring.contracts import monitoring_contract
from evdt.monitoring.source import SumoVehicleSource


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run_prefix(scenario, osm, output, count, asset="bus-001"):
    source = SumoVehicleSource(
        scenario, osm, output, asset, count, paced=False, catalog_version="1.4.0"
    )
    expected = set(monitoring_contract().readers["1.4.0"].catalog["signals"])
    digest = hashlib.sha256()
    frames = source.frames()
    samples = 0
    checkpoints = {}
    try:
        for frame in frames:
            values = {s["signal_id"]: s["value"] for s in frame.samples}
            require(
                set(values) == expected
                and all(isinstance(v, (int, float)) and math.isfinite(v) for v in values.values()),
                "Initial scalar coverage changed",
            )
            require(
                0 < values["battery.pack.soc"] <= 1,
                "Sustained source depleted or exceeded capacity",
            )
            digest.update(
                json.dumps(
                    [frame.sim_time_s, values], sort_keys=True, separators=(",", ":")
                ).encode()
                + b"\n"
            )
            samples += 1
            if samples in (1, 900, 1800, 1860, 2700, 3600, 3660):
                checkpoints[str(samples)] = values
    finally:
        frames.close()
    require(
        samples == count and not source.summary["mission_finished"],
        "Mission ended before the required bounded prefix",
    )
    rows = source.step_trace
    for row in rows:
        v = row["values"]
        require(
            abs(v["motion.heading"] - math.radians(row["sumo_heading_deg"])) < 1e-12,
            "Heading conversion changed",
        )
        require(
            abs(v["battery.pack.power"] - v["battery.pack.current"] * v["battery.pack.voltage"])
            < 1e-6,
            "Electrical power identity changed",
        )
        require(
            abs(
                v["battery.pack.energy"]
                - v["battery.pack.soc"] * source.summary["energy_ledger"]["capacity_j"]
            )
            < 1e-5,
            "State of charge disagrees with SUMO energy",
        )
    require(
        checkpoints[str(count)]["motion.distance"] > (8000 if count == 1860 else 16000),
        "Sustained workload did not continue driving through multiple laps",
    )
    require(
        checkpoints[str(count)]["battery.pack.energy"] < checkpoints["1"]["battery.pack.energy"],
        "Battery state reset or did not evolve",
    )
    return {
        "passed": True,
        "asset_id": asset,
        "sample_events": samples,
        "scalar_samples": samples * 20,
        "physical_intervals": len(rows),
        "trace_sha256": digest.hexdigest(),
        "completed_sim_time_s": source.summary["completed_sim_time_s"],
        "mission_finished": source.summary["mission_finished"],
        "checkpoints": checkpoints,
        "energy_ledger": source.summary["energy_ledger"],
        "scope": "accelerated model/scheduling-length validation, not a wall-time benchmark",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=Path("/scenario"))
    parser.add_argument("--osm", type=Path, default=Path("/input/roads.osm.xml"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    result = {"passed": False, "profiles": {}}
    try:
        for label, count in [("W0_plus_warmup", 1860), ("W1_plus_warmup", 3660)]:
            runs = [
                run_prefix(args.scenario, args.osm, args.output / f"{label}-{i}", count)
                for i in (1, 2)
            ]
            require(
                runs[0]["trace_sha256"] == runs[1]["trace_sha256"],
                "Sustained numeric trace was not repeatable",
            )
            result["profiles"][label] = runs
        result["passed"] = True
    finally:
        (args.output / "qualification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        "SUSTAINED_RESULT="
        + json.dumps(
            {
                "passed": result["passed"],
                "profiles": {
                    k: [{a: b for a, b in run.items() if a != "checkpoints"} for run in v]
                    for k, v in result["profiles"].items()
                },
            }
        )
    )


if __name__ == "__main__":
    main()
