"""Check versioned station configuration against the pinned static bus network."""

import argparse
import hashlib
import json
import shutil
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

import sumolib

from evdt.config import ROOT
from evdt.contract_profile import decode, validator
from scripts.known_route_energy.build_station_navigation import build

SCHEMA = "charging/station-catalog-1.0.0.schema.json"
SCENARIO = ROOT / "simulation/montreal"
OSM = ROOT / "data/public/montreal-core-v1/roads.osm.xml"
BAY_CLEARANCE_M = 4


def check(catalog_path, scenario, osm, work):
    raw = catalog_path.read_bytes()
    catalog = decode(raw)
    validator(SCHEMA).validate(catalog)
    stations = catalog["stations"]
    if len({row["station_id"] for row in stations}) != len(stations):
        raise ValueError("Station IDs must be unique")
    for station in stations:
        stop = station["approach"]
        if stop["start_m"] >= stop["end_m"]:
            raise ValueError(f"{station['station_id']}: invalid stop interval")

    mission = json.loads((scenario / "mission.json").read_bytes())
    if catalog["mission_id"] != mission["mission_id"]:
        raise ValueError("Catalog mission does not match the network fixture")
    bus = json.loads((scenario / "bus-parameters.json").read_bytes())
    bus_length = bus["parameters"]["length"]["value"]
    staged = work / "scenario"
    staged.mkdir()
    for name in ("mission.json", "import-review.json"):
        shutil.copyfile(scenario / name, staged / name)
    (staged / "stations.json").write_bytes(raw)
    navigation = build(staged, osm, work)
    if navigation["vehicles"] != 0 or navigation["physical_steps"] != 0:
        raise ValueError("Station approach validation must remain static")
    net = sumolib.net.readNet(str(work / "network.net.xml"), withInternal=True)
    origin = net.getEdge(mission["route_edges"][0])
    finish = net.getEdge(mission["route_edges"][-1])
    verified = []
    for station, projected in zip(stations, navigation["stations"], strict=True):
        identity = station["station_id"]
        stop = station["approach"]
        lane = net.getLane(stop["lane_id"])
        edge = lane.getEdge()
        if not lane.allows("bus") or edge.getFunction() == "internal":
            raise ValueError(f"{identity}: stop must be on an external bus lane")
        if stop["lane_id"] != projected["lane_id"]:
            raise ValueError(f"{identity}: map pin projects onto {projected['lane_id']}")
        if stop["end_m"] >= lane.getLength():
            raise ValueError(f"{identity}: stop ends outside the usable lane")
        stop_length = stop["end_m"] - stop["start_m"]
        required_length = station["bay_count"] * (bus_length + BAY_CLEARANCE_M)
        if stop_length < required_length:
            raise ValueError(f"{identity}: stop is too short for its declared bays")
        inbound, inbound_m = net.getShortestPath(
            origin, edge, vClass="bus", fromPos=0, toPos=stop["end_m"]
        )
        outbound, outbound_m = net.getShortestPath(
            edge, finish, vClass="bus", fromPos=stop["end_m"], toPos=finish.getLength()
        )
        if inbound is None or outbound is None:
            raise ValueError(f"{identity}: no directed path from origin or to finish")
        verified.append(
            {
                "station_id": identity,
                "lane_id": lane.getID(),
                "lane_length_m": round(lane.getLength(), 3),
                "stop_length_m": round(stop_length, 3),
                "map_pin_to_lane_m": round(projected["snap_distance_m"], 3),
                "origin_to_stop_m": round(inbound_m, 3),
                "stop_to_finish_m": round(outbound_m, 3),
            }
        )
    return {
        "passed": True,
        "scope": "Static schema, mapped stop intervals and directed bus paths",
        "catalog_sha256": hashlib.sha256(raw).hexdigest(),
        "source_sha256": {
            path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__).resolve(),
                ROOT / "contracts" / SCHEMA,
                ROOT / "contracts/profile.json",
                ROOT / "scripts/known_route_energy/build_station_navigation.py",
                ROOT / "src/evdt/contract_profile.py",
                ROOT / "src/evdt/monitoring/network.py",
                scenario / "bus-parameters.json",
            )
        },
        "navigation_input_sha256": navigation["input_sha256"],
        "sumo_version": version("eclipse-sumo"),
        "physical_steps": 0,
        "vehicles": 0,
        "stations": verified,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=SCENARIO / "charging-stations-1.0.0.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output file")
    with TemporaryDirectory() as directory:
        result = check(args.catalog, SCENARIO, OSM, Path(directory))
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")
    print(f"Verified {len(result['stations'])} hypothetical station profiles without driving.")


if __name__ == "__main__":
    main()
