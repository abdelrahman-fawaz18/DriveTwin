"""Trusted two-asset qualification policy, with original source-specific inputs."""

import argparse
import json
from pathlib import Path

from evdt.monitoring.contracts import monitoring_contract
from evdt.monitoring.formats import operational_format
from evdt.monitoring.recipes import inputs
from evdt.monitoring.run_policy import validate_policy
from evdt.monitoring.sensors import SensorBank


def expected(scenario, osm, version):
    mission = json.loads((scenario / "mission.json").read_text())
    plant = json.loads((scenario / "plant.json").read_text())
    bus = json.loads((scenario / "bus-parameters.json").read_text())["parameters"]
    catalog = monitoring_contract().readers[version].catalog
    return {
        "schema_version": operational_format(version)["schema_version"],
        "catalog_version": version,
        "model_version": plant["model_version"],
        "input_sha256": inputs(scenario, osm, version),
        "step_s": mission["step_s"],
        "signals": SensorBank(catalog, mission["step_s"], plant["sampling"]).timings(),
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    policy = {
        "policy_version": "run-policy/1.0.0",
        "mode": "simulation",
        "run_id": args.run_id,
        "sources": [
            {
                "asset_id": asset,
                "source_id": "sumo-adapter",
                "expected": expected(Path(scenario), Path("/input/roads.osm.xml"), version),
            }
            for asset, scenario, version in [
                ("bus-001", "/scenario", "1.2.0"),
                ("bus-002", "/scenario2", "1.3.0"),
            ]
        ],
    }
    raw = (json.dumps(policy, sort_keys=True, indent=2) + "\n").encode()
    validate_policy(raw)
    with args.output.open("xb") as f:
        f.write(raw)
    print("Independent two-asset trusted policy prepared")


if __name__ == "__main__":
    main()
