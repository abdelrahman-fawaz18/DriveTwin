"""Prepare and check alternate charging bays on a retained launcher trip."""

import argparse
import json
import shutil
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / part)
    for part in ("src", "scripts", "scripts/route_aware_energy", "scripts/launcher")
]
from geometry_provider import GeometryProvider  # noqa: E402
from heights import prepare as prepare_heights  # noqa: E402
from live_inputs import charging_assignment, check, digest, read, write  # noqa: E402
from plant_network import corridor  # noqa: E402
from prepare_osm_network import forecast_subset  # noqa: E402

from catalog import CATALOG, load, save_catalog, select  # noqa: E402
from evdt.energy.moving_charge_detour import find_detour, splice_detour  # noqa: E402
from evdt.energy.moving_charge_worker import calculate, validate_planning_result  # noqa: E402
from evdt.energy.route_aware.context import SourceContext, SourcePlan, SourceStop  # noqa: E402
from evdt.energy.route_aware.sumo_routes import expand_route, read_network  # noqa: E402
from evdt.energy.route_aware.wire import fingerprint, parse_request  # noqa: E402


def bus_plan(network, data):
    itinerary = data["stops"]
    stops = tuple(
        SourceStop(
            *(
                row[key]
                for key in ("stop_id", "external_index", "lane_id", "offset_m", "dwell_working_s")
            )
        )
        for row in itinerary["stops"]
    )
    return SourcePlan(network.sha256, tuple(data["mission"]["route_edges"]), stops)


def route_lanes(network, plan):
    prepared = expand_route(
        network,
        plan.edges,
        revision=plan.revision,
        required_lanes={s.external_index: s.lane_id for s in plan.remaining_stops},
    )
    return [prepared.context.occurrences[i].lane_id for i in prepared.external_occurrences]


def candidates(network, plan, lanes):
    assigned = set(plan.edges)
    stops = {stop.external_index for stop in plan.remaining_stops}
    available = [
        lane
        for lane in network.lanes.values()
        if not lane.internal
        and lane.edge_id not in assigned
        and 45 <= lane.length_m <= 200
        and lane.road_class in {"highway.residential", "highway.tertiary", "highway.unclassified"}
    ]
    options = {}
    for start in range(3, min(len(lanes) - 3, 30)):
        anchor = network.lanes[lanes[start]].shape[-1]
        nearby = sorted(
            (
                (lane.shape[0].x_m - anchor.x_m) ** 2 + (lane.shape[0].y_m - anchor.y_m) ** 2,
                lane.lane_id,
            )
            for lane in available
        )[:10]
        for end in range(start + 2, min(start + 8, len(lanes) - 1)):
            if any(start < stop < end for stop in stops):
                break
            for distance, bay in nearby:
                if distance > 175**2:
                    break
                try:
                    detour = find_detour(
                        network,
                        plan,
                        branch_index=start,
                        rejoin_index=end,
                        branch_lane_id=lanes[start],
                        rejoin_lane_id=lanes[end],
                        bay_lane_id=bay,
                        max_visited_lanes=220,
                        max_path_working_s=80,
                    )
                    if len(detour.edges) > 9 or len(set(detour.edges)) != len(detour.edges):
                        continue
                    splice_detour(
                        network,
                        plan,
                        detour,
                        source_external_index=0,
                        source_lane_id=lanes[0],
                        bay_lane_id=bay,
                    )
                except ValueError:
                    continue
                added = sum(edge not in assigned for edge in detour.edges)
                rank = (added, len(detour.edges), start, end, bay)
                if bay not in options or rank < options[bay][0]:
                    options[bay] = (rank, detour)
    return {rank[-1]: detour for rank, detour in sorted(options.values())}


def verify_offer(profile, reference_request, data, levels, bay_ids):
    provider = GeometryProvider(ROOT / profile["prepared"] / "geometry.json")
    stamp = parse_request(reference_request.read_bytes())
    plan = bus_plan(provider.network, data)
    authority = SimpleNamespace(
        geometry_bundle_sha256=provider.bundle_sha256, network_sha256=provider.network.sha256
    )
    bound = charging_assignment(
        profile, stamp.state.scope.run_id, provider.network.sha256, plan.edges
    )
    lane = stamp.route.occurrences[stamp.state.occurrence_index].lane_id
    progress = SourceContext(
        "source-route-context/2.0.0",
        stamp.state.scope,
        "f" * 64,
        stamp.state.working_s,
        stamp.state.observed_real_life_s,
        plan,
        0,
        lane,
        stamp.state.lane_offset_m,
        "not_charging",
        None,
        None,
    )
    geometry = provider(authority, progress)
    demonstrations = {}
    for soc in levels:
        request = replace(
            stamp,
            route=geometry.route,
            state=replace(stamp.state, soc=soc / 100, route_revision=plan.revision),
        )
        job = bound.prepare_job(
            request,
            progress,
            geometry,
            assignment_sha256=fingerprint(bound),
            now_real_life_s=stamp.requested_real_life_s,
            network=provider.network,
            prepare_alternate=lambda context, **args: provider(authority, context, **args),
        )
        for offer in job.offers:
            if offer.stop.bay.bay_id not in bay_ids:
                continue
            sole = replace(job, offers=(offer,))
            result = calculate(sole)
            validate_planning_result(sole, result)
            option = result.decision.preferred
            if option:
                transfer = option.transfer
                headroom = (
                    request.vehicle.reserve_soc
                    + bound.policy.margin_soc
                    + max(
                        0,
                        request.state.soc
                        - min(transfer.plug_in_soc, option.approach.forecast.summary.minimum_soc),
                    )
                )
                stop_time = (
                    transfer.end_working_s - option.approach.forecast.summary.arrival_working_s
                )
                if max(headroom, request.state.soc) < transfer.departure_soc and stop_time < 600:
                    demonstrations[option.bay_id] = {
                        "starting_charge_percent": soc,
                        "target_soc": transfer.departure_soc,
                        "arrival_working_s": option.arrival_working_s,
                        "minimum_soc": min(
                            transfer.plug_in_soc,
                            option.approach.forecast.summary.minimum_soc,
                            option.onward.forecast.summary.minimum_soc,
                        ),
                    }
        if set(demonstrations) == set(bay_ids):
            break
    if set(demonstrations) != set(bay_ids):
        raise ValueError(
            f"Missing reserve-safe approach and onward forecast: {set(bay_ids) - demonstrations.keys()}"
        )
    return demonstrations


def install(trip_id, output, results):
    trips = load()
    trip = next(row for row in trips if row["id"] == trip_id)
    profile = read(output / "profile.json")
    prepared, _, data = check(profile)
    _, _, original = check(trip["profile"])
    if (
        profile["journey_id"] != trip_id
        or prepared != output
        or any(data[key] != original[key] for key in ("stops", "mission", "recipe"))
    ):
        raise ValueError("The prepared variant must preserve the original trip and itinerary")
    network = read(prepared / "geometry.json")["network"]["sha256"]
    assignment = charging_assignment(
        profile, "station-variant-check", network, data["mission"]["route_edges"]
    )
    off_route = {row.bay.bay_id: row for row in assignment.bays if row.detour}
    forecast = read(output / "forecast-check.json")
    if not off_route or set(forecast) != set(off_route):
        raise ValueError("Every alternate bay requires a passing reserve-safe planning check")
    verified = set()
    for result in results:
        result = result.resolve(strict=True)
        if not result.is_relative_to(ROOT / "evidence/energy-v2"):
            raise ValueError("Plant evidence must stay inside the project evidence directory")
        qualification = read(result / "qualification.json")
        measured = read(result / "worker-result.json")
        exercised = read(result / "profile.json")
        probe = exercised["detour_station_probe"]
        bay = probe["bay_id"]
        if (
            qualification.get("case") != "charging-plant"
            or qualification.get("passed") is not True
            or measured.get("passed") is not True
            or measured.get("cleanup_verified") is not True
            or exercised["inputs"] != profile["inputs"]
            or exercised["journey_id"] != trip_id
            or bay not in off_route
            or measured["measurement"]["charging"]["bay_id"] != bay
            or measured["measurement"]["passenger_stops_served"] != len(data["stops"]["stops"])
            or probe["target_soc"] != forecast[bay]["target_soc"]
            or exercised["starting_charge_percent"] != forecast[bay]["starting_charge_percent"]
        ):
            raise ValueError(
                "Plant check does not match this alternate bay, itinerary, and profile"
            )
        verified.add(bay)
    if verified != set(off_route):
        raise ValueError("Every alternate bay needs its own passing plant evidence")
    trip["charging_variant"] = profile
    trip["charging_layout"] = "mixed" if len(off_route) < len(assignment.bays) else "off-route"
    save_catalog(CATALOG, trips)
    print(
        json.dumps(
            {"trip": trip_id, "charging_layout": trip["charging_layout"], "bays": sorted(verified)}
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trip")
    parser.add_argument("output", type=Path)
    parser.add_argument("--bay", action="append", default=[])
    parser.add_argument("--mode", choices=("mixed", "off-route"))
    parser.add_argument("--starting-charge", nargs="+", type=float)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--plant-result", type=Path, action="append", default=[])
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "evidence/energy-v2"):
        raise ValueError("Prepared preset outputs belong in the ignored energy evidence directory")
    if args.install:
        install(args.trip, output, args.plant_result)
        return
    original = next(row for row in load() if row["id"] == args.trip)
    profile = select([{**original, "charging_variant": None}], args.trip, ["energy", "charging"])
    prepared, reference, data = check(profile)
    if data["recipe"].get("speed_zones"):
        raise ValueError("Detours with localized speed restrictions require separate qualification")
    raw_network = prepared / "imported.net.xml"
    network = read_network(raw_network.read_bytes(), max_bytes=64 * 1024**2)
    plan = bus_plan(network, data)
    lanes = route_lanes(network, plan)
    options = candidates(network, plan, lanes)
    if not args.mode:
        print(
            json.dumps(
                [{"lane": lane, **asdict(detour)} for lane, detour in list(options.items())[:12]]
            )
        )
        return
    if not args.starting_charge or not args.bay or len(set(args.bay)) != len(args.bay):
        raise ValueError("Select reviewed lanes and starting charges")
    output.mkdir(parents=True, exist_ok=False)
    detours = [options[bay] for bay in args.bay]
    extended_edges = tuple(
        dict.fromkeys((*plan.edges, *(edge for d in detours for edge in d.edges)))
    )
    for name in ("roads.osm.xml", "imported.net.xml", "mission.json", "stops.json"):
        shutil.copyfile(prepared / name, output / name)
    forecast_subset(raw_network, extended_edges, output / "route.net.xml")
    prepare_heights(output)
    write(output / "run-input.json", {**data, "height_receipt": read(output / "heights.json")})
    shutil.copyfile(reference / "routes.xml", output / "routes.xml")
    runtime_path = output / "reference-network.net.xml"
    write(
        output / "detour-corridor.json",
        corridor(output / "elevated-plant.net.xml", extended_edges, runtime_path),
    )
    runtime = read_network(runtime_path.read_bytes(), max_bytes=32 * 1024**2)
    runtime_plan = bus_plan(runtime, data)
    meta = read(output / "geometry.json")
    assignment = read(ROOT / profile["charging_assignment"])
    donor = assignment["bays"][0]["bay"]
    if args.mode == "off-route":
        assignment["bays"] = []
        assignment["scenario"]["schedules"] = []
    elif len(assignment["bays"]) > 1 and len(args.bay) > 1:
        assignment["bays"] = assignment["bays"][-1:]
        retained = assignment["bays"][0]["bay"]["bay_id"]
        assignment["scenario"]["schedules"] = [
            s for s in assignment["scenario"]["schedules"] if s["bay_id"] == retained
        ]
    new_ids = []
    for i, (lane, detour) in enumerate(zip(args.bay, detours, strict=True), 1):
        splice_detour(
            runtime,
            runtime_plan,
            detour,
            source_external_index=0,
            source_lane_id=lanes[0],
            bay_lane_id=lane,
        )
        identity = f"detour-bay-{i}"
        new_ids.append(identity)
        end = round(min(network.lanes[lane].length_m, runtime.lanes[lane].length_m) * 0.65, 3)
        assignment["bays"].append(
            {
                "bay": {
                    **donor,
                    "bay_id": identity,
                    "lane_id": lane,
                    "platform_length_m": 15,
                    "start_offset_m": end - 15,
                    "end_offset_m": end,
                    "access_evidence": "Simulated curb bay on an imported off-route lane",
                    "location_reference": "OpenStreetMap way and measured elevation raster",
                },
                "external_index": detour.branch_index,
                "detour": asdict(detour),
            }
        )
        assignment["scenario"]["schedules"].append(
            {
                "bay_id": identity,
                "intervals": [
                    {
                        "from_working_s": 0,
                        "until_working_s": 14_400,
                        "state": "available",
                        "power_w": donor["power_w"],
                    }
                ],
            }
        )
    assignment["network_sha256"] = meta["network"]["sha256"]
    for entry in assignment["bays"]:
        entry["bay"]["network_sha256"] = assignment["network_sha256"]
    assignment["max_candidate_jobs"] = len(assignment["bays"]) * (
        1 + len(assignment["target_soc_levels"])
    )
    assignment["scenario"]["scenario_id"] = args.trip + "-" + args.mode
    station_path = output / "charging-assignment.json"
    write(station_path, assignment)
    names = (
        "mission.json",
        "stops.json",
        "geometry.json",
        "heights.json",
        "roads.osm.xml",
        "elevated-route.net.xml",
        "elevated-plant.net.xml",
        "reference-network.net.xml",
        "routes.xml",
        "run-input.json",
        "charging-assignment.json",
        "detour-corridor.json",
    )
    relative = output.relative_to(ROOT).as_posix()
    profile.update(
        prepared=relative,
        reference=relative,
        charging_assignment=f"{relative}/{station_path.name}",
        inputs={f"{relative}/{name}": digest(output / name) for name in names},
    )
    check(profile)
    demonstrations = verify_offer(
        profile, reference / "input-0.json", data, args.starting_charge, new_ids
    )
    write(output / "forecast-check.json", demonstrations)
    write(output / "profile.json", profile)
    print(json.dumps(demonstrations))


if __name__ == "__main__":
    main()
