"""Independent SUMO battery observations on imported, elevated OSM roads.

This collector does not import the estimator or command the bus from a forecast.
"""

import hashlib
import json
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, allow_nan=False)


def collect(folder):
    import sumo
    import traci

    data = json.loads((folder / "run-input.json").read_bytes())
    recipe, mission, stops = data["recipe"], data["mission"], data["stops"]["stops"]
    sequence = mission["route_edges"]
    finish = data["stops"]["endpoint"]
    routes = ET.Element("routes")
    typ = ET.SubElement(
        routes,
        "vType",
        id="bus-type",
        vClass="bus",
        length="12",
        width="2.55",
        mass=str(recipe["mass_kg"]),
        accel="1",
        decel="1.5",
        emergencyDecel="4.5",
        sigma="0",
        tau="1",
        speedFactor="1",
        speedDev="0",
        maxSpeed="27.78",
        emissionClass="Energy/unknown",
    )
    params = {
        "has.battery.device": "true",
        "device.battery.capacity": recipe["capacity_kwh"] * 1000,
        "frontSurfaceArea": 8.16,
        "airDragCoefficient": 0.7,
        "rotatingMass": 200,
        "radialDragCoefficient": 0,
        "rollDragCoefficient": 0.01 * recipe["surface_multiplier"],
        "constantPowerIntake": recipe["auxiliary_w"],
        "propulsionEfficiency": 0.9,
        "recuperationEfficiency": 0.55,
    }
    for key, value in params.items():
        ET.SubElement(typ, "param", key=key, value=str(value))
    ET.SubElement(routes, "route", id="journey", edges=" ".join(sequence))
    bus = ET.SubElement(
        routes,
        "vehicle",
        id="bus",
        type="bus-type",
        route="journey",
        depart="0",
        departPos="12",
        departSpeed="0",
        departLane="best",
    )
    ET.SubElement(
        bus,
        "param",
        key="device.battery.chargeLevel",
        value=str(recipe["capacity_kwh"] * 1000 * recipe["initial_soc"]),
    )
    previous_index, previous_offset = 0, 0
    for count, stop in enumerate([*stops, {**finish, "dwell_working_s": 14400}]):
        target = stop["external_index"]
        edge = sequence[target]
        occurrences = [
            i
            for i in range(previous_index, target + 1)
            if sequence[i] == edge and (i != previous_index or stop["offset_m"] >= previous_offset)
        ]
        if not occurrences or occurrences[-1] != target:
            raise ValueError("Stop order is not reachable")
        ET.SubElement(
            bus,
            "stop",
            lane=stop["lane_id"],
            endPos=str(stop["offset_m"]),
            duration=str(stop["dwell_working_s"]),
            index=str(count + len(occurrences) - 1),
        )
        previous_index, previous_offset = target, stop["offset_m"]
    ET.ElementTree(routes).write(folder / "routes.xml")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    conn = None
    process = None
    rows = []
    completed = False
    version = None
    teleports = 0
    capacity = recipe["capacity_kwh"] * 3_600_000
    previous = capacity * recipe["initial_soc"]
    minimum = recipe["initial_soc"]
    draw = recovery = 0.0
    active = None
    stop_started = 0
    started = time.monotonic()
    maximum_draw_w = 0
    applied_speed_factor = None
    try:
        with (folder / "sumo.log").open("x") as log:
            process = subprocess.Popen(
                [
                    str(Path(sumo.SUMO_HOME) / "bin/sumo"),
                    "--net-file",
                    str(folder / "reference-network.net.xml"),
                    "--route-files",
                    str(folder / "routes.xml"),
                    "--remote-port",
                    str(port),
                    "--step-length",
                    "0.2",
                    "--no-step-log",
                    "true",
                    "--time-to-teleport",
                    "-1",
                    "--seed",
                    "32017",
                ],
                stdout=log,
                stderr=log,
            )
            conn = traci.connect(
                port=port, host="127.0.0.1", numRetries=100, waitBetweenRetries=0.05, proc=process
            )
            version = conn.getVersion()
            if version[0] != 22 or "1.28.0" not in version[1]:
                raise ValueError("SUMO version differs from the pinned collector")
            for tick in range(1, 72001):
                if time.monotonic() - started > 480:
                    raise TimeoutError("Reference exceeded eight real-life minutes")
                conn.simulationStep()
                teleports += conn.simulation.getStartingTeleportNumber()
                if "bus" not in conn.vehicle.getIDList():
                    if tick > 5:
                        raise RuntimeError("Bus disappeared before its controlled endpoint")
                    continue
                working = conn.simulation.getTime()
                energy = (
                    float(conn.vehicle.getParameter("bus", "device.battery.chargeLevel")) * 3600
                )
                change = previous - energy
                previous = energy
                draw += max(0, change)
                recovery += max(0, -change)
                minimum = min(minimum, energy / capacity)
                maximum_draw_w = max(maximum_draw_w, change / 0.2)
                ix = conn.vehicle.getRouteIndex("bus")
                speed_factor = recipe["speed_factor"]
                for zone in recipe.get("speed_zones", []):
                    if (
                        int(zone["start_fraction"] * len(sequence))
                        <= ix
                        < int(zone["end_fraction"] * len(sequence))
                    ):
                        speed_factor = min(speed_factor, zone["speed_factor"])
                if speed_factor != applied_speed_factor:
                    conn.vehicle.setSpeedFactor("bus", speed_factor)
                    applied_speed_factor = speed_factor
                lane = conn.vehicle.getLaneID("bus")
                offset = conn.vehicle.getLanePosition("bus")
                speed = conn.vehicle.getSpeed("bus")
                remaining = conn.vehicle.getStops("bus")
                count = len(stops) + 1 - len(remaining)
                current = count if conn.vehicle.isStopped("bus") and count < len(stops) else None
                entering = current is not None and current != active and speed > 0.001
                if current != active:
                    active = current
                    stop_started = working
                completed = (
                    ix == finish["external_index"]
                    and lane == finish["lane_id"]
                    and offset >= finish["offset_m"] - 0.2
                    and speed <= 0.001
                )
                if tick % 5 == 0 or not rows or completed:
                    x, y, z = conn.vehicle.getPosition3D("bus")
                    lon, lat = conn.simulation.convertGeo(x, y)
                    rows.append(
                        {
                            "working_s": round(working, 6),
                            "external_index": ix,
                            "lane_id": lane,
                            "lane_offset_m": offset,
                            "speed_m_s": speed,
                            "energy_j": energy,
                            "soc": energy / capacity,
                            "distance_m": conn.vehicle.getDistance("bus"),
                            "draw_j": draw,
                            "recovery_j": recovery,
                            "interval_minimum_soc": minimum,
                            "completed_stop_indices": list(range(min(count, len(stops)))),
                            "current_stop_index": current,
                            "stop_entry_transition": entering,
                            "remaining_dwell_s": max(
                                0, stops[current]["dwell_working_s"] - (working - stop_started)
                            )
                            if current is not None
                            else None,
                            "longitude": lon,
                            "latitude": lat,
                            "height_m": z,
                            "slope_deg": conn.vehicle.getSlope("bus"),
                            "declared_speed_factor": speed_factor,
                        }
                    )
                    minimum = energy / capacity
                if completed:
                    break
            if not completed:
                raise RuntimeError("Journey did not finish within four working hours")
            if teleports:
                raise RuntimeError("A teleport invalidated the trajectory")
    finally:
        if conn:
            conn.close()
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        write(
            folder / "reference.json",
            {
                "version": "osm-sumo-reference/1.0.0",
                "complete": completed and not teleports,
                "rows": rows,
                "sumo": version,
                "teleports": teleports,
                "maximum_observed_draw_w": maximum_draw_w,
                "stops": stops,
                "edges": sequence,
                "network_sha256": data["height_receipt"]["plant_network_sha256"],
                "runtime_network_sha256": hashlib.sha256(
                    (folder / "reference-network.net.xml").read_bytes()
                ).hexdigest(),
                "input_sha256": hashlib.sha256(
                    (folder / "run-input.json").read_bytes()
                ).hexdigest(),
            },
        )


if __name__ == "__main__":
    collect(Path(sys.argv[1]))
