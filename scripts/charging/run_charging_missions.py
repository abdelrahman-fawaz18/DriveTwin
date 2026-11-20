"""Measure charging and resumption in bounded, isolated SUMO mission fixtures."""

import argparse
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path

import sumo
import traci
from traci.exceptions import FatalTraCIError, TraCIException

from evdt.command_timing import ClockLease, host_clock
from evdt.config import ROOT
from evdt.contract_profile import Rejected
from evdt.energy.charging_advisor import POLICY_VERSION, TARGET_KWH
from evdt.energy.charging_dispatch import (
    CHARGING_COMMAND_VERSION,
    DiversionContext,
    DiversionProposal,
    check_diversion,
)
from evdt.energy.station_arrival import RESERVE_KWH
from evdt.monitoring.plant import EnergyLedger, battery_step_power
from scripts.charging.collect_station_arrivals import DESIGN, VEHICLE, digest, write_routes
from scripts.charging.qualify_station_arrival_forecast import REFERENCE, RELEASE, load, write
from scripts.charging.run_charging_advisor import inputs, replay

ARMS = ("no-charge-reference", "advisor", "nearest", "stationfinder")
PROTECTION_ARMS = {
    "operator-override": ("operator_override", True),
    "thermal-block": ("thermal_block", True),
    "local-block": ("local_protection_block", True),
    "stale-departure": ("observation_elapsed_ns", 0),
    "expired-command": ("expires_sim_s", 0.2),
    "missing-automatic-approval": ("automatic", True),
}
MAX_SIM_S = 4800
SEED = 44101
STATIONFINDER = {
    "rescueAction": "none",
    "emptyThreshold": "0.1",
    "needToChargeLevel": "0.20",
    "saturatedChargeLevel": "0.25",
    "radius": "1200",
    "waitForCharge": "45",
    "checkEnergyForRoute": "false",
    "replacePlannedStop": "0",
    "charging.knowledge": "0",
}


def prepare(folder, arm, route, decision, stations, bus, energy, departure_s=0):
    routes = folder / "mission.rou.xml"
    drive = {
        "departure_m": decision["departure_m"],
        "initial_soc": decision["initial_energy_kwh"] / bus["usable_battery_energy"],
        "auxiliary_power_w": 500_000 if arm == "unexpected-draw" else decision["auxiliary_power_w"],
        "wait_s": 0,
    }
    write_routes(routes, drive, route, stations[0]["approach"], bus, energy, 350_000)
    tree = ET.parse(routes)
    vehicle = tree.find("vehicle")
    vehicle.remove(vehicle.find("stop"))
    if departure_s:
        vehicle.set("depart", str(departure_s))
    kind = tree.find("vType")
    ET.SubElement(
        kind,
        "param",
        key="device.battery.maximumChargeRate",
        value=str(decision["bus_acceptance_w"]),
    )
    if arm == "stationfinder":
        kind.find("param[@key='has.stationfinder.device']").set("value", "true")
        for key, value in STATIONFINDER.items():
            ET.SubElement(kind, "param", key=f"device.stationfinder.{key}", value=value)
    tree.write(routes, encoding="utf-8", xml_declaration=True)

    additional = ET.Element("additional")
    if arm != "no-charge-reference":
        for station in stations:
            if arm == "outage-fallback" and station["station_id"] == "depot-a":
                continue
            if arm == "all-stations-blocked":
                continue
            stop = station["approach"]
            ET.SubElement(
                additional,
                "chargingStation",
                id=station["station_id"],
                lane=stop["lane_id"],
                startPos=str(stop["start_m"]),
                endPos=str(stop["end_m"]),
                power=str(station["power_per_bay_w"]),
                efficiency=str(station["charge_efficiency"]),
                chargeDelay="2",
                chargeInTransit="false",
            )
    chargers = folder / "chargers.add.xml"
    ET.ElementTree(additional).write(chargers, encoding="utf-8", xml_declaration=True)
    return routes, chargers


def fixture_admission(arm, candidate, provenance, now, events):
    clock = host_clock()
    scope = {
        **provenance,
        "station_id": candidate["station_id"],
        "policy_version": POLICY_VERSION,
        "departure_observation_id": f"{arm}:inserted:{now}",
    }
    proposal = DiversionProposal(
        **scope,
        version=CHARGING_COMMAND_VERSION,
        correlation_id=f"p4-mission-{arm}",
        valid_from_sim_s=now,
        expires_sim_s=now + 1,
        wall_lease=ClockLease.until(clock, clock.utc + timedelta(seconds=3)),
    )
    context = DiversionContext(
        **scope,
        now_sim_s=now,
        observation_sim_s=now,
        observation_elapsed_ns=clock.elapsed_ns,
        station_observed_sim_s=candidate["status"]["observed_sim_s"],
        station_available_sim_s=candidate["status"]["available_sim_s"],
        station_max_age_s=candidate["station_max_age_s"],
        automatic_approved=False,
        operator_fixture_approved=True,
        operator_override=False,
        thermal_block=False,
        local_protection_block=False,
        station_eligible=candidate["eligible"],
    )
    automatic = False
    if arm in PROTECTION_ARMS:
        field, value = PROTECTION_ARMS[arm]
        if field == "expires_sim_s":
            proposal = replace(proposal, expires_sim_s=now)
        elif field == "automatic":
            automatic = value
        else:
            context = replace(context, **{field: value})
    wire = {**asdict(proposal), "wall_lease": proposal.wall_lease.encode()}
    try:
        check_diversion(proposal, context, host_clock(), automatic=automatic)
    except Rejected as exc:
        events.append({"event": "admission_refused", "proposal": wire, "reason": str(exc)})
        return str(exc)
    events.append({"event": "fixture_admitted", "proposal": wire})
    return None


def measure(
    connection,
    arm,
    decision,
    candidate,
    provenance,
    step_s,
    trace,
    events,
    handoff=None,
    departure_s=0,
    world=None,
    choose=None,
):
    vehicle = connection.vehicle
    initial_wh = decision["initial_energy_kwh"] * 1000
    ledger = EnergyLedger(initial_wh * 3600, 350_000 * 3600)
    minimum_wh = initial_wh
    charged_wh = 0.0
    peak_battery_input_w = 0.0
    active_station = None
    selected_station = candidate["station_id"] if candidate else None
    route_changed = target_reached = resumed = False
    charge_end_s = None
    departure_distance = None
    target_energy_wh = None
    last_route = None
    last_sample = None
    completed = False
    reason = "timeout"
    correlation = f"p4-mission-{arm}"
    native_destination = None
    native_replans = 0
    for _ in range(round((MAX_SIM_S + departure_s) / step_s)):
        if world is not None:
            world(connection, connection.simulation.getTime(), events)
        connection.simulationStep()
        now = connection.simulation.getTime()
        if connection.simulation.getStartingTeleportIDList():
            reason = "teleport"
            break
        if connection.simulation.getCollidingVehiclesIDList():
            reason = "collision"
            break
        if VEHICLE in connection.simulation.getArrivedIDList():
            completed = True
            reason = "mission_arrived"
            break
        if VEHICLE not in vehicle.getIDList():
            if last_sample is None and now <= departure_s:
                continue
            reason = "missing_vehicle"
            break
        actual_wh = float(vehicle.getParameter(VEHICLE, "device.battery.chargeLevel"))
        used_wh = float(vehicle.getParameter(VEHICLE, "device.battery.energyConsumed"))
        reported_wh = float(vehicle.getParameter(VEHICLE, "device.battery.energyCharged"))
        station = vehicle.getParameter(VEHICLE, "device.battery.chargingStationId")
        speed = vehicle.getSpeed(VEHICLE)
        distance = vehicle.getDistance(VEHICLE)
        lane = vehicle.getLaneID(VEHICLE)
        route = tuple(vehicle.getRoute(VEHICLE))
        # SUMO retains the last charge reading while leaving the station lane.
        # In-transit charging is disabled, and the energy balance still has to pass.
        input_wh = reported_wh if speed < 0.1 else 0.0
        if last_sample is None:
            if (
                not math.isclose(actual_wh, initial_wh, abs_tol=1e-6)
                or used_wh
                or input_wh
                or speed
            ):
                raise ValueError("Matched mission departure differs from the fixture")
            last_route = route
            if choose is not None:
                candidate, provenance = choose(connection, now, actual_wh, events)
                selected_station = candidate["station_id"] if candidate else None
            if handoff is not None:
                candidate, correlation = handoff(connection, now, actual_wh, events)
                if candidate is None:
                    trace.write(
                        json.dumps(
                            {
                                "sim_s": now,
                                "speed_m_s": speed,
                                "battery_wh": actual_wh,
                                "route_before": route,
                                "route_after": tuple(vehicle.getRoute(VEHICLE)),
                            }
                        )
                        + "\n"
                    )
                    return {
                        "completed": False,
                        "terminal_reason": "handoff_interrupted",
                        "route_changed": tuple(vehicle.getRoute(VEHICLE)) != route,
                    }
                selected_station = candidate["station_id"]
            if arm == "all-stations-blocked" or (choose is not None and candidate is None):
                trace.write(
                    json.dumps(
                        {
                            "sim_s": now,
                            "lane": lane,
                            "speed_m_s": speed,
                            "battery_wh": actual_wh,
                            "charge_wh": input_wh,
                            "route": route,
                        }
                    )
                    + "\n"
                )
                return {
                    "completed": False,
                    "terminal_reason": "no_feasible_station",
                    "terminal_sim_s": now,
                    "route_changed": False,
                    "minimum_kwh": actual_wh / 1000,
                    "charge_delivered_kwh": 0.0,
                    "reserve_violation": actual_wh < RESERVE_KWH * 1000,
                    "depleted": actual_wh <= 0,
                    "replans": 0,
                    "operator_fixture_interventions": 0,
                }
            if candidate:
                refusal = (
                    fixture_admission(arm, candidate, provenance, now, events)
                    if handoff is None
                    else None
                )
                if refusal:
                    actual_route = tuple(vehicle.getRoute(VEHICLE))
                    trace.write(
                        json.dumps(
                            {
                                "sim_s": now,
                                "lane": lane,
                                "speed_m_s": speed,
                                "battery_wh": actual_wh,
                                "charge_wh": input_wh,
                                "route_before": route,
                                "route_after": actual_route,
                            }
                        )
                        + "\n"
                    )
                    return {
                        "completed": False,
                        "terminal_reason": refusal,
                        "terminal_sim_s": now,
                        "route_changed": actual_route != route,
                        "charge_delivered_kwh": input_wh / 1000,
                        "minimum_kwh": actual_wh / 1000,
                        "reserve_violation": actual_wh < RESERVE_KWH * 1000,
                        "depleted": actual_wh <= 0,
                        "replans": 0,
                        "operator_fixture_interventions": 0,
                    }
                via = candidate["route"]
                diversion = via["approach_edges"] + via["rejoin_edges"][1:]
                if route[0] != diversion[0] or route[-1] != diversion[-1]:
                    raise ValueError("Diversion does not preserve the mission endpoints")
                events.append(
                    {
                        "event": "operator_fixture_diversion",
                        "correlation_id": correlation,
                        "station": selected_station,
                        "sim_s": now,
                    }
                )
                vehicle.setRoute(VEHICLE, diversion)
                vehicle.setChargingStationStop(VEHICLE, selected_station, duration=MAX_SIM_S)
        else:
            ledger.observe(actual_wh * 3600, battery_step_power(used_wh - input_wh, step_s), step_s)
        if arm == "stationfinder" and world is not None:
            destination = vehicle.getParameter(VEHICLE, "device.stationfinder.chargingStation")
            if destination and destination != native_destination:
                native_replans += int(native_destination is not None)
                native_destination = destination
                events.append(
                    {"event": "native_destination_observed", "station": destination, "sim_s": now}
                )
        minimum_wh = min(minimum_wh, actual_wh)
        if last_route != route and not route_changed:
            route_changed = True
            events.append({"event": "route_observed", "correlation_id": correlation, "sim_s": now})
        if input_wh > 0:
            if speed > 0.1 or (selected_station is not None and station != selected_station):
                raise ValueError("Charging occurred outside the selected stationary stop")
            if active_station is None:
                active_station = station
                events.append(
                    {
                        "event": "charging_observed",
                        "correlation_id": correlation,
                        "station": station,
                        "sim_s": now,
                        "lane": lane,
                        "energy_kwh": actual_wh / 1000,
                    }
                )
            elif station != active_station:
                raise ValueError("A second charger supplied unexpected energy")
            charged_wh += input_wh
            peak_battery_input_w = max(peak_battery_input_w, input_wh * 3600 / step_s)
            if not target_reached and actual_wh >= TARGET_KWH * 1000:
                target_reached = True
                target_energy_wh = actual_wh
                events.append(
                    {
                        "event": "target_observed",
                        "correlation_id": correlation,
                        "sim_s": now,
                        "energy_kwh": actual_wh / 1000,
                    }
                )
                if candidate:
                    vehicle.resume(VEHICLE)
        elif active_station and charge_end_s is None:
            charge_end_s = now
            departure_distance = distance
        if (
            departure_distance is not None
            and not resumed
            and speed > 0.1
            and distance > departure_distance
        ):
            resumed = True
            events.append({"event": "motion_resumed", "correlation_id": correlation, "sim_s": now})
        last_sample = {
            "sim_s": now,
            "lane": lane,
            "speed_m_s": speed,
            "distance_m": distance,
            "battery_wh": actual_wh,
            "draw_wh": used_wh,
            "charge_wh": input_wh,
            "reported_charge_wh": reported_wh,
        }
        trace.write(json.dumps(last_sample, allow_nan=False) + "\n")
        if actual_wh <= 0 or actual_wh < RESERVE_KWH * 1000:
            reason = "depleted" if actual_wh <= 0 else "reserve_violated"
            break
    return {
        "completed": completed,
        "terminal_reason": reason,
        "terminal_sim_s": now,
        "station_charged": active_station,
        "route_changed": route_changed,
        "target_reached": target_reached,
        "target_observed_kwh": (target_energy_wh / 1000 if target_energy_wh is not None else None),
        "resumed_after_charging": resumed,
        "charge_delivered_kwh": charged_wh / 1000,
        "peak_battery_charge_input_w": peak_battery_input_w,
        "minimum_kwh": minimum_wh / 1000,
        "reserve_violation": minimum_wh < RESERVE_KWH * 1000,
        "depleted": minimum_wh <= 0,
        "max_balance_residual_j": ledger.maximum_residual_j,
        "physical_steps_reconciled": ledger.steps,
        "last_sample": last_sample,
        "replans": native_replans
        if arm == "stationfinder" and world is not None
        else (0 if candidate or arm == "no-charge-reference" else None),
        "operator_fixture_interventions": int(candidate is not None),
    }


def run(
    folder,
    arm,
    network,
    route,
    decision,
    candidate,
    provenance,
    stations,
    bus,
    energy,
    step_s,
    handoff=None,
    departure_s=0,
    world=None,
    choose=None,
):
    folder.mkdir()
    routes, chargers = prepare(folder, arm, route, decision, stations, bus, energy, departure_s)
    command = [
        str(Path(sumo.SUMO_HOME) / "bin/sumo"),
        "--net-file",
        str(network),
        "--route-files",
        str(routes),
        "--additional-files",
        str(chargers),
        "--step-length",
        str(step_s),
        "--seed",
        str(SEED),
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
    trace_path = folder / "measurements.jsonl"
    events = []
    try:
        with (
            (folder / "sumo.log").open("x") as log,
            trace_path.open("x", encoding="utf-8") as trace,
        ):
            traci.start(command, label=arm, doSwitch=False, numRetries=5, stdout=log)
            connection = traci.getConnection(arm)
            try:
                result = measure(
                    connection,
                    arm,
                    decision,
                    candidate,
                    provenance,
                    step_s,
                    trace,
                    events,
                    handoff,
                    departure_s,
                    world,
                    choose,
                )
            finally:
                connection.close()
    except (ValueError, RuntimeError, TraCIException, FatalTraCIError) as exc:
        result = {"completed": False, "terminal_reason": "fixture_error", "error": str(exc)}
    result.update(
        {
            "arm": arm,
            "stationfinder_enabled": arm == "stationfinder",
            "trace_sha256": digest(trace_path),
            "events": events,
        }
    )
    write(folder / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    net, mission, catalog, bus, release = inputs()
    scenario = ROOT / load(DESIGN)["scenario"]
    energy = load(scenario / "plant.json")["energy"]
    network = (ROOT / load(REFERENCE)["private_report"]).parent / "network.net.xml"
    config = ROOT / "qualification/charging/advisory-scenario-1.0.0.json"
    offline = load(config)
    decision = offline["decisions"][0]
    stations = catalog["stations"]
    offline["decisions"] = [decision]
    offline["station_updates"] = [
        {**row, "state": "open"}
        for row in offline["station_updates"]
        if row["available_sim_s"] == 0
    ]
    choice = replay(offline, net, mission, catalog, bus, release)[0]
    ages = {station["station_id"]: station["status_max_age_sim_s"] for station in stations}
    rows = {
        row["station_id"]: {**row, "station_max_age_s": ages[row["station_id"]]}
        for row in choice["candidates"]
        if row["eligible"]
    }
    selected = {
        "advisor": rows[choice["recommended_station"]],
        "nearest": rows[choice["nearest_feasible_station"]],
    }
    route = [
        net.getEdge(edge) for edge in mission["route_edges"][decision["departure_route_index"] :]
    ]
    args.output.mkdir()
    choice_path = args.output / "advisory-input-and-selection.json"
    write(choice_path, {"input": offline, "decision": choice})
    provenance = {
        "run_id": offline["run_id"],
        "vehicle_id": VEHICLE,
        "release_sha256": digest(RELEASE),
        "decision_sha256": digest(choice_path),
    }
    results = []
    for arm in ARMS:
        result = run(
            args.output / arm,
            arm,
            network,
            route,
            decision,
            selected.get(arm),
            {**provenance, "run_id": f"{offline['run_id']}-{arm}"},
            stations,
            bus,
            energy,
            mission["step_s"],
        )
        results.append(result)
        print(f"{arm}: {result['terminal_reason']}", flush=True)
    protected = []
    for arm in PROTECTION_ARMS:
        result = run(
            args.output / arm,
            arm,
            network,
            route,
            decision,
            selected["advisor"],
            {**provenance, "run_id": f"{offline['run_id']}-{arm}"},
            stations,
            bus,
            energy,
            mission["step_s"],
        )
        protected.append(result)
        print(f"{arm}: {result['terminal_reason']}", flush=True)
    adverse = []
    for arm in ("outage-fallback", "all-stations-blocked"):
        changed = {
            **offline,
            "station_updates": [
                {
                    **row,
                    "state": "outage"
                    if arm == "all-stations-blocked" or row["station_id"] == "depot-a"
                    else "open",
                }
                for row in offline["station_updates"]
            ],
        }
        adverse_choice = replay(changed, net, mission, catalog, bus, release)[0]
        chosen = adverse_choice["recommended_station"]
        if (arm == "all-stations-blocked" and chosen is not None) or (
            arm == "outage-fallback" and (chosen is None or chosen == "depot-a")
        ):
            raise ValueError("Advisor did not honor the adverse station configuration")
        candidate = next(
            (
                {**row, "station_max_age_s": ages[chosen]}
                for row in adverse_choice["candidates"]
                if row["station_id"] == chosen
            ),
            None,
        )
        decision_file = args.output / (arm + "-advisory.json")
        write(decision_file, {"input": changed, "decision": adverse_choice})
        result = run(
            args.output / arm,
            arm,
            network,
            route,
            decision,
            candidate,
            {
                **provenance,
                "run_id": f"{offline['run_id']}-{arm}",
                "decision_sha256": digest(decision_file),
            },
            stations,
            bus,
            energy,
            mission["step_s"],
        )
        adverse.append(result)
        print(f"{arm}: {result['terminal_reason']}", flush=True)
    reference_time = results[0].get("terminal_sim_s") if results[0]["completed"] else None
    for row in results:
        row["delay_vs_reference_s"] = (
            row["terminal_sim_s"] - reference_time
            if reference_time is not None and row["completed"]
            else None
        )
    manual_pass = all(
        row["completed"]
        and row["target_reached"]
        and row["resumed_after_charging"]
        and row["route_changed"]
        and not row["reserve_violation"]
        for row in results[1:3]
    )
    protection_pass = [row["terminal_reason"] for row in protected] == [
        "operator_override",
        "local_protection",
        "local_protection",
        "charging_stale_departure",
        "charging_expired_or_early",
        "automatic_dispatch_not_approved",
    ] and all(not row["route_changed"] and row["charge_delivered_kwh"] == 0 for row in protected)
    draw_result = run(
        args.output / "unexpected-draw",
        "unexpected-draw",
        network,
        route,
        decision,
        selected["advisor"],
        {**provenance, "run_id": f"{offline['run_id']}-unexpected-draw"},
        stations,
        bus,
        energy,
        mission["step_s"],
    )
    print(f"unexpected-draw: {draw_result['terminal_reason']}", flush=True)
    fallback, blocked = adverse
    adverse.append(draw_result)
    adverse_pass = (
        fallback["completed"]
        and fallback["station_charged"] != "depot-a"
        and fallback["target_reached"]
        and fallback["resumed_after_charging"]
        and not fallback["reserve_violation"]
        and blocked["terminal_reason"] == "no_feasible_station"
        and not blocked["route_changed"]
        and blocked["charge_delivered_kwh"] == 0
        and draw_result["terminal_reason"] == "reserve_violated"
        and draw_result["route_changed"]
        and draw_result["reserve_violation"]
        and not draw_result["completed"]
        and draw_result["charge_delivered_kwh"] == 0
    )
    passed = manual_pass and protection_pass and adverse_pass and results[0]["completed"]
    write(
        args.output / "qualification.json",
        {
            "scope": "One-bus nominal SUMO fixture with operator-injected diversions and native baseline",
            "fixture_passed": passed,
            "protection_checks_passed": protection_pass,
            "protection_outcomes": protected,
            "adverse_checks_passed": adverse_pass,
            "adverse_outcomes": adverse,
            "seed": SEED,
            "stationfinder_settings": STATIONFINDER,
            "teleport_disabled": True,
            "source_sha256": {
                path.relative_to(ROOT).as_posix(): digest(path)
                for path in (
                    Path(__file__).resolve(),
                    config,
                    RELEASE,
                    ROOT / "src/evdt/energy/charging_dispatch.py",
                    ROOT / "src/evdt/energy/charging_advisor.py",
                    ROOT / "src/evdt/command_timing.py",
                    ROOT / "scripts/charging/run_charging_advisor.py",
                )
            },
            "network_sha256": digest(network),
            "outcomes": results,
        },
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
