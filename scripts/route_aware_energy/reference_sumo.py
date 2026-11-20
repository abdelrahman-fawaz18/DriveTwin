"""Independent SUMO study plant. This process never imports the forecast package."""

import hashlib
import importlib.metadata
import json
import math
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

STOPPED_SPEED_M_S = 0.001
# SUMO 1.28 may mark arrival on a step with residual speed up to its halting speed.
SUMO_HALTING_SPEED_M_S = 0.1


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def stop_indices(sequence, stops):
    """SUMO insertion indices bind repeated-lane stops to their intended lap.

    An insertion index above the existing stop count skips occurrences of that
    edge. Equal-position stops otherwise both belong to the first matching lap.
    """
    cursor, offset = 0, 0.0
    result = []
    for count, stop in enumerate(stops):
        target = stop["external_index"]
        if target < cursor or target >= len(sequence):
            raise ValueError("Stop occurrence order is invalid")
        edge = sequence[target]
        if stop["lane_id"] != edge + "_0":
            raise ValueError("Stop lane differs from its assigned occurrence")
        candidates = [
            index
            for index in range(cursor, target + 1)
            if sequence[index] == edge and (index != cursor or stop["offset_m"] >= offset)
        ]
        if not candidates or candidates[-1] != target:
            raise ValueError("Stop cannot follow the preceding assigned stop")
        result.append(count + len(candidates) - 1)
        cursor, offset = target, stop["offset_m"]
    return result


def validate_progress(row, stops):
    """Check current/past observations against the declared stop occurrences."""
    current = row["current_stop_index"]
    entering_stop = row.get("stop_entry_transition", False)
    if entering_stop and current is None:
        raise ValueError("Reference stop entry requires a current stop")
    # SUMO retains the incoming route index inside a junction, but reports a
    # lane offset measured along the internal connector, past the stop lane.
    on_junction = row["lane_id"].startswith(":")
    for number in row["completed_stop_indices"]:
        if number < 0 or number >= len(stops):
            raise ValueError("Unknown completed reference stop")
        stop = stops[number]
        if stop["external_index"] > row["external_index"] or (
            stop["external_index"] == row["external_index"]
            and not on_junction
            and (
                stop["lane_id"] != row["lane_id"] or stop["offset_m"] > row["lane_offset_m"] + 0.21
            )
        ):
            raise ValueError(
                f"Reference completed a stop before its assigned occurrence: stop {number} "
                f"on route index {stop['external_index']}, observed route index "
                f"{row['external_index']} on lane {row['lane_id']} at {row['working_s']} s"
            )
    if current is not None:
        if current < 0 or current >= len(stops) or current in row["completed_stop_indices"]:
            raise ValueError("Invalid current reference stop")
        stop = stops[current]
        speed_limit = SUMO_HALTING_SPEED_M_S if entering_stop else STOPPED_SPEED_M_S
        if (
            stop["external_index"] != row["external_index"]
            or stop["lane_id"] != row["lane_id"]
            or abs(stop["offset_m"] - row["lane_offset_m"]) > 0.21
            or row["speed_m_s"] > speed_limit
        ):
            raise ValueError(
                f"Reference current stop differs from its assigned occurrence: stop {current} "
                f"expected route index {stop['external_index']}, lane {stop['lane_id']}, "
                f"offset {stop['offset_m']:.6f} m; observed route index {row['external_index']}, "
                f"lane {row['lane_id']}, offset {row['lane_offset_m']:.6f} m, "
                f"speed {row['speed_m_s']:.6f} m/s at {row['working_s']} working s"
            )


def collect(folder):
    import sumo
    import traci

    recipe = json.loads((folder / "recipe.json").read_bytes())
    nodes, edges = ET.Element("nodes"), ET.Element("edges")
    for index, (x, y, z) in enumerate(recipe["points"]):
        ET.SubElement(nodes, "node", id=f"n{index}", x=str(x), y=str(y), z=str(z), type="priority")
    visits = recipe["visits"]
    identities = {}
    sequence = []
    for a, b in zip(visits, visits[1:]):
        key = (a, b)
        if key not in identities:
            identity = f"e{len(identities)}"
            identities[key] = identity
            ET.SubElement(
                edges,
                "edge",
                id=identity,
                **{"from": f"n{a}", "to": f"n{b}"},
                numLanes="1",
                speed=str(recipe["speed_m_s"]),
                allow="bus",
                spreadType="center",
            )
        sequence.append(identities[key])
    for a, b in recipe.get("links", []):
        if a == b or (a, b) in identities or min(a, b) < 0 or max(a, b) >= len(recipe["points"]):
            raise ValueError("Invalid additional network link")
        identity = f"e{len(identities)}"
        identities[(a, b)] = identity
        ET.SubElement(
            edges,
            "edge",
            id=identity,
            **{"from": f"n{a}", "to": f"n{b}"},
            numLanes="1",
            speed=str(recipe["speed_m_s"]),
            allow="bus",
            spreadType="center",
        )
    ET.ElementTree(nodes).write(folder / "nodes.xml")
    ET.ElementTree(edges).write(folder / "edges.xml")
    binary = Path(sumo.SUMO_HOME) / "bin"
    converted = subprocess.run(
        [
            str(binary / "netconvert"),
            "--node-files",
            str(folder / "nodes.xml"),
            "--edge-files",
            str(folder / "edges.xml"),
            "--output-file",
            str(folder / "network.net.xml"),
            "--no-turnarounds",
            "true",
            "--junctions.corner-detail",
            "5",
        ],
        capture_output=True,
        timeout=30,
    )
    (folder / "netconvert.log").write_bytes(converted.stdout + converted.stderr)
    if converted.returncode:
        raise RuntimeError("Independent road generation failed")
    tree = ET.parse(folder / "network.net.xml")
    for lane in tree.getroot().iter("lane"):
        lane.set(
            "shape",
            " ".join(p if p.count(",") == 2 else p + ",0" for p in lane.attrib["shape"].split()),
        )
    tree.write(folder / "network.net.xml", encoding="utf-8", xml_declaration=True)
    lanes = {lane.attrib["id"]: lane.attrib for lane in tree.getroot().iter("lane")}
    stops = []
    for index in recipe["stop_indices"]:
        lane = sequence[index] + "_0"
        stops.append(
            {
                "stop_id": f"scheduled-{index}",
                "external_index": index,
                "lane_id": lane,
                "offset_m": float(lanes[lane]["length"]) * 0.7,
                "dwell_working_s": recipe["dwell_s"],
            }
        )
    routes = ET.Element("routes")
    vehicle_type = ET.SubElement(
        routes,
        "vType",
        id="independent-bus",
        vClass="bus",
        length="12",
        width="2.55",
        mass=str(recipe["plant_mass_kg"]),
        accel="1",
        decel="1.5",
        emergencyDecel="4.5",
        sigma="0",
        tau="1",
        speedFactor="1",
        speedDev="0",
        emissionClass="Energy/unknown",
    )
    parameters = {
        "has.battery.device": "true",
        "device.battery.capacity": recipe["plant_capacity_kwh"] * 1000,
        "frontSurfaceArea": 8.16,
        "airDragCoefficient": 0.7,
        "rotatingMass": 200,
        "radialDragCoefficient": 0,
        "rollDragCoefficient": 0.01,
        "constantPowerIntake": recipe["auxiliary_w"],
        "propulsionEfficiency": 0.9,
        "recuperationEfficiency": 0.55,
    }
    for key, value in parameters.items():
        ET.SubElement(vehicle_type, "param", key=key, value=str(value))
    ET.SubElement(routes, "route", id="trip", edges=" ".join(sequence))
    vehicle = ET.SubElement(
        routes,
        "vehicle",
        id="bus",
        type="independent-bus",
        route="trip",
        depart="0",
        departPos="0",
        departSpeed="0",
    )
    ET.SubElement(
        vehicle,
        "param",
        key="device.battery.chargeLevel",
        value=str(recipe["initial_soc"] * recipe["plant_capacity_kwh"] * 1000),
    )
    final_lane = sequence[-1] + "_0"
    finish = float(lanes[final_lane]["length"]) - 0.1
    all_stops = [
        *stops,
        {
            "external_index": len(sequence) - 1,
            "lane_id": final_lane,
            "offset_m": finish,
            "dwell_working_s": 10000,
        },
    ]
    for stop, insertion in zip(all_stops, stop_indices(sequence, all_stops), strict=True):
        ET.SubElement(
            vehicle,
            "stop",
            lane=stop["lane_id"],
            endPos=str(stop["offset_m"]),
            duration=str(stop["dwell_working_s"]),
            index=str(insertion),
        )
    ET.ElementTree(routes).write(folder / "routes.xml")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    connection = None
    process = None
    rows = []
    stop_started = None
    active_stop = None
    previous_energy = recipe["initial_soc"] * recipe["plant_capacity_kwh"] * 3_600_000
    interval_minimum_soc = recipe["initial_soc"]
    draw = recovery = 0.0
    with (folder / "sumo.log").open("w") as log:
        try:
            process = subprocess.Popen(
                [
                    str(binary / "sumo"),
                    "--net-file",
                    str(folder / "network.net.xml"),
                    "--route-files",
                    str(folder / "routes.xml"),
                    "--remote-port",
                    str(port),
                    "--step-length",
                    "0.2",
                    "--no-step-log",
                    "true",
                    "--no-warnings",
                    "true",
                    "--seed",
                    str(recipe["seed"]),
                ],
                stdout=log,
                stderr=log,
            )
            connection = traci.connect(
                port=port, host="127.0.0.1", numRetries=100, waitBetweenRetries=0.05, proc=process
            )
            version = connection.getVersion()
            if "1.28.0" not in version[1] or version[0] != 22:
                raise RuntimeError("SUMO reference version differs from the study lock")
            for tick in range(1, 60_001):
                connection.simulationStep()
                if "bus" not in connection.vehicle.getIDList():
                    if tick > 5:
                        raise RuntimeError("Reference bus disappeared before controlled endpoint")
                    continue
                working = round(connection.simulation.getTime(), 6)
                energy = (
                    float(connection.vehicle.getParameter("bus", "device.battery.chargeLevel"))
                    * 3600
                )
                change = previous_energy - energy
                draw += max(0, change)
                recovery += max(0, -change)
                previous_energy = energy
                interval_minimum_soc = min(
                    interval_minimum_soc, energy / (recipe["plant_capacity_kwh"] * 3_600_000)
                )
                index = connection.vehicle.getRouteIndex("bus")
                lane = connection.vehicle.getLaneID("bus")
                offset = connection.vehicle.getLanePosition("bus")
                speed = connection.vehicle.getSpeed("bus")
                remaining = connection.vehicle.getStops("bus")
                completed_count = len(stops) + 1 - len(remaining)
                completed = list(range(min(completed_count, len(stops))))
                stop_number = (
                    completed_count
                    if connection.vehicle.isStopped("bus") and completed_count < len(stops)
                    else None
                )
                entering_stop = (
                    stop_number is not None
                    and stop_number != active_stop
                    and speed > STOPPED_SPEED_M_S
                )
                if stop_number != active_stop:
                    stop_started, active_stop = working, stop_number
                dwell = (
                    max(0, stops[stop_number]["dwell_working_s"] - (working - stop_started))
                    if stop_number is not None
                    else None
                )
                at_end = (
                    index == len(sequence) - 1
                    and lane == final_lane
                    and offset >= finish - 0.2
                    and speed <= STOPPED_SPEED_M_S
                )
                if tick % 5 == 0 or not rows or at_end:
                    rows.append(
                        {
                            "working_s": working,
                            "external_index": index,
                            "lane_id": lane,
                            "lane_offset_m": offset,
                            "speed_m_s": speed,
                            "energy_j": energy,
                            "soc": energy / (recipe["plant_capacity_kwh"] * 3_600_000),
                            "distance_m": connection.vehicle.getDistance("bus"),
                            "draw_j": draw,
                            "recovery_j": recovery,
                            "completed_stop_indices": completed,
                            "current_stop_index": stop_number,
                            "stop_entry_transition": entering_stop,
                            "remaining_dwell_s": dwell,
                            "interval_minimum_soc": interval_minimum_soc,
                        }
                    )
                    validate_progress(rows[-1], stops)
                    interval_minimum_soc = rows[-1]["soc"]
                if at_end:
                    break
            else:
                raise RuntimeError("Reference trip exceeded 12000 working seconds")
        finally:
            if connection is not None:
                connection.close()
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
    if len(rows) < 30 or not all(math.isfinite(row["energy_j"]) for row in rows):
        raise RuntimeError("Incomplete reference trip")
    write(
        folder / "reference.json",
        {
            "version": "independent-sumo-trip/2.0.1",
            "sumo": version,
            "packages": {
                name: importlib.metadata.version(name)
                for name in ("eclipse-sumo", "traci", "sumolib", "sumo-data")
            },
            "network_sha256": hashlib.sha256((folder / "network.net.xml").read_bytes()).hexdigest(),
            "edges": sequence,
            "stops": stops,
            "rows": rows,
            "complete": at_end,
        },
    )


if __name__ == "__main__":
    collect(Path(sys.argv[1]))
