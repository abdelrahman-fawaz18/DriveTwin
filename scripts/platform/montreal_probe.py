"""Import the reviewed OSM snapshot and qualify a project-owned bus mission."""

import argparse
import hashlib
import json
import math
import socket
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import sumo
import sumolib
import traci


def require(value, message):
    if not value:
        raise RuntimeError(message)


def binary(name):
    return str(Path(sumo.SUMO_HOME) / "bin" / name)


def network(folder):
    folder.mkdir()
    command = [
        binary("netconvert"),
        "--osm-files",
        "/input/roads.osm.xml",
        "--output-file",
        str(folder / "network.net.xml"),
        "--keep-edges.by-vclass",
        "bus",
        "--junctions.join",
        "true",
        "--output.original-names",
        "true",
        "--remove-edges.isolated",
        "true",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    (folder / "netconvert.log").write_text(result.stdout + result.stderr)
    require(result.returncode == 0, "Road import failed; inspect netconvert.log")
    messages = [
        line
        for line in (result.stdout + result.stderr).splitlines()
        if line.startswith(("Warning:", "Error:"))
    ]
    return sumolib.net.readNet(str(folder / "network.net.xml")), messages


def choose_mission(net, folder):
    # Fixed geographic anchors are design inputs, not imported service/schedule data.
    anchors = [(-73.573, 45.510), (-73.575, 45.516), (-73.565, 45.516), (-73.565, 45.510)]
    candidates = [e for e in net.getEdges() if e.allows("bus") and e.getLength() >= 30]
    chosen = []
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    with (folder / "projection.log").open("w") as log:
        process = subprocess.Popen(
            [
                binary("sumo"),
                "--net-file",
                str(folder / "network.net.xml"),
                "--remote-port",
                str(port),
                "--no-step-log",
                "true",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        connection = None
        try:
            connection = traci.connect(
                port=port, host="127.0.0.1", numRetries=100, waitBetweenRetries=0.05, proc=process
            )
            for lon, lat in anchors:
                x, y = connection.simulation.convertGeo(lon, lat, fromGeo=True)
                ordered = sorted(
                    candidates,
                    key=lambda e: (
                        min((px - x) ** 2 + (py - y) ** 2 for px, py in e.getShape()),
                        e.getID(),
                    ),
                )
                chosen.append(ordered[0])
        finally:
            try:
                if connection is not None:
                    connection.close(wait=False)
            finally:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
    edges = [chosen[0]]
    for start, end in zip(chosen, chosen[1:] + chosen[:1]):
        route, cost = net.getShortestPath(start, end, vClass="bus")
        require(route and math.isfinite(cost), "Geographic anchors are not bus-connected")
        edges.extend(route[1:])
    require(
        len(edges) >= 5 and sum(e.getLength() for e in edges) >= 500,
        "Mission too short for a useful movement fixture",
    )
    stop_edge = next(e for e in edges[1:-1] if e.getLength() > 40 and e != edges[0])
    lane = next(lane for lane in stop_edge.getLanes() if lane.allows("bus"))
    return {
        "mission_id": "montreal-core-loop-v1",
        "status": "hypothetical, not an STM route",
        "anchors_lon_lat": anchors,
        "route_edges": [e.getID() for e in edges],
        "route_length_m": sum(e.getLength() for e in edges),
        "stop": {"lane": lane.getID(), "end_pos_m": lane.getLength() * 0.6, "duration_s": 10},
        "step_s": 0.2,
        "seed": 20261103,
        "deadline_sim_s": 1800,
        "map_attribution": "OpenStreetMap contributors; ODbL-1.0",
        "scope": "Movement, bus-class graph access, coordinate and repeatability checks only",
    }


def run_once(folder, mission, parameters):
    net, import_messages = network(folder)
    review = json.loads(Path("/scenario/import-review.json").read_text())
    require(
        Counter(import_messages) == Counter(review["expected_messages"]),
        "Import messages changed; explicit map-model review required",
    )
    edges = [net.getEdge(name) for name in mission["route_edges"]]
    require(all(e.allows("bus") for e in edges), "Route uses a bus-disallowed edge")
    require(
        all(
            b in a.getOutgoing() and any(c.allows("bus") for c in a.getOutgoing()[b])
            for a, b in zip(edges, edges[1:])
        ),
        "Invalid bus-class route connection",
    )
    p = {name: data["value"] for name, data in parameters["parameters"].items()}
    routes = ET.Element("routes")
    ET.SubElement(
        routes,
        "vType",
        id="synthetic-bus",
        vClass="bus",
        length=str(p["length"]),
        width=str(p["width"]),
        maxSpeed=str(p["maximum_speed"]),
        accel=str(p["acceleration"]),
        decel=str(p["comfortable_deceleration"]),
        emergencyDecel=str(p["emergency_deceleration"]),
        tau=str(p["driver_tau"]),
        sigma=str(p["driver_sigma"]),
    )
    ET.SubElement(routes, "route", id="mission", edges=" ".join(mission["route_edges"]))
    vehicle = ET.SubElement(
        routes,
        "vehicle",
        id="bus-001",
        type="synthetic-bus",
        route="mission",
        depart="0",
        departSpeed="0",
    )
    stop = mission["stop"]
    ET.SubElement(
        vehicle,
        "stop",
        lane=stop["lane"],
        endPos=str(stop["end_pos_m"]),
        duration=str(stop["duration_s"]),
    )
    ET.ElementTree(routes).write(folder / "routes.rou.xml", encoding="utf-8", xml_declaration=True)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    command = [
        binary("sumo"),
        "--net-file",
        str(folder / "network.net.xml"),
        "--route-files",
        str(folder / "routes.rou.xml"),
        "--remote-port",
        str(port),
        "--step-length",
        str(mission["step_s"]),
        "--seed",
        str(mission["seed"]),
        "--no-step-log",
        "true",
        "--duration-log.disable",
        "true",
        "--tripinfo-output",
        str(folder / "tripinfo.xml"),
        "--tripinfo-output.write-unfinished",
        "true",
        "--xml-validation",
        "local",
    ]
    trace, departed, arrived, collisions, teleports = [], [], [], [], []
    max_geo_error = 0.0
    connection = None
    with (folder / "sumo.log").open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            connection = traci.connect(
                port=port, host="127.0.0.1", numRetries=100, waitBetweenRetries=0.05, proc=process
            )
            version = connection.getVersion()
            while connection.simulation.getMinExpectedNumber() > 0:
                connection.simulationStep()
                now = connection.simulation.getTime()
                require(now <= mission["deadline_sim_s"], "Mission deadline exceeded")
                departed.extend(connection.simulation.getDepartedIDList())
                arrived.extend(connection.simulation.getArrivedIDList())
                collisions.extend(connection.simulation.getCollidingVehiclesIDList())
                teleports.extend(connection.simulation.getStartingTeleportIDList())
                for name in connection.vehicle.getIDList():
                    require(name == "bus-001", "Unexpected asset")
                    x, y = connection.vehicle.getPosition(name)
                    lon, lat = connection.simulation.convertGeo(x, y)
                    rx, ry = connection.simulation.convertGeo(lon, lat, fromGeo=True)
                    geo_error = math.hypot(rx - x, ry - y)
                    max_geo_error = max(max_geo_error, geo_error)
                    require(geo_error < 0.01, "Coordinate round-trip exceeds 1 cm")
                    speed = connection.vehicle.getSpeed(name)
                    require(
                        math.isfinite(speed) and 0 <= speed <= p["maximum_speed"] + 1e-6,
                        "Invalid speed",
                    )
                    trace.append(
                        {
                            "t": now,
                            "x": x,
                            "y": y,
                            "lon": lon,
                            "lat": lat,
                            "speed": speed,
                            "distance": connection.vehicle.getDistance(name),
                            "road": connection.vehicle.getRoadID(name),
                            "stopped": bool(connection.vehicle.getStopState(name) & 1),
                        }
                    )
        finally:
            try:
                if connection is not None:
                    connection.close(wait=False)
            finally:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
    require(process.returncode == 0, "SUMO exited unsuccessfully")
    require(departed == ["bus-001"] and arrived == ["bus-001"], "Journey did not complete")
    require(not collisions and not teleports, "Collision or teleport occurred")
    require(len(trace) > 100 and trace[-1]["distance"] > 500, "Insufficient movement")
    for previous, current in zip(trace, trace[1:]):
        require(
            abs(current["t"] - previous["t"] - mission["step_s"]) < 1e-9,
            "Simulation time discontinuity",
        )
        delta = current["distance"] - previous["distance"]
        require(
            -1e-9 <= delta <= p["maximum_speed"] * mission["step_s"] + 1e-6,
            "Odometer discontinuity",
        )
        require(
            math.hypot(current["x"] - previous["x"], current["y"] - previous["y"])
            <= p["maximum_speed"] * mission["step_s"] + 2.0,
            "Position jump exceeds declared 2 m lane/junction geometry allowance",
        )
    stopped_s = sum(row["stopped"] for row in trace) * mission["step_s"]
    require(
        stop["duration_s"] - 0.2 <= stopped_s <= stop["duration_s"] + 0.4,
        "Scheduled dwell not observed",
    )
    require(
        all(-73.7 < r["lon"] < -73.4 and 45.4 < r["lat"] < 45.7 for r in trace),
        "Geographic output is not in the expected Montreal region",
    )
    canonical = json.dumps(trace, sort_keys=True, separators=(",", ":")).encode()
    (folder / "trajectory.json").write_bytes(canonical)
    trip = ET.parse(folder / "tripinfo.xml").getroot().find("tripinfo")
    return {
        "records": len(trace),
        "arrival_sim_s": float(trip.attrib["arrival"]),
        "distance_m": trace[-1]["distance"],
        "scheduled_stopped_s": stopped_s,
        "trajectory_sha256": hashlib.sha256(canonical).hexdigest(),
        "collisions": len(collisions),
        "teleports": len(teleports),
        "coordinate_round_trip_max_allowed_m": 0.01,
        "sumo_version": version,
        "coordinate_round_trip_max_observed_m": max_geo_error,
        "projection": ET.parse(folder / "network.net.xml").getroot().find("location").attrib,
        "import_messages": import_messages,
        "simulation_messages": (folder / "sumo.log").read_text().splitlines(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    output = Path("/output")
    record = {"passed": False, "scope": "P0-06 independent Montreal movement fixture"}
    try:
        if args.prepare:
            net, messages = network(output / "prepare")
            mission = choose_mission(net, output / "prepare")
            (output / "candidate-mission.json").write_text(json.dumps(mission, indent=2) + "\n")
            record.update(passed=True, prepared_mission=mission, import_messages=messages)
        else:
            mission = json.loads(Path("/scenario/mission.json").read_text())
            params = json.loads(Path("/scenario/bus-parameters.json").read_text())
            runs = [run_once(output / name, mission, params) for name in ("run-a", "run-b")]
            require(
                runs[0]["trajectory_sha256"] == runs[1]["trajectory_sha256"],
                "Repeated numeric trajectories differ",
            )
            record.update(passed=True, runs=runs)
    except Exception as exc:
        record["error"] = str(exc)
    (output / "probe.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"passed": record["passed"], "error": record.get("error")}))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
