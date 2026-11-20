"""Prepare trusted fleet policy and per-asset map context from one explicit scenario."""

import argparse
import copy
import json
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from evdt.monitoring.extension_policy import expected
from evdt.monitoring.map_context import build
from evdt.monitoring.run_policy import validate_policy

ASSETS = tuple(f"bus-{i:03}" for i in range(1, 11))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, default=Path("/sustained"))
    args = parser.parse_args()
    osm = Path("/input/roads.osm.xml")
    spec = expected(args.scenario, osm, "1.4.0")
    policy = {
        "policy_version": "run-policy/1.0.0",
        "mode": "simulation",
        "run_id": args.run_id,
        "sources": [
            {"asset_id": asset, "source_id": "sumo-adapter", "expected": copy.deepcopy(spec)}
            for asset in ASSETS
        ],
    }
    raw = (json.dumps(policy, sort_keys=True, indent=2) + "\n").encode()
    validate_policy(raw)
    (args.output / "run_policy.json").write_bytes(raw)
    with tempfile.TemporaryDirectory() as work:
        context = build(args.scenario, osm, args.run_id, ASSETS[0], Path(work))
        net = ET.parse(Path(work) / "network.net.xml").getroot()
        lengths = {
            edge.attrib["id"]: float(edge.find("lane").attrib["length"])
            for edge in net.findall("edge")
            if edge.find("lane") is not None
        }
        edges = json.loads((args.scenario / "mission.json").read_bytes())["route_edges"]
        route_length = sum(lengths[e] for e in edges)
    for index, asset in enumerate(ASSETS, 1):
        context["asset_id"] = asset
        name = "map_context.json" if index == 1 else f"map_context{index}.json"
        (args.output / name).write_text(json.dumps(context, sort_keys=True) + "\n")
    print(
        "SOURCE_SETUP_RESULT="
        + json.dumps(
            {
                "assets": list(ASSETS),
                "route_length_m": route_length,
                "format": ["3.2.0", "1.4.0", "source-run/2.2.0"],
            }
        )
    )


if __name__ == "__main__":
    main()
