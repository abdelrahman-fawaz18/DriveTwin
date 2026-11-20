"""Prepare a trusted local run policy from reviewed source/configuration inputs."""

import argparse
import json
from pathlib import Path

from evdt.monitoring.contracts import monitoring_contract
from evdt.monitoring.recipes import inputs
from evdt.monitoring.run_policy import validate_policy
from evdt.monitoring.sensors import SensorBank


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scenario", type=Path, default=Path("/scenario"))
    parser.add_argument("--osm", type=Path, default=Path("/input/roads.osm.xml"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    mission = json.loads((args.scenario / "mission.json").read_text())
    plant = json.loads((args.scenario / "plant.json").read_text())
    bus = json.loads((args.scenario / "bus-parameters.json").read_text())["parameters"]
    catalog = monitoring_contract().catalog
    bank = SensorBank(catalog, mission["step_s"], plant["sampling"])
    expected = {
        "schema_version": "3.0.0",
        "catalog_version": "1.2.0",
        "model_version": plant["model_version"],
        "input_sha256": inputs(args.scenario, args.osm),
        "step_s": mission["step_s"],
        "signals": bank.timings(),
        "heartbeat_deadline_s": 5,
        "paced": True,
        "initial_energy_j": bus["usable_battery_energy"]["value"]
        * 3_600_000
        * bus["initial_soc"]["value"],
        "capacity_j": bus["usable_battery_energy"]["value"] * 3_600_000,
        "packages": {name: "1.28.0" for name in ("eclipse-sumo", "traci", "sumolib", "sumo-data")},
        "traci_protocol": 22,
        "sumo_version": "SUMO 1.28.0",
    }
    policy = {
        "policy_version": "run-policy/1.0.0",
        "run_id": args.run_id,
        "mode": "simulation",
        "sources": [
            {"asset_id": asset, "source_id": source, "expected": expected}
            for asset, item in catalog["assets"].items()
            for source in item["sources"]
        ],
    }
    raw = (json.dumps(policy, sort_keys=True, indent=2) + "\n").encode()
    validate_policy(raw)
    with args.output.open("xb") as output:
        output.write(raw)
    print("Trusted local run policy prepared", flush=True)


if __name__ == "__main__":
    main()
