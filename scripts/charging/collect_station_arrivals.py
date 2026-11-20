"""Collect bounded, no-charge SUMO station approaches for development only."""

import argparse
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import sumo
import sumolib
import traci

from evdt.config import ROOT
from evdt.contract_profile import decode, validator
from evdt.monitoring.network import build_network
from evdt.monitoring.plant import EnergyLedger, battery_step_power
from scripts.charging.check_station_profiles import OSM, SCHEMA

DESIGN = ROOT / "qualification/charging/station-arrival-design-1.0.0.json"
VEHICLE = "station-reference-bus"
J_PER_WH = 3600
J_PER_KWH = 3_600_000


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_routes(path, drive, route, stop, bus, energy, capacity_wh):
    root = ET.Element("routes")
    bus_type = ET.SubElement(
        root, "vType", id="reference-bus", vClass="bus", emissionClass="Energy/unknown"
    )
    for attribute, parameter in (
        ("length", "length"),
        ("width", "width"),
        ("height", "height"),
        ("mass", "curb_mass"),
        ("maxSpeed", "maximum_speed"),
        ("accel", "acceleration"),
        ("decel", "comfortable_deceleration"),
        ("emergencyDecel", "emergency_deceleration"),
        ("tau", "driver_tau"),
        ("sigma", "driver_sigma"),
    ):
        bus_type.set(attribute, str(bus[parameter]))
    for key, value in {
        "has.battery.device": "true",
        "has.stationfinder.device": "false",
        "device.battery.capacity": capacity_wh,
        "loading": bus["passenger_load"],
        "frontSurfaceArea": energy["front_area_m2"],
        "airDragCoefficient": energy["air_drag_coefficient"],
        "rotatingMass": energy["rotating_mass_kg"],
        "radialDragCoefficient": energy["radial_drag_coefficient"],
        "rollDragCoefficient": energy["rolling_drag_coefficient"],
        "constantPowerIntake": drive["auxiliary_power_w"],
        "propulsionEfficiency": energy["propulsion_efficiency"],
        "recuperationEfficiency": energy["recuperation_efficiency"],
    }.items():
        ET.SubElement(bus_type, "param", key=key, value=str(value))
    ET.SubElement(root, "route", id="approach", edges=" ".join(edge.getID() for edge in route))
    bus_vehicle = ET.SubElement(
        root,
        "vehicle",
        id=VEHICLE,
        type="reference-bus",
        route="approach",
        depart="0",
        departLane="best",
        departSpeed="0",
        departPos=str(drive["departure_m"]),
    )
    ET.SubElement(
        bus_vehicle,
        "param",
        key="device.battery.chargeLevel",
        value=str(capacity_wh * drive["initial_soc"]),
    )
    ET.SubElement(
        bus_vehicle,
        "stop",
        lane=stop["lane_id"],
        startPos=str(stop["start_m"]),
        endPos=str(stop["end_m"]),
        duration=str(drive["wait_s"] + 2),
    )
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def trace_drive(connection, drive, stop, bus, step_s, domain, trace):
    if domain["simulator_version"] not in connection.getVersion()[1] or not math.isclose(
        connection.simulation.getDeltaT(), step_s
    ):
        raise ValueError("Simulator version or step differs from the protocol")
    if connection.chargingstation.getIDList():
        raise ValueError("Reference simulation must have zero charging stations")
    capacity_j = bus["usable_battery_energy"] * J_PER_KWH
    ledger = EnergyLedger(capacity_j * drive["initial_soc"], capacity_j)
    arrival = None
    minimum = ledger.previous_j
    wait_steps = round(drive["wait_s"] / step_s)
    for step in range(round(domain["maximum_sim_s"] / step_s)):
        connection.simulationStep()
        if (
            connection.simulation.getCollidingVehiclesIDList()
            or connection.simulation.getStartingTeleportIDList()
        ):
            raise RuntimeError("Unexpected collision or relocation")
        vehicle = connection.vehicle
        if tuple(vehicle.getIDList()) != (VEHICLE,):
            raise RuntimeError("Reference bus missing or unexpected vehicle present")
        energy_j = float(vehicle.getParameter(VEHICLE, "device.battery.chargeLevel")) * J_PER_WH
        device_wh = float(vehicle.getParameter(VEHICLE, "device.battery.energyConsumed"))
        charged_wh = float(vehicle.getParameter(VEHICLE, "device.battery.energyCharged"))
        if not 0 < energy_j <= capacity_j or charged_wh != 0:
            raise RuntimeError("Battery depleted, invalid, or received charging energy")
        row = {
            "elapsed_sim_s": round(step * step_s, 6),
            "lane": vehicle.getLaneID(VEHICLE),
            "lane_position_m": vehicle.getLanePosition(VEHICLE),
            "distance_m": vehicle.getDistance(VEHICLE),
            "speed_m_s": vehicle.getSpeed(VEHICLE),
            "stopped": vehicle.isStopped(VEHICLE),
            "battery_energy_j": energy_j,
            "step_energy_wh": device_wh,
            "charge_received_wh": charged_wh,
        }
        trace.write(json.dumps(row, allow_nan=False) + "\n")
        if step == 0:
            actual_capacity_j = float(vehicle.getParameter(VEHICLE, "device.battery.capacity"))
            if (
                abs(actual_capacity_j * J_PER_WH - capacity_j) > ledger.tolerance_j
                or abs(energy_j - ledger.previous_j) > ledger.tolerance_j
                or device_wh != 0
                or row["speed_m_s"] != 0
            ):
                raise RuntimeError("Unexpected insertion energy, capacity, or motion")
        else:
            ledger.observe(energy_j, battery_step_power(device_wh, step_s), step_s)
        minimum = min(minimum, energy_j)
        # SUMO can mark a stop while finishing the last deceleration step.
        if not row["stopped"] or row["speed_m_s"] != 0:
            if arrival is not None:
                raise RuntimeError("Bus left the stop before the measured wait ended")
            continue
        if (
            row["lane"] != stop["lane_id"]
            or not stop["start_m"] + bus["length"] - 0.1
            <= row["lane_position_m"]
            <= stop["end_m"] + 0.1
            or row["distance_m"] <= 0
        ):
            raise RuntimeError("Stationary bus was outside the configured stop interval")
        if arrival is None:
            arrival = (step, row, minimum)
        arrived_at, arrival_row, minimum_approach_j = arrival
        if step - arrived_at == wait_steps:
            wait_energy_j = arrival_row["battery_energy_j"] - energy_j
            if not math.isclose(
                wait_energy_j, drive["auxiliary_power_w"] * drive["wait_s"], abs_tol=3.6
            ):
                raise RuntimeError("Stationary battery draw differs from configured auxiliary load")
            return {
                "arrival_sim_s": arrival_row["elapsed_sim_s"],
                "arrival_distance_m": arrival_row["distance_m"],
                "arrival_lane": arrival_row["lane"],
                "arrival_position_m": arrival_row["lane_position_m"],
                "arrival_energy_kwh": arrival_row["battery_energy_j"] / J_PER_KWH,
                "arrival_soc": arrival_row["battery_energy_j"] / capacity_j,
                "minimum_approach_energy_kwh": minimum_approach_j / J_PER_KWH,
                "minimum_through_wait_energy_kwh": minimum / J_PER_KWH,
                "energy_after_wait_kwh": energy_j / J_PER_KWH,
                "wait_energy_kwh": wait_energy_j / J_PER_KWH,
                "physical_steps_reconciled": ledger.steps,
                "max_energy_balance_residual_j": ledger.maximum_residual_j,
                "charge_received_wh": 0,
            }
    raise RuntimeError("Drive did not reach and finish the stop within its simulation budget")


def approach_route(net, drive, stop, bus_length):
    lane = net.getLane(stop["lane_id"])
    origin = net.getEdge(drive["departure_edge"])
    if not any(
        candidate.allows("bus") and bus_length <= drive["departure_m"] < candidate.getLength()
        for candidate in origin.getLanes()
    ):
        raise ValueError(f"{drive['id']}: departure position is outside a usable bus lane")
    route, distance = net.getShortestPath(
        origin, lane.getEdge(), vClass="bus", fromPos=drive["departure_m"], toPos=stop["end_m"]
    )
    if not route or not math.isfinite(distance) or distance <= 0:
        raise ValueError(f"{drive['id']}: no positive directed approach route")
    return route


def collect(drive, stop, route, domain, network, bus, energy, step_s, folder):
    folder.mkdir()
    routes = folder / "approach.rou.xml"
    write_routes(routes, drive, route, stop, bus, energy, bus["usable_battery_energy"] * 1000)
    cmd = [
        str(Path(sumo.SUMO_HOME) / "bin/sumo"),
        "--net-file",
        str(network),
        "--route-files",
        str(routes),
        "--step-length",
        str(step_s),
        "--seed",
        str(drive["seed"]),
        "--time-to-teleport",
        "-1",
        "--collision.action",
        "warn",
        "--no-step-log",
        "true",
        "--duration-log.disable",
        "true",
        "--precision",
        "12",
    ]
    trace = folder / "plant-trace.jsonl"
    with (folder / "sumo.log").open("x") as log, trace.open("x", encoding="utf-8") as stream:
        traci.start(cmd, label=drive["id"], doSwitch=False, numRetries=5, stdout=log)
        connection = traci.getConnection(drive["id"])
        try:
            measured = trace_drive(connection, drive, stop, bus, step_s, domain, stream)
        finally:
            connection.close()
    return {**measured, "trace_sha256": digest(trace), "route_sha256": digest(routes)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    design = decode(DESIGN.read_bytes())
    scenario = ROOT / design["scenario"]
    mission = json.loads((scenario / "mission.json").read_bytes())
    catalog_path = ROOT / design["catalog"]
    catalog = decode(catalog_path.read_bytes())
    validator(SCHEMA).validate(catalog)
    stations = {station["station_id"]: station for station in catalog["stations"]}
    bus = {
        key: spec["value"]
        for key, spec in json.loads((scenario / "bus-parameters.json").read_bytes())[
            "parameters"
        ].items()
    }
    energy = json.loads((scenario / "plant.json").read_bytes())["energy"]
    domain = design["domain"]
    drives = design["drives"]
    development = [drive for drive in drives if drive["partition"] == "development"]
    reserved = [drive for drive in drives if drive["partition"] == "reserved"]
    if (
        design["version"] != "station-arrival-design/1.0.0"
        or not development
        or not reserved
        or len(development) + len(reserved) != len(drives)
        or len({drive["id"] for drive in drives}) != len(drives)
        or len({drive["seed"] for drive in drives}) != len(drives)
        or any(not re.fullmatch(r"[a-z0-9-]+", drive["id"]) for drive in drives)
        or catalog["mission_id"] != mission["mission_id"]
        or len(stations) != len(catalog["stations"])
        or {drive["station"] for drive in development} != stations.keys()
        or {drive["station"] for drive in reserved} != stations.keys()
        or any(drive["departure_edge"] not in mission["route_edges"] for drive in drives)
        or {(drive["station"], drive["departure_edge"]) for drive in development}
        & {(drive["station"], drive["departure_edge"]) for drive in reserved}
        or bus["usable_battery_energy"] != domain["battery_capacity_kwh"]
        or bus["curb_mass"] + bus["passenger_load"] != domain["loaded_mass_kg"]
        or any(
            not domain["initial_soc"][0] <= drive["initial_soc"] <= domain["initial_soc"][1]
            or not domain["auxiliary_power_w"][0]
            <= drive["auxiliary_power_w"]
            <= domain["auxiliary_power_w"][1]
            or not domain["stationary_wait_sim_s"][0]
            <= drive["wait_s"]
            <= domain["stationary_wait_sim_s"][1]
            or drive["wait_s"] > 0
            and stations[drive["station"]]["queue_capacity"] == 0
            or not math.isclose(
                round(drive["wait_s"] / mission["step_s"]) * mission["step_s"], drive["wait_s"]
            )
            for drive in drives
        )
    ):
        raise ValueError("Reference drive design, partitions, or modeled bus does not match")

    args.output.mkdir()
    report = {
        "passed": False,
        "partition": "development",
        "reserved_collected": False,
        "simulator_version": domain["simulator_version"],
        "reserve_kwh": design["reserve_soc"] * bus["usable_battery_energy"],
        "development_drive_ids": [drive["id"] for drive in development],
        "reserved_drive_ids": [drive["id"] for drive in reserved],
        "source_sha256": {
            path.relative_to(ROOT).as_posix(): digest(path)
            for path in (
                Path(__file__).resolve(),
                DESIGN,
                catalog_path,
                OSM,
                ROOT / "src/evdt/monitoring/network.py",
                ROOT / "src/evdt/monitoring/plant.py",
                *(
                    scenario / name
                    for name in (
                        "mission.json",
                        "bus-parameters.json",
                        "plant.json",
                        "import-review.json",
                    )
                ),
            )
        },
        "drives": [],
    }
    try:
        network = build_network(scenario, OSM, args.output)
        net = sumolib.net.readNet(str(network), withInternal=True)
        report["network_sha256"] = digest(network)
        routes = {
            drive["id"]: approach_route(
                net, drive, stations[drive["station"]]["approach"], bus["length"]
            )
            for drive in drives
        }
        for drive in development:
            measured = collect(
                drive,
                stations[drive["station"]]["approach"],
                routes[drive["id"]],
                domain,
                network,
                bus,
                energy,
                mission["step_s"],
                args.output / drive["id"],
            )
            measured["reserve_maintained"] = (
                measured["minimum_through_wait_energy_kwh"] >= report["reserve_kwh"]
            )
            report["drives"].append({"case": drive, **measured})
            print(f"Collected {drive['id']}: {measured['arrival_energy_kwh']:.3f} kWh", flush=True)
        report["passed"] = True
    except Exception as exc:
        report["failure"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        (args.output / "qualification.json").write_text(
            json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
