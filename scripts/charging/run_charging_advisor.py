"""Replay an offline station scenario against the qualified Phase 4A inputs."""

import argparse
import hashlib
import math
from importlib.metadata import version
from pathlib import Path

import sumolib

from evdt.config import ROOT
from evdt.contract_profile import decode, validator
from evdt.energy.charging_advisor import (
    COOLDOWN_S,
    IMPROVEMENT_S,
    MAX_SAMPLE_GAP_S,
    PERSISTENCE_S,
    POLICY_VERSION,
    Recommendation,
    eligibility,
    rank,
)
from evdt.energy.station_arrival import RESERVE_KWH
from scripts.charging.collect_station_arrivals import DESIGN, digest
from scripts.charging.qualify_station_arrival_forecast import (
    REFERENCE,
    RELEASE,
    load,
    make_approach,
    write,
)

RESULT = ROOT / "docs/verification/station-forecast-reserved.json"
SCHEMA = "charging/advisory-scenario-1.0.0.schema.json"


def inputs():
    release = load(RELEASE)
    result = load(RESULT)
    reference = load(REFERENCE)
    report = ROOT / reference["private_report"]
    network = report.parent / "network.net.xml"
    if (
        not result["passed"]
        or not result["phase_4b_offline_ready"]
        or digest(RELEASE) != result["release_sha256"]
        or any(digest(ROOT / name) != sha for name, sha in release["source_sha256"].items())
        or digest(network) != release["network_sha256"]
        or version("eclipse-sumo") != release["domain"]["simulator_version"]
    ):
        raise ValueError("Phase 4A release, inputs, or network differ from the qualified evidence")
    design = load(DESIGN)
    scenario = ROOT / design["scenario"]
    bus = {
        key: item["value"]
        for key, item in load(scenario / "bus-parameters.json")["parameters"].items()
    }
    catalog = load(ROOT / design["catalog"])
    mission = load(scenario / "mission.json")
    net = sumolib.net.readNet(str(network), withInternal=True)
    return net, mission, catalog, bus, release


def road_time(route, start_m, end_m, speed_cap, *, from_lane=None, to_lane=None):
    """Compare external-road travel time on legal directed bus connections."""
    for index, (before, after) in enumerate(zip(route, route[1:])):
        if not any(
            connection.getFromLane().allows("bus")
            and connection.getToLane().allows("bus")
            and (index != 0 or from_lane is None or connection.getFromLane().getID() == from_lane)
            and (
                index != len(route) - 2
                or to_lane is None
                or connection.getToLane().getID() == to_lane
            )
            for connection in before.getConnections(after)
        ):
            raise ValueError("Missing bus connection between consecutive road edges")
    if len(route) == 1 and from_lane is not None and to_lane not in (None, from_lane):
        raise ValueError("Single-edge path requires an unverified lane change")
    lengths = [edge.getLength() for edge in route]
    lengths[0] -= start_m
    lengths[-1] -= route[-1].getLength() - end_m
    if min(lengths) < -1e-8:
        raise ValueError("Road path runs backwards")
    return sum(
        max(0.0, length) / min(edge.getSpeed(), speed_cap)
        for length, edge in zip(lengths, route, strict=True)
    )


def planned_routes(net, mission, stations, bus, decision):
    edge_ids = mission["route_edges"]
    index = decision["departure_route_index"]
    start_m = decision["departure_m"]
    stop = mission["stop"]
    stop_lane = net.getLane(stop["lane"])
    stop_index = edge_ids.index(stop_lane.getEdge().getID())
    if index >= len(edge_ids):
        raise ValueError("Departure is outside the mission route")
    origin = net.getEdge(edge_ids[index])
    if not any(
        lane.allows("bus") and bus["length"] <= start_m < lane.getLength()
        for lane in origin.getLanes()
    ):
        raise ValueError("Departure is outside a usable bus lane")
    served = decision["mission_stop_served"]
    position = (index, start_m)
    required_position = (stop_index, stop["end_pos_m"])
    if (served and position < required_position) or (not served and position > required_position):
        raise ValueError("Departure and required-stop progress disagree")
    rejoin_index = len(edge_ids) - 1 if served else stop_index
    base = [net.getEdge(name) for name in edge_ids[index : rejoin_index + 1]]
    rejoin = base[-1]
    rejoin_lane = None if served else stop["lane"]
    rejoin_m = rejoin.getLength() if served else stop["end_pos_m"]
    speed_cap = bus["maximum_speed"]
    base_time = road_time(base, start_m, rejoin_m, speed_cap, to_lane=rejoin_lane)
    drive = {
        "id": "advisory-departure",
        "departure_edge": origin.getID(),
        "departure_m": start_m,
        "initial_soc": decision["initial_energy_kwh"] / bus["usable_battery_energy"],
        "auxiliary_power_w": decision["auxiliary_power_w"],
        "wait_s": 0,
    }
    routes = {}
    for station in stations:
        identity = station["station_id"]
        site = station["approach"]
        try:
            inbound, approach = make_approach(net, drive, site, bus)
            inbound_time = road_time(
                inbound, start_m, site["end_m"], speed_cap, to_lane=site["lane_id"]
            )
        except ValueError:
            routes[identity] = "approach_unreachable"
            continue
        outbound, distance = net.getShortestPath(
            inbound[-1], rejoin, vClass="bus", fromPos=site["end_m"], toPos=rejoin_m
        )
        if not outbound or not math.isfinite(distance):
            routes[identity] = "mission_rejoin_unreachable"
            continue
        try:
            outbound_time = road_time(
                outbound,
                site["end_m"],
                rejoin_m,
                speed_cap,
                from_lane=site["lane_id"],
                to_lane=rejoin_lane,
            )
        except ValueError:
            routes[identity] = "mission_rejoin_unreachable"
            continue
        routes[identity] = (
            approach,
            {
                "approach_edges": [edge.getID() for edge in inbound],
                "approach_distance_m": approach.distance_m,
                "rejoin_edges": [edge.getID() for edge in outbound],
                "rejoin_route_index": rejoin_index,
                "rejoin_lane": rejoin_lane,
                "rejoin_position_m": rejoin_m,
                "original_travel_s": base_time,
                "via_station_travel_s": inbound_time + outbound_time,
                "detour_s": max(0.0, inbound_time + outbound_time - base_time),
            },
        )
    return routes


def decision_gate(decision, domain):
    energy = decision["initial_energy_kwh"]
    minimum = decision["remaining_minimum_kwh"]
    if decision["observed_sim_s"] != decision["sim_s"]:
        return "departure_observation_unavailable"
    if energy < RESERVE_KWH:
        return "reserve_violated"
    lo, hi = domain["initial_soc"]
    capacity = domain["battery_capacity_kwh"]
    aux_lo, aux_hi = domain["auxiliary_power_w"]
    if (
        decision["speed_m_s"] != 0
        or not lo * capacity <= energy <= hi * capacity
        or not aux_lo <= decision["auxiliary_power_w"] <= aux_hi
    ):
        return "departure_outside_forecast_domain"
    if minimum is None or minimum > energy:
        return "remaining_mission_forecast_unavailable"
    if minimum >= RESERVE_KWH:
        return "no_diversion_needed"
    return None


def replay(scenario, net, mission, catalog, bus, release):
    if (
        scenario["mission_id"] != mission["mission_id"]
        or catalog["mission_id"] != mission["mission_id"]
    ):
        raise ValueError("Scenario, station catalog, and mission identities differ")
    stations = catalog["stations"]
    updates = {station["station_id"]: {} for station in stations}
    for row in scenario["station_updates"]:
        identity = row["station_id"]
        observed = row["observed_sim_s"]
        if (
            identity not in updates
            or observed in updates[identity]
            or row["available_sim_s"] < observed
        ):
            raise ValueError("Unknown station, duplicate observation, or invalid availability time")
        updates[identity][observed] = row

    recommendation = Recommendation()
    records = []
    for decision in scenario["decisions"]:
        now = decision["sim_s"]
        gate = decision_gate(decision, release["domain"])
        rows = []
        if gate is None:
            try:
                routes = planned_routes(net, mission, stations, bus, decision)
            except ValueError:
                gate = "mission_position_unavailable"
        if gate is None:
            for station in stations:
                identity = station["station_id"]
                status = max(
                    (row for row in updates[identity].values() if row["available_sim_s"] <= now),
                    key=lambda row: row["observed_sim_s"],
                    default=None,
                )
                rows.append(
                    eligibility(
                        station,
                        status,
                        decision,
                        routes[identity],
                        release["model"]["candidate"],
                        release["domain"]["maximum_sim_s"],
                    )
                )
        ordered, nearest = rank(rows)
        change = recommendation.update(now, ordered)
        if gate is not None:
            change["reason"] = gate
        records.append(
            {
                "decision_sim_s": now,
                "departure": decision,
                "candidates": rows,
                "ranked_eligible_stations": [row["station_id"] for row in ordered],
                "nearest_feasible_station": nearest["station_id"] if nearest else None,
                **change,
            }
        )
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output file")
    scenario_bytes = args.scenario.read_bytes()
    scenario = decode(scenario_bytes)
    validator(SCHEMA).validate(scenario)
    net, mission, catalog, bus, release = inputs()
    records = replay(scenario, net, mission, catalog, bus, release)
    write(
        args.output,
        {
            "version": POLICY_VERSION,
            "scope": "offline_advisory",
            "run_id": scenario["run_id"],
            "vehicle_id": scenario["vehicle_id"],
            "mission_id": scenario["mission_id"],
            "catalog_id": catalog["catalog_id"],
            "scenario_sha256": hashlib.sha256(scenario_bytes).hexdigest(),
            "policy_source_sha256": digest(ROOT / "src/evdt/energy/charging_advisor.py"),
            "runner_source_sha256": digest(Path(__file__)),
            "release_sha256": digest(RELEASE),
            "network_sha256": release["network_sha256"],
            "switch_policy": {
                "minimum_gain_s": IMPROVEMENT_S,
                "persistence_s": PERSISTENCE_S,
                "cooldown_s": COOLDOWN_S,
                "maximum_sample_gap_s": MAX_SAMPLE_GAP_S,
            },
            "decisions": records,
        },
    )
    print(f"Recorded {len(records)} offline charging decisions")


if __name__ == "__main__":
    main()
