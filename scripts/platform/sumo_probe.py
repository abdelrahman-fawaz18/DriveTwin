"""Real SUMO/TraCI integration probe, executed in a network-isolated container."""

import argparse
import hashlib
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

import sumo
import traci

SCENARIO = Path("/app/scenario")
PACKAGES = ("eclipse-sumo", "traci", "sumolib", "sumo-data")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def binary(name):
    path = Path(sumo.SUMO_HOME) / "bin" / name
    require(path.is_file(), f"Missing wheel executable: {path}")
    return str(path)


def run_once(output, expected):
    output.mkdir()
    for name in ("nodes.nod.xml", "edges.edg.xml", "routes.rou.xml", "scenario.sumocfg"):
        shutil.copyfile(SCENARIO / name, output / name)
    net_command = [
        binary("netconvert"),
        "--node-files",
        "nodes.nod.xml",
        "--edge-files",
        "edges.edg.xml",
        "--output-file",
        "network.net.xml",
    ]
    environment = {**os.environ, "SUMO_HOME": str(sumo.SUMO_HOME)}
    converted = subprocess.run(
        net_command, cwd=output, env=environment, capture_output=True, text=True, timeout=30
    )
    (output / "netconvert.log").write_text(converted.stdout + converted.stderr)
    require(converted.returncode == 0, "netconvert failed; inspect netconvert.log")
    require("warning" not in (converted.stdout + converted.stderr).lower(), "netconvert warning")

    # No other container/process shares this network namespace. Keep TraCI local.
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    command = [
        binary("sumo"),
        "-c",
        "scenario.sumocfg",
        "--remote-port",
        str(port),
        "--tripinfo-output",
        "tripinfo.xml",
        "--tripinfo-output.write-unfinished",
        "true",
        "--no-step-log",
        "true",
        "--duration-log.disable",
        "true",
        "--xml-validation",
        "local",
    ]
    trace = []
    departures = []
    arrivals = []
    teleports = []
    collisions = []
    connection = None
    started = time.monotonic()
    with (output / "sumo.log").open("w") as log:
        process = subprocess.Popen(
            command,
            cwd=output,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        try:
            connection = traci.connect(
                port=port, host="127.0.0.1", numRetries=100, waitBetweenRetries=0.05, proc=process
            )
            protocol, server = connection.getVersion()
            require("1.28.0" in server, f"Unexpected SUMO server: {server}")
            require(
                abs(connection.simulation.getDeltaT() - expected["step_s"]) < 1e-9,
                "Configured step length differs from expectations",
            )
            while connection.simulation.getMinExpectedNumber() > 0:
                connection.simulationStep()
                now = connection.simulation.getTime()
                require(now <= expected["deadline_sim_s"], "Bus failed to finish before deadline")
                departures.extend(connection.simulation.getDepartedIDList())
                arrivals.extend(connection.simulation.getArrivedIDList())
                teleports.extend(connection.simulation.getStartingTeleportIDList())
                collisions.extend(connection.simulation.getCollidingVehiclesIDList())
                for vehicle in connection.vehicle.getIDList():
                    require(vehicle == expected["vehicle_id"], "Unexpected vehicle")
                    position = connection.vehicle.getPosition(vehicle)
                    trace.append(
                        {
                            "sim_time_s": now,
                            "vehicle_id": vehicle,
                            "x_m": position[0],
                            "y_m": position[1],
                            "speed_m_s": connection.vehicle.getSpeed(vehicle),
                            "distance_m": connection.vehicle.getDistance(vehicle),
                            "road_id": connection.vehicle.getRoadID(vehicle),
                            "stopped": bool(connection.vehicle.getStopState(vehicle) & 1),
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
    require(process.returncode == 0, f"SUMO exited with {process.returncode}")
    require("warning" not in (output / "sumo.log").read_text().lower(), "SUMO warning")
    require(departures == [expected["vehicle_id"]], f"Departure mismatch: {departures}")
    require(arrivals == [expected["vehicle_id"]], f"Arrival mismatch: {arrivals}")
    require(not teleports and not collisions, "Teleport or collision occurred")
    require(len(trace) > 100, "Too few measurements")
    tolerance = expected["numeric_tolerance"]
    for index, row in enumerate(trace):
        require(
            all(
                math.isfinite(row[key])
                for key in ("sim_time_s", "x_m", "y_m", "speed_m_s", "distance_m")
            ),
            "Nonfinite reading",
        )
        require(
            -tolerance <= row["speed_m_s"] <= expected["speed_limit_m_s"] + tolerance,
            "Speed outside limits",
        )
        if index:
            previous = trace[index - 1]
            require(
                abs(row["sim_time_s"] - previous["sim_time_s"] - expected["step_s"]) < tolerance,
                "Unexpected time step",
            )
            require(row["distance_m"] >= previous["distance_m"] - tolerance, "Distance regressed")
            displacement = math.hypot(row["x_m"] - previous["x_m"], row["y_m"] - previous["y_m"])
            require(
                displacement <= expected["speed_limit_m_s"] * expected["step_s"] + tolerance,
                "Position jumped beyond the physical step bound",
            )
    require(
        set(expected["route_edges"]).issubset({row["road_id"] for row in trace}),
        "Bus did not traverse both roads",
    )
    require(
        expected["minimum_distance_m"] <= trace[-1]["distance_m"] <= expected["maximum_distance_m"],
        "Travel distance outside expected interval",
    )
    stopped_s = sum(row["stopped"] for row in trace) * expected["step_s"]
    require(
        expected["minimum_stopped_s"] <= stopped_s <= expected["maximum_stopped_s"],
        f"Scheduled stop duration differs: {stopped_s}",
    )
    trips = ET.parse(output / "tripinfo.xml").getroot().findall("tripinfo")
    require(
        len(trips) == 1 and trips[0].get("id") == expected["vehicle_id"], "Trip record mismatch"
    )
    require(float(trips[0].get("arrival", "-1")) > 0, "Trip unfinished")
    trace_bytes = (
        json.dumps(trace, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()
    (output / "trajectory.json").write_bytes(trace_bytes)
    return {
        "passed": True,
        "sample_count": len(trace),
        "final_sim_time_s": now,
        "wall_seconds": round(time.monotonic() - started, 4),
        "stopped_s": stopped_s,
        "last_observed_distance_m": trace[-1]["distance_m"],
        "max_speed_m_s": max(row["speed_m_s"] for row in trace),
        "trajectory_sha256": hashlib.sha256(trace_bytes).hexdigest(),
        "departures": departures,
        "arrivals": arrivals,
        "teleports": teleports,
        "collisions": collisions,
        "sumo_exit_code": process.returncode,
        "sumo_child_reaped": process.poll() is not None,
        "traci_protocol": protocol,
        "traci_server": server,
        "trip": dict(trips[0].attrib),
        "commands": [net_command, command],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/output"))
    args = parser.parse_args()
    output = args.output.resolve()
    require(output == Path("/output"), "Use the designated output mount")
    result = {
        "client_date": "2026-11-03",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "passed": False,
        "scope": "P0 headless movement/TraCI, no EV model or telemetry integration",
        "python": sys.version,
        "package_versions": {name: metadata.version(name) for name in PACKAGES},
        "license_expressions": {
            name: metadata.metadata(name).get("License-Expression") for name in PACKAGES
        },
        "runs": [],
    }
    try:
        shutil.copyfile(Path("/app/debian-packages.tsv"), output / "debian-packages.tsv")
        require(
            set(result["package_versions"].values()) == {"1.28.0"}, "SUMO package version mismatch"
        )
        result["binary_versions"] = {
            name: subprocess.check_output([binary(name), "--version"], text=True)
            for name in ("sumo", "netconvert")
        }
        expected = json.loads((SCENARIO / "expectations.json").read_text())
        result["expectations"] = expected
        for number in (1, 2):
            result["runs"].append(run_once(output / f"run-{number}", expected))
        require(
            result["runs"][0]["trajectory_sha256"] == result["runs"][1]["trajectory_sha256"],
            "Repeated scenario produced different numeric trajectories",
        )
        result["repeatability"] = (
            "exact canonical numeric trajectory match on this platform/version"
        )
        result["passed"] = True
    except BaseException as exc:
        result["error"] = repr(exc)
    finally:
        (output / "probe.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("passed", "runs")}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
