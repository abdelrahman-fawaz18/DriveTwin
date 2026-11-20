"""Build a static bus-access profile; no vehicles, battery model or future traffic."""

import argparse
import hashlib
import json
import math
import socket
import subprocess
import tempfile
from pathlib import Path

import sumo
import sumolib
import traci

from evdt.config import ROOT
from evdt.monitoring.network import build_network


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def closest_position(lane, point):
    shape = lane.getShape()
    lengths = [math.dist(a, b) for a, b in zip(shape, shape[1:])]
    total = math.fsum(lengths)
    walked = 0
    best = None
    for a, b, length in zip(shape, shape[1:], lengths):
        if not length:
            continue
        dx, dy = b[0] - a[0], b[1] - a[1]
        t = max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (length * length)))
        distance = math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy)
        candidate = (distance, (walked + t * length) / total * lane.getLength())
        if best is None or candidate < best:
            best = candidate
        walked += length
    return best


def build(scenario, osm, work):
    network = Path(build_network(scenario, osm, work))
    net = sumolib.net.readNet(str(network), withInternal=True)
    mission = json.loads((scenario / "mission.json").read_bytes())
    stations = json.loads((scenario / "stations.json").read_bytes())["stations"]
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    with (work / "static-navigation.log").open("w") as log:
        process = subprocess.Popen(
            [
                str(Path(sumo.SUMO_HOME) / "bin/sumo"),
                "--net-file",
                str(network),
                "--remote-port",
                str(port),
                "--no-step-log",
                "true",
            ],
            stdout=log,
            stderr=log,
        )
        connection = None
        try:
            connection = traci.connect(
                port=port, host="127.0.0.1", numRetries=100, waitBetweenRetries=0.05, proc=process
            )
            if connection.vehicle.getIDCount() != 0:
                raise ValueError("Static navigation must not contain vehicles")
            approaches = []
            for station in stations:
                original_xy = connection.simulation.convertGeo(
                    station["longitude"], station["latitude"], fromGeo=True
                )
                candidates = []
                for road in net.getEdges(withInternal=False):
                    for lane in road.getLanes():
                        if lane.allows("bus"):
                            position = closest_position(lane, original_xy)
                            if position is not None:
                                candidates.append((*position, lane.getID(), lane))
                if not candidates:
                    raise ValueError("No external bus-allowed station approach")
                error, offset, _, lane = min(candidates, key=lambda p: (p[0], p[2]))
                edge = lane.getEdge().getID()
                xy = connection.simulation.convert2D(edge, offset, lane.getIndex())
                geo = connection.simulation.convertGeo(*xy)
                if not lane.allows("bus") or error > 50:
                    raise ValueError("Hypothetical station has no nearby bus-allowed approach")
                approaches.append(
                    {
                        "station_id": station["station_id"],
                        "hypothetical": True,
                        "station_lon_lat": [station["longitude"], station["latitude"]],
                        "approach_lon_lat": list(geo),
                        "edge_id": edge,
                        "lane_id": lane.getID(),
                        "offset_m": offset,
                        "snap_distance_m": error,
                        "availability": "unverified",
                        "charger_access": "Planning road approach only; physical connector/site access unverified",
                    }
                )
            roads = []
            for identity in sorted(set(mission["route_edges"])):
                edge = net.getEdge(identity)
                lanes = [lane for lane in edge.getLanes() if lane.allows("bus")]
                if not lanes:
                    raise ValueError("Known route contains a bus-inaccessible edge")
                lane = lanes[0]
                paths = {}
                for station in approaches:
                    target = net.getEdge(station["edge_id"])
                    path, distance = net.getShortestPath(
                        edge, target, vClass="bus", fromPos=0, toPos=station["offset_m"]
                    )
                    paths[station["station_id"]] = (
                        None
                        if path is None
                        else {
                            "edges": [e.getID() for e in path],
                            "distance_from_edge_start_m": distance,
                            "speed_limit_m_s": min(e.getSpeed() for e in path),
                            "internal_connectors_in_distance": True,
                        }
                    )
                roads.append(
                    {
                        "edge_id": identity,
                        "lane_id": lane.getID(),
                        "length_m": lane.getLength(),
                        "shape_lon_lat": [
                            list(connection.simulation.convertGeo(*p)) for p in lane.getShape()
                        ],
                        "bus_allowed": True,
                        "paths": paths,
                    }
                )
            return {
                "version": "static-station-navigation/1.0.0",
                "mission_id": mission["mission_id"],
                "vehicle_class": "bus",
                "method": "Pinned SUMO bus-class shortest directed paths with internal connectors; no vehicle simulation",
                "maximum_station_snap_m": 50,
                "maximum_location_match_m": 15,
                "ambiguity_margin_m": 2,
                "heading_tolerance_rad": 1.0471975511965976,
                "stations": approaches,
                "roads": roads,
                "input_sha256": {
                    "network.net.xml": sha(network),
                    "roads.osm.xml": sha(osm),
                    "mission.json": sha(scenario / "mission.json"),
                    "stations.json": sha(scenario / "stations.json"),
                    "import-review.json": sha(scenario / "import-review.json"),
                },
                "generator_sha256": sha(
                    ROOT / "scripts/known_route_energy/build_station_navigation.py"
                ),
                "attribution": "OpenStreetMap contributors; ODbL-1.0. Derived static road/approach geometry; hypothetical charging sites, not infrastructure availability.",
                "physical_steps": 0,
                "vehicles": 0,
            }
        finally:
            if connection is not None:
                connection.close(wait=False)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--osm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Do not overwrite an existing versioned profile")
    with tempfile.TemporaryDirectory() as directory:
        value = build(args.scenario, args.osm, Path(directory))
    args.output.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "stations": len(value["stations"]),
                "road_edges": len(value["roads"]),
                "vehicles": 0,
                "physical_steps": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
